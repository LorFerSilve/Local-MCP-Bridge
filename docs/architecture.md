# Architecture

## Goal

Local-MCP-Bridge is a narrow, policy-enforced boundary between an MCP client and explicitly authorized local development resources. The model is never a trusted security principal.

## High-level architecture

```text
MCP client / AI agent
        |
        v
+--------------------------------+
| Runtime composition root       |
| - loads ignored base config    |
| - applies ignored Git overlay  |
| - enables persistent job state |
+---------------+----------------+
                v
+--------------------------------+
| MCP tool layer                 |
| - project metadata             |
| - read-only filesystem         |
| - one-shot execution           |
| - managed background jobs      |
| - constrained Git sync         |
+------+-------------+-----------+
       |             |
       |             +-----------------------------+
       |                                           |
       v                                           v
+-------------+                           +----------------------+
| Filesystem  |                           | GitService           |
| service     |                           | - status             |
| + limits    |                           | - trusted fetch      |
+------+------+                           | - clean FF-only sync |
       |                                  +----------+-----------+
       |                                             |
       |                                  +----------v-----------+
       |                                  | GitRepository        |
       |                                  | - .git validation    |
       |                                  | - config audit       |
       |                                  | - trusted remote     |
       |                                  +----------+-----------+
       |                                             |
       |                                  +----------v-----------+
       |                                  | GitCommandRunner     |
       |                                  | - constrained PATH   |
       |                                  | - minimal env        |
       |                                  | - HTTPS protocol     |
       |                                  | - bounded execution  |
       |                                  +----------+-----------+
       |                                             |
       |                                             v
       |                                         git process
       |
       +-------------------+
                           |
                           v
                  +-------------------+
                  | PathGuard         |
                  | confinement       |
                  +--------+----------+
                           |
                           v
                    local filesystem

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
```

## Active Phase 7 modules

```text
src/local_mcp_bridge/
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

`git_config.py` applies the separate local-only Phase 7 Git overlay. The overlay binds a logical project ID to one trusted remote name, one branch, one HTTPS URL, and bounded Git resource limits. Applying the overlay turns on that project's Git capability; MCP request data never supplies those values.

`registry.py` maps logical project IDs to canonical roots and host-only executable/Git rules. Public project metadata exposes capability flags and executable aliases but not absolute roots, pinned executable paths, or trusted remote URLs.

`tools/filesystem.py` owns project read/search permissions, sensitive-path filtering, text/binary policy, output shaping, and filesystem resource ceilings.

`security/paths.py` owns the reusable path trust boundary. Filesystem reads, process working-directory validation, and persisted job-state reads reuse `PathGuard` rather than implementing independent path rules.

`tools/execution.py` owns controlled process execution: permission checks, executable aliases, constrained executable resolution, argv validation, minimal child environments, shell-free process creation, timeout/output enforcement, concurrency limits, output sanitization, and project-root redaction.

`jobs.py` owns background-job lifecycle and bounded persistence. It delegates process creation and termination to `ExecutionService` so background execution cannot bypass Phase 5 policy.

`tools/git_runner.py` is a separate Git-only process primitive. It does not accept arbitrary MCP argv. It resolves Git outside the project root from a constrained absolute `PATH`, launches without a shell, applies a minimal environment, disables system/global Git config and interactive authentication, limits transport to HTTPS, disables hooks/submodule recursion/automatic maintenance, and enforces timeout/output ceilings.

`tools/git_repository.py` validates the repository trust boundary before Git operations. It validates `.git` metadata, exact worktree identity, local Git configuration, configured remote URL, and status/ref helpers.

`tools/git_service.py` implements the only public Git semantics: path-free status, configured-branch fetch, and clean fast-forward synchronization. It serializes Git operations per project and fails concurrent operations immediately.

`server.py` remains a pure MCP factory. By default it creates in-memory/non-persistent services suitable for tests. `runtime.py` is the composition root that loads base config, applies the optional Git overlay, and installs disk-backed job state.

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

Clients do not choose a host root, job-state directory, executable path, Git binary, remote URL, remote name, branch, refspec, protocol, or raw Git arguments per call.

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

## Managed-job model

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

## Persistent state boundary

The configured runtime stores job records below `runtime/jobs/` by default. `LOCAL_MCP_BRIDGE_JOB_STATE_DIR` can select another absolute path. The state directory is local-only and ignored by Git.

Each record contains safe metadata and already-sanitized terminal output. Raw command arguments are deliberately absent. Persistence uses exclusive temporary files, flush/fsync, and atomic replacement. Runtime-state paths and recovered records are treated as untrusted input and are reauthorized against current policy.

A persisted `starting`, `running`, or `cancelling` record is converted to `interrupted` on startup. Phase 6 deliberately does not persist a PID and later reattach to it because PID reuse makes blind reattachment unsafe.

## Phase 7 Git policy composition

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
- disables hooks, fsmonitor, submodule recursion, reflog updates for Phase 7 commands, auto-GC, and auto-maintenance;
- bounds combined output and runtime.

Generic execution and Git synchronization are separate capabilities. The runtime refuses to enable generic `execute` with an allowlisted `git`/`git.exe` command, preventing an obvious bypass of the Phase 7 API.

## Git status flow

`git_status` returns only path-free metadata:

```text
validate repository
       |
       v
current branch + HEAD
       |
       v
porcelain status (internal)
       |
       +-- count staged / unstaged / untracked
       +-- do not return filenames
       |
       v
optional remote-tracking ref
       |
       +-- compute ahead/behind counts when present
```

## Git fetch flow

The fetch target is fixed by trusted local policy:

```text
refs/heads/<branch>
       |
       v
refs/remotes/<remote>/<branch>
```

The caller cannot alter the source URL, destination ref, branch, flags, or protocol. Tags and recursive submodules are disabled and `FETCH_HEAD` is not written.

## Fast-forward synchronization flow

```text
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
merge --ff-only --no-edit --no-stat <remote-ref>
       |
       v
verify new HEAD == previously verified fetched target
```

Phase 7 never resolves a conflict or divergence with reset, clean, stash, rebase, merge commit, force, or history rewriting.

## Resource boundaries

Process/job ceilings remain from Phases 5–6. Phase 7 adds:

- at most one bridge-managed Git operation per project at a time;
- Git timeout hard ceiling: 120 seconds;
- combined Git output hard ceiling: 1 MiB;
- conservative branch/remote-name limits;
- 256 KiB maximum local Git-policy overlay.

These are application-level denial-of-service controls, not CPU/RAM/network sandboxing of Git or project code.

## Test/runtime isolation

```text
pytest -> pure server factory + injected/tmp registries + local temp Git repos
runtime -> base config -> optional Git overlay -> disk-backed JobManager
```

Machine-local `config/config.yaml`, `config/git.local.yaml`, and `runtime/jobs/` are not implicit dependencies of reusable server tests.

## Security boundary

Phases 5–7 are application-level security layers, not an OS sandbox. `execute=true` means selected programs can run under the bridge account. `git=true` means the bridge may intentionally fetch objects and fast-forward the authorized working tree under the pinned local Git policy.

A local actor with equivalent OS privileges can race filesystem/Git metadata between checks. The policy is designed to constrain model-driven capability selection and remove known Git command-execution/redirection surfaces; it does not claim containment of an already-compromised host or hostile replacement Git binary supplied by the host administrator.

## Future boundaries

Phase 8 adds audit logging and broader runtime hardening. Phase 9 adds remote/tunnel integration only after authenticated encrypted exposure can preserve the same local authorization boundary.

Long-term connector modularity is documented separately in `future_modularity_expansion_proposal.md`.
