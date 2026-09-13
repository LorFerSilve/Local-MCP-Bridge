# Phase 10 — Lightweight Claude MCP Validation

## Goal

Phase 10 validates the Phase 9 remote endpoint against the requirements of the intended lightweight Claude target without weakening the bridge simply to make a client connect.

The phase is deliberately split into three proofs:

1. **Bridge transport proof** — the configured public HTTPS endpoint can complete an authenticated Streamable HTTP MCP round trip and exposes exactly the expected tool surface.
2. **Claude product compatibility proof** — determine whether the intended Claude product can provision the same authentication method and reach the endpoint from Anthropic infrastructure.
3. **Minimal live smoke proof** — once connected, use only harmless/read-only calls to confirm that Claude can discover and invoke the bridge.

The first proof is automated in this repository. The second and third require a real Claude account because they depend on Anthropic's live product UI and cloud connector infrastructure.

## Current target-client facts

This phase records the target behavior as documented by Anthropic on **2026-09-13**. These are external product facts and may change independently of this repository.

Anthropic currently documents that:

- custom connectors using remote MCP are available on Claude Free, Pro, Max, Team and Enterprise;
- Free users are limited to one custom connector;
- custom remote connectors originate from Anthropic's cloud rather than from the user's local machine;
- therefore the MCP endpoint must be reachable from the public internet;
- the Claude custom-connector setup flow asks for a remote MCP URL and optionally OAuth client credentials;
- the documented Claude UI flow does not expose a field for an arbitrary static `Authorization: Bearer ...` header;
- the Claude API MCP connector, by contrast, supports an `authorization_token` field for remote MCP servers and supports Streamable HTTP.

Primary references:

- <https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp>
- <https://support.claude.com/en/articles/11176164-use-connectors-to-extend-claude-s-capabilities>
- <https://docs.anthropic.com/en/docs/agents-and-tools/mcp-connector>

The consequence for Local-MCP-Bridge is important: **Phase 9's transport and bearer protocol are compatible with a normal MCP client and with the Claude API connector model, but the current Claude web-app custom-connector UI does not document a way to provision the Phase 9 pre-shared bearer secret directly.** A real Claude UI attempt is still required before that mismatch is treated as final product behavior.

## Automated compatibility probe

Phase 10 adds:

```text
local-mcp-bridge-claude-probe
```

The command deliberately accepts no URL, token or arbitrary headers from command-line arguments. It loads the same ignored Phase 9 remote policy and the same process-local bearer token used by the remote runtime:

```text
config/remote.local.yaml
LOCAL_MCP_BRIDGE_REMOTE_TOKEN
```

It then connects to the configured public HTTPS `.../mcp` endpoint using Streamable HTTP and validates:

- bearer-authenticated MCP initialization succeeds;
- `health_check()` succeeds and reports `status="ok"`;
- the public MCP tool surface contains exactly the expected Phase 9/10 tools;
- no unexpected tool has appeared;
- the report itself contains no endpoint, bearer token, response body or exception string.

Expected successful output is a small fixed-schema JSON object similar to:

```json
{"authentication":"pre-shared-bearer","expected_tool_count":15,"health_status":"ok","missing_tools":[],"ok":true,"tool_count":15,"transport":"streamable-http","unexpected_tool_count":0}
```

The command exits:

- `0` when the endpoint passes the complete probe;
- `1` when MCP responds but the health/tool contract is not the expected contract;
- `2` when local remote configuration/authentication is invalid or the remote probe cannot be completed.

Network/third-party exception strings are intentionally suppressed from standard output so a diagnostic path cannot accidentally reflect endpoint/auth material into copied logs.

## What the probe proves

A green probe proves:

```text
this machine
    -> configured public HTTPS endpoint
    -> tunnel / reverse proxy
    -> Phase 9 bearer middleware
    -> MCP Streamable HTTP
    -> health/tool discovery
```

This is a useful precondition for Claude testing because it catches tunnel routing, TLS, bearer, Host validation, MCP session and tool-surface problems before Claude is involved.

## What the probe does not prove

It does **not** prove:

