"""Configuration tests for the Phase 7 local Git-policy overlay."""

from pathlib import Path

import pytest

from local_mcp_bridge.git_config import GitConfigError, apply_git_policy_overlay
from local_mcp_bridge.registry import (
    ExecutableRule,
    ProjectPermissions,
    ProjectRecord,
    ProjectRegistry,
)


def _registry(root: Path, *, executable_rules: tuple[ExecutableRule, ...] = ()) -> ProjectRegistry:
    return ProjectRegistry(
        [
            ProjectRecord(
                project_id="demo",
                root=root.resolve(),
                permissions=ProjectPermissions(read=True, execute=bool(executable_rules)),
                allowed_executables=executable_rules,
            )
        ]
    )


def _write_overlay(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "git.local.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_overlay_enables_git_without_exposing_remote_url(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    overlay = _write_overlay(
        tmp_path,
        """
projects:
  demo:
    remote: origin
    branch: main
    remote_url: https://github.com/example/demo.git
    timeout_seconds: 45
    max_output_bytes: 65536
""",
    )

    registry = apply_git_policy_overlay(_registry(project), overlay)
    record = registry.require("demo")

    assert record.permissions.git is True
    assert record.git is not None
    assert record.git.remote == "origin"
    assert record.git.branch == "main"
    assert record.git.timeout_seconds == 45
    public = registry.get_public("demo")
    assert public is not None
    assert public["permissions"]["git"] is True
    assert "github.com" not in str(public)


def test_overlay_rejects_unknown_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    overlay = _write_overlay(
        tmp_path,
        """
projects:
  missing:
    remote: origin
    branch: main
    remote_url: https://github.com/example/demo.git
""",
    )

    with pytest.raises(GitConfigError, match="unknown project"):
        apply_git_policy_overlay(_registry(project), overlay)


def test_overlay_rejects_duplicate_policy_keys(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    overlay = _write_overlay(
        tmp_path,
        """
projects:
  demo:
    remote: origin
    remote: upstream
    branch: main
    remote_url: https://github.com/example/demo.git
""",
    )

    with pytest.raises(GitConfigError, match="Duplicate Git policy key"):
        apply_git_policy_overlay(_registry(project), overlay)


@pytest.mark.parametrize(
    "remote_url",
    [
        "http://github.com/example/demo.git",
        "https://user:secret@github.com/example/demo.git",
        "file:///tmp/demo.git",
    ],
)
def test_overlay_rejects_untrusted_remote_forms(tmp_path: Path, remote_url: str) -> None:
    project = tmp_path / "project"
    project.mkdir()
    overlay = _write_overlay(
        tmp_path,
        f"""
projects:
  demo:
    remote: origin
    branch: main
    remote_url: {remote_url!r}
""",
    )

    with pytest.raises(GitConfigError, match="HTTPS URL"):
        apply_git_policy_overlay(_registry(project), overlay)


def test_generic_git_execution_is_rejected_when_execute_is_enabled(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    registry = _registry(
        project,
        executable_rules=(ExecutableRule(alias="git", executable="git"),),
    )

    with pytest.raises(GitConfigError, match="generic process execution"):
        apply_git_policy_overlay(registry, None)
