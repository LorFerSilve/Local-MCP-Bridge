# Architecture

## Goal

Local-MCP-Bridge is a narrow, policy-enforced boundary between an MCP client and explicitly authorized local development resources. The model is never a trusted security principal. Repository content, MCP input, paths, process output, persisted state, Git metadata, audit state, HTTP requests, and proxy metadata are all treated as potentially hostile.

Phase 9 changes **reachability**, not authority. Remote HTTP reaches the same server and services used by local stdio.

## High-level architecture

```text
                         local client
                             |
                             | stdio
                             v
                      +-------------+
                      | runtime.py  |
                      +------+------+ 
                             |
                             |
remote MCP client            |
      |                      |
      | HTTPS + Bearer       |
      v                      |
external tunnel/proxy        |
      |                      |
      | loopback HTTP        |
      v                      |
+----------------------+     |
| remote_transport.py  |     |
| - bearer gate        |     |
| - Host/Origin policy |     |
| - body/session caps  |     |
+----------+-----------+     |
           |                 |
           v                 v
      +--------------------------+
      | runtime_composition.py   |
      | - base project config    |
      | - Git policy overlay     |
      | - persistent JobManager  |
      | - persistent AuditLogger |
      +------------+-------------+
                   |
                   v
      +--------------------------+
      | create_mcp_server(...)   |
      | same MCP tool surface    |
      +------------+-------------+
                   |
       +-----------+-----------+------------------+
       |                       |                  |
       v                       v                  v
+-------------+        +---------------+    +-------------+
| Filesystem  |        | Execution /   |    | GitService  |
| + PathGuard |        | JobManager    |    | constrained |
+-------------+        +---------------+    +-------------+
       |                       |                  |
       v                       v                  v
 local project             subprocess          git process
 filesystem               (no shell)        (fixed policy)

security-relevant tool activity
       |
       v
+-------------------+
| AuditLogger       |
| fixed schema      |
| bounded JSONL     |
| rotation + fsync |
+---------+---------+
          |
          v
   runtime/audit/
```

The tunnel/reverse proxy is deliberately **outside** Local-MCP-Bridge. The project does not launch provider binaries, accept arbitrary tunnel argv, store provider credentials, or manage public DNS/TLS.

## Active Phase 9 modules

```text
src/local_mcp_bridge/
├── audit.py
├── config.py
├── git_config.py
├── jobs.py
├── registry.py
├── remote_config.py
├── remote_runtime.py
├── remote_transport.py
├── runtime.py
├── runtime_composition.py
├── server.py
├── security/
│   └── paths.py
└── tools/
    ├── execution.py
    ├── filesystem.py
    ├── git_repository.py
    ├── git_runner.py
    └── git_service.py
```

`server.py` remains the pure MCP factory. Constructing it does not read machine-local configuration or create persistent state.

`runtime_composition.py` is the shared configured composition root. It loads the base project registry, applies the optional Git overlay, creates the persistent Phase 8 audit logger, installs the persistent job manager, and injects those same services into `create_mcp_server(...)`.

`runtime.py` is the default local stdio entry point. It uses `runtime_composition.py` and does not read Phase 9 remote policy.

`remote_config.py` parses the separate ignored Phase 9 policy. Remote exposure is therefore not implied by project authorization. It validates explicit enablement, loopback bind, stable HTTPS public `/mcp` URL, and bounded request/session settings. The bearer secret is never accepted from YAML.

`remote_runtime.py` is an explicit network entry point. It validates remote policy and bearer material first, then builds the same configured runtime composition used by stdio, then starts the loopback HTTP listener.

`remote_transport.py` wraps the MCP Streamable HTTP ASGI app with bearer authentication, credential-header scrubbing, DNS-rebinding Host/Origin policy, request/session limits, and hardened Uvicorn listener options.

`audit.py`, `jobs.py`, `tools/execution.py`, the filesystem/PathGuard modules, and the Git modules preserve their Phase 3–8 responsibilities unchanged.

