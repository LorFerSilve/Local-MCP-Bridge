"""Regression tests for runtime-config isolation."""

import os
import subprocess
import sys
from pathlib import Path


def test_server_import_ignores_invalid_local_runtime_config(tmp_path: Path) -> None:
    """Importing the pure server factory must never read machine-local config."""
    env = os.environ.copy()
    env["LOCAL_MCP_BRIDGE_CONFIG"] = str(tmp_path / "definitely-missing-config.yaml")

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import local_mcp_bridge.server; print('server-import-ok')",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "server-import-ok"


def test_runtime_import_still_fails_closed_for_invalid_explicit_config(tmp_path: Path) -> None:
    """The runtime wiring must still reject an explicitly invalid local config."""
    env = os.environ.copy()
    missing_config = tmp_path / "definitely-missing-config.yaml"
    env["LOCAL_MCP_BRIDGE_CONFIG"] = str(missing_config)

    result = subprocess.run(
        [sys.executable, "-c", "import local_mcp_bridge.runtime"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "ConfigError" in result.stderr
    assert "does not exist" in result.stderr
