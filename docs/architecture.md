# Architecture

## Goal

Local-MCP-Bridge is a narrow, policy-enforced boundary between an MCP client and explicitly authorized local development resources. The model is never a trusted security principal. Machine-local policy remains authoritative even when repository content, model-generated requests, process output, persisted state, Git metadata, or audit-state paths are hostile.

## High-level architecture

```text
MCP client / AI agent
        |
        v
+-----------------------------------+
| Runtime composition root          |
| - loads ignored base config       |
| - applies ignored Git overlay     |
| - enables persistent job state    |
| - enables hardened audit state    |
+----------------+------------------+
                 v
+-----------------------------------+
| MCP tool layer                    |
| - project metadata                |
| - read-only filesystem            |
| - one-shot execution              |
| - managed background jobs         |
| - constrained Git sync            |
| - fixed-schema audit instrumentation|
+------+-------------------+--------+
       |                   |
       |                   +-------------------------+
       |                                             |
       v                                             v
+---------------+                         +----------------------+
| Filesystem    |                         | GitService           |
| service       |                         | - status             |
| + limits      |                         | - trusted fetch      |
+-------+-------+                         | - clean FF-only sync |
        |                                 +----------+-----------+
        |                                            |
        v                                 +----------v-----------+
+---------------+                         | GitRepository        |
| PathGuard     |                         | - .git validation    |
| confinement   |                         | - config audit       |
+-------+-------+                         | - trusted remote     |
        |                                 +----------+-----------+
        |                                            |
        v                                 +----------v-----------+
 local filesystem                         | GitCommandRunner     |
                                          | - constrained PATH   |
                                          | - minimal env        |
                                          | - HTTPS protocol     |
                                          | - bounded execution  |
                                          +----------+-----------+
                                                     |
                                                     v
                                                 git process

MCP job tools
       |
       v
+-------------------+
| JobManager        |
| - opaque job IDs  |
| - persistence     |
| - recovery        |
+---------+---------+
          |
          v
+-------------------+
| ExecutionService  |
| - execute permit  |
| - alias allowlist |
| - argv/env limits |
| - timeout/output  |
+---------+---------+
          |
          v
   direct subprocess
      (no shell)

Security-relevant tool activity
       |
       v
+-------------------+
| AuditLogger       |
| - fixed schema    |
| - bounded JSONL   |
| - hardened path  |
| - rotation/fsync |
+---------+---------+
          |
          v
   runtime/audit/
   (local-only)
```

## Active Phase 8 modules

```text
src/local_mcp_bridge/
├── audit.py
├── config.py
├── git_config.py
├── jobs.py
├── registry.py
├── runtime.py
├── server.py
├── security/
│   └── paths.py
└── tools/
    ├── execution.py
    ├── filesystem.py
    ├── git_repository.py
    ├── git_runner.py
    └── git_service.py
```

`config.py` parses the base local YAML and constructs the immutable project registry.

`git_config.py` applies the separate local-only Git overlay. The overlay binds a logical project ID to one trusted remote name, one branch, one HTTPS URL, and bounded Git resource limits. Applying the overlay turns on that project's Git capability; MCP request data never supplies those values.

`registry.py` maps logical project IDs to canonical roots and host-only executable/Git rules. Public project metadata exposes capability flags and executable aliases but not absolute roots, pinned executable paths, or trusted remote URLs.

`tools/filesystem.py` owns project read/search permissions, sensitive-path filtering, text/binary policy, output shaping, and filesystem resource ceilings.

`security/paths.py` owns the reusable path trust boundary. Filesystem reads, process working-directory validation, and persisted job-state reads reuse `PathGuard` rather than implementing independent path rules.

`tools/execution.py` owns controlled process execution: permission checks, executable aliases, constrained executable resolution, argv validation, minimal child environments, shell-free process creation, timeout/output enforcement, concurrency limits, output sanitization, and project-root redaction.

`jobs.py` owns background-job lifecycle and bounded persistence. It delegates process creation and termination to `ExecutionService` so background execution cannot bypass the execution policy.

