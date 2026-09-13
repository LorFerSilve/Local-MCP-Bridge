"""Explicit remote/tunnel runtime entry point.

Running the normal package still uses stdio. Network exposure requires this separate entry
point plus an enabled local remote policy. The remote listener is loopback-only; a
separately managed tunnel or reverse proxy provides public HTTPS.
"""

from __future__ import annotations

from local_mcp_bridge.oauth import (
    AUTH_MODE_OAUTH,
    PreconfiguredOAuthProvider,
    build_oauth_auth_settings,
    install_oauth_consent_route,
    load_oauth_client_credentials,
    load_remote_auth_mode,
)
from local_mcp_bridge.remote_config import load_remote_token, load_runtime_remote_settings
from local_mcp_bridge.remote_transport import serve_remote
from local_mcp_bridge.runtime_composition import create_runtime_composition


def main() -> None:
    """Validate remote policy/auth first, then compose and serve the hardened runtime."""
    settings = load_runtime_remote_settings()
    auth_mode = load_remote_auth_mode()

    if auth_mode == AUTH_MODE_OAUTH:
        client_id, client_secret = load_oauth_client_credentials()
        provider = PreconfiguredOAuthProvider(
            public_origin=settings.public_origin,
            resource_url=settings.public_url,
            client_id=client_id,
            client_secret=client_secret,
        )
        auth_settings = build_oauth_auth_settings(
            public_origin=settings.public_origin,
            resource_url=settings.public_url,
        )
        composition = create_runtime_composition(
            auth_settings=auth_settings,
            auth_server_provider=provider,
        )
        install_oauth_consent_route(composition.server, provider)
        serve_remote(composition.server, settings, sdk_oauth=True)
        return

    token = load_remote_token()
    composition = create_runtime_composition()
    serve_remote(composition.server, settings, token)


if __name__ == "__main__":  # pragma: no cover - exercised through the console entry point
    main()
