# Threat Model

## Assets to protect

- files outside explicitly authorized project roots;
- credentials, tokens, private keys, cookies, and environment secrets;
- integrity of local repositories and the host operating system;
- CPU, RAM, GPU, disk, and network availability;
- private benchmark/runtime data;
- Git history and remotes.

## Threats and mitigations

### Prompt injection through repository content

**Scenario:** Source, documentation, logs, generated files, or process output instruct the model to escape policy.

**Mitigation:** Content/output is data, not policy. Local project, permission, path, executable, argument, environment, timeout, output, and resource controls apply independently of model reasoning.

### Lexical path traversal

**Scenario:** A caller uses `..`, an absolute/UNC/drive path, ADS syntax, device names, or mixed separators to escape a root.

**Mitigation:** Filesystem callers and Phase 5 process working directories use project-relative paths only. Unsafe lexical forms are rejected before use and effective paths must resolve under the canonical project root.

### Symlink or junction escape

**Scenario:** A path component inside a project redirects to another location.

**Mitigation:** Phase 4 inspects components without following them and rejects symlinks and redirecting Windows name-surrogate reparse points, including junction-style paths. Nested mount points are denied. Phase 5 reuses the same `PathGuard` for `cwd` authorization.

### Hard-link escape

**Scenario:** A hard link inside an authorized root names the same regular file object as a sensitive file outside the root on the same volume.

**Mitigation:** Phase 4 denies regular files whose link count is greater than one for filesystem reads/search. Such files are not exposed as normal read targets.

### File replacement / TOCTOU

**Scenario:** A local process replaces a checked file or redirects its pathname between authorization and use.

**Mitigation:** Phase 4 captures non-following file identity, opens read-only, compares descriptor identity, revalidates the pathname, and only then reads content. Directory identity is similarly checked around enumeration.

### Secret exfiltration from an authorized root

**Scenario:** The source tree contains `.env`, cloud/SSH credentials, keys, Terraform state, or token files.

**Mitigation:** Read/search tools apply a defense-in-depth sensitive-path deny policy. Operators should still keep real secrets outside authorized roots.

**Residual execution risk:** A Phase 5 child process is not subject to the read-tool sensitive-path filter. If executable/project code can open a secret file using ordinary OS APIs, it can read it. Execution must therefore be enabled only for code trusted to run as the bridge account.

### Binary or oversized extraction / search exhaustion

**Scenario:** Huge/binary/model/database files or enormous trees consume RAM, disk I/O, or model context.

**Mitigation:** Filesystem reads/search remain bounded and text-only as defined in Phase 4.

### Shell command injection

**Scenario:** Model-controlled arguments contain `;`, `&&`, pipes, redirections, quoting tricks, or other shell syntax intended to execute additional commands.

**Mitigation:** Phase 5 has no `shell(command)` primitive. `run_process` uses an executable alias plus an argv vector and launches with `asyncio.create_subprocess_exec`. Known shell executables (`cmd.exe`, PowerShell, `sh`, `bash`, etc.) are rejected. Shell metacharacters in ordinary argv therefore remain data.

**Residual risk:** An allowlisted programmable executable can itself evaluate code or invoke other processes. For example, allowlisting Python permits Python behavior supported by the supplied argv/project code. This is an explicit execution trust grant, not shell confinement.

### Arbitrary executable selection

**Scenario:** A caller provides `C:\Windows\...`, `/usr/bin/...`, a project-local binary, or another host path to bypass policy.

**Mitigation:** MCP callers provide an alias only. The alias must exist in the selected project's local allowlist. Absolute executable paths can only be configured locally as pinned alias targets and are never accepted from the MCP request.

### Executable substitution through PATH

**Scenario:** An allowlisted name such as `python` or `pytest` resolves to attacker-controlled code earlier in PATH, including a project-local executable.

**Mitigation:** Unpinned resolution uses a constrained PATH that excludes empty/relative entries, redirecting PATH directories, unavailable paths, and directories inside the project root. The resolved target is validated as a regular executable file and its identity is rechecked immediately before launch. Operators can instead pin an alias to a canonical absolute executable path in ignored local config.

**Residual risk:** Portable Python subprocess APIs do not provide the same cross-platform descriptor-based execution primitive used for Phase 4 reads. A concurrently privileged local actor may still replace an executable during the final launch race. Pinned targets and immediate identity checks reduce but do not formally eliminate this risk.

### Working-directory escape

**Scenario:** A future process starts outside the project by using an absolute path, traversal, symlink, or Windows junction as `cwd`.

