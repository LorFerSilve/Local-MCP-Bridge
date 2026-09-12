"""Local-only structured audit logging for security-relevant bridge activity.

The audit log is deliberately metadata-only. It never accepts raw command arguments,
process output, search queries, file contents, host paths, Git URLs, or credentials.
Runtime wiring enables a persistent logger; the reusable server factory defaults to a
non-persistent logger so imports and unit tests remain hermetic.
"""

from __future__ import annotations

import json
import os
import stat
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping, TypeAlias

from typing_extensions import TypedDict

from local_mcp_bridge.security.paths import file_identity, is_redirecting_metadata

AUDIT_SCHEMA_VERSION = 1
DEFAULT_AUDIT_FILE_BYTES = 4 * 1024 * 1024
MAX_AUDIT_FILE_BYTES = 64 * 1024 * 1024
DEFAULT_AUDIT_RETAINED_FILES = 5
MAX_AUDIT_RETAINED_FILES = 16
MAX_AUDIT_EVENT_BYTES = 4096
MAX_AUDIT_LABEL_CHARS = 128
MAX_AUDIT_DETAIL_FIELDS = 16
MAX_AUDIT_DETAIL_STRING_CHARS = 256

AuditScalar: TypeAlias = str | int | bool | None
AuditDetails: TypeAlias = Mapping[str, AuditScalar]
_ALLOWED_OUTCOMES = {"attempt", "success", "error", "denied"}


class AuditError(RuntimeError):
    """Raised when the local audit boundary cannot be used safely."""


class AuditStatus(TypedDict):
    enabled: bool
    healthy: bool


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _validate_label(value: str, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_AUDIT_LABEL_CHARS:
        raise AuditError(f"Audit {field} is invalid.")
    if any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value):
        raise AuditError(f"Audit {field} contains unsupported control characters.")
    return value


def _validate_details(details: AuditDetails | None) -> dict[str, AuditScalar]:
    if details is None:
        return {}
    if not isinstance(details, Mapping) or len(details) > MAX_AUDIT_DETAIL_FIELDS:
        raise AuditError("Audit details exceed the bounded metadata policy.")

    validated: dict[str, AuditScalar] = {}
    for key, value in details.items():
        safe_key = _validate_label(key, "detail key")
        if isinstance(value, bool) or value is None:
            validated[safe_key] = value
        elif type(value) is int:
            validated[safe_key] = value
        elif isinstance(value, str):
            if len(value) > MAX_AUDIT_DETAIL_STRING_CHARS:
                raise AuditError("Audit detail text exceeds the safety limit.")
            if any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value):
                raise AuditError("Audit detail text contains unsupported control characters.")
            validated[safe_key] = value
        else:
            raise AuditError("Audit details may contain only bounded scalar metadata.")
    return validated


