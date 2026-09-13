"""Retry-safe consent handling for the live Claude OAuth flow.

Browsers, reverse proxies, or hosted OAuth clients may retry a consent submission. The base
provider deliberately consumes a pending authorization exactly once, so this adapter keeps
a very short-lived redirect cache and also carries the opaque request id in both the query
string and form body. It does not create a new authorization, code, scope, redirect URI, or
client registration; it only makes delivery of an already-approved response retry-safe.
"""

from __future__ import annotations

import time
from html import escape
from urllib.parse import parse_qs, quote

from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from local_mcp_bridge.oauth import MAX_CONSENT_FORM_BYTES, PreconfiguredOAuthProvider

CONSENT_RETRY_TTL_SECONDS = 60

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


class RetrySafeOAuthProvider(PreconfiguredOAuthProvider):
    """Preconfigured provider with bounded, idempotent consent-response delivery."""

    def __init__(self, **kwargs: str) -> None:
        super().__init__(**kwargs)
        self._completed_consent: dict[str, tuple[str, str, float]] = {}

    def _prune_completed(self) -> None:
        now = time.time()
        self._completed_consent = {
            request_id: value
            for request_id, value in self._completed_consent.items()
            if value[2] >= now
        }

    def _completed_target(self, request_id: str, decision: str) -> str | None:
        self._prune_completed()
        completed = self._completed_consent.get(request_id)
        if completed is None or completed[0] != decision:
            return None
        return completed[1]

    def _remember_target(self, request_id: str, decision: str, target: str) -> str:
        self._prune_completed()
        self._completed_consent[request_id] = (
            decision,
            target,
            time.time() + CONSENT_RETRY_TTL_SECONDS,
        )
        return target

    def _approve_retry_safe(self, request_id: str) -> str | None:
        cached = self._completed_target(request_id, "allow")
        if cached is not None:
            return cached
        target = super()._approve(request_id)
        if target is None:
            return None
        return self._remember_target(request_id, "allow", target)

    def _deny_retry_safe(self, request_id: str) -> str | None:
        cached = self._completed_target(request_id, "deny")
        if cached is not None:
            return cached
        target = super()._deny(request_id)
        if target is None:
            return None
        return self._remember_target(request_id, "deny", target)

    async def handle_consent(self, request: Request) -> Response:
        """Render/submit consent with query fallback and bounded replay tolerance."""
        query_request_id = request.query_params.get("request", "")

        if request.method == "GET":
            completed = self._completed_target(query_request_id, "allow")
            if completed is None:
                completed = self._completed_target(query_request_id, "deny")
            if completed is not None:
                return RedirectResponse(completed, status_code=302, headers=_CONSENT_HEADERS)

            pending = self._peek_pending(query_request_id)
            if pending is None:
                return PlainTextResponse(
                    "Authorization request is invalid or expired.",
                    status_code=400,
                    headers=_CONSENT_HEADERS,
                )
            request_value = escape(query_request_id, quote=True)
            request_query = quote(query_request_id, safe="")
            redirect_value = escape(str(pending.params.redirect_uri))
            html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Authorize Local-MCP-Bridge</title></head>
<body><main><h1>Authorize Local-MCP-Bridge</h1>
<p>Claude is requesting access to the configured MCP bridge.</p>
<p>Redirect destination: <code>{redirect_value}</code></p>
<form method="post" action="/oauth/consent?request={request_query}">
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

        body_request_id = fields.get("request", [""])[0]
        if query_request_id and body_request_id and query_request_id != body_request_id:
            return PlainTextResponse(
                "Authorization form request mismatch.",
                status_code=400,
                headers=_CONSENT_HEADERS,
            )
        request_id = body_request_id or query_request_id
        decision = fields.get("decision", [""])[0]

        if decision == "allow":
            target = self._approve_retry_safe(request_id)
        elif decision == "deny":
            target = self._deny_retry_safe(request_id)
        else:
            target = None

        if target is None:
            return PlainTextResponse(
                "Authorization request is invalid or expired.",
                status_code=400,
                headers=_CONSENT_HEADERS,
            )
        return RedirectResponse(target, status_code=302, headers=_CONSENT_HEADERS)
