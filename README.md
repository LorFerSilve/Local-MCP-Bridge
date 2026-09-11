# Local-MCP-Bridge

A security-scoped Model Context Protocol (MCP) bridge for controlled local filesystem access and development task execution.

## Purpose

Local-MCP-Bridge lets an MCP-compatible AI client interact with explicitly authorized local development projects without granting an unrestricted shell or arbitrary host-path access.

The project is built in layers: project authorization, read-only filesystem access, hardened path confinement, controlled process execution, persistent jobs, Git synchronization, audit/runtime hardening, and remote MCP integration.

## Security model

The MCP client, model-generated tool calls, repository content, paths, command arguments, and process output are untrusted input. Deterministic local policy is the security boundary.

Core rules:

- **Deny by default.** No project is available unless explicitly configured.
- **Project IDs instead of host paths.** Clients address logical IDs; absolute roots remain server-side.
- **Project-root confinement.** Filesystem access and process working directories are restricted to configured roots.
- **No path redirection.** Symlinks, redirecting Windows reparse points/junctions, nested mount points, and hard-linked regular files are denied for filesystem reads.
- **Race-resistant reads.** File identity is checked around open; directory identity is checked around enumeration.
- **No arbitrary shell.** Phase 5 exposes `run_process(...)`, never `shell(command)`.
- **Executable allowlists.** MCP callers select configured aliases; they cannot supply arbitrary executable paths.
- **Deterministic PATH resolution.** Unpinned names are searched only in validated absolute PATH directories; the process current directory and project-local PATH entries are never implicit lookup locations.
- **No shell-script fallback.** Known shells and Windows batch/PowerShell script targets are rejected.
- **Direct argv execution.** Processes are launched without shell-string parsing.
- **Confined working directories.** `cwd` is project-relative and validated by the Phase 4 `PathGuard`.
- **Minimal child environment.** Arbitrary parent environment variables and credentials are not inherited.
- **Bounded execution.** Argument count/size, runtime, combined stdout/stderr, and per-project concurrency are capped; excess concurrent calls fail immediately instead of building an unbounded queue.
- **Untrusted output handling.** Control characters are escaped and the configured project-root string is redacted before output is returned.
- **Local secrets stay local.** Real config, credentials, logs, runtime state, and machine-specific paths remain ignored by Git.
- **Hermetic tests.** Importing the reusable MCP server factory never reads machine-local runtime configuration.

Phase 5 is **controlled execution, not a kernel sandbox**. If an allowlisted program executes repository code, that code still runs with the operating-system privileges of the Local-MCP-Bridge process. `execute: true` is therefore a high-trust opt-in for code you are willing to run locally.

See [`docs/security-model.md`](docs/security-model.md) and [`docs/threat-model.md`](docs/threat-model.md).

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
│   │   ├── execution.py
│   │   └── filesystem.py
│   ├── config.py
│   ├── registry.py
│   ├── runtime.py
│   └── server.py
├── tests/
│   ├── test_execution.py
│   ├── test_execution_concurrency.py
│   ├── test_execution_resolution.py
│   ├── test_filesystem.py
│   ├── test_path_confinement.py
│   ├── test_path_races.py
│   ├── test_registry.py
│   ├── test_runtime_isolation.py
│   ├── test_server.py
│   └── test_unsafe_entries.py
├── future_modularity_expansion_proposal.md
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

Start the configured MCP server over stdio:

```powershell
python -m local_mcp_bridge
```

For MCP Inspector development:

```powershell
mcp dev src/local_mcp_bridge/runtime.py
```

## Configure authorized projects

Copy the tracked template to the ignored machine-local file:

```powershell
Copy-Item config/config.example.yaml config/config.yaml
```

Execution remains disabled until explicitly enabled:

```yaml
projects:
  example-project:
    root: "C:/absolute/path/to/example-project"
    permissions:
      read: true
      search: true
      execute: true
      git: false

    allowed_executables:
      - python
      - pytest
      - ruff

    execution:
      default_timeout_seconds: 60
      max_timeout_seconds: 300
      max_output_bytes: 262144
      max_concurrent_jobs: 1
```

Simple executable names are resolved by scanning only validated absolute entries from a constrained `PATH`; project-local PATH directories and implicit current-directory lookup are excluded. If the executable lives inside the project, for example in `.venv`, pin it explicitly in your ignored local config:

```yaml
allowed_executables:
  python: "C:/absolute/path/to/project/.venv/Scripts/python.exe"
  pytest: "C:/absolute/path/to/project/.venv/Scripts/pytest.exe"
```

