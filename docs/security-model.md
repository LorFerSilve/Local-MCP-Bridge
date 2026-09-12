# Security Model

## Trust model

The bridge assumes model-generated calls, repository content, MCP input, filenames, paths, command arguments, child-process output, persisted job state, Git metadata, and repository-local Git configuration may be malicious or incorrect. Deterministic local policy is the authorization boundary; security never depends on the model behaving correctly.

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

Policy failures are returned as MCP errors without exposing configured host paths.

## Executable allowlist policy

Unpinned aliases such as `python` or `pytest` are resolved through a constrained `PATH`. Empty/relative entries, redirecting PATH directories, unavailable entries, and PATH directories inside the authorized project root are removed.

Pinned aliases can point to an absolute executable path in ignored local configuration. Only the alias is exposed to MCP clients. The target is canonicalized and validated locally, then rechecked immediately before launch.

Portable subprocess APIs do not provide the same cross-platform descriptor-based execution primitive used for file reads. Executable replacement by a concurrently privileged local actor therefore remains a residual race; pinned targets and immediate identity checks reduce but do not formally eliminate it.

## No-shell guarantee

Processes are created with `asyncio.create_subprocess_exec`, not a shell parser. Known shell targets such as `cmd.exe`, PowerShell, `sh`, and `bash` are rejected even when placed behind an alias.

Shell metacharacters in an ordinary argv entry remain argument data. This does not make interpreters equivalent to a sandbox: if Python, Node, a compiler, test runner, package manager, or build system is allowlisted, it can execute code with the bridge account's operating-system privileges.

## Working-directory policy

The launch `cwd` is always project-relative and must pass `PathGuard`. Traversal, symlink, junction, reparse, nested-mount, and other unsafe directory paths are rejected before process creation.

Once started, a child is not restricted by `PathGuard`. It may use ordinary OS APIs to access resources available to the bridge account. `cwd` confinement controls where the bridge starts the process; it is not a child filesystem sandbox.

## Child environment and resource policy

The child does not inherit the full bridge environment. It receives a small set of platform/runtime variables plus a constrained `PATH`; arbitrary MCP environment overrides are not supported.

Execution is bounded by hard and configurable limits for argv, runtime, captured stdout/stderr, and per-project concurrency. stdin is `DEVNULL`. Output-budget exhaustion and timeout terminate the invocation and produce explicit structured results.

Current application ceilings include a 300-second timeout, 1 MiB combined captured output, four concurrent one-shot processes per project, 64 arguments, 4096 characters per argument, and 16384 combined argument characters.

## Phase 6 managed-job boundary

`start_job(...)` validates the request before allocating an opaque 128-bit job ID. The manager records only safe metadata such as project ID, executable alias, relative cwd, timestamps, status, exit metadata, argument count, and sanitized output.

Raw argv is deliberately never persisted. Arguments may contain credentials, URLs with tokens, private paths, or other sensitive data and are needed only by the live supervised task.

Job lookup errors are generic and do not reveal whether a differently shaped ID exists. Job IDs are opaque identifiers, not authorization credentials; access still occurs through the locally authorized MCP endpoint.

## Persistent job-state boundary

The configured runtime uses `runtime/jobs/` by default; `LOCAL_MCP_BRIDGE_JOB_STATE_DIR` may select another absolute local path. Runtime state is ignored by Git and must be treated as locally sensitive.

The manager creates/checks state-directory components without intentionally following redirecting links and rejects symlink/junction/name-surrogate reparse components. On POSIX it applies restrictive file/directory modes as defense in depth. This does not replace OS account isolation or platform ACL policy, and application-level path checks cannot eliminate every race against a concurrently privileged local process.

State records are written using exclusive temporary files, flush/fsync, and atomic replacement. Recovery rejects redirecting, hard-linked, non-regular, empty, oversized, malformed, or schema-invalid records.

Actual persisted-record reads reuse `PathGuard.read_bounded`, including file identity checks around the open. Startup recovery is bounded by retained-history limits, scan count, per-file size, total attempted recovery bytes, and recovered-output size.

