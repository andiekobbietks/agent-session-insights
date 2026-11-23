"""Explicit, read-only adapters for coding-agent histories and portable exports."""
import io
import json
import re
from datetime import datetime, timezone

HARNESSES = {"copilot": "GitHub Copilot", "claude": "Claude Code", "codex": "Codex",
             "dsh": "DeepSeek Harness", "cursor": "Cursor", "replit": "Replit"}
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
SECRET = re.compile(
    r"(?:sk-or-v1-|github_pat_|ghp_|gho_|hf_|sk-)[A-Za-z0-9_-]{15,}"
    r"|(?i:Bearer\s+)[A-Za-z0-9._~+/-]{16,}"
    r"|(?i:(?:api[_-]?key|password|access[_-]?token)\s*[=:]\s*)[\"']?[A-Za-z0-9_./+-]{12,}[\"']?"
)
SHARED_SESSION_URL = re.compile(
    r"https?://(?:www\.)?(?:claude\.ai|chatgpt\.com|chat\.openai\.com)"
    r"/share/[^\s<>'\"`)\]}]+", re.IGNORECASE
)


def redact(text):
    return SHARED_SESSION_URL.sub(
        "[REDACTED_SHARED_SESSION_URL]", SECRET.sub("[REDACTED_CREDENTIAL]", text)
    )


def textof(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            item.get("text", "") for item in value
            if isinstance(item, dict) and item.get("type") in ("text", "input_text", "output_text")
        )
    return ""


