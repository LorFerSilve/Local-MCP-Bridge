"""Sanitized Phase 10 probe for the configured public MCP endpoint.

The probe deliberately reuses the existing ignored remote policy and memory-only bearer
secret. It accepts no caller-supplied URL or token, follows no redirects, prints no remote
response bodies, and reports only fixed-schema compatibility metadata.

A successful probe proves that the configured HTTPS endpoint can complete an authenticated
Streamable HTTP MCP round trip from the machine running the probe. It does *not* prove that
Anthropic's cloud can reach the endpoint or that a Claude product can provision the same
authentication method; those are separate Phase 10 validation steps.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from local_mcp_bridge.remote_config import (
    RemoteConfigError,
    RemoteSettings,
    load_remote_token,
    load_runtime_remote_settings,
)

PROBE_TIMEOUT_SECONDS = 15

EXPECTED_TOOL_NAMES = frozenset(
    {
        "health_check",
        "list_projects",
        "get_project",
        "list_directory",
        "read_file",
        "search_text",
        "run_process",
        "start_job",
        "get_job",
        "list_jobs",
        "get_job_output",
        "cancel_job",
        "git_status",
        "git_fetch",
        "git_sync_fast_forward",
    }
)


@dataclass(frozen=True, slots=True)
class ClaudeProbeReport:
    """Fixed-schema, non-secret result of one MCP compatibility probe."""

    ok: bool
    transport: str
    authentication: str
    health_status: str
    tool_count: int
    expected_tool_count: int
    missing_tools: tuple[str, ...]
    unexpected_tool_count: int

    def as_public_dict(self) -> dict[str, object]:
        """Return a stable JSON-safe representation with no endpoint/token material."""
        return {
            "ok": self.ok,
            "transport": self.transport,
            "authentication": self.authentication,
            "health_status": self.health_status,
            "tool_count": self.tool_count,
            "expected_tool_count": self.expected_tool_count,
            "missing_tools": list(self.missing_tools),
            "unexpected_tool_count": self.unexpected_tool_count,
        }


async def validate_mcp_client(client: Any) -> ClaudeProbeReport:
    """Validate the Phase 10 tool/health contract through an initialized MCP client."""
    tools_result = await client.list_tools()
    names = {
        tool.name
        for tool in tools_result.tools
        if isinstance(getattr(tool, "name", None), str)
    }

    health_result = await client.call_tool("health_check", {})
    health_status = "invalid"
    structured = getattr(health_result, "structured_content", None)
    if not getattr(health_result, "is_error", True) and isinstance(structured, dict):
        candidate = structured.get("status")
        if candidate in {"ok", "degraded"}:
            health_status = candidate

    missing = tuple(sorted(EXPECTED_TOOL_NAMES - names))
    unexpected_count = len(names - EXPECTED_TOOL_NAMES)
    ok = health_status == "ok" and not missing and unexpected_count == 0

    return ClaudeProbeReport(
        ok=ok,
        transport="streamable-http",
        authentication="pre-shared-bearer",
        health_status=health_status,
        tool_count=len(names),
        expected_tool_count=len(EXPECTED_TOOL_NAMES),
        missing_tools=missing,
        unexpected_tool_count=unexpected_count,
    )


async def probe_remote_endpoint(settings: RemoteSettings, token: str) -> ClaudeProbeReport:
    """Probe the configured public endpoint without accepting arbitrary network targets."""
    timeout = httpx2.Timeout(PROBE_TIMEOUT_SECONDS)
    async with (
        httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            follow_redirects=False,
        ) as http_client,
        Client(
            streamable_http_client(
                settings.public_url,
                http_client=http_client,
            )
        ) as client,
    ):
        return await validate_mcp_client(client)


def _failure_payload(code: str) -> str:
    return json.dumps(
        {
            "ok": False,
            "error": code,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def main() -> None:
    """Run the sanitized endpoint probe using only configured local policy."""
    try:
        settings = load_runtime_remote_settings()
        token = load_remote_token()
    except RemoteConfigError:
        print(_failure_payload("invalid_remote_configuration"))
        raise SystemExit(2) from None

    try:
        report = asyncio.run(probe_remote_endpoint(settings, token))
    except Exception:  # noqa: BLE001 - intentionally suppress third-party/network detail
        print(_failure_payload("remote_probe_failed"))
        raise SystemExit(2) from None

    print(json.dumps(report.as_public_dict(), sort_keys=True, separators=(",", ":")))
    if not report.ok:
        raise SystemExit(1)


if __name__ == "__main__":  # pragma: no cover - console entry point
    main()
