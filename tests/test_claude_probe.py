"""Phase 10 compatibility-probe tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from local_mcp_bridge.claude_probe import (
    EXPECTED_TOOL_NAMES,
    ClaudeProbeReport,
    main,
    validate_mcp_client,
)
from local_mcp_bridge.remote_config import RemoteSettings
from local_mcp_bridge.remote_transport import create_remote_app
from local_mcp_bridge.server import create_mcp_server

TOKEN = "T" * 64


def _settings() -> RemoteSettings:
    return RemoteSettings(
        enabled=True,
        bind_host="127.0.0.1",
        port=8765,
        public_url="https://mcp.example.com/mcp",
        public_host="mcp.example.com",
        public_origin="https://mcp.example.com",
        max_request_body_bytes=262_144,
        session_idle_timeout_seconds=300,
        max_sessions=32,
    )


def test_validate_mcp_client_accepts_exact_phase_10_surface() -> None:
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
            report = await validate_mcp_client(client)

        assert report == ClaudeProbeReport(
            ok=True,
            transport="streamable-http",
            authentication="pre-shared-bearer",
            health_status="ok",
            tool_count=len(EXPECTED_TOOL_NAMES),
            expected_tool_count=len(EXPECTED_TOOL_NAMES),
            missing_tools=(),
            unexpected_tool_count=0,
        )
        payload = report.as_public_dict()
        assert TOKEN not in json.dumps(payload)
        assert "mcp.example.com" not in json.dumps(payload)

    asyncio.run(scenario())


def test_validate_mcp_client_rejects_missing_unexpected_and_degraded_surface() -> None:
    class Tool:
        def __init__(self, name: str) -> None:
            self.name = name

    class ToolResult:
        tools = [Tool("health_check"), Tool("unexpected_tool")]

    class HealthResult:
        is_error = False
        structured_content = {"status": "degraded"}

    class FakeClient:
        async def list_tools(self) -> ToolResult:
            return ToolResult()

        async def call_tool(self, name: str, arguments: dict[str, object]) -> HealthResult:
            assert name == "health_check"
            assert arguments == {}
            return HealthResult()

    report = asyncio.run(validate_mcp_client(FakeClient()))

    assert report.ok is False
    assert report.health_status == "degraded"
    assert report.unexpected_tool_count == 1
    assert "list_projects" in report.missing_tools
    assert "unexpected_tool" not in json.dumps(report.as_public_dict())


def test_probe_cli_fails_closed_without_remote_policy(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LOCAL_MCP_BRIDGE_REMOTE_CONFIG", str(tmp_path / "missing.yaml"))
    monkeypatch.delenv("LOCAL_MCP_BRIDGE_REMOTE_TOKEN", raising=False)

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"error": "invalid_remote_configuration", "ok": False}


def test_probe_cli_suppresses_network_exception_detail(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret_marker = "should-never-be-printed"

    monkeypatch.setattr("local_mcp_bridge.claude_probe.load_runtime_remote_settings", _settings)
    monkeypatch.setattr("local_mcp_bridge.claude_probe.load_remote_token", lambda: TOKEN)

    async def fail_probe(settings: RemoteSettings, token: str) -> ClaudeProbeReport:
        assert settings.public_host == "mcp.example.com"
        assert token == TOKEN
        raise RuntimeError(secret_marker)

    monkeypatch.setattr("local_mcp_bridge.claude_probe.probe_remote_endpoint", fail_probe)

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == 2
    output = capsys.readouterr().out
    assert secret_marker not in output
    assert TOKEN not in output
    assert json.loads(output) == {"error": "remote_probe_failed", "ok": False}
