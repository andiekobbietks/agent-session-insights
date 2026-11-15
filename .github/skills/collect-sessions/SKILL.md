---
name: collect-sessions
description: Find sessions by repository path, support user selection, and freeze authorized histories with explicit coverage and provenance.
---

Read DESIGN.md's collection contracts. Confirm scope from the operator request;
ask only for unresolved scope/access/export questions and the output location.
For a Python launch request with `"phase": "annotate"`, collection is already
complete: read the request and its frozen evidence with file tools, then perform
only the reviewed-annotation stage. Do not run discovery or preparation,
and do not change the selected sessions or output location. Shell commands are
prohibited except for Codex read-only inspection within the generated temporary
workspace when direct file-reading tools are unavailable.
For a repository path, run `python review.py discover --repo REPO_PATH`; optionally
add the requested `--harness NAME`. This inspects repository/session metadata in
the current user's native Copilot, Claude, and Codex stores, not message content
for keyword matches. No global home-directory scan or other-user search is allowed.
An explicitly supplied nonstandard native history root can be used with
`--repo REPO_PATH --harness NAME --root HISTORY_DIRECTORY`.

List matching dates, metadata titles when available, harnesses and session IDs.
Use `ask_user` for multi-selection when the user wants to pick sessions, or when
a generic report request has multiple matches and no explicit selection. Honor
explicit IDs or "all"; a single match may be automatic for a generic report.
Ask for the output directory after selection if none was given, suggesting the
absolute `agent-session-reports/<repo-name>` path in the current workspace.
Use the target repository's directory name. Do not generate
until the user supplies/confirms that path. Never overwrite without explicit
confirmation. The Python CLI prompts before replacing an existing review and
defaults to No; directories with unrelated files or selected inputs are refused.
An inventory-only request needs no output prompt. The command-line `repo` command
automatically picks the first match and launches its harness (`--noagent` opts out).
When receiving its JSON launch request, use the already selected manifest/output
without rediscovery or additional selection prompts. Otherwise the agent must not
use that shortcut instead of the requested session-selection/output-confirmation
steps. The Python wrapper prepares and publishes for an annotation-only request.
For other agent requests, use `prepare` and `run`, not recursive `repo` calls.

For already approved history directories, the original file-only inventory is
`python review.py discover --harness NAME --root PATH`. Neither discovery mode
ingests whole conversations; only selected sources enter the report.

For Copilot choose events.jsonl; for Claude choose project JSONL including genuine
queued human messages; for Codex choose rollout JSONL. Include explicitly relevant
subagents only as separate identified sources; do not recount copied parent context.
For Cursor/Replit request an existing private export or a complete role-labeled copy.
Convert that copy to the documented interchange contract without inventing timestamps
or usage. Never assume a project ZIP is a conversation history.

Write a manifest listing exact paths, harness/session IDs, optional inclusive
start/end, title, timezone, and missing harnesses with reasons. Scope is an interaction,
not merely keyword matches. The pipeline freezes each source, hashes it, records
provenance, and reports unsupported/unknown records. Stop on malformed or conflicting
scope instead of silently publishing incomplete results.
