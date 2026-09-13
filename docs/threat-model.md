# Threat Model

## Assets to protect

- files outside explicitly authorized project roots;
- credentials, bearer tokens, private keys, cookies, environment secrets, and local authentication material;
- integrity of authorized repositories and the host operating system;
- CPU, RAM, GPU, disk, network, and bridge availability;
- private benchmark/runtime/job data;
- Git history, trusted remote policy, and local ignored/untracked data;
- integrity/availability of the local audit trail;
- the boundary between operational runtime state and MCP/model context;
- the Phase 9 remote endpoint from unauthenticated or misrouted network access.

## Core local threats

### Prompt injection through repository, process, or fetched content

**Scenario:** Source, documentation, generated files, process output, recovered job output, or fetched repository content instructs the model to escape policy.

**Mitigation:** Content is data, not policy. Project, path, executable, argument, environment, persistence, Git, audit, transport, and resource controls are deterministic local checks independent of model reasoning. Raw audit logs are not exposed as MCP context.

### Path traversal or redirection

**Scenario:** A caller uses `..`, an absolute/UNC/drive path, device/ADS syntax, symlinks, Windows junctions/reparse points, nested mounts, or hard links to escape an authorized root.

**Mitigation:** Project-relative lexical validation, non-following component inspection, canonical containment, redirection/mount rejection, protected hard-link rejection, and identity checks are applied before protected I/O. Process/job working directories reuse the same `PathGuard` boundary.

**Residual risk:** Application-level checks cannot eliminate every race against another process with equivalent or greater OS privileges.

### Sensitive file disclosure

**Scenario:** An otherwise authorized tree contains `.env`, SSH/cloud credentials, private keys, Terraform state, or token/service-account files.

**Mitigation:** Filesystem read/search tools apply a sensitive-path deny policy as defense in depth. Operators should keep real secrets outside authorized roots.

**Residual risk:** Allowlisted child code is not constrained by the read-tool filter and can use normal OS APIs with the bridge account's privileges.

### Shell or arbitrary executable escape

**Scenario:** Model-controlled input tries shell metacharacters, a shell executable, an arbitrary host executable path, or PATH substitution.

**Mitigation:** No generic shell command exists. Callers choose only configured executable aliases. Known shells are rejected; argv is passed directly to `asyncio.create_subprocess_exec`; unpinned lookup uses a constrained PATH; resolved targets are validated/rechecked immediately before launch.

**Residual risk:** An allowlisted interpreter/compiler/package manager/build tool is inherently programmable and remains a high-trust capability. Portable subprocess APIs also cannot provide a completely race-free descriptor-based cross-platform exec primitive.

### Execution resource exhaustion

**Scenario:** Child code hangs, emits unlimited output, waits for input, or the client starts too many tasks.

**Mitigation:** stdin is disabled; argv, timeout, captured output, per-project execution concurrency, and global managed-job inventory are bounded.

**Residual risk:** Child code can still spawn descendants/threads, use GPU/network/disk, or otherwise consume resources beyond these application-level counters.

### Process output exfiltration or prompt injection

**Scenario:** A child emits control sequences, huge output, prompt injection, or sensitive host data.

**Mitigation:** Capture is byte-bounded before decode; unsupported control characters are escaped; direct configured-root strings are redacted; recovered output is re-sanitized.

**Residual risk:** Generic redaction cannot recognize arbitrary encoding/obfuscation of host secrets.

## Persistent-job threats

### State tampering, startup exhaustion, or stale authorization

**Scenario:** Local state is malformed/oversized, forged, or refers to projects/executables whose authorization was later revoked.

**Mitigation:** Recovery bounds file count/size/total bytes/output, rejects unsafe file/link types and invalid schema, and reauthorizes project, execute permission, executable alias, relative cwd, status, timestamps, and sizes against current policy.

### Raw argument persistence

**Scenario:** A command argument contains a password, token, signed URL, or private path and is written to durable job state.

**Mitigation:** Raw argv is never persisted; only bounded metadata such as argument count is retained.

### PID reuse after restart

**Scenario:** A persisted PID is reused by another OS process and the bridge later attaches to or kills it.

