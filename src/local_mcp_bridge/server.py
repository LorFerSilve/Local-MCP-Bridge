"""MCP server entry point for Local-MCP-Bridge."""

from mcp.server import MCPServer
from typing_extensions import TypedDict

from local_mcp_bridge import __version__
from local_mcp_bridge.config import load_runtime_registry
from local_mcp_bridge.registry import ProjectRegistry, PublicProject

SERVER_NAME = "Local MCP Bridge"


class HealthStatus(TypedDict):
    """Non-sensitive bridge status."""

    status: str
    server: str
    version: str
    projects_configured: int
    filesystem_enabled: bool
    execution_enabled: bool


class ProjectListStatus(TypedDict):
    """MCP-safe list of configured projects."""

    projects: list[PublicProject]


class ProjectLookupStatus(TypedDict):
    """MCP-safe result for a project metadata lookup."""

    found: bool
    project: PublicProject | None


def create_mcp_server(registry: ProjectRegistry | None = None) -> MCPServer:
    """Create a bridge server bound to an immutable project registry."""
    active_registry = registry if registry is not None else ProjectRegistry.empty()
    server = MCPServer(SERVER_NAME)

    @server.tool()
    def health_check() -> HealthStatus:
        """Return basic bridge health without exposing host filesystem paths."""
        return HealthStatus(
            status="ok",
            server=SERVER_NAME,
            version=__version__,
            projects_configured=len(active_registry),
            filesystem_enabled=False,
            execution_enabled=False,
        )

    @server.tool()
    def list_projects() -> ProjectListStatus:
        """List authorized project IDs and capability flags without local paths."""
        return ProjectListStatus(projects=active_registry.list_public())

    @server.tool()
    def get_project(project_id: str) -> ProjectLookupStatus:
        """Return public metadata for one project ID without exposing its local root."""
        project = active_registry.get_public(project_id)
        return ProjectLookupStatus(found=project is not None, project=project)

    return server


mcp = create_mcp_server(load_runtime_registry())


def main() -> None:
    """Run the bridge over MCP's local stdio transport."""
    mcp.run()


if __name__ == "__main__":
    main()
