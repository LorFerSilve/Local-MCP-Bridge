"""Phase 8 audit logging and runtime-hardening tests."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from mcp import Client

from local_mcp_bridge.audit import AuditError, AuditLogger
from local_mcp_bridge.registry import ProjectPermissions, ProjectRecord, ProjectRegistry
from local_mcp_bridge.server import create_mcp_server


def _events(directory: Path) -> list[dict[str, object]]:
    path = directory / "audit.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_disabled_audit_logger_is_hermetic(tmp_path: Path) -> None:
    logger = AuditLogger()

    assert logger.enabled is False
    assert logger.healthy is True
    assert (
        logger.record("runtime.bootstrap", "success", details={"projects_configured": 0})
        is False
    )
    assert list(tmp_path.iterdir()) == []


def test_audit_logger_writes_fixed_schema_jsonl(tmp_path: Path) -> None:
    directory = tmp_path / "audit"
    logger = AuditLogger(directory)

    assert logger.record(
        "filesystem.read_file",
        "success",
        project_id="demo",
        details={"requested_max_lines": 25},
    )

    events = _events(directory)
    assert len(events) == 1
    event = events[0]
    assert event["schema_version"] == 1
    assert event["action"] == "filesystem.read_file"
    assert event["outcome"] == "success"
    assert event["project_id"] == "demo"
    assert event["details"] == {"requested_max_lines": 25}
    assert isinstance(event["event_id"], str) and len(event["event_id"]) == 32
    assert isinstance(event["session_id"], str) and len(event["session_id"]) == 32
    assert event["sequence"] == 1

    if os.name != "nt":
        assert directory.stat().st_mode & 0o777 == 0o700
        assert (directory / "audit.jsonl").stat().st_mode & 0o777 == 0o600


def test_audit_schema_rejects_arbitrary_text_fields(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path / "audit")

    with pytest.raises(AuditError, match="fixed metadata schema"):
        logger.record(
            "filesystem.read_file",
            "success",
            project_id="demo",
            details={"path": "secret.txt"},
        )

    with pytest.raises(AuditError, match="project ID"):
        logger.record("filesystem.read_file", "error", project_id="../../secret")


def test_audit_rotation_is_bounded(tmp_path: Path) -> None:
    directory = tmp_path / "audit"
    logger = AuditLogger(directory, max_file_bytes=1024, retained_files=3)

    for index in range(40):
        logger.record(
            "job.list",
            "success",
            project_id="demo",
            details={"returned_jobs": index, "requested_limit": 20},
        )

    files = sorted(path for path in directory.iterdir() if path.name.startswith("audit.jsonl"))
    assert 1 <= len(files) <= 3
    assert all(path.stat().st_size <= 1024 for path in files)
    assert logger.healthy is True


def test_audit_rejects_hard_linked_active_file(tmp_path: Path) -> None:
    directory = tmp_path / "audit"
    directory.mkdir()
    active = directory / "audit.jsonl"
    active.write_text("{}\n", encoding="utf-8")
    alias = tmp_path / "audit-alias.jsonl"
    try:
        os.link(active, alias)
    except OSError:
        pytest.skip("Hard links are unavailable in this environment.")

    with pytest.raises(AuditError, match="safe private regular file"):
        AuditLogger(directory)


def test_audit_rejects_redirecting_directory(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    redirect = tmp_path / "redirect"
    try:
        redirect.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("Symbolic links are unavailable in this environment.")

    with pytest.raises(AuditError, match="non-redirecting directories"):
        AuditLogger(redirect)


def test_write_failure_marks_audit_unhealthy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = AuditLogger(tmp_path / "audit")

    def fail_write(encoded: bytes) -> None:
        raise OSError("simulated audit storage failure")

    monkeypatch.setattr(logger, "_append_event_locked", fail_write)

    with pytest.raises(AuditError, match="could not be persisted"):
        logger.record("git.fetch", "attempt", project_id="demo")
    assert logger.healthy is False


def test_server_audit_never_logs_paths_queries_contents_or_argv(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    secret_name = "private-filename.txt"
    secret_query = "ultra-sensitive-query"
    secret_argument = "ultra-sensitive-argument"
    (project / secret_name).write_text(f"{secret_query}\n", encoding="utf-8")

    registry = ProjectRegistry(
        [
            ProjectRecord(
                project_id="demo",
                root=project.resolve(),
                permissions=ProjectPermissions(read=True, search=True),
            )
        ]
    )
    audit_dir = tmp_path / "audit"
    logger = AuditLogger(audit_dir)

    async def scenario() -> None:
        async with Client(
            create_mcp_server(registry, audit_logger=logger),
            raise_exceptions=False,
        ) as client:
            read = await client.call_tool(
                "read_file",
                {"project_id": "demo", "path": secret_name},
            )
            assert read.is_error is False
            searched = await client.call_tool(
                "search_text",
                {"project_id": "demo", "query": secret_query},
            )
            assert searched.is_error is False
            failed_process = await client.call_tool(
                "run_process",
                {
                    "project_id": "demo",
                    "executable": "python",
                    "args": [secret_argument],
                },
            )
            assert failed_process.is_error is True

    asyncio.run(scenario())

    log_text = (audit_dir / "audit.jsonl").read_text(encoding="utf-8")
    assert "filesystem.read_file" in log_text
    assert "filesystem.search_text" in log_text
    assert "process.run" in log_text
    assert secret_name not in log_text
    assert secret_query not in log_text
    assert secret_argument not in log_text
    assert "python" not in log_text
    assert str(project.resolve()) not in log_text


def test_sensitive_operation_is_refused_before_execution_when_audit_fails(
    tmp_path: Path,
) -> None:
    class BrokenAudit:
        enabled = True
        healthy = False

        def record(self, *args: object, **kwargs: object) -> bool:
            raise AuditError("broken")

    class SpyExecution:
        called = False

        async def run_process(self, **kwargs: object) -> dict[str, object]:
            self.called = True
            return {
                "project_id": "demo",
                "executable": "python",
                "cwd": ".",
                "exit_code": 0,
                "stdout": "",
                "stderr": "",
                "termination_reason": "exited",
                "output_truncated": False,
                "duration_ms": 0,
            }

    class DummyJobs:
        persistent = False

    registry = ProjectRegistry(
        [
            ProjectRecord(
                project_id="demo",
                root=tmp_path.resolve(),
                permissions=ProjectPermissions(execute=True),
            )
        ]
    )
    execution = SpyExecution()

    async def scenario() -> None:
        async with Client(
            create_mcp_server(
                registry,
                execution_service=execution,  # type: ignore[arg-type]
                job_manager=DummyJobs(),  # type: ignore[arg-type]
                audit_logger=BrokenAudit(),  # type: ignore[arg-type]
            ),
            raise_exceptions=False,
        ) as client:
            result = await client.call_tool(
                "run_process",
                {
                    "project_id": "demo",
                    "executable": "python",
                    "args": [],
                },
            )
            assert result.is_error is True
            assert execution.called is False

    asyncio.run(scenario())
