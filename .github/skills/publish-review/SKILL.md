---
name: publish-review
description: Generate and verify Venn-style HTML/PDF analysis and a standalone zoomable session player.
---

For a Python launch request with `"phase": "annotate"`, do not execute publication,
tests or shell commands. Write reviewed annotations only; the wrapper performs
all deterministic publication and verification after the harness exits.
The following instructions apply to a full agent-led review.

Run unit tests, then `review.py run --manifest MANIFEST --output EMPTY_DIRECTORY`.
Use the user-supplied or confirmed output directory. If none was supplied, ask
first, suggesting `session-report` in the current workspace/repository. Keep
preparation output and manifests outside that final empty directory.
The pipeline must keep raw snapshots private and embed redacted text only.

Validate full prompt/assistant coverage against source, not a proxy such as the number
of material markers. Verify annotation references, request deduplication, source hashes,
embedded player data, all messages, PDF output, and browser behavior.
Check right-aligned message arrows, drag-to-zoom, native scrollbar panning/reset, clock ticks, playback,
search, source references, filters, and mobile layout. Confirm zero network requests.
Check session/model/turn token totals, estimated spend and priced-call coverage.
Confirm drag, scrollbar, reset, day and harness filters change only the window
metrics, not entire-session or whole-turn accounting. Include the frozen pricing
card/hash and assumptions; undated requests stay outside the zoomed time window.
Keep the session total in one compact location and hide the range subtotal when
unfiltered. Check that model/cost/token metadata sits beneath the user prompt and
on each human full-log entry, not above the transcript or repeated on agent rows.

Do not modify historical reports or source logs. Do not publish automatically.
On successful completion, list only report.html, report.pdf and player.html with
their absolute paths. Keep supporting evidence, verification, images, optional
pricing/annotations/manifests and private snapshots in the output directory,
but do not list them. The Python CLI prints the three report paths.
If any check fails, fix the implementation and rerun into a fresh output directory,
or report the failed gate plainly; do not hide missing messages or weaken assertions.
