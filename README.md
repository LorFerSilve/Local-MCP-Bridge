# Local-MCP-Bridge

A security-scoped Model Context Protocol (MCP) bridge for controlled local filesystem access and development task execution.

## Purpose

Local-MCP-Bridge lets an MCP-compatible AI client interact with explicitly authorized local development projects without granting an unrestricted shell or arbitrary host-path access.

The project is built in layers: project authorization, read-only filesystem access, hardened path confinement, controlled process execution, persistent background jobs, Git synchronization, audit/runtime hardening, and remote MCP integration.

## Security model

The MCP client, model-generated tool calls, repository content, paths, command arguments, process output, and persisted job state are untrusted input. Deterministic local policy is the security boundary.

Core rules:

- **Deny by default.** No project is available unless explicitly configured.
- **Project IDs instead of host paths.** Clients address logical IDs; absolute roots remain server-side.
- **Project-root confinement.** Filesystem access and process working directories are restricted to configured roots.
- **No path redirection.** Symlinks, redirecting Windows reparse points/junctions, nested mount points, and hard-linked regular files are denied for filesystem reads.
- **Race-resistant reads.** File identity is checked around open; directory identity is checked around enumeration.
- **No arbitrary shell.** The bridge exposes structured process/job tools, never `shell(command)`.
- **Executable allowlists.** MCP callers select configured aliases; they cannot supply arbitrary executable paths.
- **Deterministic PATH resolution.** Unpinned names are searched only in validated absolute PATH directories; the current directory and project-local PATH entries are never implicit lookup locations.
- **No shell-script fallback.** Known shells and Windows batch/PowerShell script targets are rejected.
- **Direct argv execution.** Processes are launched without shell-string parsing.
- **Confined working directories.** `cwd` is project-relative and validated by the Phase 4 `PathGuard`.
- **Minimal child environment.** Arbitrary parent environment variables and credentials are not inherited.
- **Bounded execution.** Argument count/size, runtime, output, and concurrency are capped.
- **Bounded job management.** Background-job inventory, retained history, state-file size, recovery work, list results, and output pages are capped.
- **No argv persistence.** Raw command arguments are never written to job-state files.
- **Hardened job-state paths.** Runtime job state rejects redirecting symlink/junction/reparse components and unsafe state files.
- **Recovery reauthorization.** Persisted jobs are exposed again only when the current project and executable policy still authorizes them.
- **Untrusted output handling.** Control characters are escaped and the configured project-root string is redacted before output is returned or retained as job output; recovered output is processed again.
- **Local runtime state stays local.** Real config, credentials, job state, logs, and machine-specific paths remain ignored by Git.
- **Hermetic tests.** Importing the reusable MCP server factory never reads machine-local configuration or creates persistent runtime state.

Phases 5 and 6 provide **controlled application-level execution, not a kernel sandbox**. If an allowlisted program executes repository code, that code still runs with the operating-system privileges of the Local-MCP-Bridge process. `execute: true` is therefore a high-trust opt-in.

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
│   ├── jobs.py
│   ├── registry.py
│   ├── runtime.py
│   └── server.py
├── tests/
│   ├── test_execution.py
│   ├── test_execution_concurrency.py
│   ├── test_execution_resolution.py
│   ├── test_filesystem.py
│   ├── test_jobs.py
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

Only aliases such as `python` and `pytest` are exposed to MCP clients. Absolute executable paths remain server-side.

Do **not** enable `execute` merely because a command is allowlisted. Python, pytest, compilers, package managers, and build systems can execute project-controlled code and may therefore modify files, access the network, or access anything available to the bridge OS account.

## Current MCP tools

Phase 6 exposes:

- `health_check()`;
- `list_projects()`;
- `get_project(project_id)`;
- `list_directory(project_id, path=".")`;
- `read_file(project_id, path, start_line=1, max_lines=400)`;
- `search_text(project_id, query, path=".", case_sensitive=false, max_results=50)`;
- `run_process(project_id, executable, args=[], cwd=".", timeout_seconds=null)`;
- `start_job(project_id, executable, args=[], cwd=".", timeout_seconds=null)`;
- `get_job(job_id)`;
- `list_jobs(project_id=null, limit=20)`;
- `get_job_output(job_id, stream="stdout", offset=0, max_chars=32768)`;
- `cancel_job(job_id)`.

There is no generic shell tool, caller-provided environment, arbitrary host executable path, interactive stdin, filesystem-write MCP primitive, or dedicated Git mutation tool.

## Filesystem confinement

Before filesystem content is returned, the bridge validates project-relative paths, sensitive paths, path components, canonical containment, symlink/junction/reparse behavior, hard links, and file/directory identity. Recursive search reauthorizes entries immediately before use.

That boundary is reused for process and job working directories.

## Controlled process execution

`run_process(...)` validates the project, execution permission, executable alias, bounded argv, timeout, confined working directory, executable identity, child environment, output ceiling, and execution capacity before returning a structured result. Non-zero child exit codes are returned as process results; policy violations fail as MCP errors.

Hard execution ceilings include:

