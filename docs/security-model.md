# Security Model

## Trust model

The bridge assumes model-generated calls, repository content, MCP input, filenames, paths, command arguments, child-process output, persisted job state, Git metadata, repository-local Git configuration, and local audit-state paths may be malicious or incorrect. Deterministic machine-local policy is the authorization boundary; security never depends on the model behaving correctly.

## Deny by default

Projects are registered explicitly. Capability flags are `read`, `search`, `execute`, and `git`; missing permissions are denied. `search: true` requires `read: true`. Execution requires `execute: true` plus a non-empty executable allowlist. Git synchronization is enabled only when the separate local Git-policy overlay supplies an explicit policy for that project.

`security.deny_by_default` cannot be disabled and `security.allow_arbitrary_shell` cannot be enabled. Configurations attempting either change fail closed.

## Project registry boundary

The registry maps AI-visible project IDs to canonical local roots. Absolute roots remain server-side. Roots must exist, be absolute directories, and may not overlap.

Executable rules, execution settings, and Git synchronization settings are internal project data. Public metadata exposes executable aliases and capability flags only; pinned host paths and trusted Git remote URLs remain local.

Duplicate YAML keys, unknown project/permission/execution keys, invalid IDs, invalid permission combinations, invalid executable rules, and out-of-range limits fail closed.

No local config means an empty runtime registry. An explicitly selected invalid config fails closed. The pure MCP server factory does not read machine-local config or create persistent runtime state, keeping tests hermetic.

## Filesystem authorization

Every read-only filesystem request follows this sequence:

1. resolve the project ID and required permission;
2. parse a project-relative path and reject unsafe lexical forms;
3. reject common sensitive/credential paths;
4. inspect existing components without following redirections;
5. reject symbolic links, redirecting Windows reparse points/junctions, and nested filesystem mount points;
6. strictly resolve the effective path and prove canonical containment in the configured root;
7. reject unsupported file types and hard-linked regular files;
8. perform operation-specific identity checks and bounded I/O;
9. return project-relative metadata only.

Rejected lexical forms include POSIX absolute paths, Windows drive/UNC paths, `..`, control/NUL characters, NTFS ADS/colon syntax, reserved DOS device names, and components ending in a Windows-ambiguous space or period.

## Link, hard-link, and race policy

The bridge does not support symlink traversal, even when a link target remains inside an authorized root. Redirecting Windows name-surrogate reparse points and nested mount points are denied. Regular files with more than one hard link are denied for protected reads.

`PathGuard.read_bounded` captures non-following file identity, opens read-only with `O_NOFOLLOW` where available, compares descriptor identity, revalidates the pathname, and only then reads bounded content. Directory enumeration verifies identity around enumeration. Recursive search reauthorizes each entry at point of use.

Application-level path policy cannot eliminate every race against another process with equivalent or greater local OS privileges. Local account isolation remains part of the security boundary.

## Sensitive-path defense in depth

Read permission does not expose every file in a root. Common secret-bearing locations/formats remain denied, including `.env`, `.git`, SSH/cloud credential directories, credential/token directories, private-key formats, Terraform state, and common service-account files. Templates such as `.env.example` remain readable.

This is defense in depth, not secret discovery. Operators should keep real secrets outside authorized roots whenever practical.

## Controlled execution authorization

Both `run_process(...)` and `start_job(...)` ultimately execute through the same `ExecutionService` policy. There is intentionally no `shell(command)` tool and the caller never supplies a host executable path.

An execution request must pass all of the following gates:

1. the project ID exists;
2. `permissions.execute` is true;
3. the requested executable alias is configured for that project;
4. the alias/target is not a known shell executable;
5. argv satisfies hard count/size/control-character limits;
6. `timeout_seconds` is within the project's configured maximum;
7. `cwd` is a project-relative directory that passes `PathGuard` confinement;
8. the configured executable resolves to a safe regular executable file;
9. executable identity is rechecked immediately before process creation;
10. the process is started directly with an argv vector and a minimal environment.

In the configured Phase 8 runtime there is an additional outer gate: the fixed-schema persistent audit sink must accept the pre-operation `attempt` event before `run_process` or `start_job` reaches the execution/job service.

Policy failures are returned as MCP errors without exposing configured host paths.

