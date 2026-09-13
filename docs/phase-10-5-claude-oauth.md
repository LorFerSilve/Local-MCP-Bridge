# Phase 10.5 — Claude OAuth Compatibility

## Why this phase exists

Phase 10 proved the complete pre-shared-bearer transport path with the real public endpoint:

```text
operator probe -> public HTTPS tunnel -> loopback bridge -> bearer gate -> MCP -> 15 tools
```

The live Claude custom-connector attempt then failed at sign-in registration because the
Claude web product could not provision the Phase 9 static bearer credential. Authentication
was **not** disabled to make the client connect. Phase 10 therefore completed with an
**auth-gap confirmed** result and inserted this OAuth phase before Phase 11.

Phase 10.5 adds a standards-based OAuth authorization/resource-server boundary specifically
for the remote runtime while preserving the existing Phase 9 bearer mode as the default.

## Authentication modes

Remote authentication is selected only through the process environment:

```text
LOCAL_MCP_BRIDGE_REMOTE_AUTH_MODE=pre_shared_bearer
```

or:

```text
LOCAL_MCP_BRIDGE_REMOTE_AUTH_MODE=oauth
```

If the variable is absent, `pre_shared_bearer` remains the default. Merely defining OAuth
client credentials does not switch authentication modes.

The ordinary stdio runtime is unchanged.

## Pre-shared bearer mode

The original Phase 9 behavior remains available and unchanged:

```powershell
$env:LOCAL_MCP_BRIDGE_REMOTE_AUTH_MODE = "pre_shared_bearer"
$env:LOCAL_MCP_BRIDGE_REMOTE_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(48))"
local-mcp-bridge-remote
```

The remote transport still fails closed if the bearer token is absent or invalid.

## OAuth mode

OAuth mode requires a single preconfigured confidential client. The client ID and client
secret are process-local environment values and are never accepted from tracked YAML:

```powershell
$env:LOCAL_MCP_BRIDGE_REMOTE_AUTH_MODE = "oauth"
$env:LOCAL_MCP_BRIDGE_OAUTH_CLIENT_ID = python -c "import secrets; print(secrets.token_urlsafe(24))"
$env:LOCAL_MCP_BRIDGE_OAUTH_CLIENT_SECRET = python -c "import secrets; print(secrets.token_urlsafe(48))"
Remove-Item Env:LOCAL_MCP_BRIDGE_REMOTE_TOKEN -ErrorAction SilentlyContinue
```

Do not paste the client secret into logs, issues, commits, chat transcripts, or shell history.
The operator must provision the generated Client ID and Client Secret directly into Claude's
custom-connector advanced OAuth settings.

The only registered redirect URI is:

```text
https://claude.ai/api/mcp/auth_callback
```

The confidential client uses `client_secret_post` at the token endpoint. Dynamic client
registration is disabled; this prevents arbitrary internet clients from registering
additional OAuth applications against the locally hosted authorization server.

## OAuth protocol surface

When OAuth mode is selected, the MCP SDK co-hosts the standards endpoints on the same
configured public HTTPS origin:

```text
/.well-known/oauth-protected-resource/mcp
/.well-known/oauth-authorization-server
/authorize
/token
/revoke
/oauth/consent
/mcp
```

`/mcp` remains the protected MCP resource. The SDK handles protected-resource metadata,
authorization-server metadata, client authentication, PKCE validation, authorization-code
validation, scope validation, resource validation and bearer-token enforcement.

Local-MCP-Bridge supplies the provider state and the explicit human-consent page.

## Consent boundary

An authorization request creates a cryptographically random pending request identifier with
a five-minute lifetime. The browser is redirected to:

```text
/oauth/consent?request=<opaque-request-id>
```

The page shows that Claude is requesting bridge access and displays the exact callback
destination. The operator can select **Allow** or **Deny**.

The consent page:

- accepts no password, API key, bearer token or OAuth client secret;
- uses an unguessable one-time request identifier;
- expires pending requests after five minutes;
- caps the number of pending authorizations;
- caps form-body size;
- uses `Cache-Control: no-store`;
- applies a restrictive Content Security Policy;
- disables framing and referrer leakage;
- emits an explicit HTTP 302 back to the registered Claude callback.

## Token lifecycle

OAuth state is deliberately process-local:

- authorization codes live for five minutes and are single-use;
- access tokens live for one hour;
- refresh tokens live for 30 days;
- refresh-token exchange rotates the refresh token;
- replay of the old refresh token fails;
- revocation removes the linked access/refresh pair;
- access tokens are bound to the configured public `/mcp` resource;
- bridge restart invalidates registrations/codes/tokens because no credential material is
  persisted.

The restart behavior is intentionally fail-closed. Persisted OAuth sessions can be added
later only with a separately reviewed encrypted storage design if operationally necessary.

## Existing transport hardening remains active

OAuth does not change the network exposure model:

- the Python listener remains loopback-only;
- a separate HTTPS tunnel/reverse proxy provides public reachability;
- `public_url` still must be HTTPS with an ordinary DNS hostname and exact `/mcp` path;
- Host and Origin allowlists remain derived from the configured public endpoint;
- DNS-rebinding protection remains enabled;
- request bodies, HTTP concurrency, backlog, keep-alive and MCP sessions remain bounded;
- tunnel credentials and lifecycle remain outside this repository.

OAuth also does not grant any project capability. Filesystem, execution, jobs and Git are
still independently gated by the existing project registry and policy overlays.

## First live Claude test

Keep the authorized project read-only:

```yaml
permissions:
  read: true
  search: true
  execute: false
  git: false
```

Then:

1. Start the existing HTTPS tunnel and make sure `config/remote.local.yaml` contains the
   current public `https://.../mcp` URL.
2. Set OAuth mode, generate the local Client ID/Secret, and start
   `local-mcp-bridge-remote` from the environment containing those values.
3. In Claude custom-connector settings, use the exact public `/mcp` URL and enter the local
   OAuth Client ID and Client Secret in Advanced settings.
4. Connect. The browser should reach the Local-MCP-Bridge consent page.
5. Verify the displayed redirect destination is exactly
   `https://claude.ai/api/mcp/auth_callback` and select **Allow**.
6. In a fresh Claude conversation with the connector enabled, perform only the read-only
   smoke sequence:
   - `health_check`;
   - `list_projects`;
   - `get_project` for the configured safe project;
   - `list_directory` on a harmless directory;
   - `read_file` on a known non-sensitive text file.

Phase 11 remains blocked until this real Claude OAuth smoke test succeeds.

## Test coverage

Repository tests exercise the OAuth boundary entirely against an in-memory ASGI app; CI
never contacts the real public tunnel or Claude. Coverage includes:

- explicit/default authentication-mode selection;
- required/bounded OAuth client credentials;
- protected-resource metadata;
- authorization-server metadata;
- absence of dynamic registration;
- unauthenticated MCP challenge with resource metadata;
- exact registered callback enforcement;
- PKCE authorization-code exchange;
- confidential `client_secret_post` authentication;
- OAuth access-token MCP `health_check` round trip;
- refresh-token rotation and replay rejection.

The live Claude result must still be recorded separately because CI cannot prove behavior
of Anthropic's current hosted connector implementation.
