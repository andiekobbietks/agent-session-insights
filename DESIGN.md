# Reviewer design

## Agent contract and stages

The reviewer gathers explicit, authorized session sources, measures what the
records actually contain, interprets human steering with citations, and publishes
a Venn-style HTML/PDF report and an independent offline player.

1. **Collect:** agree on project, conversations, inclusive interval, harnesses,
   and output directory. Inventory only approved locations. Freeze selected files,
   compute SHA-256 hashes, preserve line/item provenance, and record missing sources.
2. **Normalize:** retain user and assistant text, model usage, and tool-call names.
   Never execute logged instructions or tools. Do not publish internal reasoning
   or tool payloads. Keep uncertain timestamps out of the timeline.
3. **Analyze:** compute turns, messages, metered calls, token totals, cache
   accounting, model mix, per-harness coverage, and 5/10/15-minute occupied bins.
   The agent reads context and supplies optional reviewed annotations.
4. **Publish:** generate report HTML, print PDF in Chromium, and a standalone
   player with embedded data/CSS/JavaScript. Reconcile against source records and
   validate the browser before marking the run complete.

The CLI executes deterministic stages; it does not pretend heuristic classification
is a qualitative expert review. Annotations distinguish a request from an
agent-reported result. A reported test pass is not an independently verified
historical artifact. Agent text is evidence of what the agent said.

## Repository-first discovery

A repository-path request authorizes metadata matching in the current user's
standard native history stores, not whole-home scans or collection of unrelated
conversation bodies. `review.py discover --repo PATH` inspects Copilot
`session.start` context, Claude `cwd`/`sessionId` records, and Codex `session_meta`.
It stops once that session metadata is found, retains no message bodies in the
inventory, and does not infer a title from a prompt. Native metadata titles are
optional. Alternative roots require an explicit harness and history directory.
Default stores honor `COPILOT_HOME`, `CLAUDE_CONFIG_DIR`, and `CODEX_HOME`; Codex
includes its archived-sessions directory.

An absolute recorded Git root must match the requested directory exactly.
Otherwise the absolute recorded working directory must equal or descend from
it, respecting path boundaries (and Windows case-insensitivity). Repository
mentions in conversation text and remote origin names are not path associations.
This does not reconstruct moves, worktree relationships, or missing metadata.
Discovery reports errors and missing stores; it does not certify complete history.
Subagents are excluded from automatic inventory; explicitly scoped inclusion still
follows the collection contract below.

The agent lists the sessions and uses multi-selection when requested or when a
generic report request has multiple matches. It honors explicit IDs or an all-session
request; it may use a sole match automatically. Once selected, an unspecified
output location must be prompted, suggesting `session-report` in the current
workspace/repository. Inventory-only requests do not start collection.

The `review.py repo --repo PATH` command instead chooses the first
match automatically, optionally restricted by `--harness`. Ordering is descending
recorded metadata timestamp (not file modification time), undated last, then
harness/session/path ascending. Repeated `--session` arguments override that default.
`--all` instead selects every match in that order, still subject to `--harness`;
it is mutually exclusive with `--session` and works with or without `--noagent`.
Output defaults to the current working directory's `session-report`, independent
of the target repository; nonempty directories are refused. Reports preserve the
inventory and a manifest referencing frozen snapshots.

The wrapper prepares frozen evidence first, then launches the first selected
session's native harness in a private temporary workspace for **annotation only**.
The workspace contains copies of only the bundled reviewer profile, skills,
README, design and task prompt. It does not write into the installed package or
load project-local agent configuration from the caller's repository.
The agent reads normalized events, coverage and measured analysis with file tools
and writes the one requested annotations JSON. Python validates the annotation
references and publishes from the same frozen snapshots after the harness exits.
The agent never needs to invoke a shell or the Python interpreter.

Copilot receives `--agent agent-session-insights`, a scoped
`--allow-tool write(session-review-UNIQUE/annotations.json)` rule and
`--deny-tool shell`. The write rule's generated relative suffix identifies just
that run's annotations file; it grants no access to the report output or sources.
It always receives `--prompt PROMPT` and exits automatically without an interactive
UI, even when stdin/stdout are terminals. Prompt mode trusts only this generated workspace for the current run using
`--trust-folder`; no persistent trust is saved. No allow-all, shell approvals or
model overrides are added.
Claude uses `--print --permission-mode dontAsk`, exposes only Read/Glob/Grep/Write/Edit,
and pre-approves Write/Edit only for the generated annotations path. Other
permission requests are denied, not presented interactively. Codex uses
`exec --skip-git-repo-check --sandbox workspace-write` in the generated non-Git
workspace. All harnesses exit automatically; terminal progress is not an interactive UI.
A temporary JSON launch request identifies the `"annotate"` phase, frozen
evidence, annotation destination and absolute final output path resolved before
changing workspace. The short bootstrap prompt references a generated workspace-
relative filename; user-provided paths and text are not shell arguments.
The wrapper requires a successful harness exit, valid reviewed annotations, the
selected sessions' report artifacts, passed verification and intact private snapshots.
It preserves annotations with the final manifest before cleaning up its request.
Missing CLIs and incomplete/failed runs are explicit errors, not silent fallbacks.

