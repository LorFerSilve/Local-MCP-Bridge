"""Local-only Phase 7 Git synchronization policy loading.

Git synchronization policy is kept in a separate ignored overlay so existing project
configuration remains backwards compatible. The overlay is trusted operator input;
repository-local Git configuration is not.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from local_mcp_bridge.registry import GitSettings, ProjectRegistry, RegistryError

GIT_CONFIG_ENV_VAR = "LOCAL_MCP_BRIDGE_GIT_CONFIG"
DEFAULT_GIT_CONFIG_PATH = Path("config/git.local.yaml")
MAX_GIT_CONFIG_BYTES = 262_144
_ALLOWED_ROOT_KEYS = {"projects"}
_ALLOWED_POLICY_KEYS = {
    "remote",
    "branch",
    "remote_url",
    "timeout_seconds",
    "max_output_bytes",
}


class GitConfigError(ValueError):
    """Raised when the local Git policy overlay is invalid."""


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
            raise GitConfigError("Git policy mapping keys must be scalar values.") from exc
        if duplicate:
            raise GitConfigError(f"Duplicate Git policy key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _load_yaml(path: Path) -> object:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise GitConfigError(f"Cannot access Git policy file: {path}") from exc
    if size > MAX_GIT_CONFIG_BYTES:
        raise GitConfigError("Git policy file exceeds the 256 KiB safety limit.")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise GitConfigError(f"Cannot read Git policy file: {path}") from exc
    try:
        return yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise GitConfigError("Git policy file is not valid YAML.") from exc


def _parse_policies(raw: object, registry: ProjectRegistry) -> dict[str, GitSettings]:
    if raw is None:
        return {}
    if not isinstance(raw, dict) or not all(isinstance(key, str) for key in raw):
        raise GitConfigError("Git policy root must be a YAML mapping.")
    unknown_root = sorted(set(raw) - _ALLOWED_ROOT_KEYS)
    if unknown_root:
        raise GitConfigError(f"Unknown Git policy root key(s): {', '.join(unknown_root)}")

    projects = raw.get("projects", {})
    if not isinstance(projects, dict) or not all(isinstance(key, str) for key in projects):
        raise GitConfigError("Git policy projects must be a YAML mapping.")

    policies: dict[str, GitSettings] = {}
    for project_id, policy_raw in projects.items():
        try:
            registry.require(project_id)
        except RegistryError as exc:
            raise GitConfigError("Git policy references an unknown project ID.") from exc
        if not isinstance(policy_raw, dict) or not all(
            isinstance(key, str) for key in policy_raw
        ):
            raise GitConfigError(f"Git policy for project {project_id!r} must be a mapping.")
        unknown = sorted(set(policy_raw) - _ALLOWED_POLICY_KEYS)
        if unknown:
            raise GitConfigError(f"Unknown Git policy key(s): {', '.join(unknown)}")
        for required in ("remote", "branch", "remote_url"):
            if required not in policy_raw:
                raise GitConfigError(
                    f"Git policy for project {project_id!r} is missing {required!r}."
                )

        remote = policy_raw["remote"]
        branch = policy_raw["branch"]
        remote_url = policy_raw["remote_url"]
        if not all(isinstance(value, str) for value in (remote, branch, remote_url)):
            raise GitConfigError("Git remote, branch, and remote_url must be strings.")
        timeout = policy_raw.get("timeout_seconds", 60)
        output_limit = policy_raw.get("max_output_bytes", 262_144)
        if type(timeout) is not int or type(output_limit) is not int:
            raise GitConfigError("Git timeout and output limits must be integers.")
        try:
            policies[project_id] = GitSettings(
                remote=remote,
                branch=branch,
                remote_url=remote_url,
                timeout_seconds=timeout,
                max_output_bytes=output_limit,
            )
        except RegistryError as exc:
            raise GitConfigError(str(exc)) from exc
    return policies


def _reject_generic_git_execution(registry: ProjectRegistry) -> None:
    for public in registry.list_public():
        record = registry.require(public["id"])
        if not record.permissions.execute:
            continue
        for rule in record.allowed_executables:
            if rule.alias.casefold() in {"git", "git.exe"} or Path(
                rule.executable
            ).name.casefold() in {"git", "git.exe"}:
                raise GitConfigError(
                    "Git may not be exposed through generic process execution; "
                    "Phase 7 reserves Git for dedicated synchronization tools."
                )


def apply_git_policy_overlay(
    registry: ProjectRegistry,
    path: str | Path | None,
) -> ProjectRegistry:
    """Return a registry enriched with trusted Git policies from one local file."""
    _reject_generic_git_execution(registry)
    if path is None:
        return registry

    config_path = Path(path)
    policies = _parse_policies(_load_yaml(config_path), registry)
    records = []
    for public in registry.list_public():
        record = registry.require(public["id"])
        policy = policies.get(record.project_id)
        if policy is None:
            records.append(record)
            continue
        permissions = replace(record.permissions, git=True)
        records.append(replace(record, permissions=permissions, git=policy))
    return ProjectRegistry(records)


def load_runtime_git_registry(registry: ProjectRegistry) -> ProjectRegistry:
    """Apply the configured local Git overlay, or leave Git disabled when absent."""
    configured = os.getenv(GIT_CONFIG_ENV_VAR)
    if configured:
        return apply_git_policy_overlay(registry, configured)
    if DEFAULT_GIT_CONFIG_PATH.is_file():
        return apply_git_policy_overlay(registry, DEFAULT_GIT_CONFIG_PATH)
    _reject_generic_git_execution(registry)
    return registry
