"""Integration tests for the Phase 7 Git synchronization boundary."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from local_mcp_bridge.registry import GitSettings, ProjectPermissions, ProjectRecord, ProjectRegistry
from local_mcp_bridge.tools.git_service import GitError, GitFetchResult, GitService

REMOTE_URL = "https://github.com/example/demo.git"


def _git(path: Path, *args: str) -> str:
    executable = shutil.which("git")
    if executable is None:
        pytest.skip("Git is unavailable in this environment.")
    completed = subprocess.run(
        [executable, "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.name", "Phase Seven Test")
    _git(project, "config", "user.email", "phase7@example.invalid")
    _git(project, "remote", "add", "origin", REMOTE_URL)
    (project / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(project, "add", "tracked.txt")
    _git(project, "commit", "-m", "base")
    return project, _git(project, "rev-parse", "HEAD")


def _service(project: Path) -> GitService:
    registry = ProjectRegistry(
        [
            ProjectRecord(
                project_id="demo",
                root=project.resolve(),
                permissions=ProjectPermissions(read=True, git=True),
                git=GitSettings(
                    remote="origin",
                    branch="main",
                    remote_url=REMOTE_URL,
                    timeout_seconds=30,
                    max_output_bytes=131_072,
                ),
            )
        ]
    )
    return GitService(registry)


def test_git_status_returns_path_free_clean_metadata(tmp_path: Path) -> None:
    project, head = _repo(tmp_path)
    service = _service(project)

    status = asyncio.run(service.git_status("demo"))

    assert status["branch"] == "main"
    assert status["configured_branch"] == "main"
    assert status["head"] == head
    assert status["clean"] is True
    assert status["staged"] == 0
    assert status["unstaged"] == 0
    assert status["untracked"] == 0
    assert str(project.resolve()) not in str(status)


def test_git_status_counts_changes_without_exposing_filenames(tmp_path: Path) -> None:
    project, _ = _repo(tmp_path)
    (project / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (project / "private-name.txt").write_text("untracked\n", encoding="utf-8")

    status = asyncio.run(_service(project).git_status("demo"))

    assert status["clean"] is False
    assert status["unstaged"] == 1
    assert status["untracked"] == 1
    assert "private-name.txt" not in str(status)


def test_git_status_rejects_remote_url_mismatch(tmp_path: Path) -> None:
    project, _ = _repo(tmp_path)
    _git(project, "remote", "set-url", "origin", "https://github.com/example/other.git")

    with pytest.raises(GitError, match="does not match"):
        asyncio.run(_service(project).git_status("demo"))


def test_git_status_rejects_dangerous_local_git_config(tmp_path: Path) -> None:
    project, _ = _repo(tmp_path)
    _git(project, "config", "filter.evil.smudge", "dangerous-command")

    with pytest.raises(GitError, match="disallowed"):
        asyncio.run(_service(project).git_status("demo"))


def test_fast_forward_sync_refuses_dirty_worktree(tmp_path: Path) -> None:
    project, _ = _repo(tmp_path)
    (project / "untracked.txt").write_text("do not discard\n", encoding="utf-8")

    with pytest.raises(GitError, match="dirty working tree"):
        asyncio.run(_service(project).git_sync_fast_forward("demo"))


def test_fast_forward_sync_updates_only_to_verified_remote_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, base = _repo(tmp_path)
    (project / "tracked.txt").write_text("remote update\n", encoding="utf-8")
    _git(project, "add", "tracked.txt")
    _git(project, "commit", "-m", "remote update")
    target = _git(project, "rev-parse", "HEAD")
    _git(project, "reset", "--hard", base)
    _git(project, "update-ref", "refs/remotes/origin/main", target)
    service = _service(project)

    async def fake_fetch(repository: object) -> GitFetchResult:
        return GitFetchResult(
            project_id="demo",
            remote="origin",
            branch="main",
            previous_remote_head=target,
            remote_head=target,
            changed=False,
        )

    monkeypatch.setattr(service, "_fetch_locked", fake_fetch)
    result = asyncio.run(service.git_sync_fast_forward("demo"))

    assert result["updated"] is True
    assert result["previous_head"] == base
    assert result["head"] == target
    assert _git(project, "rev-parse", "HEAD") == target
    assert (project / "tracked.txt").read_text(encoding="utf-8") == "remote update\n"


def test_fast_forward_sync_refuses_divergent_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, base = _repo(tmp_path)
    (project / "tracked.txt").write_text("remote\n", encoding="utf-8")
    _git(project, "add", "tracked.txt")
    _git(project, "commit", "-m", "remote")
    remote_head = _git(project, "rev-parse", "HEAD")
    _git(project, "reset", "--hard", base)
    (project / "local.txt").write_text("local\n", encoding="utf-8")
    _git(project, "add", "local.txt")
    _git(project, "commit", "-m", "local")
    local_head = _git(project, "rev-parse", "HEAD")
    _git(project, "update-ref", "refs/remotes/origin/main", remote_head)
    service = _service(project)

    async def fake_fetch(repository: object) -> GitFetchResult:
        return GitFetchResult(
            project_id="demo",
            remote="origin",
            branch="main",
            previous_remote_head=remote_head,
            remote_head=remote_head,
            changed=False,
        )

    monkeypatch.setattr(service, "_fetch_locked", fake_fetch)
    with pytest.raises(GitError, match="not a fast-forward"):
        asyncio.run(service.git_sync_fast_forward("demo"))

    assert _git(project, "rev-parse", "HEAD") == local_head