**Mitigation:** PIDs are not persisted/reused. Nonterminal recovered jobs are marked `interrupted`.

### Descendants survive termination

**Scenario:** A child starts descendants that outlive timeout/cancellation or bridge failure.

**Mitigation:** POSIX uses a new session/process group. Windows terminates the direct child.

**Residual risk:** Portable Python does not guarantee recursive Windows descendant termination, and a force-killed bridge can leave OS children behind.

## Git threats

### Arbitrary/destructive Git operation

**Scenario:** An agent pushes, resets, cleans, rebases, changes branch, supplies arbitrary URLs/refspecs/argv, force-updates history, or deletes refs.

**Mitigation:** The dedicated API exposes only path-free status, fixed trusted fetch, and clean fast-forward synchronization. Caller-controlled Git argv/URL/refspec and history-rewrite operations are absent.

### Generic execution bypasses Git policy

**Scenario:** Generic process execution exposes `git` as a second unrestricted path around the dedicated service.

**Mitigation:** Runtime policy rejects a direct `git`/`git.exe` generic executable rule where the dedicated Git boundary is active.

**Residual risk:** A separately allowlisted interpreter/program can itself invoke Git; generic execution is already a high-trust capability.

### Repository-local Git config executes or redirects

**Scenario:** Malicious local configuration enables hooks, aliases, credential helpers, filters, includes, URL rewrites, submodules, external merge/diff helpers, alternate object stores, or transport redirection.

**Mitigation:** Repository layout and high-risk configuration namespaces are validated/rejected. System/global configuration and interactive helpers are disabled for bridge Git invocations. Transport is HTTPS-only and the configured repository remote must exactly match local trusted policy.

### Dirty/divergent synchronization destroys work

**Scenario:** Automated synchronization overwrites local changes/ignored data or rewinds/merges local history.

**Mitigation:** Sync requires the configured checked-out branch and zero staged/unstaged/untracked changes; rechecks after fetch; proves local `HEAD` is an ancestor of the fetched target; uses `--ff-only --no-overwrite-ignore`; verifies final `HEAD` exactly equals the fetched target.

### Remote/repository changes during sync

**Scenario:** The remote or another local process changes state mid-operation.

**Mitigation:** One fetched object ID is treated as the operation target; repository/branch/cleanliness are revalidated; final HEAD is verified. Git operations are serialized per project within one bridge process.

**Residual risk:** Another bridge instance or same-privilege local process can still race application-level checks.

### Fetched code later executes

**Scenario:** A compromised trusted remote delivers malicious code that is later executed locally.

**Mitigation:** Git synchronization and execution are separate capabilities; execution still requires explicit permission/alias and the audit precondition.

**Residual risk:** Enabling both capabilities is a deliberate trust decision, not a code-safety proof.

## Audit threats

### Sensitive data accidentally enters audit records

**Scenario:** Logging captures argv, output, search text, paths/content, remote URLs, credentials, environment values, transport headers, or arbitrary exceptions.

**Mitigation:** The audit logger accepts a fixed allowlisted metadata schema rather than a general string dictionary. Raw sensitive/request-derived text has no generic field.

### Audit redirection, hard-link abuse, or tampering

**Scenario:** A local actor places a symlink/junction/reparse/hard-linked object at the audit path or replaces a checked file.

**Mitigation:** Audit directories are inspected component-by-component; files must be regular/non-redirecting/single-link; first creation is exclusive; existing-file identity is checked around open; `O_NOFOLLOW` is used where available.

**Residual risk:** Local audit is not tamper-proof against a same/higher-privilege OS actor. There is no remote trusted sink or cryptographic signing key.

### Audit disk exhaustion

**Scenario:** Repeated activity grows audit data without bound.

**Mitigation:** Event size/detail count, active-file size, and retained-file count are bounded with rotation.

### Audit failure silently allows high-impact effects

**Scenario:** Disk/permission/tampering failure prevents logging while execution or Git mutation continues.

**Mitigation:** `run_process`, `start_job`, `cancel_job`, `git_fetch`, and `git_sync_fast_forward` require a successfully persisted `attempt` event before effect in the configured runtime. A failed completion write marks audit unhealthy; the next high-impact attempt again fails closed.

