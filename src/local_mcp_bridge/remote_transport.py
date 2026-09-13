"""Authenticated loopback-only Streamable HTTP hosting for remote MCP.

The bridge deliberately does not open a public socket and does not launch a tunnel
provider. A separate trusted tunnel/reverse-proxy process terminates public HTTPS and
forwards to this loopback listener. Remote authentication is explicit: the Phase 9
pre-shared bearer gate remains the default, while Phase 10.5 can delegate authentication
to the MCP SDK's OAuth authorization/resource-server middleware.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Awaitable, Callable
from typing import Any

import uvicorn
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from local_mcp_bridge.remote_config import MAX_REMOTE_TOKEN_CHARS, RemoteSettings

ASGIScope = dict[str, Any]
ASGIMessage = dict[str, Any]
ASGIReceive = Callable[[], Awaitable[ASGIMessage]]
ASGISend = Callable[[ASGIMessage], Awaitable[None]]
ASGIApp = Callable[[ASGIScope, ASGIReceive, ASGISend], Awaitable[None]]

REMOTE_HTTP_CONCURRENCY_LIMIT = 64
REMOTE_HTTP_BACKLOG = 64
REMOTE_KEEP_ALIVE_SECONDS = 5
_LOOPBACK_HOSTS = {"127.0.0.1", "::1"}


class BearerAuthMiddleware:
    """Require exactly one constant-time-verified Bearer header on every HTTP request.

    The validated Authorization header is removed before the MCP application receives the
    scope. This minimizes the chance that a future tool/context helper accidentally reflects
    the transport credential into model-visible data or application logs.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._token = token.encode("ascii")

    @staticmethod
    def _extract_bearer(scope: ASGIScope) -> bytes | None:
        values = [
            value
            for key, value in scope.get("headers", [])
            if bytes(key).lower() == b"authorization"
        ]
        if len(values) != 1:
            return None

        try:
            raw = bytes(values[0]).decode("latin-1")
        except (TypeError, UnicodeError):
            return None
        parts = raw.split(" ")
        if len(parts) != 2 or parts[0].casefold() != "bearer" or not parts[1]:
            return None
        candidate = parts[1]
        if len(candidate) > MAX_REMOTE_TOKEN_CHARS or not candidate.isascii():
            return None
        try:
            return candidate.encode("ascii")
        except UnicodeEncodeError:  # pragma: no cover - guarded by isascii()
            return None

    @staticmethod
    async def _unauthorized(send: ASGISend) -> None:
        body = json.dumps(
            {"error": "unauthorized"},
            separators=(",", ":"),
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                    (b"www-authenticate", b"Bearer"),
                    (b"x-content-type-options", b"nosniff"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        candidate = self._extract_bearer(scope)
        if candidate is None or not hmac.compare_digest(candidate, self._token):
            await self._unauthorized(send)
            return

        clean_scope = dict(scope)
        clean_scope["headers"] = [
            (key, value)
            for key, value in scope.get("headers", [])
            if bytes(key).lower() not in {b"authorization", b"proxy-authorization"}
        ]
        await self._app(clean_scope, receive, send)


def _require_loopback(settings: RemoteSettings) -> None:
    if settings.bind_host not in _LOOPBACK_HOSTS:
        raise RuntimeError("Remote transport may bind only to an explicit loopback address.")


def create_remote_app(
    server: MCPServer,
    settings: RemoteSettings,
    token: str | None = None,
    *,
    sdk_oauth: bool = False,
) -> ASGIApp:
    """Build a protected Streamable HTTP ASGI app without binding a socket.

    ``sdk_oauth`` is an explicit opt-in. Without it the original pre-shared bearer token is
    mandatory, preventing a missing token from accidentally producing an authless listener.
    """
    _require_loopback(settings)
    if sdk_oauth and token is not None:
        raise RuntimeError("OAuth mode may not also install the pre-shared bearer wrapper.")
    if not sdk_oauth and token is None:
        raise RuntimeError("Pre-shared bearer mode requires an explicit token.")

    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(settings.allowed_hosts),
        allowed_origins=list(settings.allowed_origins),
    )
    mcp_app = server.streamable_http_app(
        host=settings.bind_host,
        streamable_http_path="/mcp",
        stateless_http=False,
        max_request_body_size=settings.max_request_body_bytes,
        session_idle_timeout=settings.session_idle_timeout_seconds,
        max_sessions=settings.max_sessions,
        transport_security=transport_security,
    )
    if sdk_oauth:
        return mcp_app
    assert token is not None
    return BearerAuthMiddleware(mcp_app, token)


def serve_remote(
    server: MCPServer,
    settings: RemoteSettings,
    token: str | None = None,
    *,
    sdk_oauth: bool = False,
) -> None:
    """Serve the protected MCP app on loopback for an external HTTPS tunnel."""
    _require_loopback(settings)
    app = create_remote_app(server, settings, token, sdk_oauth=sdk_oauth)
    uvicorn.run(
        app,
        host=settings.bind_host,
        port=settings.port,
        access_log=False,
        server_header=False,
        proxy_headers=True,
        forwarded_allow_ips=settings.bind_host,
        limit_concurrency=REMOTE_HTTP_CONCURRENCY_LIMIT,
        backlog=REMOTE_HTTP_BACKLOG,
        timeout_keep_alive=REMOTE_KEEP_ALIVE_SECONDS,
        log_level="warning",
    )
