# Threat Model

## Assets to protect

- files outside explicitly authorized project roots;
- credentials, tokens, private keys, cookies, environment secrets, and local authentication material;
- integrity of authorized repositories and the host operating system;
- CPU, RAM, GPU, disk, and network availability;
- private benchmark/runtime/job data;
- Git history, trusted remote policy, and local ignored/untracked data;
- availability and integrity of the local Phase 8 audit trail;
- separation between operational audit metadata and MCP/model context.

## Threats and mitigations

### Prompt injection through repository, process, or fetched content

**Scenario:** Source, documentation, generated files, logs, process output, recovered job output, or fetched repository content instructs the model to escape policy.

**Mitigation:** Content/output is data, not policy. Local project, permission, path, executable, argument, environment, timeout, output, persistence, Git, audit, and resource controls apply independently of model reasoning. Recovered output is sanitized again before exposure. Raw audit logs are not exposed as an MCP tool.

### Lexical path traversal

**Scenario:** A caller uses `..`, an absolute/UNC/drive path, ADS syntax, device names, or mixed separators to escape a project root.

**Mitigation:** Filesystem callers and process/job working directories use project-relative paths only. Unsafe lexical forms are rejected before use and effective paths must resolve under the canonical project root.

### Symlink, junction, reparse, mount, or hard-link escape

**Scenario:** A path component inside a project or local runtime-state path redirects to another location, or a hard-linked protected file aliases another file object.

**Mitigation:** `PathGuard` rejects symlinks, redirecting Windows reparse points/junctions, nested mounts, and hard-linked protected regular files. Job-state and Phase 8 audit-state paths have their own non-following component/file validation. Audit files must be single-link regular files.

### File replacement / TOCTOU

**Scenario:** A local process replaces a checked project, job-state, executable, Git metadata, or audit file between authorization and use.

**Mitigation:** Protected file reads capture non-following file identity, open, compare descriptor identity, and revalidate paths. Executables are rechecked immediately before process launch. Existing audit files are identity-checked around open, new audit files use exclusive creation, and `O_NOFOLLOW` is used where available. Git repository state is validated before operations and revalidated after fetch where mutation is possible.

**Residual risk:** Application-level checks cannot eliminate every race against another process with equivalent or greater OS privileges. Local account/host isolation remains part of the security boundary.

### Secret exfiltration from an authorized root

**Scenario:** The source tree contains `.env`, cloud/SSH credentials, keys, Terraform state, token files, or other secrets.

**Mitigation:** Read/search tools apply a defense-in-depth sensitive-path deny policy. Operators should still keep real secrets outside authorized roots.

**Residual execution risk:** Child processes are not subject to the read-tool sensitive-path filter. Trusted executable/project code can use ordinary OS APIs to read anything available to the bridge account.

### Shell command injection

**Scenario:** Model-controlled arguments contain `;`, `&&`, pipes, redirects, quotes, or other shell syntax intended to execute additional commands.

**Mitigation:** There is no `shell(command)` primitive. `run_process` and `start_job` use an executable alias plus argv and launch with `asyncio.create_subprocess_exec`. Known shell executables are rejected. Ordinary shell metacharacters remain argument data.

**Residual risk:** An allowlisted programmable executable can evaluate code or spawn other processes. That is an explicit high-trust execution grant, not shell confinement.

### Arbitrary executable selection or PATH substitution

**Scenario:** A caller supplies an arbitrary host executable path, or an allowlisted command name resolves to attacker-controlled code earlier in PATH.

**Mitigation:** MCP callers supply only aliases. Pinned absolute targets can only be defined in local configuration. Unpinned lookup uses a constrained PATH excluding empty/relative, redirecting, unavailable, and project-root entries. The resolved target is validated and identity is rechecked immediately before launch.

**Residual risk:** Portable subprocess APIs do not provide a fully descriptor-based cross-platform exec primitive. A concurrently privileged local actor may still win the final replacement race.

### Working-directory escape

**Scenario:** A process/job starts outside its project through absolute path, traversal, symlink, mount, or junction.

**Mitigation:** `cwd` is normalized as project-relative and authorized through `PathGuard` before process creation and before a managed job ID is allocated.

**Residual risk:** The launch directory is not a child-process sandbox. Once running, the process can change directory and open paths available to the bridge OS user.

### Environment-secret inheritance

**Scenario:** The bridge process has API keys/tokens in environment variables and an executed tool inherits or prints them.

**Mitigation:** Child execution receives a deliberately small environment plus constrained PATH. Arbitrary MCP environment overrides are not supported.

