"""Read-only filesystem capabilities for authorized project roots.

Phase 4 routes every filesystem operation through a confinement guard that
rejects redirecting links/reparse points and verifies file identity before any
content is read.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from typing_extensions import TypedDict

from local_mcp_bridge.registry import ProjectRecord, ProjectRegistry, RegistryError
from local_mcp_bridge.security.paths import (
    PathConfinementError,
    PathGuard,
    normalize_relative_path,
)

DEFAULT_READ_LINES = 400
DEFAULT_SEARCH_RESULTS = 50
_PREVIEW_CONTEXT = 120

_SENSITIVE_DIRECTORIES = {
    ".git",
    ".ssh",
    ".aws",
    ".azure",
    ".direnv",
    ".docker",
    ".gnupg",
    ".kube",
    ".terraform",
    "credentials",
    "secrets",
    "tokens",
}
_SENSITIVE_FILENAMES = {
    ".git-credentials",
    ".netrc",
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
    ".kdbx",
    ".p12",
    ".pem",
    ".pfx",
    ".ppk",
    ".tfstate",
}
_ENV_TEMPLATE_SUFFIXES = (".example", ".sample", ".template")


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
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError("Filesystem limits must all be positive integers.")


def _is_sensitive(relative_path: PurePosixPath) -> bool:
    parts = tuple(part.casefold() for part in relative_path.parts if part not in ("", "."))
    if not parts:
        return False

    if any(part in _SENSITIVE_DIRECTORIES for part in parts):
        return True

    name = parts[-1]
    if name in _SENSITIVE_FILENAMES:
        return True
    if name == ".env" or name == ".envrc":
        return True
    if name.startswith(".env.") and not name.endswith(_ENV_TEMPLATE_SUFFIXES):
        return True
    if name.endswith(".tfstate.backup"):
        return True
    if name.endswith(".json") and name.startswith(("service-account", "service_account")):
        return True

    return any(name.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES)


def _relative_text(relative_path: PurePosixPath) -> str:
    return relative_path.as_posix() if relative_path.parts != (".",) else "."


def _safe_relative(raw_path: str) -> PurePosixPath:
    try:
        return normalize_relative_path(raw_path)
    except PathConfinementError as exc:
        raise FilesystemAccessError(str(exc)) from exc


def _guard_for(project: ProjectRecord) -> PathGuard:
    try:
        return PathGuard(project.root)
    except PathConfinementError as exc:
        raise FilesystemAccessError(str(exc)) from exc


def _guard_error(exc: PathConfinementError) -> FilesystemAccessError:
    return FilesystemAccessError(str(exc))


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

    def _authorize_path(
        self,
        project: ProjectRecord,
        raw_path: str,
    ) -> tuple[PathGuard, PurePosixPath]:
        relative_path = _safe_relative(raw_path)
        if _is_sensitive(relative_path):
            raise FilesystemAccessError("The requested path is restricted.")
        return _guard_for(project), relative_path

    def list_directory(self, project_id: str, path: str = ".") -> DirectoryListResult:
        """Return a bounded, sorted listing of a permitted directory."""
        project = self._require_project(project_id, "read")
        guard, relative_directory = self._authorize_path(project, path)

        try:
            snapshot, truncated = guard.snapshot_directory(
                relative_directory,
                self._limits.max_directory_entries,
            )
        except PathConfinementError as exc:
            raise _guard_error(exc) from exc

        entries: list[DirectoryEntry] = []
        restricted = 0
        for entry in snapshot:
            if entry.restricted or _is_sensitive(entry.relative_path):
                restricted += 1
                continue
            entries.append(
                DirectoryEntry(
                    name=entry.name,
                    path=_relative_text(entry.relative_path),
                    type="directory" if entry.is_directory else "file",
                    size_bytes=entry.size_bytes,
                )
            )

        entries.sort(key=lambda item: (item["type"] != "directory", item["name"].casefold()))
        return DirectoryListResult(
            project_id=project_id,
            path=_relative_text(relative_directory),
            entries=entries,
            truncated=truncated,
            restricted_entries_omitted=restricted,
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
        guard, relative_file = self._authorize_path(project, path)
        try:
            data = guard.read_bounded(relative_file, self._limits.max_read_bytes)
        except PathConfinementError as exc:
            raise _guard_error(exc) from exc

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
        if total_lines == 0 and start_line != 1:
            raise FilesystemAccessError("start_line is beyond the end of the file.")
        if total_lines != 0 and start_line > total_lines:
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
            path=_relative_text(relative_file),
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
        guard, start_relative = self._authorize_path(project, path)
        try:
            start_kind = guard.kind(start_relative)
        except PathConfinementError as exc:
            raise _guard_error(exc) from exc

        matches: list[SearchMatch] = []
        files_scanned = 0
        bytes_scanned = 0
        skipped = 0
        restricted = 0
        entries_seen = 0
        truncated = False

        pending_directories: deque[PurePosixPath] = deque()
        pending_files: deque[PurePosixPath] = deque()
        if start_kind == "file":
            pending_files.append(start_relative)
        else:
            pending_directories.append(start_relative)

        needle = query if case_sensitive else query.lower()

        while pending_directories or pending_files:
            if files_scanned >= self._limits.max_search_files:
                truncated = True
                break

            if pending_files:
                file_relative = pending_files.popleft()
                try:
                    data = guard.read_bounded(file_relative, self._limits.max_search_file_bytes)
                except PathConfinementError:
                    skipped += 1
                    continue

                if len(data) > self._limits.max_search_file_bytes or b"\x00" in data:
                    skipped += 1
                    continue
                if bytes_scanned + len(data) > self._limits.max_search_total_bytes:
                    truncated = True
                    break

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
                                path=_relative_text(file_relative),
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
                continue

            directory_relative = pending_directories.popleft()
            remaining_entries = self._limits.max_search_entries - entries_seen
            if remaining_entries <= 0:
                truncated = True
                break

            try:
                snapshot, snapshot_truncated = guard.snapshot_directory(
                    directory_relative,
                    remaining_entries,
                )
            except PathConfinementError:
                restricted += 1
                continue

            entries_seen += len(snapshot)
            for entry in sorted(snapshot, key=lambda item: item.name.casefold()):
                if entry.restricted or _is_sensitive(entry.relative_path):
                    restricted += 1
                    continue
                if entry.is_directory:
                    pending_directories.append(entry.relative_path)
                elif entry.is_file:
                    pending_files.append(entry.relative_path)
                else:
                    restricted += 1

            if snapshot_truncated:
                truncated = True
                break

        return SearchTextResult(
            project_id=project_id,
            path=_relative_text(start_relative),
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
