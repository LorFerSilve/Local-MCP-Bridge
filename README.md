# Local-MCP-Bridge

A security-scoped Model Context Protocol (MCP) bridge for controlled local filesystem access and development task execution.

## Purpose

Local-MCP-Bridge is intended to let an MCP-compatible AI client interact with explicitly authorized local development projects without granting unrestricted access to the host machine.

The bridge is designed around two primary capability groups:

- **Filesystem access** — list, search, inspect, and read files inside configured project roots.
- **Controlled execution** — run approved development commands and scripts with bounded working directories, timeouts, output limits, and audit logging.

The long-term goal is to support workflows such as:

1. An AI agent modifies code through GitHub or another source-control integration.
2. The local bridge synchronizes the selected repository.
3. The bridge starts a benchmark, test suite, build, or other approved development task locally.
4. The AI agent reads structured results and logs through MCP.
5. The agent uses those results to decide whether further code changes are required.

## Security model

This project treats every MCP client, model-generated tool call, repository file, and command argument as potentially untrusted input.

Core rules:

- **Deny by default.** No filesystem root or executable is available unless explicitly configured.
- **No unrestricted shell by default.** The bridge should expose narrow capabilities rather than arbitrary `cmd.exe`, PowerShell, or shell command strings.
- **Project-root confinement.** Filesystem operations and process working directories must remain inside canonicalized, explicitly allowed roots.
- **Escape protection.** Path traversal, symbolic-link, junction, and reparse-point escape paths must be validated before access.
- **Least privilege.** Read, execution, Git, and future write permissions are separate capabilities.
- **Bounded execution.** Commands use timeouts, output limits, and resource-aware job handling.
- **Local secrets stay local.** Real configuration, tokens, credentials, logs, job state, and machine-specific paths are excluded from Git.
- **Auditable actions.** Security-relevant operations should produce local audit records without leaking secrets.

See [`docs/security-model.md`](docs/security-model.md) and [`docs/threat-model.md`](docs/threat-model.md).

## Repository layout

```text
Local-MCP-Bridge/
├── .github/
│   └── dependabot.yml
├── config/
│   └── config.example.yaml
├── docs/
│   ├── architecture.md
│   ├── security-model.md
│   └── threat-model.md
├── .env.example
├── .gitignore
├── LICENSE
├── README.md
└── SECURITY.md
```

Implementation code will be added under `src/` with tests under `tests/` as the project progresses.

## Configuration policy

Only example configuration belongs in Git.

Tracked:

```text
.env.example
config/config.example.yaml
```

Local-only and ignored:

```text
.env
config/config.yaml
config/config.local.yaml
credentials/
secrets/
tokens/
runtime/
jobs/
logs/
output/
artifacts/
```

Never place real API keys, authentication tokens, tunnel credentials, private certificates, personal absolute paths, or sensitive benchmark output in committed configuration.

## Planned development phases

1. Repository and security baseline
2. Minimal MCP server
3. Project registry and allowed roots
4. Safe filesystem tools
5. Path/symlink/junction confinement
6. Controlled process execution
7. Persistent local job manager
8. Git synchronization tools
9. Audit logging and runtime hardening
10. Remote/tunnel integration
11. Lightweight Claude MCP validation
12. Real project integration and autonomous workflow testing

## Current status

**Phase 0 — security and repository bootstrap.**

No local execution capability is exposed yet.

## Contributing

Security properties take precedence over convenience. Changes that broaden filesystem, command, network, or credential access should include explicit threat analysis and tests for denial/escape cases.

## License

MIT License. See [`LICENSE`](LICENSE).
