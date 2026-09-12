"""Runtime wiring for Local-MCP-Bridge.

Unlike :mod:`local_mcp_bridge.server`, this module intentionally loads machine-local
configuration, the optional local Git-policy overlay, and disk-backed job state.
Reusable server-factory tests remain hermetic because only this runtime module touches
machine-local state.
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server import MCPServer

from local_mcp_bridge.config import load_runtime_registry
from local_mcp_bridge.git_config import load_runtime_git_registry
from local_mcp_bridge.jobs import JobManager
from local_mcp_bridge.server import create_mcp_server
from local_mcp_bridge.tools.execution import ExecutionService

JOB_STATE_ENV_VAR = "LOCAL_MCP_BRIDGE_JOB_STATE_DIR"
DEFAULT_JOB_STATE_DIR = Path("runtime/jobs")


def _job_state_dir() -> Path:
    configured = os.getenv(JOB_STATE_ENV_VAR)
    if not configured:
        return DEFAULT_JOB_STATE_DIR

    path = Path(configured).expanduser()
    if not path.is_absolute():
        raise RuntimeError(f"{JOB_STATE_ENV_VAR} must be an absolute path when supplied.")
    return path


def create_runtime_server() -> MCPServer:
    """Create an MCP server using local project, Git, and persistent-job policy."""
    registry = load_runtime_git_registry(load_runtime_registry())
    execution = ExecutionService(registry)
    jobs = JobManager(registry, execution, state_dir=_job_state_dir())
    return create_mcp_server(
        registry,
        execution_service=execution,
        job_manager=jobs,
    )


mcp = create_runtime_server()


def main() -> None:
    """Run the configured bridge over MCP's local stdio transport."""
    mcp.run()
