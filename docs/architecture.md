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
+-------------+-------------+
              v
+---------------------------+
| MCP tool layer            |
| - project metadata        |
| - read-only filesystem    |
| - controlled execution    |
+-------------+-------------+
              |
      +-------+-------+
      |               |
      v               v
+-------------+  +-------------------+
| Filesystem  |  | ExecutionService  |
| policy      |  | - execute permit  |
| + limits    |  | - alias allowlist |
+------+------+  | - argv/env limits |
       |         | - timeout/output  |
       |         +---------+---------+
       |                   |
       v                   v
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

## Active Phase 5 modules

```text
src/local_mcp_bridge/
├── config.py
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

`config.py` parses local YAML and constructs the immutable project registry. Phase 5 activates executable aliases and bounded execution settings in addition to filesystem permissions.

`registry.py` maps logical project IDs to canonical roots and host-only executable rules. Public project metadata exposes executable **aliases**, never pinned absolute executable paths.

`tools/filesystem.py` owns project read/search permissions, sensitive-path filtering, text/binary policy, output shaping, and filesystem resource ceilings.

`security/paths.py` owns the lower-level project path trust boundary. Phase 5 reuses `PathGuard` for process working-directory authorization.

`tools/execution.py` owns the Phase 5 execution boundary: permission checks, allowlisted executable aliases, constrained executable resolution, argv validation, minimal child environments, process creation without a shell, timeout/output enforcement, concurrency limits, process-output sanitization, and project-root redaction.

`server.py` remains a pure MCP factory. `runtime.py` remains the only composition root that reads ignored local configuration.

## Project identity boundary

MCP clients address logical IDs plus relative paths:

```text
read_file(project_id="example-project", path="src/main.py")
run_process(project_id="example-project", executable="pytest", args=["-q"], cwd=".")
```

Clients do not choose a host project root or arbitrary executable path per call.

## Phase 4 path flow

```text
relative caller path
      |
      v
lexical validation
      |
      v
sensitive-path policy (filesystem reads)
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
      +-- deny hard-linked regular files for reads
      |
      v
operation-specific identity checks
      |
      v
bounded I/O
```

## File-read identity protocol

`PathGuard.read_bounded` performs a check-open-check sequence:

```text
lstat/resolve path
      |
      v
capture FileIdentity(dev, inode, type)
      |
      v
os.open(read-only, O_NOFOLLOW where available)
      |
      v
fstat(open descriptor)
      |
      +-- identity must match
      |
      v
re-resolve/revalidate pathname
      |
      +-- identity/path must still match
      |
      v
read bounded bytes from verified descriptor
```

Directory snapshots likewise verify identity before and after enumeration. Recursive search stores project-relative paths and reauthorizes each directory/file immediately before use.

## Phase 5 execution model

Phase 5 adds a deliberately **one-shot** process primitive:

```text
run_process(project_id, executable_alias, args, cwd, timeout_seconds)
```

It is not a job manager. The MCP call remains active until the process exits, times out, hits the output ceiling, or is cancelled. Persistent job IDs and durable supervision belong to Phase 6.

### Authorization flow

```text
MCP run_process request
        |
        v
registry.require(project_id)
        |
        +-- require permissions.execute == true
        |
        v
ProjectRecord.require_executable(alias)
        |
        +-- no arbitrary executable path from caller
        |
        v
validate argv + timeout
        |
        v
normalize/PathGuard-authorize cwd
        |
        v
resolve configured executable target
        |
        v
recheck executable identity
        |
        v
build minimal environment
        |
        v
asyncio.create_subprocess_exec(...)
        |
        +-- stdin=DEVNULL
        +-- stdout/stderr=PIPE
        +-- direct argv; no shell
        |
        v
bounded collection + termination policy
        |
        v
sanitize/redact output
```

### Executable rules

There are two local configuration forms.

An unpinned alias:

```yaml
allowed_executables:
  - python
  - pytest
```

is resolved through a constrained `PATH`. Relative/empty PATH entries, redirecting PATH directories, unavailable entries, and entries inside the authorized project root are omitted. This reduces executable-substitution risk from a project-local `python`, `pytest`, or similarly named binary.

A pinned alias:

```yaml
allowed_executables:
  python: "C:/absolute/path/to/python.exe"
