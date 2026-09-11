"""Runtime wiring for Local-MCP-Bridge.

Unlike :mod:`local_mcp_bridge.server`, this module intentionally loads the
machine-local project registry. MCP hosts that need the real configured
projects should target this module.
"""

from mcp.server import MCPServer

from local_mcp_bridge.config import load_runtime_registry
from local_mcp_bridge.server import create_mcp_server


def create_runtime_server() -> MCPServer:
    """Create an MCP server using the machine-local runtime configuration."""
    return create_mcp_server(load_runtime_registry())


mcp = create_runtime_server()


def main() -> None:
    """Run the configured bridge over MCP's local stdio transport."""
    mcp.run()
