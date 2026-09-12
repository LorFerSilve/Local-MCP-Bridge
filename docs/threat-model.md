# Threat Model

## Assets to protect

- files outside explicitly authorized project roots;
- credentials, tokens, private keys, cookies, and environment secrets;
- integrity of local repositories and the host operating system;
- CPU, RAM, GPU, disk, and network availability;
- private benchmark/runtime/job data;
- Git history, trusted remote policy, and local ignored/untracked data.

## Threats and mitigations

### Prompt injection through repository or process content

**Scenario:** Source, documentation, generated files, logs, process output, recovered job output, or fetched repository content instructs the model to escape policy.

**Mitigation:** Content/output is data, not policy. Local project, permission, path, executable, argument, environment, timeout, output, persistence, Git, and resource controls apply independently of model reasoning. Recovered output is sanitized again before exposure.

### Lexical path traversal

**Scenario:** A caller uses `..`, an absolute/UNC/drive path, ADS syntax, device names, or mixed separators to escape a root.

**Mitigation:** Filesystem callers and process/job working directories use project-relative paths only. Unsafe lexical forms are rejected before use and effective paths must resolve under the canonical project root.

### Symlink, junction, or reparse escape

**Scenario:** A path component inside a project or the runtime-state path redirects to another location.

**Mitigation:** `PathGuard` inspects project components without following them and rejects symlinks and redirecting Windows reparse points/junctions. Phase 6 separately checks runtime-state directory components and uses `PathGuard` for actual state-file reads.

### Hard-link escape

**Scenario:** A hard link inside an authorized root or job-state directory refers to another regular file object.

**Mitigation:** Protected regular-file reads reject link counts greater than one. Phase 6 state-file recovery also rejects hard-linked candidates and rechecks identity during the actual read.

### File replacement / TOCTOU

**Scenario:** A local process replaces a checked file between authorization and use.

**Mitigation:** Protected reads capture non-following file identity, open read-only, compare descriptor identity, revalidate the pathname, and only then read content. Persisted job-state reads reuse the same `PathGuard.read_bounded` protocol.

**Residual risk:** Directory creation and general application-level path policy cannot eliminate every race against another process with equivalent or greater local OS privileges. Local account isolation remains part of the security boundary.

### Secret exfiltration from an authorized root

**Scenario:** The source tree contains `.env`, cloud/SSH credentials, keys, Terraform state, or token files.

**Mitigation:** Read/search tools apply a defense-in-depth sensitive-path deny policy. Operators should still keep real secrets outside authorized roots.

**Residual execution risk:** Child processes are not subject to the read-tool sensitive-path filter. Trusted executable/project code can use ordinary OS APIs to read anything available to the bridge account.

### Shell command injection

**Scenario:** Model-controlled arguments contain `;`, `&&`, pipes, redirects, quotes, or other shell syntax intended to execute additional commands.

**Mitigation:** There is no `shell(command)` primitive. `run_process` and `start_job` use an executable alias plus argv and ultimately launch with `asyncio.create_subprocess_exec`. Known shell executables are rejected. Ordinary shell metacharacters remain argument data.

**Residual risk:** An allowlisted programmable executable can evaluate code or spawn other processes. That is an explicit high-trust execution grant, not shell confinement.

### Arbitrary executable selection

**Scenario:** A caller supplies an arbitrary host executable path or project-local binary to bypass policy.

**Mitigation:** MCP callers supply only an alias. The alias must exist in the selected project's local allowlist. Pinned absolute targets can only be defined in machine-local configuration and are never supplied by an MCP request.

### Executable substitution through PATH

**Scenario:** An allowlisted name resolves to attacker-controlled code earlier in PATH.

**Mitigation:** Unpinned lookup uses a constrained PATH that excludes empty/relative entries, redirecting directories, unavailable entries, and project-root directories. The resolved target is validated and identity is rechecked immediately before launch. Operators can pin aliases to canonical paths.

**Residual risk:** Portable subprocess APIs do not offer the same cross-platform descriptor-based exec guarantee used by protected file reads. A concurrently privileged local actor may still win the final executable-replacement race.

