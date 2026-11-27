# Changelog

## 0.2.0b1 - Beta candidate

This is a fork-only beta candidate. It is not published to the upstream
`agent-session-insights` PyPI project.

### Added

- AgentSON v1.2 JSONL and canonical JSON-document import through explicit
  manifests.
- Strict validation using the vendored AgentSON v1.2 JSON Schemas.
- Evidence mapping for user queries, feedback, assistant answers, actions,
  observations, handoffs, presence, capabilities, side-effects and system events.
- Byte-preserving AgentSON source snapshots and a SHA-256-verified
  `agentson-export` command.
- End-to-end round-trip, schema rejection, tamper, overwrite, traversal,
  symlink and redaction tests.
- Python 3.12/3.13 CI, full test execution, distribution checks and a
  six-combination OS/Python installed-wheel validation matrix.

### Privacy and fidelity

- Private `thought` entries are excluded from report events but remain in the
  original private archive.
- Credentials and recognized shared-session URLs are redacted from report
  projections. Redaction is best-effort, not a guarantee that every secret is
  detected.
- Normalized report events are an analytical projection and cannot reconstruct
  an AgentSON archive. The verified original source snapshot is the lossless
  export path.
- Session-level token/cost summaries are not fabricated into per-request usage.

### Compatibility and limitations

- Accepts canonical AgentSON v1.2 only. Legacy v1/v1.1 inputs fail closed and
  require an explicit migration.
- Does not make Agent Session Insights the canonical AgentSON format owner.
- This fork cannot publish to the upstream-owned PyPI project. A separate
  distribution would require a distinct PyPI project name and publisher setup.

### Licensing

- Application and fork code: MIT.
- Vendored AgentSON v1.2 schemas: Apache-2.0.
- Package metadata records both component licences; see the root license files
  and schema notice.
