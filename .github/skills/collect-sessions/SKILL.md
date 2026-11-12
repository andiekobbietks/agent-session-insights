---
name: collect-sessions
description: Inventory and freeze authorized Copilot, Claude, Codex, Cursor, and Replit histories with explicit coverage and provenance.
---

Read DESIGN.md's collection contracts. Confirm scope from the operator request;
ask only for unresolved scope/access/export questions. Inventory approved roots
with `review.py discover --harness NAME --root PATH`; do not scan all home directories.

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
