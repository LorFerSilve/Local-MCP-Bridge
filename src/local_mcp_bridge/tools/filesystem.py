"""Read-only filesystem capabilities for authorized project roots.

Phase 3 deliberately exposes only bounded read operations. Every caller-supplied
path is interpreted as a project-relative path, canonicalized, and checked
against the registered root before I/O occurs.
"""

from __future__ import annotations

import os
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from typing_extensions import TypedDict

from local_mcp_bridge.registry import ProjectRecord, ProjectRegistry, RegistryError

DEFAULT_READ_LINES = 400
DEFAULT_SEARCH_RESULTS = 50
MAX_PATH_CHARS = 1024
_PREVIEW_CONTEXT = 120

_SENSITIVE_DIRECTORIES = {
    ".git",
    ".ssh",
    ".aws",
    ".azure",
    ".gnupg",
    "credentials",
    "secrets",
    "tokens",
}
_SENSITIVE_FILENAMES = {
    ".npmrc",
    ".pypirc",
    "credentials.json",
    "service-account.json",
    "service_account.json",
    "id_rsa",
    "id_ed25519",
}
_SENSITIVE_SUFFIXES = {
    ".key",
    ".p12",
    ".pfx",
    ".kdbx",
}
_ENV_TEMPLATE_SUFFIXES = (".example", ".sample", ".template")
_WINDOWS_DEVICE_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


class FilesystemAccessError(ValueError):
    """Raised when a filesystem request is invalid or not authorized."""


class DirectoryEntry(TypedDict):
    """One safe, project-relative directory entry."""

    name: str
    path: str
    type: Literal["file", "directory"]
    size_bytes: int | None


class DirectoryListResult(TypedDict):
    """Bounded directory listing returned to an MCP client."""

    project_id: str
    path: str
    entries: list[DirectoryEntry]
    truncated: bool
    restricted_entries_omitted: int


class ReadFileResult(TypedDict):
    """Bounded UTF-8 text file slice returned to an MCP client."""

    project_id: str
    path: str
    content: str
    start_line: int
    end_line: int
    total_lines: int
    truncated: bool
    next_start_line: int | None


class SearchMatch(TypedDict):
    """One plain-text search match."""

    path: str
    line: int
    column: int
    preview: str


class SearchTextResult(TypedDict):
    """Bounded plain-text recursive search result."""

    project_id: str
    path: str
    query: str
    matches: list[SearchMatch]
    files_scanned: int
    bytes_scanned: int
    skipped_binary_or_large: int
    restricted_entries_omitted: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class FilesystemLimits:
    """Hard limits for read-only filesystem operations."""

    max_read_bytes: int = 1_048_576
    max_read_lines: int = 500
    max_directory_entries: int = 500
    max_search_results: int = 100
    max_search_files: int = 2_000
    max_search_entries: int = 10_000
    max_search_file_bytes: int = 1_048_576
    max_search_total_bytes: int = 33_554_432
    max_query_chars: int = 512

    def __post_init__(self) -> None:
        values = (
            self.max_read_bytes,
            self.max_read_lines,
            self.max_directory_entries,
            self.max_search_results,
            self.max_search_files,
            self.max_search_entries,
            self.max_search_file_bytes,
            self.max_search_total_bytes,
            self.max_query_chars,
        )
        if any(value <= 0 for value in values):
            raise ValueError("Filesystem limits must all be positive integers.")


def _normalize_relative_path(raw_path: str) -> PurePosixPath:
    if not isinstance(raw_path, str):
        raise FilesystemAccessError("Path must be a string.")
    if "\x00" in raw_path or len(raw_path) > MAX_PATH_CHARS:
        raise FilesystemAccessError("Path is invalid or exceeds the safety limit.")

    portable = raw_path.replace("\\", "/")
    posix_path = PurePosixPath(portable)
    windows_path = PureWindowsPath(raw_path)

    if posix_path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        raise FilesystemAccessError("Only project-relative paths are allowed.")

    parts = tuple(part for part in posix_path.parts if part not in ("", "."))
    if any(part == ".." for part in parts):
        raise FilesystemAccessError("Parent-directory traversal is not allowed.")

    for part in parts:
        if any(ord(character) < 32 for character in part):
            raise FilesystemAccessError("Path contains unsupported control characters.")
        if ":" in part:
            raise FilesystemAccessError("Alternate data streams and colon paths are not allowed.")
        if part.endswith((" ", ".")):
            raise FilesystemAccessError("Path components may not end in a space or period.")
        device_name = part.rstrip(" .").split(".", 1)[0].casefold()
        if device_name in _WINDOWS_DEVICE_NAMES:
            raise FilesystemAccessError("Reserved device paths are not allowed.")

    return PurePosixPath(*parts) if parts else PurePosixPath(".")


