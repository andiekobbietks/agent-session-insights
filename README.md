# Agent Session Insights

A local-first reviewer agent and reproducible analysis pipeline for Copilot CLI,
Claude Code, Codex, Cursor exports, and Replit conversation exports. Reports and
standalone players show the conversation, timeline, model usage, token accounting,
and evidence-linked analysis in a fixed Venn visual framework.

## Install a published release

Install from PyPI in an activated Python 3.12+ virtual environment:

```bash
python -m pip install agent-session-insights
python -m playwright install chromium
```

The package installs the `agent-session-insights` command, which works from any
directory without cloning this repository:

```powershell
agent-session-insights repo --repo C:\Source\my-project
agent-session-insights repo --repo C:\Source\my-project --all --output .\tmp\all-sessions-review
```

You can also use `python -m agent_session_insights` with the same arguments.
On macOS/Linux, use `python3` if that is your Python command. On Linux, Chromium
may also need system libraries: `python -m playwright install --with-deps chromium`.
The native harness CLI must be installed and signed in unless you use `--noagent`.
For an unpublished checkout, follow the setup below; `python review.py` remains
supported and accepts the same arguments.

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

Output defaults to **`agent-session-reports/<repo-name>` in your current working
directory**, for example `agent-session-reports/my-project`.
Completion lists only `report.html`, `report.pdf`, and the standalone `player.html`.
Supporting evidence and verification files remain in the output directory.
Existing nonempty directories
are never overwritten; use a fresh `--output` path for each report.

Open the default report in your browser:

**Windows (PowerShell):**

```powershell
Start-Process .\agent-session-reports\my-project\report.html
```

**macOS:**

```bash
open ./agent-session-reports/my-project/report.html
```