`tools/git_runner.py` is a separate Git-only process primitive. It does not accept arbitrary MCP argv. It resolves Git outside the project root from a constrained absolute `PATH`, launches without a shell, applies a minimal environment, disables system/global Git config and interactive authentication, limits transport to HTTPS, disables hooks/submodule recursion/automatic maintenance, and enforces timeout/output ceilings.

`tools/git_repository.py` validates the repository trust boundary before Git operations. It validates `.git` metadata, exact worktree identity, local Git configuration, configured remote URL, and status/ref helpers.

`tools/git_service.py` implements the only public Git semantics: path-free status, configured-branch fetch, and clean fast-forward synchronization. It serializes Git operations per project and fails concurrent operations immediately.

`audit.py` owns Phase 8 operational auditing. It accepts only a fixed bounded metadata schema, validates the local audit path and active/archive files, appends one JSONL event at a time, fsyncs successful writes, rotates bounded local state, and tracks whether the sink is currently healthy. It deliberately has no raw request/output/error field.

`server.py` remains a pure MCP factory. By default it creates in-memory/non-persistent services and a disabled `AuditLogger`, so importing or constructing the reusable server does not touch machine-local configuration, job state, or audit state.

`runtime.py` is the configured composition root. It loads base config, applies the optional Git overlay, prepares the persistent audit logger, records bootstrap activity, installs disk-backed job state, and injects those runtime services into the pure server factory.

## Project identity boundary

Clients address logical IDs and project-relative paths:

```text
read_file(project_id="example-project", path="src/main.py")
run_process(project_id="example-project", executable="pytest", args=["-q"], cwd=".")
start_job(project_id="example-project", executable="pytest", args=["-q"], cwd=".")
git_status(project_id="example-project")
git_fetch(project_id="example-project")
git_sync_fast_forward(project_id="example-project")
```

Clients do not choose a host root, job-state directory, audit directory, executable path, Git binary, remote URL, remote name, branch, refspec, protocol, or raw Git arguments per call.

## Path-confinement flow

```text
relative caller path
      |
      v
lexical validation
      |
      v
lstat each component
      |
      +-- deny symlink / redirecting reparse point / nested mount
      |
      v
strict canonical resolution
      |
      +-- prove inside configured root
      |
      v
validate regular file/directory
      |
      +-- deny hard-linked regular files for protected reads
      |
      v
operation-specific identity checks
      |
      v
bounded I/O
```

`PathGuard.read_bounded` uses a check-open-check sequence: capture file identity, open read-only with `O_NOFOLLOW` where available, compare descriptor identity, revalidate the pathname, then read bounded bytes. Directory snapshots similarly verify directory identity around enumeration.

## One-shot execution model

`run_process(...)` validates the selected project and executable alias, bounded argv, timeout, and confined `cwd`; resolves and rechecks the executable; constructs a minimal child environment; and starts the process with `asyncio.create_subprocess_exec` rather than a shell.

The full parent environment is not inherited, stdin is disabled, stdout/stderr share a bounded capture budget, and timeout/output-limit termination is enforced. POSIX termination targets the launched process group. On Windows, the portable Python primitive guarantees termination of the direct child but not every descendant.

In the configured Phase 8 runtime, a persistent `process.run / attempt` audit record must be written before process execution is allowed to reach `ExecutionService`.

## Managed-job model

Longer work uses the job manager:

```text
start_job(...)
    |
    v
persist audit attempt
    |
    v
validate project / execute permission / alias / argv / cwd / timeout
    |
    v
allocate opaque 128-bit job ID
    |
    v
persist safe metadata (never raw argv)
    |
    v
create supervised async task
    |
    v
ExecutionService.run_process(...)
    |
    +--> status transitions
    +--> bounded sanitized stdout/stderr
    +--> timeout/output-limit/nonzero-exit result
    |
    v
persist terminal state
```

Later MCP calls use only the opaque ID:

```text
get_job(job_id)
list_jobs(...)
get_job_output(job_id, stream, offset, max_chars)
cancel_job(job_id)
```

