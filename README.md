# Local-MCP-Bridge

A security-scoped Model Context Protocol (MCP) bridge for controlled local filesystem access, development execution, persistent background jobs, constrained Git synchronization, and metadata-only operational auditing.

## Purpose

Local-MCP-Bridge lets an MCP-compatible AI client interact with explicitly authorized local development projects without granting an unrestricted shell, arbitrary host-path access, generic Git execution, or raw access to local runtime/audit state.

The project is built in layers: project authorization, read-only filesystem access, hardened path confinement, controlled process execution, persistent background jobs, constrained Git synchronization, audit/runtime hardening, and remote MCP integration.

## Security model

The MCP client, model-generated tool calls, repository content, paths, command arguments, process output, persisted job state, Git metadata, repository-local Git configuration, and local runtime-state paths are untrusted input. Deterministic machine-local policy is the security boundary.

Core rules:

- **Deny by default.** No project is available unless explicitly configured.
- **Project IDs instead of host paths.** Clients address logical IDs; absolute roots remain server-side.
- **Project-root confinement.** Filesystem access and process working directories are restricted to configured roots.
- **No path redirection.** Symlinks, redirecting Windows reparse points/junctions, nested mount points, and hard-linked protected files are denied at security-sensitive boundaries.
- **Race-resistant reads.** File identity is checked around open; directory identity is checked around enumeration.
- **No arbitrary shell.** Structured process/job tools are exposed instead of `shell(command)`.
- **Executable allowlists.** MCP callers select configured aliases; they cannot supply arbitrary executable paths.
- **Direct argv execution.** Processes are launched without shell-string parsing.
- **Minimal child environment.** Arbitrary parent environment variables and credentials are not inherited.
- **Bounded execution and jobs.** Runtime, output, arguments, concurrency, retained history, recovery work, and output pages are capped.
- **No argv persistence.** Raw command arguments are never written to persistent job-state files.
- **Recovery reauthorization.** Persisted jobs are exposed again only when current policy still authorizes them.
- **Dedicated Git boundary.** Git is limited to status, trusted fetch, and clean fast-forward synchronization instead of generic Git execution.
- **Pinned Git policy.** Remote name, branch, and HTTPS URL come from an ignored local policy overlay, never from MCP request parameters.
- **No Git history rewriting.** No push, reset, clean, checkout, rebase, force operation, arbitrary refspec, arbitrary URL, or caller-supplied Git argv is exposed.
- **Repository-local Git config is untrusted.** Hooks, filters, credentials, includes, URL rewrites, submodules, protocol/HTTP overrides, merge drivers, and other dangerous capabilities are rejected.
- **Metadata-only audit schema.** Phase 8 audit events cannot contain raw argv, output, queries, caller paths, file content, Git URLs, credentials, environment values, or arbitrary exception text.
- **Fail-closed sensitive actions.** In the configured runtime, process execution, job start/cancel, Git fetch, and Git synchronization require a successful persistent pre-operation audit record.
- **Bounded local audit state.** Audit paths reject unsafe redirection/hard links; event size, active-log size, and retained files are capped.
- **Audit logs are not MCP context.** There is no raw audit-log reader tool.
- **Local runtime state stays local.** Real config, Git policy, credentials, job state, audit logs, and machine-specific paths remain ignored by Git.
- **Hermetic server factory.** Importing/reusing the pure MCP server factory never loads machine-local project/Git/audit policy or creates persistent runtime state.

Phases 5–8 provide controlled application-level capabilities, **not a kernel sandbox or tamper-proof forensic system**. Allowlisted executable code and Git still run with the operating-system privileges of the Local-MCP-Bridge process.

See [`docs/security-model.md`](docs/security-model.md), [`docs/threat-model.md`](docs/threat-model.md), [`docs/phase-7-git-sync.md`](docs/phase-7-git-sync.md), and [`docs/phase-8-audit-runtime-hardening.md`](docs/phase-8-audit-runtime-hardening.md).

## Repository layout