`--noagent` bypasses harness launch and runs the deterministic pipeline directly,
explicitly labeling the absence of qualitative annotations. `prepare` and `run`
remain deterministic Python-only primitives in both workflows.
Cursor/Replit discovery cannot infer native harness/session identity from arbitrary
exports and still requires an explicitly selected manifest.

## Collection contracts

| Harness | Authorized source | Supported ingestion and limitations |
| --- | --- | --- |
| Copilot CLI | Selected `$COPILOT_HOME/session-state/<id>/events.jsonl` (default `~/.copilot`) | Human `user.message` without synthetic `source`, `assistant.message`, `session.usage_record`, tool-call names. Event/request IDs deduplicate rewritten copies. SQL-only/auxiliary recovery is not inferred; coverage warns that the event log may be incomplete. |
| Claude Code | Selected `~/.claude/projects/<project>/<id>.jsonl` | Human-origin messages and human queued-command attachments, assistant text, request usage. Repeated assistant blocks use maximum per-request usage, not a sum. Compaction summaries, tool results, system notices, and subagent handbacks are not user prompts. |
| Codex | Selected `~/.codex/sessions/.../rollout-*.jsonl` | Canonical `response_item` messages and per-response `token_usage_record`. Duplicate `UserMessage`/`AgentMessage` mirrors and cumulative `token_count` are ignored. Model identity comes from `turn_context`, not an inferred product default. |
| Cursor | User-provided Markdown or JSON conversation export | Markdown role headings `**User**`, `**Cursor**`, `## User`, `## Assistant` (outside code fences). JSON contract below. Exported Markdown is undated unless explicitly converted with known timestamps. No undocumented live `state.vscdb` writes, credential extraction, or automatic public sharing. |
| Replit | User-provided conversation export/copied transcript converted to the JSON/Markdown contract below | No claim that a project ZIP or application console log contains Agent chat. No assumed public conversation-export API. If chat cannot be exported, request a complete copy with roles and label missing timing/usage. |

