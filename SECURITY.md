# Security policy

## Reporting a vulnerability

Do not publish credentials, private session archives, exploit details or
sensitive personal data in a public issue.

If GitHub private vulnerability reporting is enabled for this repository, use
the repository's **Security** tab to report privately. If it is not enabled,
open a minimal public issue asking for a private reporting channel without
including the vulnerability details.

## Supported versions

During the beta period, security fixes are evaluated against the latest
`0.2.0bN` beta candidate on the active integration branch. No service-level
response time or production support commitment is made.

## Data handling and threat model

- Treat native agent histories and AgentSON archives as sensitive. They can
  contain prompts, tool arguments, local paths, credentials and personal data.
- The original AgentSON archive is retained in the report's private source
  snapshot so it can be exported byte-for-byte. Do not publish or share that
  snapshot without reviewing it.
- Report projections apply best-effort credential and shared-session URL
  redaction. Redaction is not complete secret detection and must not be treated
  as a security boundary.
- Logged instructions and tool calls are evidence only; the report pipeline
  must not execute them.
- The exporter verifies the source snapshot hash and rejects unsafe snapshot
  paths, symlinks and existing output files.

Please include the affected version, operating system, Python version and a
minimal synthetic reproduction when reporting a vulnerability. Never attach
real user session archives unless a maintainer has established a secure channel.