**Residual risk:** Secrets available through files, credential agents, local services, key stores, or other host mechanisms remain accessible to sufficiently privileged child code.

### Process output injection or exfiltration

**Scenario:** A child emits ANSI/control sequences, prompt injection, huge output, or sensitive host data.

**Mitigation:** Capture is byte-bounded before decoding. Unsupported control characters are escaped and direct occurrences of the configured project-root string are redacted. Recovered output is sanitized/redacted again.

**Residual risk:** A malicious child can encode or transform sensitive information in ways generic redaction cannot recognize. Persisted output can therefore still be sensitive local data.

### Process-output, runtime, or concurrency exhaustion

**Scenario:** A child emits indefinitely, hangs waiting for input, or the caller starts too many expensive tasks.

**Mitigation:** Output capture, runtime, argv, per-project execution concurrency, and global managed-job count are bounded. stdin is `DEVNULL`. Excess work fails rather than building an unbounded queue.

**Residual risk:** Child code can spawn descendants, threads, GPU work, network traffic, or disk-heavy workloads. Application-level counts are not OS resource quotas.

### Job-state disk or startup exhaustion

**Scenario:** A state directory contains huge numbers of files, large records, or malformed records intended to consume startup time/memory.

**Mitigation:** Recovery caps directory entries examined, per-file size, retained-history count, total candidate bytes attempted, and recovered-output size. Malformed records are ignored rather than trusted.

### Persisted-state tampering and stale authorization

**Scenario:** A local actor edits a `.job.json` record to forge status/output/path information, or authorization is revoked while historical state remains.

**Mitigation:** Persisted state is untrusted. Recovery verifies file/link behavior, identity, schema, IDs, current project authorization, executable alias, cwd syntax, timestamps/status/sizes, output types, and optional fields. Recovered text is sanitized/root-redacted again. Revoked projects/executables are not re-admitted.

### Secret persistence through argv

**Scenario:** A command argument contains a token, password, signed URL, private path, or other secret and is written into durable job state.

**Mitigation:** Raw argv is never persisted. Only argument count is retained. Live argv exists only for the supervised task that needs it.

### Guessing or manipulating job IDs

**Scenario:** A caller uses malformed IDs or guesses IDs to probe job inventory.

**Mitigation:** Jobs use opaque UUID-derived 128-bit IDs and malformed/unknown IDs receive a generic failure. IDs are not the authorization boundary.

### Bridge restart while a job is active / PID reuse

**Scenario:** The bridge restarts with durable state saying a process was active, or attempts to reattach to a reused PID.

**Mitigation:** Recovered nonterminal states become `interrupted` with a restart reason. PIDs are not persisted/reused for reattachment.

**Residual risk:** If the bridge itself crashes or is forcibly killed, an OS child may survive as an orphan depending on platform/process topology.

### Descendant process survives cancellation or timeout

**Scenario:** A child launches descendants that outlive cancellation/timeout.

**Mitigation:** POSIX launches use a new session and process-group termination. Windows kills the direct child and uses a new process group.

**Residual risk:** Portable Python APIs do not guarantee recursive Windows descendant termination. Phase 8 does not claim Windows Job Object or kernel-level process-tree containment.

### Process mutates project/host state

**Scenario:** An allowlisted program modifies source, deletes files, installs packages, changes config, or writes elsewhere on the host.

**Mitigation:** Execution is denied by default and requires explicit per-project permission plus executable allowlist. The configured Phase 8 runtime also requires a durable audit-attempt record before execution begins.

**Residual risk:** This is inherent to application-level execution. Allowed executable/project code has the OS privileges of the bridge account.

### Runtime state is accidentally published

**Scenario:** Persisted job output or audit records contain private operational data and are committed to the public repository.

**Mitigation:** `runtime/`, `jobs/`, logs, outputs, local config, credentials, Git-policy overlays, and related state are ignored by Git and prohibited by repository security policy. Security-baseline CI checks common sensitive tracked paths.

### Arbitrary or destructive Git operation

**Scenario:** An agent resets, cleans, rebases, force-pushes, deletes refs, supplies arbitrary URLs/refspecs, or rewrites history.

**Mitigation:** The Git surface exposes only `git_status`, `git_fetch`, and `git_sync_fast_forward`. There is no generic Git argv surface, push, reset, clean, checkout/switch, rebase, cherry-pick, force operation, arbitrary URL/refspec, or history-rewrite primitive.

### Generic process execution bypasses Git policy

**Scenario:** A project has `execute: true` and an MCP caller invokes an allowlisted generic `git` executable to escape the dedicated API.