**Mitigation:** Phase 5 normalizes `cwd` as a project-relative path and authorizes it with Phase 4 `PathGuard` before process creation.

**Residual risk:** `cwd` controls only the starting directory. The child process itself is not filesystem-sandboxed and can change directory/open paths accessible to the bridge OS user.

### Environment-secret inheritance

**Scenario:** The bridge process has API keys/tokens in environment variables and an executed test/tool reads or prints them.

**Mitigation:** Phase 5 does not inherit the full parent environment and provides no MCP environment-override parameter. It constructs a minimal environment from selected platform/runtime variables plus a constrained PATH.

**Residual risk:** Secrets available through files, credential agents, local services, OS key stores, or other mechanisms are outside this environment filter.

### Process output injection

**Scenario:** A child emits ANSI escapes, terminal control sequences, prompt injection, huge output, or direct host paths.

**Mitigation:** Capture is byte-bounded before decoding. Unsupported control characters are escaped and direct occurrences of the configured project-root string are redacted. Output remains untrusted data and is never policy.

### Process-output resource exhaustion

**Scenario:** A child writes stdout/stderr indefinitely and fills memory or pipe buffers.

**Mitigation:** stdout and stderr share a bounded capture budget. Hitting the configured ceiling terminates the process and returns a structured `output_limit` result. Local configuration itself is capped at a hard 1 MiB capture ceiling.

### Long-running or stuck process

**Scenario:** A test/build hangs indefinitely or waits interactively for input.

**Mitigation:** Every process has a bounded timeout; caller overrides cannot exceed the project's configured maximum or the hard 300-second ceiling. stdin is `DEVNULL`, so interactive input is unavailable. Timeout terminates the launched process.

### Process fan-out / concurrency exhaustion

**Scenario:** Multiple MCP calls launch many expensive processes simultaneously.

**Mitigation:** Phase 5 enforces a per-project semaphore with configurable concurrency capped by a hard maximum of four simultaneous one-shot processes.

**Residual risk:** Child processes can themselves spawn descendants, threads, GPU work, network traffic, or disk-heavy tasks. Phase 5 does not enforce OS-level CPU/RAM/GPU/network quotas.

### Descendant process survives parent termination

**Scenario:** A timed-out process spawns children that survive after the direct process is killed.

**Mitigation:** POSIX launches use a new session and termination targets the process group. Windows launches use a new process group.

**Residual risk:** Python's portable Windows process kill does not guarantee recursive descendant termination. Stronger Windows Job Object/process-tree supervision is deferred to Phase 6/runtime hardening.

### Process cancellation / MCP disconnect

**Scenario:** The MCP request is cancelled while a process is running.

**Mitigation:** Phase 5 catches async cancellation, terminates the launched process, then propagates cancellation.

### Process mutates project/host state

**Scenario:** An allowlisted program modifies source, deletes files, installs packages, changes configuration, or writes elsewhere on the host.

**Mitigation:** Execution is disabled by default and requires explicit per-project permission plus an executable allowlist. No claim is made that Phase 5 makes executed code read-only.

**Residual risk:** This is inherent to application-level process execution. Any allowed executable/project code has the OS privileges of the bridge account. Operators must treat `execute: true` as a high-trust permission.

### Destructive Git operation

**Scenario:** An agent resets, cleans, force-pushes, deletes branches, or rewrites history.

**Mitigation:** Dedicated Git MCP capabilities remain absent until Phase 7, where Git operations will receive specific policy. Operators should avoid generically allowlisting Git for untrusted agent control because generic argv execution cannot safely classify every Git-side effect.

### Public MCP exposure / compromised cloud account

**Scenario:** An unauthenticated endpoint or compromised AI session sends malicious requests.

**Mitigation:** Current development uses local stdio. Remote integration will require authenticated encrypted transport, but authentication never replaces local capability/path/execution enforcement.

## Residual Phase 5 risk

Phase 5 materially reduces model-driven shell injection, arbitrary executable selection, project-starting-directory escape, accidental environment inheritance, excessive output/runtime, and uncontrolled one-shot concurrency. It does **not** provide a hardened OS/container/VM sandbox.

In particular, it does not make hostile native code, Python code, tests, build scripts, package managers, compilers, or other programmable tools safe. An executed process can use the bridge account's host privileges, filesystem access, network access, and other OS capabilities. Stronger process supervision begins in Phase 6; broader runtime hardening remains a later phase.

Direct filesystem write/delete/rename MCP primitives and dedicated Git mutation APIs remain intentionally separate from Phase 5.
