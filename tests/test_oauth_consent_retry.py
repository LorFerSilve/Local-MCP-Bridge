"""Regression coverage for the live Claude consent retry path."""

from __future__ import annotations

import asyncio
import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import httpx2

from local_mcp_bridge.oauth import (
    CLAUDE_CALLBACK_URL,
    build_oauth_auth_settings,
    install_oauth_consent_route,
)
from local_mcp_bridge.oauth_consent import RetrySafeOAuthProvider
from local_mcp_bridge.remote_config import RemoteSettings
from local_mcp_bridge.remote_transport import create_remote_app
from local_mcp_bridge.server import create_mcp_server

CLIENT_ID = "claude-local-bridge-client-01"
CLIENT_SECRET = "S" * 64
RESOURCE_URL = "https://mcp.example.com/mcp"
PUBLIC_ORIGIN = "https://mcp.example.com"


def _settings() -> RemoteSettings:
    return RemoteSettings(
        enabled=True,
        bind_host="127.0.0.1",
        port=8765,
        public_url=RESOURCE_URL,
        public_host="mcp.example.com",
        public_origin=PUBLIC_ORIGIN,
        max_request_body_bytes=262_144,
        session_idle_timeout_seconds=300,
        max_sessions=32,
    )


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _app() -> tuple[object, object]:
    provider = RetrySafeOAuthProvider(
        public_origin=PUBLIC_ORIGIN,
        resource_url=RESOURCE_URL,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
    )
    server = create_mcp_server(
        auth_settings=build_oauth_auth_settings(
            public_origin=PUBLIC_ORIGIN,
            resource_url=RESOURCE_URL,
        ),
        auth_server_provider=provider,
    )
    install_oauth_consent_route(server, provider)
    return server, create_remote_app(server, _settings(), sdk_oauth=True)


def test_consent_request_id_survives_body_loss_and_duplicate_submit() -> None:
    async def scenario() -> None:
        server, app = _app()
        verifier = "v" * 64
        transport = httpx2.ASGITransport(app=app)

        async with (
            server.session_manager.run(),
            httpx2.AsyncClient(
                transport=transport,
                base_url=PUBLIC_ORIGIN,
                follow_redirects=False,
            ) as client,
        ):
            authorize = await client.get(
                "/authorize",
                params={
                    "response_type": "code",
                    "client_id": CLIENT_ID,
                    "redirect_uri": CLAUDE_CALLBACK_URL,
                    "code_challenge": _pkce_challenge(verifier),
                    "code_challenge_method": "S256",
                    "scope": "mcp",
                    "state": "state-123",
                    "resource": RESOURCE_URL,
                },
            )
            assert authorize.status_code == 302
            consent_url = authorize.headers["location"]
            request_id = parse_qs(urlsplit(consent_url).query)["request"][0]

            consent = await client.get(consent_url)
            assert consent.status_code == 200
            assert f"/oauth/consent?request={request_id}" in consent.text
            assert (
                "form-action 'self' https://claude.ai"
                in consent.headers["content-security-policy"]
            )

            first = await client.post(
                consent_url,
                data={"decision": "allow"},
            )
            assert first.status_code == 302
            assert first.headers["location"].startswith(CLAUDE_CALLBACK_URL)

            retry = await client.post(
                consent_url,
                data={"request": request_id, "decision": "allow"},
            )
            assert retry.status_code == 302
            assert retry.headers["location"] == first.headers["location"]

            refresh = await client.get(consent_url)
            assert refresh.status_code == 302
            assert refresh.headers["location"] == first.headers["location"]

    asyncio.run(scenario())


def test_consent_rejects_query_and_body_request_mismatch() -> None:
    async def scenario() -> None:
        server, app = _app()
        transport = httpx2.ASGITransport(app=app)

        async with (
            server.session_manager.run(),
            httpx2.AsyncClient(
                transport=transport,
                base_url=PUBLIC_ORIGIN,
                follow_redirects=False,
            ) as client,
        ):
            authorize = await client.get(
                "/authorize",
                params={
                    "response_type": "code",
                    "client_id": CLIENT_ID,
                    "redirect_uri": CLAUDE_CALLBACK_URL,
                    "code_challenge": _pkce_challenge("x" * 64),
                    "code_challenge_method": "S256",
                    "scope": "mcp",
                    "resource": RESOURCE_URL,
                },
            )
            consent_url = authorize.headers["location"]
            response = await client.post(
                consent_url,
                data={"request": "different-request-id", "decision": "allow"},
            )

        assert response.status_code == 400
        assert response.text == "Authorization form request mismatch."

    asyncio.run(scenario())
