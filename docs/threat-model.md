# Threat Model

## Assets to protect

- files outside explicitly authorized project roots;
- credentials, tokens, private keys, cookies, and environment secrets;
- integrity of local repositories and the host operating system;
- CPU, RAM, GPU, disk, and network availability;
- private benchmark/runtime/job data;
- Git history and remotes.

## Threats and mitigations

### Prompt injection through repository or process content

**Scenario:** Source, documentation, generated files, logs, process output, or recovered job output instructs the model to escape policy.

**Mitigation:** Content/output is data, not policy. Local project, permission, path, executable, argument, environment, timeout, output, persistence, and resource controls apply independently of model reasoning. Recovered output is sanitized again before exposure.

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

**Mitigation:** Protected reads capture non-following identity, open read-only, compare descriptor identity, revalidate the pathname, and only then read content. Persisted job-state reads reuse the same `PathGuard.read_bounded` protocol.

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

**Mitigation:** Unpinned lookup uses a constrained PATH that excludes empty/relative entries, redirecting directories, unavailable paths, and project-root directories. The resolved target is validated and identity is rechecked immediately before launch. Operators can pin aliases to canonical paths.

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

**Mitigation:** `runtime/`, `jobs/`, logs, outputs, config, credentials, and related local state are ignored by Git and prohibited by repository security policy. The CI hygiene gate checks common sensitive tracked paths.

### Destructive Git operation

**Scenario:** An agent resets, cleans, force-pushes, deletes branches, or rewrites history.

**Mitigation:** Dedicated Git MCP capabilities remain absent until Phase 7, where Git operations receive specific policy. Generic unrestricted Git/shell execution should not be exposed to untrusted agent control.

### Public MCP exposure / compromised cloud account

**Scenario:** An unauthenticated endpoint or compromised AI session sends malicious requests.

**Mitigation:** Current development uses local stdio. Remote integration will require authenticated encrypted transport, but authentication never replaces local project/path/execution/job enforcement.

## Residual Phase 6 risk

Phase 6 materially improves long-running orchestration by separating jobs from individual MCP calls, bounding durable state, reauthorizing recovery, avoiding raw-argv persistence, and providing explicit cancellation/restart semantics.

It still does not provide a container, VM, seccomp profile, Windows Job Object sandbox, filesystem namespace, network sandbox, or OS-level CPU/RAM/GPU quota. Hostile native/interpreted code remains outside the promised security model.

Direct filesystem write/delete/rename tools and dedicated Git mutation APIs remain intentionally separate. Phase 7 introduces constrained Git synchronization; broader runtime/audit hardening follows in Phase 8.
