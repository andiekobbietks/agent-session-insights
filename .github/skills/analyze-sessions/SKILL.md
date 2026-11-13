---
name: analyze-sessions
description: Analyze measured usage and human/agent decision provenance without inventing timing, costs, or verified outcomes.
---

Read the complete visible conversation in scope, not just prompts containing
"report", "player", or another keyword. Preserve logistics in the replay even when
excluding them from the qualitative directions ledger.

Use DESIGN.md's token-accounting and timing rules. Inspect collection warnings and
coverage before interpreting metrics. Missing harnesses and missing usage are not zeros.
For a Python launch request with `"phase": "annotate"`, use the already prepared
evidence paths in that request. Read with file tools, write only its annotations
JSON, and stop. Do not run Python, preparation, tests, or publication.
Shell commands are prohibited except for Codex read-only inspection within the
generated temporary workspace when direct file-reading tools are unavailable;
the wrapper performs and verifies those stages after the harness exits.
The remaining preparation/publication commands apply to a full agent-led review.
Use `review.py prepare --manifest MANIFEST --output EMPTY_DRAFT_DIRECTORY` to freeze
the evidence and obtain event IDs before writing annotations. Publish with `run`
into a separate empty directory; the preparation directory is not a completed review.
Use those frozen snapshots in the final manifest, keeping source order and scope
unchanged so that live-log changes cannot invalidate reviewed evidence IDs.
Identify a small set of material choices: data scope, question formulation,
corrections, evidence standards, framing, experiments, product direction, and delivery.

Write the annotations JSON described in DESIGN.md. Each decision cites a genuine
input ID and, when available, a later response ID from the same conversation.
Use each input ID at most once; combine multiple observations about one input
into the same decision's analysis.
Explain what changed and what the evidence cannot establish. Label results as
agent-reported unless a separate artifact/test was actually inspected.
Do not rank models by quality from token share, infer original authorship from
pasted text, invent cost rates, or convert occupied bins into working hours.
When an approved dated pricing card is available, set the manifest `pricing` path.
Price fresh input separately from inclusive cache reads/writes, apply long-context
rules per request, and distinguish an API-equivalent estimate from a real bill.
Preserve unknown prices and no-usage turns as unknown. Prefer native turn IDs;
label inferred splits and retain unmatched requests in an unattributed bucket.
Reconcile per-turn plus unattributed tokens, requests and spend to session totals.

For an unannotated run, state that qualitative interpretation has not been supplied.
Do not fabricate annotations merely to make the report look complete.
