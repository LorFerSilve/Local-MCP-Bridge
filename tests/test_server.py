"""Phase 2 MCP contract tests for project registry metadata."""

import asyncio
from pathlib import Path

from mcp import Client

from local_mcp_bridge import __version__
from local_mcp_bridge.registry import ProjectPermissions, ProjectRecord, ProjectRegistry
from local_mcp_bridge.server import SERVER_NAME, create_mcp_server


def test_server_exposes_only_phase_2_tools() -> None:
    """Phase 2 must expose metadata only, not filesystem or execution tools."""

    async def scenario() -> None:
        async with Client(create_mcp_server(), raise_exceptions=True) as client:
            result = await client.list_tools()
            assert {tool.name for tool in result.tools} == {
                "health_check",
                "list_projects",
                "get_project",
            }

    asyncio.run(scenario())


def test_health_check_reports_registry_count_and_disabled_privileged_capabilities(
    tmp_path: Path,
) -> None:
    """Health status reports only a project count, never host-specific paths."""
    registry = ProjectRegistry(
        [
            ProjectRecord(
                project_id="demo",
                root=tmp_path.resolve(),
                permissions=ProjectPermissions(read=True),
            )
        ]
    )

    async def scenario() -> None:
        async with Client(create_mcp_server(registry), raise_exceptions=True) as client:
            result = await client.call_tool("health_check", {})

            assert result.is_error is False
            assert result.structured_content == {
                "status": "ok",
                "server": SERVER_NAME,
                "version": __version__,
                "projects_configured": 1,
                "filesystem_enabled": False,
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
