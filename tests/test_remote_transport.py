"""Authenticated Streamable HTTP boundary tests."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from local_mcp_bridge.remote_config import RemoteSettings
from local_mcp_bridge.remote_transport import BearerAuthMiddleware, create_remote_app, serve_remote
from local_mcp_bridge.server import create_mcp_server

TOKEN = "T" * 64


def _settings(*, max_request_body_bytes: int = 262_144) -> RemoteSettings:
    return RemoteSettings(
        enabled=True,
        bind_host="127.0.0.1",
        port=8765,
        public_url="https://mcp.example.com/mcp",
        public_host="mcp.example.com",
        public_origin="https://mcp.example.com",
        max_request_body_bytes=max_request_body_bytes,
        session_idle_timeout_seconds=300,
        max_sessions=32,
    )


def test_remote_authentication_modes_fail_closed() -> None:
    server = create_mcp_server()

    with pytest.raises(RuntimeError, match="requires an explicit token"):
        create_remote_app(server, _settings())

    with pytest.raises(RuntimeError, match="may not also install"):
        create_remote_app(server, _settings(), TOKEN, sdk_oauth=True)


def test_bearer_middleware_rejects_missing_wrong_and_duplicate_headers() -> None:
    async def scenario() -> None:
        reached = False

        async def downstream(scope: dict[str, Any], receive: Any, send: Any) -> None:
            nonlocal reached
            reached = True
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        app = BearerAuthMiddleware(downstream, TOKEN)
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
            missing = await client.post("/mcp", content=b"{}")
            wrong = await client.post(
                "/mcp",
                content=b"{}",
                headers={"Authorization": "Bearer " + "W" * 64},
            )
            duplicate = await client.post(
                "/mcp",
                content=b"{}",
                headers=[
                    ("Authorization", "Bearer " + TOKEN),
                    ("Authorization", "Bearer " + TOKEN),
                ],
            )

        assert missing.status_code == 401
        assert wrong.status_code == 401
        assert duplicate.status_code == 401
        assert missing.headers["www-authenticate"] == "Bearer"
        assert TOKEN not in missing.text + wrong.text + duplicate.text
        assert reached is False

    asyncio.run(scenario())


def test_valid_bearer_is_scrubbed_before_downstream() -> None:
    async def scenario() -> None:
        seen_headers: list[tuple[bytes, bytes]] = []

        async def downstream(scope: dict[str, Any], receive: Any, send: Any) -> None:
            seen_headers.extend(scope["headers"])
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        app = BearerAuthMiddleware(downstream, TOKEN)
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/mcp",
                content=b"{}",
                headers={
                    "Authorization": "Bearer " + TOKEN,
                    "Proxy-Authorization": "Basic should-not-reach-tools",
                },
            )

        assert response.status_code == 204
        lowered = {key.lower() for key, _ in seen_headers}
        assert b"authorization" not in lowered
        assert b"proxy-authorization" not in lowered

    asyncio.run(scenario())


def test_authenticated_streamable_http_round_trip_reaches_mcp_tool() -> None:
    async def scenario() -> None:
        server = create_mcp_server()
        app = create_remote_app(server, _settings(), TOKEN)
        transport = httpx2.ASGITransport(app=app)
        url = "https://mcp.example.com/mcp"

        async with (
            server.session_manager.run(),
            httpx2.AsyncClient(
                transport=transport,
                base_url=url,
                headers={"Authorization": "Bearer " + TOKEN},
            ) as http_client,
            Client(streamable_http_client(url, http_client=http_client)) as client,
        ):
            result = await client.call_tool("health_check", {})

        assert result.is_error is False
        assert result.structured_content is not None
        assert result.structured_content["status"] == "ok"

    asyncio.run(scenario())


def test_transport_rejects_untrusted_host_and_origin_before_mcp_dispatch() -> None:
    async def scenario() -> None:
        server = create_mcp_server()
        app = create_remote_app(server, _settings(), TOKEN)
        transport = httpx2.ASGITransport(app=app)

        async with (
            server.session_manager.run(),
            httpx2.AsyncClient(
                transport=transport,
                base_url="http://127.0.0.1:8765",
                headers={"Authorization": "Bearer " + TOKEN},
            ) as client,
        ):
            bad_host = await client.post(
                "/mcp",
                content=b"{}",
                headers={
                    "Host": "evil.example",
                    "Content-Type": "application/json",
                },
            )
            bad_origin = await client.post(
                "/mcp",
                content=b"{}",
                headers={
                    "Origin": "https://evil.example",
                    "Content-Type": "application/json",
                },
            )

        assert bad_host.status_code == 421
        assert bad_origin.status_code == 403

    asyncio.run(scenario())


def test_transport_rejects_oversized_request_before_protocol_parsing() -> None:
    async def scenario() -> None:
        server = create_mcp_server()
        app = create_remote_app(server, _settings(max_request_body_bytes=1024), TOKEN)
        transport = httpx2.ASGITransport(app=app)

        async with httpx2.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8765",
            headers={"Authorization": "Bearer " + TOKEN},
        ) as client:
            response = await client.post(
                "/mcp",
                content=b"x" * 2048,
                headers={"Content-Type": "application/json"},
            )

        assert response.status_code == 413

    asyncio.run(scenario())


def test_remote_runner_uses_loopback_and_hardened_uvicorn_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(app: Any, **kwargs: Any) -> None:
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr("local_mcp_bridge.remote_transport.uvicorn.run", fake_run)

    serve_remote(create_mcp_server(), _settings(), TOKEN)

    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8765
    assert captured["access_log"] is False
    assert captured["server_header"] is False
    assert captured["proxy_headers"] is True
    assert captured["forwarded_allow_ips"] == "127.0.0.1"
    assert captured["limit_concurrency"] == 64
    assert captured["backlog"] == 64
    assert captured["timeout_keep_alive"] == 5
