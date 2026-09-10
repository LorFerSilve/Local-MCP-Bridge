"""Minimal MCP server entry point for Local-MCP-Bridge."""

from typing import TypedDict

from mcp.server import MCPServer

from local_mcp_bridge import __version__

SERVER_NAME = "Local MCP Bridge"


class HealthStatus(TypedDict):
    """Non-sensitive status returned by the Phase 1 health tool."""

    status: str
    server: str
    version: str
    filesystem_enabled: bool
    execution_enabled: bool


mcp = MCPServer(SERVER_NAME)


@mcp.tool()
def health_check() -> HealthStatus:
    """Return basic server health without exposing host or project information."""
    return HealthStatus(
        status="ok",
        server=SERVER_NAME,
        version=__version__,
        filesystem_enabled=False,
        execution_enabled=False,
    )


def main() -> None:
    """Run the bridge over MCP's local stdio transport."""
    mcp.run()


if __name__ == "__main__":
    main()
