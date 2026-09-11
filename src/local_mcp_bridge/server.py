"""Pure MCP server factory for Local-MCP-Bridge."""

from mcp.server import MCPServer
from typing_extensions import TypedDict

from local_mcp_bridge import __version__
from local_mcp_bridge.registry import ProjectRegistry, PublicProject
from local_mcp_bridge.tools.execution import ExecutionService, ProcessResult
from local_mcp_bridge.tools.filesystem import (
    DEFAULT_READ_LINES,
    DEFAULT_SEARCH_RESULTS,
    DirectoryListResult,
    FilesystemLimits,
    FilesystemService,
    ReadFileResult,
    SearchTextResult,
)

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


def create_mcp_server(
    registry: ProjectRegistry | None = None,
    filesystem_limits: FilesystemLimits | None = None,
) -> MCPServer:
    """Create a bridge server bound to an explicitly supplied registry.

    This factory intentionally does not read local runtime configuration. That
    keeps imports deterministic and makes unit tests independent of a user's
    machine-specific ``config/config.yaml``.
    """
    active_registry = registry if registry is not None else ProjectRegistry.empty()
    filesystem = FilesystemService(active_registry, filesystem_limits)
    execution = ExecutionService(active_registry)
    server = MCPServer(SERVER_NAME)

    @server.tool()
    def health_check() -> HealthStatus:
        """Return basic bridge health without exposing host filesystem paths."""
        return HealthStatus(
            status="ok",
            server=SERVER_NAME,
            version=__version__,
            projects_configured=len(active_registry),
            filesystem_enabled=True,
            execution_enabled=True,
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

    @server.tool()
    def list_directory(project_id: str, path: str = ".") -> DirectoryListResult:
        """List a bounded directory inside an authorized project root."""
        return filesystem.list_directory(project_id, path)

    @server.tool()
    def read_file(
        project_id: str,
        path: str,
        start_line: int = 1,
        max_lines: int = DEFAULT_READ_LINES,
    ) -> ReadFileResult:
        """Read a bounded UTF-8 text slice from an authorized project file."""
        return filesystem.read_file(project_id, path, start_line, max_lines)

    @server.tool()
    def search_text(
        project_id: str,
        query: str,
        path: str = ".",
        case_sensitive: bool = False,
        max_results: int = DEFAULT_SEARCH_RESULTS,
    ) -> SearchTextResult:
        """Search authorized UTF-8 project files using a bounded plain-text query."""
        return filesystem.search_text(
            project_id,
            query,
            path,
            case_sensitive,
            max_results,
        )

    @server.tool()
    async def run_process(
        project_id: str,
        executable: str,
        args: list[str] | None = None,
        cwd: str = ".",
        timeout_seconds: int | None = None,
    ) -> ProcessResult:
        """Run one allowlisted executable without a shell inside a confined project cwd."""
        return await execution.run_process(
            project_id=project_id,
            executable=executable,
            args=args,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
        )

    return server
