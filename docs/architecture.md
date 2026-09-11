# Architecture

## Goal

Local-MCP-Bridge is a narrow, policy-enforced boundary between an MCP client and explicitly authorized local development resources. The model is never a trusted security principal.

## High-level architecture

```text
MCP client / AI agent
        |
        v
+---------------------------+
| Runtime composition root  |
| - loads ignored config    |
| - builds registry         |
| - enables local job state |
+-------------+-------------+
              v
+---------------------------+
| MCP tool layer            |
| - project metadata        |
| - read-only filesystem    |
| - one-shot execution      |
| - managed background jobs |
+------+------+-------------+
       |      |
       |      +-------------------------+
       |                                |
       v                                v
+-------------+                +-------------------+
| Filesystem  |                | JobManager        |
| service     |                | - opaque job IDs  |
| + limits    |                | - status/output   |
+------+------+                | - cancellation    |
       |                       | - bounded history |
       |                       | - restart recovery|
       |                       +---------+---------+
       |                                 |
       |                         +-------+-------+
       |                         |               |
       |                         v               v
       |                +-------------------+  runtime/jobs/
       |                | ExecutionService  |  local state
       |                | - execute permit  |
       |                | - alias allowlist |
       |                | - argv/env limits |
       |                | - timeout/output  |
       |                +---------+---------+
       |                          |
       +-------------+------------+
                     v
+---------------------------+
| PathGuard confinement     |
| - lexical validation      |
| - lstat component checks  |
| - reparse/link rejection  |
| - canonical containment   |
| - identity verification   |
+-------------+-------------+
              |
       +------+------+
       |             |
       v             v
 local filesystem   direct subprocess
                   (no shell)
```

## Active Phase 6 modules

```text
src/local_mcp_bridge/
├── config.py
├── jobs.py
├── registry.py
├── runtime.py
├── server.py
├── security/
│   ├── __init__.py
│   └── paths.py
└── tools/
    ├── execution.py
    └── filesystem.py
```

`config.py` parses local YAML and constructs the immutable project registry.

`registry.py` maps logical project IDs to canonical roots and host-only executable rules. Public project metadata exposes executable aliases, never pinned absolute executable paths.

`tools/filesystem.py` owns project read/search permissions, sensitive-path filtering, text/binary policy, output shaping, and filesystem resource ceilings.

`security/paths.py` owns the reusable path trust boundary. Filesystem reads, process working-directory validation, and persisted job-state reads reuse `PathGuard` rather than implementing independent path rules.

`tools/execution.py` owns controlled process execution: permission checks, executable aliases, constrained executable resolution, argv validation, minimal child environments, shell-free process creation, timeout/output enforcement, concurrency limits, output sanitization, and project-root redaction.

`jobs.py` owns Phase 6 background-job lifecycle and bounded persistence. It delegates actual process creation and termination to `ExecutionService` so background execution cannot bypass Phase 5 policy.

`server.py` remains a pure MCP factory. By default it uses an in-memory job manager, which keeps imports and tests free of machine-local state. `runtime.py` is the composition root that loads ignored local configuration and installs the disk-backed job manager.

## Project identity boundary

Clients address logical IDs and project-relative paths:

```text
read_file(project_id="example-project", path="src/main.py")
run_process(project_id="example-project", executable="pytest", args=["-q"], cwd=".")
start_job(project_id="example-project", executable="pytest", args=["-q"], cwd=".")
```

Clients do not choose a host project root, job-state directory, or arbitrary executable path per call.

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

`run_process(...)` remains available for short operations. It validates the selected project and executable alias, bounded argv, timeout, and confined `cwd`; resolves and rechecks the executable; constructs a minimal child environment; and starts the process with `asyncio.create_subprocess_exec` rather than a shell.

The full parent environment is not inherited, stdin is disabled, stdout/stderr share a bounded capture budget, and timeout/output-limit termination is enforced. POSIX termination targets the launched process group. On Windows, the portable Python primitive guarantees termination of the direct child but not every descendant.

