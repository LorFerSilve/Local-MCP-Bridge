"""Behavior and persistence tests for the Phase 6 local job manager."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from local_mcp_bridge.jobs import JobError, JobManager
from local_mcp_bridge.registry import (
    ExecutableRule,
    ExecutionSettings,
    ProjectPermissions,
    ProjectRecord,
    ProjectRegistry,
)
from local_mcp_bridge.tools.execution import ExecutionService

_TERMINAL = {"succeeded", "failed", "cancelled", "timeout", "output_limit", "interrupted"}


def _registry(root: Path, *, timeout: int = 10) -> ProjectRegistry:
    python = Path(sys.executable).resolve(strict=True)
    return ProjectRegistry(
        [
            ProjectRecord(
                project_id="demo",
                root=root.resolve(strict=True),
                permissions=ProjectPermissions(execute=True),
                allowed_executables=(
                    ExecutableRule(alias="python", executable=str(python), pinned=True),
                ),
                execution=ExecutionSettings(
                    default_timeout_seconds=timeout,
                    max_timeout_seconds=timeout,
                    max_output_bytes=262_144,
                    max_concurrent_jobs=2,
                ),
            )
        ]
    )


def _manager(root: Path, state_dir: Path | None = None) -> JobManager:
    registry = _registry(root)
    return JobManager(registry, ExecutionService(registry), state_dir=state_dir)


async def _wait_terminal(manager: JobManager, job_id: str, timeout: float = 8.0) -> dict:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        job = manager.get_job(job_id)
        if job["status"] in _TERMINAL:
            return job
        await asyncio.sleep(0.02)
    raise AssertionError("managed job did not reach a terminal state in time")


def test_background_job_returns_id_and_completes_without_blocking_start(tmp_path: Path) -> None:
    manager = _manager(tmp_path)

    async def scenario() -> None:
        started = await manager.start_job(
            "demo",
            "python",
            ["-c", "import time; time.sleep(0.2); print('done')"],
        )
        job = started["job"]
        assert len(job["job_id"]) == 32
        assert job["status"] in {"starting", "running"}
        assert job["persistent"] is False

        terminal = await _wait_terminal(manager, job["job_id"])
        assert terminal["status"] == "succeeded"
        assert terminal["exit_code"] == 0

        output = manager.get_job_output(job["job_id"], "stdout")
        assert output["data"].strip() == "done"
        assert output["complete"] is True
        assert output["eof"] is True

    asyncio.run(scenario())


def test_job_output_is_paginated_with_stable_offsets(tmp_path: Path) -> None:
    manager = _manager(tmp_path)

    async def scenario() -> None:
        started = await manager.start_job(
            "demo",
            "python",
            ["-c", "print('abcdefghij' * 5, end='')"],
        )
        job_id = started["job"]["job_id"]
        await _wait_terminal(manager, job_id)

        first = manager.get_job_output(job_id, max_chars=12)
        second = manager.get_job_output(job_id, offset=first["next_offset"], max_chars=12)
        assert first["data"] == "abcdefghijab"
        assert second["data"] == "cdefghijabcd"
        assert first["next_offset"] == 12
        assert second["next_offset"] == 24
        assert first["complete"] is True

    asyncio.run(scenario())


def test_cancel_job_stops_supervised_process(tmp_path: Path) -> None:
    manager = _manager(tmp_path)

    async def scenario() -> None:
        started = await manager.start_job(
            "demo",
            "python",
            ["-c", "import time; time.sleep(10)"],
        )
        job_id = started["job"]["job_id"]

        for _ in range(100):
            if manager.get_job(job_id)["status"] == "running":
                break
            await asyncio.sleep(0.01)

        cancelled = await manager.cancel_job(job_id)
        assert cancelled["accepted"] is True
        assert cancelled["status"] == "cancelled"
        assert manager.get_job(job_id)["status"] == "cancelled"

    asyncio.run(scenario())


def test_job_state_and_terminal_output_survive_manager_restart(tmp_path: Path) -> None:
    state_dir = tmp_path / "job-state"
    project = tmp_path / "project"
    project.mkdir()
    manager = _manager(project, state_dir)

    async def scenario() -> str:
        started = await manager.start_job("demo", "python", ["-c", "print('persisted')"])
        job_id = started["job"]["job_id"]
        terminal = await _wait_terminal(manager, job_id)
        assert terminal["persistent"] is True
        return job_id

    job_id = asyncio.run(scenario())

    recovered = _manager(project, state_dir)
    job = recovered.get_job(job_id)
    assert job["status"] == "succeeded"
    assert job["persistent"] is True
    assert recovered.get_job_output(job_id)["data"].strip() == "persisted"


def test_raw_arguments_are_never_persisted_to_job_state(tmp_path: Path) -> None:
    state_dir = tmp_path / "job-state"
    project = tmp_path / "project"
    project.mkdir()
    manager = _manager(project, state_dir)
    secret_argument = "phase6-secret-argument-that-must-not-be-written"

    async def scenario() -> str:
        started = await manager.start_job(
            "demo",
            "python",
            ["-c", "print('safe-output')", secret_argument],
        )
        job_id = started["job"]["job_id"]
        await _wait_terminal(manager, job_id)
        return job_id

    job_id = asyncio.run(scenario())
    state_text = (state_dir / f"{job_id}.job.json").read_text(encoding="utf-8")
    assert secret_argument not in state_text
    assert "print('safe-output')" not in state_text
    assert "safe-output" in state_text


def test_nonterminal_state_is_marked_interrupted_during_recovery(tmp_path: Path) -> None:
    state_dir = tmp_path / "job-state"
    state_dir.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    job_id = "a" * 32
    payload = {
        "schema_version": 1,
        "job_id": job_id,
        "project_id": "demo",
        "executable": "python",
        "cwd": ".",
        "status": "running",
        "created_at": "2026-09-11T00:00:00.000Z",
        "started_at": "2026-09-11T00:00:01.000Z",
        "finished_at": None,
        "exit_code": None,
        "termination_reason": None,
        "output_truncated": False,
        "argument_count": 1,
        "stdout": "partial",
        "stderr": "",
        "error": None,
    }
    (state_dir / f"{job_id}.job.json").write_text(json.dumps(payload), encoding="utf-8")

    recovered = _manager(project, state_dir)
    job = recovered.get_job(job_id)
    assert job["status"] == "interrupted"
    assert job["termination_reason"] == "bridge_restart"
    assert job["finished_at"] is not None
    assert recovered.get_job_output(job_id)["data"] == "partial"


def test_malformed_or_oversized_state_does_not_break_recovery(tmp_path: Path) -> None:
    state_dir = tmp_path / "job-state"
    state_dir.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (state_dir / f"{'b' * 32}.job.json").write_text("{not-json", encoding="utf-8")

    recovered = _manager(project, state_dir)
    assert recovered.list_jobs() == {"jobs": []}


def test_start_validation_rejects_unknown_executable_before_allocating_job(tmp_path: Path) -> None:
    manager = _manager(tmp_path)

    async def scenario() -> None:
        with pytest.raises(JobError, match="not allowlisted"):
            await manager.start_job("demo", "cmd", [])
        assert manager.list_jobs() == {"jobs": []}

    asyncio.run(scenario())


def test_start_validation_rejects_traversal_before_allocating_job(tmp_path: Path) -> None:
    manager = _manager(tmp_path)

    async def scenario() -> None:
        with pytest.raises(JobError, match="project-relative"):
            await manager.start_job("demo", "python", ["--version"], cwd="../outside")
        assert manager.list_jobs() == {"jobs": []}

    asyncio.run(scenario())


def test_unknown_job_ids_do_not_leak_job_inventory(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    with pytest.raises(JobError, match="Unknown job ID"):
        manager.get_job("c" * 32)
    with pytest.raises(JobError, match="Unknown job ID"):
        manager.get_job("../not-a-job")
