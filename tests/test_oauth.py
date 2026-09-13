"""Phase 10.5 preconfigured OAuth integration tests."""

from __future__ import annotations

import asyncio
import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from local_mcp_bridge.oauth import (
    AUTH_MODE_OAUTH,
    AUTH_MODE_PRE_SHARED_BEARER,
    CLAUDE_CALLBACK_URL,
    OAUTH_CLIENT_ID_ENV_VAR,
    OAUTH_CLIENT_SECRET_ENV_VAR,
    REMOTE_AUTH_MODE_ENV_VAR,
    PreconfiguredOAuthProvider,
    build_oauth_auth_settings,
    install_oauth_consent_route,
    load_oauth_client_credentials,
    load_remote_auth_mode,
)
from local_mcp_bridge.remote_config import RemoteConfigError, RemoteSettings
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


def _oauth_server() -> tuple[object, PreconfiguredOAuthProvider, object]:
    provider = PreconfiguredOAuthProvider(
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
    app = create_remote_app(server, _settings(), sdk_oauth=True)
    return server, provider, app


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def test_remote_auth_mode_is_explicit_and_defaults_to_existing_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(REMOTE_AUTH_MODE_ENV_VAR, raising=False)
    assert load_remote_auth_mode() == AUTH_MODE_PRE_SHARED_BEARER

    monkeypatch.setenv(REMOTE_AUTH_MODE_ENV_VAR, AUTH_MODE_OAUTH)
    assert load_remote_auth_mode() == AUTH_MODE_OAUTH

    monkeypatch.setenv(REMOTE_AUTH_MODE_ENV_VAR, "disabled")
    with pytest.raises(RemoteConfigError, match=REMOTE_AUTH_MODE_ENV_VAR):
        load_remote_auth_mode()


def test_oauth_credentials_are_required_and_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OAUTH_CLIENT_ID_ENV_VAR, raising=False)
    monkeypatch.delenv(OAUTH_CLIENT_SECRET_ENV_VAR, raising=False)
    with pytest.raises(RemoteConfigError, match=OAUTH_CLIENT_ID_ENV_VAR):
        load_oauth_client_credentials()

    monkeypatch.setenv(OAUTH_CLIENT_ID_ENV_VAR, CLIENT_ID)
    monkeypatch.setenv(OAUTH_CLIENT_SECRET_ENV_VAR, "short")
    with pytest.raises(RemoteConfigError, match=OAUTH_CLIENT_SECRET_ENV_VAR):
        load_oauth_client_credentials()

    monkeypatch.setenv(OAUTH_CLIENT_SECRET_ENV_VAR, CLIENT_SECRET)
    assert load_oauth_client_credentials() == (CLIENT_ID, CLIENT_SECRET)


def test_oauth_metadata_challenge_and_no_dynamic_registration() -> None:
    async def scenario() -> None:
        server, _, app = _oauth_server()
        transport = httpx2.ASGITransport(app=app)
        async with (
            server.session_manager.run(),
            httpx2.AsyncClient(
                transport=transport,
                base_url=PUBLIC_ORIGIN,
                follow_redirects=False,
            ) as client,
        ):
            prm = await client.get("/.well-known/oauth-protected-resource/mcp")
            metadata = await client.get("/.well-known/oauth-authorization-server")
            registration = await client.post("/register", json={})
            unauthorized = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            )

        assert prm.status_code == 200
        assert prm.json()["resource"] == RESOURCE_URL
        assert metadata.status_code == 200
        assert metadata.json().get("registration_endpoint") is None
        assert registration.status_code == 404
        assert unauthorized.status_code == 401
        challenge = unauthorized.headers["www-authenticate"]
        assert "Bearer" in challenge
        assert "resource_metadata=" in challenge

    asyncio.run(scenario())


def test_oauth_authorization_code_flow_reaches_mcp_and_rotates_refresh_token() -> None:
    async def scenario() -> None:
        server, _, app = _oauth_server()
        transport = httpx2.ASGITransport(app=app)
        verifier = "v" * 64
        challenge = _pkce_challenge(verifier)

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
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "scope": "mcp",
                    "state": "state-123",
                    "resource": RESOURCE_URL,
                },
            )
            assert authorize.status_code == 302
            consent_url = authorize.headers["location"]
            consent_query = parse_qs(urlsplit(consent_url).query)
            request_id = consent_query["request"][0]

            consent = await client.get(consent_url)
            assert consent.status_code == 200
            assert "Authorize Local-MCP-Bridge" in consent.text
            assert CLIENT_SECRET not in consent.text

            approved = await client.post(
                "/oauth/consent",
                data={"request": request_id, "decision": "allow"},
            )
            assert approved.status_code == 302
            callback = urlsplit(approved.headers["location"])
            callback_params = parse_qs(callback.query)
            assert f"{callback.scheme}://{callback.netloc}{callback.path}" == CLAUDE_CALLBACK_URL
            assert callback_params["state"] == ["state-123"]
            code = callback_params["code"][0]

            token_response = await client.post(
                "/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": CLAUDE_CALLBACK_URL,
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "code_verifier": verifier,
                    "resource": RESOURCE_URL,
                },
            )
            assert token_response.status_code == 200
            tokens = token_response.json()
            access_token = tokens["access_token"]
            refresh_token = tokens["refresh_token"]
            assert tokens["token_type"] == "Bearer"
            assert access_token != CLIENT_SECRET

            async with (
                httpx2.AsyncClient(
                    transport=transport,
                    base_url=RESOURCE_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                ) as authenticated_http,
                Client(
                    streamable_http_client(RESOURCE_URL, http_client=authenticated_http)
                ) as mcp_client,
            ):
                result = await mcp_client.call_tool("health_check", {})
            assert result.is_error is False
            assert result.structured_content is not None
            assert result.structured_content["status"] == "ok"

            refreshed = await client.post(
                "/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "scope": "mcp",
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "resource": RESOURCE_URL,
                },
            )
            assert refreshed.status_code == 200
            assert refreshed.json()["refresh_token"] != refresh_token

            replay = await client.post(
                "/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "scope": "mcp",
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "resource": RESOURCE_URL,
                },
            )
            assert replay.status_code == 400
            assert replay.json()["error"] == "invalid_grant"

    asyncio.run(scenario())


def test_oauth_rejects_unregistered_redirect_uri() -> None:
    async def scenario() -> None:
        server, _, app = _oauth_server()
        transport = httpx2.ASGITransport(app=app)
        async with (
            server.session_manager.run(),
            httpx2.AsyncClient(
                transport=transport,
                base_url=PUBLIC_ORIGIN,
                follow_redirects=False,
            ) as client,
        ):
            response = await client.get(
                "/authorize",
                params={
                    "response_type": "code",
                    "client_id": CLIENT_ID,
                    "redirect_uri": "https://evil.example/callback",
                    "code_challenge": _pkce_challenge("x" * 64),
                    "code_challenge_method": "S256",
                    "scope": "mcp",
                    "resource": RESOURCE_URL,
                },
            )
        assert response.status_code == 400

    asyncio.run(scenario())
