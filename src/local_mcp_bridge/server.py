"""Pure MCP server factory for Local-MCP-Bridge."""

from mcp.server import MCPServer
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    OAuthAuthorizationServerProvider,
    RefreshToken,
)
from mcp.server.auth.settings import AuthSettings
from typing_extensions import TypedDict

from local_mcp_bridge import __version__
from local_mcp_bridge.audit import AuditError, AuditLogger
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
from local_mcp_bridge.tools.git_service import (
    GitFetchResult,
    GitService,
    GitStatusResult,
    GitSyncResult,
)

SERVER_NAME = "Local MCP Bridge"
OAuthProvider = OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]


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
    git_enabled: bool
    audit_enabled: bool
    audit_healthy: bool


class ProjectListStatus(TypedDict):
    """MCP-safe list of configured projects."""

    projects: list[PublicProject]


class ProjectLookupStatus(TypedDict):
    """MCP-safe result for a project metadata lookup."""

    found: bool
    project: PublicProject | None


def _audit_event(
    audit: AuditLogger,
    action: str,
    outcome: str,
    *,
    project_id: str | None = None,
    details: dict[str, str | int | bool | None] | None = None,
    strict: bool = False,
) -> None:
    """Record metadata without letting audit failures hide ordinary read results.

    Security-sensitive operations call this with ``strict=True`` *before* causing an
    effect. The configured runtime therefore refuses new execution/job/Git mutations if
    its audit log becomes unavailable. Completion logging is deliberately non-strict:
    once an effect happened, returning an artificial failure cannot undo it. A failed
    completion write marks the logger unhealthy so the next strict attempt fails closed.
    """
    try:
        audit.record(
            action,
            outcome,
            project_id=project_id,
            details=details,
        )
    except AuditError as exc:
        if strict:
            raise RuntimeError(
                "Audit logging is unavailable; security-sensitive operation refused."
            ) from exc


