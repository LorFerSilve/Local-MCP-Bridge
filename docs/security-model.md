# Security Model

## Trust model

Local-MCP-Bridge treats model-generated calls, repository content, MCP input, filenames, paths, process arguments/output, persisted job state, Git metadata/configuration, audit-state paths, HTTP requests, request headers, proxy metadata, and tunnel-facing input as untrusted.

Deterministic machine-local policy is the authorization boundary. Authentication proves possession of a remote transport credential; it does **not** grant broader project, filesystem, execution, job, or Git capabilities.

## Deny by default

Projects are registered explicitly. Capability flags are `read`, `search`, `execute`, and `git`; missing permissions are denied. `search: true` requires `read: true`. Execution requires `execute: true` plus an explicit executable allowlist. Dedicated Git synchronization requires the separate ignored Git-policy overlay.

`security.deny_by_default` cannot be disabled and `security.allow_arbitrary_shell` cannot be enabled.

Remote networking is a separate opt-in. The base project configuration cannot enable a listener. Phase 9 requires the separate ignored remote-policy file, explicit `enabled: true`, a valid loopback bind, a valid HTTPS public `/mcp` URL, and a valid bearer token from the environment.

## Project identity and path boundary

MCP clients address logical project IDs, never absolute host roots. Configured roots must exist, be canonical directories, and may not overlap.

Filesystem requests accept only project-relative paths. Unsafe lexical forms are rejected, including traversal (`..`), absolute/drive/UNC paths, control/NUL characters, NTFS ADS/colon syntax, reserved DOS device names, and Windows-ambiguous trailing spaces/periods.

Existing path components are inspected without intentionally following redirections. Symlinks, redirecting Windows reparse points/junctions, nested mount points, unsupported file types, and hard-linked protected regular files are rejected. Effective paths are strictly resolved and proven to remain under the configured root.

Protected reads use identity checks around open. Directory enumeration verifies directory identity, and recursive search reauthorizes entries at point of use. Application-level path checks still cannot eliminate every race against another process with equivalent or greater OS privileges.

## Sensitive-path defense in depth

Read/search permission does not expose every file below a root. Common secret-bearing paths and formats such as `.env`, `.git`, SSH/cloud credential directories, private-key files, Terraform state, and common service-account/token locations remain denied. Templates such as `.env.example` remain readable.

This is defense in depth, not secret discovery. Operators should keep real credentials outside authorized roots where practical.

## Controlled execution boundary

There is no `shell(command)` primitive. `run_process(...)` and managed jobs select a locally configured executable alias and ultimately launch through `asyncio.create_subprocess_exec` with a direct argv vector.

Execution requires:

1. an existing project;
2. `execute: true`;
3. an allowlisted executable alias;
4. a non-shell target;
5. bounded argv;
6. a timeout within policy;
7. a project-relative `cwd` authorized by `PathGuard`;
8. a safely resolved executable target whose identity is rechecked immediately before launch;
9. a minimal child environment, disabled stdin, and bounded output/concurrency.

The configured runtime adds a Phase 8 outer gate: a persistent audit `attempt` record must succeed before `run_process` or `start_job` reaches the underlying execution/job service.

Allowlisted interpreters, compilers, package managers, test runners, and build systems remain high-trust capabilities. Once launched, child code has the OS privileges of the bridge account; Local-MCP-Bridge is not a child-process filesystem/network sandbox.

## Managed jobs and persistence

Managed jobs use opaque 128-bit IDs. Raw argv is never persisted. Durable records contain bounded safe metadata plus sanitized/redacted output.

Persistent state is untrusted on recovery. Records are revalidated against the current registry, current `execute` permission, current executable allowlist, field schema, path policy, status/timestamps/sizes, and output limits. Previously nonterminal jobs become `interrupted` after restart.

The bridge deliberately does not persist a PID for blind reattachment because PID reuse could target an unrelated process. POSIX cancellation/timeout can target the launched process group; portable Windows behavior guarantees the direct child but does not claim complete descendant-tree containment.

## Git synchronization boundary

Git synchronization is a dedicated capability rather than caller-controlled Git execution. The MCP caller supplies only a logical `project_id`; remote name, branch, HTTPS URL, timeout, and output ceiling come from ignored local policy.

The exposed surface is limited to:

- `git_status(project_id)`;
- `git_fetch(project_id)`;
- `git_sync_fast_forward(project_id)`.

There is no push, reset, clean, checkout/switch, rebase, force operation, arbitrary URL/refspec, arbitrary Git argv, branch/tag deletion, or history-rewrite primitive.

Before exposed operations, repository layout, exact worktree root, critical Git metadata, local repository configuration, and the configured remote URL are validated. High-risk repository configuration such as aliases, credentials, hooks, filters, includes, URL rewrites, submodules, merge drivers, HTTP/protocol overrides, SSH commands, and external helpers is rejected.

Git runs shell-free with system/global config and interactive authentication disabled, HTTPS-only protocol policy, bounded output/runtime, and per-project serialization. `git_sync_fast_forward` requires the configured branch, a clean tree, trusted fetch, post-fetch revalidation, ancestry proof, `merge --ff-only --no-overwrite-ignore`, and final verification that `HEAD` equals the previously fetched object ID.

`git_fetch` and `git_sync_fast_forward` require successful persistent pre-operation audit events in the configured runtime.

## Audit boundary

The configured runtime enables a local fixed-schema `AuditLogger` under `runtime/audit/` by default. An explicit `LOCAL_MCP_BRIDGE_AUDIT_DIR` override must be absolute.

Audit records contain only bounded metadata: schema/timestamp, opaque session/event identifiers, sequence, fixed action/outcome labels, optional validated project ID, and allowlisted scalar/enumerated details. The schema intentionally cannot accept raw argv, output, search queries, caller path text, file contents, executable targets, Git URLs, credentials, environment values, transport headers, or arbitrary exception text.

