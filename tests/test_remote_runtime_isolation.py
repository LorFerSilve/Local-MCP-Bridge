"""Regression tests for Phase 9 remote-runtime isolation."""

import os
import subprocess
import sys
from pathlib import Path


def test_remote_runtime_module_import_is_side_effect_free(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["LOCAL_MCP_BRIDGE_REMOTE_CONFIG"] = str(tmp_path / "missing-remote.yaml")
    env.pop("LOCAL_MCP_BRIDGE_REMOTE_TOKEN", None)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import local_mcp_bridge.remote_runtime; print('remote-import-ok')",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "remote-import-ok"


def test_remote_runtime_main_refuses_missing_policy_before_startup(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["LOCAL_MCP_BRIDGE_REMOTE_CONFIG"] = str(tmp_path / "missing-remote.yaml")
    env["LOCAL_MCP_BRIDGE_REMOTE_TOKEN"] = "A" * 64

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from local_mcp_bridge.remote_runtime import main; main()",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "RemoteConfigError" in result.stderr
    assert "Cannot access remote configuration file" in result.stderr