def create_mcp_server(
    registry: ProjectRegistry | None = None,
    filesystem_limits: FilesystemLimits | None = None,
    execution_service: ExecutionService | None = None,
    job_manager: JobManager | None = None,
    git_service: GitService | None = None,
    audit_logger: AuditLogger | None = None,
    auth_settings: AuthSettings | None = None,
    auth_server_provider: OAuthProvider | None = None,
) -> MCPServer:
    """Create a bridge server bound to explicitly supplied runtime services.

    This factory intentionally avoids machine-local config and persistent state.
    The real runtime module supplies the Git-enabled registry, disk-backed jobs, and
    persistent audit logger; tests can inject in-memory services or leave auditing off.
    OAuth is also explicit input so importing or reusing this factory never opts a caller
    into a network-facing authentication mode.
    """
    active_registry = registry if registry is not None else ProjectRegistry.empty()
    filesystem = FilesystemService(active_registry, filesystem_limits)

    if (execution_service is None) != (job_manager is None):
        raise ValueError("execution_service and job_manager must be supplied together.")
    execution = execution_service or ExecutionService(active_registry)
    jobs = job_manager or JobManager(active_registry, execution)
    git = git_service or GitService(active_registry)
    audit = audit_logger or AuditLogger()
    server = MCPServer(
        SERVER_NAME,
        auth=auth_settings,
        auth_server_provider=auth_server_provider,
    )

    @server.tool()
    def health_check() -> HealthStatus:
        """Return basic bridge health without exposing host filesystem paths."""
        return HealthStatus(
            status="ok" if audit.healthy else "degraded",
            server=SERVER_NAME,
            version=__version__,
            projects_configured=len(active_registry),
            filesystem_enabled=True,
            execution_enabled=True,
            jobs_enabled=True,
            persistent_jobs=jobs.persistent,
            git_enabled=True,
            audit_enabled=audit.enabled,
            audit_healthy=audit.healthy,
        )

    @server.tool()
    def list_projects() -> ProjectListStatus:
        """List authorized project IDs and capability flags without local paths."""
        return ProjectListStatus(projects=active_registry.list_public())

    @server.tool()
    def get_project(project_id: str) -> ProjectLookupStatus:
        """Return public metadata for one project ID without exposing its local root."""
        return ProjectLookupStatus(
            found=(project := active_registry.get_public(project_id)) is not None,
            project=project,
        )

    @server.tool()
    def list_directory(project_id: str, path: str = ".") -> DirectoryListResult:
        """List a bounded directory inside an authorized project root."""
        try:
            result = filesystem.list_directory(project_id, path)
        except Exception:
            _audit_event(audit, "filesystem.list_directory", "error", project_id=project_id)
            raise
        _audit_event(audit, "filesystem.list_directory", "success", project_id=project_id)
        return result

    @server.tool()
    def read_file(
        project_id: str,
        path: str,
        start_line: int = 1,
        max_lines: int = DEFAULT_READ_LINES,
    ) -> ReadFileResult:
        """Read a bounded UTF-8 text slice from an authorized project file."""
        try:
            result = filesystem.read_file(project_id, path, start_line, max_lines)
        except Exception:
            _audit_event(audit, "filesystem.read_file", "error", project_id=project_id)
            raise
        _audit_event(
            audit,
            "filesystem.read_file",
            "success",
            project_id=project_id,
            details={"requested_max_lines": max_lines},
        )
        return result

    @server.tool()
    def search_text(
        project_id: str,
        query: str,
        path: str = ".",
        case_sensitive: bool = False,
        max_results: int = DEFAULT_SEARCH_RESULTS,
    ) -> SearchTextResult:
        """Search authorized UTF-8 project files using a bounded plain-text query."""
        try:
            result = filesystem.search_text(
                project_id,
                query,
                path,
                case_sensitive,
                max_results,
            )
        except Exception:
            _audit_event(audit, "filesystem.search_text", "error", project_id=project_id)
            raise
        _audit_event(
            audit,
            "filesystem.search_text",
            "success",
            project_id=project_id,
            details={
                "case_sensitive": case_sensitive,
                "requested_max_results": max_results,
            },
        )
        return result

    @server.tool()
    async def run_process(
        project_id: str,
        executable: str,
        args: list[str] | None = None,
        cwd: str = ".",
        timeout_seconds: int | None = None,
    ) -> ProcessResult:
        """Run one allowlisted executable synchronously under bounded local policy."""
        _audit_event(
            audit,
            "process.run",
            "attempt",
            project_id=project_id,
            details={
                "argument_count": len(args or []),
                "timeout_overridden": timeout_seconds is not None,
            },
            strict=True,
        )
        try:
            result = await execution.run_process(
                project_id=project_id,
                executable=executable,
                args=args,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            _audit_event(audit, "process.run", "error", project_id=project_id)
            raise
        _audit_event(
            audit,
            "process.run",
            "success",
            project_id=project_id,
            details={
                "exit_code": result["exit_code"],
                "termination_reason": result["termination_reason"],
                "output_truncated": result["output_truncated"],
            },
        )
        return result

    @server.tool()
    async def start_job(
        project_id: str,
        executable: str,
        args: list[str] | None = None,
        cwd: str = ".",
        timeout_seconds: int | None = None,
    ) -> JobStartResult:
        """Start an allowlisted process in the background and return an opaque job ID."""
        _audit_event(
            audit,
            "job.start",
            "attempt",
            project_id=project_id,
            details={
                "argument_count": len(args or []),
                "timeout_overridden": timeout_seconds is not None,
            },
            strict=True,
        )
        try:
            result = await jobs.start_job(
                project_id=project_id,
                executable=executable,
                args=args,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            _audit_event(audit, "job.start", "error", project_id=project_id)
            raise
        _audit_event(
            audit,
            "job.start",
            "success",
            project_id=project_id,
            details={"status": result["job"]["status"]},
        )
        return result

    @server.tool()
    def get_job(job_id: str) -> JobSummary:
        """Return safe metadata for one managed background job."""
        try:
            result = jobs.get_job(job_id)
        except Exception:
            _audit_event(audit, "job.get", "error")
            raise
        _audit_event(audit, "job.get", "success", project_id=result["project_id"])
        return result

    @server.tool()
    def list_jobs(
        project_id: str | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> JobListResult:
        """List recent managed jobs without raw argv or host paths."""
        try:
            result = jobs.list_jobs(project_id=project_id, limit=limit)
        except Exception:
            _audit_event(audit, "job.list", "error", project_id=project_id)
            raise
        _audit_event(
            audit,
            "job.list",
            "success",
            project_id=project_id,
            details={"returned_jobs": len(result["jobs"]), "requested_limit": limit},
        )
        return result

    @server.tool()
    def get_job_output(
        job_id: str,
        stream: str = "stdout",
        offset: int = 0,
        max_chars: int = DEFAULT_OUTPUT_CHARS,
    ) -> JobOutputResult:
        """Read one bounded page of sanitized stdout or stderr for a managed job."""
        if stream not in ("stdout", "stderr"):
            _audit_event(audit, "job.output", "denied")
            raise ValueError("stream must be either 'stdout' or 'stderr'.")
        try:
            result = jobs.get_job_output(
                job_id=job_id,
                stream=stream,
                offset=offset,
                max_chars=max_chars,
            )
        except Exception:
            _audit_event(audit, "job.output", "error")
            raise
        _audit_event(
            audit,
            "job.output",
            "success",
            details={
                "stream": stream,
                "requested_max_chars": max_chars,
                "complete": result["complete"],
            },
        )
        return result

    @server.tool()
    async def cancel_job(job_id: str) -> JobCancelResult:
        """Cancel a supervised running job."""
        _audit_event(audit, "job.cancel", "attempt", strict=True)
        try:
            result = await jobs.cancel_job(job_id)
        except Exception:
            _audit_event(audit, "job.cancel", "error")
            raise
        _audit_event(
            audit,
            "job.cancel",
            "success",
            details={"accepted": result["accepted"], "status": result["status"]},
        )
        return result

    @server.tool()
    async def git_status(project_id: str) -> GitStatusResult:
        """Return path-free status and ahead/behind metadata for an authorized repository."""
        try:
            result = await git.git_status(project_id)
        except Exception:
            _audit_event(audit, "git.status", "error", project_id=project_id)
            raise
        _audit_event(
            audit,
            "git.status",
            "success",
            project_id=project_id,
            details={
                "clean": result["clean"],
                "ahead": result["ahead"],
                "behind": result["behind"],
            },
        )
        return result

    @server.tool()
    async def git_fetch(project_id: str) -> GitFetchResult:
        """Fetch only the configured branch from the trusted local HTTPS remote."""
        _audit_event(audit, "git.fetch", "attempt", project_id=project_id, strict=True)
        try:
            result = await git.git_fetch(project_id)
        except Exception:
            _audit_event(audit, "git.fetch", "error", project_id=project_id)
            raise
        _audit_event(
            audit,
            "git.fetch",
            "success",
            project_id=project_id,
            details={"changed": result["changed"]},
        )
        return result

    @server.tool()
    async def git_sync_fast_forward(project_id: str) -> GitSyncResult:
        """Fast-forward a clean checked-out branch to the verified fetched head."""
        _audit_event(
            audit,
            "git.sync_fast_forward",
            "attempt",
            project_id=project_id,
            strict=True,
        )
        try:
            result = await git.git_sync_fast_forward(project_id)
        except Exception:
            _audit_event(audit, "git.sync_fast_forward", "error", project_id=project_id)
            raise
        _audit_event(
            audit,
            "git.sync_fast_forward",
            "success",
            project_id=project_id,
            details={"updated": result["updated"]},
        )
        return result

    return server
