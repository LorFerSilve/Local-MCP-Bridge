"""Shared machine-local runtime composition for stdio and remote transports.

Transport entry points reuse this module so Streamable HTTP cannot accidentally skip
the project registry, Git overlay, persistent job manager, or Phase 8 audit boundary.
Importing this module itself remains side-effect free; state is loaded only when
``create_runtime_composition`` is called.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from mcp.server import MCPServer

from local_mcp_bridge.audit import AuditLogger
from local_mcp_bridge.config import load_runtime_registry
from local_mcp_bridge.git_config import load_runtime_git_registry
from local_mcp_bridge.jobs import JobManager
from local_mcp_bridge.registry import ProjectRegistry
from local_mcp_bridge.server import create_mcp_server
from local_mcp_bridge.tools.execution import ExecutionService

JOB_STATE_ENV_VAR = "LOCAL_MCP_BRIDGE_JOB_STATE_DIR"
AUDIT_DIR_ENV_VAR = "LOCAL_MCP_BRIDGE_AUDIT_DIR"
DEFAULT_JOB_STATE_DIR = Path("runtime/jobs")
DEFAULT_AUDIT_DIR = Path("runtime/audit")


@dataclass(frozen=True, slots=True)
class RuntimeComposition:
    """Security-relevant services composing one configured bridge process."""

    registry: ProjectRegistry
    audit: AuditLogger
    jobs: JobManager
    server: MCPServer


def _runtime_directory(env_var: str, default: Path) -> Path:
    configured = os.getenv(env_var)
    if not configured:
        return default

    path = Path(configured).expanduser()
    if not path.is_absolute():
        raise RuntimeError(f"{env_var} must be an absolute path when supplied.")
    return path


def job_state_dir() -> Path:
    return _runtime_directory(JOB_STATE_ENV_VAR, DEFAULT_JOB_STATE_DIR)


def audit_dir() -> Path:
    return _runtime_directory(AUDIT_DIR_ENV_VAR, DEFAULT_AUDIT_DIR)


def create_runtime_composition() -> RuntimeComposition:
    """Create all configured services once, with identical policy for every transport."""
    registry = load_runtime_git_registry(load_runtime_registry())
    audit = AuditLogger(audit_dir())
    audit.record(
        "runtime.bootstrap",
        "attempt",
        details={"projects_configured": len(registry)},
    )

    execution = ExecutionService(registry)
    jobs = JobManager(registry, execution, state_dir=job_state_dir())
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
    return RuntimeComposition(registry=registry, audit=audit, jobs=jobs, server=server)
