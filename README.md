# Local-MCP-Bridge

A security-scoped Model Context Protocol (MCP) bridge for controlled local filesystem access and development task execution.

## Purpose

Local-MCP-Bridge lets an MCP-compatible AI client interact with explicitly authorized local development projects without granting unrestricted access to the host machine.

The project is built in layers: project authorization, read-only filesystem access, hardened path confinement, controlled process execution, managed jobs, Git synchronization, audit logging, and finally remote MCP integration.

## Security model

The MCP client, model-generated tool calls, repository content, paths, future command arguments, and future process output are untrusted input. Deterministic local policy is the security boundary.

Core rules:

- **Deny by default.** No project is available unless explicitly configured.
- **Project IDs instead of host paths.** Clients address logical IDs; absolute roots stay server-side.
- **Project-root confinement.** Filesystem access is restricted to configured roots.
- **No path redirection.** Symlinks, redirecting Windows reparse points/junctions, nested mount points, and hard-linked regular files are denied.
- **Race-resistant reads.** File identity is checked around open before content is read; directory identity is checked around enumeration.
- **Just-in-time reauthorization.** Recursive search keeps project-relative paths and reauthorizes each file immediately before reading it.
- **Sensitive paths are restricted.** Common credential stores, `.env`, private-key formats, `.git`, and similar locations are denied.
- **Bounded I/O.** Reads, listings, recursive search, query length, and result counts have hard ceilings.
- **No unrestricted shell.** Process execution is not implemented yet; Phase 5 will introduce narrow allowlisted execution.
- **Local secrets stay local.** Real config, credentials, logs, runtime state, and machine-specific paths are ignored by Git.
- **Hermetic tests.** Importing the reusable MCP server factory never reads machine-local runtime configuration.

This is application-level confinement, not an operating-system sandbox. See [`docs/security-model.md`](docs/security-model.md) and [`docs/threat-model.md`](docs/threat-model.md).

## Repository layout

```text
Local-MCP-Bridge/
├── .github/
│   └── workflows/
├── config/
│   └── config.example.yaml
├── docs/
│   ├── architecture.md
│   ├── security-model.md
│   └── threat-model.md
├── src/local_mcp_bridge/
│   ├── security/
│   │   └── paths.py
│   ├── tools/
│   │   └── filesystem.py
│   ├── config.py
│   ├── registry.py
│   ├── runtime.py
│   └── server.py
├── tests/
│   ├── test_filesystem.py
│   ├── test_path_confinement.py
│   ├── test_path_races.py
│   ├── test_registry.py
│   ├── test_runtime_isolation.py
│   └── test_server.py
├── pyproject.toml
├── SECURITY.md
└── README.md
```

## Local setup

From the repository root on Python 3.11 or newer:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Run validation:

```powershell
python -m ruff check .
python -m pytest -q
```

Pytest is isolated from `config/config.yaml`: tests inject temporary registries and importing `local_mcp_bridge.server` performs no local-config I/O.

Start the configured MCP server over stdio:

```powershell
python -m local_mcp_bridge
```

For MCP Inspector development, target the runtime composition root:

```powershell
mcp dev src/local_mcp_bridge/runtime.py
```

## Configure authorized projects

Copy the tracked template to the ignored machine-local file:

```powershell
Copy-Item config/config.example.yaml config/config.yaml
```

Example:

```yaml
projects:
  example-project:
    root: "C:/absolute/path/to/example-project"
    permissions:
      read: true
      search: true
      execute: false
      git: false
```

The root must be an existing absolute directory. `config/config.yaml` is ignored by Git. With no local config the bridge starts with zero authorized projects; an explicitly selected invalid config fails closed.

## Current MCP tools

Phase 4 keeps the Phase 3 public tool surface read-only:

- `health_check()`;
- `list_projects()`;
- `get_project(project_id)`;
- `list_directory(project_id, path=".")`;
- `read_file(project_id, path, start_line=1, max_lines=400)`;
- `search_text(project_id, query, path=".", case_sensitive=false, max_results=50)`.

No filesystem writes, deletes, renames, Git commands, shell access, or process execution are exposed yet.

## Phase 4 confinement

Before local content is returned, the bridge now:

1. validates a project-relative path and operation permission;
2. rejects sensitive paths and unsafe lexical forms;
3. checks each traversed component without following links;
4. rejects symlinks and Windows name-surrogate reparse points such as junctions;
5. rejects nested mount points and hard-linked regular files;
6. resolves and confirms canonical containment within the project root;
7. for reads, records file identity, opens without following the final symlink where supported, compares descriptor identity, revalidates the path, and only then reads bytes;
8. for listings, verifies directory identity before and after enumeration;
9. for recursive search, queues only project-relative paths and reauthorizes every file immediately before reading it.

The policy intentionally fails closed when it cannot prove a path safe. Phase 4 materially reduces symlink/junction and TOCTOU risk, but it does not claim kernel-grade isolation against a simultaneously malicious local process.

## Configuration policy

Only templates belong in Git. Never commit real API keys, auth tokens, tunnel credentials, private certificates, personal machine configuration, or sensitive benchmark output.

Local-only paths include:

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

## Development phases

- **Phase 0:** Repository and security baseline — complete
- **Phase 1:** Minimal MCP server — complete
- **Phase 2:** Project registry and allowed roots — complete
- **Phase 3:** Safe filesystem tools — complete
- **Phase 4:** Path/symlink/junction confinement — complete
- **Phase 5:** Controlled process execution
- **Phase 6:** Persistent local job manager
- **Phase 7:** Git synchronization tools
- **Phase 8:** Audit logging and runtime hardening
- **Phase 9:** Remote/tunnel integration
- **Phase 10:** Lightweight Claude MCP validation
- **Phase 11:** Real project integration and autonomous workflow testing

## Current status

**Phase 4 complete.** The bridge can inspect authorized project files through hardened read-only confinement. Process execution remains disabled until Phase 5.

## License

MIT License. See [`LICENSE`](LICENSE).
