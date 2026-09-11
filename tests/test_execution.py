"""Security and behavior tests for Phase 5 controlled process execution."""

import asyncio
import os
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


def _service(
    root: Path,
    *,
    execute: bool = True,
    settings: ExecutionSettings | None = None,
) -> ExecutionService:
    python_path = Path(sys.executable).resolve(strict=True)
    registry = ProjectRegistry(
        [
            ProjectRecord(
                project_id="demo",
                root=root.resolve(strict=True),
                permissions=ProjectPermissions(execute=execute),
                allowed_executables=(
                    ExecutableRule(alias="python", executable=str(python_path), pinned=True),
                ),
                execution=settings or ExecutionSettings(),
            )
        ]
    )
    return ExecutionService(registry)


def test_execution_requires_explicit_project_permission(tmp_path: Path) -> None:
    service = _service(tmp_path, execute=False)

    async def scenario() -> None:
        with pytest.raises(ExecutionError, match="not enabled"):
            await service.run_process("demo", "python", ["--version"])

    asyncio.run(scenario())


def test_only_allowlisted_executable_aliases_are_accepted(tmp_path: Path) -> None:
    service = _service(tmp_path)

    async def scenario() -> None:
        with pytest.raises(ExecutionError, match="not allowlisted"):
            await service.run_process("demo", "not-allowed", [])

    asyncio.run(scenario())


def test_working_directory_is_confined_by_path_guard(tmp_path: Path) -> None:
    service = _service(tmp_path)

    async def scenario() -> None:
        with pytest.raises(ExecutionError, match="Working directory"):
            await service.run_process("demo", "python", ["--version"], cwd="../outside")

    asyncio.run(scenario())


def test_process_runs_without_shell_interpretation(tmp_path: Path) -> None:
    service = _service(tmp_path)
    payload = "hello; echo SHOULD_NOT_RUN && touch nope"

    async def scenario() -> None:
        result = await service.run_process(
            "demo",
            "python",
            ["-c", "import sys; print(sys.argv[1])", payload],
        )
        assert result["termination_reason"] == "exited"
        assert result["exit_code"] == 0
        assert result["stdout"].strip() == payload
        assert not (tmp_path / "nope").exists()

    asyncio.run(scenario())


def test_child_environment_does_not_inherit_arbitrary_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCAL_MCP_BRIDGE_TEST_SECRET", "super-secret-value")
    service = _service(tmp_path)

    async def scenario() -> None:
        result = await service.run_process(
            "demo",
            "python",
            [
                "-c",
                "import os; print(os.getenv('LOCAL_MCP_BRIDGE_TEST_SECRET', '<missing>'))",
            ],
        )
        assert result["exit_code"] == 0
        assert result["stdout"].strip() == "<missing>"
        assert "super-secret-value" not in result["stdout"]

    asyncio.run(scenario())


def test_project_root_is_redacted_from_child_output(tmp_path: Path) -> None:
    service = _service(tmp_path)

    async def scenario() -> None:
        result = await service.run_process(
            "demo",
            "python",
            ["-c", "import os; print(os.getcwd())"],
        )
        assert result["exit_code"] == 0
        assert str(tmp_path.resolve()) not in result["stdout"]
        assert "<project-root>" in result["stdout"]

    asyncio.run(scenario())


def test_nested_confined_working_directory_is_supported(tmp_path: Path) -> None:
    nested = tmp_path / "src"
    nested.mkdir()
    service = _service(tmp_path)

    async def scenario() -> None:
        result = await service.run_process(
            "demo",
            "python",
            ["-c", "import os; print(os.path.basename(os.getcwd()))"],
            cwd="src",
        )
        assert result["exit_code"] == 0
        assert result["cwd"] == "src"
        assert result["stdout"].strip() == "src"

    asyncio.run(scenario())


def test_timeout_terminates_long_running_process(tmp_path: Path) -> None:
    settings = ExecutionSettings(
        default_timeout_seconds=1,
        max_timeout_seconds=2,
        max_output_bytes=262_144,
        max_concurrent_jobs=1,
    )
    service = _service(tmp_path, settings=settings)

    async def scenario() -> None:
        result = await service.run_process(
            "demo",
            "python",
            ["-c", "import time; time.sleep(10)"],
            timeout_seconds=1,
        )
        assert result["termination_reason"] == "timeout"
        assert result["duration_ms"] < 6_000
        assert result["exit_code"] is not None

    asyncio.run(scenario())