## Recovery is reauthorization

Persisted state is never trusted merely because the bridge wrote it previously. On restart a recovered job is admitted only if:

- its project still exists in the current registry;
- `execute` is still enabled for that project;
- its executable alias is still allowlisted;
- its relative cwd and record fields are syntactically valid;
- its status, timestamps, sizes, and optional fields satisfy the current schema.

Recovered stdout/stderr/error text is sanitized and project-root-redacted again before it becomes MCP-visible. This prevents a modified state file from bypassing the normal output treatment and prevents a revoked project from regaining historical job visibility simply because state remains on disk.

## Restart semantics

Terminal status and bounded terminal output can survive bridge restart. Persisted `starting`, `running`, or `cancelling` records are conservatively converted to `interrupted` with `bridge_restart` as the termination reason.

Phase 6 intentionally does not persist a PID and later reattach to it. PIDs are reusable identifiers; blindly reconnecting to or killing a recovered PID could target an unrelated process. If the bridge crashes, an OS child may survive independently depending on platform and failure mode. The recovered record does not claim resumed supervision.

## Cancellation and process-tree boundary

`cancel_job` cancels the supervised async task. That cancellation enters `ExecutionService`, which terminates the launched process using the same policy as timeout/output-limit handling.

On POSIX, launches use a new session and termination targets the process group. On Windows, Python's portable kill primitive guarantees the direct child but does not guarantee recursive descendant termination. Phase 6 therefore does not claim Windows Job Object or kernel-level process-tree containment.

## Job output policy

Process output is untrusted. Capture is byte-bounded before decoding; unsupported terminal/control characters are escaped; direct occurrences of the configured project-root string are redacted. Persisted output is reprocessed during recovery.

The current manager persists terminal capture rather than promising a live durable stream. A running job can therefore have no durable output page until its underlying invocation completes.

Redaction is defense in depth. A malicious child can encode or transform host data in arbitrary ways and cannot be made non-exfiltrating through generic string replacement.

## Job-manager resource limits

Manager-level safety ceilings include:

- 32 active managed jobs globally;
- 512 retained terminal records maximum, 128 by default;
- 100 jobs per list response;
- 131072 characters per output page;
- 8 MiB per persisted state file;
- 1024 candidate state files examined at startup;
- 64 MiB of candidate bytes attempted during startup recovery;
- 4 MiB combined recovered stdout/stderr characters per admitted record.

These limits control bridge state growth and startup work. They do not impose CPU, RAM, GPU, disk-write, or network quotas on executed code.

## Phase 7 Git authorization boundary

Git synchronization uses a dedicated `GitService`; it is not implemented as caller-controlled generic process execution. The MCP caller supplies only a logical `project_id`. Remote name, branch, HTTPS URL, timeout, and output ceiling come from a separate ignored local policy overlay.

The public Phase 7 surface is intentionally limited to:

- `git_status(project_id)`;
- `git_fetch(project_id)`;
- `git_sync_fast_forward(project_id)`.

There is no Phase 7 push, commit, reset, clean, checkout/switch, rebase, cherry-pick, branch/tag mutation, force operation, arbitrary refspec, arbitrary URL, or arbitrary Git argv.

If generic execution is enabled for the same project, a `git`/`git.exe` executable rule is rejected by the Phase 7 runtime policy. This prevents the bridge from intentionally publishing a generic Git bypass alongside the narrow synchronization API.

## Git policy overlay and transport

Git remains disabled unless the ignored local Git overlay contains an entry for the project. The trusted policy requires a conservative remote name and branch, an HTTPS URL without embedded credentials/query/fragment, and bounded timeout/output values.

The configured URL must exactly match the repository's existing `remote.<name>.url`. The MCP request cannot replace the URL or branch.

Git is launched without a shell, from a constrained absolute `PATH` outside the project root. System/global Git config, interactive prompting, credential helpers, AskPass, pagers, submodule recursion, automatic GC, and automatic maintenance are disabled or neutralized. Allowed transport is restricted to HTTPS.

