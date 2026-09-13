# Phase 10 / 10.5 Live Validation

## Result

**Status: complete on 2026-09-13.**

A real Claude custom connector successfully connected to Local-MCP-Bridge through the Phase 10.5 OAuth flow and completed the intended read-only MCP validation sequence against an authorized local project. This closes the live compatibility requirement and unblocks Phase 11.

## Validated path

```text
Claude custom connector
    -> OAuth + PKCE
    -> public HTTPS tunnel
    -> loopback Local-MCP-Bridge remote runtime
    -> MCP Streamable HTTP
    -> project authorization
    -> local read/search tools
```

The local Python listener remained loopback-only. Public reachability remained external to the bridge process.

## Preflight

Before involving Claude, the operator-side Phase 10 probe passed against the real public endpoint:

- Streamable HTTP transport succeeded;
- bridge health was `ok`;
- all 15 expected tools were discovered;
- no expected tool was missing;
- no unexpected tool was present.

## Compatibility finding

The first real Claude custom-connector attempt reached the bridge but could not use the Phase 9 static bearer setup through the web connector UI. The project did not weaken authentication to make the client connect. Phase 10.5 was inserted before Phase 11 to add OAuth compatibility.

## Live OAuth fixes

The real browser flow exposed two defects that were fixed before final validation:

1. Version `0.10.2` made consent response delivery retry-safe for a short bounded window while preserving single-use authorization-code exchange.
2. Version `0.10.3` corrected the consent page Content Security Policy so the browser could follow the already-validated callback to Claude.

After those fixes, Claude completed authorization and the connector displayed a successful connected state.

## Final read-only smoke sequence

The test project remained intentionally restricted:

```yaml
permissions:
  read: true
  search: true
  execute: false
  git: false
```

Claude then completed:

- `health_check` — status `ok`, server `Local MCP Bridge`, version `0.10.3`, one configured project, and healthy audit state;
- `list_projects` — discovered `local-mcp-bridge` with read/search enabled and execute/Git disabled;
- `get_project` — returned the same project permission state without exposing the absolute host root;
- `list_directory` — listed the authorized repository root and reported one restricted entry omitted;
- `read_file` — returned the first 30 lines of the non-sensitive `README.md`.

Runtime subsystem availability remained distinct from project authorization: execution and Git existed in the bridge but remained disabled for this project.

## Security observations

The validation preserved the intended boundaries:

- remote authentication remained enabled;
- the bridge listener remained loopback-only;
- the tunnel remained separately managed;
- the project stayed read/search-only;
- execute and Git stayed disabled at project scope;
- absolute local project roots were not returned in normal project metadata;
- restricted filesystem entries continued to be filtered.

## Phase decision

- **Phase 10: complete.** Public endpoint preflight, real Claude compatibility testing, and the read-only MCP smoke sequence are recorded.
- **Phase 10.5: complete.** OAuth compatibility is implemented, CI-tested, and proven against the real Claude custom connector.
- **Phase 11: unblocked and next.** Capabilities must still be enabled incrementally; a successful remote login never overrides project-level permissions.

See [`roadmap.md`](roadmap.md) for the staged Phase 11 progression.
