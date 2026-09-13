"""Preconfigured confidential-client OAuth for remote MCP clients.

The provider is intentionally narrow: one locally provisioned client, one fixed Claude
hosted callback URI, PKCE enforced by the MCP SDK, short-lived authorization codes and
access tokens, rotating refresh tokens, and process-local token state.
"""

from __future__ import annotations

import os
import re
import secrets
import time
from dataclasses import dataclass
from html import escape
from urllib.parse import parse_qs, quote

from mcp.server import MCPServer
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl, AnyUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from local_mcp_bridge.remote_config import RemoteConfigError

OAUTH_SCOPE = "mcp"
CLAUDE_CALLBACK_URL = "https://claude.ai/api/mcp/auth_callback"
AUTH_MODE_PRE_SHARED_BEARER = "pre_shared_bearer"
AUTH_MODE_OAUTH = "oauth"
REMOTE_AUTH_MODE_ENV_VAR = "LOCAL_MCP_BRIDGE_REMOTE_AUTH_MODE"
OAUTH_CLIENT_ID_ENV_VAR = "LOCAL_MCP_BRIDGE_OAUTH_CLIENT_ID"
OAUTH_CLIENT_SECRET_ENV_VAR = "LOCAL_MCP_BRIDGE_OAUTH_CLIENT_SECRET"
AUTHORIZATION_CODE_TTL_SECONDS = 300
ACCESS_TOKEN_TTL_SECONDS = 3600
REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60
PENDING_AUTHORIZATION_TTL_SECONDS = 300
MAX_PENDING_AUTHORIZATIONS = 128
MAX_CONSENT_FORM_BYTES = 8192
MIN_OAUTH_CLIENT_ID_CHARS = 16
MAX_OAUTH_CLIENT_ID_CHARS = 128
MIN_OAUTH_CLIENT_SECRET_CHARS = 43
MAX_OAUTH_CLIENT_SECRET_CHARS = 256
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9._~-]+")

_CONSENT_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


@dataclass(slots=True)
class _PendingAuthorization:
    client_id: str
    params: AuthorizationParams
    expires_at: float


def load_remote_auth_mode() -> str:
    """Return the explicitly selected remote authentication mode.

    The existing pre-shared bearer gate remains the default. OAuth therefore cannot become
    active merely because client credentials happen to exist in the environment.
    """
    mode = os.getenv(REMOTE_AUTH_MODE_ENV_VAR, AUTH_MODE_PRE_SHARED_BEARER)
    if mode not in {AUTH_MODE_PRE_SHARED_BEARER, AUTH_MODE_OAUTH}:
        raise RemoteConfigError(
            f"{REMOTE_AUTH_MODE_ENV_VAR} must be '{AUTH_MODE_PRE_SHARED_BEARER}' or "
            f"'{AUTH_MODE_OAUTH}'."
        )
    return mode


def load_oauth_client_credentials() -> tuple[str, str]:
    """Load the single preconfigured OAuth client from process-local environment values."""
    client_id = os.getenv(OAUTH_CLIENT_ID_ENV_VAR)
    client_secret = os.getenv(OAUTH_CLIENT_SECRET_ENV_VAR)
    if client_id is None or client_secret is None:
        raise RemoteConfigError(
            f"{OAUTH_CLIENT_ID_ENV_VAR} and {OAUTH_CLIENT_SECRET_ENV_VAR} must both be set "
            "for OAuth remote transport."
        )
    if not MIN_OAUTH_CLIENT_ID_CHARS <= len(client_id) <= MAX_OAUTH_CLIENT_ID_CHARS:
        raise RemoteConfigError(
            f"{OAUTH_CLIENT_ID_ENV_VAR} must contain between {MIN_OAUTH_CLIENT_ID_CHARS} "
            f"and {MAX_OAUTH_CLIENT_ID_CHARS} characters."
        )
    if not MIN_OAUTH_CLIENT_SECRET_CHARS <= len(client_secret) <= MAX_OAUTH_CLIENT_SECRET_CHARS:
        raise RemoteConfigError(
            f"{OAUTH_CLIENT_SECRET_ENV_VAR} must contain between "
            f"{MIN_OAUTH_CLIENT_SECRET_CHARS} and {MAX_OAUTH_CLIENT_SECRET_CHARS} characters."
        )
    credentials_are_unsafe = (
        _TOKEN_PATTERN.fullmatch(client_id) is None
        or _TOKEN_PATTERN.fullmatch(client_secret) is None
    )
    if credentials_are_unsafe:
        raise RemoteConfigError(
            "OAuth client credentials must use URL-safe visible token characters."
        )
    return client_id, client_secret


class PreconfiguredOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """In-memory OAuth provider for one pre-provisioned Claude confidential client."""

    def __init__(
        self,
        *,
        public_origin: str,
        resource_url: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        self._public_origin = public_origin.rstrip("/")
        self._resource_url = resource_url
        self._client = OAuthClientInformationFull(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uris=[AnyUrl(CLAUDE_CALLBACK_URL)],
            token_endpoint_auth_method="client_secret_post",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope=OAUTH_SCOPE,
            client_name="Claude Local-MCP-Bridge",
            application_type="web",
            client_id_issued_at=int(time.time()),
            client_secret_expires_at=0,
        )
        self._pending: dict[str, _PendingAuthorization] = {}
        self._codes: dict[str, AuthorizationCode] = {}
        self._access_tokens: dict[str, AccessToken] = {}
        self._refresh_tokens: dict[str, RefreshToken] = {}
        self._access_to_refresh: dict[str, str] = {}
        self._refresh_to_access: dict[str, str] = {}

    @property
    def client_id(self) -> str:
        return self._client.client_id

    def _prune(self) -> None:
        now = time.time()
        self._pending = {
            key: value for key, value in self._pending.items() if value.expires_at >= now
        }
        self._codes = {
            key: value for key, value in self._codes.items() if value.expires_at >= now
        }
        expired_access = [
            key
            for key, value in self._access_tokens.items()
            if value.expires_at is not None and value.expires_at < now
        ]
        expired_refresh = [
            key
            for key, value in self._refresh_tokens.items()
            if value.expires_at is not None and value.expires_at < now
        ]
        for token in expired_access:
            refresh = self._access_to_refresh.pop(token, None)
            self._access_tokens.pop(token, None)
            if refresh is not None:
                self._refresh_to_access.pop(refresh, None)
        for token in expired_refresh:
            self._revoke_refresh(token)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._client if client_id == self._client.client_id else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        raise NotImplementedError("Dynamic client registration is disabled.")

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        self._prune()
        if client.client_id != self._client.client_id:
            raise AuthorizeError(error="unauthorized_client")
        if params.resource is not None and params.resource != self._resource_url:
            raise AuthorizeError(error="invalid_target")
        if len(self._pending) >= MAX_PENDING_AUTHORIZATIONS:
            raise AuthorizeError(error="temporarily_unavailable")

        request_id = secrets.token_urlsafe(32)
        self._pending[request_id] = _PendingAuthorization(
            client_id=client.client_id,
            params=params.model_copy(update={"resource": self._resource_url}),
            expires_at=time.time() + PENDING_AUTHORIZATION_TTL_SECONDS,
        )
        return f"{self._public_origin}/oauth/consent?request={quote(request_id)}"

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        self._prune()
        code = self._codes.get(authorization_code)
        if code is None or code.client_id != client.client_id:
            return None
        return code

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        self._prune()
        stored = self._codes.pop(authorization_code.code, None)
        if stored is None or stored.client_id != client.client_id:
            raise TokenError(error="invalid_grant")
        return self._mint_token_pair(
            client_id=stored.client_id,
            scopes=stored.scopes,
            resource=stored.resource or self._resource_url,
        )

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        self._prune()
        token = self._refresh_tokens.get(refresh_token)
        if token is None or token.client_id != client.client_id:
            return None
        return token

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        self._prune()
        stored = self._refresh_tokens.get(refresh_token.token)
        if stored is None or stored.client_id != client.client_id:
            raise TokenError(error="invalid_grant")
        effective_scopes = scopes or stored.scopes
        if not set(effective_scopes).issubset(stored.scopes):
            raise TokenError(error="invalid_scope")
        resource = stored.resource or self._resource_url
        self._revoke_refresh(stored.token)
        return self._mint_token_pair(
            client_id=stored.client_id,
            scopes=effective_scopes,
            resource=resource,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        self._prune()
        access = self._access_tokens.get(token)
        if access is None or access.resource != self._resource_url:
            return None
        return access

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        if isinstance(token, AccessToken):
            self._revoke_access(token.token)
        else:
            self._revoke_refresh(token.token)

    def _mint_token_pair(self, *, client_id: str, scopes: list[str], resource: str) -> OAuthToken:
        now = int(time.time())
        access_value = f"access_{secrets.token_urlsafe(32)}"
        refresh_value = f"refresh_{secrets.token_urlsafe(32)}"
        self._access_tokens[access_value] = AccessToken(
            token=access_value,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + ACCESS_TOKEN_TTL_SECONDS,
            resource=resource,
            subject="local-operator",
            claims={"iss": self._public_origin},
        )
        self._refresh_tokens[refresh_value] = RefreshToken(
            token=refresh_value,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + REFRESH_TOKEN_TTL_SECONDS,
            resource=resource,
            subject="local-operator",
        )
        self._access_to_refresh[access_value] = refresh_value
        self._refresh_to_access[refresh_value] = access_value
        return OAuthToken(
            access_token=access_value,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            refresh_token=refresh_value,
            scope=" ".join(scopes),
        )

    def _revoke_access(self, access: str) -> None:
        refresh = self._access_to_refresh.pop(access, None)
        self._access_tokens.pop(access, None)
        if refresh is not None:
            self._refresh_to_access.pop(refresh, None)
            self._refresh_tokens.pop(refresh, None)

    def _revoke_refresh(self, refresh: str) -> None:
        access = self._refresh_to_access.pop(refresh, None)
        self._refresh_tokens.pop(refresh, None)
        if access is not None:
            self._access_to_refresh.pop(access, None)
            self._access_tokens.pop(access, None)

    def _consume_pending(self, request_id: str) -> _PendingAuthorization | None:
        self._prune()
        return self._pending.pop(request_id, None)

    def _peek_pending(self, request_id: str) -> _PendingAuthorization | None:
        self._prune()
        return self._pending.get(request_id)

    def _approve(self, request_id: str) -> str | None:
        pending = self._consume_pending(request_id)
        if pending is None:
            return None
        params = pending.params
        code_value = f"code_{secrets.token_urlsafe(32)}"
        self._codes[code_value] = AuthorizationCode(
            code=code_value,
            client_id=pending.client_id,
            scopes=params.scopes or [OAUTH_SCOPE],
            expires_at=time.time() + AUTHORIZATION_CODE_TTL_SECONDS,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=self._resource_url,
            subject="local-operator",
        )
        return construct_redirect_uri(
            str(params.redirect_uri),
            code=code_value,
            state=params.state,
        )

    def _deny(self, request_id: str) -> str | None:
        pending = self._consume_pending(request_id)
        if pending is None:
            return None
        return construct_redirect_uri(
            str(pending.params.redirect_uri),
            error="access_denied",
            state=pending.params.state,
        )

    async def handle_consent(self, request: Request) -> Response:
        if request.method == "GET":
            request_id = request.query_params.get("request", "")
            pending = self._peek_pending(request_id)
            if pending is None:
                return PlainTextResponse(
                    "Authorization request is invalid or expired.",
                    status_code=400,
                    headers=_CONSENT_HEADERS,
                )
            request_value = escape(request_id, quote=True)
            redirect_value = escape(str(pending.params.redirect_uri))
            html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Authorize Local-MCP-Bridge</title></head>
<body><main><h1>Authorize Local-MCP-Bridge</h1>
<p>Claude is requesting access to the configured MCP bridge.</p>
<p>Redirect destination: <code>{redirect_value}</code></p>
<form method="post" action="/oauth/consent">
<input type="hidden" name="request" value="{request_value}">
<button type="submit" name="decision" value="allow">Allow</button>
<button type="submit" name="decision" value="deny">Deny</button>
</form></main></body></html>"""
            return HTMLResponse(html, headers=_CONSENT_HEADERS)

        content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type != "application/x-www-form-urlencoded":
            return PlainTextResponse(
                "Unsupported form encoding.", status_code=415, headers=_CONSENT_HEADERS
            )

        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_CONSENT_FORM_BYTES:
                return PlainTextResponse(
                    "Authorization form is too large.", status_code=413, headers=_CONSENT_HEADERS
                )
            chunks.append(chunk)
        try:
            fields = parse_qs(b"".join(chunks).decode("utf-8"), keep_blank_values=True)
        except UnicodeError:
            return PlainTextResponse(
                "Invalid authorization form.", status_code=400, headers=_CONSENT_HEADERS
            )

        request_id = fields.get("request", [""])[0]
        decision = fields.get("decision", [""])[0]
        if self._peek_pending(request_id) is None:
            return PlainTextResponse(
                "Authorization request is invalid or expired.",
                status_code=400,
                headers=_CONSENT_HEADERS,
            )
        if decision == "allow":
            target = self._approve(request_id)
        elif decision == "deny":
            target = self._deny(request_id)
        else:
            target = None
        if target is None:
            return PlainTextResponse(
                "Invalid authorization decision.", status_code=400, headers=_CONSENT_HEADERS
            )
        return RedirectResponse(target, status_code=302, headers=_CONSENT_HEADERS)


def build_oauth_auth_settings(*, public_origin: str, resource_url: str) -> AuthSettings:
    """Build the SDK OAuth/PRM configuration for the preconfigured client flow."""
    return AuthSettings(
        issuer_url=AnyHttpUrl(public_origin),
        resource_server_url=AnyHttpUrl(resource_url),
        required_scopes=[OAUTH_SCOPE],
        client_registration_options=ClientRegistrationOptions(
            enabled=False,
            valid_scopes=[OAUTH_SCOPE],
            default_scopes=[OAUTH_SCOPE],
        ),
        revocation_options=RevocationOptions(enabled=True),
        validate_token_resource=True,
    )


def install_oauth_consent_route(server: MCPServer, provider: PreconfiguredOAuthProvider) -> None:
    """Add the human consent endpoint before the Streamable HTTP app is materialized."""

    @server.custom_route(
        "/oauth/consent",
        methods=["GET", "POST"],
        name="oauth-consent",
        include_in_schema=False,
    )
    async def oauth_consent(request: Request) -> Response:
        return await provider.handle_consent(request)