For natural-language session selection and output-location prompts, see
[Report on a repository](#report-on-a-repository). For all command options, see
[Automatic Python command](#automatic-python-command).

## Which command should I use?

**Use `repo` for most reports.** It handles session discovery, selection, agent
annotations and report generation in one command. `discover` lets you inspect
the available sessions first. `prepare` and `run` are lower-level commands for
explicit source files and staged workflows.

| Command | Required input | What it produces | Agent/model calls? |
| --- | --- | --- | --- |
| `discover` | `--repo PATH`, or `--harness NAME --root PATH` | JSON inventory of matching sessions or source files; no report files | No |
| `repo` | `--repo PATH` | Complete, verified HTML report, PDF and standalone player, plus supporting evidence | Yes, headlessly by default; `--noagent` disables this |
| `prepare` | `--manifest FILE --output DIRECTORY` | Frozen private source snapshots, normalized events, coverage and measured analysis; **no HTML/PDF/player** | No |
| `run` | `--manifest FILE --output DIRECTORY` | Complete, verified HTML/PDF/player using existing annotations if supplied | No |

Use the installed Python module from any directory:

```powershell
# See what sessions are available; nothing is generated:
python -m agent_session_insights discover --repo C:\Source\polypost

# Complete report for the newest session, including headless agent review:
python -m agent_session_insights repo --repo C:\Source\polypost

# Use a specific session ID returned by discover:
python -m agent_session_insights repo --repo C:\Source\polypost --session SESSION_ID

# Combine every matching session without launching an agent:
python -m agent_session_insights repo --repo C:\Source\polypost --all --noagent --output .\polypost-all
```

`repo` defaults to the newest session, not all sessions, and does not prompt for
selection. Repeat `--session` to choose multiple IDs, or use `--all`.
`--harness claude`, for example, selects only Claude histories; it does **not**
ask Claude to review another harness's logs. For combined sessions, the first
selected session's harness performs the review.

Without `--output`, the Polypost example writes to
**`.\agent-session-reports\polypost` under your current working directory**.
Existing nonempty output directories are never overwritten.
`prepare` and `run` always require an explicit `--output`.

### Manifest-based and staged workflows

A **manifest** is a JSON file listing exact source logs, harness names and session
IDs, with optional timezone, date bounds, pricing and an annotations-file path.
Source and annotation paths are relative to the manifest.
See [Supply other sessions](#supply-other-sessions) for its format.

`prepare` is useful when you want to inspect frozen evidence and write reviewed
annotations before rendering. It does not launch a reviewer or finish the report:

```powershell
python -m agent_session_insights prepare --manifest .\review.json --output .\review-draft
```

The draft contains `collection.json`, `events.jsonl`, `analysis.json` and private
source snapshots. Qualitative interpretation is marked pending, even if the input
manifest contains annotations. For subsequent rendering, create a manifest that
references those frozen snapshots and your reviewed annotations, then use `run`.
This manual staging is optional; `repo` performs it automatically.

`run` produces a complete report directly from a manifest. It never launches an
agent: annotations must already exist, or the report labels qualitative
interpretation unavailable. It can also regenerate a previous repository report
using that report's saved manifest, with no new model calls:

```powershell
python -m agent_session_insights run --manifest .\agent-session-reports\polypost\manifest.json --output .\polypost-rerendered
```

Keep the original report directory intact: its manifest points to the frozen
snapshots and annotations saved beside it. Regenerate into a different, empty
directory.

All commands accept `--help`, including command-specific examples:

```powershell
python -m agent_session_insights --help
python -m agent_session_insights repo --help
python -m agent_session_insights discover --help
python -m agent_session_insights prepare --help
python -m agent_session_insights run --help
```

The `agent-session-insights` console command accepts the same arguments.
From a source checkout, replace `python -m agent_session_insights` with
`python review.py`.

## Example report and embedded replay

The bundled example reviews the Copilot CLI conversation that built this project.

- **Live demo:** [HTML report with embedded replay](https://markrussinovich.github.io/agent-session-insights/),
  [standalone player](https://markrussinovich.github.io/agent-session-insights/player.html),
  and [PDF report](https://markrussinovich.github.io/agent-session-insights/report.pdf).

Repository copies for offline use:

- [Read the report (PDF)](https://github.com/markrussinovich/agent-session-insights/blob/main/examples/creation/report.pdf).
- [Open the HTML report with its embedded player](https://github.com/markrussinovich/agent-session-insights/blob/main/examples/creation/report.html).
- [Open the standalone player](https://github.com/markrussinovich/agent-session-insights/blob/main/examples/creation/player.html).

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
| [`examples/creation-session.jsonl`](https://github.com/markrussinovich/agent-session-insights/blob/main/examples/creation-session.jsonl) | Scoped, redacted Copilot CLI-format export of this project's creation |
| [`examples/creation-source.json`](https://github.com/markrussinovich/agent-session-insights/blob/main/examples/creation-source.json) | Original source hash, scope, export method and expected results |
| [`examples/creation-annotations.json`](https://github.com/markrussinovich/agent-session-insights/blob/main/examples/creation-annotations.json) | Eleven evidence-linked material directions; all selected inputs remain in replay |
| [`examples/pricing-rates.json`](https://github.com/markrussinovich/agent-session-insights/blob/main/examples/pricing-rates.json) | Frozen historical, cache-aware API-equivalent pricing |

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

The [Pages workflow](https://github.com/markrussinovich/agent-session-insights/blob/main/.github/workflows/pages.yml) regenerates and browser-verifies
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
**`agent-session-reports/<repo-name>` in the current workspace** as the suggestion.
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

The harness starts in a private temporary workspace containing the bundled
reviewer profile, skills and documentation, not in the installation directory.
The workspace is removed after the report is generated or the run fails.
Copilot is granted write approval
only for that run's annotations file, and shell execution is explicitly denied.
All harnesses run headlessly, even from a terminal: Copilot uses `--prompt`,
Claude uses `--print`, and Codex uses `exec`. Progress or final text may appear
in the terminal, but no interactive harness UI opens or waits for you to exit.
For Copilot, the generated
workspace is trusted for that run only; no persistent folder trust is saved.
Claude exposes only file-read/search/edit tools and pre-approves edits only to
that run's annotations file; other permission requests are denied automatically.
Codex uses a workspace-write sandbox in the temporary workspace and skips its
Git-repository check. On Windows, it enables `prefer_mxc` for that run so supported
devices use native MXC isolation, falling back only to a permitted configured
Windows sandbox. No saved sandbox settings are changed. Codex may use read-only
shell commands within the temporary workspace when no file-reading tool is
available. No allow-all or permission-bypass flags are used.
The wrapper publishes automatically after the harness exits.

Output defaults to **`.\agent-session-reports\<repo-name>` in your original current working directory**,
not automatically inside the repository passed to `--repo` or the harness's
temporary workspace. Use `--output` to change it. With several explicitly selected sessions,
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
| `--output PATH` | Empty destination directory; defaults to `agent-session-reports/<repo-name>` in the caller's working directory. |
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

Open `agent-session-reports\<repo-name>\report.html`, `report.pdf`, or `player.html`. Python repository
runs also save `discovery.json` and a reusable `manifest.json` pointing at the
frozen private snapshots, plus `annotations.json` for agent-reviewed runs, so
reruns need not reread changing live logs or repeat model interpretation. A zero
harness exit status alone is not success: the wrapper requires the selected
sessions' artifacts, passed verification, source snapshots and reviewed annotations.
Choose a different empty output directory when rerunning that manifest. The default
`agent-session-reports` directory is ignored by **this repository's** Git configuration;
in other workspaces, keep private report outputs out of version control yourself.
Successful `repo` and `run` commands list only the HTML report, PDF and standalone
player with their absolute paths. Supporting evidence, verification and private
snapshots are still saved, but are not listed. `prepare` creates only supporting
files and does not print a report-output list.

See [DESIGN.md](https://github.com/markrussinovich/agent-session-insights/blob/main/DESIGN.md) for collection contracts, accounting rules, privacy,
analysis boundaries, and the player design.

## Supply other sessions

Create a JSON manifest with a title, timezone, and explicit source files. Paths
are relative to the manifest. Each source needs `harness`, `path`, and `session`.
Supported harness identifiers: `copilot`, `claude`, `codex`, `cursor`, `replit`.
Optional `start` and `end` are inclusive timezone-aware ISO timestamps.
An optional `annotations` path supplies human/agent-reviewed interpretation.
An optional `pricing` path supplies a dated, cache-aware USD-per-million-token
rate card (see [`examples/pricing-rates.json`](https://github.com/markrussinovich/agent-session-insights/blob/main/examples/pricing-rates.json) for the schema). The creation
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

## Publishing releases

The package uses the [MIT license](https://github.com/markrussinovich/agent-session-insights/blob/main/LICENSE). Its name and version are defined in
`pyproject.toml`.
The `.github/workflows/publish.yml` workflow builds the wheel and source
distribution, checks metadata and packaged files, and runs tests on Windows,
macOS and Linux. Installed-package smoke tests generate HTML, PDF and a player
outside the checkout without model calls. User histories, generated reports,
private snapshots and the example conversation are excluded from distributions.

### One-time Trusted Publisher setup

Create a GitHub Actions environment named **`pypi`** in this repository's
**Settings > Environments**. Allow deployments from **`main`** for automatic
releases, and release tags such as `v*` if you also use manual GitHub releases.
Required reviewers are optional; configuring them pauses each upload for approval.

On PyPI, open your account's **Publishing** page and add a pending GitHub Trusted
Publisher for the first release, or add a publisher to the existing project.
Use these exact values:

| Field | Value |
| --- | --- |
| PyPI project name | `agent-session-insights` |
| GitHub owner | `markrussinovich` |
| Repository | `agent-session-insights` |
| Workflow filename | `publish.yml` |
| Environment | `pypi` |

A pending publisher does not reserve the project name. Registration in your PyPI
account and the GitHub environment are manual setup steps; adding the workflow
alone does not configure either service.
Set the publisher's **Environment** to exactly **`pypi`**, not blank. If PyPI warns
that an existing publisher allows any environment, constrain it to `pypi` in the
project's publishing settings.

No PyPI password or API-token secret is needed. Only the publishing job receives
`id-token: write`; PyPI exchanges its GitHub OIDC identity for short-lived upload
credentials. The upload uses the exact distributions that passed the build and
cross-platform tests.

### Automatic releases from main

Push to **`main`** to release automatically. No manual version bump, tag or GitHub
release is required:

1. The serialized release workflow checks out the latest `main` and checks Git
   tags and PyPI. An unreleased `project.version` is used as-is; an already
   released version advances to the first unused patch number, such as
   `0.1.1` to `0.1.2`. You can still set a new minor or major version manually.
2. If a bump is needed, the workflow commits and pushes only `pyproject.toml`
   back to `main`. It builds that exact commit and runs the Windows, macOS and
   Linux tests against the same source and built wheel.
3. After all checks pass, it publishes the tested distributions to PyPI using
   the `pypi` environment, then creates the matching `vVERSION` GitHub release.

The version job and GitHub release job have narrowly scoped `contents: write`.
They use the built-in `GITHUB_TOKEN`, whose pushes and releases do not trigger
another workflow run. Repository rules must permit the bot's version commit;
the workflow does not bypass branch protection. If `main` changes while that
commit is being pushed, the normal non-fast-forward check stops the run instead
of overwriting changes. Failed builds/tests do not publish.

Pull requests and default manual workflow runs only build and test.
A manually published GitHub release is also supported: its tag must exactly
match `v` plus the package version, and its upload still requires all checks.
Do not manually release a version that the automatic workflow already published.

For an optional TestPyPI trial, create a separate **`testpypi`** GitHub environment
and register the same publisher on TestPyPI with environment `testpypi`. Manually
run the workflow with **Publish the tested distributions to TestPyPI** enabled.
TestPyPI and PyPI require separate publisher registrations.

See the [PyPI Trusted Publishing documentation](https://docs.pypi.org/trusted-publishers/).

### Build locally

From a checkout, install the build tools in a development environment:

```bash
python -m pip install build twine
python -m build
python -m twine check --strict dist/*
python scripts/check_distribution.py
```

Install the resulting wheel into a separate clean virtual environment, then
install Chromium and run `python scripts/smoke_install.py` from the checkout.
That script uses the installed console/module entry points and synthetic
sessions, not local user histories. Do not upload with a long-lived API token;
the release workflow provides Trusted Publishing.
