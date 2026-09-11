"""Cross-platform policy tests for unsafe directory child names."""

import os
from pathlib import Path

import pytest

from local_mcp_bridge.registry import ProjectPermissions, ProjectRecord, ProjectRegistry
from local_mcp_bridge.tools.filesystem import FilesystemService


@pytest.mark.skipif(os.name == "nt", reason="Colon filenames are not creatable on Windows.")
def test_unsafe_child_name_is_omitted_without_hiding_safe_siblings(tmp_path: Path) -> None:
    (tmp_path / "safe.txt").write_text("needle", encoding="utf-8")
    (tmp_path / "unsafe:name.txt").write_text("needle", encoding="utf-8")
    registry = ProjectRegistry(
        [
            ProjectRecord(
                project_id="demo",
                root=tmp_path.resolve(),
                permissions=ProjectPermissions(read=True, search=True),
            )
        ]
    )
    service = FilesystemService(registry)

    listing = service.list_directory("demo")
    assert [entry["name"] for entry in listing["entries"]] == ["safe.txt"]
    assert listing["restricted_entries_omitted"] == 1

    searched = service.search_text("demo", "needle")
    assert [match["path"] for match in searched["matches"]] == ["safe.txt"]
    assert searched["restricted_entries_omitted"] == 1
