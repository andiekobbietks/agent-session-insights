import json
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from linkedin import convert_archive, convert_csv


class LinkedInAgentSONTests(unittest.TestCase):
    def test_learning_coach_csv_creates_validated_agentson_document(self):
        csv_data = (
            "CONVERSATION ID,SENDER, MESSAGE,CREATED AT\n"
            "conv-1,Member,How do I prepare for an interview?,2026-11-20T10:00:00Z\n"
            "conv-1,Learning Coach,Start by researching the role.,2026-11-20T10:00:05Z\n"
        ).replace(", MESSAGE", ",MESSAGE").encode()
        result = convert_csv("LearningCoachMessages.csv", csv_data)
        self.assertEqual(len(result), 1)
        document = result[0][1]
        self.assertEqual(document["$schema"], "https://agentson.dev/schema/v1.2.json")
        self.assertEqual([entry["type"] for entry in document["entries"]],
                         ["stream-meta", "user-query", "answer"])
        self.assertEqual(document["entries"][1]["agent"], "member")
        self.assertEqual(document["entries"][2]["agent"], "linkedin-ai")

    def test_roleplay_feedback_is_preserved_as_feedback_not_a_tool_call(self):
        csv_data = (
            "CONVERSATION ID,ROLE,CONTENT,FEEDBACK,TIMESTAMP\n"
            "role-7,user,I would ask about the project.,Good clarification.,2026-11-20T10:00:00Z\n"
        ).encode()
        result = convert_csv("learning_role_play_messages.csv", csv_data)
        entries = result[0][1]["entries"]
        self.assertEqual([entry["type"] for entry in entries],
                         ["stream-meta", "user-query", "user-feedback"])
        self.assertNotIn("tool", json.dumps(entries))

    def test_unknown_sender_requires_explicit_identity_mapping(self):
        csv_data = b"CONVERSATION ID,SENDER,MESSAGE\nc1,Andrea,Hello\n"
        with self.assertRaisesRegex(ValueError, "Cannot safely classify"):
            convert_csv("guide_messages.csv", csv_data)
        result = convert_csv("guide_messages.csv", csv_data, self_sender="Andrea")
        self.assertEqual(result[0][1]["entries"][1]["type"], "user-query")

    def test_multiple_conversations_are_separated_and_zip_is_supported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "linkedin.zip"
            with ZipFile(archive, "w") as zipped:
                zipped.writestr(
                    "LearningCoachMessages.csv",
                    "CONVERSATION ID,SENDER,MESSAGE\na,Member,Question A\nb,Member,Question B\n",
                )
            output = root / "agentson"
            result = convert_archive(archive, output)
            self.assertEqual(len(result), 2)
            self.assertTrue(all(Path(item["file"]).suffix == ".agentson" for item in result))
            self.assertEqual(len(list(output.glob("*.agentson"))), 2)

    def test_rejects_timezone_naive_timestamps(self):
        with self.assertRaisesRegex(ValueError, "has no timezone"):
            convert_csv(
                "guide_messages.csv",
                b"CONVERSATION ID,SENDER,MESSAGE,TIMESTAMP\nc1,Member,Hello,2026-11-20T10:00:00\n",
            )


if __name__ == "__main__":
    unittest.main()