## Executable allowlist and no-shell policy

Unpinned aliases such as `python` or `pytest` are resolved through a constrained `PATH`. Empty/relative entries, redirecting PATH directories, unavailable entries, and PATH directories inside the authorized project root are removed.

Pinned aliases can point to an absolute executable path in ignored local configuration. Only the alias is exposed to MCP clients. The target is canonicalized and validated locally, then rechecked immediately before launch.

Processes are created with `asyncio.create_subprocess_exec`, not a shell parser. Known shell targets such as `cmd.exe`, PowerShell, `sh`, and `bash` are rejected even when placed behind an alias. Shell metacharacters in an ordinary argv entry remain argument data.

Portable subprocess APIs do not provide the same cross-platform descriptor-based execution primitive used for file reads. Executable replacement by a concurrently privileged local actor therefore remains a residual race. An allowlisted programmable executable is also not a sandbox: Python, Node, compilers, package managers, test runners, or build systems can execute project-controlled code with the bridge account's privileges.

## Working-directory, environment, and resource policy

The launch `cwd` is always project-relative and must pass `PathGuard`. Traversal, symlink, junction, reparse, nested-mount, and other unsafe directory paths are rejected before process creation.

Once started, a child is not restricted by `PathGuard`. It may use ordinary OS APIs to access resources available to the bridge account. `cwd` confinement controls where the bridge starts the process; it is not a child filesystem sandbox.

The child does not inherit the full bridge environment. It receives a small set of platform/runtime variables plus a constrained `PATH`; arbitrary MCP environment overrides are not supported.

Execution is bounded by hard and configurable limits for argv, runtime, captured stdout/stderr, and per-project concurrency. stdin is `DEVNULL`. Output-budget exhaustion and timeout terminate the invocation and produce explicit structured results.

Current application ceilings include a 300-second timeout, 1 MiB combined captured output, four concurrent one-shot processes per project, 64 arguments, 4096 characters per argument, and 16384 combined argument characters.

## Managed-job boundary

`start_job(...)` validates the request before allocating an opaque 128-bit job ID. The manager records only safe metadata such as project ID, executable alias, relative cwd, timestamps, status, exit metadata, argument count, and sanitized output.

Raw argv is deliberately never persisted. Arguments may contain credentials, URLs with tokens, private paths, or other sensitive data and are needed only by the live supervised task.

Job lookup errors are generic and do not reveal whether a differently shaped ID exists. Job IDs are opaque identifiers, not authorization credentials; access still occurs through the locally authorized MCP endpoint.

The configured runtime uses `runtime/jobs/` by default; `LOCAL_MCP_BRIDGE_JOB_STATE_DIR` may select another absolute local path. Runtime state is ignored by Git and must be treated as locally sensitive.

State-directory components and state files are checked for unsafe redirection. State records are written using exclusive temporary files, flush/fsync, and atomic replacement. Recovery rejects redirecting, hard-linked, non-regular, empty, oversized, malformed, or schema-invalid records. Actual persisted-record reads reuse `PathGuard.read_bounded`.

Recovery is a fresh authorization decision: a record is discarded unless the current registry still contains the project, `execute` remains enabled, and the executable alias remains allowlisted. Recovered output/error text is sanitized and project-root-redacted again.

Persisted `starting`, `running`, or `cancelling` records become `interrupted` after restart. The bridge intentionally does not persist a PID and later reattach to it because PID reuse could target an unrelated process.

## Cancellation and process-tree boundary

`cancel_job` cancels the supervised async task. In the configured Phase 8 runtime, cancellation first requires a successful persistent audit attempt record.

On POSIX, launches use a new session and termination targets the process group. On Windows, Python's portable kill primitive guarantees the direct child but does not guarantee recursive descendant termination. The bridge does not claim Windows Job Object or kernel-level process-tree containment.

## Job output and persistence limits

Process output is untrusted. Capture is byte-bounded before decoding; unsupported terminal/control characters are escaped; direct occurrences of the configured project-root string are redacted. Persisted output is reprocessed during recovery.

Redaction is defense in depth. A malicious child can encode or transform host data in arbitrary ways and cannot be made non-exfiltrating through generic string replacement.

Manager-level ceilings include:

