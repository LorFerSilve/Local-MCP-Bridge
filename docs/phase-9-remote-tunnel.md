# Phase 9 — Remote / Tunnel Integration

## Goal

Phase 9 makes the already-authorized bridge reachable through MCP Streamable HTTP without turning the Local-MCP-Bridge process into a public network service.

The design deliberately separates two responsibilities:

```text
remote MCP client
      |
      | HTTPS + Authorization: Bearer <secret>
      v
trusted tunnel / reverse proxy
      |
      | loopback HTTP only
      v
127.0.0.1:8765
      |
      v
Phase 9 bearer + Host/Origin gate
      |
      v
MCP Streamable HTTP
      |
      v
same Phase 0–8 server/services
```

The bridge never binds `0.0.0.0`, a LAN interface, or a public interface. It also never starts a tunnel process or accepts a caller-supplied tunnel command. Tunnel lifecycle, provider credentials, DNS and TLS termination remain outside the bridge process.

## Explicit opt-in

The normal entry point remains local stdio:

```powershell
python -m local_mcp_bridge
```

Remote transport uses a different command:

```powershell
local-mcp-bridge-remote
```

That command fails closed unless all of the following are true:

1. an explicit local remote-policy file exists;
2. `enabled: true` is present;
3. the local listener is `127.0.0.1` or `::1`;
4. a stable HTTPS public MCP URL ending exactly in `/mcp` is configured;
5. `LOCAL_MCP_BRIDGE_REMOTE_TOKEN` contains a valid high-entropy bearer secret;
6. the ordinary project/Git/job/audit runtime composition also validates.

Importing the reusable server or the remote entry-point module does not itself open a socket.

## Local remote policy

Copy the tracked template:

```powershell
Copy-Item config/remote.example.yaml config/remote.local.yaml
```

The real `config/remote.local.yaml` is ignored by Git.

Example:

```yaml
enabled: true

bind:
  host: "127.0.0.1"
  port: 8765

public_url: "https://mcp.example.com/mcp"

limits:
  max_request_body_bytes: 262144
  session_idle_timeout_seconds: 300
  max_sessions: 32
```

`LOCAL_MCP_BRIDGE_REMOTE_CONFIG` may select another policy file. `LOCAL_MCP_BRIDGE_REMOTE_PUBLIC_URL` may override only the public URL. Both still pass the same validation.

The public URL must:

- use `https`;
- use an ordinary DNS hostname rather than an IP literal or `localhost`;
- have the exact path `/mcp`;
- contain no username/password, query string or fragment;
- use a syntactically valid port if a non-default port is present.

The local bind is independently restricted to loopback. A valid public URL can therefore never cause the Python process itself to listen publicly.

## Bearer authentication

The bridge uses a pre-shared bearer secret as a transport gate for Phase 9. The secret is not accepted in YAML and is not persisted by the bridge.

Generate one for the current PowerShell process, for example:

```powershell
$env:LOCAL_MCP_BRIDGE_REMOTE_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(48))"
```

The token policy requires 43–256 URL-safe visible ASCII characters. Every HTTP request must contain exactly one header:

```text
Authorization: Bearer <secret>
```

The comparison uses `hmac.compare_digest`. Missing, malformed, duplicate or incorrect authorization produces a small `401` response. The validated `Authorization` header, and any `Proxy-Authorization` header, are removed from the ASGI request scope before MCP dispatch so tool/context code cannot accidentally reflect those credentials.

Authentication failures are not written to the durable Phase 8 audit log. This avoids giving unauthenticated internet traffic a direct disk-churn primitive. Once authentication succeeds, ordinary MCP tool activity is covered by the same Phase 8 audit policy as stdio.

## Authentication model and scope

The Phase 9 bearer mechanism is deliberately narrower than a full OAuth authorization server. It provides possession-based authentication for one locally managed bridge instance. It does **not** provide:

- OAuth discovery;
- token refresh;
- per-user identities;
- per-client scopes;
- delegated consent;
- a remote identity provider.

Possession of the bearer secret authorizes access to the MCP endpoint, but it does not bypass Local-MCP-Bridge authorization. Project IDs, read/search permissions, executable aliases, job policy, Git policy and audit fail-closed gates still apply after transport authentication.

A client that mandates standards-based OAuth rather than accepting a configured bearer header will require a later auth-provider integration. Phase 10 validates actual target-client compatibility before such complexity is added.

## DNS-rebinding, Host and Origin policy

MCP SDK DNS-rebinding protection remains enabled.