## Phase 6 managed-job model

Longer work uses the job manager:

```text
start_job(...)
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

`cancel_job` cancels the supervised task; cancellation propagates into `ExecutionService`, which terminates the process using the same Phase 5 termination policy.

## Persistent state boundary

The configured runtime stores job records below `runtime/jobs/` by default. `LOCAL_MCP_BRIDGE_JOB_STATE_DIR` can select another absolute path. The state directory is local-only and ignored by Git.

Each record contains safe metadata and already-sanitized terminal output. Raw command arguments are deliberately absent because argv may contain credentials or private data.

Persistence uses exclusive temporary files, flush/fsync, and atomic replacement. On POSIX the bridge applies restrictive directory/file modes as defense in depth. This is application-level hardening; it is not a substitute for OS account isolation or Windows ACL policy.

Runtime-state path components are inspected without following redirecting links. Recovery rejects symlink/junction/reparse state paths, hard-linked or non-regular state files, malformed schemas/types, invalid IDs/timestamps/statuses, and oversized records.

Actual state-file reads reuse `PathGuard.read_bounded`, including identity checks around the open. Recovery is additionally bounded by history count, directory scan count, per-file size, and a total startup byte budget.

## Recovery and reauthorization

Persisted state is untrusted input. A recovered record is admitted only when the current registry still contains its project, `execute` remains enabled, and its executable alias is still allowlisted. Output and error strings are sanitized and project-root-redacted again during recovery rather than trusting the bytes previously written to disk.

Terminal jobs can therefore survive a bridge restart without reopening authorization that has since been revoked.

A persisted `starting`, `running`, or `cancelling` record is converted to `interrupted` on startup. Phase 6 deliberately does not persist a PID and later reattach to it: PID reuse makes blind reattachment/termination unsafe. If the bridge process crashes, an OS child may survive independently; the recovered metadata does not imply that supervision resumed.

## Output semantics

The underlying Phase 5 runner captures output under a byte ceiling and sanitizes/redacts it before returning a result. Phase 6 stores that bounded terminal output and serves character-offset pages.

The current job manager does not promise live durable streaming while a process is running. Output becomes durable when the underlying invocation finalizes. This keeps Phase 6 persistence simple and avoids presenting partial state as a durable log protocol.

## Resource boundaries

Phase 6 adds manager-level ceilings on top of Phase 5 process ceilings:

- 32 active managed jobs globally;
- 512 retained terminal records maximum, 128 by default;
- 100 records returned by one list call;
- 131072 characters returned by one output-page call;
- 8 MiB maximum per state file;
- 1024 candidate state files examined at startup;
- 64 MiB maximum candidate bytes attempted during startup recovery;
- 4 MiB maximum recovered stdout/stderr characters per admitted record.

These are denial-of-service controls, not CPU/RAM/GPU/network sandboxing of executed code.

## Test/runtime isolation

```text
pytest -> pure server factory + in-memory JobManager + tmp_path registries
runtime -> load_runtime_registry() -> disk-backed JobManager
```

Machine-local `config/config.yaml` and `runtime/jobs/` cannot become implicit dependencies of reusable server tests.

## Security boundary

Phases 5 and 6 are application-level security layers, not an OS sandbox. `execute=true` means the operator intentionally allows selected programs to run under the bridge account. A programmable executable may execute project-controlled code that can access resources available to that account.

The bridge constrains *selection and orchestration*: project, executable alias, cwd-at-launch, argv shape, environment inheritance, timeout, captured output, concurrency, persistent metadata, and restart behavior. It does not contain intentionally hostile code.

## Future boundaries

Phase 7 adds dedicated Git synchronization tools so Git operations receive narrow policy instead of being treated as generic shell commands. Phase 8 adds broader audit logging and runtime hardening. Remote transport remains deferred until authenticated encrypted exposure can preserve the same local authorization boundary.

Long-term connector modularity is documented separately in `future_modularity_expansion_proposal.md`.
