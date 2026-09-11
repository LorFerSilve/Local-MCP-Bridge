"""MCP contract tests for the Phase 6 public tool surface."""

import asyncio
from pathlib import Path

from mcp import Client

from local_mcp_bridge import __version__
from local_mcp_bridge.registry import ProjectPermissions, ProjectRecord, ProjectRegistry
from local_mcp_bridge.server import SERVER_NAME, create_mcp_server


def _registry(root: Path) -> ProjectRegistry:
    return ProjectRegistry(
        [
            ProjectRecord(
                project_id="demo",
                root=root.resolve(),
                permissions=ProjectPermissions(read=True, search=True),
            )
        ]
    )


def test_server_exposes_exact_phase_6_tools() -> None:
    async def scenario() -> None:
        async with Client(create_mcp_server(), raise_exceptions=True) as client:
            result = await client.list_tools()
            names = {tool.name for tool in result.tools}
            assert names == {
                "health_check",
                "list_projects",
                "get_project",
                "list_directory",
                "read_file",
                "search_text",
                "run_process",
                "start_job",
                "get_job",
                "list_jobs",
                "get_job_output",
                "cancel_job",
            }
            assert "shell" not in names

    asyncio.run(scenario())


def test_health_check_reports_phase_6_capabilities(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with Client(create_mcp_server(_registry(tmp_path)), raise_exceptions=True) as client:
            result = await client.call_tool("health_check", {})
            assert result.is_error is False
            assert result.structured_content == {
                "status": "ok",
                "server": SERVER_NAME,
                "version": __version__,
                "projects_configured": 1,
                "filesystem_enabled": True,
                "execution_enabled": True,
                "jobs_enabled": True,
                "persistent_jobs": False,
            }
            assert str(tmp_path) not in str(result.structured_content)

    asyncio.run(scenario())


def test_project_and_filesystem_tools_remain_path_safe(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("needle\n", encoding="utf-8")

    async def scenario() -> None:
        async with Client(create_mcp_server(_registry(tmp_path)), raise_exceptions=True) as client:
            listed = await client.call_tool("list_projects", {})
            read = await client.call_tool(
                "read_file",
                {"project_id": "demo", "path": "hello.txt"},
            )
            searched = await client.call_tool(
                "search_text",
                {"project_id": "demo", "query": "needle"},
            )
            combined = f"{listed}{read}{searched}"
            assert "needle" in combined
            assert str(tmp_path.resolve()) not in combined

    asyncio.run(scenario())


def test_unknown_job_id_is_reported_as_tool_error() -> None:
    async def scenario() -> None:
        async with Client(create_mcp_server(), raise_exceptions=False) as client:
            result = await client.call_tool("get_job", {"job_id": "a" * 32})
            assert result.is_error is True

    asyncio.run(scenario())
