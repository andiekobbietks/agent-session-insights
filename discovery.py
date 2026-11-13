"""Metadata-only repository matching in the current user's native history stores."""
import ntpath
import os
from pathlib import Path
import posixpath

if __package__:
    from .sources import jsonl, redact, timestamp
else:
    from sources import jsonl, redact, timestamp

NATIVE_HARNESSES = ("copilot", "claude", "codex")


def history_roots():
    home = Path.home()
    copilot = Path(os.environ.get("COPILOT_HOME") or home / ".copilot").expanduser()
    claude = Path(os.environ.get("CLAUDE_CONFIG_DIR") or home / ".claude").expanduser()
    codex = Path(os.environ.get("CODEX_HOME") or home / ".codex").expanduser()
    return {
        "copilot": (copilot / "session-state",),
        "claude": (claude / "projects",),
        "codex": (codex / "sessions", codex / "archived_sessions"),
    }


def path_key(value):
    if not isinstance(value, str) or not value:
        return None
    if ntpath.splitdrive(value)[0]:
        if not ntpath.isabs(value):
            return None
        return ntpath.normcase(ntpath.normpath(value)).replace("\\", "/").rstrip("/") + "/"
    if value.startswith("/"):
        return posixpath.normpath(value).rstrip("/") + "/"
    return None


def matches_repository(repository, metadata):
    root = path_key(str(repository))
    git_root = path_key(metadata.get("git_root"))
    cwd = path_key(metadata.get("cwd"))
    return root == git_root if git_root else bool(cwd and cwd.startswith(root))


def session_metadata(path, harness):
    """Stop at native session metadata; never derive labels from message bodies."""
    title = None
    for _, record in jsonl(path):
        kind = record.get("type")
        if harness == "copilot":
            if kind != "session.start":
                continue
            data = record.get("data") or {}
            context = data.get("context") or {}
            return {
                "session": data.get("sessionId"),
                "cwd": context.get("cwd"),
                "git_root": context.get("gitRoot"),
                "started_at": timestamp(data.get("startTime") or record.get("timestamp")),
                "title": data.get("title") or data.get("summary"),
                "subagent": bool(data.get("parentSessionId")),
            }
        if harness == "codex":
            if kind != "session_meta":
                continue
            data = record.get("payload") or {}
            source = data.get("source")
            return {
                "session": data.get("id"),
                "cwd": data.get("cwd"),
                "started_at": timestamp(data.get("timestamp") or record.get("timestamp")),
                "title": data.get("title"),
                "subagent": (data.get("thread_source") == "subagent"
                             or source == "subagent"
                             or isinstance(source, dict) and "subagent" in source),
            }
        if harness == "claude":
            if kind in ("summary", "custom-title"):
                title = record.get("customTitle") or record.get("summary") or title
            if record.get("cwd") and record.get("sessionId"):
                return {
                    "session": record["sessionId"],
                    "cwd": record["cwd"],
                    "started_at": timestamp(record.get("timestamp")),
                    "title": title,
                    "subagent": bool(record.get("isSidechain")),
                }
    return None


def discover_repository(repository, harness=None, root=None):
    repository = Path(repository).expanduser().resolve()
    if not repository.is_dir():
        raise ValueError(f"Repository must be an accessible directory: {repository}")
    if harness is not None and harness not in NATIVE_HARNESSES:
        raise ValueError(
            f"{harness}: automatic repository discovery needs native session metadata. "
            "Use an explicitly selected Cursor/Replit export in a manifest instead."
        )
    if root is not None and harness is None:
        raise ValueError("--root requires --harness when discovering repository sessions.")
    if root is not None and not Path(root).expanduser().is_dir():
        raise ValueError(f"History root must be an accessible directory: {root}")
    roots = history_roots() if root is None else {harness: (Path(root).expanduser(),)}
    inventory = {"repository": str(repository), "sessions": [], "warnings": [], "unavailable": {}}
    patterns = {"copilot": "*/events.jsonl", "claude": "**/*.jsonl", "codex": "**/rollout-*.jsonl"}
    for name in (harness,) if harness else NATIVE_HARNESSES:
        available = False
        for directory in roots[name]:
            if not directory.is_dir():
                continue
            available = True
            try:
                paths = sorted(directory.glob(patterns[name]))
                for path in paths:
                    if not path.is_file() or path.is_symlink() or "subagents" in path.relative_to(directory).parts:
                        continue
                    try:
                        metadata = session_metadata(path, name)
                        if not metadata:
                            inventory["warnings"].append(f"No repository/session metadata: {path}")
                            continue
                        if metadata["subagent"] or not matches_repository(repository, metadata):
                            continue
                        session = metadata["session"]
                        if not isinstance(session, str) or not session:
                            raise ValueError("Missing session identifier in repository-matching metadata")
                        title = metadata["title"]
                        inventory["sessions"].append({
                            "harness": name, "session": session, "path": str(path.resolve()),
                            "cwd": metadata["cwd"], "started_at": metadata["started_at"],
                            "title": redact(title) if isinstance(title, str) else None,
                            "bytes": path.stat().st_size,
                        })
                    except (OSError, ValueError, TypeError, AttributeError) as error:
                        inventory["warnings"].append(f"Cannot inspect metadata in {path}: {error}")
            except OSError as error:
                inventory["warnings"].append(f"Cannot inventory {directory}: {error}")
        if not available:
            inventory["unavailable"][name] = "No accessible native history directory."
    inventory["sessions"].sort(key=lambda s: (s["harness"], s["session"], s["path"]))
    inventory["sessions"].sort(key=lambda s: s["started_at"] or "", reverse=True)
    return inventory


def select_sessions(inventory, identifiers=(), *, all_sessions=False):
    if all_sessions and identifiers:
        raise ValueError("--all cannot be combined with --session.")
    sessions = inventory["sessions"]
    if not sessions:
        raise ValueError(
            "No repository-matching sessions found. Check the repo path and history root; "
            "logs without native repository metadata need an explicit manifest."
        )
    if all_sessions:
        return sessions[:]
    if not identifiers:
        return sessions[:1]
    selected = []
    for identifier in identifiers:
        matches = [s for s in sessions if identifier in (s["session"], f'{s["harness"]}:{s["session"]}')]
        if len(matches) != 1:
            raise ValueError(
                f"Session selection {identifier!r} matched {len(matches)} files; "
                "use --harness or a harness:session identifier, or select exact paths in a manifest."
            )
        if matches[0] not in selected:
            selected.append(matches[0])
    return selected
