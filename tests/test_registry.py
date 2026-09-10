"""Security-focused tests for Phase 2 project configuration and registry loading."""

from pathlib import Path

import pytest

from local_mcp_bridge.config import ConfigError, load_project_registry, load_runtime_registry


def _write_config(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def _project_yaml(root: Path, *, permissions: str = "read: true\n      search: true") -> str:
    return f"""
projects:
  demo:
    root: {root.as_posix()!r}
    permissions:
      {permissions.replace(chr(10), chr(10) + '      ')}
"""


def test_valid_project_is_canonicalized_and_public_metadata_hides_root(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    config = _write_config(tmp_path, _project_yaml(project_root))

    registry = load_project_registry(config)
    record = registry.require("demo")

    assert record.root == project_root.resolve(strict=True)
    assert registry.list_public() == [
        {
            "id": "demo",
            "permissions": {
                "read": True,
                "search": True,
                "execute": False,
                "git": False,
            },
        }
    ]
    assert "root" not in registry.list_public()[0]


def test_permissions_default_to_deny(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    config = _write_config(
        tmp_path,
        f"projects:\n  demo:\n    root: {project_root.as_posix()!r}\n",
    )

    registry = load_project_registry(config)

    assert registry.require("demo").permissions.read is False
    assert registry.require("demo").permissions.search is False
    assert registry.require("demo").permissions.execute is False
    assert registry.require("demo").permissions.git is False


def test_relative_project_root_is_rejected(tmp_path: Path) -> None:
    config = _write_config(
        tmp_path,
        "projects:\n  demo:\n    root: ./relative-project\n",
    )

    with pytest.raises(ConfigError, match="absolute path"):
        load_project_registry(config)


def test_missing_project_root_is_rejected(tmp_path: Path) -> None:
    missing = (tmp_path / "does-not-exist").as_posix()
    config = _write_config(
        tmp_path,
        f"projects:\n  demo:\n    root: {missing!r}\n",
    )

    with pytest.raises(ConfigError, match="does not exist"):
        load_project_registry(config)


def test_file_cannot_be_used_as_project_root(tmp_path: Path) -> None:
    file_path = tmp_path / "not-a-directory.txt"
    file_path.write_text("data", encoding="utf-8")
    config = _write_config(
        tmp_path,
        f"projects:\n  demo:\n    root: {file_path.as_posix()!r}\n",
    )

    with pytest.raises(ConfigError, match="must reference a directory"):
        load_project_registry(config)


def test_invalid_project_id_is_rejected(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    config = _write_config(
        tmp_path,
        f"projects:\n  Bad_Project:\n    root: {project_root.as_posix()!r}\n",
    )

    with pytest.raises(ConfigError, match="Project IDs must start"):
        load_project_registry(config)


def test_duplicate_yaml_project_id_is_rejected(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    config = _write_config(
        tmp_path,
        f"""
projects:
  demo:
    root: {first.as_posix()!r}
  demo:
    root: {second.as_posix()!r}
""",
    )

    with pytest.raises(ConfigError, match="Duplicate configuration key"):
        load_project_registry(config)


def test_same_canonical_root_cannot_be_registered_twice(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    config = _write_config(
        tmp_path,
        f"""
projects:
  alpha:
    root: {project_root.as_posix()!r}
  beta:
    root: {project_root.as_posix()!r}
""",
    )

    with pytest.raises(ConfigError, match="same root"):
        load_project_registry(config)


def test_unknown_permission_is_rejected(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    config = _write_config(
        tmp_path,
        _project_yaml(project_root, permissions="read: true\nadmin: true"),
    )

    with pytest.raises(ConfigError, match="Unknown permission"):
        load_project_registry(config)


def test_permission_values_must_be_boolean(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    config = _write_config(
        tmp_path,
        _project_yaml(project_root, permissions="read: yes-please"),
    )

    with pytest.raises(ConfigError, match="must be true or false"):
        load_project_registry(config)


def test_search_requires_read_permission(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    config = _write_config(
        tmp_path,
        _project_yaml(project_root, permissions="read: false\nsearch: true"),
    )

    with pytest.raises(ConfigError, match="search requires read access"):
        load_project_registry(config)


def test_unknown_project_config_key_is_rejected(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    config = _write_config(
        tmp_path,
        f"""
projects:
  demo:
    root: {project_root.as_posix()!r}
    unrestricted_shell: true
""",
    )

    with pytest.raises(ConfigError, match="Unknown project 'demo' key"):
        load_project_registry(config)


def test_missing_default_config_starts_with_empty_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LOCAL_MCP_BRIDGE_CONFIG", raising=False)

    registry = load_runtime_registry()

    assert len(registry) == 0


def test_explicit_missing_config_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "missing.yaml"
    monkeypatch.setenv("LOCAL_MCP_BRIDGE_CONFIG", str(missing))

    with pytest.raises(ConfigError, match="Cannot access configuration file"):
        load_runtime_registry()