### Audit failure corrupts safe read semantics

**Scenario:** A read succeeds but completion logging fails and the caller is falsely told the read failed.

**Mitigation:** Read-only completion logging is non-strict. The operation result remains accurate and health becomes degraded.

### Raw audit becomes model context

**Scenario:** Locally modified audit records become a prompt-injection channel.

**Mitigation:** No raw audit-reader MCP tool exists. Only audit enabled/healthy health metadata is exposed.

## Phase 9 remote/tunnel threats

### Accidental public bind

**Scenario:** A config mistake binds the Python server to `0.0.0.0`, a LAN address, or a public interface, bypassing the intended tunnel boundary.

**Mitigation:** Remote policy accepts only `127.0.0.1` or `::1`. The transport layer independently enforces the same invariant before constructing/serving the app. Public reachability must arrive through a separate HTTPS tunnel/reverse proxy.

### Remote exposure enabled unintentionally

**Scenario:** Authorizing projects in the normal base config unexpectedly opens a network service.

**Mitigation:** Stdio remains the default runtime. Remote networking uses a separate command plus separate ignored remote overlay with explicit `enabled: true`. Importing the remote module is side-effect free.

### Unauthenticated internet client reaches MCP

**Scenario:** A public caller discovers the endpoint and directly invokes MCP tools.

**Mitigation:** Every HTTP request must contain exactly one valid bridge-owned bearer token before MCP dispatch. Missing, malformed, duplicate, or wrong credentials receive a generic `401`.

### Bearer-token guessing

**Scenario:** An attacker brute-forces the remote credential online.

**Mitigation:** The token must be 43–256 URL-safe ASCII characters and operators are instructed to generate cryptographically random material. Comparison is constant-time.

**Residual risk:** Provider/network rate limiting remains useful for volumetric abuse even though high-entropy online guessing is impractical.

### Bearer-token leakage

**Scenario:** The remote secret is committed, stored in YAML/URL/query strings, reflected into MCP context, written into audit/access logs, or forwarded downstream as a normal request header.

**Mitigation:** The secret is accepted only from `LOCAL_MCP_BRIDGE_REMOTE_TOKEN`; remote YAML has no secret field; the public URL rejects credentials/query/fragment; Uvicorn access logging is disabled; successful auth scrubs both `Authorization` and `Proxy-Authorization` before MCP dispatch; audit schema has no header/token field.

**Residual risk:** Environment variables are process-local configuration, not a hardware-backed secret store. A same/higher-privilege local actor may still inspect process state. Suspected exposure requires token rotation/restart.

### Stolen valid bearer token

**Scenario:** An attacker obtains the current token and sends valid authenticated MCP requests.

**Mitigation:** Existing project/tool/path/execution/Git/audit authorization still applies behind the transport gate. The token grants endpoint reachability, not arbitrary host capability.

**Residual risk:** Until rotation/restart, the attacker has the same transport reachability as the intended bearer holder. Phase 9 has no per-user identities/scopes/revocation list.

### Plaintext public transport / TLS downgrade

**Scenario:** A client reaches Local-MCP-Bridge over public plaintext HTTP or an operator treats the loopback HTTP listener as a LAN/public service.

**Mitigation:** Public policy requires an HTTPS client-facing URL while the bridge socket itself remains loopback-only. TLS termination is delegated to the separately managed ingress. Non-loopback plaintext deployment is unsupported.

### DNS rebinding / malicious Host or Origin

**Scenario:** Browser/network behavior routes a request to loopback while presenting an attacker-controlled Host/Origin.

**Mitigation:** MCP SDK DNS-rebinding protection stays enabled with public/local Host/Origin allowlists. Invalid values are rejected before normal MCP dispatch.

### Malicious forwarded headers

**Scenario:** A remote client spoofs `X-Forwarded-*` metadata to influence scheme/address interpretation.

**Mitigation:** Uvicorn trusts forwarded metadata only from the configured loopback peer expected to be the tunnel/reverse proxy.