## Runtime composition invariant

Both transports converge on exactly one composition path:

```text
load_runtime_registry()
        |
        v
load_runtime_git_registry(...)
        |
        v
AuditLogger(runtime/audit)
        |
        v
ExecutionService(registry)
        |
        v
JobManager(registry, execution, runtime/jobs)
        |
        v
create_mcp_server(
    registry,
    execution_service=execution,
    job_manager=jobs,
    audit_logger=audit,
)
```

Remote mode does **not** instantiate a second server with weaker defaults. That invariant prevents transport selection from bypassing authorization, persistent-job policy, or fail-closed audit gates.

## Project identity boundary

Clients address logical IDs and project-relative paths:

```text
read_file(project_id="example-project", path="src/main.py")
run_process(project_id="example-project", executable="pytest", args=["-q"], cwd=".")
start_job(project_id="example-project", executable="pytest", args=["-q"], cwd=".")
git_status(project_id="example-project")
git_fetch(project_id="example-project")
git_sync_fast_forward(project_id="example-project")
```

Clients do not choose a host root, job-state directory, audit directory, executable path, Git binary, remote URL, remote name, branch, refspec, protocol, raw Git arguments, listener address, tunnel command, or tunnel credentials per call.

## Filesystem and execution boundaries

Filesystem reads pass lexical validation, sensitive-path filtering, non-following component inspection, canonical root containment, file-type/link checks, identity revalidation, and bounded I/O.

Process and job working directories reuse `PathGuard`. Executable selection is by local alias/allowlist, not caller-supplied host path. Processes launch with `asyncio.create_subprocess_exec`, disabled stdin, minimal environment, bounded argv/runtime/output, and no generic shell primitive.

Managed jobs use opaque IDs, bounded persistent records, no raw argv persistence, startup reauthorization, and conservative `interrupted` recovery rather than persisted-PID reattachment.

## Git boundary

Git synchronization is a separate service rather than generic process execution. Local ignored policy pins one remote name, branch, HTTPS URL, timeout, and output ceiling. Before exposed operations, the bridge validates repository layout, exact worktree identity, local Git configuration, and trusted remote match.

The public Git semantics remain:

```text
git_status(project_id)
git_fetch(project_id)
git_sync_fast_forward(project_id)
```

Synchronization requires the configured branch, a clean tree, trusted fetch, post-fetch revalidation, ancestry proof, `merge --ff-only --no-overwrite-ignore`, and final target verification. There is no push/reset/clean/rebase/force/history-rewrite API.

## Phase 8 audit flow

```text
read-only operation
    -> perform operation
    -> best-effort completion audit
       -> failure marks audit unhealthy

high-impact operation
    -> strict persistent attempt audit
       -> failure refuses operation before effect
    -> perform effect
    -> non-transactional completion audit
       -> failure marks audit unhealthy
```

Strict pre-audit applies to `run_process`, `start_job`, `cancel_job`, `git_fetch`, and `git_sync_fast_forward`.

Audit records are fixed-schema metadata only. Raw argv, output, search queries, caller path text, file content, executable targets, Git URLs, credentials, environment values, headers, and arbitrary exception strings cannot be placed into the general audit detail map.

## Phase 9 transport flow

```text
remote client
    |
    | HTTPS (external ingress)
    v
separately managed tunnel/reverse proxy
    |
    | HTTP to loopback only
    v
127.0.0.1:<configured port>
    |
    v
BearerAuthMiddleware
    |
    +-- missing/malformed/duplicate/wrong token -> 401
    |
    v
strip Authorization + Proxy-Authorization
    |
    v
MCP SDK request-body limit
    |
    v
MCP SDK Host / Origin validation
    |
    +-- invalid Host -> reject
    +-- invalid Origin -> reject
    |
    v
Streamable HTTP session manager
    |
    v
same MCP server/tool layer as stdio
```

### Explicit enablement

