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
│   ├── dependabot.yml
│   └── workflows/
│       ├── ci.yml
│       └── security-baseline.yml
├── config/
│   └── config.example.yaml
├── docs/
│   ├── architecture.md
│   ├── security-model.md
│   └── threat-model.md
├── src/
│   └── local_mcp_bridge/
│       ├── __init__.py
│       ├── __main__.py
│       └── server.py
├── tests/
│   └── test_server.py
├── .env.example
├── .gitignore
├── LICENSE
├── pyproject.toml
├── README.md
└── SECURITY.md
```

## Phase 1: minimal MCP server

Phase 1 establishes a real, installable MCP server while deliberately exposing no privileged host capabilities yet.

The server currently exposes exactly one tool:

- `health_check` — returns non-sensitive server/version information and confirms that filesystem and execution capabilities are disabled.

The bridge uses the MCP Python SDK v2 and defaults to MCP's `stdio` transport for local development.

### Local setup

From the repository root on Python 3.11 or newer:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Run the tests and linter:

```powershell
python -m ruff check .
python -m pytest -q
```

Start the server over stdio:

```powershell
python -m local_mcp_bridge
```

The process will wait for an MCP host on stdin/stdout; that is expected for the stdio transport.

For interactive development with the MCP Inspector:

```powershell
mcp dev src/local_mcp_bridge/server.py
```

No project directories, shell access, subprocess execution, Git operations, network credentials, or host metadata are exposed in Phase 1.

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

**Phase 1 — minimal MCP server implemented.**

Filesystem access and local command execution remain intentionally disabled until their dedicated security layers are implemented and tested.

## Contributing

Security properties take precedence over convenience. Changes that broaden filesystem, command, network, or credential access should include explicit threat analysis and tests for denial/escape cases.

## License

MIT License. See [`LICENSE`](LICENSE).
