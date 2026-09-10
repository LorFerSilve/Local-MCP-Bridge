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
│       ├── config.py
│       ├── registry.py
│       └── server.py
├── tests/
│   ├── test_registry.py
│   └── test_server.py
├── .env.example
├── .gitignore
├── LICENSE
├── pyproject.toml
├── README.md
└── SECURITY.md
```

## Local setup

From the repository root on Python 3.11 or newer:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Run the linter and tests:

```powershell
python -m ruff check .
python -m pytest -q
```

Start the MCP server over stdio:

```powershell
python -m local_mcp_bridge
```

For interactive development with the MCP Inspector:

```powershell
mcp dev src/local_mcp_bridge/server.py
```

## Phase 2: project registry and allowed roots

Phase 2 introduces the local project registry that separates MCP-facing project identifiers from host-specific absolute paths.

The server exposes three non-destructive metadata tools:

- `health_check` — returns server/version status and the number of configured projects;
- `list_projects` — returns configured project IDs and their capability flags;
- `get_project` — returns public metadata for one project ID.

Absolute local roots are never included in MCP responses.

### Configure a local project

Copy the tracked example configuration to the ignored local configuration file:

```powershell
Copy-Item config/config.example.yaml config/config.yaml
```

Then edit `config/config.yaml`, for example:

```yaml
projects:
  aurum:
    root: "C:/path/to/aurum-forecasting-tool"
    permissions:
      read: true
      search: true
      execute: false
      git: false
```

The root must already exist and must be an absolute directory path.

You may also select a different local config file with:

```powershell
$env:LOCAL_MCP_BRIDGE_CONFIG = "C:/path/to/local-config.yaml"
python -m local_mcp_bridge
```

If no `config/config.yaml` exists and no override is supplied, the bridge starts safely with **zero authorized projects**.

### Registry invariants

Phase 2 enforces the following before a root enters the registry:

- project IDs must start with a lowercase letter and contain only lowercase letters, digits, and hyphens;
- roots must be absolute, existing directories;
- roots are canonicalized with strict resolution;
- duplicate YAML keys are rejected;
- the same canonical root cannot be registered under multiple IDs;
- permissions default to `false` when omitted;
- `search: true` requires `read: true`;
- unknown project and permission keys are rejected;
- MCP-visible metadata never contains the local absolute root.

Filesystem content access and command execution remain intentionally disabled in Phase 2.

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

- **Phase 0:** Repository and security baseline
- **Phase 1:** Minimal MCP server
- **Phase 2:** Project registry and allowed roots
- **Phase 3:** Safe filesystem tools
- **Phase 4:** Path/symlink/junction confinement
- **Phase 5:** Controlled process execution
- **Phase 6:** Persistent local job manager
- **Phase 7:** Git synchronization tools
- **Phase 8:** Audit logging and runtime hardening
- **Phase 9:** Remote/tunnel integration
- **Phase 10:** Lightweight Claude MCP validation
- **Phase 11:** Real project integration and autonomous workflow testing

## Current status

**Phase 2 — project registry and allowed roots implemented.**

The bridge can now load and expose safe project metadata, but it still cannot read project files or execute local commands. Those capabilities remain gated behind later security phases.

## Contributing

Security properties take precedence over convenience. Changes that broaden filesystem, command, network, or credential access should include explicit threat analysis and tests for denial/escape cases.

## License

MIT License. See [`LICENSE`](LICENSE).