### Working-directory escape

**Scenario:** A process/job starts outside the project through an absolute path, traversal, symlink, mount, or junction.

**Mitigation:** `cwd` is normalized as project-relative and authorized through `PathGuard` before process creation and before a managed job ID is allocated.

**Residual risk:** The launch directory is not a child-process sandbox. Once running, the process can change directory and open paths available to the bridge OS user.

### Environment-secret inheritance

**Scenario:** The bridge process has API keys/tokens in environment variables and an executed tool prints them.

**Mitigation:** Child execution does not inherit the full parent environment and provides no MCP environment-override parameter. A small selected environment plus constrained PATH is constructed explicitly.

**Residual risk:** Secrets available through files, credential agents, local services, key stores, or other host mechanisms remain accessible to sufficiently privileged child code.

### Process output injection or exfiltration

**Scenario:** A child emits ANSI/control sequences, prompt injection, huge output, or sensitive host paths.

**Mitigation:** Capture is byte-bounded before decoding. Unsupported control characters are escaped and direct occurrences of the configured project-root string are redacted. Recovered output is sanitized/redacted again.

**Residual risk:** A malicious child can deliberately encode or transform sensitive information in ways generic redaction cannot recognize. Persisted output can therefore still be sensitive local data.

### Process-output resource exhaustion

**Scenario:** A child writes stdout/stderr indefinitely.

**Mitigation:** stdout and stderr share a bounded capture budget. Hitting the configured ceiling terminates the invocation and returns an `output_limit` result. Local configuration cannot exceed the hard application ceiling.

### Long-running or interactive process

**Scenario:** A test/build hangs or waits for input.

**Mitigation:** Every invocation has a bounded timeout; caller overrides cannot exceed policy. stdin is `DEVNULL`. Timeout terminates the supervised invocation.

### Process fan-out / concurrency exhaustion

**Scenario:** An MCP client starts many expensive background jobs.

**Mitigation:** Phase 5 retains bounded per-project execution capacity and Phase 6 adds a global managed-job ceiling. Excess work fails instead of building an unbounded manager queue.

**Residual risk:** Child code can spawn descendants, threads, GPU work, network traffic, or disk-heavy workloads. Application-level job counts are not OS resource quotas.

### Job-state disk or startup exhaustion

**Scenario:** A state directory contains a huge number of files, large records, or malformed records intended to consume startup time/memory.

**Mitigation:** Recovery caps directory entries examined, per-file size, retained-history count, total candidate bytes attempted, and recovered-output size. Malformed records are ignored rather than aborting the bridge.

### Persisted-state tampering

**Scenario:** A local actor edits a `.job.json` file to inject output, forge status, add a host path, or crash the parser with unexpected types.

**Mitigation:** Persisted state is untrusted. Recovery verifies file type/link behavior and identity, schema version, IDs, current project authorization, executable alias, cwd syntax, status, timestamps, argument count, output types/sizes, termination reason, exit code, and error bounds. Unexpected/malformed types fail closed. Recovered text is sanitized and root-redacted again.

### Stale authorization after configuration changes

**Scenario:** A project or executable was previously authorized, a job record remains on disk, and authorization is later revoked.

**Mitigation:** Recovery is a new authorization decision. A record is discarded unless the current registry still contains the project, `execute` remains enabled, and the executable alias remains allowlisted.

### Secret persistence through argv

**Scenario:** A command argument contains a token, password, signed URL, private path, or other secret and is written into durable job metadata.

**Mitigation:** Raw argv is never persisted. Only argument count is retained. Live argv exists only for the supervised task that needs it.

### Guessing or manipulating job IDs

**Scenario:** A caller uses malformed IDs or guesses IDs to probe job inventory.

**Mitigation:** Managed jobs use opaque 128-bit UUID-derived identifiers and malformed/unknown IDs receive a generic `Unknown job ID` failure. IDs are not treated as the security boundary; MCP endpoint authorization and local project policy remain authoritative.

### Bridge restart while a job is active

**Scenario:** The bridge restarts with durable state saying a process was `running` or `cancelling`.