Line-ending behavior is deterministic for the bridge invocation: Phase 7 uses Windows-style `core.autocrlf=true` on Windows and `core.autocrlf=false` on POSIX rather than inheriting global/system Git settings that the security boundary intentionally disables.

## Repository-local Git configuration

Repository-local Git config is untrusted input. Before an exposed Git operation, Phase 7 validates the repository and rejects configuration namespaces/settings that can introduce command execution, external helpers, transport redirection, or policy bypass. This includes aliases, credential settings, filters, hooks, includes/includeIf, submodules, URL rewrites, merge drivers, HTTP/protocol overrides, SSH commands, external diff/filter commands, alternate-ref commands, and remote proxy/upload-pack/receive-pack overrides.

The repository layout is also constrained. Phase 7 currently requires `.git` to be a real non-redirecting directory. Critical metadata files/directories are checked, hard-linked protected metadata is rejected, external object alternates are rejected, and `git rev-parse --show-toplevel` must resolve exactly to the configured project root.

These checks are application-level defenses. A local actor with equivalent or greater OS privileges can still race repository metadata between checks.

## Git status privacy

`git_status` does not return changed filenames or local host paths. It exposes only branch/HEAD metadata, clean/dirty state, staged/unstaged/untracked counts, remote-tracking presence, and ahead/behind counts.

This prevents routine status calls from reflecting attacker-controlled path strings back into the high-level MCP surface.

## Fetch and synchronization policy

`git_fetch` uses a fixed refspec from the configured branch to its configured remote-tracking ref. Tags are not fetched, submodules are not recursively fetched, and `FETCH_HEAD` is not written. The caller cannot select another source/destination ref.

`git_sync_fast_forward` is more restrictive than a normal pull:

1. validate repository metadata and local Git config;
2. require the configured branch to be checked out;
3. require staged, unstaged, and untracked counts to all be zero;
4. fetch the configured branch into the configured remote-tracking ref;
5. recheck branch and cleanliness;
6. prove local `HEAD` is an ancestor of the fetched remote head;
7. run `merge --ff-only` against that verified remote-tracking ref;
8. verify resulting `HEAD` equals the fetched object ID.

Divergence, local-ahead history, dirty state, detached/wrong branch, or any verification failure stops synchronization. Phase 7 does not resolve such states with reset, stash, merge commits, rebase, clean, or history rewriting.

A successful fast-forward intentionally writes files inside the authorized working tree. It does not authorize remote mutation.

## Git resource and concurrency limits

Every Git subprocess uses a bounded timeout and combined stdout/stderr capture ceiling. Git operations are serialized per project with fail-fast behavior: if another Git operation is already active for that project, the new request is rejected rather than queued without a bound.

Git subprocess output is not returned directly to the caller; the service parses only the narrow metadata required for the structured MCP result.

## Security boundary and residual risk

Phases 5 through 7 are controlled application-level capabilities, not an operating-system sandbox. Granting `execute: true` authorizes selected programs to run under the bridge account, and enabling Git synchronization authorizes the bridge to read Git metadata, contact the configured HTTPS remote, update the configured remote-tracking ref, and fast-forward a clean authorized working tree.

The bridge mitigates unapproved executable selection, shell-string injection, unsafe launch cwd, accidental full-environment inheritance, excessive argv/output/runtime, unbounded bridge-managed concurrency/state, stale persisted authorization, unsafe PID reattachment, arbitrary Git command selection, Git transport redirection, and destructive/non-fast-forward synchronization.

It does not contain intentionally hostile native/interpreted code or a compromised Git binary, prevent all races by an equally privileged local actor, provide a network sandbox, or prove every Git object/parser path safe against an already-compromised host.

## Audit and network exposure

Broader audit logging and runtime hardening belong to Phase 8. Development transport remains local stdio; future remote access must use authenticated encrypted transport while retaining all local authorization checks.

Direct filesystem write/delete/rename MCP primitives remain separate and require their own reviewed policy and recovery semantics before introduction.