def _relative_text(path: Path, root: Path) -> str:
    relative = path.relative_to(root)
    return relative.as_posix() if relative.parts else "."


def _is_sensitive(relative_path: PurePosixPath) -> bool:
    parts = tuple(part.casefold() for part in relative_path.parts if part not in ("", "."))
    if not parts:
        return False

    if any(part in _SENSITIVE_DIRECTORIES for part in parts):
        return True

    name = parts[-1]
    if name in _SENSITIVE_FILENAMES:
        return True

    if name == ".env":
        return True
    if name.startswith(".env.") and not name.endswith(_ENV_TEMPLATE_SUFFIXES):
        return True

    return any(name.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES)


def _contains_path(root: Path, candidate: Path) -> bool:
    return candidate == root or root in candidate.parents


def _reject_symlink_components(root: Path, relative_path: PurePosixPath) -> None:
    current = root
    for part in relative_path.parts:
        if part == ".":
            continue
        current = current / part
        try:
            if current.is_symlink():
                raise FilesystemAccessError("Symbolic-link paths are not allowed in Phase 3.")
        except OSError as exc:
            raise FilesystemAccessError("Path cannot be safely inspected.") from exc


class FilesystemService:
    """Policy-enforced, read-only filesystem service."""

    def __init__(
        self,
        registry: ProjectRegistry,
        limits: FilesystemLimits | None = None,
    ) -> None:
        self._registry = registry
        self._limits = limits or FilesystemLimits()

    def _require_project(
        self,
        project_id: str,
        permission: Literal["read", "search"],
    ) -> ProjectRecord:
        try:
            project = self._registry.require(project_id)
        except RegistryError as exc:
            raise FilesystemAccessError("Unknown project or filesystem access denied.") from exc

        allowed = (
            project.permissions.read
            if permission == "read"
            else project.permissions.read and project.permissions.search
        )
        if not allowed:
            raise FilesystemAccessError("Unknown project or filesystem access denied.")
        return project

    def _resolve_existing(
        self,
        project: ProjectRecord,
        raw_path: str,
        *,
        expected: Literal["file", "directory", "either"],
    ) -> tuple[Path, PurePosixPath]:
        relative_path = _normalize_relative_path(raw_path)
        if _is_sensitive(relative_path):
            raise FilesystemAccessError("The requested path is restricted.")

        _reject_symlink_components(project.root, relative_path)
        candidate = project.root.joinpath(*relative_path.parts)

        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise FilesystemAccessError(
                "Requested path does not exist or cannot be resolved."
            ) from exc

        if not _contains_path(project.root, resolved):
            raise FilesystemAccessError("Requested path escapes the authorized project root.")

        try:
            if expected == "file" and not resolved.is_file():
                raise FilesystemAccessError("Requested path is not a regular file.")
            if expected == "directory" and not resolved.is_dir():
                raise FilesystemAccessError("Requested path is not a directory.")
            if expected == "either" and not (resolved.is_file() or resolved.is_dir()):
                raise FilesystemAccessError("Requested path is not a regular file or directory.")
        except OSError as exc:
            raise FilesystemAccessError("Requested path cannot be safely inspected.") from exc

        return resolved, relative_path

    def list_directory(self, project_id: str, path: str = ".") -> DirectoryListResult:
        """Return a bounded, sorted listing of a permitted directory."""
        project = self._require_project(project_id, "read")
        directory, _ = self._resolve_existing(project, path, expected="directory")
        relative_directory = _normalize_relative_path(_relative_text(directory, project.root))

        entries: list[DirectoryEntry] = []
        restricted_entries = 0
        truncated = False
        scanned = 0

        try:
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    scanned += 1
                    if scanned > self._limits.max_directory_entries:
                        truncated = True
                        break

                    child_relative = PurePosixPath(*relative_directory.parts, entry.name)
                    if _is_sensitive(child_relative) or entry.is_symlink():
                        restricted_entries += 1
                        continue

                    try:
                        is_directory = entry.is_dir(follow_symlinks=False)
                        is_file = entry.is_file(follow_symlinks=False)
                        if not is_directory and not is_file:
                            restricted_entries += 1
                            continue
                        size = entry.stat(follow_symlinks=False).st_size if is_file else None
                    except OSError:
                        restricted_entries += 1
                        continue

                    entries.append(
                        DirectoryEntry(
                            name=entry.name,
                            path=child_relative.as_posix(),
                            type="directory" if is_directory else "file",
                            size_bytes=size,
                        )
                    )
        except OSError as exc:
            raise FilesystemAccessError("Directory cannot be safely listed.") from exc

        entries.sort(key=lambda item: (item["type"] != "directory", item["name"].casefold()))
        return DirectoryListResult(
            project_id=project_id,
            path=_relative_text(directory, project.root),
            entries=entries,
            truncated=truncated,
            restricted_entries_omitted=restricted_entries,
        )

    def read_file(
        self,
        project_id: str,
        path: str,
        start_line: int = 1,
        max_lines: int = DEFAULT_READ_LINES,
    ) -> ReadFileResult:
        """Read a bounded UTF-8 text slice from an authorized regular file."""
        if start_line < 1:
            raise FilesystemAccessError("start_line must be at least 1.")
        if max_lines < 1 or max_lines > self._limits.max_read_lines:
            raise FilesystemAccessError(
                f"max_lines must be between 1 and {self._limits.max_read_lines}."
            )

        project = self._require_project(project_id, "read")
        file_path, _ = self._resolve_existing(project, path, expected="file")

        try:
            size = file_path.stat().st_size
            if size > self._limits.max_read_bytes:
                raise FilesystemAccessError("File exceeds the configured read-size limit.")
            data = file_path.read_bytes()
        except FilesystemAccessError:
            raise
        except OSError as exc:
            raise FilesystemAccessError("File cannot be safely read.") from exc

        if len(data) > self._limits.max_read_bytes:
            raise FilesystemAccessError("File exceeds the configured read-size limit.")
        if b"\x00" in data:
            raise FilesystemAccessError("Binary files are not readable through this text tool.")

        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FilesystemAccessError("File is not valid UTF-8 text.") from exc

        lines = text.splitlines(keepends=True)
        total_lines = len(lines)
        if start_line > total_lines and total_lines != 0:
            raise FilesystemAccessError("start_line is beyond the end of the file.")

        if total_lines == 0:
            content = ""
            end_line = 0
            next_start_line = None
            truncated = False
        else:
            start_index = start_line - 1
            end_index = min(start_index + max_lines, total_lines)
            content = "".join(lines[start_index:end_index])
            end_line = end_index
            next_start_line = end_index + 1 if end_index < total_lines else None
            truncated = next_start_line is not None

        return ReadFileResult(
            project_id=project_id,
            path=_relative_text(file_path, project.root),
            content=content,
            start_line=start_line,
            end_line=end_line,
            total_lines=total_lines,
            truncated=truncated,
            next_start_line=next_start_line,
        )

    def search_text(
        self,
        project_id: str,
        query: str,
        path: str = ".",
        case_sensitive: bool = False,
        max_results: int = DEFAULT_SEARCH_RESULTS,
    ) -> SearchTextResult:
        """Search UTF-8 text files using a bounded plain substring search."""
        if not isinstance(query, str) or not query:
            raise FilesystemAccessError("Search query must be a non-empty string.")
        if len(query) > self._limits.max_query_chars:
            raise FilesystemAccessError("Search query exceeds the safety limit.")
        if any(ord(character) < 32 for character in query):
            raise FilesystemAccessError("Search query may not contain control characters.")
        if max_results < 1 or max_results > self._limits.max_search_results:
            raise FilesystemAccessError(
                f"max_results must be between 1 and {self._limits.max_search_results}."
            )

        project = self._require_project(project_id, "search")
        start_path, _ = self._resolve_existing(project, path, expected="either")

        matches: list[SearchMatch] = []
        files_scanned = 0
        bytes_scanned = 0
        skipped = 0
        restricted = 0
        entries_seen = 0
        truncated = False

        pending_directories: deque[Path] = deque()
        pending_files: deque[Path] = deque()
        if start_path.is_file():
            pending_files.append(start_path)
        else:
            pending_directories.append(start_path)

        needle = query if case_sensitive else query.lower()

        while pending_directories or pending_files:
            if files_scanned >= self._limits.max_search_files:
                truncated = True
                break
            if entries_seen >= self._limits.max_search_entries:
                truncated = True
                break

            if pending_files:
                file_path = pending_files.popleft()
            else:
                directory = pending_directories.popleft()
                try:
                    scanned_entries = list(os.scandir(directory))
                except OSError:
                    restricted += 1
                    continue

                scanned_entries.sort(key=lambda entry: entry.name.casefold())
                for entry in scanned_entries:
                    entries_seen += 1
                    if entries_seen > self._limits.max_search_entries:
                        truncated = True
                        break

                    candidate = Path(entry.path)
                    try:
                        relative = PurePosixPath(_relative_text(candidate, project.root))
                    except ValueError:
                        restricted += 1
                        continue

                    if _is_sensitive(relative) or entry.is_symlink():
                        restricted += 1
                        continue

                    try:
                        resolved = candidate.resolve(strict=True)
                    except (OSError, RuntimeError):
                        restricted += 1
                        continue
                    if not _contains_path(project.root, resolved):
                        restricted += 1
                        continue

                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending_directories.append(resolved)
                        elif entry.is_file(follow_symlinks=False):
                            pending_files.append(resolved)
                    except OSError:
                        restricted += 1

                if truncated:
                    break
                continue

            try:
                stat = file_path.stat()
            except OSError:
                skipped += 1
                continue

            if stat.st_size > self._limits.max_search_file_bytes:
                skipped += 1
                continue
            if bytes_scanned + stat.st_size > self._limits.max_search_total_bytes:
                truncated = True
                break

            try:
                data = file_path.read_bytes()
            except OSError:
                skipped += 1
                continue

            if len(data) > self._limits.max_search_file_bytes or b"\x00" in data:
                skipped += 1
                continue

            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                skipped += 1
                continue

            files_scanned += 1
            bytes_scanned += len(data)

            for line_number, line in enumerate(text.splitlines(), start=1):
                haystack = line if case_sensitive else line.lower()
                offset = 0
                while True:
                    column_index = haystack.find(needle, offset)
                    if column_index < 0:
                        break

                    matches.append(
                        SearchMatch(
                            path=_relative_text(file_path, project.root),
                            line=line_number,
                            column=column_index + 1,
                            preview=_make_preview(line, column_index, len(query)),
                        )
                    )
                    if len(matches) >= max_results:
                        truncated = True
                        break
                    offset = column_index + max(1, len(needle))

                if truncated:
                    break
            if truncated:
                break

        return SearchTextResult(
            project_id=project_id,
            path=_relative_text(start_path, project.root),
            query=query,
            matches=matches,
            files_scanned=files_scanned,
            bytes_scanned=bytes_scanned,
            skipped_binary_or_large=skipped,
            restricted_entries_omitted=restricted,
            truncated=truncated,
        )


def _make_preview(line: str, column_index: int, query_length: int) -> str:
    start = max(0, column_index - _PREVIEW_CONTEXT)
    end = min(len(line), column_index + query_length + _PREVIEW_CONTEXT)
    excerpt = line[start:end]
    excerpt = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", excerpt)
    if start > 0:
        excerpt = "…" + excerpt
    if end < len(line):
        excerpt += "…"
    return excerpt
