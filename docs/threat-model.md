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

**Scenario:** Source, documentation, logs, or generated files instruct the model to escape policy.

**Mitigation:** Content is data, not policy. Local project, permission, path, sensitive-file, and resource controls apply independently of model reasoning.

### Lexical path traversal

**Scenario:** A caller uses `..`, an absolute/UNC/drive path, ADS syntax, device names, or mixed separators to escape a root.

**Mitigation:** Only project-relative paths are accepted. Unsafe lexical forms are rejected before I/O; effective paths must then resolve under the canonical project root.

### Symlink or junction escape

**Scenario:** A path component inside a project redirects to another location.

**Mitigation:** Phase 4 inspects components without following them and rejects symlinks and redirecting Windows name-surrogate reparse points, including junction-style paths. Redirecting children are omitted from listings/search. Nested mount points are denied.

### Hard-link escape

**Scenario:** A hard link inside an authorized root names the same regular file object as a sensitive file outside the root on the same volume.

**Mitigation:** Phase 4 denies regular files whose link count is greater than one. Such files are not read or exposed as normal list/search targets.

### File replacement / TOCTOU

**Scenario:** A local process replaces a checked file or redirects its pathname between authorization and use.

**Mitigation:** Phase 4 captures non-following file identity, opens read-only (`O_NOFOLLOW` where available), compares descriptor identity, revalidates the pathname and identity, and only then reads content. Identity mismatch fails closed before content is returned.

### Directory replacement during enumeration

**Scenario:** A directory is replaced while `list_directory` or recursive search enumerates it.

**Mitigation:** Directory identity is captured before enumeration and revalidated afterward. A changed directory causes the snapshot to be discarded. Search queues project-relative paths and reauthorizes each directory/file when actually used.

### Secret exfiltration from an authorized root

**Scenario:** The source tree contains `.env`, cloud/SSH credentials, keys, Terraform state, or token files.

**Mitigation:** A defense-in-depth sensitive-path deny policy blocks common secret-bearing locations/formats even when project read permission is granted. Real secrets should still be kept outside authorized roots.

### Binary or oversized extraction / search exhaustion

**Scenario:** Huge/binary/model/database files or enormous trees consume RAM, disk I/O, or model context.

**Mitigation:** Reads are UTF-8 text-only and bounded at the descriptor. Listings/search cap entries, files, per-file bytes, total bytes, query length, and results. Search uses literal substring matching rather than caller-controlled regex.

### Command injection

**Scenario:** Future caller input is interpreted by `cmd.exe`, PowerShell, or a POSIX shell.

**Mitigation:** No process-execution MCP tool exists in Phase 4. Phase 5 must launch approved executables using argument vectors and must not expose a generic shell-string primitive.

### Executable substitution / working-directory escape

**Scenario:** An allowlisted executable name resolves to attacker-controlled code or a future process starts outside its project.

**Mitigation:** Phase 5 must constrain executable resolution/PATH and authorize working directories through the hardened project boundary.

### Destructive Git operation

**Scenario:** An agent resets, cleans, force-pushes, deletes branches, or rewrites history.

**Mitigation:** Git MCP capabilities are absent in Phase 4. Future Git tools begin narrowly and deny destructive history operations by default.

### Public MCP exposure / compromised cloud account

**Scenario:** An unauthenticated endpoint or compromised AI session sends malicious requests.

**Mitigation:** Current development uses local stdio. Remote integration will require authenticated encrypted transport, but authentication never replaces local capability/path enforcement.

### Malicious future child-process output

**Scenario:** Benchmarks/tests emit prompt injection, control characters, secrets, or unbounded output.

**Mitigation:** Future job output must be treated as untrusted data, bounded, structured where practical, and sanitized where rendering could interpret controls.

## Residual Phase 4 risk

Phase 4 materially hardens application-level path access but does **not** claim a hardened multi-user/kernel sandbox or formal containment of a concurrently malicious local process with arbitrary filesystem privileges. It does not make hostile native executables safe to run.

The current implementation also intentionally does not provide filesystem writes/deletes/renames, process execution, Git mutation, arbitrary host administration, or unrestricted shell access. Those capabilities require separate policy and tests in later phases.
