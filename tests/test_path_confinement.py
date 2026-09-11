"""Adversarial tests for Phase 4 path/symlink/junction confinement."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_mcp_bridge.registry import ProjectPermissions, ProjectRecord, ProjectRegistry
from local_mcp_bridge.security import paths as path_security
from local_mcp_bridge.security.paths import (
    PathConfinementError,
    PathGuard,
    is_redirecting_metadata,
    normalize_relative_path,
)
from local_mcp_bridge.tools.filesystem import FilesystemAccessError, FilesystemService


def _service(root: Path) -> FilesystemService:
    registry = ProjectRegistry(
        [
            ProjectRecord(
                project_id="demo",
                root=root.resolve(),
                permissions=ProjectPermissions(read=True, search=True),
            )
        ]
    )
    return FilesystemService(registry)


def test_windows_name_surrogate_reparse_metadata_is_redirecting() -> None:
    metadata = SimpleNamespace(
        st_mode=stat.S_IFDIR,
        st_file_attributes=0x400,
        st_reparse_tag=0xA0000003,
    )

    assert is_redirecting_metadata(metadata) is True


def test_non_surrogate_reparse_metadata_is_not_treated_as_path_redirect() -> None:
    metadata = SimpleNamespace(
        st_mode=stat.S_IFREG,
        st_file_attributes=0x400,
        st_reparse_tag=0x9000001A,
    )

    assert is_redirecting_metadata(metadata) is False


def test_hard_link_to_file_outside_project_is_not_readable(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    linked = project / "linked.txt"
    try:
        os.link(outside, linked)
    except OSError:
        pytest.skip("Hard links are unavailable in this environment.")

    with pytest.raises(FilesystemAccessError, match="Hard-linked"):
        _service(project).read_file("demo", "linked.txt")


def test_file_replacement_between_authorization_and_open_is_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target.txt"
    replacement = tmp_path / "replacement.txt"
    target.write_text("authorized", encoding="utf-8")
    replacement.write_text("replacement", encoding="utf-8")

    guard = PathGuard(tmp_path)
    relative = normalize_relative_path("target.txt")
    real_open = path_security.os.open
    swapped = False

    def swapping_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal swapped
        if not swapped:
            target.unlink()
            replacement.replace(target)
            swapped = True
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(path_security.os, "open", swapping_open)

    with pytest.raises(PathConfinementError, match="identity changed"):
        guard.read_bounded(relative, 1024)


def test_symlink_inside_project_is_denied_even_when_target_stays_inside_root(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.txt"
    target.write_text("safe-but-linked", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Symbolic links are unavailable in this environment.")

    with pytest.raises(FilesystemAccessError, match="Redirecting"):
        _service(tmp_path).read_file("demo", "link.txt")


def test_directory_listing_omits_redirecting_child(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "file.txt").write_text("safe", encoding="utf-8")
    link = tmp_path / "linked-directory"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Symbolic links are unavailable in this environment.")

    result = _service(tmp_path).list_directory("demo")

    assert "linked-directory" not in {entry["name"] for entry in result["entries"]}
    assert result["restricted_entries_omitted"] >= 1


@pytest.mark.skipif(os.name != "nt", reason="Windows junction behavior is Windows-specific.")
def test_windows_junction_escape_is_denied_for_read_list_and_search(tmp_path: Path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (project / "safe.txt").write_text("needle", encoding="utf-8")
    (outside / "secret.txt").write_text("needle-secret", encoding="utf-8")
    junction = project / "outside-junction"

    created = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        text=True,
        check=False,
    )
    if created.returncode != 0:
        pytest.skip(f"Could not create Windows junction: {created.stderr or created.stdout}")

    service = _service(project)

    with pytest.raises(FilesystemAccessError, match="Redirecting"):
        service.read_file("demo", "outside-junction/secret.txt")

    listing = service.list_directory("demo")
    assert "outside-junction" not in {entry["name"] for entry in listing["entries"]}
    assert listing["restricted_entries_omitted"] >= 1

    searched = service.search_text("demo", "needle")
    assert [match["path"] for match in searched["matches"]] == ["safe.txt"]
    assert searched["restricted_entries_omitted"] >= 1