Only `python` and `pytest` are then visible to the MCP client. The absolute executable paths remain internal to the bridge.

Do **not** enable `execute` merely because a command is allowlisted. An executable such as Python, pytest, a compiler, package manager, or build system may run project-controlled code and can therefore modify files, access the network, or access anything available to the bridge OS account.

## Current MCP tools

Phase 5 exposes:

- `health_check()`;
- `list_projects()`;
- `get_project(project_id)`;
- `list_directory(project_id, path=".")`;
- `read_file(project_id, path, start_line=1, max_lines=400)`;
- `search_text(project_id, query, path=".", case_sensitive=false, max_results=50)`;
- `run_process(project_id, executable, args=[], cwd=".", timeout_seconds=null)`.

There is no generic shell tool, caller-provided environment, arbitrary host executable path, interactive stdin, filesystem-write MCP primitive, or dedicated Git mutation tool.

## Phase 4 filesystem confinement

Before filesystem content is returned, the bridge validates project-relative paths, sensitive paths, path components, canonical containment, symlink/junction/reparse behavior, hard links, and file/directory identity. Recursive search reauthorizes entries immediately before use.

That boundary is reused by Phase 5 for process working directories.

## Phase 5 process execution flow

```text
run_process(project_id, executable_alias, argv, cwd, timeout)
        |
        v
resolve project + require execute=true
        |
        v
resolve allowlisted alias
        |
        +-- reject arbitrary/unallowlisted executable
        +-- reject shells and shell-script targets
        |
        v
validate bounded argv + timeout
        |
        v
PathGuard-confine project-relative cwd
        |
        v
resolve executable
        |
        +-- pinned canonical executable path, or
        +-- deterministic scan of validated PATH entries
        |
        v
recheck executable identity
        |
        v
acquire bounded project execution slot
        |
        +-- fail immediately when all slots are occupied
        |
        v
build minimal child environment
        |
        v
create_subprocess_exec(...)
        |
        +-- stdin = DEVNULL
        +-- stdout/stderr = bounded pipes
        +-- no shell
        |
        v
terminate on timeout/output ceiling
        |
        v
sanitize/redact bounded output
        |
        v
return structured result
```

A non-zero child exit code is returned as a normal structured process result. Policy failures, invalid paths, invalid argv, unallowlisted executable requests, and exhausted execution capacity fail as MCP errors.

### Execution limits

The implementation has hard ceilings in addition to local configuration:

- maximum timeout: 300 seconds;
- maximum combined captured stdout/stderr: 1 MiB;
- maximum per-project concurrent one-shot processes: 4;
- maximum arguments: 64;
- maximum individual argument length: 4096 characters;
- maximum combined argument length: 16384 characters.

The example configuration is intentionally stricter than those hard ceilings. Execution capacity is fail-fast: requests beyond the active per-project concurrency limit are rejected instead of queued inside the bridge.

## Important residual execution risk

Application-level command policy cannot turn arbitrary native or interpreted code into a sandboxed workload. In particular, allowlisted project code may still:

- read/write files accessible to the bridge OS user;
- make network requests;
- spawn descendant processes;
- consume resources within limits not enforced by the operating system;
- intentionally print sensitive host data it can access.

Phase 5 reduces accidental and model-driven command injection risk. It does **not** claim containment against intentionally hostile code. Stronger OS isolation, durable process supervision, cancellation, and job-state management are separate concerns; persistent supervision begins in Phase 6.

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
- **Phase 5:** Controlled process execution — complete
- **Phase 6:** Persistent local job manager
- **Phase 7:** Git synchronization tools
- **Phase 8:** Audit logging and runtime hardening
- **Phase 9:** Remote/tunnel integration
- **Phase 10:** Lightweight Claude MCP validation
- **Phase 11:** Real project integration and autonomous workflow testing

## Future connector modularity

The repository also preserves a detailed future architecture proposal in [`future_modularity_expansion_proposal.md`](future_modularity_expansion_proposal.md). The current implementation deliberately finishes the local security/execution foundations before generalizing the bridge into a multi-connector framework.

## Current status

**Phase 5 complete.** The bridge can inspect authorized project files and run explicitly allowlisted one-shot local processes under bounded policy. Phase 6 will introduce durable job IDs, state, output retrieval, cancellation, and persistent process supervision.

## License

MIT License. See [`LICENSE`](LICENSE).
