# Local-MCP-Bridge

A security-scoped Model Context Protocol (MCP) bridge for controlled local filesystem access, development execution, background jobs, and constrained Git synchronization.

## Purpose

Local-MCP-Bridge lets an MCP-compatible AI client interact with explicitly authorized local development projects without granting an unrestricted shell, arbitrary host-path access, or a generic Git command surface.

The project is built in layers: project authorization, read-only filesystem access, hardened path confinement, controlled process execution, persistent background jobs, constrained Git synchronization, audit/runtime hardening, and remote MCP integration.

## Security model

The MCP client, model-generated tool calls, repository content, paths, command arguments, process output, persisted job state, Git metadata, and repository-local Git configuration are untrusted input. Deterministic machine-local policy is the security boundary.

Core rules:

- **Deny by default.** No project is available unless explicitly configured.
- **Project IDs instead of host paths.** Clients address logical IDs; absolute roots remain server-side.
- **Project-root confinement.** Filesystem access and process working directories are restricted to configured roots.
- **No path redirection.** Symlinks, redirecting Windows reparse points/junctions, nested mount points, and hard-linked regular files are denied for filesystem reads.
- **Race-resistant reads.** File identity is checked around open; directory identity is checked around enumeration.
- **No arbitrary shell.** Structured process/job tools are exposed instead of `shell(command)`.
- **Executable allowlists.** MCP callers select configured aliases; they cannot supply arbitrary executable paths.
- **Direct argv execution.** Processes are launched without shell-string parsing.
- **Minimal child environment.** Arbitrary parent environment variables and credentials are not inherited.
- **Bounded execution and jobs.** Runtime, output, arguments, concurrency, retained history, recovery work, and output pages are capped.
- **No argv persistence.** Raw command arguments are never written to persistent job-state files.
- **Recovery reauthorization.** Persisted jobs are exposed again only when current policy still authorizes them.
- **Dedicated Git boundary.** Phase 7 exposes status, trusted fetch, and clean fast-forward synchronization instead of generic Git execution.
- **Pinned Git policy.** Remote name, branch, and HTTPS URL come from an ignored local policy overlay, never from MCP request parameters.
- **No Git history rewriting.** No push, reset, clean, checkout, rebase, force operation, arbitrary refspec, arbitrary URL, or caller-supplied Git argv is exposed.
- **Repository-local Git config is untrusted.** Hooks, filters, credentials, includes, URL rewrites, submodule configuration, protocol/HTTP overrides, merge drivers, and other dangerous capabilities are rejected.
- **Local runtime state stays local.** Real config, Git policy, credentials, job state, logs, and machine-specific paths remain ignored by Git.
- **Hermetic server factory.** Importing the reusable MCP server factory never loads machine-local project/Git policy or creates persistent runtime state.

Phases 5–7 provide controlled application-level capabilities, **not a kernel sandbox**. Allowlisted executable code and Git itself still run with the operating-system privileges of the Local-MCP-Bridge process.

See [`docs/security-model.md`](docs/security-model.md), [`docs/threat-model.md`](docs/threat-model.md), and [`docs/phase-7-git-sync.md`](docs/phase-7-git-sync.md).

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
│   ├── config.py
│   ├── git_config.py
│   ├── jobs.py
│   ├── registry.py
│   ├── runtime.py
│   └── server.py
├── tests/
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

`git` is intentionally not a generic executable alias. Phase 7 uses a separate Git policy overlay.

If an executable lives inside the project, for example in `.venv`, pin it explicitly in the ignored base config:

```yaml
allowed_executables:
  python: "C:/absolute/path/to/project/.venv/Scripts/python.exe"
  pytest: "C:/absolute/path/to/project/.venv/Scripts/pytest.exe"
```

Do **not** enable `execute` merely because a command is allowlisted. Python, pytest, compilers, package managers, and build systems can execute project-controlled code and may modify files, access the network, or access anything available to the bridge OS account.

## Configure Phase 7 Git synchronization

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

The overlay activates the Git capability only for the listed project. The local repository's configured `remote.<name>.url` must match the trusted overlay URL exactly.

Phase 7 accepts HTTPS-only remote URLs without embedded credentials, query strings, or fragments. Interactive credential prompting and inherited credential helpers are intentionally disabled. This keeps the current synchronization boundary auditable; private-repository authentication that requires a credential integration is outside Phase 7.

An alternate local overlay may be selected with:

