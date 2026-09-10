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

**Scenario:** A README, log file, generated artifact, issue text, or source comment contains instructions telling the AI to read secrets or execute dangerous commands.

**Mitigation:** The model is not trusted to enforce policy. Local authorization checks must reject disallowed paths and operations regardless of the model's reasoning.

### Path traversal

**Scenario:** A caller requests `../../secret.txt` or uses mixed separators/encoded path components to escape the project root.

**Mitigation:** Canonicalize paths before authorization and verify containment using filesystem-aware path semantics rather than string prefixes.

### Symlink, junction, or reparse-point escape

**Scenario:** A path located inside an allowed project points to a target outside it.

**Mitigation:** Resolve filesystem indirections and reject access when the effective target leaves the configured root. Windows-specific junction/reparse behavior must be covered by tests.

### Command injection

**Scenario:** User/model-controlled text is passed through `cmd.exe`, PowerShell, or a POSIX shell and interpreted as extra commands.

**Mitigation:** Keep arbitrary shell execution disabled. Launch approved executables with argument arrays and shell interpretation disabled.

### Executable substitution

**Scenario:** An allowlisted executable name resolves to a malicious binary earlier in `PATH`.

**Mitigation:** Prefer validated executable resolution, constrained PATH behavior, and optionally configured absolute executable paths for higher-risk deployments.

### Working-directory escape

**Scenario:** An allowed executable is started with a working directory outside the project root and gains access to unrelated files.

**Mitigation:** Canonicalize and authorize process working directories under the same project-root policy as filesystem tools.

### Secret exfiltration

**Scenario:** A tool exposes `.env`, process environment variables, browser data, SSH keys, cloud credentials, or tunnel tokens.

**Mitigation:** Do not expose host-wide environment or arbitrary paths. Keep secrets outside authorized roots where possible, filter inherited environment, and redact known sensitive values from output/logs.

### Resource exhaustion

**Scenario:** Repeated or malicious jobs consume excessive CPU/GPU/RAM/disk, produce unbounded logs, or run indefinitely.

**Mitigation:** Per-project concurrency limits, execution timeouts, bounded stdout/stderr, cancellation, runtime quotas where practical, and explicit job state.

### Destructive Git operation

**Scenario:** An agent force-pushes, resets, cleans, deletes branches, or rewrites history.

**Mitigation:** Expose narrow non-destructive Git operations first. Deny destructive commands by default and require separate review before adding them.

### Public MCP exposure

**Scenario:** A development server is bound directly to a public interface without authentication.

**Mitigation:** Bind locally by default. Use authenticated TLS transport/tunneling for remote access, and retain local capability enforcement even after authentication.

### Compromised MCP client or account

**Scenario:** The remote AI account/session or MCP client is compromised and issues validly authenticated malicious requests.

**Mitigation:** Authentication is not the authorization boundary. Least-privilege project permissions, narrow tools, local allowlists, logging, and optional human approval for future destructive operations limit impact.

### Malicious child-process output

**Scenario:** A benchmark/test prints prompt-injection text, terminal control characters, secrets, or huge output.

**Mitigation:** Treat output as data, bound its size, prefer structured artifacts, sanitize terminal controls, and avoid embedding secrets in command output.

## Out of scope for the initial implementation

The initial prototype will not claim to provide:

- a hardened multi-user security boundary;
- arbitrary host administration;
- unrestricted shell access;
- general-purpose sandboxing of hostile executables;
- guaranteed containment of intentionally malicious native code;
- filesystem write/delete operations.

These require stronger isolation mechanisms than an application-level MCP policy layer alone.