`cancel_job` is also pre-audited in the configured runtime because it changes supervised process state.

## Persistent job-state boundary

The configured runtime stores job records below `runtime/jobs/` by default. `LOCAL_MCP_BRIDGE_JOB_STATE_DIR` can select another absolute path. The state directory is local-only and ignored by Git.

Each record contains safe metadata and already-sanitized terminal output. Raw command arguments are deliberately absent. Persistence uses exclusive temporary files, flush/fsync, and atomic replacement. Runtime-state paths and recovered records are treated as untrusted input and are reauthorized against current policy.

A persisted `starting`, `running`, or `cancelling` record is converted to `interrupted` on startup. The bridge deliberately does not persist a PID and later reattach to it because PID reuse makes blind reattachment unsafe.

## Git policy composition

Git configuration is deliberately split from the base project config:

```text
config/config.yaml
    |
    v
load_runtime_registry()
    |
    v
base ProjectRegistry (git disabled)
    |
    +---- config/git.local.yaml or LOCAL_MCP_BRIDGE_GIT_CONFIG
    |
    v
load_runtime_git_registry(...)
    |
    v
ProjectRegistry with selected projects carrying GitSettings + git=true
```

This preserves backwards compatibility for existing local config while making Git activation an explicit second capability grant.

The Git overlay is ignored by Git and rejects duplicate/unknown keys, unknown projects, unsafe remote/branch names, non-HTTPS URLs, embedded credentials, and limits beyond hard ceilings.

## Git repository-validation boundary

Before each exposed Git operation:

```text
authorized project + GitSettings
       |
       v
validate .git is a real directory
       |
       +-- reject gitfile/worktree indirection
       +-- reject external object alternates / commondir
       +-- inspect critical metadata files
       +-- inspect existing remote-ref directories
       |
       v
git rev-parse --show-toplevel
       |
       +-- must resolve exactly to authorized project root
       |
       v
audit repository-local Git config
       |
       +-- reject command-execution / transport-redirection capabilities
       |
       v
verify remote.<configured>.url == trusted overlay URL
```

Repository-local configuration is not trusted merely because it sits under `.git`. High-risk namespaces include aliases, credential configuration, filters, hooks, includes, submodules, URL rewrites, merge drivers, HTTP/protocol overrides, external diff/filter commands, worktree/partial-clone redirection, and remote overrides beyond the permitted configured URL/fetch metadata.

## Git runner boundary

The Git runner is distinct from `ExecutionService` because Git has different policy requirements. It:

- resolves a system Git binary only from validated absolute PATH directories outside the project;
- rejects redirecting/non-regular Git executable paths and rechecks executable identity;
- launches with direct argv and no shell;
- builds a minimal child environment instead of forwarding arbitrary parent credentials/config;
- disables system/global Git config;
- disables interactive terminal authentication and inherited credential helpers;
- restricts protocol use to HTTPS;
- disables hooks, fsmonitor, submodule recursion, auto-GC, and auto-maintenance;
- bounds combined output and runtime.

Generic execution and Git synchronization are separate capabilities. The runtime refuses to enable generic `execute` with an allowlisted `git`/`git.exe` command, preventing an obvious bypass of the narrow Git API.

## Fast-forward synchronization flow

```text
persist git.sync_fast_forward attempt
       |
       v
validate repository
       |
       v
require configured branch checked out
       |
       v
require zero staged / unstaged / untracked changes
       |
       v
fetch configured branch from trusted HTTPS URL
       |
       v
revalidate repository + branch + clean state
       |
       v
capture local HEAD and verified fetched target
       |
       +-- same? return no-op
       |
       v
merge-base --is-ancestor HEAD <remote-ref>
       |
       +-- no -> reject divergence/local-ahead state
       |
       v
merge --ff-only --no-overwrite-ignore <remote-ref>
       |
       v
verify new HEAD == previously verified fetched target
```

Fetch and synchronization both require a successful persistent pre-operation audit write in the configured runtime. Phase 7/8 never resolves a conflict or divergence with reset, clean, stash, rebase, merge commit, force, or history rewriting.