def timestamp(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("A boolean is not a timestamp.")
    if isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(value / 1000 if value > 10**11 else value, timezone.utc)
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("Timestamps must include a timezone; do not infer one.")
    else:
        raise ValueError("Unsupported timestamp; use timezone-aware ISO or Unix time.")
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def epoch(value):
    return round(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000) if value else None


def integer(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("Token counts must be nonnegative integers.")
    return value


def tokens(input_tokens, output_tokens, cached=None, written=None):
    result = dict(input=integer(input_tokens), output=integer(output_tokens),
                  cached_read=integer(cached) if cached is not None else 0,
                  cache_write=integer(written) if written is not None else 0,
                  cached_read_known=cached is not None, cache_write_known=written is not None)
    if result["cached_read"] + result["cache_write"] > result["input"]:
        raise ValueError("Cached input exceeds inclusive input tokens.")
    return result


def jsonl(path):
    if is_zstd(path):
        yield from compressed_jsonl(path)
        return
    with path.open(encoding="utf-8") as stream:
        for line, value in enumerate(stream, 1):
            if not value.strip():
                continue
            try:
                record = json.loads(value)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path.name}:{line}: malformed JSON") from error
            if not isinstance(record, dict):
                raise ValueError(f"{path.name}:{line}: expected a JSON object")
            yield line, record


def is_zstd(path):
    try:
        with path.open("rb") as stream:
            return stream.read(len(ZSTD_MAGIC)) == ZSTD_MAGIC
    except OSError:
        return False


def compressed_jsonl(path):
    """Stream concatenated Zstandard frames without assuming one frame per file."""
    try:
        import zstandard
    except ImportError as error:
        raise ValueError(
            f"{path.name}: compressed DSH session logs need the zstandard package; "
            "install agent-session-insights with its declared dependencies."
        ) from error
    try:
        with path.open("rb") as raw:
            reader = zstandard.ZstdDecompressor().stream_reader(raw, read_across_frames=True)
            with reader, io.TextIOWrapper(reader, encoding="utf-8") as stream:
                for line, value in enumerate(stream, 1):
                    if not value.strip():
                        continue
                    try:
                        record = json.loads(value)
                    except json.JSONDecodeError as error:
                        raise ValueError(f"{path.name}:{line}: malformed JSON") from error
                    if not isinstance(record, dict):
                        raise ValueError(f"{path.name}:{line}: expected a JSON object")
                    yield line, record
    except zstandard.ZstdError as error:
        raise ValueError(
            f"{path.name}: malformed or unsupported Zstandard session log ({error})"
        ) from error


def parse(path, spec, source_id):
    """Return canonical records, diagnostics, and raw record count before scoping."""
    harness, session = spec["harness"], spec["session"]
    events, warnings = [], []
    seen = {}

    def emit(kind, line, time, identity, **fields):
        identity = f"{source_id}:{identity}" if isinstance(identity, int) else str(identity)
        event = dict(harness=harness, session=session, kind=kind, timestamp=timestamp(time),
                     source=f"{source_id}:{line}", identity=identity, **fields)
        key = (kind, identity)
        if key in seen:
            prior = seen[key]
            if kind == "usage" and harness == "claude":
                for field in ("input", "output", "cached_read", "cache_write"):
                    prior[field] = max(prior[field], event[field])
                for field in ("cached_read_known", "cache_write_known", "input_complete"):
                    prior[field] = prior.get(field, False) or event.get(field, False)
                prior["timestamp"] = max(prior["timestamp"] or "", event["timestamp"] or "") or None
            elif any(prior.get(k) != event.get(k) for k in fields):
                warnings.append(f"Conflicting duplicate {kind} identity at {event['source']}; first record retained.")
            return
        seen[key] = event
        events.append(event)

    def message(role, line, time, identity, content, **fields):
        body = textof(content)
        if body:
            emit(role, line, time, identity, text=redact(body), **fields)

    if harness in ("cursor", "replit"):
        if path.suffix.lower() == ".md":
            records = markdown_messages(path, harness)
        elif path.suffix.lower() == ".json":
            value = json.loads(path.read_text(encoding="utf-8"))
            records = value.get("messages") if isinstance(value, dict) else value
            if not isinstance(records, list):
                raise ValueError(f"{harness}: expected a messages array; see DESIGN.md export contract.")
            records = list(enumerate(records, 1))
        else:
            raise ValueError(f"{harness}: provide a .md or .json export, not a live database.")
        for line, record in records:
            if not isinstance(record, dict):
                raise ValueError(f"{source_id}:{line}: expected an exported message object.")
            role = str(record.get("role", "")).lower()
            role = {"human": "user", "agent": "assistant", "cursor": "assistant",
                    "replit": "assistant"}.get(role, role)
            if role not in ("user", "assistant") or not textof(record.get("content")):
                raise ValueError(f"{source_id}:{line}: export needs a recognized role and nonempty content.")
            identity = record.get("id", line)
            time = record.get("timestamp")
            message(role, line, time, identity, record["content"], turn_id=record.get("turn_id"))
            usage = record.get("usage")
            if usage is not None:
                if role != "assistant" or not isinstance(usage, dict):
                    raise ValueError("Export usage must belong to an assistant message.")
                emit("usage", line, time, identity, model=record.get("model") or "unknown",
                     turn_id=record.get("turn_id"),
                     **tokens(usage["input_tokens"], usage["output_tokens"],
                              usage.get("cached_input_tokens"), usage.get("cache_write_tokens")))
        return events, warnings, len(records)

    model, raw_count, boundary = "unknown", 0, None
    request_turns = {}
    tool_names = {}
    for line, record in jsonl(path):
        raw_count += 1
        kind, time = record.get("type"), record.get("timestamp")
        if harness == "codex":
            payload = record.get("payload", {})
            if kind == "session_meta":
                boundary = payload.get("subagent_history_start_ordinal")
                if payload.get("thread_source") == "subagent" and boundary is None:
                    raise ValueError("Codex subagent export needs its copied-history boundary.")
            if boundary is not None and record.get("ordinal", 10**12) < boundary:
                continue
            if kind == "turn_context":
                model = payload.get("model") or model
            elif kind == "response_item":
                item_type, role = payload.get("type"), payload.get("role")
                if item_type == "message" and role in ("user", "assistant"):
                    body = textof(payload.get("content"))
                    if role == "user" and body.startswith((
                        "<environment_context>", "<user_instructions>", "# AGENTS.md",
                        "<permissions instructions>", "<environment_details>",
                    )):
                        continue
                    metadata = payload.get("internal_chat_message_metadata_passthrough") or {}
                    message(role, line, time, payload.get("id", line), payload.get("content"),
                            turn_id=payload.get("turn_id") or metadata.get("turn_id"))
                elif item_type in ("function_call", "custom_tool_call"):
                    emit("tool", line, time, payload.get("call_id", line),
                         name=payload.get("name") or "unknown")
            elif kind == "token_usage_record" and payload.get("thread_id") == session:
                usage = payload["usage"]
                emit("usage", line, time, payload.get("response_id", line), model=model,
                     turn_id=payload.get("turn_id"),
                     **tokens(usage["input_tokens"], usage["output_tokens"],
                              usage.get("cached_input_tokens"), usage.get("cache_write_input_tokens")))
        elif harness == "copilot":
            data = record.get("data") or {}
            identity = record.get("id", line)
            if kind == "user.message" and not data.get("source"):
                message("user", line, time, identity, data.get("content"),
                        turn_id=data.get("interactionId"))
            elif kind == "human_response.recorded" and data.get("actor") == "human_response":
                response = data.get("response") or {}
                if response.get("responseKind") == "ask_user" and response.get("content") is not None:
                    content = response["content"]
                    message("user", line, time, identity,
                            content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2),
                            form_response=True)
            elif kind == "assistant.message":
                if data.get("apiCallId") and data.get("interactionId"):
                    request_turns[data["apiCallId"]] = data["interactionId"]
                if data.get("phase") not in ("analysis", "reasoning"):
                    message("assistant", line, time, identity, data.get("content"),
                            request_id=data.get("apiCallId"), turn_id=data.get("interactionId"))
            elif kind == "session.usage_record":
                usage = data["usage"]
                identity = (usage.get("accounting") or {}).get("usageId") or usage.get("apiCallId") or identity
                emit("usage", line, time, identity, model=usage.get("model") or "unknown",
                     turn_id=usage.get("interactionId"),
                     **tokens(usage["inputTokens"], usage["outputTokens"],
                              usage.get("cacheReadTokens"), usage.get("cacheWriteTokens")))
            elif kind == "tool.execution_start":
                emit("tool", line, time, data.get("toolCallId", identity),
                     name=data.get("toolName") or data.get("name") or "unknown")
        elif harness == "claude":
            payload = record.get("message") or {}
            content = payload.get("content")
            identity = record.get("uuid", line)
            origin = (record.get("origin") or {}).get("kind")
            if kind == "assistant":
                message("assistant", line, time, identity, content, request_id=payload.get("id"))
                usage = payload.get("usage")
                if usage:
                    request = payload.get("id") or record.get("requestId") or identity
                    emit("usage", line, time, request, model=payload.get("model") or "unknown",
                         input_complete=("cache_read_input_tokens" in usage
                                         and "cache_creation_input_tokens" in usage),
                         **tokens(usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                                  + usage.get("cache_creation_input_tokens", 0), usage["output_tokens"],
                                  usage.get("cache_read_input_tokens"),
                                  usage.get("cache_creation_input_tokens")))
                for item in content if isinstance(content, list) else []:
                    if item.get("type") == "tool_use":
                        emit("tool", line, time, item.get("id", line), name=item.get("name", "unknown"))
            elif kind == "user" and not spec.get("subagent") and not record.get("isCompactSummary") and not record.get("isMeta"):
                body = textof(content)
                if origin == "human" or (origin is None and not body.startswith((
                    "<task-notification>", "<agent-message", "<system-reminder>",
                    "This session is being continued", "[Request interrupted",
                ))):
                    message("user", line, time, identity, content)
            elif kind == "attachment" and not spec.get("subagent"):
                attachment = record.get("attachment") or {}
                if attachment.get("type") == "queued_command" and (
                    attachment.get("humanTurn") or (attachment.get("origin") or {}).get("kind") == "human"
                ):
                    message("user", line, time, attachment.get("source_uuid") or identity,
                            attachment.get("prompt"))
        elif harness == "dsh":
            time = record.get("time")
            data = record.get("data") or {}
            if kind == "session":
                if record.get("version") != 4:
                    raise ValueError(
                        f"{path.name}: DSH session format v{record.get('version')} is unsupported; "
                        "export it from a current DeepSeek Harness version (session format v4)."
                    )
            elif kind == "request/context":
                model = data.get("model") or model
            elif kind == "user/message":
                source = data.get("source") or {}
                if source.get("kind") in ("user", "schedule"):
                    message("user", line, time, data.get("id", line), data.get("content"))
            elif kind == "assistant/message":
                payload = data.get("message") or {}
                identity = payload.get("id", line)
                source = payload.get("source") or {}
                model = source.get("model") or model
                message("assistant", line, time, identity, payload.get("content"),
                        request_id=identity, turn_id=data.get("turn"))
                usage = data.get("usage")
                if usage is not None:
                    if not isinstance(usage, dict):
                        raise ValueError("DSH assistant usage must be an object.")
                    output = usage["outputTokens"]
                    cached = usage.get("cacheReadTokens", 0)
                    written = usage.get("cacheWriteTokens", 0)
                    fresh = usage.get("inputTokens", 0)
                    total = usage.get("totalTokens")
                    # DSH totalTokens is inclusive of cache reads and fresh input.
                    inclusive = total - output if total is not None else fresh + cached + written
                    if inclusive < fresh:
                        raise ValueError("DSH usage disagrees with its inclusive token totals.")
                    emit("usage", line, time, identity, model=source.get("model") or model or "unknown",
                         turn_id=data.get("turn"),
                         **tokens(inclusive, output, cached, written))
            elif kind == "tool/call":
                tool_id = data.get("callId", line)
                tool_names[tool_id] = data.get("name") or "unknown"
                emit("tool", line, time, tool_id, name=tool_names[tool_id],
                     turn_id=data.get("turn"))
            elif kind == "tool/result":
                payload = data.get("message") or {}
                tool_id = payload.get("toolCallId") or data.get("toolCallId")
                if tool_names.get(tool_id) == "ask_user_question" and not payload.get("isError"):
                    body = textof(payload.get("content"))
                    try:
                        answers = json.loads(body).get("answers")
                    except (json.JSONDecodeError, AttributeError):
                        answers = None
                    if isinstance(answers, list) and answers:
                        message("user", line, time, tool_id or line,
                                json.dumps({"answers": answers}, ensure_ascii=False, indent=2),
                                form_response=True, turn_id=data.get("turn"))
        else:
            raise ValueError(f"Unsupported harness: {harness}")
    if harness == "copilot":
        for event in events:
            if event["kind"] == "usage" and not event.get("turn_id"):
                event["turn_id"] = request_turns.get(event["identity"])
    return events, warnings, raw_count


def markdown_messages(path, harness):
    result, body, role, start = [], [], None, 1
    fence = None
    heading = re.compile(r"^(?:#{1,6}\s+|\*\*)(User|Human|Assistant|Agent|Cursor|Replit)(?:\*\*)?\s*:?\s*$", re.I)
    for line, value in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        marker = re.match(r"^\s*(`{3,}|~{3,})", value)
        if marker:
            if fence is None:
                fence = marker.group(1)
            elif marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence):
                fence = None
        match = heading.match(value) if fence is None else None
        if match:
            if role is not None:
                result.append((start, dict(role=role, content="\n".join(body).strip())))
            role, start, body = match.group(1).lower(), line, []
        elif role is not None:
            body.append(value)
    if role is not None:
        result.append((start, dict(role=role, content="\n".join(body).strip())))
    if not result:
        raise ValueError(f"{harness}: no role-labeled messages found; see DESIGN.md export contract.")
    return result
