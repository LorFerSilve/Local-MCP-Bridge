"""Concurrency-limit tests for Phase 5 one-shot process execution."""

import asyncio
import sys
from pathlib import Path

import pytest

from local_mcp_bridge.registry import (
    ExecutableRule,
    ExecutionSettings,
    ProjectPermissions,
    ProjectRecord,
    ProjectRegistry,
)
from local_mcp_bridge.tools.execution import ExecutionError, ExecutionService


def _service(root: Path) -> ExecutionService:
    interpreter = Path(sys.executable).resolve(strict=True)
    registry = ProjectRegistry(
        [
            ProjectRecord(
                project_id="demo",
                root=root.resolve(strict=True),
                permissions=ProjectPermissions(execute=True),
                allowed_executables=(
                    ExecutableRule(
                        alias="python",
                        executable=str(interpreter),
                        pinned=True,
                    ),
                ),
                execution=ExecutionSettings(
                    default_timeout_seconds=5,
                    max_timeout_seconds=5,
                    max_output_bytes=262_144,
                    max_concurrent_jobs=1,
                ),
            )
        ]
    )
    return ExecutionService(registry)


def test_concurrency_limit_rejects_excess_call_instead_of_queueing(tmp_path: Path) -> None:
    service = _service(tmp_path)

    async def scenario() -> None:
        first = asyncio.create_task(
            service.run_process(
                "demo",
                "python",
                ["-c", "import time; time.sleep(0.8); print('first')"],
            )
        )
        await asyncio.sleep(0.1)

        with pytest.raises(ExecutionError, match="concurrency limit"):
            await service.run_process("demo", "python", ["--version"])

        result = await first
        assert result["exit_code"] == 0
        assert result["stdout"].strip() == "first"

    asyncio.run(scenario())
