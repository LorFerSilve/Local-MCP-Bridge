# Threat Model

## Assets to protect

- files outside explicitly authorized project roots;
- credentials, tokens, private keys, cookies, and environment secrets;
- integrity of local source repositories;
- host operating system integrity;
- availability of CPU, RAM, GPU, disk, and network resources;
- private benchmark data and runtime logs;
- Git history and remote repositories.

## Threats and mitigations

### Prompt injection through repository content

**Scenario:** A README, source comment, log, or generated file tells the AI to read secrets, escape the project root, or later execute dangerous commands.

**Mitigation:** Repository content is data, not policy. Local permission/path checks reject disallowed operations independently of model reasoning. Phase 3 exposes read-only capabilities only.

### Path traversal

**Scenario:** A caller requests `../../secret.txt`, an absolute path, a Windows drive/UNC path, mixed separators, or another lexical form intended to escape the root.

**Mitigation:** Phase 3 accepts project-relative paths only, normalizes separators, rejects `..`, drive/UNC/absolute paths, control characters, NTFS ADS syntax, ambiguous Windows suffixes, and reserved device names before I/O. Effective paths are then strictly resolved and checked against the canonical project root.

### Symlink, junction, or reparse-point escape

**Scenario:** A path inside an allowed project points to a target outside it.

**Mitigation:** Phase 3 rejects standard symbolic-link path components and also rejects any strictly resolved path that leaves the registered root. Recursive search does not follow standard symlinks and re-checks canonical containment for discovered entries. Phase 4 is dedicated to stronger Windows junction/reparse-point and TOCTOU defenses before process execution is enabled.

### Secret exfiltration from an authorized root

**Scenario:** An authorized source tree itself contains `.env`, SSH/cloud credentials, private keys, Terraform state, or token files, and the model asks the read tool for them.

**Mitigation:** Phase 3 applies a defense-in-depth sensitive-path deny policy in addition to project-level read permission. Common credential directories/files and private-key formats are inaccessible. Operators should still keep real secrets outside authorized roots because deny patterns cannot identify every secret.

### NTFS alternate data streams and device paths

**Scenario:** Windows-specific syntax such as `file.txt:stream`, `CON`, `NUL`, or drive/device paths bypasses normal filename filtering.

**Mitigation:** Colon/ADS syntax, Windows drive qualification, UNC paths, and reserved DOS device names are rejected at the project-relative path parser.

### Binary or oversized file extraction

**Scenario:** A caller reads a huge file, binary artifact, database, model weight, or a file that grows after an initial size check, consuming RAM/context or leaking opaque data.

**Mitigation:** `read_file` is UTF-8 text-only, rejects NUL-containing content, has a hard byte ceiling and line pagination, and performs a bounded file-descriptor read of at most `limit + 1` bytes rather than relying solely on `stat()`.

### Recursive-search resource exhaustion

**Scenario:** A model searches a very large source tree or directory containing millions of entries and causes excessive disk I/O, memory allocation, or MCP output.

**Mitigation:** Search caps query length, result count, entries inspected, files scanned, bytes per file, and total bytes. Directory enumeration is bounded before entries are materialized in memory. Search uses literal substring matching rather than attacker-controlled regular expressions.

### Command injection

**Scenario:** User/model-controlled text is passed through `cmd.exe`, PowerShell, or a POSIX shell and interpreted as extra commands.

**Mitigation:** No process-execution MCP capability exists in Phase 3. Future execution keeps arbitrary shell strings disabled and launches approved executables with argument arrays.

### Executable substitution

**Scenario:** A future allowlisted executable name resolves to a malicious binary earlier in `PATH`.

**Mitigation:** Future execution must validate executable resolution, constrain inherited `PATH`, and support absolute executable policies where appropriate.

### Working-directory escape

**Scenario:** A future approved executable starts outside its authorized project root.

**Mitigation:** Future working directories must use the hardened project-path authorization boundary before process launch.

### Resource exhaustion by future jobs

**Scenario:** Repeated jobs consume excessive CPU/GPU/RAM/disk, produce unbounded logs, or run indefinitely.

**Mitigation:** Planned controls include per-project concurrency, timeouts, output limits, cancellation, runtime quotas where practical, and explicit job state.

### Destructive Git operation

**Scenario:** An agent force-pushes, resets, cleans, deletes branches, or rewrites history.

**Mitigation:** Git capabilities are not present in Phase 3. Future Git tools will begin with narrow non-destructive operations and deny destructive history operations by default.

### Public MCP exposure

**Scenario:** A development server is exposed directly to the internet without suitable authentication or transport security.

**Mitigation:** Current development uses local stdio. Remote integration will use authenticated encrypted transport/tunneling while retaining all local authorization checks.

### Compromised MCP client or account

**Scenario:** A cloud AI account/session is compromised and issues validly authenticated malicious MCP requests.

**Mitigation:** Authentication is not the authorization boundary. Per-project permissions, relative-path-only tools, sensitive-path filters, resource limits, and later audit/approval controls limit impact.

### Filesystem race / TOCTOU

**Scenario:** A local process changes a checked path, symlink, junction, or file identity after authorization but before/during open.

**Mitigation:** Phase 3 bounds the impact of reads but does not claim complete race-resistant filesystem isolation. Phase 4 explicitly owns TOCTOU and Windows reparse-point hardening. Process execution stays disabled until that work is reviewed.

### Malicious child-process output

**Scenario:** A future benchmark/test emits prompt-injection text, terminal control characters, secrets, or huge output.

**Mitigation:** Future job output is treated as untrusted data, bounded, structured where practical, and sanitized for terminal controls.

## Out of scope for the current implementation

The current Phase 3 implementation does not claim to provide:

- a hardened multi-user operating-system sandbox;
- arbitrary host administration;
- unrestricted shell access;
- general-purpose containment of hostile native executables;
- filesystem write/delete/rename operations;
- complete race-proof behavior against a simultaneously malicious local filesystem actor;
- complete Windows reparse-point hardening before Phase 4.

These require stronger controls than basic application-level path policy alone.