- 32 active managed jobs globally;
- 512 retained terminal records maximum, 128 by default;
- 100 jobs per list response;
- 131072 characters per output page;
- 8 MiB per persisted state file;
- 1024 candidate state files examined at startup;
- 64 MiB of candidate bytes attempted during startup recovery;
- 4 MiB combined recovered stdout/stderr characters per admitted record.

These limits control bridge state growth and startup work. They do not impose CPU, RAM, GPU, disk-write, or network quotas on executed code.

## Git authorization boundary

Git synchronization uses a dedicated `GitService`; it is not implemented as caller-controlled generic process execution. The MCP caller supplies only a logical `project_id`. Remote name, branch, HTTPS URL, timeout, and output ceiling come from a separate ignored local policy overlay.

The public Git surface is intentionally limited to:

- `git_status(project_id)`;
- `git_fetch(project_id)`;
- `git_sync_fast_forward(project_id)`.

There is no push, commit, reset, clean, checkout/switch, rebase, cherry-pick, branch/tag mutation, force operation, arbitrary refspec, arbitrary URL, or arbitrary Git argv.

If generic execution is enabled for the same project, a `git`/`git.exe` executable rule is rejected by runtime policy. This prevents the bridge from intentionally publishing a generic Git bypass alongside the narrow synchronization API.

## Git policy overlay, transport, and repository config

Git remains disabled unless the ignored local Git overlay contains an entry for the project. The trusted policy requires a conservative remote name and branch, an HTTPS URL without embedded credentials/query/fragment, and bounded timeout/output values. The configured URL must exactly match the repository's existing `remote.<name>.url`.

Git is launched without a shell, from a constrained absolute `PATH` outside the project root. System/global Git config, interactive prompting, credential helpers, AskPass, pagers, submodule recursion, automatic GC, and automatic maintenance are disabled or neutralized. Allowed transport is restricted to HTTPS.

Repository-local Git config is untrusted. Before an exposed Git operation, the bridge rejects aliases, credential settings, filters, hooks, includes/includeIf, submodules, URL rewrites, merge drivers, HTTP/protocol overrides, SSH commands, external diff/filter commands, alternate-ref commands, and unsafe remote overrides.

The repository layout is also constrained. `.git` must be a real non-redirecting directory; critical metadata/ref components are checked; hard-linked protected metadata and external object alternates/common-directory indirection are rejected; and `git rev-parse --show-toplevel` must resolve exactly to the configured project root.

## Git status, fetch, and synchronization policy

`git_status` does not return changed filenames or local host paths. It exposes only branch/HEAD metadata, clean/dirty state, staged/unstaged/untracked counts, remote-tracking presence, and ahead/behind counts.

`git_fetch` uses a fixed refspec from the configured branch to its configured remote-tracking ref. Tags are not fetched, submodules are not recursively fetched, and `FETCH_HEAD` is not written. The caller cannot select another source/destination ref.

`git_sync_fast_forward` requires the configured branch, a clean working tree, a trusted fetch, a second repository/branch/cleanliness validation, proof that local `HEAD` is an ancestor of the fetched target, `merge --ff-only --no-overwrite-ignore`, and final verification that `HEAD` equals the previously fetched object ID.

Divergence, local-ahead history, dirty state, detached/wrong branch, or any verification failure stops synchronization. The bridge does not resolve such states with reset, stash, merge commits, rebase, clean, or history rewriting.

In the configured Phase 8 runtime, both `git_fetch` and `git_sync_fast_forward` require a successfully persisted audit attempt before network/ref/worktree effects are allowed. `git_status` is read-only and uses non-strict completion auditing.

Every Git subprocess uses a bounded timeout and combined stdout/stderr capture ceiling. Git operations are serialized per project with fail-fast behavior.

## Phase 8 audit authorization boundary

The configured runtime enables a local `AuditLogger` under `runtime/audit/` by default. `LOCAL_MCP_BRIDGE_AUDIT_DIR` may select another **absolute** local directory. An invalid explicit override or unsafe audit path aborts configured runtime creation rather than silently disabling audit persistence.

The reusable server factory constructs a disabled, non-persistent logger unless one is explicitly injected. This keeps library/tests hermetic and means importing `local_mcp_bridge.server` never consults audit environment variables or creates local audit state.

### Fixed metadata schema

