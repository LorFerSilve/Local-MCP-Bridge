"""Phase 1 contract tests for the minimal MCP server."""

import asyncio

from mcp import Client

from local_mcp_bridge import __version__
from local_mcp_bridge.server import SERVER_NAME, mcp


def test_server_exposes_only_health_check() -> None:
    """Phase 1 must not accidentally expose filesystem or execution tools."""

    async def scenario() -> None:
        async with Client(mcp, raise_exceptions=True) as client:
            result = await client.list_tools()
            assert [tool.name for tool in result.tools] == ["health_check"]

    asyncio.run(scenario())


def test_health_check_reports_disabled_privileged_capabilities() -> None:
    """The health response must be structured and reveal no host-specific data."""

    async def scenario() -> None:
        async with Client(mcp, raise_exceptions=True) as client:
            result = await client.call_tool("health_check", {})

            assert result.is_error is False
            assert result.structured_content == {
                "status": "ok",
                "server": SERVER_NAME,
                "version": __version__,
                "filesystem_enabled": False,
                "execution_enabled": False,
            }

    asyncio.run(scenario())