```text
Local-MCP-Bridge/
├── .github/
│   └── workflows/
├── config/
│   ├── config.example.yaml
│   └── git.example.yaml
├── docs/
│   ├── architecture.md
│   ├── phase-7-git-sync.md
│   ├── phase-8-audit-runtime-hardening.md
│   ├── security-model.md
│   └── threat-model.md
├── src/local_mcp_bridge/
│   ├── security/
│   │   └── paths.py
│   ├── tools/
│   │   ├── execution.py
│   │   ├── filesystem.py
│   │   ├── git_repository.py
│   │   ├── git_runner.py
│   │   └── git_service.py
│   ├── audit.py
│   ├── config.py
│   ├── git_config.py
│   ├── jobs.py
│   ├── registry.py
│   ├── runtime.py
│   └── server.py
├── tests/
│   ├── test_audit.py
│   ├── test_execution.py
│   ├── test_filesystem.py
│   ├── test_git.py
│   ├── test_git_config.py
│   ├── test_jobs.py
│   ├── test_path_confinement.py
│   ├── test_registry.py
│   ├── test_runtime_isolation.py
│   ├── test_server.py
│   └── ...
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

Copy the tracked base template to the ignored local file:

```powershell
Copy-Item config/config.example.yaml config/config.yaml
```

Example base policy:

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

`git` is intentionally not a generic executable alias. Dedicated Git synchronization uses a separate policy overlay.

If an executable lives inside the project, for example in `.venv`, pin it explicitly in the ignored base config:

```yaml
allowed_executables:
  python: "C:/absolute/path/to/project/.venv/Scripts/python.exe"
  pytest: "C:/absolute/path/to/project/.venv/Scripts/pytest.exe"
```

Do **not** enable `execute` merely because a command is allowlisted. Python, pytest, compilers, package managers, and build systems can execute project-controlled code and may modify files, access the network, or access anything available to the bridge OS account.

## Configure Git synchronization

Copy the tracked Git template to the ignored local overlay:

```powershell
Copy-Item config/git.example.yaml config/git.local.yaml
```

Example:

```yaml
projects:
  example-project:
    remote: origin
    branch: main
    remote_url: "https://github.com/example/example-project.git"
    timeout_seconds: 60
    max_output_bytes: 262144
