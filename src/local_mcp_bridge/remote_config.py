"""Strict local-only configuration for the Phase 9 remote transport.

Remote exposure is intentionally a second opt-in policy layer. The normal stdio runtime
never reads this file. The remote entry point requires an explicit enabled configuration,
an HTTPS public URL, a loopback-only local bind, and bounded HTTP/session limits.

Authentication material is never accepted from YAML. The shared bearer secret is read
from ``LOCAL_MCP_BRIDGE_REMOTE_TOKEN`` at process startup and remains memory-only.
"""

from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

REMOTE_CONFIG_ENV_VAR = "LOCAL_MCP_BRIDGE_REMOTE_CONFIG"
REMOTE_PUBLIC_URL_ENV_VAR = "LOCAL_MCP_BRIDGE_REMOTE_PUBLIC_URL"
REMOTE_TOKEN_ENV_VAR = "LOCAL_MCP_BRIDGE_REMOTE_TOKEN"
DEFAULT_REMOTE_CONFIG_PATH = Path("config/remote.local.yaml")
MAX_REMOTE_CONFIG_BYTES = 65_536

DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_MAX_REQUEST_BODY_BYTES = 262_144
MAX_REMOTE_REQUEST_BODY_BYTES = 1_048_576
DEFAULT_SESSION_IDLE_TIMEOUT_SECONDS = 300
MAX_SESSION_IDLE_TIMEOUT_SECONDS = 1_800
DEFAULT_MAX_SESSIONS = 32
MAX_REMOTE_SESSIONS = 256
MIN_REMOTE_TOKEN_CHARS = 43
MAX_REMOTE_TOKEN_CHARS = 256

_ALLOWED_TOP_LEVEL_KEYS = {"enabled", "bind", "public_url", "limits"}
_ALLOWED_BIND_KEYS = {"host", "port"}
_ALLOWED_LIMIT_KEYS = {
    "max_request_body_bytes",
    "session_idle_timeout_seconds",
    "max_sessions",
}
_LOOPBACK_HOSTS = {"127.0.0.1", "::1"}
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9._~-]+")
_HOST_PATTERN = re.compile(r"[A-Za-z0-9.-]+")


