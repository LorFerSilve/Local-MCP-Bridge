"""Adversarial tests for Phase 5 executable-name resolution."""

import asyncio
import os
import sys
from pathlib import Path

import pytest

from local_mcp_bridge.registry import (
    ExecutableRule,
    ProjectPermissions,
    ProjectRecord,
    ProjectRegistry,
)
from local_mcp_bridge.tools.execution import ExecutionError, ExecutionService


def _unpinned_service(root: Path, *, alias: str, executable: str) -> ExecutionService:
    registry = ProjectRegistry(
        [
            ProjectRecord(
                project_id="demo",
                root=root.resolve(strict=True),
                permissions=ProjectPermissions(execute=True),
                allowed_executables=(
                    ExecutableRule(alias=alias, executable=executable, pinned=False),
                ),
            )
        ]
    )
    return ExecutionService(registry)


def _fake_command_path(directory: Path, stem: str) -> Path:
    filename = f"{stem}.exe" if os.name == "nt" else stem
    path = directory / filename
    path.write_text("not a real executable\n", encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o755)
    return path


def test_unpinned_resolution_uses_only_explicit_safe_path_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    interpreter = Path(sys.executable).resolve(strict=True)
    monkeypatch.setenv("PATH", str(interpreter.parent))
    service = _unpinned_service(
        project,
        alias="runtime",
        executable=interpreter.name,
    )

    async def scenario() -> None:
        result = await service.run_process("demo", "runtime", ["--version"])
        assert result["termination_reason"] == "exited"
        assert result["exit_code"] == 0

    asyncio.run(scenario())


def test_project_local_path_entry_is_excluded_from_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _fake_command_path(project, "shadow")
    monkeypatch.setenv("PATH", str(project))
    service = _unpinned_service(project, alias="shadow", executable="shadow")

    async def scenario() -> None:
        with pytest.raises(ExecutionError, match="No safe PATH entries"):
            await service.run_process("demo", "shadow", [])

    asyncio.run(scenario())


def test_current_working_directory_is_never_implicitly_searched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    safe_bin = tmp_path / "safe-bin"
    project.mkdir()
    safe_bin.mkdir()
    _fake_command_path(project, "shadow")

    monkeypatch.chdir(project)
    monkeypatch.setenv("PATH", str(safe_bin))
    service = _unpinned_service(project, alias="shadow", executable="shadow")

    async def scenario() -> None:
        with pytest.raises(ExecutionError, match="unavailable on the constrained PATH"):
            await service.run_process("demo", "shadow", [])

    asyncio.run(scenario())