def _prepare_directory(path: Path) -> Path:
    absolute = path.expanduser().absolute()
    if not absolute.anchor:
        raise AuditError("Audit directory must resolve to an absolute path.")

    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            try:
                current.mkdir(mode=0o700)
            except FileExistsError:
                pass
            except OSError as exc:
                raise AuditError("Audit directory cannot be created safely.") from exc
            try:
                metadata = os.lstat(current)
            except OSError as exc:
                raise AuditError("Audit directory cannot be inspected safely.") from exc
        except OSError as exc:
            raise AuditError("Audit directory cannot be inspected safely.") from exc

        if is_redirecting_metadata(metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise AuditError("Audit path must contain only non-redirecting directories.")

    if os.name != "nt":
        try:
            absolute.chmod(0o700)
        except OSError as exc:
            raise AuditError("Audit directory permissions cannot be secured.") from exc
    return absolute


def _existing_log_metadata(path: Path) -> os.stat_result | None:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise AuditError("Audit file cannot be inspected safely.") from exc

    if (
        is_redirecting_metadata(metadata)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink > 1
    ):
        raise AuditError("Audit file is not a safe private regular file.")
    return metadata


class AuditLogger:
    """Append bounded JSONL audit events to hardened local runtime state."""

    def __init__(
        self,
        directory: str | Path | None = None,
        *,
        max_file_bytes: int = DEFAULT_AUDIT_FILE_BYTES,
        retained_files: int = DEFAULT_AUDIT_RETAINED_FILES,
    ) -> None:
        if type(max_file_bytes) is not int or not 1024 <= max_file_bytes <= MAX_AUDIT_FILE_BYTES:
            raise AuditError(
                f"max_file_bytes must be between 1024 and {MAX_AUDIT_FILE_BYTES}."
            )
        if type(retained_files) is not int or not 1 <= retained_files <= MAX_AUDIT_RETAINED_FILES:
            raise AuditError(
                f"retained_files must be between 1 and {MAX_AUDIT_RETAINED_FILES}."
            )

        self._lock = threading.Lock()
        self._session_id = uuid.uuid4().hex
        self._sequence = 0
        self._healthy = True
        self._max_file_bytes = max_file_bytes
        self._retained_files = retained_files
        self._directory = _prepare_directory(Path(directory)) if directory is not None else None
        self._path = self._directory / "audit.jsonl" if self._directory is not None else None

        if self._path is not None:
            _existing_log_metadata(self._path)

    @property
    def enabled(self) -> bool:
        return self._path is not None

    @property
    def healthy(self) -> bool:
        return self._healthy

    def status(self) -> AuditStatus:
        return AuditStatus(enabled=self.enabled, healthy=self.healthy)

    def record(
        self,
        action: str,
        outcome: str,
        *,
        project_id: str | None = None,
        details: AuditDetails | None = None,
    ) -> bool:
        """Persist one metadata-only audit event.

        ``False`` means auditing is intentionally disabled for a hermetic/in-memory server.
        An enabled logger raises ``AuditError`` rather than silently dropping an event.
        """
        if not self.enabled:
            return False

        safe_action = _validate_label(action, "action")
        if outcome not in _ALLOWED_OUTCOMES:
            raise AuditError("Audit outcome is invalid.")
        safe_project = None if project_id is None else _validate_label(project_id, "project ID")
        safe_details = _validate_details(details)

        with self._lock:
            sequence = self._sequence + 1
            event = {
                "schema_version": AUDIT_SCHEMA_VERSION,
                "timestamp": _utc_now(),
                "session_id": self._session_id,
                "sequence": sequence,
                "event_id": uuid.uuid4().hex,
                "action": safe_action,
                "outcome": outcome,
                "project_id": safe_project,
                "details": safe_details,
            }
            encoded = (
                json.dumps(event, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                + "\n"
            ).encode("utf-8")
            if len(encoded) > MAX_AUDIT_EVENT_BYTES:
                raise AuditError("Audit event exceeds the bounded event-size policy.")

            try:
                self._append_event_locked(encoded)
            except (OSError, AuditError) as exc:
                self._healthy = False
                if isinstance(exc, AuditError):
                    raise
                raise AuditError("Audit event could not be persisted safely.") from exc
            else:
                self._sequence = sequence
                self._healthy = True
                return True

    def _append_event_locked(self, encoded: bytes) -> None:
        assert self._path is not None
        metadata = _existing_log_metadata(self._path)
        current_size = 0 if metadata is None else metadata.st_size
        if current_size + len(encoded) > self._max_file_bytes:
            self._rotate_locked()
            metadata = None

        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW

        try:
            descriptor = os.open(self._path, flags, 0o600)
        except OSError as exc:
            raise AuditError("Audit file cannot be opened safely.") from exc

        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink > 1
                or is_redirecting_metadata(opened)
            ):
                raise AuditError("Audit file changed into an unsafe object.")
            if metadata is not None and file_identity(opened) != file_identity(metadata):
                raise AuditError("Audit file identity changed during authorization.")
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)

            view = memoryview(encoded)
            written = 0
            while written < len(view):
                count = os.write(descriptor, view[written:])
                if count <= 0:
                    raise AuditError("Audit event could not be written completely.")
                written += count
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _rotate_locked(self) -> None:
        assert self._path is not None
        assert self._directory is not None
        current = _existing_log_metadata(self._path)
        if current is None:
            return

        if self._retained_files == 1:
            self._path.unlink()
            return

        # Validate every destination before changing the rotation set. A local symlink,
        # junction, device, directory, or hard link therefore makes rotation fail closed.
        for index in range(1, self._retained_files):
            _existing_log_metadata(self._directory / f"audit.jsonl.{index}")

        oldest = self._directory / f"audit.jsonl.{self._retained_files - 1}"
        if oldest.exists():
            oldest.unlink()

        for index in range(self._retained_files - 2, 0, -1):
            source = self._directory / f"audit.jsonl.{index}"
            destination = self._directory / f"audit.jsonl.{index + 1}"
            if source.exists():
                os.replace(source, destination)

        os.replace(self._path, self._directory / "audit.jsonl.1")