The configured public hostname and loopback development addresses form a narrow Host allowlist. Browser-style requests carrying an `Origin` header are accepted only for the configured public HTTPS origin or explicitly allowed local development origins. Untrusted hosts/origins are rejected before MCP dispatch.

This layer is separate from bearer authentication. Knowing the hostname is not authentication, and knowing the bearer secret does not disable Host/Origin checks.

## HTTPS and tunnel responsibility

The public hop must be HTTPS:

```text
client -- HTTPS --> tunnel/proxy -- loopback HTTP --> bridge
```

Plaintext between the tunnel process and bridge is permitted only because the listener is loopback-only. Phase 9 intentionally does not support plaintext LAN/public deployment.

The tunnel or reverse proxy must forward the configured MCP endpoint to the local listener. It may add its own access-control layer, but tunnel authentication is defense in depth and never replaces the bridge bearer token.

The bridge is provider-agnostic. Cloudflare Tunnel, ngrok, an SSH/reverse proxy, a private ingress, or another provider can be used only if it preserves the security assumptions above. Provider credentials and configuration remain outside tracked project files.

## Forwarded headers

Uvicorn accepts proxy headers only from the configured loopback peer address. This allows a local tunnel/reverse proxy to convey the external scheme/client metadata while refusing such metadata from non-loopback peers.

A malicious process already running under an equivalent local account can still connect from loopback and spoof forwarded metadata. Phase 9 does not claim to defend against a host that is already compromised at the same OS privilege level.

## Resource ceilings

Remote transport adds denial-of-service ceilings before/around MCP dispatch:

- request body: 256 KiB default, 1 MiB hard maximum;
- legacy stateful session idle timeout: 300 seconds default, 1800 seconds hard maximum;
- legacy stateful sessions: 32 default, 256 hard maximum;
- HTTP concurrency: 64;
- listener backlog: 64;
- keep-alive timeout: 5 seconds.

Modern MCP requests use the SDK's current Streamable HTTP semantics; the legacy-session limits remain important for compatible older sessionful clients.

These controls bound bridge-level state and parsing pressure. They are not bandwidth, CPU, RAM or network quotas at the operating-system or tunnel-provider layer.

## Runtime composition invariant

Both transports use the same configured composition:

```text
base project registry
      + Git overlay
      + ExecutionService
      + persistent JobManager
      + Phase 8 AuditLogger
      v
create_mcp_server(...)
```

The remote entry point does not construct a second, weaker server. It first validates remote policy/authentication material, then reuses the same runtime composition as stdio.

This means adding a tunnel does not add a new filesystem, execution, job or Git capability. It changes reachability only.

## Logging and secret handling

The bearer secret must never appear in:

- tracked YAML;
- `.env` files committed to Git;
- MCP tool results;
- audit JSONL;
- command arguments passed through MCP;
- tunnel URLs;
- query strings;
- routine HTTP access logs.

Phase 9 disables Uvicorn access logging and server-identification headers. Errors returned by the authentication middleware are intentionally generic.

Environment variables are still process-local configuration, not a hardware-backed secret store. Operators should rotate the bearer secret after suspected exposure and should use OS/service-manager secret facilities for unattended deployments.

## Threats and residual risk

### Stolen bearer secret

A stolen token lets the holder reach the remote MCP endpoint until the bridge is restarted with a new token. All local project/tool authorization still applies, but the attacker effectively has the same remote MCP reachability as the intended client.

### Compromised tunnel provider or public ingress

The ingress can observe/modify traffic after TLS termination and can forward arbitrary requests to loopback. It still needs the bridge bearer secret to pass the application gate unless the secret was also exposed to that provider.

### Same-privilege local attacker

A same-privilege local process can connect to loopback, inspect process state depending on OS policy, race local files and potentially tamper with runtime state. Phase 9 does not transform the bridge into an OS sandbox.

### Brute force and volumetric traffic

A high-entropy token makes online guessing impractical, and the bridge adds local concurrency/body/session ceilings. A public tunnel can still be subjected to volumetric traffic before it reaches the local listener. Provider-side rate limiting/firewalling remains recommended.

### OAuth-only client compatibility

Pre-shared bearer authentication is intentionally simple and auditable, but not every remote MCP client necessarily supports arbitrary bearer headers. Phase 10 tests the intended client before an OAuth layer is considered.

## Non-goals

Phase 9 does not add:

- direct public socket binding;
- automatic tunnel creation;
- tunnel provider API credentials;
- arbitrary proxy/tunnel command execution;
- OAuth authorization-server behavior;
- multi-user identities or scopes;
- browser CORS support;
- remote audit-log reading;
- new filesystem/process/Git permissions.

Those would require separate reviewed security boundaries rather than being implied by remote reachability.
