"""Convert supported LinkedIn data-export conversation CSVs to AgentSON v1.2.

This adapter consumes a member-provided archive; it does not log in to LinkedIn
or access Premium account data. Source exports remain the user's responsibility.
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import sys
from zipfile import BadZipFile, ZipFile

try:
    from .agentson import _validate, _validator
except ImportError:
    from agentson import _validate, _validator

SCHEMA_URL = "https://agentson.dev/schema/v1.2.json"
AI_EXPORT_MARKERS = (
    "guide_messages", "learning_coach_messages", "learningcoachmessages",
    "learning_role_play_messages", "learning_roleplay_messages",
    "ai_powered_conversations", "ai-powered conversations",
    "learning coach ai takeout", "learning coach inbox",
    "learning coach roleplay inbox",
)

ALIASES = {
    "conversation": ("conversation_id", "conversationid", "thread_id", "threadid", "chat_id"),
    "message": ("message", "message_content", "content", "text", "body", "utterance", "transcript"),
    "sender": ("sender", "sender_name", "author", "role", "participant", "speaker", "from"),
    "sender_url": ("sender_profile_url", "senderprofileurl", "profile_url", "author_url"),
    "timestamp": ("created_at", "createdat", "timestamp", "sent_at", "date", "time"),
    "message_id": ("message_id", "messageid", "id", "uuid"),
    "question": ("question", "prompt", "user_question"),
    "answer": ("answer", "response", "assistant_response", "coach_response"),
    "feedback": ("feedback", "insight", "coach_feedback", "roleplay_feedback"),
}


def _key(value):
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def _column(headers, aliases):
    normalized = {_key(header): header for header in headers}
    return next((normalized[name] for name in aliases if name in normalized), None)


def _timestamp(value):
    if value is None or str(value).strip() == "":
        return None
    raw = str(value).strip()
    if re.fullmatch(r"-?\d+(?:\.\d+)?", raw):
        number = float(raw)
        return round(number if abs(number) > 10**11 else number * 1000)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"Unsupported LinkedIn timestamp {raw!r}; expected ISO 8601 or Unix time.") from error
    if parsed.tzinfo is None:
        raise ValueError(f"LinkedIn timestamp {raw!r} has no timezone; refusing to infer one.")
    return round(parsed.astimezone(timezone.utc).timestamp() * 1000)


def _role(row, sender_col, sender_url_col, role_col, self_sender, self_profile_url, ai_export=False):
    sender = str(row.get(sender_col) or "").strip() if sender_col else ""
    sender_url = str(row.get(sender_url_col) or "").strip() if sender_url_col else ""
    if self_profile_url and sender_url and sender_url.rstrip("/").lower() == self_profile_url.rstrip("/").lower():
        return "user", "confirmed"
    if ai_export and self_profile_url and sender_url:
        return "assistant", "inferred"
    value = sender.lower()
    if value in {"user", "human", "member", "you", "me", "learner", "student"}:
        return "user", "confirmed"
    if value in {"assistant", "ai", "bot", "coach", "learning coach", "linkedin ai", "linkedin"}:
        return "assistant", "confirmed"
    if self_sender and sender.casefold() == self_sender.casefold():
        return "user", "confirmed"
    if not sender and not sender_url and not role_col:
        raise ValueError("LinkedIn rows need a sender/role column or an explicit question/answer pair.")
    raise ValueError(
        f"Cannot safely classify LinkedIn sender {sender!r}. Supply --self-sender or --self-profile-url "
        "so member and AI messages are not misattributed."
    )


def _safe_id(value):
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value)).strip("-_")
    return (value or "linkedin-session")[:120]


def _read_csv_bytes(name, data):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError(f"{name}: LinkedIn CSV must be UTF-8 or UTF-8 with BOM.") from error
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if not reader.fieldnames:
        raise ValueError(f"{name}: CSV has no header row.")
    return reader.fieldnames, list(reader)


def _inputs(source, include_all):
    source = Path(source)
    if source.is_dir():
        for path in sorted(source.rglob("*.csv")):
            relative = path.relative_to(source).as_posix()
            if include_all or any(marker in relative.lower() for marker in AI_EXPORT_MARKERS):
                yield relative, path.read_bytes()
    elif source.suffix.lower() == ".zip":
        try:
            with ZipFile(source) as archive:
                for item in archive.infolist():
                    if item.is_dir() or not item.filename.lower().endswith(".csv"):
                        continue
                    if include_all or any(marker in item.filename.lower() for marker in AI_EXPORT_MARKERS):
                        if item.file_size > 50 * 1024 * 1024:
                            raise ValueError(f"{item.filename}: CSV exceeds the 50 MiB safety limit.")
                        yield item.filename, archive.read(item)
        except BadZipFile as error:
            raise ValueError(f"{source}: invalid ZIP archive.") from error
    elif source.suffix.lower() == ".csv":
        yield source.name, source.read_bytes()
    else:
        raise ValueError("Input must be a LinkedIn ZIP archive, extracted directory, or CSV file.")


def convert_csv(name, data, *, self_sender=None, self_profile_url=None, source_sha256=None, source_snapshot=None):
    headers, rows = _read_csv_bytes(name, data)
    conv_col = _column(headers, ALIASES["conversation"])
    message_col = _column(headers, ALIASES["message"])
    sender_col = _column(headers, ALIASES["sender"])
    sender_url_col = _column(headers, ALIASES["sender_url"])
    time_col = _column(headers, ALIASES["timestamp"])
    id_col = _column(headers, ALIASES["message_id"])
    question_col = _column(headers, ALIASES["question"])
    answer_col = _column(headers, ALIASES["answer"])
    feedback_col = _column(headers, ALIASES["feedback"])

    if not message_col and not (question_col or answer_col or feedback_col):
        raise ValueError(f"{name}: no recognized message, question, answer, or feedback column.")
    groups = {}
    for index, row in enumerate(rows, 2):
        conversation = str(row.get(conv_col) or "").strip() if conv_col else ""
        if not conversation:
            conversation = Path(name).stem
        groups.setdefault(conversation, []).append((index, row))

    output = []
    for conversation, items in groups.items():
        entries = [{
            "type": "stream-meta",
            "stream_id": _safe_id(f"linkedin-{Path(name).stem}-{conversation}"),
            "mode": "single",
            "agents": [
                {"id": "member", "name": "LinkedIn member"},
                {"id": "linkedin-ai", "name": "LinkedIn AI experience"},
            ],
        }]
        for row_number, row in items:
            stamp = _timestamp(row.get(time_col)) if time_col else None
            common = {"timestamp": stamp} if stamp is not None else {}
            if message_col and str(row.get(message_col) or "").strip():
                role, confidence = _role(
                    row, sender_col, sender_url_col, sender_col,
                    self_sender, self_profile_url,
                    ai_export=any(marker in name.lower() for marker in AI_EXPORT_MARKERS),
                )
                entries.append({
                    "type": "user-query" if role == "user" else "answer",
                    "agent": "member" if role == "user" else "linkedin-ai",
                    "text": str(row[message_col]).strip(),
                    "provenance": {"source": "export", "confidence": confidence},
                    "source_file": name,
                    "source_row": row_number,
                    **common,
                })
            else:
                if question_col and str(row.get(question_col) or "").strip():
                    entries.append({
                        "type": "user-query", "agent": "member",
                        "text": str(row[question_col]).strip(),
                        "provenance": {"source": "export", "confidence": "confirmed"},
                        "source_file": name, "source_row": row_number, **common,
                    })
                if answer_col and str(row.get(answer_col) or "").strip():
                    entries.append({
                        "type": "answer", "agent": "linkedin-ai",
                        "text": str(row[answer_col]).strip(),
                        "provenance": {"source": "export", "confidence": "confirmed"},
                        "source_file": name, "source_row": row_number, **common,
                    })
            if feedback_col and str(row.get(feedback_col) or "").strip():
                entries.append({
                    "type": "observation", "source": "other", "agent": "linkedin-ai",
                    "text": str(row[feedback_col]).strip(),
                    "provenance": {"source": "export", "confidence": "confirmed"},
                    "source_file": name, "source_row": row_number, **common,
                })
        if len(entries) == 1:
            continue
        session_id = entries[0]["stream_id"]
        document = {
            "$schema": SCHEMA_URL,
            "id": session_id,
            "tool": {"name": "other", "session_id": session_id},
            "agent": {"name": "LinkedIn AI", "provider": "LinkedIn"},
            "task": f"Imported LinkedIn conversation from {Path(name).name}",
            "context": {
                "platform": "LinkedIn",
                "url": "https://www.linkedin.com",
                "source_archive": {
                    "path": source_snapshot,
                    "sha256": source_sha256,
                    "media_type": "text/csv",
                } if source_sha256 and source_snapshot else {},
            },
            "entries": entries,
        }
        validator_path = Path(__file__).resolve().parent / "agentson-v1.2.json"
        _validate(document, _validator(validator_path), name)
        output.append((session_id, document))
    return output


def convert_archive(source, output, *, self_sender=None, self_profile_url=None, include_all=False):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("Output directory must be empty; existing files will not be overwritten.")
    created = []
    seen = set()
    files = list(_inputs(source, include_all))
    if not files:
        raise ValueError(
            "No supported LinkedIn AI conversation CSVs found. Use --all-csv to inspect other CSV exports."
        )
    snapshot_dir = output / "private" / "source-csv"
    snapshot_dir.mkdir(parents=True, mode=0o700)
    snapshot_dir.parent.chmod(0o700)
    snapshot_dir.chmod(0o700)
    for name, data in files:
        source_digest = hashlib.sha256(data).hexdigest()
        snapshot_name = _safe_id(name) + "-" + source_digest[:16] + ".csv"
        snapshot_path = "private/source-csv/" + snapshot_name
        snapshot_file = snapshot_dir / snapshot_name
        with snapshot_file.open("xb") as stream:
            stream.write(data)
        snapshot_file.chmod(0o600)
        for session_id, document in convert_csv(
            name, data, self_sender=self_sender,
            self_profile_url=self_profile_url,
            source_sha256=source_digest, source_snapshot=snapshot_path,
        ):
            filename = _safe_id(session_id) + ".agentson"
            if filename in seen or (output / filename).exists():
                raise ValueError(f"Refusing to overwrite or collide with output {filename}.")
            seen.add(filename)
            destination = output / filename
            payload = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
            with destination.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
            destination.chmod(0o600)
            created.append({
                "file": str(destination.resolve()),
                "session_id": session_id,
                "entries": len(document["entries"]),
                "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                "source_file": name,
            })
    if not created:
        raise ValueError("No conversations could be converted from the selected LinkedIn export files.")
    return created


def main():
    parser = argparse.ArgumentParser(
        description="Convert your own LinkedIn AI conversation export CSVs into validated AgentSON v1.2 files."
    )
    parser.add_argument("--input", required=True, type=Path, help="LinkedIn ZIP, extracted export folder, or CSV.")
    parser.add_argument("--output", required=True, type=Path, help="New or empty output directory.")
    parser.add_argument("--self-sender", help="Exact sender label used for your own messages in the CSV.")
    parser.add_argument("--self-profile-url", help="Your LinkedIn profile URL, used to identify your messages.")
    parser.add_argument("--all-csv", action="store_true", help="Also inspect CSVs outside known AI/Learning Coach categories.")
    args = parser.parse_args()
    try:
        results = convert_archive(
            args.input, args.output, self_sender=args.self_sender,
            self_profile_url=args.self_profile_url, include_all=args.all_csv,
        )
    except (OSError, ValueError, KeyError, csv.Error) as error:
        parser.exit(1, f"LinkedIn AgentSON conversion failed: {error}\n")
    for item in results:
        print(f"{item['file']}  ({item['entries']} entries, SHA-256 {item['sha256']})")
    print(f"Converted {len(results)} conversation(s) to AgentSON v1.2.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
