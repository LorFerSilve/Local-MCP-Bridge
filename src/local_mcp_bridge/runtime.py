"""Runtime wiring for Local-MCP-Bridge.

Unlike :mod:`local_mcp_bridge.server`, this module intentionally loads machine-local
configuration, the optional local Git-policy overlay, disk-backed job state, and the
local Phase 8 audit log. Reusable server-factory tests remain hermetic because only
this runtime composition root touches machine-local state.
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server import MCPServer

from local_mcp_bridge.audit import AuditLogger
from local_mcp_bridge.config import load_runtime_registry
from local_mcp_bridge.git_config import load_runtime_git_registry
from local_mcp_bridge.jobs import JobManager
from local_mcp_bridge.server import create_mcp_server
from local_mcp_bridge.tools.execution import ExecutionService

JOB_STATE_ENV_VAR = "LOCAL_MCP_BRIDGE_JOB_STATE_DIR"
AUDIT_DIR_ENV_VAR = "LOCAL_MCP_BRIDGE_AUDIT_DIR"
DEFAULT_JOB_STATE_DIR = Path("runtime/jobs")
DEFAULT_AUDIT_DIR = Path("runtime/audit")


def _runtime_directory(env_var: str, default: Path) -> Path:
    configured = os.getenv(env_var)
    if not configured:
        return default

    path = Path(configured).expanduser()
    if not path.is_absolute():
        raise RuntimeError(f"{env_var} must be an absolute path when supplied.")
    return path


def _job_state_dir() -> Path:
    return _runtime_directory(JOB_STATE_ENV_VAR, DEFAULT_JOB_STATE_DIR)


def _audit_dir() -> Path:
    return _runtime_directory(AUDIT_DIR_ENV_VAR, DEFAULT_AUDIT_DIR)


def create_runtime_server() -> MCPServer:
    """Create an MCP server using local project, Git, job, and audit policy."""
    registry = load_runtime_git_registry(load_runtime_registry())
    audit = AuditLogger(_audit_dir())
    audit.record(
        "runtime.bootstrap",
        "attempt",
        details={"projects_configured": len(registry)},
    )

    execution = ExecutionService(registry)
    jobs = JobManager(registry, execution, state_dir=_job_state_dir())
    server = create_mcp_server(
        registry,
        execution_service=execution,
        job_manager=jobs,
        audit_logger=audit,
    )
    audit.record(
        "runtime.bootstrap",
        "success",
        details={
            "projects_configured": len(registry),
            "persistent_jobs": jobs.persistent,
        },
    )
    return server


mcp = create_runtime_server()


def main() -> None:
    """Run the configured bridge over MCP's local stdio transport."""
    mcp.run()
