"""Phase 7 Git synchronization policy helpers."""

from __future__ import annotations

from local_mcp_bridge.registry import GitSettings


def git_policy_enabled(settings: GitSettings | None) -> bool:
    """Return whether a project has an explicit Git synchronization policy."""
    return settings is not None