**Mitigation:** Runtime policy rejects a project configuration that exposes `git`/`git.exe` through generic process execution while the dedicated Git boundary is in use.

**Residual risk:** An allowlisted interpreter or other trusted executable can itself invoke Git. Generic execution is already a high-trust capability and not an OS sandbox.

### Git remote redirection or credential injection

**Scenario:** Repository-local config rewrites the trusted URL, invokes a credential helper, changes transport behavior, or causes Git to contact a different endpoint.

**Mitigation:** Remote URL/branch/name come from a separate ignored local policy overlay. URLs are HTTPS-only without embedded credentials/query/fragment and must exactly match the configured repository remote. System/global config, interactive prompting, credential helpers, AskPass, and non-HTTPS protocols are disabled. Dangerous repository-local config namespaces are rejected.

### Repository-local Git config executes code

**Scenario:** A malicious repository configures hooks, filters, external helpers, aliases, or includes so status/fetch/sync executes commands.

**Mitigation:** Repository-local config is validated before exposed Git operations; fixed command-line hardening is applied; hooks and interactive helpers are disabled; only fixed shell-free Git argv shapes are invoked.

**Residual risk:** Git itself remains complex native software. The bridge does not sandbox a compromised Git binary or unknown parser vulnerability.

### Git metadata redirection or alternate object store

**Scenario:** `.git`, critical metadata, refs, alternates, or common-directory indirection redirect Git outside the authorized repository.

**Mitigation:** `.git` must be a real directory; critical metadata/ref components are validated; unsafe redirection/hard-link forms and external object alternates/common-directory indirection are rejected; `git rev-parse --show-toplevel` must exactly match the authorized root.

### Dirty worktree or ignored local data loss

**Scenario:** Synchronization overwrites staged/unstaged/untracked work or an ignored local file that becomes tracked remotely.

**Mitigation:** Sync requires staged, unstaged, and untracked counts all zero before fetch and rechecks after fetch. `merge --ff-only --no-overwrite-ignore` refuses obstructing ignored local files. The bridge does not auto-stash/reset/clean.

### Divergent or local-ahead Git history

**Scenario:** Local history contains commits not in the remote and an automated sync rewinds or merges them.

**Mitigation:** The bridge proves `HEAD` is an ancestor of the fetched remote head. Divergence/local-ahead states fail closed. No reset, rebase, merge commit, or force operation is attempted.

### Remote/repository changes during synchronization

**Scenario:** The remote or another local process changes state while synchronization is in progress.

**Mitigation:** The fetched object ID is treated as the target for the current operation; repository/config/branch/cleanliness are revalidated after fetch; ancestry is proved; final HEAD must equal the fetched target. Per-project Git operations inside one bridge are serialized.

**Residual risk:** Locks are process-local. Another bridge instance or same-privilege local process can still race state.

### Fetched code later executes

**Scenario:** A trusted configured remote is compromised or intentionally contains malicious code; a fast-forward imports it and a later execution request runs it.

**Mitigation:** Git synchronization and execution remain separate capabilities. Execution still requires `execute: true`, an executable allowlist, and Phase 8 pre-operation audit availability.

**Residual risk:** Enabling both synchronization and execution is a trust decision. A trusted remote policy is not a code-safety proof.

### Git network or output resource exhaustion

**Scenario:** A remote stalls, emits excessive diagnostics, or triggers expensive Git work.

**Mitigation:** Git operations have bounded timeout and combined stdout/stderr capture, and only one bridge-managed Git operation per project is active at a time.

**Residual risk:** Application-level limits do not cap every network byte, disk write, CPU cycle, or allocation inside Git.

## Phase 8 audit-specific threats

### Sensitive data accidentally enters the audit log

**Scenario:** Generic logging records raw argv, a search query, file path/content, child output, Git URL, credential, environment value, or exception string.

**Mitigation:** `AuditLogger` uses a fixed schema rather than accepting an arbitrary logging dictionary. Detail keys are allowlisted and accept only bounded integers/booleans/`null` or a few fixed enums. Project IDs must satisfy the registry grammar. There is no general caller-controlled string, argv, output, query, path, URL, credential, or error field.

**Residual risk:** Project IDs and coarse operation metadata are intentionally retained and may themselves be operationally sensitive. Audit state therefore remains local-only and Git-ignored.

### Audit path redirection or hard-link abuse

**Scenario:** A local actor places a symlink/junction/reparse point or hard-linked file at the audit path so the bridge appends to an unintended target.