```

stores the canonical executable path locally while exposing only the alias `python` to MCP clients. Pinned targets are validated as non-redirecting regular executable files. The target identity is rechecked immediately before process creation.

The portable Python subprocess API cannot provide a single cross-platform, descriptor-based `exec` primitive with the same race guarantees as Phase 4 file reads. Executable replacement by a concurrently privileged local actor therefore remains a residual application-level race; pinned paths and identity rechecks narrow but do not eliminate it.

### Shell boundary

`ExecutionService` calls `asyncio.create_subprocess_exec`, not a shell API. Known shell targets such as `cmd.exe`, PowerShell, `sh`, and `bash` are rejected even if a local configuration attempts to place one behind an alias.

This prevents shell metacharacters in ordinary argv entries from becoming additional shell commands. It does not make arbitrary interpreters safe: for example, Python with `-c` can still execute Python code. That capability is part of the explicit high-trust execution grant.

### Working-directory boundary

`cwd` is a project-relative path and must pass `PathGuard.resolve_existing(..., expected="directory")`. Symlink/junction/mount traversal and lexical escape attempts therefore fail before process creation.

The child process itself is not filesystem-sandboxed. Once running, trusted executable/project code can access any path available to the bridge OS account.

### Environment boundary

The full parent environment is intentionally not inherited. Phase 5 constructs a small child environment from selected platform/runtime keys plus a constrained `PATH`, and sets Python hardening variables such as `PYTHONNOUSERSITE`.

There is no MCP parameter for arbitrary environment injection. This reduces accidental propagation of API keys, cloud credentials, tokens, and bridge-specific secrets into executed code.

### Resource boundary

Per-project policy controls:

```text
default timeout
maximum timeout
combined stdout/stderr bytes
concurrent one-shot processes
```

Hard application ceilings prevent local configuration from making these unbounded. Arg count and arg character counts also have hard ceilings.

When the combined output budget is exhausted, the process is terminated rather than continuing with unread pipes. Timeouts also terminate the launched process. POSIX launches use a new session and termination targets the process group; Windows uses a new process group but the standard library kill primitive only guarantees termination of the direct child. Durable process-tree supervision is deferred to the Phase 6 job manager/runtime-hardening work.

### Output boundary

Child output is untrusted. Captured bytes are bounded before decoding. Unsupported terminal/control characters are rendered as escaped text, reducing terminal-control injection. The configured canonical project-root string is replaced with `<project-root>` when it appears directly in returned output.

This redaction is defense in depth, not a secrecy guarantee: executable code can intentionally discover and encode host information in forms that cannot be generically recognized.

## Test/runtime isolation

```text
pytest -> pure server factory + tmp_path registries
runtime -> load_runtime_registry() -> local config
```

Machine-local `config/config.yaml` cannot break pytest collection. Execution tests use explicit temporary registries and pinned test-interpreter paths rather than depending on a developer's machine configuration.

## Security boundary

Phase 5 is still an application-level security layer, not an OS sandbox. `execute=true` means the operator has intentionally granted the project the ability to run allowlisted programs under the bridge account. Any executable capable of evaluating project-controlled code inherits the security implications of that code.

The security purpose of Phase 5 is therefore to stop **unapproved process selection, shell injection, working-directory escape, accidental credential inheritance, runaway output, and unbounded one-shot execution**. It does not claim to contain hostile native/interpreted code.

## Phase 6 boundary

Phase 6 will replace one-shot-only execution as the primary long-running workflow with a persistent local job manager:

```text
start_job(...) -> job_id
get_job_status(job_id)
read_job_output(job_id)
cancel_job(job_id)
```

That phase should own durable state, background process lifecycle, stronger descendant-process handling, output persistence/rotation, cancellation semantics, restart recovery policy, and job cleanup.

## Future Git and connector boundaries

Dedicated Git capabilities remain deferred to Phase 7 so Git-specific destructive operations can receive their own policy rather than being treated as generic shell commands.

Long-term connector modularity is documented separately in `future_modularity_expansion_proposal.md`; the current architecture intentionally completes the local execution/audit foundations before introducing heterogeneous external connectors.

## Transport boundary

Development uses local MCP stdio. Browser/cloud MCP clients will later connect only through authenticated encrypted transport/tunneling while all project/path/execution permissions remain enforced locally.
