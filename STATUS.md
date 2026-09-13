# Project Status

Last updated: 2026-09-13.

## Current state

- Phase 0 through Phase 9: complete.
- Phase 10 — Lightweight Claude MCP validation: complete.
- Phase 10.5 — Claude OAuth compatibility: complete.
- Phase 11 — Real project integration and autonomous workflow testing: in progress.
- Phase 11.1 — Controlled real-project onboarding: repository implementation complete; live target onboarding pending.

A real Claude custom connector has already proven the OAuth and read-only MCP path against Local-MCP-Bridge itself. Phase 11.1 now applies that boundary to one selected real development project before execution or Git are enabled.

Version 0.11.0 adds the operator-side `local-mcp-bridge-onboarding-check <project-id>` command. It validates the effective local registry, including any Git overlay, and succeeds only when the selected target is exactly read/search enabled with execute/Git disabled. Its report deliberately excludes host roots, executable targets, Git URLs, and file content.

Phase 11.1 is not considered complete until one real local project passes both the onboarding check and a live Claude read/search smoke sequence.

See:

- [`docs/phase-11-1-controlled-onboarding.md`](docs/phase-11-1-controlled-onboarding.md)
- [`docs/roadmap.md`](docs/roadmap.md)
- [`docs/phase-10-live-validation.md`](docs/phase-10-live-validation.md)
- [`docs/phase-10-5-claude-oauth.md`](docs/phase-10-5-claude-oauth.md)

Phase 11 continues to enable capabilities incrementally and treats remote authentication as separate from project-level authorization.