**Mitigation:** Recovery converts nonterminal persisted states to `interrupted` with a `bridge_restart` reason. It never claims supervision resumed.

### PID reuse / unsafe reattachment

**Scenario:** A bridge stores a PID, restarts, and later kills or attaches to a different process that reused that PID.

**Mitigation:** Phase 6 deliberately does not persist/recover PIDs and does not reattach to processes after restart.

**Residual risk:** If the bridge itself crashes or is forcibly killed, an operating-system child may survive as an orphan depending on platform and process topology.

### Descendant process survives cancellation or timeout

**Scenario:** A child launches descendants that outlive cancellation/timeout.

**Mitigation:** POSIX uses a new session and process-group termination. Windows launches use a new process group and kill the direct child.

**Residual risk:** Portable Python APIs do not guarantee recursive Windows descendant termination. Phase 6 does not claim Windows Job Object or kernel-level process-tree containment.

### Process mutates project/host state

**Scenario:** An allowlisted program modifies source, deletes files, installs packages, changes config, or writes elsewhere on the host.

**Mitigation:** Execution is denied by default and requires explicit per-project permission plus an executable allowlist.

**Residual risk:** This is inherent to application-level execution. Allowed executable/project code has the OS privileges of the bridge account.

### Runtime state is accidentally published

**Scenario:** Persisted output contains private benchmark data or child-printed secrets and is committed to the public repository.

**Mitigation:** `runtime/`, `jobs/`, logs, outputs, config, credentials, Git-policy overlays, and related local state are ignored by Git and prohibited by repository security policy. The CI hygiene gate checks common sensitive tracked paths.

### Arbitrary or destructive Git operation

**Scenario:** An agent resets, cleans, rebases, force-pushes, deletes branches/tags, supplies an arbitrary refspec, or rewrites history.

**Mitigation:** Phase 7 exposes only `git_status`, `git_fetch`, and `git_sync_fast_forward`. The caller supplies only a project ID. There is no generic Git argv surface, push, reset, clean, checkout/switch, rebase, cherry-pick, force operation, arbitrary URL, arbitrary refspec, or history-rewrite primitive.

### Generic process execution bypasses Git policy

**Scenario:** A project has `execute: true` and an MCP caller invokes an allowlisted generic `git` executable to escape the Phase 7 API.

**Mitigation:** Phase 7 runtime policy rejects a project configuration that exposes `git`/`git.exe` through generic process execution. The narrow Git service and the generic execution service cannot intentionally expose Git simultaneously through two policy surfaces.

**Residual risk:** An allowlisted interpreter or other trusted executable can itself invoke Git or another VCS. Generic execution is already a high-trust capability and is not an OS sandbox.

### Git remote redirection or credential injection

**Scenario:** Repository-local config rewrites the trusted URL, invokes a credential helper, changes transport behavior, or causes Git to contact a different endpoint.

**Mitigation:** The remote URL/branch/name come from a separate ignored local policy overlay. The URL is HTTPS-only, cannot contain embedded credentials/query/fragment, and must exactly match the configured repository remote. System/global Git config, interactive prompting, credential helpers, AskPass, and non-HTTPS protocols are disabled. Repository-local aliases, credentials, URL rewrites, HTTP/protocol overrides, includes, submodules, hooks, filters, merge drivers, SSH commands, and unsafe remote overrides are rejected.

### Repository-local Git config executes code

**Scenario:** A malicious repository sets hooks, filters, external diff/merge helpers, aliases, include files, or other command-bearing config so a harmless status/fetch/sync causes arbitrary execution.

**Mitigation:** Phase 7 validates repository-local config before exposed Git operations, applies fixed command-line hardening, disables system/global config, disables hooks and interactive helpers, and invokes only fixed shell-free Git argv shapes.

**Residual risk:** Git itself remains a complex native parser. Phase 7 does not claim to sandbox a compromised Git binary or an unknown Git implementation vulnerability.

### Git metadata redirection or alternate object store

**Scenario:** `.git`, critical metadata, remote-ref directories, or object alternates redirect Git outside the authorized repository or alter object lookup.

