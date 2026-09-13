from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_mcp_bridge.onboarding import evaluate_onboarding, main
from local_mcp_bridge.registry import (
    GitSettings,
    ProjectPermissions,
    ProjectRecord,
    ProjectRegistry,
)


def _registry(
    root: Path,
    *,
    read: bool = True,
    search: bool = True,
    execute: bool = False,
    git: bool = False,
) -> ProjectRegistry:
    git_settings = (
        GitSettings(
            remote="origin",
            branch="main",
            remote_url="https://example.com/owner/repo.git",
        )
        if git
        else None
    )
    return ProjectRegistry(
        [
            ProjectRecord(
                project_id="real-project",
                root=root.resolve(),
                permissions=ProjectPermissions(
                    read=read,
                    search=search,
                    execute=execute,
                    git=git,
                ),
                git=git_settings,
            )
        ]
    )


def test_read_search_only_project_is_ready(tmp_path: Path) -> None:
    report = evaluate_onboarding(_registry(tmp_path), "real-project")

    assert report.ok is True
    assert report.reason == "ready"
    assert report.read is True
    assert report.search is True
    assert report.execute is False
    assert report.git is False
    assert report.configured_project_count == 1


def test_execution_enabled_project_is_not_ready(tmp_path: Path) -> None:
    report = evaluate_onboarding(_registry(tmp_path, execute=True), "real-project")

    assert report.ok is False
    assert report.reason == "permissions_not_read_search_only"
    assert report.execute is True


def test_git_enabled_project_is_not_ready(tmp_path: Path) -> None:
    report = evaluate_onboarding(_registry(tmp_path, git=True), "real-project")

    assert report.ok is False
    assert report.reason == "permissions_not_read_search_only"
    assert report.git is True


def test_missing_project_returns_sanitized_failure(tmp_path: Path) -> None:
    report = evaluate_onboarding(_registry(tmp_path), "missing-project")
    payload = report.as_public_dict()

    assert payload["ok"] is False
    assert payload["reason"] == "project_not_configured"
    assert str(tmp_path) not in json.dumps(payload)


def test_report_never_exposes_project_root(tmp_path: Path) -> None:
    report = evaluate_onboarding(_registry(tmp_path), "real-project")

    encoded = json.dumps(report.as_public_dict(), sort_keys=True)
    assert str(tmp_path) not in encoded
    assert "root" not in report.as_public_dict()


def test_main_rejects_invalid_project_id(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["../bad-project"])

    assert exc_info.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "error": "invalid_project_id",
        "ok": False,
        "project_id": "../bad-project",
    }
