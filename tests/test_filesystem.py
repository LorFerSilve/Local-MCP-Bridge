"""Security-focused tests for read-only filesystem tools."""

from pathlib import Path

import pytest

from local_mcp_bridge.registry import ProjectPermissions, ProjectRecord, ProjectRegistry
from local_mcp_bridge.tools.filesystem import (
    FilesystemAccessError,
    FilesystemLimits,
    FilesystemService,
)


def _service(
    root: Path,
    *,
    read: bool = True,
    search: bool = True,
    limits: FilesystemLimits | None = None,
) -> FilesystemService:
    registry = ProjectRegistry(
        [
            ProjectRecord(
                project_id="demo",
                root=root.resolve(),
                permissions=ProjectPermissions(read=read, search=search),
            )
        ]
    )
    return FilesystemService(registry, limits)


def test_list_directory_is_bounded_sorted_and_hides_sensitive_entries(tmp_path: Path) -> None:
    (tmp_path / "z-dir").mkdir()
    (tmp_path / "a-dir").mkdir()
    (tmp_path / "z.txt").write_text("z", encoding="utf-8")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=secret", encoding="utf-8")
    (tmp_path / ".git").mkdir()

    result = _service(tmp_path).list_directory("demo")

    assert [entry["name"] for entry in result["entries"]] == [
        "a-dir",
        "z-dir",
        "a.txt",
        "z.txt",
    ]
    assert result["restricted_entries_omitted"] == 2
    assert result["truncated"] is False
    assert str(tmp_path) not in str(result)


def test_list_directory_limit_sets_truncated(tmp_path: Path) -> None:
    for index in range(3):
        (tmp_path / f"{index}.txt").write_text(str(index), encoding="utf-8")

    limits = FilesystemLimits(max_directory_entries=2)
    result = _service(tmp_path, limits=limits).list_directory("demo")

    assert len(result["entries"]) == 2
    assert result["truncated"] is True


def test_read_file_returns_bounded_line_slice(tmp_path: Path) -> None:
    file_path = tmp_path / "module.py"
    file_path.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8", newline="\n")

    result = _service(tmp_path).read_file("demo", "module.py", start_line=2, max_lines=2)

    assert result == {
        "project_id": "demo",
        "path": "module.py",
        "content": "two\nthree\n",
        "start_line": 2,
        "end_line": 3,
        "total_lines": 4,
        "truncated": True,
        "next_start_line": 4,
    }
    assert str(tmp_path) not in str(result)


def test_read_file_preserves_existing_crlf_bytes(tmp_path: Path) -> None:
    file_path = tmp_path / "windows.txt"
    file_path.write_bytes(b"one\r\ntwo\r\n")

    result = _service(tmp_path).read_file("demo", "windows.txt")

    assert result["content"] == "one\r\ntwo\r\n"
    assert result["total_lines"] == 2


def test_read_file_rejects_binary_and_large_files(tmp_path: Path) -> None:
    (tmp_path / "binary.bin").write_bytes(b"abc\x00def")
    (tmp_path / "large.txt").write_text("12345", encoding="utf-8")
    service = _service(tmp_path, limits=FilesystemLimits(max_read_bytes=4))

    with pytest.raises(FilesystemAccessError, match="Binary files"):
        _service(tmp_path).read_file("demo", "binary.bin")

    with pytest.raises(FilesystemAccessError, match="read-size limit"):
        service.read_file("demo", "large.txt")