- that Anthropic's cloud can reach the endpoint;
- that a firewall allows Anthropic-originated traffic;
- that Claude's current UI can provision a static bearer secret;
- that OAuth discovery/authorization works;
- that Claude chooses the correct tool from natural language;
- that execution or Git mutation should be enabled for the smoke test.

No CI job contacts a real public endpoint. CI tests the probe against an in-memory ASGI server only.

## Lightweight Claude Free smoke test

Do not disable Phase 9 authentication for this test.

### 1. Prepare the bridge

On the machine hosting Local-MCP-Bridge:

```powershell
git switch main
git pull origin main
python -m pip install -e ".[dev]"
```

Configure the existing ignored files normally:

```text
config/config.yaml
config/remote.local.yaml
```

For the first Claude test, prefer a project with only:

```yaml
permissions:
  read: true
  search: true
  execute: false
  git: false
```

This keeps the first real remote-client interaction read-only.

Set a fresh bearer token in the process environment, start the remote runtime, then start the separately managed HTTPS tunnel/reverse proxy.

### 2. Run the Phase 10 preflight

In a second terminal with the same remote token available:

```powershell
local-mcp-bridge-claude-probe
```

Do not continue until it returns `"ok":true`.

### 3. Add the connector in Claude

Anthropic currently documents custom remote MCP connectors under:

```text
Customize -> Connectors -> Add custom connector
```

Enter the exact public HTTPS MCP URL from the ignored remote policy.

For a Claude Free account, Anthropic currently documents a limit of one custom connector.

### 4. Record the authentication outcome

There are two acceptable Phase 10 outcomes:

#### Outcome A — Claude can provision the bearer gate

If the live product provides a supported way to send the Phase 9 bearer token and connects successfully, proceed to the harmless tool smoke test below.

#### Outcome B — Claude requires its documented OAuth flow

If the UI has no static bearer option and rejects the connector because the server does not implement OAuth discovery/authorization, **do not weaken or remove Phase 9 authentication**.

That result is a successful Phase 10 compatibility finding: the bridge transport works, but Claude UI authentication requires a standards-based OAuth adapter before Phase 11 can use Claude remotely.

A separate security-reviewed OAuth phase should then be inserted before real autonomous project integration.

## Harmless live tool smoke test

If the connector authenticates, enable it only for one test conversation and keep the authorized project read-only.

Use a minimal sequence:

1. Ask Claude whether the connector is available.
2. Ask it to call `health_check`.
3. Ask it to call `list_projects`.
4. Ask it for metadata for one known safe project with `get_project`.
5. Ask it to list one harmless directory.
6. Ask it to read one known non-sensitive text file.

The validation passes only when the returned information matches the local policy and no host root, bearer token, audit contents or other local-only state appears.

Do not enable `execute` or Git merely to prove the connector works. Those capabilities already have their own tested policy gates and are exercised later during Phase 11 integration.

## Phase 10 completion gate

Phase 10 is complete only after all of the following are true:

- repository probe implementation is merged and CI/security baseline are green;
- the real public endpoint passes `local-mcp-bridge-claude-probe`;
- a real Claude custom-connector attempt has been made;
- the result is recorded as either:
  - **compatible** — Claude connects through the existing Phase 9 bearer model and completes the read-only smoke sequence; or
  - **auth gap confirmed** — Claude UI requires OAuth and the project explicitly blocks Phase 11 until an OAuth integration phase is completed.

This definition makes incompatibility a valid validation result. It does not turn a client limitation into a reason to remove authentication.

## Security invariants

Phase 10 adds no new MCP capability. The probe is an operator-side diagnostic command, not an MCP tool.

It must preserve these properties:

- no bearer token in command-line arguments;
- no arbitrary caller URL;
- no redirect following;
- no token/URL/response-body/exception-text output;
- no automatic Claude API calls;
- no Anthropic API key requirement;
- no automatic tunnel provisioning;
- no authentication downgrade for client compatibility;
- no persistent copy of the bearer token;
- no execution/Git enablement required for the live smoke test.

## Next decision

If Claude Free can authenticate to the Phase 9 endpoint, Phase 11 can proceed with a deliberately limited real-project integration.

If Claude Free requires OAuth, insert an OAuth authorization integration phase before Phase 11. That phase must be treated as a new security boundary rather than a small transport tweak.
