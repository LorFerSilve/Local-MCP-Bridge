"""Phase 9 remote transport configuration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_mcp_bridge.remote_config import (
    REMOTE_CONFIG_ENV_VAR,
    REMOTE_PUBLIC_URL_ENV_VAR,
    REMOTE_TOKEN_ENV_VAR,
    RemoteConfigError,
    load_remote_settings,
    load_remote_token,
    load_runtime_remote_settings,
)


def _write_remote_config(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def _valid_config(path: Path, *, public_url: str = "https://mcp.example.com/mcp") -> Path:
    return _write_remote_config(
        path,
        f"""enabled: true
bind:
  host: "127.0.0.1"
  port: 8765
public_url: "{public_url}"
limits:
  max_request_body_bytes: 262144
  session_idle_timeout_seconds: 300
  max_sessions: 32
""",
    )


def test_valid_remote_policy_derives_strict_transport_allowlists(tmp_path: Path) -> None:
    settings = load_remote_settings(_valid_config(tmp_path / "remote.yaml"))

    assert settings.enabled is True
    assert settings.bind_host == "127.0.0.1"
    assert settings.port == 8765
    assert settings.public_url == "https://mcp.example.com/mcp"
    assert settings.public_host == "mcp.example.com"
    assert settings.public_origin == "https://mcp.example.com"
    assert "mcp.example.com" in settings.allowed_hosts
    assert "mcp.example.com:*" in settings.allowed_hosts
    assert "127.0.0.1:*" in settings.allowed_hosts
    assert settings.allowed_origins[0] == "https://mcp.example.com"


def test_remote_transport_requires_explicit_enabled_true(tmp_path: Path) -> None:
    path = _write_remote_config(
        tmp_path / "remote.yaml",
        'public_url: "https://mcp.example.com/mcp"\n',
    )

    with pytest.raises(RemoteConfigError, match="disabled"):
        load_remote_settings(path)


def test_remote_bind_is_loopback_only(tmp_path: Path) -> None:
    path = _write_remote_config(
        tmp_path / "remote.yaml",
        """enabled: true
bind:
  host: "0.0.0.0"
  port: 8765
public_url: "https://mcp.example.com/mcp"
""",
    )

    with pytest.raises(RemoteConfigError, match="loopback-only"):
        load_remote_settings(path)


@pytest.mark.parametrize(
    "public_url",
    [
        "http://mcp.example.com/mcp",
        "https://localhost/mcp",
        "https://127.0.0.1/mcp",
        "https://user:secret@mcp.example.com/mcp",
        "https://mcp.example.com/mcp?token=nope",
        "https://mcp.example.com/mcp#fragment",
        "https://mcp.example.com/other",
        "https://*.example.com/mcp",
    ],
)
def test_public_url_rejects_unsafe_or_ambiguous_forms(
    tmp_path: Path,
    public_url: str,
) -> None:
    with pytest.raises(RemoteConfigError):
        load_remote_settings(_valid_config(tmp_path / "remote.yaml", public_url=public_url))


def test_remote_config_rejects_duplicate_and_unknown_keys(tmp_path: Path) -> None:
    duplicate = _write_remote_config(
        tmp_path / "duplicate.yaml",
        """enabled: true
enabled: true
public_url: "https://mcp.example.com/mcp"
""",
    )
    with pytest.raises(RemoteConfigError, match="Duplicate"):
        load_remote_settings(duplicate)

    unknown = _write_remote_config(
        tmp_path / "unknown.yaml",
        """enabled: true
public_url: "https://mcp.example.com/mcp"
expose_everywhere: true
""",
    )
    with pytest.raises(RemoteConfigError, match="Unknown"):
        load_remote_settings(unknown)


def test_remote_limits_are_hard_bounded(tmp_path: Path) -> None:
    path = _write_remote_config(
        tmp_path / "remote.yaml",
        """enabled: true
public_url: "https://mcp.example.com/mcp"
limits:
  max_request_body_bytes: 1048577
""",
    )

    with pytest.raises(RemoteConfigError, match="max_request_body_bytes"):
        load_remote_settings(path)


def test_runtime_remote_policy_supports_explicit_path_and_public_url_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _valid_config(tmp_path / "remote.yaml")
    monkeypatch.setenv(REMOTE_CONFIG_ENV_VAR, str(path))
    monkeypatch.setenv(REMOTE_PUBLIC_URL_ENV_VAR, "https://override.example.net:8443/mcp")

    settings = load_runtime_remote_settings()

    assert settings.public_url == "https://override.example.net:8443/mcp"
    assert settings.public_origin == "https://override.example.net:8443"
    assert settings.public_host == "override.example.net"


def test_remote_token_is_required_high_entropy_and_header_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(REMOTE_TOKEN_ENV_VAR, raising=False)
    with pytest.raises(RemoteConfigError, match=REMOTE_TOKEN_ENV_VAR):
        load_remote_token()

    monkeypatch.setenv(REMOTE_TOKEN_ENV_VAR, "too-short")
    with pytest.raises(RemoteConfigError, match="between"):
        load_remote_token()

    monkeypatch.setenv(REMOTE_TOKEN_ENV_VAR, "A" * 50 + " bad")
    with pytest.raises(RemoteConfigError, match="URL-safe"):
        load_remote_token()

    token = "A" * 64
    monkeypatch.setenv(REMOTE_TOKEN_ENV_VAR, token)
    assert load_remote_token() == token
