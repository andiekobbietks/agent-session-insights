# Maintainer guide

User installation and report commands are in [README.md](README.md).
Collection, analysis, privacy, and publication contracts are in
[DESIGN.md](DESIGN.md).

## Development setup

Clone the repository and use Python 3.12 or later:

```powershell
git clone https://github.com/markrussinovich/agent-session-insights.git
Set-Location agent-session-insights
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

These PowerShell commands use the virtual environment directly; activation is
not required. For an existing checkout, skip cloning and changing directory.

On macOS/Linux:

```bash
git clone https://github.com/markrussinovich/agent-session-insights.git
cd agent-session-insights
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

On Linux, browser system libraries may require
`python -m playwright install --with-deps chromium` and system-install privileges.

### Tests

From the checkout, using the environment where dependencies were installed:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check .\assets\player.js
```

On macOS/Linux, use `python` for the Python command and
`node --check assets/player.js`. Node is optional for the syntax check; Python
tests and publication verification also exercise the player in Chromium.

## Bundled example

Regenerate the example without agent/model calls:

```powershell
.\.venv\Scripts\python.exe review.py run --manifest .\examples\creation.json --output .\tmp\creation-review
```

The [manifest](examples/creation.json) uses only repository-local files:

| Input | Purpose |
| --- | --- |
| [creation-session.jsonl](examples/creation-session.jsonl) | Scoped, redacted Copilot CLI-format export of this project's creation. |
| [creation-source.json](examples/creation-source.json) | Original source hash, scope, export method, and expected results. |
| [creation-annotations.json](examples/creation-annotations.json) | Evidence-linked material directions; all selected inputs remain in replay. |
| [pricing-rates.json](examples/pricing-rates.json) | Frozen historical, cache-aware API-equivalent pricing. |

The export is reconstructed from verified normalized evidence, not a
byte-for-byte copy of the full original history. It preserves selected message
text and genuine user form answers after best-effort credential and shared-session
URL redaction, timestamps, identities, tool-call names, and per-request accounting.
Opaque provider request IDs are consistently SHA-256 pseudonymized; recorded
interaction IDs preserve prompt ownership. It excludes unrelated history, system
context, internal reasoning, tool payloads, and cumulative mirrors. No original
workspace files are required to rebuild it.

The checked-in example still contains conversation text and must be reviewed
before publication. Full original histories and runtime `private/` snapshots are
not versioned; fresh runs recreate snapshots under the ignored output directory.
Historical rates are not current quotes or actual Copilot bills.

## GitHub Pages demo

The [Pages workflow](.github/workflows/pages.yml) regenerates and browser-verifies
the bundled example, then stages only its nine report/player/evidence assets plus
`index.html`. Raw snapshots, input histories, code, Git history, and unrelated
files are excluded. Staging rejects recognized credentials and unredacted
shared-session URLs; this does not replace reviewing a conversation before
approving publication.

To build the site locally, use fresh output directories:

```powershell
.\.venv\Scripts\python.exe review.py run --manifest .\examples\creation.json --output .\tmp\pages-review --public-base-url https://markrussinovich.github.io/agent-session-insights/
.\.venv\Scripts\python.exe scripts\prepare_pages.py --source .\tmp\pages-review --output .\tmp\pages-site
```

`--public-base-url` points PDF hyperlinks at the HTTPS site. HTML/player links
remain relative and offline-compatible; omit it for local-only reports.

Configure Pages to use **GitHub Actions**. Changes to example inputs, rendering
code, or the workflow on `main` redeploy the reviewed demo. Manual workflow
dispatch is also available.

## Beta acceptance gate

A beta candidate is not a production-support promise. Before tagging or
publishing a beta, require all of the following on the exact release commit:

- Full unit/integration suite passes on Python 3.12 and 3.13.
- Wheel and source distribution build; strict Twine metadata checks pass.
- Distribution audit confirms runtime schemas, both licence texts, and notices
  are present, while local histories and private snapshots are absent.
- Installed-wheel smoke tests pass outside the checkout on Linux, Windows,
  and macOS.
- AgentSON JSONL and JSON document imports validate against the vendored v1.2
  schemas; unknown and legacy versions fail closed.
- End-to-end collection/export is byte-identical; tampering, path traversal,
  symlink escape, and overwrite attempts are rejected.
- Report projection tests prove private thought entries do not enter published
  report events and sensitive metadata is redacted.
- README and release notes state the exact fidelity boundary: normalized
  report events are not reversible; the verified original archive is the
  lossless export source.

The fork's CI runs the full test suite and package checks. The release workflow
also tests the built wheel across operating systems and Python versions. A
green focused AgentSON test job alone is not sufficient to declare Beta.

## Publishing releases

The package name and version are defined in [pyproject.toml](pyproject.toml).
The [release workflow](.github/workflows/publish.yml) builds wheel and source
distributions, checks metadata and packaged files, and tests on Windows, macOS,
and Linux. Installed-package smoke tests generate reports outside the checkout
without model calls. User histories, generated reports, private snapshots, and
the example conversation are excluded from distributions.

### One-time Trusted Publisher setup

Create a GitHub Actions environment named **`pypi`** in this repository's
**Settings > Environments**. Allow deployments from **`main`** for automatic
releases, and release tags such as `v*` if manual GitHub releases are needed.
Required reviewers are optional; configuring them pauses uploads for approval.

On PyPI, open your account's **Publishing** page and add a pending GitHub Trusted
Publisher for the first release, or a publisher to the existing project:

| Field | Value |
| --- | --- |
| PyPI project name | `agent-session-insights` |
| GitHub owner | `markrussinovich` |
| Repository | `agent-session-insights` |
| Workflow filename | `publish.yml` |
| Environment | `pypi` |

A pending publisher does not reserve the project name. PyPI registration and the
GitHub environment are manual setup steps; the workflow alone configures neither.
Set the publisher's environment to exactly **`pypi`**, not blank. If an existing
publisher allows any environment, constrain it to `pypi`.

No PyPI password or API-token secret is needed. Only the publishing job receives
`id-token: write`; PyPI exchanges its GitHub OIDC identity for short-lived upload
credentials. Uploads use the exact distributions that passed the build and
cross-platform tests.

### Automatic releases from main

Pushing to **`main`** releases automatically; no manual version bump, tag, or
GitHub release is required:

1. The serialized workflow checks the latest `main`, Git tags, and PyPI. An
   unreleased `project.version` is used as-is; an already released version
   advances to the first unused patch number. Minor/major bumps can be set manually.
2. If needed, the workflow commits and pushes only `pyproject.toml` back to
   `main`, then builds and tests that exact commit and wheel on all three platforms.
3. After all checks pass, it publishes via the `pypi` environment and creates
   the matching `vVERSION` GitHub release.

Version and release jobs have narrowly scoped `contents: write`. Their built-in
`GITHUB_TOKEN` pushes/releases do not trigger another workflow run. Repository
rules must permit the bot's version commit; the workflow does not bypass branch
protection. A concurrent change to `main` causes the normal non-fast-forward check
to stop the push rather than overwrite changes. Failed builds/tests do not publish.

Pull requests and default manual workflow runs only build and test. Manual
GitHub releases are supported when the tag exactly matches `v` plus the package
version; uploads still require all checks. Do not manually release an already
automatically published version.

For TestPyPI, create a separate **`testpypi`** GitHub environment and register the
same publisher on TestPyPI with environment `testpypi`. Manually run the workflow
with **Publish the tested distributions to TestPyPI** enabled. TestPyPI and PyPI
require separate publisher registrations.

See [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/).

### Build locally

Install build tools in a development environment:

```powershell
python -m pip install build twine
python -m build
python -m twine check --strict .\dist\*
python scripts\check_distribution.py
```

Install the resulting wheel in a separate clean virtual environment, install
Chromium there, then run `python scripts\smoke_install.py` from the checkout.
That script uses installed entry points and synthetic sessions, not local user
histories. Use the interpreter from that clean environment for the smoke test.
Do not upload using a long-lived API token; the workflow provides Trusted Publishing.