class RemoteConfigError(ValueError):
    """Raised when remote transport policy or its authentication secret is invalid."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise RemoteConfigError("Remote configuration mapping keys must be scalar values.") from exc
        if duplicate:
            raise RemoteConfigError(f"Duplicate remote configuration key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _expect_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RemoteConfigError(f"{label} must be a YAML mapping.")
    if not all(isinstance(key, str) for key in value):
        raise RemoteConfigError(f"{label} keys must be strings.")
    return value


def _validate_known_keys(mapping: Mapping[str, object], allowed: set[str], label: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise RemoteConfigError(f"Unknown {label} key(s): {', '.join(unknown)}")


def _parse_int(
    raw: object,
    label: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if type(raw) is not int or not minimum <= raw <= maximum:
        raise RemoteConfigError(f"{label} must be an integer between {minimum} and {maximum}.")
    return raw


def _validate_dns_hostname(host: str) -> str:
    normalized = host.rstrip(".").lower()
    if not normalized or normalized != host.lower().rstrip("."):
        raise RemoteConfigError("Remote public URL hostname is invalid.")
    if len(normalized) > 253 or not _HOST_PATTERN.fullmatch(normalized):
        raise RemoteConfigError("Remote public URL must use an ASCII DNS hostname.")
    labels = normalized.split(".")
    if any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        for label in labels
    ):
        raise RemoteConfigError("Remote public URL hostname is invalid.")
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        pass
    else:
        raise RemoteConfigError("Remote public URL must use a DNS hostname, not an IP literal.")
    if normalized == "localhost" or normalized.endswith(".localhost"):
        raise RemoteConfigError("Remote public URL may not use a localhost hostname.")
    return normalized


def _parse_public_url(raw: object) -> tuple[str, str, str]:
    if not isinstance(raw, str) or not raw.strip() or raw != raw.strip():
        raise RemoteConfigError("Remote public_url must be a non-empty HTTPS URL string.")

    parsed = urlsplit(raw)
    if parsed.scheme != "https":
        raise RemoteConfigError("Remote public_url must use HTTPS.")
    if parsed.username is not None or parsed.password is not None:
        raise RemoteConfigError("Remote public_url may not contain embedded credentials.")
    if parsed.query or parsed.fragment:
        raise RemoteConfigError("Remote public_url may not contain a query string or fragment.")
    if parsed.path != "/mcp":
        raise RemoteConfigError("Remote public_url path must be exactly '/mcp'.")
    if parsed.hostname is None:
        raise RemoteConfigError("Remote public_url must include a hostname.")

    host = _validate_dns_hostname(parsed.hostname)
    try:
        port = parsed.port
    except ValueError as exc:
        raise RemoteConfigError("Remote public_url contains an invalid port.") from exc

    if port is not None and not 1 <= port <= 65_535:
        raise RemoteConfigError("Remote public_url contains an invalid port.")

    origin = f"https://{host}" if port in (None, 443) else f"https://{host}:{port}"
    normalized_url = f"{origin}/mcp"
    return normalized_url, host, origin


@dataclass(frozen=True, slots=True)
class RemoteSettings:
    """Validated transport-only policy for one remote bridge listener."""

    enabled: bool
    bind_host: str
    port: int
    public_url: str
    public_host: str
    public_origin: str
    max_request_body_bytes: int
    session_idle_timeout_seconds: int
    max_sessions: int

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        """Exact public host plus loopback values accepted by MCP Host validation."""
        return (
            self.public_host,
            f"{self.public_host}:*",
            "127.0.0.1:*",
            "localhost:*",
            "[::1]:*",
        )

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        """Same-origin public browser traffic plus local development origins."""
        return (
            self.public_origin,
            "http://127.0.0.1:*",
            "http://localhost:*",
            "http://[::1]:*",
        )


def load_remote_settings(path: str | Path, *, public_url_override: str | None = None) -> RemoteSettings:
    """Load and strictly validate one local remote-transport YAML file."""
    config_path = Path(path)
    try:
        size = config_path.stat().st_size
    except OSError as exc:
        raise RemoteConfigError(f"Cannot access remote configuration file: {config_path}") from exc
    if size > MAX_REMOTE_CONFIG_BYTES:
        raise RemoteConfigError("Remote configuration exceeds the 64 KiB safety limit.")

    try:
        text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RemoteConfigError(f"Cannot read remote configuration file: {config_path}") from exc

    try:
        loaded = yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise RemoteConfigError("Remote configuration is not valid YAML.") from exc
    if loaded is None:
        loaded = {}

    document = _expect_mapping(loaded, "remote configuration root")
    _validate_known_keys(document, _ALLOWED_TOP_LEVEL_KEYS, "remote top-level configuration")

    enabled = document.get("enabled", False)
    if not isinstance(enabled, bool):
        raise RemoteConfigError("Remote enabled must be true or false.")
    if not enabled:
        raise RemoteConfigError("Remote transport is disabled; set enabled: true explicitly.")

    bind = _expect_mapping(document.get("bind", {}), "remote bind")
    _validate_known_keys(bind, _ALLOWED_BIND_KEYS, "remote bind")
    bind_host = bind.get("host", DEFAULT_BIND_HOST)
    if not isinstance(bind_host, str) or bind_host not in _LOOPBACK_HOSTS:
        raise RemoteConfigError("Remote bind.host must be loopback-only: 127.0.0.1 or ::1.")
    port = _parse_int(bind.get("port", DEFAULT_PORT), "Remote bind.port", minimum=1024, maximum=65_535)

    public_url_raw = public_url_override if public_url_override is not None else document.get("public_url")
    public_url, public_host, public_origin = _parse_public_url(public_url_raw)

    limits = _expect_mapping(document.get("limits", {}), "remote limits")
    _validate_known_keys(limits, _ALLOWED_LIMIT_KEYS, "remote limit")
    max_request_body_bytes = _parse_int(
        limits.get("max_request_body_bytes", DEFAULT_MAX_REQUEST_BODY_BYTES),
        "Remote max_request_body_bytes",
        minimum=1024,
        maximum=MAX_REMOTE_REQUEST_BODY_BYTES,
    )
    session_idle_timeout_seconds = _parse_int(
        limits.get("session_idle_timeout_seconds", DEFAULT_SESSION_IDLE_TIMEOUT_SECONDS),
        "Remote session_idle_timeout_seconds",
        minimum=30,
        maximum=MAX_SESSION_IDLE_TIMEOUT_SECONDS,
    )
    max_sessions = _parse_int(
        limits.get("max_sessions", DEFAULT_MAX_SESSIONS),
        "Remote max_sessions",
        minimum=1,
        maximum=MAX_REMOTE_SESSIONS,
    )

    return RemoteSettings(
        enabled=True,
        bind_host=bind_host,
        port=port,
        public_url=public_url,
        public_host=public_host,
        public_origin=public_origin,
        max_request_body_bytes=max_request_body_bytes,
        session_idle_timeout_seconds=session_idle_timeout_seconds,
        max_sessions=max_sessions,
    )


def load_runtime_remote_settings() -> RemoteSettings:
    """Load the explicitly selected/default local remote policy and optional URL override."""
    configured_path = os.getenv(REMOTE_CONFIG_ENV_VAR)
    path = Path(configured_path).expanduser() if configured_path else DEFAULT_REMOTE_CONFIG_PATH
    override = os.getenv(REMOTE_PUBLIC_URL_ENV_VAR)
    return load_remote_settings(path, public_url_override=override)


def load_remote_token() -> str:
    """Read and validate the memory-only pre-shared bearer token from the environment."""
    token = os.getenv(REMOTE_TOKEN_ENV_VAR)
    if token is None:
        raise RemoteConfigError(f"{REMOTE_TOKEN_ENV_VAR} must be set for remote transport.")
    if not MIN_REMOTE_TOKEN_CHARS <= len(token) <= MAX_REMOTE_TOKEN_CHARS:
        raise RemoteConfigError(
            f"{REMOTE_TOKEN_ENV_VAR} must contain between {MIN_REMOTE_TOKEN_CHARS} and "
            f"{MAX_REMOTE_TOKEN_CHARS} characters."
        )
    if not token.isascii() or _TOKEN_PATTERN.fullmatch(token) is None:
        raise RemoteConfigError(
            f"{REMOTE_TOKEN_ENV_VAR} must contain only URL-safe visible token characters."
        )
    return token