**Residual risk:** A malicious same-privilege local process can connect from loopback and spoof proxy metadata. Host compromise is outside Phase 9 containment claims.

### Tunnel/reverse-proxy compromise

**Scenario:** The ingress provider is compromised or misconfigured and forwards arbitrary traffic, observes post-TLS requests, or changes headers.

**Mitigation:** The bridge bearer gate remains independent of provider authentication and is required after the tunnel hop. Tunnel authentication is defense in depth, not a replacement for Local-MCP-Bridge authorization.

**Residual risk:** The ingress is part of the transport trust chain and can observe/modify traffic after TLS termination. If it also gains the bearer secret, the bridge cannot distinguish it from the authorized bearer holder.

### Tunnel provider credentials committed or executed by the bridge

**Scenario:** Cloudflare/ngrok/SSH credentials enter the public repo, or model input controls a tunnel executable/command.

**Mitigation:** Phase 9 is provider-agnostic and contains no tunnel-management tool or subprocess path. Provider credentials/configuration remain external local operational state. Repository hygiene rejects common secret/local state patterns.

### Network request/session exhaustion

**Scenario:** Authenticated or unauthenticated traffic sends huge bodies, opens excessive sessions/connections, or holds keep-alive resources.

**Mitigation:** Authentication blocks normal MCP work for invalid clients; MCP request bodies are capped; legacy session idle lifetime/count are bounded; Uvicorn concurrency/backlog are capped at 64 and keep-alive at 5 seconds.

**Residual risk:** Traffic can consume upstream tunnel/provider/bandwidth resources before bridge-level controls apply. Provider-side DDoS/rate limiting remains outside the bridge.

### Authentication failures create durable disk churn

**Scenario:** Internet scanners repeatedly send bad tokens and force a persistent audit write per request until disk is exhausted or logs become noisy.

**Mitigation:** Bearer failures are handled before MCP dispatch and deliberately are not written to the Phase 8 persistent audit JSONL. Authenticated MCP tool activity retains the ordinary audit policy.

### Remote transport bypasses Phase 0–8 authorization

**Scenario:** The HTTP entry point constructs a new server with permissive defaults and skips project/Git/job/audit configuration.

**Mitigation:** Stdio and remote use the same `runtime_composition.py` path and inject the same registry, execution service, persistent job manager, and audit logger into the same `create_mcp_server(...)` factory.

### OAuth-only client incompatibility

**Scenario:** A target MCP client requires standards-based OAuth discovery/consent and cannot attach the Phase 9 static bearer header.

**Mitigation:** This is treated as a compatibility failure, not a reason to weaken authentication. Phase 10 validates the intended target client. If OAuth is mandatory, it requires a separate reviewed standards-based auth integration.

## Runtime state publication

### Local state is accidentally committed

**Scenario:** Job state, audit logs, bearer secrets, remote/tunnel policy, or private output enters the public repository.

**Mitigation:** Runtime/job/log/output directories, `.env`, local overlays including `config/*.local.yaml`, credential/token files, and similar state are ignored by Git. Security-baseline CI rejects common tracked sensitive paths and obvious private keys. Operators must still review diffs before publication.

If a secret is committed, later deletion is insufficient: rotate/revoke it immediately and purge history where appropriate.

## Residual Phase 9 risk

Phases 5–9 materially constrain model-driven orchestration but do not provide a VM/container, seccomp profile, Windows Job Object sandbox, filesystem namespace, network sandbox, provider firewall, or OS-level CPU/RAM/GPU quota.

The most important remaining risks are:

- same/higher-privilege local actors can race/tamper with process and runtime state;
- executable project code can use the bridge account's OS/network privileges;
- Git remains a complex native parser of repository/network data;
- audit files are local operational evidence, not non-repudiable records;
- a stolen Phase 9 bearer token provides endpoint reachability until rotation;
- the HTTPS tunnel/reverse proxy is part of the transport trust chain;
- volumetric traffic may consume resources upstream of bridge limits;
- Phase 9's simple bearer model may not satisfy clients that mandate OAuth.

Remote authentication never replaces local capability authorization. Direct filesystem write/delete/rename MCP tools remain intentionally absent and require a separate security design before introduction.