Audit events are JSONL records containing only:

- schema version;
- UTC timestamp;
- opaque session and event identifiers;
- session-local sequence number;
- bounded action/outcome labels;
- optional project ID satisfying the registry project-ID grammar;
- allowlisted integers/booleans/`null` values and a few fixed enums.

There is deliberately no general string-dictionary or exception field. The audit schema cannot accept raw argv, process output, search queries, caller file paths, file contents, executable targets, Git URLs, environment values, credentials, tokens, headers, or arbitrary exception text.

This is a security property, not just a logging convention: introducing arbitrary caller-controlled strings into the schema requires explicit review.

### Audit file boundary

The audit directory is prepared component-by-component. Redirecting symlink/name-surrogate reparse components and non-directories are rejected. Existing audit files must be regular, non-redirecting, single-link objects.

For a new active log, creation is exclusive. For an existing log, file identity is captured before open and compared with descriptor identity. `O_NOFOLLOW` is used where available. On POSIX the directory/file modes are tightened to `0700`/`0600`. Successful event writes are bounded and fsynced.

Application-level checks do not make the log tamper-proof against a local administrator or same-privilege actor. Such an actor can still delete, replace, or race state outside the guarantees of the bridge process.

### Fail-closed sensitive operations

Configured-runtime high-impact operations require a successful persistent `attempt` event before effect:

- `run_process`;
- `start_job`;
- `cancel_job`;
- `git_fetch`;
- `git_sync_fast_forward`.

If the audit sink cannot accept that event, the operation is refused before reaching its underlying service.

Completion events are deliberately non-transactional. If an OS/Git effect has already occurred and the completion event later fails, returning an artificial failure cannot undo that effect. Instead, the logger becomes unhealthy. `health_check()` then reports `status="degraded"` and `audit_healthy=false`, and subsequent sensitive attempts fail closed until audit persistence succeeds again.

Read-only/inspection operations do not fail solely because their audit completion write fails. This prevents an audit-disk problem from converting safe reads into misleading operation failures while still surfacing degraded health.

### Audit resource and MCP exposure policy

The active log defaults to 4 MiB and five total retained files. Hard ceilings are 64 MiB active-file size, 16 retained files, 4096 encoded bytes per event, and 16 detail fields.

Rotation validates existing archive targets before mutation. Unsafe local archive objects cause audit persistence to fail rather than being silently followed.

There is no `read_audit_log` MCP tool. Raw audit records are local operational state, not model context. Only `audit_enabled` and `audit_healthy` are exposed through health metadata.

## Runtime composition and isolation

Configured runtime startup follows:

```text
base config
    -> optional Git overlay
    -> hardened persistent AuditLogger
    -> runtime.bootstrap attempt
    -> ExecutionService
    -> persistent JobManager
    -> MCP server
    -> runtime.bootstrap success
```

The pure server factory does not perform any of those machine-local reads/writes implicitly.

## Security boundary and residual risk

Phases 5 through 8 are controlled application-level capabilities, not an operating-system sandbox or tamper-proof forensic platform. Granting `execute: true` authorizes selected programs to run under the bridge account, and enabling Git synchronization authorizes the bridge to contact the configured HTTPS remote and fast-forward a clean authorized working tree.

The bridge mitigates unapproved executable selection, shell-string injection, unsafe launch cwd, accidental full-environment inheritance, excessive argv/output/runtime, unbounded bridge-managed state, stale persisted authorization, unsafe PID reattachment, arbitrary Git command selection, Git transport redirection, destructive/non-fast-forward synchronization, accidental sensitive audit payloads, unsafe audit-path redirection, and unaudited high-impact operations when the configured audit sink is unavailable.

It does not contain intentionally hostile native/interpreted code or a compromised Git binary, prevent all races by an equally privileged local actor, provide a network sandbox, cryptographically sign audit records, send logs to a trusted remote sink, or prevent a local administrator from deleting local runtime state.

## Network exposure

Development transport remains local stdio. Phase 9 remote access must use authenticated encrypted transport while retaining all local project/path/execution/job/Git/audit checks. Authentication must not become a substitute for local capability authorization.

Direct filesystem write/delete/rename MCP primitives remain separate and require their own reviewed policy and recovery semantics before introduction.