**Mitigation:** Phase 7 requires `.git` to be a real directory, validates critical metadata and ref-directory components, rejects unsafe redirection/hard-link forms for protected metadata, rejects external object alternates/common-directory indirection, and requires `git rev-parse --show-toplevel` to resolve exactly to the authorized root.

### Dirty worktree data loss

**Scenario:** Synchronization overwrites or merges local staged, unstaged, or untracked work.

**Mitigation:** Phase 7 requires staged, unstaged, and untracked counts to be zero before fetch and rechecks after fetch before mutation. It does not auto-stash, reset, clean, or resolve conflicts.

### Ignored local file overwrite

**Scenario:** A remote commit starts tracking a path that currently contains a local ignored file such as a machine-local config or generated secret, and normal Git merge behavior would silently overwrite it.

**Mitigation:** Fast-forward synchronization uses `git merge --ff-only --no-overwrite-ignore`, causing Git to abort instead of replacing an ignored local file that obstructs the target tree.

### Divergent or local-ahead Git history

**Scenario:** Local history has commits not in the fetched remote, and an automated sync rewinds or merges them.

**Mitigation:** Phase 7 proves `HEAD` is an ancestor of the fetched remote head before mutation. Divergence/local-ahead states fail closed. No reset, rebase, merge commit, or force operation is attempted.

### Remote changes during synchronization

**Scenario:** The remote branch changes while synchronization is in progress.

**Mitigation:** The bridge fetches one object ID into the configured remote-tracking ref and treats that fetched ID as the target for the current operation. It verifies final `HEAD` equals that previously fetched target. A later remote update is handled by a later explicit operation.

### Repository state changes during synchronization

**Scenario:** Another local process changes branch, worktree, Git config, refs, or metadata between checks.

**Mitigation:** Phase 7 serializes Git operations within one bridge process, validates before operations, revalidates after fetch, rechecks branch/cleanliness, proves ancestry, and verifies final HEAD.

**Residual risk:** Locks are process-local and application-level checks cannot eliminate races from another bridge instance or local process with equivalent OS privileges. Git's own lock files provide additional integrity but do not turn the bridge into an OS sandbox.

### Fetched code later executes

**Scenario:** A trusted configured remote is compromised or intentionally contains malicious code; a fast-forward imports that code and a later `run_process`/job executes it.

**Mitigation:** Git synchronization and execution remain separate capabilities. Execution still requires `execute: true` and an executable allowlist.

**Residual risk:** If an operator enables both synchronization and execution for a project, newly fetched code can be executed with the bridge account's OS privileges. A trusted remote policy is therefore a trust decision, not a code-safety proof.

### Git network or output resource exhaustion

**Scenario:** A remote stalls, sends excessive diagnostic output, or causes repeated expensive Git work.

**Mitigation:** Git operations have bounded timeout and combined stdout/stderr capture, and only one Phase 7 operation per project may be active in a bridge process. Excess concurrent requests fail instead of queueing indefinitely.

**Residual risk:** Application-level limits do not cap all network bytes, disk writes, CPU, or memory consumed internally by Git.

### Public MCP exposure / compromised cloud account

**Scenario:** An unauthenticated endpoint or compromised AI session sends malicious requests.

**Mitigation:** Current development uses local stdio. Remote integration will require authenticated encrypted transport, but authentication never replaces local project/path/execution/job/Git enforcement.

## Residual Phase 7 risk

Phases 5 through 7 materially constrain orchestration but do not provide a container, VM, seccomp profile, Windows Job Object sandbox, filesystem namespace, network sandbox, or OS-level CPU/RAM/GPU quota.

Phase 7 narrows Git synchronization to a trusted local HTTPS policy and clean fast-forward updates, but Git remains complex native software processing untrusted repository/network data. A local actor with equivalent OS privileges can race metadata, another bridge instance can operate concurrently, and a compromised trusted remote can deliver malicious project content.

Direct filesystem write/delete/rename tools remain intentionally separate. Broader audit logging and runtime hardening follow in Phase 8.