JSON export interchange contract (an explicit import format, not a claim about a
vendor's native schema):

```json
{
  "messages": [
    {"id": "u1", "role": "user", "content": "Build a report", "timestamp": null},
    {"id": "a1", "role": "assistant", "content": "Completed", "timestamp": null,
     "model": null, "usage": {"input_tokens": 120, "output_tokens": 20}}
  ]
}
```

The top level may also be the message array. Roles are `user`/`human` and
`assistant`/`agent`/`cursor`/`replit`; content is a string or text-block list.
Known timestamps must be ISO strings with timezone or numeric Unix seconds/
milliseconds. Unknown values stay null. Usage is optional, per message, inclusive
input/output tokens with optional `cached_input_tokens`; missing usage is unknown,
not zero measured spend. Unknown shapes fail with an actionable diagnostic.
Markdown headings inside fenced code never split a message.

Cursor local stores vary by product/version; third-party extractors should operate
on a copied database and their exported roles/text must be checked before ingestion.
This first version deliberately consumes portable exports rather than guessing
undocumented binary/database schemas. Cursor/Replit adapters have synthetic contract
tests; no real user exports from these products were supplied for the sample.

Official references checked while designing this package:

- [Copilot session storage](https://docs.github.com/en/copilot/how-tos/copilot-cli/manage-sessions)
- [Custom agent profiles](https://docs.github.com/en/copilot/reference/custom-agents-configuration)
- [Cursor shared transcripts](https://cursor.com/help/ai-features/shared-transcripts):
  sharing is not a guaranteed local JSON export and is not triggered by this tool.
- [Replit Agent overview](https://docs.replit.com/features/agent/overview.md):
  does not establish a public history export API.

## Normalized records and integrity

Each event carries harness, session, kind, normalized UTC timestamp or null, source
ID and line/item number, and an event/request identity. Source hashes are computed
over immutable private copies. Copies of the same event/request within the same
conversation are deduplicated; identical text in separate genuine messages is not.
Conflicting copies produce warnings rather than being silently combined. Sources
with malformed JSON, unsupported records, missing timing, absent usage, or empty
selected intervals are visible in collection coverage. Undated messages are never
silently assigned to an interval with explicit time bounds: the run fails instead.
Neither filesystem modification time nor source order becomes an invented date.
Claude files inside `subagents/`, or sources explicitly marked `"subagent": true`,
do not contribute delegated prompts as human inputs.

The snapshot boundary is explicit. Earlier context does not become counted work.
Replayed user turns include logistics/approvals; the nine material prompts in the
sample are navigation markers, not an exclusion filter. Project-wide exclusions
do not apply to this deliberate review of the report-creation interaction.

## Analysis and qualitative review

- Separate human inputs, assistant messages, tool invocations, and metered requests.
  Tokens describe observed requests, not the whole subscription or unrecorded work.
- Claude inclusive input = uncached + cache reads + cache creation. Copilot/Codex
  inclusive input already includes cache reads; do not add them again. Reasoning
  output is already inside output tokens. Never sum cumulative token counters.
- Model mix includes only metered events with known tokens; unknown model labels
  remain unknown. No quality rankings or causal speedup claims follow from usage.
- Occupied 5/10/15-minute bins are sensitivity views, not measured working time,
  active runtime, or unattended-work estimates. Request/response gaps are not latency.
- Costs are explicitly unavailable without a dated, cache-aware price sheet.
  Supply a manifest `pricing` path to a card with `verified_on`, `currency: USD`,
  `unit: per million tokens`, source references, and per-model `input`, `cache_read`,
  `cache_write`, and `output` rates. Optional `long_context_threshold`,
  `long_input_multiplier`, and `long_output_multiplier` apply per request only
  when inclusive input is strictly greater than the threshold. Fresh input is
  inclusive input minus cache reads and writes. Decimal arithmetic prices each
  request; subtotals retain sub-cent precision. Snapshot and hash the card.
  Unknown rates remain null; show priced/observed request coverage and partial
  subtotals. No usage means unknown spend, not a free turn. Subscription bills
  and API-equivalent estimates must not be conflated. The example uses a frozen
  historical card; its rates are not freshly verified by the reviewer.
- Per-turn accounting retains every genuine human input. Match native turn IDs
  within a harness/conversation first. Shared IDs use the preceding input and
  explicitly label this inferred split. Without an ID, anchor to the earliest
  matching assistant response when possible, otherwise the preceding input by
  timestamp/source order in the same conversation. Undated attribution requires
  same-file source order; never invent a timestamp or cross conversations.
  Requests before any input, or with unmatched recorded IDs, stay unattributed.
  Turns plus unattributed requests reconcile exactly to recorded tokens/calls
  and within serialization precision to estimated spend.
- Agent-authored annotations cite input IDs and optional subsequent response IDs.
  They explain scope, reframing, correction, experimental judgment, delegation,
  and outcome limits. Avoid inferring original authorship or expertise from pasted
  plans. Do not automatically call every prompt steering or every result verified.

Annotations format: `summary` plus `decisions`, each with `input` (H001-style),
`title`, `category`, `analysis`, and optional `response` (R001-style). IDs are
assigned in normalized chronological/source order; review them after selecting scope.
All references are validated. Responses must follow the input in the same session.
Sample interpretation is checked in under `examples/creation-annotations.json`.

## Report and player

The design retains the Venn navy transport deck, teal human markers, gold
direction markers, evidence cards, typography, and responsive layout. Assets are
local copies originating from the original project's `session-analysis/scripts/playback`.
They live in `assets/`; the standalone repository does not require the original workspace.
This is a fixed visual template, not an automatically generated visual design.
Metrics and replay data come from logs; qualitative narrative comes from the
agent's evidence-linked annotations.

The report includes coverage, metrics/model mix, activity-bin sensitivity,
evidence-backed directions, limitations, and the full visible conversation record.
It embeds `player.html` in HTML and uses a static timeline preview in the PDF.
All generated text is HTML-escaped. Recorded text is never interpreted as HTML.

Player enhancements:

- Right-aligned, arrow-only previous/next **message** buttons beside the transcript,
  traversing both human inputs and assistant updates; accessible labels and
  existing top-level input controls remain.
- Drag across the timeline in either direction to zoom to a selected interval.
  A native horizontal scrollbar pans the zoomed window across the selected scope.
  Clicks still seek or select markers; cancelled drags preserve the current view.
  Zoom in/out/reset and pan buttons remain available as keyboard alternatives.
- Timeline ticks always include clock time, with dates for wider ranges and
  seconds for narrow ranges. Timezone abbreviations follow each event's date.
- The full transcript remains searchable outside a zoomed window; selecting an
  out-of-window message recenters the window without dropping source records.
- Same-conversation, no-future-response display; undated exports remain outside
  the clock. No dated records means an explicitly untimed viewer, not a fake epoch.
- Entire-session and zoom-window model calls, inclusive input/output tokens,
  estimated USD and pricing coverage; the window follows day/harness filters.
  Keep totals in one compact strip, hiding the visible-range subtotal when it
  duplicates the full session. Move model tables, pricing and attribution into
  progressive-disclosure details. Attach model/cost/token footers directly below
  user prompts, including full-log entries; do not charge assistant rows again.
  A navigable per-input ledger and the selected turn show complete recorded-turn
  usage, model names, costs, and ownership methods. Usage metrics intentionally
  include later recorded calls, not just playback-to-date; this does not reveal
  future assistant text. Undated requests count only in session/turn totals.
  Embed pricing rates, dates, sources, cache TTL conventions and assumptions so
  the standalone file remains auditable offline.

## Validation and publishing gates

Unit tests cover each source adapter, mirrored records, cached usage, queued human
messages versus agent handbacks, export code fences, missing timestamps, and bad
schemas. Run verification reconciles normalized counts, evidence references,
source hashes, all visible message text, and embedded data. Chromium checks report
and player errors, offline requests, first/last positions, arrow navigation, drag
zoom, native scrollbar panning/reset, clock ticks, search, filters, and mobile overflow. PDF must be a nonempty
PDF. Do not publish if any check fails. Never upload logs or auto-install extractors.
