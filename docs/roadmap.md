# Implementation Roadmap

This file records the high-level implementation sequence for Local-MCP-Bridge.

## Completed phases

- **Phase 0 — Repository and security baseline:** complete
- **Phase 1 — Minimal MCP server:** complete
- **Phase 2 — Project registry and allowed roots:** complete
- **Phase 3 — Safe filesystem tools:** complete
- **Phase 4 — Path/symlink/junction confinement:** complete
- **Phase 5 — Controlled process execution:** complete
- **Phase 6 — Persistent local job manager:** complete
- **Phase 7 — Git synchronization tools:** complete
- **Phase 8 — Audit logging and runtime hardening:** complete
- **Phase 9 — Remote/tunnel integration:** complete
- **Phase 10 — Lightweight Claude MCP validation:** complete
- **Phase 10.5 — Claude OAuth compatibility:** complete

Phase 10 first proved the public HTTPS/tunnel/MCP path with the repository probe. The real Claude custom connector then confirmed that the web product required OAuth rather than the Phase 9 static bearer model. Phase 10.5 added the OAuth boundary, and the real Claude connector subsequently completed OAuth authentication and read-only MCP calls successfully. See [`phase-10-live-validation.md`](phase-10-live-validation.md).

## Current phase

### Phase 11 — Real project integration and autonomous workflow testing

**Status: in progress.**

Phase 11 is active because the real Claude connector has successfully completed OAuth authentication and read-only end-to-end MCP calls against an authorized local project.

Progression:

1. **11.1 — Controlled real-project onboarding:** **in progress**. Repository-side onboarding validation is implemented; one selected local project must still pass the operator check and real Claude read/search smoke test. See [`phase-11-1-controlled-onboarding.md`](phase-11-1-controlled-onboarding.md).
2. **11.2 — Controlled execution enablement:** blocked on 11.1 completion.
3. **11.3 — Persistent job workflow:** blocked on 11.2 completion.
4. **11.4 — Read-only Git state validation:** blocked on the earlier Phase 11 gates.
5. **11.5 — Constrained Git synchronization:** blocked on 11.4 completion.
6. **11.6 — End-to-end development loop:** blocked on the preceding capability validations.
7. **11.7 — Phase 11 closeout:** final live evidence and residual-risk review.

## Phase 11 entry invariants

- Local HTTP remains loopback-only.
- Public reachability remains a separate HTTPS tunnel/reverse proxy.
- Remote authentication remains explicit and fail-closed.
- Project authorization remains deny-by-default.
- Local project roots remain server-side.
- There is no generic shell or arbitrary executable path.
- Execution remains allowlisted and bounded.
- Git remains a dedicated constrained service.
- High-impact operations retain fail-closed pre-operation auditing.
- Remote authentication never overrides project-level permissions.

## Operational note

The Phase 10 live proof used a temporary HTTPS tunnel suitable for validation. Longer-running Phase 11 use should prefer a stable operator-managed HTTPS endpoint while preserving the same loopback-only bridge listener and local authorization boundary.

## Later work

Multi-connector modularity remains deferred until the single-connector workflow is proven end to end. See [`../future_modularity_expansion_proposal.md`](../future_modularity_expansion_proposal.md).