```

The overlay activates the Git capability only for listed projects. The local repository's configured `remote.<name>.url` must match the trusted overlay URL exactly.

The Git boundary accepts HTTPS-only remote URLs without embedded credentials, query strings, or fragments. Interactive credential prompting and inherited credential helpers are disabled. An alternate local overlay may be selected with `LOCAL_MCP_BRIDGE_GIT_CONFIG`.

See [`docs/phase-7-git-sync.md`](docs/phase-7-git-sync.md) for the full Git security boundary.

## Current MCP tools

Phase 8 retains the same narrow public tool surface:

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
- `cancel_job(job_id)`;
- `git_status(project_id)`;
- `git_fetch(project_id)`;
- `git_sync_fast_forward(project_id)`.

There is no generic shell tool, arbitrary host executable path, caller-provided process environment, interactive stdin, filesystem-write MCP primitive, generic Git tool, Git push tool, history-rewrite tool, or raw audit-log reader.

## Filesystem confinement

Before filesystem content is returned, the bridge validates project-relative paths, sensitive paths, path components, canonical containment, symlink/junction/reparse behavior, hard links, and file/directory identity. Recursive search reauthorizes entries immediately before use.

That boundary is reused for process and job working directories.

## Controlled process execution

`run_process(...)` validates project authorization, executable alias, bounded argv, timeout, confined working directory, executable identity, child environment, output ceiling, and execution capacity before returning a structured result.

Hard execution ceilings include:

- maximum timeout: 300 seconds;
- maximum combined captured stdout/stderr: 1 MiB;
- maximum per-project concurrent processes: 4;
- maximum arguments: 64;
- maximum individual argument length: 4096 characters;
- maximum combined argument length: 16384 characters.

In the configured Phase 8 runtime, execution also requires a successful persistent `process.run / attempt` audit event **before** the subprocess service is invoked.

## Persistent local job manager

`start_job(...)` applies the same execution policy but returns an opaque job ID while the process continues under bridge supervision. Later calls can inspect metadata, poll bounded output, or request cancellation.

The configured runtime stores local job history under `runtime/jobs/` by default. `LOCAL_MCP_BRIDGE_JOB_STATE_DIR` can select another absolute path.

Persistent records deliberately contain no raw argv. State-file size, startup scan count, recovery bytes, retained history, active jobs, list sizes, and output pages are bounded. On restart, previously active records are marked `interrupted`; the bridge never blindly reattaches to a persisted PID.

Phase 8 requires successful pre-operation audit persistence before `start_job` and `cancel_job` may affect supervised process state.

## Constrained Git synchronization

`git_status(project_id)` returns path-free branch/HEAD/cleanliness and ahead/behind metadata. Changed filenames are deliberately not exposed.

`git_fetch(project_id)` fetches only the configured branch from the trusted local HTTPS URL into its configured remote-tracking ref. It does not accept caller-provided URL/refspec/flags.

`git_sync_fast_forward(project_id)` requires the configured branch, zero staged/unstaged/untracked changes, a successful trusted fetch, post-fetch revalidation, an ancestor proof, `merge --ff-only --no-overwrite-ignore`, and final verification that `HEAD` equals the previously fetched object ID.

`git_fetch` and `git_sync_fast_forward` are Phase 8 high-impact operations and require a persistent pre-operation audit event.

## Phase 8 audit logging and runtime hardening

The configured runtime writes operational audit events under:

```text
runtime/audit/
```

An alternate local directory may be selected with:

```text
LOCAL_MCP_BRIDGE_AUDIT_DIR
```

An explicit override must be absolute. Unsafe redirecting path components, non-directories, redirecting/non-regular/hard-linked audit files, and unsafe rotation targets fail closed.

Audit events are bounded JSONL metadata. Each event has a schema version, UTC timestamp, opaque session/event IDs, a session-local sequence number, fixed action/outcome, optional validated project ID, and allowlisted scalar details.

The schema intentionally cannot record raw argv, stdout/stderr, search queries, caller file paths, file contents, executable targets, Git URLs, environment variables, credentials, tokens, headers, or arbitrary exception text.

High-impact configured-runtime actions require successful persistent `attempt` logging before effect:

```text
run_process
start_job
cancel_job
git_fetch
git_sync_fast_forward
```

If the audit sink is unavailable, these actions are refused before reaching their underlying service. Read-only operations keep accurate tool semantics if a completion audit fails; instead, `health_check()` reports `status="degraded"` and `audit_healthy=false`.

Audit growth is bounded:

- active log: 4 MiB default, 64 MiB hard ceiling;
- retained files: 5 default, 16 hard ceiling;
- encoded event: 4096 bytes maximum;
- detail fields: 16 maximum.

Successful event writes are fsynced. On POSIX, local audit directory/file permissions are tightened to `0700`/`0600` as defense in depth.

Raw audit files are deliberately **not** exposed as MCP model context. See [`docs/phase-8-audit-runtime-hardening.md`](docs/phase-8-audit-runtime-hardening.md).

## Runtime isolation

The pure `create_mcp_server(...)` factory is non-persistent by default. It does not load local project/Git config, persistent jobs, or audit directories. Persistent machine-local composition lives in `runtime.py`.

This means test/server imports remain deterministic even when local runtime environment variables are invalid. The configured runtime, by contrast, deliberately fails closed on invalid explicit config or unsafe persistent runtime state.

## Residual risk

Application-level policy cannot turn arbitrary native/interpreted code or Git into a sandboxed workload. Allowlisted project code may still access anything available to the bridge OS account. A local actor with equivalent OS privileges can race files/configuration/runtime state between checks. A successful fast-forward intentionally modifies files inside the authorized working tree.

Phase 8 audit logging is operational accountability, not cryptographic non-repudiation. A local administrator or same-privilege actor can delete or alter local audit files. There is no remote trusted log sink or signing key in Phase 8.

POSIX process termination can target the launched process group; portable Windows behavior guarantees the direct child but does not claim recursive descendant containment.

## Configuration policy

Only templates belong in Git. Never commit real API keys, auth tokens, tunnel credentials, private certificates, personal machine configuration, local Git policy containing sensitive endpoints, sensitive benchmark/job output, or runtime audit logs.

Local-only paths include:

```text
.env
config/config.yaml
config/config.local.yaml
config/git.local.yaml
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
- **Phase 7:** Git synchronization tools — complete
- **Phase 8:** Audit logging and runtime hardening — complete
- **Phase 9:** Remote/tunnel integration
- **Phase 10:** Lightweight Claude MCP validation
- **Phase 11:** Real project integration and autonomous workflow testing

## Future connector modularity

The repository preserves a detailed future architecture proposal in [`future_modularity_expansion_proposal.md`](future_modularity_expansion_proposal.md). The current implementation finishes the local security/execution/synchronization/audit foundations before generalizing the bridge into a multi-connector framework.

## Current status

**Phase 8 complete.** The bridge now combines confined read-only filesystem access, controlled process execution, persistent supervised jobs, constrained Git synchronization, and bounded metadata-only local auditing. High-impact configured-runtime actions fail closed when audit persistence is unavailable. Phase 9 will add authenticated encrypted remote/tunnel integration while preserving the local authorization boundary.

## License

MIT License. See [`LICENSE`](LICENSE).