```text
LOCAL_MCP_BRIDGE_GIT_CONFIG
```

See [`docs/phase-7-git-sync.md`](docs/phase-7-git-sync.md) for the full Git security boundary.

## Current MCP tools

Phase 7 exposes:

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

There is no generic shell tool, arbitrary host executable path, caller-provided process environment, interactive stdin, filesystem-write MCP primitive, generic Git tool, Git push tool, or history-rewrite tool.

## Filesystem confinement

Before filesystem content is returned, the bridge validates project-relative paths, sensitive paths, path components, canonical containment, symlink/junction/reparse behavior, hard links, and file/directory identity. Recursive search reauthorizes entries immediately before use.

That boundary is reused for process and job working directories.

## Controlled process execution

`run_process(...)` validates the project, execution permission, executable alias, bounded argv, timeout, confined working directory, executable identity, child environment, output ceiling, and execution capacity before returning a structured result.

Hard execution ceilings include:

- maximum timeout: 300 seconds;
- maximum combined captured stdout/stderr: 1 MiB;
- maximum per-project concurrent processes: 4;
- maximum arguments: 64;
- maximum individual argument length: 4096 characters;
- maximum combined argument length: 16384 characters.

## Persistent local job manager

`start_job(...)` applies the same execution policy but returns an opaque job ID while the process continues under bridge supervision. Later calls can inspect metadata, poll bounded output, or request cancellation.

The configured runtime stores local job history under `runtime/jobs/` by default. `LOCAL_MCP_BRIDGE_JOB_STATE_DIR` can select another absolute path.

Persistent records deliberately contain no raw argv. State-file size, startup scan count, recovery bytes, retained history, active jobs, list sizes, and output pages are bounded. On restart, previously active records are marked `interrupted`; the bridge never blindly reattaches to a persisted PID.

## Phase 7 Git synchronization

### Status

`git_status(project_id)` validates the repository and returns path-free metadata: current branch, configured branch, HEAD object ID, clean/dirty state, staged/unstaged/untracked counts, and optional ahead/behind counts. Changed filenames are deliberately not exposed by this high-level tool.

### Fetch

`git_fetch(project_id)` fetches only the configured branch from the trusted local HTTPS URL into:

```text
refs/remotes/<configured-remote>/<configured-branch>
```

It does not fetch tags recursively, recurse into submodules, write `FETCH_HEAD`, or accept caller-provided URLs/refspecs.

### Fast-forward sync

`git_sync_fast_forward(project_id)` requires:

1. the configured branch to be checked out;
2. zero staged, unstaged, and untracked changes;
3. a successful trusted fetch;
4. the same branch and clean worktree after fetch;
5. local `HEAD` to be an ancestor of the fetched remote head.

Only then is a hook-disabled `--ff-only` update allowed, followed by verification that `HEAD` equals the fetched object ID. Divergence or local-ahead history is rejected rather than repaired with reset, rebase, merge commit, stash, or clean.

## Residual risk

Application-level policy cannot turn arbitrary native/interpreted code or Git into a sandboxed workload. Allowlisted project code may still access anything available to the bridge OS account. A local actor with equivalent OS privileges can race files/configuration between checks. A successful Phase 7 fast-forward intentionally modifies files inside the authorized working tree.

POSIX process termination can target the launched process group; portable Windows behavior guarantees the direct child but does not claim recursive descendant containment.

## Configuration policy

Only templates belong in Git. Never commit real API keys, auth tokens, tunnel credentials, private certificates, personal machine configuration, local Git policy containing sensitive endpoints, or sensitive benchmark/job output.

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
- **Phase 8:** Audit logging and runtime hardening
- **Phase 9:** Remote/tunnel integration
- **Phase 10:** Lightweight Claude MCP validation
- **Phase 11:** Real project integration and autonomous workflow testing

## Future connector modularity

The repository preserves a detailed future architecture proposal in [`future_modularity_expansion_proposal.md`](future_modularity_expansion_proposal.md). The current implementation finishes the local security/execution/synchronization foundations before generalizing the bridge into a multi-connector framework.

## Current status

**Phase 7 complete.** The bridge can inspect authorized project files, run bounded processes, supervise persistent background jobs, inspect trusted Git status, fetch one locally configured HTTPS branch, and advance a clean configured working tree by fast-forward only. Phase 8 will add audit logging and broader runtime hardening.

## License

MIT License. See [`LICENSE`](LICENSE).
