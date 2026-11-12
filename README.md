# Agent Session Insights

A local-first reviewer agent and reproducible analysis pipeline for Copilot CLI,
Claude Code, Codex, Cursor exports, and Replit conversation exports. Reports and
standalone players use a fixed Venn visual framework. Content,
timeline, model usage, and spend are generated from the selected logs; the visual
theme itself is not generated anew for each session.

## Example paper and embedded replay

This repository includes a generated **project meta-report**: a report about the
**Copilot CLI conversation that built this project's reviewer, player and Git
repository**. It is not the earlier Codex conversation or original cross-harness
study used as development input.

- **Live demo:** [HTML report with embedded replay](https://markrussinovich.github.io/agent-session-insights/),
  [standalone player](https://markrussinovich.github.io/agent-session-insights/player.html),
  and [PDF paper](https://markrussinovich.github.io/agent-session-insights/report.pdf).

Repository copies for offline use:

- [Read the paper / meta-report (PDF)](examples/creation/report.pdf).
- [Open the HTML meta-report with its embedded player](examples/creation/report.html).
- [Open the standalone player](examples/creation/player.html).

Clone/download the repository and open these HTML files locally. The HTML report
embeds the adjacent `player.html`; both work offline. GitHub's file viewer does
not run the player inside the README.

## Run this exact session

Clone the repository, then run the bundled example with Python 3.12 or later:

```bash
git clone https://github.com/markrussinovich/agent-session-insights.git
cd agent-session-insights

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium

python review.py run \
  --manifest examples/creation.json \
  --output tmp/creation-review
```

For an existing checkout (`sessionreviewer/` in the original workspace), skip the
clone and `cd` commands and run from that repository root. Private-repository
access requires GitHub authentication.

Open **`tmp/creation-review/report.html`** for the report with embedded playback,
**`tmp/creation-review/report.pdf`** for the regenerated paper, or
**`tmp/creation-review/player.html`** for the standalone replay. No server, API key,
LLM call or GPU is needed. On Linux, if browser system libraries are missing, use
`python -m playwright install --with-deps chromium` (requires system-install privileges).
Choose a new output directory for each run: the pipeline refuses to overwrite a
nonempty one.

The manifest uses only repository-local files:

| Input | Purpose |
| --- | --- |
| [`examples/creation-session.jsonl`](examples/creation-session.jsonl) | Scoped, redacted Copilot CLI-format export of this project's creation |
| [`examples/creation-source.json`](examples/creation-source.json) | Original source hash, scope, export method and expected results |
| [`examples/creation-annotations.json`](examples/creation-annotations.json) | Eleven evidence-linked material directions; all selected inputs remain in replay |
| [`examples/pricing-rates.json`](examples/pricing-rates.json) | Frozen historical, cache-aware API-equivalent pricing |

The example is this **GitHub Copilot CLI** session,
**`53fca206-b87f-4fad-bd90-f09c26d61fac`**, scoped inclusively to
**2026-11-12 16:45:47.965–20:06:03.385 UTC**: from the `sessionreviewer` request
through the private GitHub push. Earlier prompt recovery and later Pages setup
are excluded.

Expect **15 human inputs (12 typed prompts and 3 form answers), 21 assistant
messages, 216 tool-call names, and 120 metered requests**, all using
`gpt-6.1-sol`: **18,500,781 inclusive input tokens**, **166,272 output tokens**,
**17,603,808 cached-read tokens**, **896,613 cache-write tokens**, and
**$5.6653533 estimated API-equivalent cost** using the bundled historical rates.
This is not an actual Copilot bill or newly verified pricing.

Historical replies discussing 22 prompts, 176 requests or $12.587361 refer to
the earlier Codex fixture used to develop the player, not the metrics of this
Copilot example. The report makes that distinction explicit.

The export is reconstructed from verified normalized evidence, not a byte-for-byte
copy of the full original history. It preserves the selected message text and
genuine human form answers after
best-effort credential and shared-session URL redaction, timestamps, identities, tool-call names and
per-request accounting. Opaque provider request identifiers are consistently
SHA-256 pseudonymized; recorded interaction IDs preserve prompt ownership.
It excludes unrelated history, system context, internal
reasoning, tool payloads and cumulative mirrors. No original workspace files are
required to rebuild the example.

Validate the implementation:

```bash
python -m unittest discover -s tests -v
node --check assets/player.js
```

Node is optional for the JavaScript syntax check; the Python tests and publisher
also exercise the player in Chromium.

Outputs: `report.html`, `report.pdf`, `player.html`, `analysis.json`,
`events.jsonl`, `collection.json`, `verification.json`, an optional frozen
`pricing.json`, and private raw snapshots.
Open `report.html` or `player.html` directly; neither requires a server.
The output directory is private (0700); files are 0600. It contains conversation
text and must be reviewed before sharing. Secret redaction is best effort.
The checked-in example contains conversation text too. Full raw histories and
runtime `private/` snapshots are not versioned; fresh runs recreate private
snapshots under the ignored `tmp/` output directory.

## Public GitHub Pages demo

The example conversation is approved for public sharing; the source repository
remains private. External shared-session URLs are replaced with
`[REDACTED_SHARED_SESSION_URL]` so publication does not expose another conversation.
Recorded usage and cost are unchanged by redaction.

The [Pages workflow](.github/workflows/pages.yml) regenerates and browser-verifies
the bundled example, then stages only its nine report/player/evidence assets plus
`index.html`. Raw snapshots, input histories, code, Git history and unrelated
files are never included in the Pages artifact. Staging rejects recognized
credentials and unredacted shared-session URLs; this is not a substitute for
reviewing a new conversation before approving it for publication.

To reproduce the public artifact locally, use fresh output directories:

```bash
python review.py run --manifest examples/creation.json --output tmp/pages-review \
  --public-base-url https://markrussinovich.github.io/agent-session-insights/
python scripts/prepare_pages.py --source tmp/pages-review --output tmp/pages-site
```

`--public-base-url` makes PDF hyperlinks point to the HTTPS site; HTML and player
links remain relative and offline-compatible. Omit it for a local-only review.
Pages is configured to use **GitHub Actions**. Changes to example inputs,
rendering code or the workflow on `main` redeploy the reviewed demo; a manual
workflow dispatch is also available.

## Agent, skills, and prompts

The agent profile is `.github/agents/agent-session-insights.agent.md`, with three
repository skills under `.github/skills/`: collect, analyze, and publish.
Open this directory as the agent workspace, or copy its `.github` definitions
into the destination repository. `prompts/review.md` is the reusable task prompt.
This package does not register agents globally or modify your personal settings.
The executable pipeline does not call an LLM; the agent supplies evidence-backed
interpretation in the optional annotations file.
For a new corpus, first run `review.py prepare --manifest MANIFEST --output DRAFT`
to obtain stable event IDs and measured usage without rendering. Read the resulting
evidence, write annotations referencing those IDs, add that file to the manifest,
point the final manifest at the prepared snapshots in the same source order, and
use `run` with a different empty output directory for the completed review.

See [DESIGN.md](DESIGN.md) for collection contracts, accounting rules, privacy,
analysis boundaries, and the player design.

## Supply other sessions

Create a JSON manifest with a title, timezone, and explicit source files. Paths
are relative to the manifest. Each source needs `harness`, `path`, and `session`.
Supported harness identifiers: `copilot`, `claude`, `codex`, `cursor`, `replit`.
Optional `start` and `end` are inclusive timezone-aware ISO timestamps.
An optional `annotations` path supplies human/agent-reviewed interpretation.
An optional `pricing` path supplies a dated, cache-aware USD-per-million-token
rate card (see [`examples/pricing-rates.json`](examples/pricing-rates.json) for the schema). The creation
example uses that historical snapshot, not newly verified prices. The card and
its SHA-256 are preserved with the results.

The player shows model calls, input/output tokens, estimated full-session spend,
spend in the zoomed/time-and-harness-filtered area, and a complete per-input ledger.
Session totals appear once in a compact strip; a visible-range subtotal appears
only when the time/harness scope differs. Model/pricing details are collapsed.
Each prompt has an attached model/cost/token footer, also shown on user entries
in the full session log and beneath prompts in the HTML/PDF conversation record.
Selected-turn totals cover the whole recorded turn, not just the visible window or
elapsed playback. Input includes cached reads/writes; costs subtract them before
pricing fresh input and apply per-request long-context premiums. Recorded turn IDs
are preferred; inferred ownership and unattributed requests are labeled.
Missing model prices produce partial subtotals, never invented zero-dollar bills.
No metered calls means unknown usage/spend. Undated calls count in session/turn
totals but cannot be placed in a zoomed time window.

```json
{
  "title": "Project review",
  "timezone": "America/Los_Angeles",
  "sources": [
    {"harness": "copilot", "path": "/approved/session/events.jsonl", "session": "session-id"},
    {"harness": "cursor", "path": "/approved/chat.md", "session": "export-id"}
  ]
}
```

Inventory only an explicitly approved directory:

```bash
python review.py discover --harness copilot --root /approved/session-directory
```

Discovery does not read conversation bodies. Review the inventory and select
files in the manifest; do not ingest unrelated sessions or another user's data.
For Cursor/Replit, see the export contract in DESIGN.md. Missing exports are
reported as unavailable, never represented as empty complete histories.