- maximum timeout: 300 seconds;
- maximum combined captured stdout/stderr: 1 MiB;
- maximum per-project concurrent processes: 4;
- maximum arguments: 64;
- maximum individual argument length: 4096 characters;
- maximum combined argument length: 16384 characters.

Execution capacity is fail-fast: requests beyond configured active capacity are rejected rather than queued without a bound.

## Phase 6 persistent local job manager

`start_job(...)` applies the same Phase 5 execution policy but returns an opaque 32-character job ID instead of keeping the MCP call open until the process exits. The process then remains supervised by the running bridge and can be inspected from later MCP calls.

```text
start_job(...)
    |
    +--> validate project / alias / argv / cwd / timeout
    |
    +--> allocate opaque job_id
    |
    +--> persist safe job metadata
    |
    +--> start supervised background task
    |
    `--> return job_id immediately

later:

get_job(job_id) ----------> status / exit metadata
list_jobs(...) -----------> bounded recent job inventory
get_job_output(...) ------> bounded stdout/stderr page
cancel_job(job_id) -------> terminate supervised running job
```

### Job persistence

The configured runtime server stores job history by default under:

```text
runtime/jobs/
```

That directory is ignored by Git. A different location may be selected with:

```text
LOCAL_MCP_BRIDGE_JOB_STATE_DIR
```

When supplied, the override must be an absolute path. The state directory rejects redirecting symlink/junction/reparse components. State files are written through exclusive temporary files, `fsync`, and atomic replacement. Redirecting, hard-linked, malformed, and oversized state files are ignored during recovery, and actual record reads reuse the shared race-resistant `PathGuard` primitive.

Persistent state deliberately contains **no raw argv**. It stores only bounded safe metadata plus sanitized/redacted captured stdout and stderr. Output can still contain sensitive information deliberately printed by executed code, so `runtime/` must be treated as local sensitive state and must never be committed.

Recovery is a fresh authorization decision: a record is admitted only when the current registry still contains its project, `execute` remains enabled, and its executable alias remains allowlisted. Recovered output/error text is sanitized and project-root-redacted again before MCP exposure.

### Restart semantics

Terminal job history and terminal output can be recovered after the bridge restarts. A job recorded as `starting`, `running`, or `cancelling` when the bridge starts again is marked `interrupted`; the bridge does not claim to have resumed supervision.

Phase 6 intentionally does **not** persist a PID and later reattach to it. PIDs can be reused, and blindly killing a recovered PID could terminate an unrelated process. If the bridge process itself crashes or is forcibly terminated, an operating-system child may survive as an orphan depending on platform and failure mode. Stronger OS-level process containment is outside the Phase 6 boundary.

### Output polling semantics

Phase 6 persists the bounded final stdout/stderr captured by the Phase 5 runner. Output is paginated with stable character offsets after completion. The current implementation does not promise live durable streaming while the process is still running; a running job may therefore return an empty output page until its execution result is finalized.

### Job limits

In addition to the underlying process limits, the manager enforces:

- at most 32 active managed jobs globally;
- at most 512 configured retained terminal-history entries, with a default of 128;
- at most 100 jobs returned by one list call;
- at most 131072 characters returned by one output-page request;
- at most 8 MiB per persisted state file;
- at most 1024 candidate state files examined during startup recovery;
- at most 64 MiB of candidate state bytes attempted during startup recovery;
- at most 4 MiB combined recovered stdout/stderr characters per admitted record.

These limits are local safety ceilings and do not turn executed code into a sandbox.

## Residual execution risk

Application-level policy cannot turn arbitrary native or interpreted code into a sandboxed workload. Allowlisted project code may still:

- read/write files accessible to the bridge OS user;
- make network requests;
- spawn descendant processes;
- consume resources not bounded by the operating system;
- deliberately print sensitive host data it can access.

Phase 6 separates long-running work from individual MCP calls and adds bounded local state/recovery. It does **not** claim containment against intentionally hostile code. POSIX cancellation/timeout can target the launched process group; portable Windows behavior guarantees the direct child but does not claim recursive descendant containment.

## Configuration policy

Only templates belong in Git. Never commit real API keys, auth tokens, tunnel credentials, private certificates, personal machine configuration, or sensitive benchmark/job output.

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
- **Phase 6:** Persistent local job manager — complete
- **Phase 7:** Git synchronization tools
- **Phase 8:** Audit logging and runtime hardening
- **Phase 9:** Remote/tunnel integration
- **Phase 10:** Lightweight Claude MCP validation
- **Phase 11:** Real project integration and autonomous workflow testing

## Future connector modularity

The repository also preserves a detailed future architecture proposal in [`future_modularity_expansion_proposal.md`](future_modularity_expansion_proposal.md). The current implementation deliberately finishes the local security/execution foundations before generalizing the bridge into a multi-connector framework.

## Current status

**Phase 6 complete.** The bridge can inspect authorized project files, run bounded one-shot processes, and supervise background jobs by opaque ID with persistent terminal history, bounded output retrieval, cancellation, reauthorization, and conservative restart recovery. Phase 7 will add constrained Git synchronization tools.

## License

MIT License. See [`LICENSE`](LICENSE).