**Mitigation:** Audit directories are walked/created component-by-component and reject redirecting components. Existing active/archive files must be regular, non-redirecting, single-link files. New active files are exclusively created; existing files are identity-checked around open; `O_NOFOLLOW` is used where available.

**Residual risk:** A same-privilege local process can still race application-level checks on platforms without stronger kernel handle/path guarantees.

### Audit disk exhaustion

**Scenario:** Repeated MCP activity grows logs without bound and fills local storage.

**Mitigation:** Events and detail counts are bounded. The active audit file rotates at a configured ceiling; retained file count is bounded. Defaults are 4 MiB active file and five total files; hard maxima are 64 MiB and 16 files.

**Residual risk:** Rotation controls bridge-owned audit growth, not overall host disk use by executed code, Git, or unrelated processes.

### Audit failure silently bypasses accountability

**Scenario:** Disk failure, permissions, tampering, or an unsafe audit object prevents recording while the bridge continues executing code or mutating Git state.

**Mitigation:** In the configured runtime, `run_process`, `start_job`, `cancel_job`, `git_fetch`, and `git_sync_fast_forward` require a successful persistent `attempt` event **before** their underlying service is invoked. An unavailable audit sink therefore refuses those operations.

For already-completed effects, completion audit is non-transactional. A completion-write failure marks the logger unhealthy rather than falsely reporting that the prior OS/Git effect did not happen. The next high-impact attempt must again pass the strict audit gate.

### Audit failure turns safe reads into misleading failures

**Scenario:** A read/search/status operation succeeds but its audit completion write fails, and the caller is told the underlying read itself failed.

**Mitigation:** Read-only/inspection completion logging is non-strict. The tool result remains accurate while audit health becomes degraded. `health_check()` exposes `audit_healthy=false`.

### Raw audit log becomes prompt-injection/model context

**Scenario:** Audit records or locally modified audit files are exposed through MCP and become instructions/data for the model.

**Mitigation:** Phase 8 adds no `read_audit_log` tool. Raw audit state is operational local state, not an AI context source. Health exposes only non-sensitive audit enabled/healthy booleans.

### Local audit tampering or deletion

**Scenario:** A local administrator or same-privilege actor edits/deletes audit records to hide activity.

**Mitigation:** File/path validation reduces accidental and model-driven redirection/tampering surfaces, but Phase 8 intentionally does not claim tamper-proof forensics.

**Residual risk:** There is no cryptographic signing key, append-only kernel primitive, remote trusted log sink, or protection from a same/higher-privilege host actor. Local audit is operational accountability, not non-repudiation.

### Runtime startup with unsafe audit state

**Scenario:** The configured audit directory override is relative, redirecting, non-directory, or contains an unsafe existing active log.

**Mitigation:** Explicit audit overrides must be absolute. `AuditLogger` validates/prepares the local path before the configured server is returned. Unsafe persistent audit state fails configured runtime startup rather than silently downgrading to no audit.

### Pure server tests accidentally depend on machine-local audit state

**Scenario:** Importing/reusing the server factory reads `LOCAL_MCP_BRIDGE_AUDIT_DIR` or writes `runtime/audit`, making tests nondeterministic and machine configuration security-relevant to unit imports.

**Mitigation:** Persistent audit wiring lives only in `runtime.py`. `create_mcp_server()` defaults to a disabled in-memory `AuditLogger`. Regression tests set deliberately invalid local runtime/audit/job environment values and verify that importing the pure server factory remains successful.

### Public MCP exposure / compromised remote session

**Scenario:** An unauthenticated endpoint or compromised AI session sends malicious requests.

**Mitigation:** Phase 8 remains local stdio. Phase 9 remote integration must require authenticated encrypted transport and must preserve all local project/path/execution/job/Git/audit gates. Authentication will not replace capability authorization.

## Residual Phase 8 risk

Phases 5 through 8 materially constrain orchestration but do not provide a container, VM, seccomp profile, Windows Job Object sandbox, filesystem namespace, network sandbox, OS-level CPU/RAM/GPU quota, or tamper-proof remote audit service.

Git remains complex native software processing untrusted repository/network data. Allowlisted project code runs with the bridge account's privileges. A local actor with equivalent OS privileges can race or alter local state, another bridge instance can operate concurrently, and a compromised trusted remote can deliver malicious project content.

Phase 8 adds bounded metadata-only operational auditing and fail-closed pre-audit gates for high-impact actions, but it intentionally does not record enough caller data for full request replay and cannot prevent a privileged local actor from deleting local audit records.

Direct filesystem write/delete/rename tools remain intentionally separate. Phase 9 is the remote/tunnel integration boundary and must preserve the Phase 8 local security model.
