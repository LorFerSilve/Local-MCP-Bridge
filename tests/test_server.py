"""MCP contract tests for Phase 3 project and filesystem capabilities."""

import asyncio
from pathlib import Path

from mcp import Client

from local_mcp_bridge import __version__
from local_mcp_bridge.registry import ProjectPermissions, ProjectRecord, ProjectRegistry
from local_mcp_bridge.server import SERVER_NAME, create_mcp_server


def _registry(root: Path, *, read: bool = True, search: bool = True) -> ProjectRegistry:
    return ProjectRegistry(
        [
            ProjectRecord(
                project_id="demo",
                root=root.resolve(),
                permissions=ProjectPermissions(read=read, search=search),
            )
        ]
    )


def test_server_exposes_exact_phase_3_tools() -> None:
    """Phase 3 exposes metadata plus read-only filesystem tools, and nothing else."""

    async def scenario() -> None:
        async with Client(create_mcp_server(), raise_exceptions=True) as client:
            result = await client.list_tools()
            assert {tool.name for tool in result.tools} == {
                "health_check",
                "list_projects",
                "get_project",
                "list_directory",
                "read_file",
                "search_text",
            }

    asyncio.run(scenario())


def test_health_check_reports_filesystem_enabled_and_execution_disabled(
    tmp_path: Path,
) -> None:
    """Health status reflects Phase 3 without exposing host-specific paths."""

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
                "execution_enabled": False,
            }
            assert str(tmp_path) not in str(result.structured_content)

    asyncio.run(scenario())


def test_project_metadata_tools_never_expose_local_root(tmp_path: Path) -> None:
    """MCP-visible project responses contain IDs and permissions only."""
    registry = ProjectRegistry(
        [
            ProjectRecord(
                project_id="demo",
                root=tmp_path.resolve(),
                permissions=ProjectPermissions(read=True, search=True, git=True),
            )
        ]
    )

    async def scenario() -> None:
        async with Client(create_mcp_server(registry), raise_exceptions=True) as client:
            listed = await client.call_tool("list_projects", {})
            looked_up = await client.call_tool("get_project", {"project_id": "demo"})

            expected_project = {
                "id": "demo",
                "permissions": {
                    "read": True,
                    "search": True,
                    "execute": False,
                    "git": True,
                },
            }
            assert listed.structured_content == {"projects": [expected_project]}
            assert looked_up.structured_content == {"found": True, "project": expected_project}
            assert "root" not in str(listed.structured_content)
            assert str(tmp_path) not in str(listed.structured_content)
            assert str(tmp_path) not in str(looked_up.structured_content)

    asyncio.run(scenario())


def test_unknown_project_lookup_is_non_enumerating() -> None:
    """Unknown IDs return a simple miss without leaking valid roots or paths."""

    async def scenario() -> None:
        async with Client(create_mcp_server(), raise_exceptions=True) as client:
            result = await client.call_tool("get_project", {"project_id": "does-not-exist"})

            assert result.is_error is False
            assert result.structured_content == {"found": False, "project": None}

    asyncio.run(scenario())


def test_filesystem_tools_work_end_to_end_without_absolute_path_leaks(tmp_path: Path) -> None:
    """MCP filesystem responses expose only project-relative paths and bounded content."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text("def main():\n    return 'needle'\n", encoding="utf-8")

    async def scenario() -> None:
        async with Client(create_mcp_server(_registry(tmp_path)), raise_exceptions=True) as client:
            listed = await client.call_tool(
                "list_directory",
                {"project_id": "demo", "path": "src"},
            )
            read = await client.call_tool(
                "read_file",
                {"project_id": "demo", "path": "src/main.py", "max_lines": 20},
            )
            searched = await client.call_tool(
                "search_text",
                {"project_id": "demo", "query": "needle", "path": "src"},
            )

            assert listed.is_error is False
            assert listed.structured_content is not None
            assert listed.structured_content["path"] == "src"
            assert listed.structured_content["entries"][0]["path"] == "src/main.py"

            assert read.is_error is False
            assert read.structured_content is not None
            assert read.structured_content["content"] == "def main():\n    return 'needle'\n"
            assert read.structured_content["path"] == "src/main.py"

            assert searched.is_error is False
            assert searched.structured_content is not None
            assert searched.structured_content["matches"][0]["path"] == "src/main.py"

            combined = (
                f"{listed.structured_content}"
                f"{read.structured_content}"
                f"{searched.structured_content}"
            )
            assert str(tmp_path) not in combined

    asyncio.run(scenario())


def test_mcp_rejects_traversal_without_leaking_project_root(tmp_path: Path) -> None:
    """Filesystem policy errors remain MCP errors with no absolute root disclosure."""

    async def scenario() -> None:
        async with Client(create_mcp_server(_registry(tmp_path)), raise_exceptions=False) as client:
            result = await client.call_tool(
                "read_file",
                {"project_id": "demo", "path": "../outside.txt"},
            )

            assert result.is_error is True
            assert str(tmp_path) not in str(result)

    asyncio.run(scenario())