Audit paths reject unsafe redirection and hard-linked/non-regular files. New active files use exclusive creation; existing files are identity-checked around open; `O_NOFOLLOW` is used where available; successful writes are bounded and fsynced. POSIX state uses private permissions as defense in depth.

Configured-runtime high-impact operations require a successful `attempt` event before effect:

- `run_process`;
- `start_job`;
- `cancel_job`;
- `git_fetch`;
- `git_sync_fast_forward`.

If the audit sink is unavailable, those operations fail closed. Completion logging is non-transactional: once an OS/Git effect occurred, an audit failure marks the logger unhealthy rather than falsely claiming the effect failed. Read-only operations preserve their true result while `health_check()` reports degraded audit health.

Raw audit files are not exposed through MCP.

## Phase 9 remote transport boundary

### Separate explicit entry point

The normal command remains local stdio:

```text
python -m local_mcp_bridge
```

Remote mode uses the separate `local-mcp-bridge-remote` entry point. Importing the reusable server or remote entry-point module does not open a socket. The remote `main()` first validates remote policy and bearer material, then builds the same configured runtime composition used by stdio, then starts the listener.

### Loopback-only listener and external TLS

The Local-MCP-Bridge process may bind only to `127.0.0.1` or `::1`. That restriction is checked both while parsing remote policy and again at the transport boundary.

Public reachability is provided by a separately managed tunnel/reverse proxy:

```text
remote MCP client
    -> HTTPS
    -> tunnel/reverse proxy
    -> loopback HTTP
    -> Local-MCP-Bridge
```

The configured public endpoint must use HTTPS, a DNS hostname, and exactly `/mcp`. Credentials, query strings, fragments, localhost names, and IP literals are rejected. The public URL determines Host/Origin policy but never changes the local socket bind.

The bridge does not launch Cloudflare Tunnel, ngrok, SSH, or another provider command and does not store provider credentials.

### Bearer authentication

Every Phase 9 HTTP request must contain exactly one:

```text
Authorization: Bearer <secret>
```

The secret comes only from `LOCAL_MCP_BRIDGE_REMOTE_TOKEN`, must contain 43–256 URL-safe visible ASCII characters, and is compared with `hmac.compare_digest`.

Missing, malformed, duplicate, or incorrect authorization receives a generic `401`. After successful verification, `Authorization` and `Proxy-Authorization` are removed from the downstream ASGI request scope before MCP dispatch. Uvicorn access logging is disabled and the remote policy does not accept the bearer value in YAML or URLs.

Authentication failures are deliberately not written to the persistent audit JSONL. Otherwise unauthenticated internet traffic could directly drive durable audit writes. Once authentication succeeds, MCP tool calls use the normal Phase 8 audit behavior.

The bearer token provides possession-based transport authentication only. It is not a per-user identity system and does not provide OAuth discovery, scopes, consent, refresh tokens, or delegated authorization. A client requiring standards-based OAuth needs a separate reviewed integration.

### Host, Origin, and proxy metadata

MCP SDK DNS-rebinding protection remains enabled. The configured public host/origin and explicit loopback development values form the Host/Origin allowlists. An invalid Host or Origin is rejected before normal MCP dispatch.

Proxy-forwarded metadata is trusted only from the configured loopback peer. A same-privilege local process can still connect from loopback or spoof local proxy metadata; Phase 9 does not claim containment of an already-compromised host.

### Remote resource limits

Remote transport adds bounded request/session/listener state:

- request body: 256 KiB default, 1 MiB hard maximum;
- legacy stateful session idle timeout: 300 seconds default, 1800 seconds hard maximum;
- legacy stateful sessions: 32 default, 256 hard maximum;
- Uvicorn concurrency ceiling: 64;
- listener backlog: 64;
- HTTP keep-alive timeout: 5 seconds.

These are application-level limits, not bandwidth, CPU, RAM, or provider-level DDoS controls. Provider-side rate limiting/firewalling remains useful.

## Shared runtime composition invariant

Stdio and remote transports converge on the same configured services:

```text
base project registry
    -> optional Git overlay
    -> persistent AuditLogger
    -> ExecutionService
    -> persistent JobManager
    -> create_mcp_server(...)
```

Remote reachability therefore cannot create a second, weaker filesystem/execution/job/Git authorization path. Possession of the bearer token gets a request to the MCP boundary; all existing project/tool policies still apply afterward.

## Runtime and test isolation

The pure `create_mcp_server(...)` factory is non-persistent by default and does not load machine-local project, Git, audit, job, or remote configuration.

Remote transport tests use an in-memory ASGI client and explicitly run the MCP Streamable HTTP session manager, so CI does not need a public tunnel or real listener. Runtime-isolation tests verify that importing the remote module is side-effect free and invalid remote policy fails only when the explicit remote command is invoked.

## Residual risk

Phases 5–9 are application-level security layers, not an OS sandbox, trusted network perimeter, or tamper-proof forensic system.

Important residual risks include:

- a stolen Phase 9 bearer token gives MCP endpoint reachability until token rotation/restart;
- a tunnel/reverse-proxy provider terminates public TLS and is therefore part of the transport trust chain;
- volumetric attacks can consume provider/network resources before bridge-level limits apply;
- a same/higher-privilege local actor can inspect/race/tamper with local process and runtime state;
- allowlisted executable/project code can access resources available to the bridge OS account;
- Git remains complex native software processing untrusted repository/network data;
- local audit records are not cryptographically signed or protected from a local administrator.

Direct filesystem write/delete/rename tools remain intentionally absent and require their own reviewed authorization/recovery policy before introduction.
