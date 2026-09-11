# Local-MCP-Bridge

A security-scoped Model Context Protocol (MCP) bridge for controlled local filesystem access and development task execution.

## Purpose

Local-MCP-Bridge lets an MCP-compatible AI client interact with explicitly authorized local development projects without granting unrestricted access to the host machine.

The project is being built in layers: first project authorization, then read-only filesystem access, then hardened path handling, controlled execution, managed jobs, Git synchronization, audit logging, and finally remote MCP integration.

## Security model

The bridge treats the MCP client, model-generated tool calls, repository content, paths, search queries, future command arguments, and process output as untrusted input.

Core rules:

- **Deny by default.** No project is available unless explicitly configured.
- **Project IDs instead of host paths.** The client addresses a logical project ID; absolute roots remain local to the bridge.
- **Project-root confinement.** Filesystem operations must resolve inside the selected registered root.
- **Read/search permissions are separate.** Capabilities are enforced locally, not by trusting the model.
- **Sensitive paths are restricted.** Common credential stores, `.env` files, private-key formats, `.git`, and similar paths are not exposed by Phase 3 tools.
- **Bounded output and scanning.** Reads, listings, recursive search, query length, and search results have hard safety limits.
- **No unrestricted shell.** Command execution is not implemented yet and future execution will use narrow allowlisted operations.
- **Local secrets stay local.** Real configuration, credentials, logs, runtime state, and machine-specific paths remain untracked.

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
│       ├── server.py
│       └── tools/
│           ├── __init__.py
│           └── filesystem.py
├── tests/
│   ├── test_filesystem.py
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

Run validation:

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

## Configure authorized projects

Copy the tracked example configuration to the ignored local file:

```powershell
Copy-Item config/config.example.yaml config/config.yaml
```

Then configure only directories you explicitly want the bridge to know about:

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

The configured root must already exist and be an absolute directory path. `config/config.yaml` is ignored by Git.

You can select another local configuration file with:

```powershell
$env:LOCAL_MCP_BRIDGE_CONFIG = "C:/absolute/path/to/local-config.yaml"
python -m local_mcp_bridge
```

With no local configuration, the bridge starts fail-closed with zero authorized projects.

## Phase 3: safe filesystem tools

Phase 3 adds the first local-content capabilities. They are read-only and project-scoped:

- `list_directory(project_id, path=".")` — returns a bounded directory listing using project-relative paths;
- `read_file(project_id, path, start_line=1, max_lines=400)` — reads a bounded UTF-8 text slice;
- `search_text(project_id, query, path=".", case_sensitive=false, max_results=50)` — performs bounded plain-text search without regex evaluation.

Metadata tools from Phase 2 remain available:

- `health_check`;
- `list_projects`;
- `get_project`.

### Active Phase 3 safeguards

Before filesystem content is returned, the bridge applies the following controls:

- only registered projects may be selected;
- `read` permission is required for listing and reading;
- both `read` and `search` are required for recursive text search;
- caller paths must be relative to the project root;
- absolute POSIX paths, Windows drive paths, UNC paths, `..` traversal, NTFS alternate-data-stream syntax, control characters, and Windows device names are rejected;
- the effective path is resolved and must remain inside the canonical project root;
- standard symbolic-link path components are rejected;
- common credential and secret paths are denied by default;
- binary/non-UTF-8 files are not returned through `read_file`;
- file reads are bounded at the file descriptor rather than trusting a prior file-size check;
- directory and recursive-search traversal is bounded;
- search is plain substring matching, avoiding regex/ReDoS behavior;
- MCP path fields remain project-relative and do not disclose the configured absolute root.

`read_file` is intentionally line-paged. If a file contains more lines than the requested slice, the response includes `next_start_line` so a client can continue without loading the whole file into context.

### Deliberate Phase 3 limitations

Phase 3 does **not** add filesystem writes, deletes, renames, Git operations, shell access, or process execution.

It also does not yet claim complete Windows reparse-point/TOCTOU hardening. Phase 4 is dedicated to deeper symlink, junction, reparse-point, and race-resistant path confinement before execution capabilities are introduced.

## Configuration policy

Only templates belong in Git.

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

Never commit real API keys, authentication tokens, tunnel credentials, private certificates, personal machine configuration, or sensitive benchmark output.

## Development phases

- **Phase 0:** Repository and security baseline — complete
- **Phase 1:** Minimal MCP server — complete
- **Phase 2:** Project registry and allowed roots — complete
- **Phase 3:** Safe filesystem tools — complete
- **Phase 4:** Path/symlink/junction confinement
- **Phase 5:** Controlled process execution
- **Phase 6:** Persistent local job manager
- **Phase 7:** Git synchronization tools
- **Phase 8:** Audit logging and runtime hardening
- **Phase 9:** Remote/tunnel integration
- **Phase 10:** Lightweight Claude MCP validation
- **Phase 11:** Real project integration and autonomous workflow testing

## Current status

**Phase 3 — safe read-only filesystem tools implemented.**

The bridge can now inspect authorized project files but still cannot modify files or execute local commands.

## Contributing

Security properties take precedence over convenience. Changes that broaden filesystem, command, network, or credential access should include explicit threat analysis and denial/escape tests.

## License

MIT License. See [`LICENSE`](LICENSE).