Remote startup requires `config/remote.local.yaml` (or an explicit override) with `enabled: true`. The base project config cannot enable network exposure. The default `python -m local_mcp_bridge` command remains stdio.

### Loopback-only listener

Policy parsing accepts only `127.0.0.1` or `::1`. `remote_transport.py` checks the same invariant again before creating the ASGI app or starting Uvicorn. A malformed `RemoteSettings` object therefore cannot trivially bypass the parser and bind publicly.

### Public URL versus local bind

The configured `public_url` is the **client-facing** stable HTTPS URL and must end exactly in `/mcp`. It is used to derive the public Host/Origin allowlist. It does not control the socket bind.

The Python listener remains loopback HTTP. TLS is terminated by the separately managed ingress. Plaintext LAN/public deployment is not a supported architecture.

### Bearer credential

`LOCAL_MCP_BRIDGE_REMOTE_TOKEN` provides one process-memory pre-shared token. It must be 43–256 URL-safe ASCII characters. Requests must carry exactly one `Authorization: Bearer ...` header.

The credential is compared with `hmac.compare_digest`. After success, authorization/proxy-authorization headers are removed before MCP dispatch. Authentication failures produce a generic no-store response and are not persisted to the audit log, preventing unauthenticated internet traffic from becoming a direct disk-write primitive.

This is transport authentication, not per-user OAuth authorization. Local MCP project/tool policy remains authoritative after authentication.

### DNS rebinding and proxy metadata

MCP SDK DNS-rebinding protection remains enabled with configured Host/Origin allowlists. Proxy-forwarded metadata is accepted by Uvicorn only from the configured loopback peer.

A same-privilege local attacker may still connect from loopback or spoof forwarded metadata; Phase 9 does not claim host compromise containment.

## Remote resource boundaries

Phase 9 adds:

- request body: 256 KiB default, 1 MiB hard maximum;
- legacy stateful session idle timeout: 300 seconds default, 1800 seconds hard maximum;
- legacy stateful sessions: 32 default, 256 hard maximum;
- Uvicorn HTTP concurrency: 64;
- listener backlog: 64;
- keep-alive timeout: 5 seconds.

Uvicorn access logs and the server-identification header are disabled. These limits reduce application-level resource abuse but are not network bandwidth/CPU/RAM quotas. Public ingress rate limiting remains a separate operational control.

## Test/runtime isolation

```text
pytest
  -> pure server factory / temporary services
  -> ASGITransport for Phase 9 network tests
  -> no real public listener or tunnel

stdio runtime
  -> runtime_composition.py
  -> server
  -> stdio

remote runtime
  -> validate remote local policy + token
  -> runtime_composition.py
  -> authenticated Streamable HTTP app
  -> loopback Uvicorn listener
```

Importing `local_mcp_bridge.server` remains independent of all machine-local configuration. Importing `local_mcp_bridge.remote_runtime` is also side-effect free; missing remote configuration fails only when the explicit remote `main()` is executed.

Tests cover remote-policy parsing, unsafe URL/bind rejection, token policy, missing/wrong/duplicate bearer requests, credential-header scrubbing, Host/Origin rejection, request-size rejection, Uvicorn hardening, remote-runtime import isolation, and an authenticated end-to-end Streamable HTTP `health_check` round trip.

## Security boundary and residual risk

Phases 5–9 remain application-level controls, not an OS sandbox. A stolen Phase 9 bearer token gives endpoint reachability until rotation/restart, though normal project/tool authorization still applies. The external tunnel/provider terminates public TLS and is part of the transport trust chain. A compromised same-privilege local actor remains outside the containment claim.

Phase 9 does not provide OAuth discovery, per-user identity, scopes, token refresh, browser CORS, automatic tunnel management, or provider credential storage. Phase 10 validates the intended target client and determines whether a standards-based OAuth integration is necessary.

Long-term connector modularity remains documented separately in `future_modularity_expansion_proposal.md`.
