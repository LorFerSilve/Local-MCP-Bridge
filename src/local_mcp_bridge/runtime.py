"""Configured stdio runtime for Local-MCP-Bridge.

Unlike :mod:`local_mcp_bridge.server`, this module intentionally composes machine-local
project/Git policy, persistent job state, and the Phase 8 audit log. The shared
composition is also reused by the Phase 9 remote entry point so transport choice cannot
bypass the existing security layers.
"""

from mcp.server import MCPServer

from local_mcp_bridge.runtime_composition import create_runtime_composition


def create_runtime_server() -> MCPServer:
    """Create the configured bridge server for the local stdio transport."""
    return create_runtime_composition().server


mcp = create_runtime_server()


def main() -> None:
    """Run the configured bridge over MCP's local stdio transport."""
    mcp.run()