def test_read_file_rejects_sensitive_files_but_allows_templates(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("TOKEN=secret", encoding="utf-8")
    (tmp_path / ".env.example").write_text("TOKEN=replace-me", encoding="utf-8")
    (tmp_path / "private.key").write_text("secret", encoding="utf-8")

    service = _service(tmp_path)

    with pytest.raises(FilesystemAccessError, match="restricted"):
        service.read_file("demo", ".env")
    with pytest.raises(FilesystemAccessError, match="restricted"):
        service.read_file("demo", "private.key")

    result = service.read_file("demo", ".env.example")
    assert result["content"] == "TOKEN=replace-me"


@pytest.mark.parametrize(
    "path",
    [
        "../outside.txt",
        "/etc/passwd",
        r"C:\Windows\win.ini",
        r"\\server\share\file.txt",
        "file.txt:alternate-stream",
        "CON.txt",
    ],
)
def test_unsafe_path_forms_are_rejected_without_host_path_leak(
    tmp_path: Path,
    path: str,
) -> None:
    service = _service(tmp_path)

    with pytest.raises(FilesystemAccessError) as exc_info:
        service.read_file("demo", path)

    assert str(tmp_path) not in str(exc_info.value)


def test_standard_symbolic_link_escape_is_rejected(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    outside_root = tmp_path / "outside"
    project_root.mkdir()
    outside_root.mkdir()
    (outside_root / "secret.txt").write_text("secret", encoding="utf-8")

    link = project_root / "outside-link"
    try:
        link.symlink_to(outside_root, target_is_directory=True)
    except OSError:
        pytest.skip("Symbolic links are unavailable in this environment.")

    service = _service(project_root)
    with pytest.raises(FilesystemAccessError, match="Redirecting"):
        service.read_file("demo", "outside-link/secret.txt")


def test_read_permission_is_enforced(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    service = _service(tmp_path, read=False, search=False)

    with pytest.raises(FilesystemAccessError, match="access denied"):
        service.list_directory("demo")
    with pytest.raises(FilesystemAccessError, match="access denied"):
        service.read_file("demo", "README.md")


def test_unknown_project_is_non_enumerating(tmp_path: Path) -> None:
    service = _service(tmp_path)

    with pytest.raises(FilesystemAccessError, match="Unknown project or filesystem access denied"):
        service.list_directory("unknown")


def test_search_permission_is_enforced_separately(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("needle", encoding="utf-8")
    service = _service(tmp_path, read=True, search=False)

    with pytest.raises(FilesystemAccessError, match="access denied"):
        service.search_text("demo", "needle")


def test_search_is_case_configurable_and_result_bounded(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("Needle needle NEEDLE\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("needle\n", encoding="utf-8")
    service = _service(tmp_path)

    result = service.search_text("demo", "needle", max_results=2)

    assert len(result["matches"]) == 2
    assert result["truncated"] is True
    assert result["matches"][0]["path"] == "a.txt"
    assert result["matches"][0]["line"] == 1
    assert result["matches"][0]["column"] == 1
    assert str(tmp_path) not in str(result)

    exact = service.search_text("demo", "NEEDLE", case_sensitive=True)
    assert [(match["path"], match["column"]) for match in exact["matches"]] == [
        ("a.txt", 15)
    ]


def test_search_skips_sensitive_binary_and_large_files(tmp_path: Path) -> None:
    (tmp_path / "safe.txt").write_text("find-me", encoding="utf-8")
    (tmp_path / ".env").write_text("find-me=secret", encoding="utf-8")
    (tmp_path / "binary.bin").write_bytes(b"find-me\x00binary")
    (tmp_path / "large.txt").write_text("find-me-too-large", encoding="utf-8")

    limits = FilesystemLimits(max_search_file_bytes=10)
    result = _service(tmp_path, limits=limits).search_text("demo", "find-me")

    assert [match["path"] for match in result["matches"]] == ["safe.txt"]
    assert result["restricted_entries_omitted"] >= 1
    assert result["skipped_binary_or_large"] >= 2


def test_search_does_not_follow_symbolic_links(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    outside_root = tmp_path / "outside"
    project_root.mkdir()
    outside_root.mkdir()
    (project_root / "safe.txt").write_text("needle", encoding="utf-8")
    (outside_root / "secret.txt").write_text("needle", encoding="utf-8")

    link = project_root / "outside-link"
    try:
        link.symlink_to(outside_root, target_is_directory=True)
    except OSError:
        pytest.skip("Symbolic links are unavailable in this environment.")

    result = _service(project_root).search_text("demo", "needle")

    assert [match["path"] for match in result["matches"]] == ["safe.txt"]
    assert result["restricted_entries_omitted"] >= 1


def test_search_limits_query_and_result_arguments(tmp_path: Path) -> None:
    service = _service(tmp_path, limits=FilesystemLimits(max_query_chars=4, max_search_results=2))

    with pytest.raises(FilesystemAccessError, match="query exceeds"):
        service.search_text("demo", "12345")
    with pytest.raises(FilesystemAccessError, match="max_results"):
        service.search_text("demo", "a", max_results=3)
    with pytest.raises(FilesystemAccessError, match="control characters"):
        service.search_text("demo", "a\nb")


def test_filesystem_limits_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        FilesystemLimits(max_read_bytes=0)