## Phase 8 audit flow

```text
MCP tool call
    |
    +-- read-only/inspection action
    |       |
    |       v
    |   perform policy-controlled action
    |       |
    |       v
    |   best-effort completion audit
    |       +-- failure => logger unhealthy / health degraded
    |
    +-- high-impact action
            |
            v
       strict attempt audit
            |
            +-- failure => refuse action before effect
            |
            v
       perform policy-controlled effect
            |
            v
       completion audit
            +-- failure => logger unhealthy; effect is not falsely rolled back
```

High-impact actions are `run_process`, `start_job`, `cancel_job`, `git_fetch`, and `git_sync_fast_forward`.

The logger records only fixed metadata such as project ID, counts, booleans, job-state enums, stream enum, termination reason, and ahead/behind counts. It cannot accept arbitrary path strings, argv, file content, query text, output, Git URLs, credentials, environment values, or exception text.

## Audit persistence boundary

The configured runtime stores operational audit records below `runtime/audit/` by default. `LOCAL_MCP_BRIDGE_AUDIT_DIR` can select another absolute local path.

The path is prepared component-by-component. Redirecting components and non-directories are rejected. Audit files must be regular, non-redirecting, single-link objects. New active files use exclusive creation; existing files are identity-checked around open; `O_NOFOLLOW` is used where available. Successful writes are fsynced. POSIX state receives private directory/file modes as defense in depth.

The active file defaults to a 4 MiB ceiling with five total retained files. Code-enforced hard limits cap the active file at 64 MiB, retained files at 16, one encoded event at 4096 bytes, and event details at 16 fields.

Audit files are not exposed through an MCP tool. `health_check()` exposes only `audit_enabled` and `audit_healthy`; an unhealthy configured logger changes overall health to `degraded`.

## Resource boundaries

Application ceilings include:

- process timeout hard ceiling: 300 seconds;
- combined captured process output hard ceiling: 1 MiB;
- managed jobs: 32 globally active, bounded retained/recovered state;
- at most one bridge-managed Git operation per project at a time;
- Git timeout hard ceiling: 120 seconds;
- combined Git output hard ceiling: 1 MiB;
- 256 KiB maximum local Git-policy overlay;
- audit active file: 4 MiB default / 64 MiB hard ceiling;
- audit files retained: 5 default / 16 hard ceiling;
- audit event: 4096-byte hard ceiling;
- audit details: 16 fields maximum.

These are application-level denial-of-service controls, not CPU/RAM/GPU/network/filesystem quotas for executed code or Git.

## Test/runtime isolation

```text
pytest -> pure server factory
          + injected/tmp registries/services
          + disabled audit logger by default
          + local temporary Git repos

runtime -> base config
           -> optional Git overlay
           -> hardened persistent AuditLogger
           -> disk-backed JobManager
           -> MCP server
```

Machine-local `config/config.yaml`, `config/git.local.yaml`, `runtime/jobs/`, and `runtime/audit/` are not implicit dependencies of reusable server tests. Invalid local runtime environment variables do not affect importing `local_mcp_bridge.server`.

## Security boundary

Phases 5–8 are application-level security layers, not an OS sandbox or tamper-proof forensic system. `execute=true` means selected programs can run under the bridge account. `git=true` means the bridge may intentionally fetch objects and fast-forward the authorized working tree under pinned local Git policy. Audit logging gives bounded local accountability but does not protect records from a local administrator or same-privilege attacker who can delete or alter them.

A local actor with equivalent OS privileges can still race filesystem/Git/audit metadata between checks. The policy is designed to constrain model-driven capability selection, reduce accidental secret persistence, and remove known execution/redirection surfaces; it does not claim containment of an already-compromised host.

## Future boundary

Phase 9 adds remote/tunnel integration only after authenticated encrypted exposure can preserve the same local project, path, execution, Git, job, and audit authorization boundaries.

Long-term connector modularity is documented separately in `future_modularity_expansion_proposal.md`.
