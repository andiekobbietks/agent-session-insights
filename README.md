# Agent Session Insights

Create offline reports from coding-agent conversations: complete messages,
evidence-linked analysis, per-model token and cost comparisons, and a zoomable
timeline with local day/night shading. Supports Copilot CLI, Claude Code, Codex,
DeepSeek Harness, Cursor exports, and Replit conversation exports.

**See the example:** [HTML report](https://markrussinovich.github.io/agent-session-insights/),
[standalone player](https://markrussinovich.github.io/agent-session-insights/player.html),
or [PDF](https://markrussinovich.github.io/agent-session-insights/report.pdf).

![Wordle session review with a timeline, per-turn token and cost estimates, and evidence-linked user decisions.](assets/wordle-review.png)

## Install

Use Python 3.12 or later. Install the package and its Chromium renderer in a
virtual environment:

**Windows PowerShell**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install agent-session-insights
python -m playwright install chromium
```

**macOS/Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install agent-session-insights
python -m playwright install chromium
```

On Linux, missing browser system libraries may require
`python -m playwright install --with-deps chromium` and system-install privileges.
Report commands check rendering dependencies before processing sessions or
launching an agent.

The installed `agent-session-insights` command works from any directory.
`python -m agent_session_insights` accepts the same arguments. For a source
checkout, use `python review.py` after installing `requirements.txt`; see the
[maintainer guide](https://github.com/markrussinovich/agent-session-insights/blob/main/MAINTAINING.md#development-setup).
If Windows activation is unavailable, invoke the virtual environment's
`.\.venv\Scripts\python.exe` with the module or checkout entry point directly.

## Generate a report

```powershell
agent-session-insights repo --repo C:\Source\my-project
```

This selects the **newest matching session**, freezes its logs, launches its
native harness headlessly for reviewed annotations, then generates and verifies
the report. The harness CLI must be on `PATH` and signed in; normal model charges
apply. For DeepSeek Harness sessions this runs `dsh headless` with a
`workspace-write` permission preset, so the `dsh` CLI and a working model route
must be available; headless credentials follow that profile's configuration.
Only the selected evidence is reviewed. Use `--noagent` for measured
accounting and replay without qualitative annotations or model calls.

Automatic discovery supports Copilot, Claude, Codex, and DeepSeek Harness. It
matches recorded repository/working-directory metadata in the current user's
native history stores, not project mentions in messages, and excludes native
subagents. Missing or malformed metadata limits discovery; Cursor/Replit require
[explicit exports](#explicit-exports-and-manifests).

Native stores under your home are `.copilot\session-state`, `.claude\projects`,
`.codex\sessions`, `.codex\archived_sessions`, and `.dsh\sessions`. Home-directory
overrides honor `COPILOT_HOME`, `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, and `DSH_HOME`.
DeepSeek Harness sessions are stored as current-format (v4) JSONL, compressed
with Zstandard by default; the adapter reads either representation and refuses
older formats explicitly. Only current-generation session logs for the selected
working directory are read; subagent sessions are excluded. Missing DSH cache
counters stay unknown instead of becoming explicit zeros; without `totalTokens`
they also mark inclusive input incomplete.

### Select sessions

List the matching sessions before choosing:

```powershell
agent-session-insights discover --repo C:\Source\my-project
agent-session-insights repo --repo C:\Source\my-project --session copilot:SESSION_ID
```

Repeat `--session` to combine selected IDs, or use `--all` for every match.
For a combined report, the first selected session's harness reviews the evidence.
`--harness` filters the histories searched; it does not choose a different
reviewer for those histories.

| Option | Purpose |
| --- | --- |
| `--repo PATH` | Required local repository directory. |
| `--session ID` | Select an ID or `harness:ID`; repeat for multiple sessions. |
| `--all` | Combine all matches; mutually exclusive with `--session`. |
| `--harness NAME` | Search only `copilot`, `claude`, `codex`, or `dsh`. |
| `--root PATH` | Override the native history directory; requires `--harness`. |
| `--output PATH` | Override the report destination. |
| `--noagent` | Skip the harness and qualitative annotations. |
| `--logs-only` | Save selected raw logs as `session-logs.zip` in the output directory; no analysis, agent, or report. |
| `--timezone ZONE` | IANA report timezone; default `America/Los_Angeles`. |
| `--pricing PATH` | Override the bundled, dated retail API pricing card. |

For example, combine only Copilot sessions without launching an agent:

```powershell
agent-session-insights repo --repo C:\Source\my-project --harness copilot --all --noagent
```

All commands have `--help` with command-specific examples.

### Gather logs without a review

```powershell
agent-session-insights repo --repo C:\Source\my-project --all --logs-only --output .\collected-logs
```

This writes `.\collected-logs\session-logs.zip` without running analysis, calling
a model, or requiring Chromium/Playwright. The same selection rules apply:
newest matching session by default, repeated `--session` for specific sessions,
or `--all` for every match; `--harness` and `--root` also work.
Omit `--output` to use the normal default destination directory.

The ZIP contains byte-for-byte copies of the selected history files under
`logs`, a `manifest.json` with source hashes, and `discovery.json`. It does not
include auxiliary session files or subagent histories. Extract it and use
`run --manifest` with the extracted manifest to generate a report later.
Report-only `--pricing`, `--timezone`, and `--noagent` do not trigger analysis;
the timezone is saved in the manifest for later use.

Existing destination files are left untouched, and an existing
`session-logs.zip` is refused; choose another output directory or move the
previous ZIP. **Raw logs are not redacted** and may contain credentials,
private conversation text, and local paths. Review before sharing.

## Outputs and safety

The default destination is **`agent-session-reports\<repo-name>` under your
current working directory**, not automatically inside the repository passed to
`--repo`.

Successful report commands print the absolute paths of `report.html`,
`report.pdf`, and `player.html`. Open the HTML files directly; no server is needed:

```powershell
Start-Process .\agent-session-reports\my-project\report.html
```

On macOS, use `open` instead of `Start-Process`. Reports include supporting
accounting, normalized events, coverage, verification, frozen pricing, and
private source snapshots. Repository reports also save `discovery.json` and a
reusable `manifest.json`, plus `annotations.json` when agent-reviewed.

Reports and players share bold blue typography, white light-mode surfaces, and
a navy dark mode. They follow your system theme; embedded replay and log panes
follow the surrounding report. Append `?scoutTheme=light` or `?scoutTheme=dark`
to a report or standalone player URL to override the system preference.

**Overwrite protection:** existing reviews require `Overwrite? [y/N]?`
confirmation; only `y` or `yes` proceeds. An empty answer or closed stdin cancels.
Directories containing unrelated files or selected inputs are refused.
Confirmed replacement removes the previous report before generation, so use a
fresh `--output` directory when you need to retain it if generation fails.

**Privacy:** outputs contain conversation text and private raw snapshots.
Credential and shared-session URL redaction is best effort; review files before
sharing or publishing them. This repository ignores the default report directory;
keep outputs out of version control yourself in other workspaces. POSIX outputs
use directory mode 0700 and file mode 0600. Windows outputs inherit the parent
directory's NTFS ACLs; Python's `chmod` does not set those ACLs.

**Interpretation limits:** retail API-equivalent estimates are not invoices.
Frozen rates may be historical; unknown models or missing total usage are
unpriced, not free. Missing cache counters can produce labeled estimates and
ranges, not verified cache hits. Different workloads and cache use prevent
per-model totals from being an efficiency benchmark. Timeline activity is not
measured working time; day/night shading uses 06:00-18:00 daytime in the report
timezone, not astronomical sunrise/sunset.

A missing or failed harness is an error, not a silent fallback to `--noagent`.
Completion requires verified artifacts and intact snapshots, not merely a zero
harness exit status.

## Advanced workflows

| Command | Input | Result |
| --- | --- | --- |
| `discover` | `--repo PATH`, or `--harness NAME --root PATH` | JSON inventory; no report or agent. |
| `repo` | `--repo PATH` | Complete report with agent review unless opted out. |
| `prepare` | `--manifest FILE --output DIRECTORY` | Frozen evidence and measured analysis; no HTML/PDF/player or agent. |
| `run` | `--manifest FILE --output DIRECTORY` | Complete report using existing annotations if supplied; never launches an agent. |

### Regenerate without another review

Use a report's saved manifest to render its frozen sources, annotations, and
pricing into a different, empty directory:

```powershell
agent-session-insights run --manifest .\agent-session-reports\my-project\manifest.json --output .\rerendered
```

Keep the original directory intact: the manifest references files saved beside
it. This avoids rereading changing live logs or repeating model interpretation.

### AgentSON v1.2 interchange

Agent Session Insights can consume an AgentSON v1.2 archive as an explicitly
selected source. This is an adapter, not a replacement for the AgentSON
canonical format.

The reader accepts either:
- A v1.2 JSON document with an `entries` array and the canonical
  `https://agentson.dev/schema/v1.2.json` schema identifier.
- A v1.2 JSONL stream whose first record is `stream-meta`.

The reader validates inputs against the vendored AgentSON v1.2 JSON Schemas.
Legacy v1/v1.1 documents are rejected with a migration message rather than
silently interpreted as v1.2.

For this fork, install the integration from the checkout (the upstream PyPI
release does not contain this fork-only change until it is released):

```powershell
python -m pip install -e .
```

Create a manifest beside your archive:

```json
{
  "title": "AgentSON session review",
  "timezone": "Europe/London",
  "sources": [
    {
      "harness": "agentson",
      "path": "session.agentson",
      "session": "YOUR_STREAM_ID"
    }
  ]
}
```

Generate the report without launching a model:

```powershell
agent-session-insights run --manifest .\\agentson-review.json --output .\\agentson-review
```

The report projection maps user queries and feedback to user messages,
answers to assistant messages, and actions/observations/handoffs/state events
to tool evidence. Private `thought` entries are retained in the private
byte-for-byte source snapshot but excluded from report events. Session-level
token/cost summaries are not fabricated into per-request usage.

**Lossless archive round-trip:** normalized report events are not a reversible
serialization of AgentSON. The original archive is frozen, SHA-256 verified,
and can be exported byte-for-byte:

```powershell
agentson-export --review-dir .\\agentson-review --source S01 --output .\\roundtrip.agentson
```

The exporter refuses modified snapshots and existing destination files. The
round-trip test asserts exact byte equality and hash verification. This
preserves every original entry, extension field, correlation, provenance
object, and payload even where the reporting projection intentionally omits
data. Review private snapshots before sharing; they may contain sensitive
conversation content or credentials.

### Explicit exports and manifests

Create a JSON manifest for selected history files, including Cursor/Replit
exports. Paths are relative to the manifest:

```json
{
  "title": "Project review",
  "timezone": "America/Los_Angeles",
  "sources": [
    {"harness": "copilot", "path": "logs\\events.jsonl", "session": "session-id"},
    {"harness": "cursor", "path": "chat.md", "session": "export-id"}
  ]
}
```

Each source needs `harness`, `path`, and `session`. Supported harness identifiers
are `copilot`, `claude`, `codex`, `dsh`, `cursor`, and `replit`. Optional source `start`
and `end` values are inclusive, timezone-aware ISO timestamps. Optional
`annotations` and `pricing` paths provide reviewed interpretation and a custom
rate card. Without supplied annotations, `run` labels qualitative interpretation
unavailable.

```powershell
agent-session-insights run --manifest .\review.json --output .\imported-review
```

For manual annotation, use `prepare`, review its stable event IDs and measured
evidence, then create a final manifest referencing the prepared snapshots and
your annotations in the same source order. `prepare` marks interpretation
pending even if its input manifest contains annotations. This staging is
optional; `repo` handles it automatically.

See [collection contracts](DESIGN.md#collection-contracts) for export formats
and scoping, [analysis rules](DESIGN.md#analysis-and-qualitative-review) for
accounting limits, and the [example rate card](examples/pricing-rates.json) for
the pricing schema. No report-time network access is needed for token pricing.

### Interactive reviewer agent

For natural-language selection, use the bundled **agent-session-insights** agent
in this repository's workspace and ask:

> Make a report for `C:\Source\my-project`. I want to pick the sessions.

Unlike the CLI's automatic selection, the agent prompts for unresolved session
choices and the output location. Its profile is
`.github/agents/agent-session-insights.agent.md`; the collect, analyze, and publish
skills are under `.github/skills/`. You can copy those definitions into another
workspace. Installation does not register agents globally or change personal
settings. See [agent contracts](DESIGN.md#agent-contract-and-stages) for the
annotation-only launcher and interactive workflows.

### Rebuild the bundled example

Clone/download this repository, then run from its root:

```powershell
agent-session-insights run --manifest .\examples\creation.json --output .\tmp\creation-review
```

The example uses a scoped, redacted export of the conversation that built this
project, reviewed annotations, and frozen historical prices. Rebuilding it needs
no agent or model call. Offline copies of the
[HTML report](examples/creation/report.html),
[player](examples/creation/player.html), and
[PDF](examples/creation/report.pdf) are also checked in. Download the HTML files
and open them locally; GitHub's file viewer does not run the player.

## Development and publishing

See the [maintainer guide](https://github.com/markrussinovich/agent-session-insights/blob/main/MAINTAINING.md)
for checkout setup, tests, example provenance, GitHub Pages deployment, and PyPI
releases. [DESIGN.md](DESIGN.md) contains collection, accounting, privacy, and
player contracts. The project is [MIT licensed](LICENSE).
