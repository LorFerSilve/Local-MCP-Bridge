"""Race-resistant path confinement primitives for authorized project roots."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

MAX_PATH_CHARS = 1024
_WINDOWS_REPARSE_NAME_SURROGATE = 0x20000000
_WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_WINDOWS_DEVICE_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


class PathConfinementError(ValueError):
    """Raised when a path cannot be proven safe inside an authorized root."""


@dataclass(frozen=True, slots=True)
class FileIdentity:
    """Stable-enough identity used to detect path replacement around an open."""

    device: int
    inode: int
    file_type: int


@dataclass(frozen=True, slots=True)
class GuardedEntry:
    """One directory entry captured without following redirecting links."""

    name: str
    relative_path: PurePosixPath
    is_directory: bool
    is_file: bool
    size_bytes: int | None
    restricted: bool


def normalize_relative_path(raw_path: str) -> PurePosixPath:
    """Normalize one caller path while rejecting host-absolute/unsafe forms."""
    if not isinstance(raw_path, str):
        raise PathConfinementError("Path must be a string.")
    if "\x00" in raw_path or len(raw_path) > MAX_PATH_CHARS:
        raise PathConfinementError("Path is invalid or exceeds the safety limit.")

    portable = raw_path.replace("\\", "/")
    posix_path = PurePosixPath(portable)
    windows_path = PureWindowsPath(raw_path)

    if posix_path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        raise PathConfinementError("Only project-relative paths are allowed.")

    parts = tuple(part for part in posix_path.parts if part not in ("", "."))
    if any(part == ".." for part in parts):
        raise PathConfinementError("Parent-directory traversal is not allowed.")

    for part in parts:
        if any(ord(character) < 32 for character in part):
            raise PathConfinementError("Path contains unsupported control characters.")
        if ":" in part:
            raise PathConfinementError("Alternate data streams and colon paths are not allowed.")
        if part.endswith((" ", ".")):
            raise PathConfinementError("Path components may not end in a space or period.")
        device_name = part.rstrip(" .").split(".", 1)[0].casefold()
        if device_name in _WINDOWS_DEVICE_NAMES:
            raise PathConfinementError("Reserved device paths are not allowed.")

    return PurePosixPath(*parts) if parts else PurePosixPath(".")


def is_redirecting_metadata(metadata: os.stat_result) -> bool:
    """Return whether metadata represents a symlink/name-surrogate reparse point."""
    if stat.S_ISLNK(metadata.st_mode):
        return True

    attributes = getattr(metadata, "st_file_attributes", 0)
    if not attributes & _WINDOWS_REPARSE_POINT:
        return False

    reparse_tag = getattr(metadata, "st_reparse_tag", 0)
    return bool(reparse_tag & _WINDOWS_REPARSE_NAME_SURROGATE)


def file_identity(metadata: os.stat_result) -> FileIdentity:
    """Build a compact identity used for pre-open/post-open verification."""
    return FileIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        file_type=stat.S_IFMT(metadata.st_mode),
    )


def _contains_path(root: Path, candidate: Path) -> bool:
    return candidate == root or root in candidate.parents


def _relative_text(path: Path, root: Path) -> str:
    relative = path.relative_to(root)
    return relative.as_posix() if relative.parts else "."


def _lstat(path: Path) -> os.stat_result:
    try:
        return os.lstat(path)
    except OSError as exc:
        raise PathConfinementError("Path cannot be safely inspected.") from exc


def _reject_redirecting_components(root: Path, relative_path: PurePosixPath) -> None:
    current = root
    for part in relative_path.parts:
        if part == ".":
            continue
        current = current / part
        metadata = _lstat(current)
        if is_redirecting_metadata(metadata):
            raise PathConfinementError("Redirecting link/reparse paths are not allowed.")
        try:
            if current != root and os.path.ismount(current):
                raise PathConfinementError("Nested filesystem mount points are not allowed.")
        except OSError as exc:
            raise PathConfinementError("Path mount status cannot be safely inspected.") from exc


def _validate_regular_file_metadata(metadata: os.stat_result) -> None:
    if is_redirecting_metadata(metadata) or not stat.S_ISREG(metadata.st_mode):
        raise PathConfinementError("Requested path is not a safe regular file.")
    if metadata.st_nlink > 1:
        raise PathConfinementError("Hard-linked regular files are not allowed.")


class PathGuard:
    """Confinement helper bound to one canonical authorized project root."""

    def __init__(self, root: Path) -> None:
        try:
            canonical = root.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise PathConfinementError("Authorized project root cannot be resolved.") from exc

        root_metadata = _lstat(canonical)
        if not stat.S_ISDIR(root_metadata.st_mode) or is_redirecting_metadata(root_metadata):
            raise PathConfinementError("Authorized project root is not a safe directory.")
        self.root = canonical

    def resolve_existing(
        self,
        relative_path: PurePosixPath,
        *,
        expected: Literal["file", "directory", "either"],
    ) -> Path:
        """Resolve an existing path only after proving each traversed component safe."""
        _reject_redirecting_components(self.root, relative_path)
        candidate = self.root.joinpath(*relative_path.parts)

        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise PathConfinementError(
                "Requested path does not exist or cannot be resolved."
            ) from exc

        if not _contains_path(self.root, resolved):
            raise PathConfinementError("Requested path escapes the authorized project root.")

        _reject_redirecting_components(self.root, normalize_relative_path(_relative_text(resolved, self.root)))
        metadata = _lstat(resolved)
        if is_redirecting_metadata(metadata):
            raise PathConfinementError("Redirecting link/reparse paths are not allowed.")

        if expected == "file":
            _validate_regular_file_metadata(metadata)
        elif expected == "directory" and not stat.S_ISDIR(metadata.st_mode):
            raise PathConfinementError("Requested path is not a directory.")
        elif expected == "either" and not (
            stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)
        ):
            raise PathConfinementError("Requested path is not a regular file or directory.")

        return resolved

    def kind(self, relative_path: PurePosixPath) -> Literal["file", "directory"]:
        """Return the safe type of one confined path."""
        resolved = self.resolve_existing(relative_path, expected="either")
        metadata = _lstat(resolved)
        return "directory" if stat.S_ISDIR(metadata.st_mode) else "file"

    def read_bounded(self, relative_path: PurePosixPath, max_bytes: int) -> bytes:
        """Open and read a regular file with identity checks before any content read."""
        resolved = self.resolve_existing(relative_path, expected="file")
        before = _lstat(resolved)
        _validate_regular_file_metadata(before)
        expected_identity = file_identity(before)

        flags = os.O_RDONLY
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)

        try:
            descriptor = os.open(resolved, flags)
        except OSError as exc:
            raise PathConfinementError("File cannot be safely opened.") from exc

        try:
            opened = os.fstat(descriptor)
            _validate_regular_file_metadata(opened)
            if file_identity(opened) != expected_identity:
                raise PathConfinementError("File identity changed during authorization.")

            verified = self.resolve_existing(relative_path, expected="file")
            verified_metadata = _lstat(verified)
            if verified != resolved or file_identity(verified_metadata) != expected_identity:
                raise PathConfinementError("Path identity changed during authorization.")

            chunks: list[bytes] = []
            remaining = max_bytes + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(65_536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            return b"".join(chunks)
        except OSError as exc:
            raise PathConfinementError("File cannot be safely read.") from exc
        finally:
            os.close(descriptor)

    def snapshot_directory(self, relative_path: PurePosixPath) -> list[GuardedEntry]:
        """Capture one directory snapshot and reject identity changes around enumeration."""
        resolved = self.resolve_existing(relative_path, expected="directory")
        before = _lstat(resolved)
        expected_identity = file_identity(before)
        captured: list[GuardedEntry] = []

        try:
            with os.scandir(resolved) as iterator:
                for entry in iterator:
                    child_relative = normalize_relative_path(
                        str(relative_path / entry.name).replace("\\", "/")
                    )
                    try:
                        metadata = entry.stat(follow_symlinks=False)
                    except OSError:
                        captured.append(
                            GuardedEntry(
                                name=entry.name,
                                relative_path=child_relative,
                                is_directory=False,
                                is_file=False,
                                size_bytes=None,
                                restricted=True,
                            )
                        )
                        continue

                    redirecting = is_redirecting_metadata(metadata)
                    is_directory = stat.S_ISDIR(metadata.st_mode)
                    is_file = stat.S_ISREG(metadata.st_mode)
                    hard_linked = is_file and metadata.st_nlink > 1
                    captured.append(
                        GuardedEntry(
                            name=entry.name,
                            relative_path=child_relative,
                            is_directory=is_directory,
                            is_file=is_file,
                            size_bytes=metadata.st_size if is_file else None,
                            restricted=redirecting or hard_linked or not (is_directory or is_file),
                        )
                    )
        except (OSError, PathConfinementError) as exc:
            if isinstance(exc, PathConfinementError):
                raise
            raise PathConfinementError("Directory cannot be safely listed.") from exc

        after_resolved = self.resolve_existing(relative_path, expected="directory")
        after = _lstat(after_resolved)
        if after_resolved != resolved or file_identity(after) != expected_identity:
            raise PathConfinementError("Directory identity changed during enumeration.")

        return captured
