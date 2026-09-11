"""Pure MCP server factory for Local-MCP-Bridge."""

from mcp.server import MCPServer
from typing_extensions import TypedDict

from local_mcp_bridge import __version__
from local_mcp_bridge.jobs import (
    DEFAULT_LIST_LIMIT,
    DEFAULT_OUTPUT_CHARS,
    JobCancelResult,
    JobListResult,
    JobManager,
    JobOutputResult,
    JobStartResult,
    JobSummary,
)
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
    jobs_enabled: bool
    persistent_jobs: bool


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
    execution_service: ExecutionService | None = None,
    job_manager: JobManager | None = None,
) -> MCPServer:
    """Create a bridge server bound to explicitly supplied runtime services.

    This factory intentionally avoids machine-local config and persistent state.
    The real runtime module supplies a disk-backed job manager; tests use memory.
    """
    active_registry = registry if registry is not None else ProjectRegistry.empty()
    filesystem = FilesystemService(active_registry, filesystem_limits)

    if (execution_service is None) != (job_manager is None):
        raise ValueError("execution_service and job_manager must be supplied together.")
    execution = execution_service or ExecutionService(active_registry)
    jobs = job_manager or JobManager(active_registry, execution)
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
            jobs_enabled=True,
            persistent_jobs=jobs.persistent,
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
        """Run one allowlisted executable synchronously under bounded local policy."""
        return await execution.run_process(
            project_id=project_id,
            executable=executable,
            args=args,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
        )

    @server.tool()
    async def start_job(
        project_id: str,
        executable: str,
        args: list[str] | None = None,
        cwd: str = ".",
        timeout_seconds: int | None = None,
    ) -> JobStartResult:
        """Start an allowlisted process in the background and return an opaque job ID."""
        return await jobs.start_job(
            project_id=project_id,
            executable=executable,
            args=args,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
        )

    @server.tool()
    def get_job(job_id: str) -> JobSummary:
        """Return safe metadata for one managed background job."""
        return jobs.get_job(job_id)

    @server.tool()
    def list_jobs(
        project_id: str | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> JobListResult:
        """List recent managed jobs without raw argv or host paths."""
        return jobs.list_jobs(project_id=project_id, limit=limit)

    @server.tool()
    def get_job_output(
        job_id: str,
        stream: str = "stdout",
        offset: int = 0,
        max_chars: int = DEFAULT_OUTPUT_CHARS,
    ) -> JobOutputResult:
        """Read one bounded page of sanitized stdout or stderr for a managed job."""
        if stream not in ("stdout", "stderr"):
            raise ValueError("stream must be either 'stdout' or 'stderr'.")
        return jobs.get_job_output(
            job_id=job_id,
            stream=stream,
            offset=offset,
            max_chars=max_chars,
        )

    @server.tool()
    async def cancel_job(job_id: str) -> JobCancelResult:
        """Cancel a supervised running job."""
        return await jobs.cancel_job(job_id)

    return server
