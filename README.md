# Agent Session Insights

A local-first reviewer agent and reproducible analysis pipeline for Copilot CLI,
Claude Code, Codex, Cursor exports, and Replit conversation exports. Reports and
standalone players show the conversation, timeline, model usage, token accounting,
and evidence-linked analysis in a fixed Venn visual framework.

## Quick start

Complete the [setup](#setup-and-bundled-example) with Python 3.12 or later, then
run from the repository root using the Python environment where you installed
the dependencies. If you have not activated the virtual environment on Windows,
replace `python` below with `.\.venv\Scripts\python.exe`.

```powershell
# Report on the newest matching session; launch its harness for reviewed annotations:
python review.py repo --repo C:\Source\my-project

# Combine all matching sessions into one report:
python review.py repo --repo C:\Source\my-project --all --output .\tmp\all-sessions-review

# List matching sessions without generating a report:
python review.py discover --repo C:\Source\my-project

# Generate a report directly in Python, without an agent or model calls:
python review.py repo --repo C:\Source\my-project --noagent --output .\tmp\python-review
```

By default, the command selects **one session, newest first**, detects its harness,
and launches that CLI for qualitative annotations. The CLI must be installed,
on `PATH`, and signed in; normal model charges apply. Use `--all` for every match,
or repeat `--session ID` for selected sessions. Automatic discovery supports
Copilot, Claude and Codex; Cursor/Replit require explicit exports.

Output defaults to **`session-report` in your current working directory**.
Every generated file is listed at completion, including `report.html`,
`report.pdf`, and the standalone `player.html`. Existing nonempty directories
are never overwritten; use a fresh `--output` path for each report.

Open the default report in your browser:

```powershell
Start-Process .\session-report\report.html
```

For natural-language session selection and output-location prompts, see
[Report on a repository](#report-on-a-repository). For all command options, see
[Automatic Python command](#automatic-python-command).

## Example report and embedded replay

The bundled example reviews the Copilot CLI conversation that built this project.

- **Live demo:** [HTML report with embedded replay](https://markrussinovich.github.io/agent-session-insights/),
  [standalone player](https://markrussinovich.github.io/agent-session-insights/player.html),
  and [PDF report](https://markrussinovich.github.io/agent-session-insights/report.pdf).

Repository copies for offline use:

- [Read the report (PDF)](examples/creation/report.pdf).
- [Open the HTML report with its embedded player](examples/creation/report.html).
- [Open the standalone player](examples/creation/player.html).

Clone/download the repository and open these HTML files locally. The HTML report
embeds the adjacent `player.html`; both work offline. GitHub's file viewer does
not run the player inside the README.

## Setup and bundled example

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

On Windows PowerShell, run these commands from the repository root. This uses
the virtual environment directly, so activation is not required:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe review.py run `
  --manifest .\examples\creation.json `
  --output .\tmp\creation-review
```

For an existing checkout, skip the clone and `cd` commands.

Open **`tmp/creation-review/report.html`** for the report with embedded playback,
**`tmp/creation-review/report.pdf`** for the PDF report, or
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

The example manifest selects a scoped, redacted session export and includes
reviewed annotations and a frozen historical pricing card. The rates are not
current price quotes, and API-equivalent estimates are not actual Copilot bills.

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

Report outputs: `report.html`, `report.pdf`, `player.html`, `timeline.png`,
`analysis.json`, `events.jsonl`, `collection.json`, `verification.json`, an
optional frozen `pricing.json`, and private raw snapshots.
Repository runs also save `discovery.json`, a reusable `manifest.json`, and
`annotations.json` when the report is agent-reviewed.
Open `report.html` or `player.html` directly; neither requires a server.
On POSIX, the output directory is restricted to 0700 and files to 0600. On
Windows, Python's `chmod` does not set NTFS ACLs, so output inherits the parent
directory's ACL. The output contains conversation text and must be reviewed
before sharing. Secret redaction is best effort.
The checked-in example contains conversation text too. Full raw histories and
runtime `private/` snapshots are not versioned; fresh runs recreate private
snapshots under the ignored `tmp/` output directory.

## GitHub Pages demo

External shared-session URLs are replaced with `[REDACTED_SHARED_SESSION_URL]`.
Review conversation text and generated files before deploying a report.

The [Pages workflow](.github/workflows/pages.yml) regenerates and browser-verifies
the bundled example, then stages only its nine report/player/evidence assets plus
`index.html`. Raw snapshots, input histories, code, Git history and unrelated
files are never included in the Pages artifact. Staging rejects recognized
credentials and unredacted shared-session URLs; this is not a substitute for
reviewing a new conversation before approving it for publication.

To build the demo site locally, use fresh output directories:

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
The `repo` command launches a harness by default; `prepare`, `run`, and
`repo --noagent` execute only the deterministic Python pipeline. The agent
supplies evidence-backed interpretation in the annotations file.
For a new corpus, first run `review.py prepare --manifest MANIFEST --output DRAFT`
to obtain stable event IDs and measured usage without rendering. Read the resulting
evidence, write annotations referencing those IDs, add that file to the manifest,
point the final manifest at the prepared snapshots in the same source order, and
use `run` with a different empty output directory for the completed review.

## Report on a repository

With the **agent-session-insights** agent selected in this workspace, say:

> What sessions are there for `C:\Source\my-project`? I want to pick one or more for a report.

Or:

> Make a report for `C:\Source\my-project`.

The agent finds matching native Copilot, Claude, and Codex sessions and lists their
recorded dates, harnesses, IDs and metadata titles when available. If you ask to
pick, it prompts for one or more sessions. A generic report request uses a sole
match or asks you to select if several match; you can also explicitly request all.
After selection, if you have not supplied an output path, it prompts with
**`session-report` in the current workspace/repository** as the suggestion.
The agent then prepares evidence, adds reviewed qualitative annotations, and
generates the HTML report, PDF and offline player. Existing nonempty results are
never overwritten.

### Automatic Python command

After [setup](#setup-and-bundled-example), run from this repository:

```powershell
python review.py repo --repo C:\Source\my-project
```

Or use `.\.venv\Scripts\python.exe` instead of `python` if you installed the
dependencies into the virtual environment. No manifest or session/output selection
prompts are needed. This command detects the harness from its native history store and
**picks the first matching session**: newest recorded metadata timestamp first,
undated entries last, then harness/session/path as deterministic tie-breakers.
By default it **launches that harness** (`copilot`, `claude`, or `codex`) with a
prompt to run the reviewer and write evidence-linked qualitative annotations.
The installed CLI must be on `PATH` and signed in; its normal permissions,
model configuration and charges apply. Copilot selects the bundled custom agent;
Claude and Codex receive instructions to read the same reviewer profile and skills.
Python freezes the selected logs before launching the harness. The agent only
reads the prepared evidence and writes the annotations JSON; Python then validates
the references, renders the report and performs browser/PDF checks.

The harness starts in this package's workspace. Copilot is granted write approval
only for that run's annotations file, and shell execution is explicitly denied.
It uses interactive mode when stdin/stdout are terminals; otherwise it uses
noninteractive prompt mode, so a report launched from a captured shell command
does not depend on unavailable approval prompts. No unrestricted shell or
allow-all permissions are granted. In an interactive harness, exit after the
annotations are written so the Python wrapper can publish the report.

Output defaults to **`.\session-report` in your original current working directory**,
not automatically inside the repository passed to `--repo` or the harness's
workspace. Use `--output` to change it. With several explicitly selected sessions,
or with `--all`, the first selected session's harness performs the combined review.

```powershell
# Only Codex sessions, with an explicit destination:
python review.py repo --repo C:\Source\my-project --harness codex --output .\tmp\codex-review

# Combine every matching session in one report:
python review.py repo --repo C:\Source\my-project --all --output .\tmp\all-sessions-review

# No agent/model calls: generate measured accounting and replay directly in Python:
python review.py repo --repo C:\Source\my-project --noagent

# List matching sessions without generating a report:
python review.py discover --repo C:\Source\my-project

# Choose one or more IDs from that inventory instead of the automatic first match:
python review.py repo --repo C:\Source\my-project `
  --session copilot:SESSION_ID_1 --session codex:SESSION_ID_2 `
  --output .\tmp\combined-review

# Nonstandard native history directory (Copilot: directory containing session folders):
python review.py repo --repo C:\Source\my-project `
  --harness copilot --root D:\History\session-state --output .\tmp\custom-review
```

| Option | Behavior |
| --- | --- |
| `--repo PATH` | Required repository directory whose session metadata should match. |
| `--all` | Combine every matching session, still filtered by `--harness` if supplied. |
| `--session ID` | Select an ID or `harness:ID`; repeat for multiple sessions. Mutually exclusive with `--all`. |
| `--harness NAME` | Search only `copilot`, `claude`, or `codex` instead of detecting all three. |
| `--output PATH` | Empty destination directory; defaults to `session-report` in the caller's working directory. |
| `--noagent` | Skip harness launch and qualitative annotations; render directly in Python. |
| `--root PATH` | Override the native history directory; requires `--harness`. |
| `--timezone ZONE` | IANA report timezone; defaults to `America/Los_Angeles`. |
| `--pricing PATH` | Optional approved, dated pricing card; without one, estimated spend is unavailable. |

`--session` also accepts a bare ID if it uniquely identifies a file.
`--all` includes every matching session in the discovery order, honors `--harness`,
and works with `--noagent`. It cannot be combined with `--session`.
`--timezone` defaults to `America/Los_Angeles`; `--pricing` optionally supplies an
approved, dated rate card. Without one, spend remains unknown.
**`--noagent`** disables harness launch and qualitative interpretation, retaining
the deterministic Python-only report. There is no silent fallback to this mode
if the selected harness is missing or fails. The lower-level `prepare` and `run`
commands remain Python-only, so the reviewer can use them without launching
another agent.

Automatic discovery inspects metadata in the current user's
`~\.copilot\session-state`, `~\.claude\projects`, and `~\.codex\sessions` plus
`~\.codex\archived_sessions`. It honors `COPILOT_HOME`, `CLAUDE_CONFIG_DIR`, and
`CODEX_HOME`. Only sessions whose recorded local repository root matches, or whose
working directory is inside the requested repository when no root is recorded,
are offered. Native subagent sessions are excluded from automatic selection.
Conversation keyword mentions and filesystem modification dates are not used to
guess project membership or dates. Missing/malformed metadata is not proof that
no session exists; use an explicit manifest for such histories.
Cursor/Replit still require explicit conversation exports.

Open `session-report\report.html`, `report.pdf`, or `player.html`. Python repository
runs also save `discovery.json` and a reusable `manifest.json` pointing at the
frozen private snapshots, plus `annotations.json` for agent-reviewed runs, so
reruns need not reread changing live logs or repeat model interpretation. A zero
harness exit status alone is not success: the wrapper requires the selected
sessions' artifacts, passed verification, source snapshots and reviewed annotations.
Choose a different empty output directory when rerunning that manifest. The default
`session-report` directory is ignored by **this repository's** Git configuration;
in other workspaces, keep private report outputs out of version control yourself.
Successful `repo`, `run`, and `prepare` commands list every generated file with
its absolute path. Reports list HTML, PDF and the standalone player first;
raw snapshots are explicitly labeled private.

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
