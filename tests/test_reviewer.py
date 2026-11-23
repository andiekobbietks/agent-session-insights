import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sources import epoch, parse, redact, timestamp
from review import analyze, annotations, collect
from render import duration, payload, player_html, public_pdf_links, publish, report_html, spend, spend_matches_display
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


    def dsh_log(self, records, compressed=False):
        if not compressed:
            return self.load("dsh", records)
        import zstandard
        path = self.root / "session.v4.jsonl.zstd"
        compressor = zstandard.ZstdCompressor()
        path.write_bytes(b"".join(
            compressor.compress(json.dumps(record).encode("utf-8") + b"\n") for record in records
        ))
        return parse(path, {"harness": "dsh", "session": "test"}, "S01")[0]

    def dsh_records(self):
        answers = json.dumps({"answers": [{"id": "scope", "selected": ["Full report"]}]})
        return [
            {"type": "session", "version": 4, "id": "session-test", "createdAt": 1794037718022,
             "cwd": "/work/project", "isSeeded": False, "delegationDepth": 0},
            {"type": "user/message", "seq": 0, "time": 1794037804257,
             "data": {"id": "u1", "source": {"kind": "user"},
                      "content": [{"type": "text", "text": "Build the report"}]}},
            {"type": "user/message", "seq": 1, "time": 1794037804258,
             "data": {"id": "ctx", "source": {"kind": "runtime-context"},
                      "content": [{"type": "text", "text": "Current runtime context"}]}},
            {"type": "request/context", "seq": 2, "time": 1794037804259,
             "data": {"provider": "deepseek", "model": "deepseek-chat", "contextWindow": 1000000}},
            {"type": "assistant/message", "seq": 3, "time": 1794037811713,
             "data": {"turn": 1, "step": 1, "message": {
                 "id": "a1", "role": "assistant",
                 "source": {"kind": "model", "provider": "deepseek", "model": "deepseek-chat"},
                 "content": [{"type": "reasoning", "text": "internal reasoning"},
                             {"type": "text", "text": "Checking the repository."}]},
                 "usage": {"inputTokens": 100, "outputTokens": 10, "totalTokens": 150,
                           "cacheReadTokens": 40}}},
            {"type": "tool/call", "seq": 4, "time": 1794037811716,
             "data": {"turn": 1, "step": 1, "callId": "call_ask", "name": "ask_user_question"}},
            {"type": "tool/result", "seq": 5, "time": 1794037811720,
             "data": {"turn": 1, "step": 1, "toolCallId": "call_ask",
                      "message": {"role": "tool", "toolCallId": "call_ask", "isError": False,
                                  "content": [{"type": "text", "text": answers}]}}},
            {"type": "assistant/message", "seq": 6, "time": 1794037811800,
             "data": {"turn": 1, "step": 2, "message": {
                 "id": "a2", "role": "assistant",
                 "source": {"kind": "model", "provider": "deepseek", "model": "deepseek-chat"},
                 "content": [{"type": "reasoning", "text": "one more step"}]},
                 "usage": {"inputTokens": 20, "outputTokens": 5, "cacheReadTokens": 5}}},
        ]

    def test_dsh_messages_usage_tools_and_human_answers(self):
        events = self.dsh_log(self.dsh_records())
        self.assertEqual([event["kind"] for event in events],
                         ["user", "assistant", "usage", "tool", "user", "usage"])
        self.assertEqual(events[0]["text"], "Build the report")
        self.assertEqual(events[1]["text"], "Checking the repository.")
        self.assertEqual(events[2]["model"], "deepseek-chat")
        self.assertEqual((events[2]["input"], events[2]["output"], events[2]["cached_read"]),
                         (140, 10, 40))
        self.assertEqual((events[5]["input"], events[5]["output"], events[5]["cached_read"]),
                         (25, 5, 5))
        self.assertFalse(events[2]["cache_write_known"])
        self.assertTrue(events[2]["input_complete"])
        self.assertFalse(events[5]["cache_write_known"])
        self.assertFalse(events[5]["input_complete"])
        self.assertEqual(events[3]["name"], "ask_user_question")
        self.assertTrue(events[4]["form_response"])
        self.assertIn("Full report", events[4]["text"])
        self.assertNotIn("internal reasoning", json.dumps(events))
        self.assertNotIn("Current runtime context", json.dumps(events))
        self.assertEqual(events[0]["timestamp"], "2026-11-07T07:50:04.257Z")

    def test_dsh_missing_cache_fields_stay_unknown(self):
        def assistant(identity, usage):
            return {"type": "assistant/message", "time": 1794037811713,
                    "data": {"turn": 1, "step": 1, "message": {
                        "id": identity, "role": "assistant",
                        "source": {"kind": "model", "model": "deepseek-chat"},
                        "content": [{"type": "text", "text": identity}]},
                        "usage": usage}}

        records = [
            {"type": "session", "version": 4, "id": "session-test", "createdAt": 1,
             "cwd": "/work/project", "isSeeded": False, "delegationDepth": 0},
            assistant("missing", {"inputTokens": 100, "outputTokens": 10}),
            assistant("zero", {"inputTokens": 100, "outputTokens": 10,
                               "cacheReadTokens": 0, "cacheWriteTokens": 0}),
            assistant("total-only", {"inputTokens": 100, "outputTokens": 10, "totalTokens": 150}),
        ]
        events = self.dsh_log(records)
        missing, zero, total_only = [event for event in events if event["kind"] == "usage"]
        self.assertEqual((missing["input"], missing["cached_read"], missing["cache_write"]),
                         (100, 0, 0))
        self.assertFalse(missing["cached_read_known"])
        self.assertFalse(missing["cache_write_known"])
        self.assertFalse(missing["input_complete"])
        self.assertTrue(zero["cached_read_known"])
        self.assertTrue(zero["cache_write_known"])
        self.assertTrue(zero["input_complete"])
        self.assertEqual(total_only["input"], 140)
        self.assertFalse(total_only["cached_read_known"])
        self.assertFalse(total_only["cache_write_known"])
        self.assertTrue(total_only["input_complete"])

        for usage in ({"outputTokens": 10}, {"inputTokens": 100},
                      {"inputTokens": 100, "outputTokens": 10, "totalTokens": 5}):
            with self.subTest(usage=usage):
                with self.assertRaises(ValueError):
                    self.dsh_log([records[0], assistant("bad", usage)])

    def test_dsh_compressed_sessions_stream_concatenated_frames(self):
        events = self.dsh_log(self.dsh_records(), compressed=True)
        self.assertEqual(len(events), 6)
        self.assertEqual(events[0]["text"], "Build the report")

    def test_dsh_accounting_preserves_prompt_and_form_response_ownership(self):
        for bracketed in (False, True):
            for with_answer in (False, True):
                with self.subTest(bracketed=bracketed, with_answer=with_answer):
                    records = self.dsh_records()
                    if with_answer:
                        records[-1]["data"]["message"]["content"] = [
                            {"type": "text", "text": "The report is ready."}]
                    else:
                        records = records[:5]
                    if bracketed:
                        records.insert(1, {"type": "turn/start", "time": 1794037804256,
                                           "data": {"turn": 1}})
                        records.append({"type": "turn/end", "time": 1794037811801,
                                        "data": {"turn": 1, "reason": "completed"}})
                    self.dsh_log(records)
                    manifest = self.root / "manifest.json"
                    manifest.write_text(json.dumps({
                        "timezone": "UTC", "sources": [
                            {"harness": "dsh", "session": "test", "path": "source.jsonl"}],
                    }), encoding="utf-8")
                    _, collection, events = collect(
                        manifest, self.root / f"ownership-{bracketed}-{with_answer}")
                    analysis = analyze(collection, events, {"summary": "Test", "decisions": []})
                    accounting = analysis["accounting"]
                    turns = accounting["turns"]
                    replies = [event for event in events if event["kind"] == "assistant"]
                    self.assertEqual([turn["calls"] for turn in turns],
                                     [1, 1] if with_answer else [1])
                    self.assertEqual([turn["response_ids"] for turn in turns],
                                     [[reply["id"]] for reply in replies])
                    self.assertEqual([request["turn"] for request in accounting["requests"]],
                                     [turn["id"] for turn in turns])
                    self.assertEqual(accounting["unattributed"]["calls"], 0)
                    self.assertTrue(all(turn["duration_ms"] is not None for turn in turns))

    def test_dsh_compressed_sessions_reject_incomplete_final_frames(self):
        import zstandard
        records = self.dsh_records()
        compressor = zstandard.ZstdCompressor(write_checksum=True)
        prefix = compressor.compress(
            ("\n".join(json.dumps(record) for record in records[:4]) + "\n").encode("utf-8"))
        final = compressor.compress((json.dumps(records[4]) + "\n").encode("utf-8"))
        path = self.root / "session.v4.jsonl.zstd"
        for retained in (1, 4, 10, len(final) - 1):
            with self.subTest(retained=retained):
                path.write_bytes(prefix + final[:retained])
                with self.assertRaisesRegex(ValueError, "incomplete Zstandard frame"):
                    parse(path, {"harness": "dsh", "session": "test"}, "S01")

    def test_dsh_compressed_sessions_preserve_large_lines_and_unterminated_json(self):
        import random
        import zstandard
        records = self.dsh_records()[:2]
        text = random.Random(0).randbytes(100000).hex() + "\N{EURO SIGN}"
        records[1]["data"]["content"] = [{"type": "text", "text": text}]
        path = self.root / "session.v4.jsonl.zstd"
        path.write_bytes(zstandard.ZstdCompressor().compress(
            "\n".join(json.dumps(record, ensure_ascii=False) for record in records).encode("utf-8")))
        events, _, count = parse(path, {"harness": "dsh", "session": "test"}, "S01")
        self.assertEqual(count, 2)
        self.assertEqual(events[0]["text"], text)

    def test_dsh_future_or_past_format_versions_fail_explicitly(self):
        records = self.dsh_records()
        records[0]["version"] = 3
        with self.assertRaisesRegex(ValueError, "session format v3 is unsupported"):
            self.dsh_log(records)

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

    def test_spend_display_accepts_half_micro_rounding_and_rejects_larger_errors(self):
        self.assertTrue(spend_matches_display("$0.318329", 0.3183295))
        self.assertTrue(spend_matches_display("$1.463448", 1.4634475))
        self.assertFalse(spend_matches_display("$0.318328", 0.3183295))
        total = {"calls": 67, "priced_calls": 67, "unpriced_calls": 0, "estimated_usd": 1.4626616,
                 "estimated_usd_min": 1.4626616, "estimated_usd_max": 1.4626616}
        self.assertEqual(spend(total), "$1.46")
        self.assertTrue(spend_matches_display("$1.46", total["estimated_usd"]))
        self.assertFalse(spend_matches_display("$1.47", total["estimated_usd"]))

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

    def test_missing_cache_is_distinct_from_explicit_zero_in_native_logs(self):
        for harness, records in (
            ("copilot", [
                {"type": "session.usage_record", "id": "one", "data": {"usage": {
                    "inputTokens": 100, "outputTokens": 10}}},
                {"type": "session.usage_record", "id": "two", "data": {"usage": {
                    "inputTokens": 100, "outputTokens": 10, "cacheReadTokens": 0, "cacheWriteTokens": 0}}},
            ]),
            ("codex", [
                {"type": "token_usage_record", "payload": {"thread_id": "test", "response_id": "one",
                 "usage": {"input_tokens": 100, "output_tokens": 10}}},
                {"type": "token_usage_record", "payload": {"thread_id": "test", "response_id": "two",
                 "usage": {"input_tokens": 100, "output_tokens": 10,
                           "cached_input_tokens": 0, "cache_write_input_tokens": 0}}},
            ]),
        ):
            with self.subTest(harness=harness):
                missing, zero = self.load(harness, records)
                self.assertFalse(missing["cached_read_known"])
                self.assertFalse(missing["cache_write_known"])
                self.assertTrue(zero["cached_read_known"])
                self.assertTrue(zero["cache_write_known"])
        usage = self.load("claude", [{"type": "assistant", "message": {
            "id": "one", "content": [], "usage": {"input_tokens": 100, "output_tokens": 10}}}])[0]
        self.assertFalse(usage["input_complete"])

    def test_bundled_retail_rates_are_frozen_and_price_without_manifest_override(self):
        source = self.root / "source.jsonl"
        source.write_text(json.dumps({"type": "session.usage_record", "id": "one",
                                     "data": {"usage": {"model": "gpt-6.1-sol", "inputTokens": 1000,
                                                       "outputTokens": 100}}}) + "\n")
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"sources": [{"harness": "copilot", "session": "test",
                                                     "path": str(source)}]}))
        _, collection, events = collect(manifest, self.root / "report")
        analysis = analyze(collection, events, {"summary": "Test", "decisions": []})
        self.assertIsNone(analysis["cache_read_tokens"])
        self.assertIsNone(analysis["cache_write_tokens"])
        self.assertEqual(analysis["accounting"]["total"]["priced_calls"], 1)
        self.assertGreater(analysis["accounting"]["total"]["estimated_usd_max"], 0)
        self.assertIn("range", analysis["cost"])
        bundled = Path(__file__).resolve().parents[1] / "retail-pricing.json"
        self.assertEqual((self.root / "report" / "pricing.json").read_bytes(), bundled.read_bytes())

    def test_report_overview_shows_cost_period_and_user_terminology_without_changing_sources(self):
        from playwright.sync_api import sync_playwright
        collection = {"title": "Overview", "timezone": "America/Los_Angeles",
                      "sources": [], "missing_harnesses": {}, "warnings": [],
                      "pricing": {"card": {
                          "currency": "USD", "unit": "per million tokens", "verified_on": "2026-11-07",
                          "sources": {"test": "https://example.com/pricing"},
                          "models": {"m": {"input": 2, "cache_read": 0.1, "cache_write": 2.5,
                                           "output": 10, "source": "test"}}}}}
        quoted = "The human said: human inputs must stay verbatim."
        first = "2026-10-31T17:06:22.211Z"
        messages = [
            {"id": "H001", "kind": "user", "text": quoted, "timestamp": first, "t": epoch(first)},
            {"id": "R001", "kind": "assistant", "text": "Response", "timestamp": TIME, "t": epoch(TIME)},
            {"id": "H002", "kind": "user", "text": "Undated input", "timestamp": None, "t": None},
        ]
        usage = {"id": "A001", "identity": "call", "kind": "usage", "timestamp": TIME, "t": epoch(TIME),
                 "model": "m", "input": 700000, "output": 6000, "cached_read": 0, "cache_write": 0}
        unknown = {**usage, "id": "A002", "identity": "unknown", "model": "unknown"}
        cases = [
            ([usage], "$1.46"),
            ([{**usage, "cached_read_known": False, "cache_write_known": False}], "Range $0.13 - $1.81"),
            ([usage, unknown], "$1.46 + unpriced calls"),
            ([unknown], "Unpriced"),
            ([], "Unknown (no recorded usage)"),
        ]
        interpretation = {"summary": "The human reviewed human inputs.", "decisions": [
            {"input": "H001", "title": "Human direction", "category": "Human scope",
             "analysis": "The human set the scope."}]}
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            for calls, expected in cases:
                with self.subTest(cost=expected):
                    events = [{**event, "harness": "copilot", "session": "test", "source": f"S01:{i}"}
                              for i, event in enumerate(messages + calls, 1)]
                    if not calls:
                        events = [{**event, "timestamp": None, "t": None} for event in events]
                    analysis = analyze(collection, events, interpretation)
                    page.set_content(report_html(collection, events, analysis))
                    self.assertEqual(page.locator(".stats .stat").count(), 5)
                    self.assertEqual(page.locator(".stat.cost b").inner_text(), expected)
                    self.assertEqual(page.locator("header p").last.inner_text(), "The user reviewed user inputs.")
                    self.assertEqual(page.locator(".decision .quote").inner_text(), quoted)
                    self.assertEqual(page.locator(".decision h3").inner_text(), "User direction")
                    self.assertEqual(page.locator(".decision p").last.inner_text(), "The user set the scope.")
                    period = page.locator(".report-period").inner_text()
                    if calls:
                        self.assertIn("2026-10-31 10:06:22 PDT - 2026-11-07 09:06:22 PST", period)
                        self.assertIn("undated records also included", period)
                        self.assertEqual(page.locator("#wall-time").inner_text(), "7d (dated records only)")
                        self.assertEqual(page.locator("#total-turn-time").inner_text(), "7d (partial)")
                        self.assertIn("1 of 2 turns timed", page.locator(".timing-summary").inner_text())
                    else:
                        self.assertIn("Unknown (no reliable timestamps)", period)
                        self.assertEqual(page.locator("#wall-time").inner_text(), "Unknown")
                        self.assertEqual(page.locator("#total-turn-time").inner_text(), "Unknown")
                    data = payload(events, analysis)
                    prompt = next(row for row in data["humans"] + data["undated"] if row["id"] == "H001")
                    self.assertEqual(prompt["text"], quoted)
                    self.assertEqual(prompt["moment"]["note"], "The user set the scope.")
                    for width in (1280, 390):
                        page.set_viewport_size({"width": width, "height": 900})
                        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                        if width == 1280:
                            for media in ("screen", "print"):
                                page.emulate_media(media=media)
                                positions = page.locator(".stat").evaluate_all("""cards => cards.map(card => ({
                                    value:card.querySelector('b').getBoundingClientRect().top,
                                    label:card.querySelector('span').getBoundingClientRect().bottom
                                }))""")
                                for key in ("value", "label"):
                                    coordinates = [position[key] for position in positions]
                                    self.assertLessEqual(max(coordinates) - min(coordinates), 1)
                            page.emulate_media(media="screen")
            browser.close()

    def test_duration_display_rounds_to_whole_seconds_with_unit_carry(self):
        for milliseconds, expected in [
            (263621709, "3d 1h 13m 42s"),
            (960201, "16m"),
            (59999, "1m"),
            (86399999, "1d"),
            (201, "0s"),
            (500, "1s"),
            (0, "0s"),
        ]:
            with self.subTest(milliseconds=milliseconds):
                self.assertEqual(duration(milliseconds), expected)

    def test_duration_totals_sum_overlapping_turns_and_include_wall_gaps(self):
        collection = {"title": "Timing", "timezone": "UTC", "sources": [],
                      "missing_harnesses": {}, "warnings": []}
        start = epoch(TIME)
        events = [
            {"id": "H001", "kind": "user", "t": start, "session": "one", "turn_id": "first"},
            {"id": "H002", "kind": "user", "t": start + 1000, "session": "one", "turn_id": "second"},
            {"id": "R003", "kind": "assistant", "t": start + 2000, "session": "one", "turn_id": "first"},
            {"id": "R004", "kind": "assistant", "t": start + 3000, "session": "one", "turn_id": "second"},
            {"id": "H005", "kind": "user", "t": start + 500, "session": "two"},
            {"id": "R006", "kind": "assistant", "t": start + 2000, "session": "two"},
            {"id": "H007", "kind": "user", "t": start + 60000, "session": "one"},
        ]
        events = [{**event, "harness": "copilot", "timestamp": timestamp(event["t"]),
                   "source": f"S01:{i}", "text": event["id"]}
                  for i, event in enumerate(sorted(events, key=lambda event: event["t"]), 1)]
        analysis = analyze(collection, events, {"summary": "Test", "decisions": []})
        self.assertEqual(analysis["timing"]["wall_ms"], 60000)
        self.assertEqual(analysis["timing"]["total_turn_ms"], 5500)
        self.assertEqual(analysis["timing"]["timed_turns"], 3)
        self.assertEqual(analysis["timing"]["total_turns"], 4)
        html = report_html(collection, events, analysis)
        self.assertIn('id="wall-time">1m<', html)
        self.assertIn('id="total-turn-time">6s (partial)<', html)
        self.assertIn("3 of 4 turns timed", html)
        self.assertIn("overlaps summed", html)

    def test_report_chart_and_section_spacing_are_compact(self):
        from playwright.sync_api import sync_playwright
        collection = {"title": "Layout", "timezone": "America/Los_Angeles",
                      "sources": [], "missing_harnesses": {}, "warnings": []}
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            for count in (1, 8):
                events = [{"id": f"A{i:03d}", "identity": str(i), "kind": "usage", "t": None,
                           "timestamp": None, "harness": "copilot", "session": "test", "source": f"S01:{i}",
                           "model": f"model-{i}", "input": 100, "output": 10,
                           "cached_read": 0, "cache_write": 0} for i in range(count)]
                analysis = analyze(collection, events, {"summary": "Test", "decisions": []})
                page.set_content(report_html(collection, events, analysis))
                self.assertEqual(page.locator("header .eyebrow").inner_text(), "AGENT SESSION INSIGHTS")
                chart = page.locator(".chart").bounding_box()
                self.assertAlmostEqual(chart["height"] / chart["width"], count * 48 / 900, places=3)
                if count == 1:
                    self.assertLess(chart["height"], 64)
                section = page.locator("#usage").bounding_box()
                heading = page.locator("#usage h2").bounding_box()
                self.assertLessEqual(heading["y"] - section["y"], 18)
            browser.close()

    def test_report_interactions_are_visually_separate_cards(self):
        from playwright.sync_api import sync_playwright
        collection = {"title": "Interactions", "timezone": "America/Los_Angeles",
                      "sources": [], "missing_harnesses": {}, "warnings": []}
        events = [
            {"id": identifier, "kind": kind, "text": text, "harness": "copilot",
             "session": "test", "source": f"S01:{i}", "t": epoch(TIME) + i * 1000,
             "timestamp": timestamp(epoch(TIME) + i * 1000)}
            for i, (identifier, kind, text) in enumerate((
                ("H001", "user", "First prompt"), ("R001", "assistant", "First reply"),
                ("H002", "user", "Second prompt"), ("R002", "assistant", "Second reply"),
            ), 1)
        ]
        analysis = analyze(collection, events, {"summary": "Test", "decisions": [
            {"input": "H001", "response": "R001", "title": "First interaction",
             "category": "Scope", "analysis": "First analysis"},
            {"input": "H002", "response": "R002", "title": "Second interaction",
             "category": "Correction", "analysis": "Second analysis"},
        ]})
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            for width in (1280, 390):
                for theme in ("light", "dark"):
                    for media in ("screen", "print"):
                        with self.subTest(width=width, theme=theme, media=media):
                            page.set_viewport_size({"width": width, "height": 1000})
                            page.emulate_media(color_scheme=theme, media=media)
                            page.set_content(report_html(collection, events, analysis))
                            cards = page.locator("#directions .decision")
                            self.assertEqual(cards.count(), 2)
                            first, second = [cards.nth(i).bounding_box() for i in range(2)]
                            self.assertGreaterEqual(second["y"] - first["y"] - first["height"], 24)
                            for card in cards.all():
                                self.assertTrue(card.evaluate("""el => {
                                    const s = getComputedStyle(el);
                                    return parseFloat(s.borderTopWidth) > 0 && parseFloat(s.borderBottomWidth) > 0
                                        && parseFloat(s.borderRightWidth) > 0 && parseFloat(s.paddingTop) >= 16;
                                }"""))
                                self.assertEqual(card.locator(".quote").count(), 2)
                            self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
            browser.close()

    def test_model_comparison_aligns_token_totals_and_costs_with_distinct_shares(self):
        from playwright.sync_api import sync_playwright
        collection = {"title": "Comparison", "timezone": "UTC", "sources": [],
                      "missing_harnesses": {}, "warnings": [], "pricing": {"card": {
                          "currency": "USD", "unit": "per million tokens", "verified_on": "2026-11-07",
                          "sources": {"test": "https://example.com/pricing"}, "models": {
                              "premium": {"input": 2, "cache_read": 0.2, "cache_write": 2.5, "output": 10, "source": "test"},
                              "economy": {"input": 1, "cache_read": 0.1, "cache_write": 2, "output": 2, "source": "test"},
                          }}}}
        events = [
            {"id": "A001", "model": "premium", "input": 1000000, "output": 100000,
             "cached_read": 500000, "cache_write": 100000},
            {"id": "A002", "model": "economy", "input": 3000000, "output": 100000,
             "cached_read": 2000000, "cache_write": 0},
        ]
        events = [{**event, "kind": "usage", "identity": event["id"], "harness": "copilot",
                   "session": "test", "source": f"S01:{i}", "timestamp": TIME, "t": epoch(TIME)}
                  for i, event in enumerate(events, 1)]
        analysis = analyze(collection, events, {"summary": "Test", "decisions": []})
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(report_html(collection, events, analysis))
            rows = page.locator("#model-comparison tbody tr")
            self.assertEqual(rows.count(), 2)
            self.assertEqual(rows.filter(has_text="premium").locator("td").all_text_contents(),
                             ["premium", "1", "1,000,000", "100,000", "1,100,000", "26.2%", "$2.15", "60.6%"])
            self.assertEqual(rows.filter(has_text="economy").locator("td").all_text_contents(),
                             ["economy", "1", "3,000,000", "100,000", "3,100,000", "73.8%", "$1.40", "39.4%"])
            self.assertEqual(page.locator(".model-chart rect").count(), 4)
            self.assertIn("premium: cost share 60.6%", page.locator(".model-chart").text_content())
            details = page.locator("#model-cost-details")
            self.assertFalse(details.evaluate("el => el.open"))
            details.locator("summary").click()
            self.assertIn("500,000 / $0.10", details.inner_text())
            self.assertIn("not a model-efficiency benchmark", page.locator("#usage").inner_text())
            for width in (1280, 390):
                page.set_viewport_size({"width": width, "height": 900})
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
            page.set_viewport_size({"width": 794, "height": 1123})
            page.emulate_media(media="print")
            self.assertLessEqual(page.locator("#model-comparison table").bounding_box()["width"],
                                 page.locator("#usage").bounding_box()["width"])
            browser.close()

    def test_model_comparison_does_not_invent_shares_for_incomplete_costs_or_tokens(self):
        from playwright.sync_api import sync_playwright
        collection = {"title": "Incomplete comparison", "timezone": "UTC", "sources": [],
                      "missing_harnesses": {}, "warnings": [], "pricing": {"card": {
                          "currency": "USD", "unit": "per million tokens", "verified_on": "2026-11-07",
                          "sources": {"test": "https://example.com/pricing"}, "models": {
                              "known": {"input": 2, "cache_read": 0.1, "cache_write": 2.5, "output": 10, "source": "test"},
                          }}}}
        base = {"model": "known", "input": 1000000, "output": 100000, "cached_read": 0, "cache_write": 0}
        cases = [
            ([{**base, "cached_read_known": False, "cache_write_known": False}], "Range"),
            ([{**base, "input_complete": False}], "Unpriced"),
            ([{**base, "input": 0, "output": 0}], "$0.00"),
        ]
        def render(calls):
            events = [{**call, "id": f"A{i:03d}", "identity": str(i), "kind": "usage",
                       "harness": "copilot", "session": "test", "source": f"S01:{i}",
                       "timestamp": None, "t": None} for i, call in enumerate(calls, 1)]
            analysis = analyze(collection, events, {"summary": "Test", "decisions": []})
            page.set_content(report_html(collection, events, analysis))
            return page.locator("#model-comparison tbody tr")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            with self.subTest(expected="partial"):
                rows = render([base, {**base, "model": "unknown"}])
                self.assertEqual(rows.locator("td:last-child").all_text_contents(), ["100.0%", "Unpriced"])
                self.assertIn("Cost share (partial)", page.locator("#model-comparison thead").inner_text())
                self.assertIn("1 unpriced requests are excluded", page.locator("#model-comparison + .source").inner_text())
                self.assertEqual(page.locator('.model-chart rect[fill="var(--gold)"]').count(), 1)
                self.assertIn("Cost share unpriced", page.locator(".model-chart").text_content())
            for calls, expected in cases:
                with self.subTest(expected=expected):
                    rows = render(calls)
                    self.assertIn(expected, rows.inner_text() if rows.count() == 1 else rows.last.inner_text())
                    self.assertEqual(rows.locator("td:last-child").all_text_contents(), ["Unavailable"] * len(calls))
                    self.assertEqual(page.locator('.model-chart rect[fill="var(--gold)"]').count(), 0)
                    if any(not call.get("input_complete", True) for call in calls):
                        self.assertEqual(rows.locator("td:nth-child(6)").inner_text(), "Unavailable")
                        self.assertIn("incomplete", rows.locator("td:nth-child(5)").inner_text())
            browser.close()

    def test_collection_preserves_session_start_separately_from_selected_bounds(self):
        source = self.root / "native.jsonl"
        later = "2026-11-07T17:07:22.211Z"
        source.write_text("\n".join(json.dumps(record) for record in [
            {"type": "session.start", "timestamp": TIME, "data": {"sessionId": "native"}},
            {"type": "user.message", "id": "u1", "timestamp": later, "data": {"content": "Scoped request"}},
        ]))
        exported = self.root / "export.json"
        exported.write_text(json.dumps([{"role": "user", "timestamp": later, "content": "Exported request"}]))
        undated = self.root / "undated.json"
        undated.write_text(json.dumps([{"role": "user", "content": "Undated request"}]))
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"sources": [
            {"harness": "copilot", "session": "native", "path": str(source), "start": later},
            {"harness": "cursor", "session": "export", "path": str(exported)},
            {"harness": "replit", "session": "undated", "path": str(undated)},
        ]}))
        _, collection, _ = collect(manifest, self.root / "report")
        native, fallback, unknown = collection["sources"]
        self.assertEqual(native["started_at"], TIME)
        self.assertEqual(native["start"], later)
        self.assertEqual(native["started_at_basis"], "Session metadata")
        self.assertEqual(fallback["started_at"], later)
        self.assertIn("session start unavailable", fallback["started_at_basis"])
        self.assertIsNone(unknown["started_at"])

    def test_direction_analysis_scrolls_on_screen_and_prints_without_clipping(self):
        from playwright.sync_api import sync_playwright
        collection = {"title": "Long directions", "timezone": "UTC", "sources": [],
                      "missing_harnesses": {}, "warnings": []}
        events = [{"id": f"H{i:03d}", "kind": "user", "harness": "copilot", "session": "test",
                   "source": f"S01:{i}", "text": f"Request {i}", "timestamp": TIME, "t": epoch(TIME)}
                  for i in range(1, 21)]
        decisions = [{"input": event["id"], "title": f"Direction {i}", "category": "Scope",
                      "analysis": "An evidence-backed interpretation of the recorded request. " * 12}
                     for i, event in enumerate(events, 1)]
        analysis = analyze(collection, events, {"summary": "Test", "decisions": decisions})
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(report_html(collection, events, analysis))
            region = page.get_by_role("region", name="How the user shaped the work")
            self.assertEqual(region.locator(".decision").count(), 20)
            self.assertLessEqual(region.bounding_box()["height"], 420)
            self.assertTrue(region.evaluate("(el) => el.scrollHeight > el.clientHeight"))
            region.evaluate("(el) => el.scrollTop = el.scrollHeight")
            self.assertGreater(region.evaluate("(el) => el.scrollTop"), 0)
            page.emulate_media(media="print")
            self.assertGreater(region.bounding_box()["height"], 420)
            self.assertTrue(region.evaluate("(el) => el.clientHeight >= el.scrollHeight"))
            browser.close()

    def test_batched_filter_verification_rejects_incorrect_session_usage(self):
        collection = {"title": "Filter verification", "timezone": "UTC", "sources": [],
                      "missing_harnesses": {}, "warnings": []}
        start = epoch(TIME)
        events = []
        for i, session in enumerate(("one", "two")):
            common = {"harness": "copilot", "session": session, "timestamp": TIME}
            events.append({**common, "id": f"H{i+1:03d}", "kind": "user", "t": start + i * 60000,
                           "source": f"S01:{i*2+1}", "text": f"Input {session}"})
            events.append({**common, "id": f"A{i+1:03d}", "identity": f"call-{i}", "kind": "usage",
                           "t": start + i * 60000 + 1000, "source": f"S01:{i*2+2}", "text": "",
                           "model": "unpriced", "input": 100, "output": 10, "cached_read": 0, "cache_write": 0})
        analysis = analyze(collection, events, {"summary": "Test", "decisions": []})
        output = self.root / "incorrect-filter"
        output.mkdir()

        def faulty_player(data):
            return player_html(data).replace("</body>", """<script>
                document.getElementById('session').addEventListener('change', () => {
                    if (JSON.parse(document.getElementById('session').value)[1] === 'two')
                        document.getElementById('window-spend').textContent = '$999.00';
                });
            </script></body>""")

        with patch("render.player_html", side_effect=faulty_player), self.assertRaisesRegex(AssertionError, "999"):
            publish(output, collection, events, analysis)
        self.assertFalse((output / "verification.json").exists())

    def test_hundreds_of_sessions_and_turns_are_collapsed_without_pricing_footnotes(self):
        from playwright.sync_api import sync_playwright
        collection = {"title": "Large report", "timezone": "UTC", "missing_harnesses": {},
                      "warnings": ["A coverage warning"] * 200,
                      "sources": [{"id": f"S{i:02d}", "session": f"session-{i}",
                                   "started_at": TIME, "started_at_basis": "Session metadata",
                                   "start": None, "end": None, "raw_records": 10, "outside_interval": 0}
                                  for i in range(1, 201)],
                      "pricing": {"sha256": "f" * 64, "card": {
                          "currency": "USD", "unit": "per million tokens", "verified_on": "2026-11-13",
                          "models": {}, "sources": {}, "assumptions": ["Pricing footnote sentinel"]}}}
        events = [{"id": f"H{i:03d}", "kind": "user", "harness": "copilot", "session": f"session-{i}",
                   "source": f"S{i:02d}:1", "text": "Synthetic request", "timestamp": TIME, "t": epoch(TIME)}
                  for i in range(1, 201)]
        analysis = analyze(collection, events, {"summary": "Test", "decisions": []})
        html = report_html(collection, events, analysis)
        self.assertNotIn("SHA-256", html)
        self.assertNotIn("Frozen pricing", html)
        self.assertNotIn("Pricing footnote sentinel", html)
        self.assertNotIn("Evidence limits and method", html)
        self.assertIn("Observed activity (not measured working time)", html)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            page.set_content(html)
            for selector in ("#coverage-details", "#turn-spend-details", "#hourly-details"):
                details = page.locator(selector)
                self.assertFalse(details.evaluate("(el) => el.open"))
                self.assertFalse(details.locator("table").is_visible())
                details.locator("summary").click()
                self.assertTrue(details.locator("table").is_visible())
                self.assertLessEqual(details.locator(".detail-scroll").bounding_box()["height"], 420)
                self.assertEqual(details.locator("tbody tr").count(), 1 if selector == "#hourly-details" else 200)
            cells = page.locator("#coverage-details tbody tr").first.locator("td").all_text_contents()
            self.assertEqual(cells[0], "2026-11-07 17:06 UTC")
            self.assertIn("session-1", cells[1])
            page.emulate_media(media="print")
            self.assertGreater(page.locator("#coverage-details .detail-scroll").bounding_box()["height"], 420)
            browser.close()

    def test_copilot_form_answers_and_recorded_request_links_exclude_reasoning(self):
        events = self.load("copilot", [
            {"type": "user.message", "id": "human", "timestamp": TIME,
             "data": {"content": "Build the player", "interactionId": "interaction"}},
            {"type": "session.usage_record", "id": "usage", "timestamp": TIME,
             "data": {"usage": {"apiCallId": "request", "model": "m",
                               "inputTokens": 100, "outputTokens": 10, "cacheWriteTokens": 90}}},
            {"type": "assistant.message", "id": "tool-only", "timestamp": TIME,
             "data": {"apiCallId": "request", "interactionId": "interaction", "content": ""}},
            {"type": "human_response.recorded", "id": "answer", "timestamp": TIME,
             "data": {"actor": "human_response", "response": {
                 "responseKind": "ask_user", "content": {"framework": "Venn"}}}},
            {"type": "assistant.message", "id": "reasoning", "timestamp": TIME,
             "data": {"phase": "analysis", "content": "Not visible session text"}},
            {"type": "human_response.recorded", "id": "synthetic", "timestamp": TIME,
             "data": {"actor": "agent", "response": {
                 "responseKind": "ask_user", "content": {"framework": "Not a human answer"}}}},
        ])
        self.assertEqual([event["kind"] for event in events], ["user", "usage", "user"])
        self.assertEqual(events[0]["turn_id"], events[1]["turn_id"])
        self.assertEqual(events[1]["cache_write"], 90)
        self.assertEqual(json.loads(events[2]["text"]), {"framework": "Venn"})
        self.assertTrue(events[2]["form_response"])

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
        analysis = analyze(collection, events, {"summary": "Unknown timing", "decisions": [
            {"input": "H001", "title": "Unsafe example", "category": "Test", "analysis": "Escaped transcript"},
        ]})
        data = payload(events, analysis)
        self.assertIsNone(data["meta"]["start"])
        self.assertEqual(len(data["undated"]), 1)
        self.assertIsNone(analysis["input_tokens"])
        self.assertNotIn(events[0]["text"], player_html(data))
        self.assertIn("&lt;script&gt;", report_html(collection, events, analysis))
        self.assertEqual(epoch(TIME), 1794071182211)

    def test_report_does_not_duplicate_the_player_conversation(self):
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
        self.assertNotIn("Complete visible conversation", html)
        self.assertNotIn('id="record"', html)
        self.assertIn('src="player.html"', html)
        data = payload(events, analysis)
        self.assertEqual([row["text"] for row in data["undated"]], ["Build it", "Done"])
        self.assertIn("m&lt;script&gt;", html)

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
        self.assertTrue(all(event["harness"] == "copilot" for event in events))
        self.assertEqual(sum(event.get("form_response", False) for event in events), 3)
        self.assertIn("create a subdirectory, sessionreviewer", events[0]["text"])
        output = self.root / "example-review"
        (output / "analysis.json").write_text(json.dumps(analysis))
        publish(output, collection, events, analysis, "https://example.org/session/")
        verification = json.loads((output / "verification.json").read_text())
        self.assertEqual(verification["status"], "passed")
        self.assertEqual(verification["pdf_link_base_url"], "https://example.org/session/")


if __name__ == "__main__":
    unittest.main()