def test_combined_output_limit_terminates_noisy_process(tmp_path: Path) -> None:
    settings = ExecutionSettings(
        default_timeout_seconds=5,
        max_timeout_seconds=5,
        max_output_bytes=4_096,
        max_concurrent_jobs=1,
    )
    service = _service(tmp_path, settings=settings)

    async def scenario() -> None:
        result = await service.run_process(
            "demo",
            "python",
            [
                "-c",
                "import sys; sys.stdout.write('x' * 10000); sys.stdout.flush(); "
                "sys.stderr.write('y' * 10000); sys.stderr.flush()",
            ],
        )
        assert result["termination_reason"] == "output_limit"
        assert result["output_truncated"] is True
        encoded = result["stdout"].encode() + result["stderr"].encode()
        assert len(encoded) <= 4_096
        assert result["exit_code"] is not None

    asyncio.run(scenario())


def test_nonzero_exit_is_returned_as_structured_process_result(tmp_path: Path) -> None:
    service = _service(tmp_path)

    async def scenario() -> None:
        result = await service.run_process(
            "demo",
            "python",
            ["-c", "import sys; print('failure'); sys.exit(7)"],
        )
        assert result["termination_reason"] == "exited"
        assert result["exit_code"] == 7
        assert result["stdout"].strip() == "failure"

    asyncio.run(scenario())


def test_control_characters_in_output_are_escaped(tmp_path: Path) -> None:
    service = _service(tmp_path)

    async def scenario() -> None:
        result = await service.run_process(
            "demo",
            "python",
            ["-c", "import sys; sys.stdout.write('safe\\x1b[31mtext')"],
        )
        assert "\x1b" not in result["stdout"]
        assert "\\x1b[31m" in result["stdout"]

    asyncio.run(scenario())


def test_nul_argument_is_rejected_before_process_creation(tmp_path: Path) -> None:
    service = _service(tmp_path)

    async def scenario() -> None:
        with pytest.raises(ExecutionError, match="NUL"):
            await service.run_process("demo", "python", ["bad\x00argument"])

    asyncio.run(scenario())


def test_timeout_override_cannot_exceed_project_policy(tmp_path: Path) -> None:
    settings = ExecutionSettings(
        default_timeout_seconds=2,
        max_timeout_seconds=3,
        max_output_bytes=262_144,
        max_concurrent_jobs=1,
    )
    service = _service(tmp_path, settings=settings)

    async def scenario() -> None:
        with pytest.raises(ExecutionError, match="configured maximum"):
            await service.run_process("demo", "python", ["--version"], timeout_seconds=4)

    asyncio.run(scenario())


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell path is platform-specific.")
def test_shell_executable_target_is_rejected_even_if_allowlisted(tmp_path: Path) -> None:
    shell = Path("/bin/sh")
    if not shell.exists():
        pytest.skip("/bin/sh is unavailable on this runner.")

    registry = ProjectRegistry(
        [
            ProjectRecord(
                project_id="demo",
                root=tmp_path.resolve(strict=True),
                permissions=ProjectPermissions(execute=True),
                allowed_executables=(
                    ExecutableRule(alias="shell", executable=str(shell.resolve()), pinned=True),
                ),
            )
        ]
    )
    service = ExecutionService(registry)

    async def scenario() -> None:
        with pytest.raises(ExecutionError, match="Shell executables"):
            await service.run_process("demo", "shell", ["-c", "echo unsafe"])

    asyncio.run(scenario())


def test_shell_script_target_is_rejected_before_launch(tmp_path: Path) -> None:
    script = tmp_path / "danger.cmd"
    script.write_text("echo SHOULD_NOT_RUN\n", encoding="utf-8")
    if os.name != "nt":
        script.chmod(0o755)

    registry = ProjectRegistry(
        [
            ProjectRecord(
                project_id="demo",
                root=tmp_path.resolve(strict=True),
                permissions=ProjectPermissions(execute=True),
                allowed_executables=(
                    ExecutableRule(alias="danger", executable=str(script), pinned=True),
                ),
            )
        ]
    )
    service = ExecutionService(registry)

    async def scenario() -> None:
        with pytest.raises(ExecutionError, match="shell scripts"):
            await service.run_process("demo", "danger", [])

    asyncio.run(scenario())
