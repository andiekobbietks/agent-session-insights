"""AgentSON v1.2 reader and byte-preserving source exporter."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

SCHEMA_DIR = Path(__file__).resolve().parent
SESSION_SCHEMA = SCHEMA_DIR / "agentson-v1.2.json"
ENTRY_SCHEMA = SCHEMA_DIR / "agentson-v1.2-entries.json"
TYPE_MAP = {
    "user-query": "user", "user-feedback": "user", "answer": "assistant",
    "action": "tool", "observation": "tool", "side-effect": "tool",
    "handoff": "tool", "presence": "tool", "capabilities": "tool",
    "system-event": "tool",
}


def _validator(path):
    try:
        import jsonschema
    except ImportError as error:
        raise ValueError("AgentSON validation requires jsonschema; install project dependencies.") from error
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"Bundled AgentSON schema is unavailable: {path}") from error
    return jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())


def _validate(value, validator, label):
    errors = sorted(validator.iter_errors(value), key=lambda e: tuple(str(part) for part in e.path))
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "$"
        raise ValueError(f"{label}: AgentSON v1.2 schema validation failed at {location}: {error.message}")


def _load(path):
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError(f"{path.name}: AgentSON must be UTF-8.") from error
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        records = []
        for line_number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path.name}:{line_number}: malformed AgentSON JSONL.") from error
            if not isinstance(record, dict):
                raise ValueError(f"{path.name}:{line_number}: JSONL records must be objects.")
            records.append((line_number, record))
        if not records:
            raise ValueError(f"{path.name}: empty AgentSON stream.")
        return "jsonl", records
    if isinstance(value, dict) and isinstance(value.get("entries"), list):
        return "document", value
    if isinstance(value, dict) and value.get("type"):
        return "jsonl", [(1, value)]
    raise ValueError(f"{path.name}: expected a v1.2 JSON document or JSONL stream beginning with stream-meta.")


def _timestamp(value, label):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label}: timestamps must be integer Unix milliseconds.")
    try:
        return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError) as error:
        raise ValueError(f"{label}: timestamp is outside the supported range.") from error


def _redact_tree(value, redact):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_redact_tree(item, redact) for item in value]
    if isinstance(value, dict):
        return {key: _redact_tree(item, redact) for key, item in value.items()}
    return value


def parse_agentson(path, spec, source_id, redact):
    """Validate AgentSON v1.2 and produce a privacy-aware reporting projection."""
    mode, loaded = _load(path)
    warnings = []
    if mode == "document":
        document = loaded
        if "agentson.dev/schema/v1.2.json" not in str(document.get("$schema", "")):
            raise ValueError(
                f"{path.name}: only canonical AgentSON v1.2 documents are accepted; "
                "legacy v1/v1.1 files need explicit migration."
            )
        _validate(document, _validator(SESSION_SCHEMA), path.name)
        entries = list(enumerate(document["entries"], 1))
        session_id = document["tool"]["session_id"]
        tool_name = document["tool"]["name"]
        agent_meta = document.get("agent") or {}
        source_meta = document.get("context") or {}
    else:
        records = loaded
        validator = _validator(ENTRY_SCHEMA)
        for line, record in records:
            _validate(record, validator, f"{path.name}:{line}")
        header_line, header = records[0]
        if header.get("type") != "stream-meta":
            raise ValueError(f"{path.name}:{header_line}: v1.2 JSONL must begin with stream-meta.")
        session_id = header["stream_id"]
        tool_name = (header.get("source") or {}).get("platform", "agentson")
        agent_meta = {}
        source_meta = header.get("source") or {}
        entries = records[1:]
    if str(spec.get("session")) != str(session_id):
        raise ValueError(
            f"{path.name}: manifest session {spec.get('session')!r} does not match "
            f"AgentSON stream/session ID {session_id!r}."
        )

    events, hidden_thoughts = [], 0
    for line, entry in entries:
        entry_type = entry["type"]
        if entry_type == "stream-meta":
            continue
        if entry_type == "thought":
            hidden_thoughts += 1
            continue
        kind = TYPE_MAP.get(entry_type)
        if kind is None:
            raise ValueError(f"{path.name}:{line}: unsupported AgentSON entry type {entry_type!r}.")
        event = {
            "harness": "agentson", "session": str(session_id), "kind": kind,
            "timestamp": _timestamp(entry.get("timestamp"), f"{path.name}:{line}"),
            "source": f"{source_id}:{line}",
            "identity": f"{entry_type}:" + str(entry.get("id") or entry.get("tool_call_id")
                            or entry.get("correlation_id") or f"{source_id}:{line}"),
            "agentson_entry_type": entry_type, "agentson_tool": redact(tool_name) if isinstance(tool_name, str) else tool_name,
            "agentson_agent": _redact_tree(entry.get("agent"), redact),
            "agentson_correlation_id": _redact_tree(entry.get("correlation_id"), redact),
            "agentson_provenance": _redact_tree(entry.get("provenance"), redact),
            "agentson_session_metadata": {
                "model": _redact_tree(agent_meta.get("name"), redact),
                "provider": _redact_tree(agent_meta.get("provider"), redact),
            },
        }
        if kind in ("user", "assistant"):
            event["text"] = redact(entry["text"])
        else:
            if entry_type == "action":
                event["name"] = redact(entry["tool"])
            elif entry_type == "side-effect":
                event["name"] = "AgentSON side-effect: " + redact(entry.get("action", "unknown"))
            else:
                event["name"] = f"AgentSON {entry_type}"
            detail = entry.get("text") or entry.get("output") or entry.get("message")
            if detail is None and entry_type == "action":
                detail = entry.get("code")
            if detail is None and entry_type == "side-effect":
                detail = entry.get("diff") or entry.get("path") or entry.get("action")
            if detail is None and entry_type == "capabilities":
                detail = entry.get("capabilities")
            if detail is not None:
                event["detail"] = _redact_tree(detail, redact) if isinstance(detail, str) else json.dumps(
                    _redact_tree(detail, redact), ensure_ascii=False, sort_keys=True
                )
            for field in ("status", "duration_ms", "tool_call_id", "from", "to", "reason", "context", "action", "path", "diff"):
                if field in entry:
                    event[f"agentson_{field}"] = _redact_tree(entry[field], redact)
        events.append(event)

    if hidden_thoughts:
        plural = "entry was" if hidden_thoughts == 1 else "entries were"
        warnings.append(
            f"{hidden_thoughts} AgentSON thought {plural} preserved in the private source snapshot "
            "but excluded from report events."
        )
    warnings.append(
        "AgentSON is imported as a reporting projection. The original v1.2 archive is preserved "
        "byte-for-byte; use agentson-export to retrieve it. Non-report fields are not reconstructed "
        "from normalized events."
    )
    warnings.append(
        "AgentSON session-level token/cost metadata is not treated as per-request usage; "
        "no authoritative per-request usage records were supplied."
    )
    return events, warnings, len(entries) + (1 if mode == "jsonl" else 0)


def export_source_snapshot(review_dir, source_id, destination):
    """Export an imported AgentSON source byte-for-byte after verifying its SHA-256."""
    review_dir = Path(review_dir).resolve()
    collection_path = review_dir / "collection.json"
    if not collection_path.is_file():
        raise ValueError(f"Not a review directory: {collection_path} is missing.")
    collection = json.loads(collection_path.read_text(encoding="utf-8"))
    source = next((item for item in collection.get("sources", []) if item.get("id") == source_id), None)
    if source is None:
        raise ValueError(f"Unknown source ID: {source_id}")
    if source.get("harness") != "agentson":
        raise ValueError(f"Source {source_id} is {source.get('harness')!r}, not AgentSON.")
    relative = Path(source["snapshot"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Unsafe snapshot path in collection metadata.")
    candidate = review_dir / relative
    if any(parent.is_symlink() for parent in (candidate, *candidate.parents) if parent != review_dir.parent):
        raise ValueError("AgentSON snapshot path contains a symbolic link.")
    snapshot = candidate.resolve()
    if review_dir not in snapshot.parents or not snapshot.is_file():
        raise ValueError("AgentSON snapshot is missing, unsafe, or outside the review directory.")
    data = snapshot.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != source.get("sha256"):
        raise ValueError("AgentSON source snapshot hash mismatch; refusing export.")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("xb") as stream:
            stream.write(data)
        destination.chmod(0o600)
    except FileExistsError as error:
        raise ValueError(f"Refusing to overwrite existing export: {destination}") from error
    return digest


def main():
    parser = argparse.ArgumentParser(
        description="Export an imported AgentSON archive byte-for-byte from a verified review snapshot."
    )
    parser.add_argument("--review-dir", type=Path, required=True)
    parser.add_argument("--source", required=True, help="Source ID from collection.json, for example S01.")
    parser.add_argument("--output", type=Path, required=True, help="New output path; existing files are never overwritten.")
    args = parser.parse_args()
    try:
        digest = export_source_snapshot(args.review_dir, args.source, args.output)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        parser.exit(1, f"AgentSON export failed: {error}\n")
    print(f"Verified byte-identical AgentSON export: {args.output.resolve()}")
    print(f"SHA-256: {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
