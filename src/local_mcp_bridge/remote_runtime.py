"""Explicit Phase 9 remote/tunnel runtime entry point.

Running the normal package still uses stdio. Network exposure requires this separate entry
point plus an enabled local remote policy and a memory-only bearer token. The remote
listener is loopback-only; a separately managed tunnel or reverse proxy provides public
HTTPS.
"""

from __future__ import annotations

from local_mcp_bridge.remote_config import load_remote_token, load_runtime_remote_settings
from local_mcp_bridge.remote_transport import serve_remote
from local_mcp_bridge.runtime_composition import create_runtime_composition


def main() -> None:
    """Validate remote policy/auth first, then compose and serve the hardened runtime."""
    settings = load_runtime_remote_settings()
    token = load_remote_token()
    composition = create_runtime_composition()
    serve_remote(composition.server, settings, token)


if __name__ == "__main__":  # pragma: no cover - exercised through the console entry point
    main()
