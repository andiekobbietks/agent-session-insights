import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentson import export_source_snapshot
from sources import parse
from review import collect


def records():
    return [
        {"type": "stream-meta", "stream_id": "agentson-test-001",
         "agents": [{"id": "agent-1", "name": "Test agent"}], "mode": "single" ,
         "timestamp": 1795780800000},
        {"type": "user-query", "agent": "user", "text": "Inspect the project.",
         "timestamp": 1795780801000, "provenance": {"source": "user", "confidence": "confirmed"}},
        {"type": "thought", "agent": "agent-1", "text": "private reasoning must not enter report events"},
        {"type": "action", "agent": "agent-1", "tool": "browser.navigate",
         "args": {"url": "https://example.test"}, "tool_call_id": "call-1",
         "status": "success", "timestamp": 1795780802000},
        {"type": "observation", "agent": "agent-1", "text": "Page loaded",
         "source": "tool", "correlation_id": "call-1", "timestamp": 1795780803000},
        {"type": "answer", "agent": "agent-1", "text": "The page loaded successfully.",
         "timestamp": 1795780804000},
        {"type": "handoff", "from": "agent-1", "to": "agent-2", "conch": "agent-2",
         "reason": "delegate review", "timestamp": 1795780805000},
        {"type": "presence", "agent": "agent-2", "status": "busy"},
        {"type": "capabilities", "agent": "agent-2", "capabilities": {"review": "Review code"}},
        {"type": "side-effect", "action": "file_write", "path": "src/example.py"},
        {"type": "user-feedback", "text": "Please add a test.", "timestamp": 1795780806000},
        {"type": "system-event", "text": "Run completed.", "source": "manual"},
    ]


class AgentSONReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def write_stream(self, values=None):
        path = self.root / "source.agentson"
        path.write_text("".join(json.dumps(item) + "\n" for item in (values or records())), encoding="utf-8")
        return path

    def test_v12_jsonl_import_preserves_types_and_hides_thought(self):
        path = self.write_stream()
        events, warnings, raw_count = parse(
            path, {"harness": "agentson", "session": "agentson-test-001"}, "S01"
        )
        self.assertEqual(raw_count, len(records()))
        self.assertEqual([event["agentson_entry_type"] for event in events], [
            "user-query", "action", "observation", "answer", "handoff",
            "presence", "capabilities", "side-effect", "user-feedback", "system-event",
        ])
        self.assertEqual(events[0]["kind"], "user")
        self.assertEqual(events[1]["kind"], "tool")
        self.assertEqual(events[1]["name"], "browser.navigate")
        self.assertEqual(events[1]["agentson_tool_call_id"], "call-1")
        self.assertEqual(events[2]["agentson_correlation_id"], "call-1")
        self.assertEqual(events[3]["kind"], "assistant")
        self.assertEqual(events[8]["agentson_entry_type"], "user-feedback")
        self.assertNotIn("form_response", events[8])
        self.assertEqual(events[7]["agentson_action"], "file_write")
        self.assertIn("Review code", events[6]["detail"])
        self.assertNotIn("private reasoning", json.dumps(events))
        self.assertTrue(any("thought entry was" in warning for warning in warnings))

    def test_v12_json_document_import(self):
        document = {
            "$schema": "https://agentson.dev/schema/v1.2.json",
            "id": "document-session",
            "tool": {"name": "opencode", "session_id": "document-session"},
            "agent": {"name": "model-x", "provider": "provider-y"},
            "entries": [{"type": "user-query", "text": "Question"},
                        {"type": "answer", "text": "Answer"}],
        }
        path = self.root / "session.AgentSON"
        path.write_text(json.dumps(document), encoding="utf-8")
        events, _warnings, count = parse(
            path, {"harness": "agentson", "session": "document-session"}, "S01"
        )
        self.assertEqual(count, 2)
        self.assertEqual([event["kind"] for event in events], ["user", "assistant"])
        self.assertEqual(events[0]["agentson_session_metadata"]["model"], "model-x")

    def test_legacy_v1_is_rejected_explicitly(self):
        path = self.root / "legacy.AgentSON"
        path.write_text(json.dumps({
            "$schema": "https://agentsong.dev/schema/v1.json", "id": "legacy",
            "tool": {"name": "opencode", "session_id": "legacy"}, "entries": [],
        }), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "only canonical AgentSON v1.2"):
            parse(path, {"harness": "agentson", "session": "legacy"}, "S01")

    def test_unknown_entry_type_is_rejected_by_schema(self):
        path = self.write_stream([
            {"type": "stream-meta", "stream_id": "agentson-test-001",
             "agents": [{"id": "agent-1"}], "mode": "single"},
            {"type": "made-up-event", "payload": "not a v1.2 event"},
        ])
        with self.assertRaisesRegex(ValueError, "schema validation failed"):
            parse(path, {"harness": "agentson", "session": "agentson-test-001"}, "S01")

    def test_session_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "does not match AgentSON"):
            parse(self.write_stream(), {"harness": "agentson", "session": "wrong"}, "S01")

    def test_export_is_byte_identical_and_refuses_overwrite(self):
        original = self.write_stream()
        data = original.read_bytes()
        review = self.root / "review"
        snapshot = review / "private" / "sources" / "S01.agentson"
        snapshot.parent.mkdir(parents=True)
        snapshot.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        (review / "collection.json").write_text(json.dumps({
            "sources": [{"id": "S01", "harness": "agentson",
                         "snapshot": "private/sources/S01.agentson", "sha256": digest}]
        }), encoding="utf-8")
        exported = self.root / "roundtrip.agentson"
        self.assertEqual(export_source_snapshot(review, "S01", exported), digest)
        self.assertEqual(exported.read_bytes(), data)
        with self.assertRaisesRegex(ValueError, "Refusing to overwrite"):
            export_source_snapshot(review, "S01", exported)

    def test_collect_then_export_is_byte_identical_end_to_end(self):
        original = self.write_stream()
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({
            "title": "AgentSON end-to-end",
            "timezone": "Europe/London",
            "sources": [{
                "harness": "agentson",
                "path": original.name,
                "session": "agentson-test-001",
            }],
        }), encoding="utf-8")
        review = self.root / "review"
        collect(manifest, review)
        exported = self.root / "exported.agentson"
        digest = export_source_snapshot(review, "S01", exported)
        self.assertEqual(exported.read_bytes(), original.read_bytes())
        collection = json.loads((review / "collection.json").read_text(encoding="utf-8"))
        self.assertEqual(collection["sources"][0]["sha256"], digest)
        self.assertEqual(collection["sources"][0]["harness"], "agentson")
        self.assertEqual(collection["sources"][0]["counts"]["tool"], 7)
        self.assertEqual(collection["sources"][0]["counts"]["user"], 2)
        self.assertEqual(collection["sources"][0]["counts"]["assistant"], 1)

    def test_credentials_are_redacted_from_report_but_original_is_exportable(self):
        values = records()
        values[0]["stream_id"] = "ghp_abcdefghijklmnopqrs"
        values[1]["text"] = "Use this credential: Bearer abcdefghijklmnopqrstuvwxyz123456"
        values[3]["tool_call_id"] = "ghp_zyxwvutsrqponmlkjihg"
        values[3]["args"] = {"note": "api_key=abcdefghijklmnop123456"}
        values[3]["provenance"] = {"url": "https://chatgpt.com/share/private-session-token"}
        original = self.write_stream(values)
        events, _warnings, _count = parse(
            original, {"harness": "agentson", "session": "ghp_abcdefghijklmnopqrs"}, "S01"
        )
        projection = json.dumps(events)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz123456", projection)
        self.assertNotIn("abcdefghijklmnop123456", projection)
        self.assertNotIn("private-session-token", projection)
        self.assertNotIn("ghp_abcdefghijklmnopqrs", projection)
        self.assertNotIn("ghp_zyxwvutsrqponmlkjihg", projection)
        self.assertIn("[REDACTED_CREDENTIAL]", projection)
        self.assertIn("[REDACTED_SHARED_SESSION_URL]", projection)

        review = self.root / "review"
        snapshot = review / "private" / "sources" / "S01.agentson"
        snapshot.parent.mkdir(parents=True)
        snapshot.write_bytes(original.read_bytes())
        digest = hashlib.sha256(original.read_bytes()).hexdigest()
        (review / "collection.json").write_text(json.dumps({
            "sources": [{"id": "S01", "harness": "agentson",
                         "snapshot": "private/sources/S01.agentson", "sha256": digest}]
        }), encoding="utf-8")
        exported = self.root / "private-original.agentson"
        export_source_snapshot(review, "S01", exported)
        self.assertEqual(exported.read_bytes(), original.read_bytes())

    def test_export_rejects_path_traversal_in_collection_metadata(self):
        review = self.root / "review"
        review.mkdir()
        (review / "collection.json").write_text(json.dumps({
            "sources": [{"id": "S01", "harness": "agentson",
                         "snapshot": "../outside.agentson", "sha256": "irrelevant"}]
        }), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Unsafe snapshot path"):
            export_source_snapshot(review, "S01", self.root / "escaped.agentson")

    def test_export_rejects_symlinked_source_snapshot(self):
        review = self.root / "review"
        source_dir = review / "private" / "sources"
        source_dir.mkdir(parents=True)
        outside = self.root / "outside.agentson"
        outside.write_text("{}\\n", encoding="utf-8")
        link = source_dir / "S01.agentson"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation is not available in this environment.")
        (review / "collection.json").write_text(json.dumps({
            "sources": [{"id": "S01", "harness": "agentson",
                         "snapshot": "private/sources/S01.agentson",
                         "sha256": hashlib.sha256(outside.read_bytes()).hexdigest()}]
        }), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            export_source_snapshot(review, "S01", self.root / "escaped.agentson")

    def test_export_refuses_tampered_snapshot(self):
        review = self.root / "review"
        snapshot = review / "private" / "sources" / "S01.agentson"
        snapshot.parent.mkdir(parents=True)
        snapshot.write_text("{}\n", encoding="utf-8")
        (review / "collection.json").write_text(json.dumps({
            "sources": [{"id": "S01", "harness": "agentson",
                         "snapshot": "private/sources/S01.agentson", "sha256": "wrong"}]
        }), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            export_source_snapshot(review, "S01", self.root / "nope.agentson")


if __name__ == "__main__":
    unittest.main()
