import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sources import epoch, parse, redact, timestamp
from review import analyze, annotations, collect
from render import payload, player_html, public_pdf_links, report_html
from scripts.prepare_pages import PUBLIC_FILES, prepare

TIME = "2026-11-07T17:06:22.211Z"


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)

    def load(self, harness, records, suffix=".jsonl"):
        path = self.root / ("source" + suffix)
        path.write_text(
            "\n".join(json.dumps(r) for r in records) if suffix == ".jsonl" else records,
            encoding="utf-8",
        )
        return parse(path, {"harness": harness, "session": "test"}, "S01")[0]

    def test_shared_conversation_urls_are_redacted_without_removing_the_prompt(self):
        for host in ("claude.ai", "chatgpt.com", "chat.openai.com"):
            text = f"Use [this session](https://{host}/share/example-session?view=1), not https://example.org/docs."
            expected = "Use [this session]([REDACTED_SHARED_SESSION_URL]), not https://example.org/docs."
            self.assertEqual(redact(text), expected)
            events = self.load("codex", [{"type": "response_item", "timestamp": TIME, "payload": {
                "type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}}])
            self.assertEqual(events[0]["text"], expected)

    def test_public_pdf_links_preserve_offline_html_and_do_not_fetch_the_site(self):
        from playwright.sync_api import sync_playwright
        html = '<a href="player.html#H006">Replay</a><a href="analysis.json">Data</a><a href="#record">Record</a>'
        path = self.root / "report.html"
        path.write_text(html)
        requests = []
        base = "https://example.org/session/"
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.on("request", lambda request: requests.append(request.url))
            page.goto(path.as_uri())
            self.assertEqual(public_pdf_links(page, base.rstrip("/")), base)
            pdf = page.pdf()
            browser.close()
        links = re.findall(rb"/URI\s*\(([^)]*)\)", pdf)
        self.assertIn((base + "player.html#H006").encode(), links)
        self.assertIn((base + "analysis.json").encode(), links)
        self.assertFalse(any(link.lower().startswith(b"file:") for link in links))
        self.assertFalse(any(url.startswith(("http://", "https://")) for url in requests))
        self.assertEqual(path.read_text(), html)
        with self.assertRaises(ValueError):
            public_pdf_links(None, "http://example.org/session/")

    def test_pages_staging_excludes_private_files_and_rejects_unreviewed_content(self):
        source = self.root / "review"
        source.mkdir()
        bundled = Path(__file__).resolve().parents[1] / "examples" / "creation"
        for name in PUBLIC_FILES:
            shutil.copyfile(bundled / name, source / name)
        (source / "private").mkdir()
        (source / "private" / "raw.jsonl").write_text("Not approved for publication.")
        (source / "README.md").write_text("Not a public asset.")
        output = prepare(source, self.root / "site")
        self.assertEqual({path.name for path in output.iterdir()}, set(PUBLIC_FILES) | {"index.html"})
        self.assertEqual((output / "index.html").read_bytes(), (output / "report.html").read_bytes())
        for number, text in enumerate(("https://claude.ai/share/example-session", "ghp_" + "a" * 30)):
            (source / "player.html").write_text(text)
            rejected = self.root / f"rejected-{number}"
            with self.assertRaisesRegex(ValueError, "review before publishing"):
                prepare(source, rejected)
            self.assertFalse(rejected.exists())

    def test_codex_mirrors_cumulative_usage_and_context_are_not_counted(self):
        records = [
            {"type": "turn_context", "payload": {"model": "recorded-model"}},
            {"type": "response_item", "timestamp": TIME, "payload": {
                "type": "message", "id": "u1", "role": "user",
                "internal_chat_message_metadata_passthrough": {"turn_id": "native-turn"},
                "content": [{"type": "input_text", "text": "Build it"}]}},
            {"type": "event_msg", "timestamp": TIME, "payload": {
                "type": "item_completed", "item": {"type": "UserMessage", "content": [{"type": "text", "text": "Build it"}]}}},
            {"type": "token_usage_record", "timestamp": TIME, "payload": {
                "thread_id": "test", "response_id": "call", "turn_id": "native-turn", "usage": {
                    "input_tokens": 100, "output_tokens": 20, "cached_input_tokens": 40,
                    "reasoning_output_tokens": 10}}},
            {"type": "event_msg", "timestamp": TIME, "payload": {"type": "token_count", "info": {
                "total_token_usage": {"input_tokens": 1000000}}}},
        ]
        events = self.load("codex", records + [records[3]])
        self.assertEqual([e["kind"] for e in events], ["user", "usage"])
        self.assertEqual((events[1]["input"], events[1]["output"], events[1]["cached_read"]), (100, 20, 40))
        self.assertEqual(events[1]["model"], "recorded-model")
        self.assertEqual((events[0]["turn_id"], events[1]["turn_id"]), ("native-turn", "native-turn"))

    def test_copilot_synthetic_users_and_duplicate_requests(self):
        records = [
            {"type": "user.message", "id": "u", "timestamp": TIME, "data": {"content": "Human"}},
            {"type": "user.message", "id": "s", "timestamp": TIME, "data": {"source": "agent", "content": "Handback"}},
            {"type": "session.usage_record", "id": "call1", "timestamp": TIME, "data": {"usage": {
                "accounting": {"usageId": "request"}, "model": "m", "inputTokens": 100,
                "outputTokens": 10, "cacheReadTokens": 60}}},
            {"type": "session.usage_record", "id": "call2", "timestamp": TIME, "data": {"usage": {
                "accounting": {"usageId": "request"}, "model": "m", "inputTokens": 100,
                "outputTokens": 10, "cacheReadTokens": 60}}},
        ]
        events = self.load("copilot", records)
        self.assertEqual([e["kind"] for e in events], ["user", "usage"])
        self.assertEqual(events[1]["input"], 100)

    def test_claude_queued_humans_usage_blocks_and_handbacks(self):
        records = [
            {"type": "attachment", "uuid": "queue", "timestamp": TIME, "attachment": {
                "type": "queued_command", "origin": {"kind": "human"}, "source_uuid": "u1", "prompt": "Queued direction"}},
            {"type": "user", "uuid": "u1", "timestamp": TIME, "origin": {"kind": "human"},
             "message": {"content": "Queued direction"}},
            {"type": "attachment", "timestamp": TIME, "attachment": {
                "type": "queued_command", "prompt": "Agent handback"}},
            {"type": "user", "uuid": "summary", "timestamp": TIME, "isCompactSummary": True,
             "origin": {"kind": "human"}, "message": {"content": "Compaction"}},
            {"type": "assistant", "uuid": "a1", "timestamp": TIME, "message": {
                "id": "request", "model": "claude", "content": [{"type": "text", "text": "First block"}],
                "usage": {"input_tokens": 10, "cache_read_input_tokens": 20,
                          "cache_creation_input_tokens": 5, "output_tokens": 2}}},
            {"type": "assistant", "uuid": "a2", "timestamp": TIME, "message": {
                "id": "request", "model": "claude", "content": [{"type": "text", "text": "Second block"}],
                "usage": {"input_tokens": 10, "cache_read_input_tokens": 20,
                          "cache_creation_input_tokens": 5, "output_tokens": 9}}},
        ]
        events = self.load("claude", records)
        self.assertEqual(len([e for e in events if e["kind"] == "user"]), 1)
        self.assertEqual(len([e for e in events if e["kind"] == "assistant"]), 2)
        usage = next(e for e in events if e["kind"] == "usage")
        self.assertEqual((usage["input"], usage["output"]), (35, 9))

    def test_cursor_and_replit_json_preserve_unknowns_and_text(self):
        for harness in ("cursor", "replit"):
            with self.subTest(harness=harness):
                events = self.load(harness, json.dumps({"messages": [
                    {"role": "user", "content": "Request"},
                    {"role": "assistant", "content": "Reply", "timestamp": 1794071182.211,
                     "usage": {"input_tokens": 25, "output_tokens": 5}},
                ]}), ".json")
                self.assertIsNone(events[0]["timestamp"])
                self.assertEqual(events[1]["timestamp"], TIME)
                self.assertEqual(events[2]["model"], "unknown")

    def test_markdown_does_not_split_role_headings_inside_code(self):
        for harness, agent in (("cursor", "Cursor"), ("replit", "Assistant")):
            with self.subTest(harness=harness):
                events = self.load(harness, f"# Export\n\n**User**\nQuestion\n```md\n**User**\nNot another message\n```\n\n**{agent}**\nAnswer", ".md")
                self.assertEqual(len(events), 2)
                self.assertIn("Not another message", events[0]["text"])
                self.assertTrue(all(e["timestamp"] is None for e in events))

    def test_bad_exports_and_naive_time_fail_instead_of_guessing(self):
        with self.assertRaises(ValueError):
            self.load("cursor", '{"unknown": []}', ".json")
        with self.assertRaises(ValueError):
            timestamp("2026-11-07T17:06:22")
        with self.assertRaises(ValueError):
            self.load("replit", "Unlabeled chat copy", ".md")

    def test_collection_scopes_deduplicates_and_preserves_private_original(self):
        source = self.root / "source.jsonl"
        records = [
            {"type": "response_item", "timestamp": TIME, "payload": {
                "type": "message", "id": "u1", "role": "user",
                "content": [{"type": "input_text", "text": "key ghp_" + "x" * 25}]}},
            {"type": "response_item", "timestamp": "2026-11-08T00:00:00Z", "payload": {
                "type": "message", "id": "u2", "role": "user", "content": [{"type": "input_text", "text": "Outside"}]}},
        ]
        source.write_text("\n".join(json.dumps(r) for r in records))
        manifest_path = self.root / "manifest.json"
        spec = {"harness": "codex", "session": "test", "path": str(source), "start": TIME, "end": TIME}
        pricing_path = self.root / "rates.json"
        pricing_path.write_text(json.dumps({"currency": "USD", "unit": "per million tokens",
                                            "verified_on": "2026-11-07", "sources": {}, "models": {}}))
        manifest_path.write_text(json.dumps({"sources": [spec, spec], "pricing": "rates.json"}))
        manifest, collection, events = collect(manifest_path, self.root / "output")
        self.assertEqual(len(events), 1)
        self.assertIn("[REDACTED_CREDENTIAL]", events[0]["text"])
        self.assertIn("ghp_", source.read_text())
        self.assertEqual(collection["sources"][0]["outside_interval"], 1)
        self.assertEqual((self.root / "output" / "pricing.json").read_bytes(), pricing_path.read_bytes())
        self.assertEqual(collection["pricing"]["card"]["verified_on"], "2026-11-07")
        if sys.platform != "win32":
            self.assertEqual((self.root / "output").stat().st_mode & 0o777, 0o700)
        with self.assertRaises(ValueError):
            collect(manifest_path, self.root / "output")

    def test_undated_scoped_exports_fail_and_annotations_need_valid_evidence(self):
        source = self.root / "chat.json"
        source.write_text('[{"role":"user","content":"Untimed"}]')
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"sources": [{"harness": "cursor", "session": "x", "path": "chat.json", "start": TIME}]}))
        with self.assertRaisesRegex(ValueError, "undated"):
            collect(manifest, self.root / "bad-output")
        annotation_path = self.root / "annotations.json"
        annotation_path.write_text(json.dumps({"summary": "Review", "decisions": [
            {"input": "H099", "title": "Bad", "category": "Scope", "analysis": "No evidence"}]}))
        with self.assertRaisesRegex(ValueError, "Invalid"):
            annotations(manifest, {"annotations": "annotations.json"}, [])

    def test_untimed_payload_and_html_escape_do_not_invent_dates(self):
        events = [{"id": "H001", "kind": "user", "harness": "cursor", "session": "x",
                   "source": "S01:1", "text": "</script><script>window.pwned=1</script>",
                   "timestamp": None, "t": None}]
        collection = {"title": "<unsafe>", "timezone": "America/Los_Angeles",
                      "sources": [], "missing_harnesses": {}, "warnings": []}
        analysis = analyze(collection, events, {"summary": "Unknown timing", "decisions": []})
        data = payload(events, analysis)
        self.assertIsNone(data["meta"]["start"])
        self.assertEqual(len(data["undated"]), 1)
        self.assertIsNone(analysis["input_tokens"])
        self.assertNotIn(events[0]["text"], player_html(data))
        self.assertIn("&lt;script&gt;", report_html(collection, events, analysis))
        self.assertEqual(epoch(TIME), 1794071182211)

    def test_report_conversation_attaches_usage_only_to_user_prompts(self):
        common = {"harness": "cursor", "session": "x", "timestamp": None, "t": None}
        events = [
            {**common, "id": "H001", "kind": "user", "source": "S01:1", "text": "Build it"},
            {**common, "id": "R001", "kind": "assistant", "source": "S01:2", "text": "Done"},
            {**common, "id": "A001", "kind": "usage", "source": "S01:3", "model": "m<script>",
             "input": 25, "output": 5, "cached_read": 0, "cache_write": 0},
        ]
        collection = {"title": "Review", "timezone": "America/Los_Angeles",
                      "sources": [], "missing_harnesses": {}, "warnings": []}
        analysis = analyze(collection, events, {"summary": "Test", "decisions": []})
        html = report_html(collection, events, analysis)
        human = html.split('<details id="H001" class="record">')[1].split("</details>")[0]
        agent = html.split('<details id="R001" class="record">')[1].split("</details>")[0]
        self.assertIn('class="record-usage"', human)
        self.assertIn("m&lt;script&gt; · Estimated Unpriced · 25 in · 5 out", human)
        self.assertGreater(human.index('class="record-usage"'), human.index(">Build it</div>"))
        self.assertNotIn('class="record-usage"', agent)

    def test_bundled_creation_session_runs_without_original_workspace(self):
        repository = Path(__file__).resolve().parents[1]
        manifest_path = repository / "examples" / "creation.json"
        manifest = json.loads(manifest_path.read_text())
        paths = [manifest["pricing"], manifest["annotations"]]
        paths.extend(source["path"] for source in manifest["sources"])
        for value in paths:
            path = (manifest_path.parent / value).resolve()
            self.assertFalse(Path(value).is_absolute())
            self.assertTrue(path.is_relative_to(repository), value)
            self.assertTrue(path.is_file(), value)
        manifest, collection, events = collect(manifest_path, self.root / "example-review")
        self.assertEqual(
            (self.root / "example-review" / "pricing.json").read_bytes(),
            (manifest_path.parent / manifest["pricing"]).read_bytes(),
        )
        analysis = analyze(collection, events, annotations(manifest_path, manifest, events))
        expected = json.loads((repository / "examples" / "creation-source.json").read_text())["expected"]
        for field, value in expected.items():
            if field == "estimated_api_equivalent_usd":
                self.assertAlmostEqual(analysis["accounting"]["total"]["estimated_usd"], value, places=7)
            else:
                self.assertEqual(analysis[field], value, field)


if __name__ == "__main__":
    unittest.main()
