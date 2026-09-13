"""Phase 11.1 controlled real-project onboarding verification.

This operator-side command validates the *effective* local project policy after the optional
Git overlay is applied. It is intentionally read-only: it does not edit configuration,
launch processes, contact a remote endpoint, or expose canonical host paths.

A Phase 11.1 target is considered ready only when its effective project permissions are
exactly read/search enabled with execution and Git disabled.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass

from local_mcp_bridge.config import ConfigError, load_runtime_registry
from local_mcp_bridge.git_config import GitConfigError, load_runtime_git_registry
from local_mcp_bridge.registry import PROJECT_ID_PATTERN, ProjectRegistry, RegistryError


@dataclass(frozen=True, slots=True)
class OnboardingReport:
    """Sanitized Phase 11.1 policy report for one configured project."""

    ok: bool
    project_id: str
    configured_project_count: int
    read: bool
    search: bool
    execute: bool
    git: bool
    allowed_executable_count: int
    reason: str

    def as_public_dict(self) -> dict[str, object]:
        """Return a fixed-schema representation that never contains a host path."""
        return {
            "ok": self.ok,
            "project_id": self.project_id,
            "configured_project_count": self.configured_project_count,
            "permissions": {
                "read": self.read,
                "search": self.search,
                "execute": self.execute,
                "git": self.git,
            },
            "allowed_executable_count": self.allowed_executable_count,
            "reason": self.reason,
        }


def evaluate_onboarding(registry: ProjectRegistry, project_id: str) -> OnboardingReport:
    """Evaluate one target against the exact Phase 11.1 read/search-only policy."""
    public = registry.get_public(project_id)
    if public is None:
        return OnboardingReport(
            ok=False,
            project_id=project_id,
            configured_project_count=len(registry),
            read=False,
            search=False,
            execute=False,
            git=False,
            allowed_executable_count=0,
            reason="project_not_configured",
        )

    permissions = public["permissions"]
    read = permissions["read"]
    search = permissions["search"]
    execute = permissions["execute"]
    git = permissions["git"]
    ok = read and search and not execute and not git

    return OnboardingReport(
        ok=ok,
        project_id=project_id,
        configured_project_count=len(registry),
        read=read,
        search=search,
        execute=execute,
        git=git,
        allowed_executable_count=len(public["allowed_executables"]),
        reason="ready" if ok else "permissions_not_read_search_only",
    )


def _failure_payload(project_id: str, code: str) -> str:
    return json.dumps(
        {
            "ok": False,
            "project_id": project_id,
            "error": code,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Validate one locally configured project for Phase 11.1 onboarding."""
    parser = argparse.ArgumentParser(
        prog="local-mcp-bridge-onboarding-check",
        description=(
            "Validate that one configured project is read/search-only and therefore ready "
            "for the Phase 11.1 live onboarding smoke test."
        ),
    )
    parser.add_argument("project_id", help="Configured logical project ID (never a host path).")
    args = parser.parse_args(argv)
    project_id = args.project_id

    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        print(_failure_payload(project_id, "invalid_project_id"))
        raise SystemExit(2)

    try:
        registry = load_runtime_git_registry(load_runtime_registry())
    except (ConfigError, GitConfigError, RegistryError):
        print(_failure_payload(project_id, "invalid_local_configuration"))
        raise SystemExit(2) from None

    report = evaluate_onboarding(registry, project_id)
    print(json.dumps(report.as_public_dict(), sort_keys=True, separators=(",", ":")))
    if not report.ok:
        raise SystemExit(1)


if __name__ == "__main__":  # pragma: no cover - console entry point
    main()
