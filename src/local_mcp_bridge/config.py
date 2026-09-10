"""Local configuration loading for the project registry.

Real configuration is intentionally local-only and ignored by Git. Configuration
parsing is strict around project roots and permissions so a typo cannot silently
broaden access.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from local_mcp_bridge.registry import (
    ProjectPermissions,
    ProjectRecord,
    ProjectRegistry,
    RegistryError,
)

CONFIG_ENV_VAR = "LOCAL_MCP_BRIDGE_CONFIG"
DEFAULT_CONFIG_PATH = Path("config/config.yaml")
MAX_CONFIG_BYTES = 1_048_576
_ALLOWED_TOP_LEVEL_KEYS = {"server", "security", "projects"}
_ALLOWED_PROJECT_KEYS = {"root", "permissions", "allowed_executables", "execution"}
_ALLOWED_PERMISSION_KEYS = {"read", "search", "execute", "git"}


class ConfigError(ValueError):
    """Raised when local bridge configuration is missing or invalid."""


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
            raise ConfigError("Configuration mapping keys must be scalar values.") from exc
        if duplicate:
            raise ConfigError(f"Duplicate configuration key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _expect_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{label} must be a YAML mapping.")
    if not all(isinstance(key, str) for key in value):
        raise ConfigError(f"{label} keys must be strings.")
    return value


def _validate_known_keys(
    mapping: Mapping[str, object],
    allowed: set[str],
    label: str,
) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise ConfigError(f"Unknown {label} key(s): {', '.join(unknown)}")


def _parse_permissions(raw: object, project_id: str) -> ProjectPermissions:
    if raw is None:
        permissions: Mapping[str, object] = {}
    else:
        permissions = _expect_mapping(raw, f"permissions for project {project_id!r}")

    _validate_known_keys(permissions, _ALLOWED_PERMISSION_KEYS, "permission")

    parsed: dict[str, bool] = {}
    for key in _ALLOWED_PERMISSION_KEYS:
        value = permissions.get(key, False)
        if not isinstance(value, bool):
            raise ConfigError(
                f"Permission {key!r} for project {project_id!r} must be true or false."
            )
        parsed[key] = value

    if parsed["search"] and not parsed["read"]:
        raise ConfigError(
            f"Project {project_id!r} enables search without read; search requires read access."
        )

    return ProjectPermissions(**parsed)


def _resolve_project_root(raw: object, project_id: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError(f"Project {project_id!r} must define a non-empty root string.")

    root = Path(raw).expanduser()
    if not root.is_absolute():
        raise ConfigError(f"Project {project_id!r} root must be an absolute path.")

    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise ConfigError(f"Project {project_id!r} root does not exist or cannot be resolved.") from exc

    if not resolved.is_dir():
        raise ConfigError(f"Project {project_id!r} root must reference a directory.")

    return resolved


def load_project_registry(path: str | Path) -> ProjectRegistry:
    """Load and validate a local YAML configuration into an immutable registry."""
    config_path = Path(path)

    try:
        size = config_path.stat().st_size
    except OSError as exc:
        raise ConfigError(f"Cannot access configuration file: {config_path}") from exc

    if size > MAX_CONFIG_BYTES:
        raise ConfigError("Configuration file exceeds the 1 MiB safety limit.")

    try:
        text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"Cannot read configuration file: {config_path}") from exc

    try:
        loaded = yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise ConfigError("Configuration file is not valid YAML.") from exc

    if loaded is None:
        loaded = {}

    document = _expect_mapping(loaded, "configuration root")
    _validate_known_keys(document, _ALLOWED_TOP_LEVEL_KEYS, "top-level configuration")

    projects_raw = document.get("projects", {})
    projects = _expect_mapping(projects_raw, "projects")

    records: list[ProjectRecord] = []
    for project_id, raw_project in projects.items():
        project = _expect_mapping(raw_project, f"project {project_id!r}")
        _validate_known_keys(project, _ALLOWED_PROJECT_KEYS, f"project {project_id!r}")

        if "root" not in project:
            raise ConfigError(f"Project {project_id!r} is missing required key 'root'.")

        root = _resolve_project_root(project["root"], project_id)
        permissions = _parse_permissions(project.get("permissions"), project_id)
        records.append(
            ProjectRecord(project_id=project_id, root=root, permissions=permissions)
        )

    try:
        return ProjectRegistry(records)
    except RegistryError as exc:
        raise ConfigError(str(exc)) from exc


def load_runtime_registry() -> ProjectRegistry:
    """Load the configured registry, defaulting to a safe empty registry.

    If the environment variable explicitly selects a config file, that file must
    exist and validate. If no override is provided and config/config.yaml is absent,
    the server starts fail-closed with zero authorized projects.
    """
    configured_path = os.getenv(CONFIG_ENV_VAR)
    if configured_path:
        return load_project_registry(configured_path)

    if DEFAULT_CONFIG_PATH.is_file():
        return load_project_registry(DEFAULT_CONFIG_PATH)

    return ProjectRegistry.empty()
