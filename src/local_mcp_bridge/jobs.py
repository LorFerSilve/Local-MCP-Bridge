"""Persistent background job management for controlled local execution.

Phase 6 turns the bounded Phase 5 execution primitive into durable background jobs.
Job metadata and already-sanitized terminal output can be persisted beneath a local,
Git-ignored state directory. Raw argv is intentionally never written to disk because
arguments may contain credentials or other sensitive values.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from typing_extensions import TypedDict

from local_mcp_bridge.registry import ProjectRegistry, RegistryError
from local_mcp_bridge.security.paths import (
    PathConfinementError,
    PathGuard,
    is_redirecting_metadata,
    normalize_relative_path,
)
from local_mcp_bridge.tools.execution import (
    MAX_ARGUMENT_CHARS,
    MAX_ARGUMENTS,
    MAX_TOTAL_ARGUMENT_CHARS,
    ExecutionError,
    ExecutionService,
    ProcessResult,
)

JOB_SCHEMA_VERSION = 1
DEFAULT_JOB_HISTORY_LIMIT = 128
MAX_JOB_HISTORY_LIMIT = 512
MAX_ACTIVE_MANAGED_JOBS = 32
DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 100
DEFAULT_OUTPUT_CHARS = 32_768
MAX_OUTPUT_CHARS = 131_072
MAX_STATE_FILES_TO_SCAN = 1_024
MAX_STATE_FILE_BYTES = 8 * 1024 * 1024

_JOB_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_ACTIVE_STATUSES = {"starting", "running", "cancelling"}
_TERMINAL_STATUSES = {
    "succeeded",
    "failed",
    "cancelled",
    "timeout",
    "output_limit",
    "interrupted",
}

JobStatus = Literal[
    "starting",
    "running",
    "cancelling",
    "succeeded",
    "failed",
    "cancelled",
    "timeout",
    "output_limit",
    "interrupted",
]


class JobError(ValueError):
    """Raised when a job request or lookup is invalid."""


class JobSummary(TypedDict):
    """MCP-safe job metadata with no host paths or raw arguments."""

    job_id: str
    project_id: str
    executable: str
    cwd: str
    status: JobStatus
    created_at: str
    started_at: str | None
    finished_at: str | None
    exit_code: int | None
    termination_reason: str | None
    output_truncated: bool
    argument_count: int
    persistent: bool
    error: str | None


class JobStartResult(TypedDict):
    job: JobSummary


class JobListResult(TypedDict):
    jobs: list[JobSummary]


class JobOutputResult(TypedDict):
    job_id: str
    stream: Literal["stdout", "stderr"]
    status: JobStatus
    data: str
    offset: int
    next_offset: int
    eof: bool
    complete: bool


class JobCancelResult(TypedDict):
    job_id: str
    accepted: bool
    status: JobStatus


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _normalize_cwd(raw: str) -> tuple[str, PurePosixPath]:
    try:
        normalized = normalize_relative_path(raw)
    except PathConfinementError as exc:
        raise JobError("Working directory is not a valid project-relative path.") from exc
    text = "." if normalized.parts == (".",) else normalized.as_posix()
    return text, normalized


@dataclass(slots=True)
class _JobRecord:
    job_id: str
    project_id: str
    executable: str
    cwd: str
    status: JobStatus
    created_at: str
    argument_count: int
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    termination_reason: str | None = None
    output_truncated: bool = False
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    persistent: bool = False
    task: asyncio.Task[None] | None = field(default=None, repr=False)

    def summary(self) -> JobSummary:
        return JobSummary(
            job_id=self.job_id,
            project_id=self.project_id,
            executable=self.executable,
            cwd=self.cwd,
            status=self.status,
            created_at=self.created_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
            exit_code=self.exit_code,
            termination_reason=self.termination_reason,
            output_truncated=self.output_truncated,
            argument_count=self.argument_count,
            persistent=self.persistent,
            error=self.error,
        )

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": JOB_SCHEMA_VERSION,
            "job_id": self.job_id,
            "project_id": self.project_id,
            "executable": self.executable,
            "cwd": self.cwd,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "exit_code": self.exit_code,
            "termination_reason": self.termination_reason,
            "output_truncated": self.output_truncated,
            "argument_count": self.argument_count,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error": self.error,
        }


class JobManager:
    """Own background process tasks and durable, bounded job history."""

    def __init__(
        self,
        registry: ProjectRegistry,
        execution: ExecutionService,
        state_dir: str | Path | None = None,
        history_limit: int = DEFAULT_JOB_HISTORY_LIMIT,
    ) -> None:
        if type(history_limit) is not int or not 1 <= history_limit <= MAX_JOB_HISTORY_LIMIT:
            raise JobError(f"history_limit must be between 1 and {MAX_JOB_HISTORY_LIMIT}.")

        self._registry = registry
        self._execution = execution
        self._history_limit = history_limit
        self._jobs: dict[str, _JobRecord] = {}
        self._admission_lock = asyncio.Lock()
        self._state_dir = (
            Path(state_dir).expanduser().absolute() if state_dir is not None else None
        )

        if self._state_dir is not None:
            self._prepare_state_directory()
            self._load_state()
            self._prune_history_sync()

    @property
    def persistent(self) -> bool:
        return self._state_dir is not None

    @staticmethod
    def _reject_redirecting_components(path: Path) -> None:
        """Reject symlink/junction/reparse components in the runtime-state path."""
        absolute = path.absolute()
        anchor = Path(absolute.anchor)
        current = anchor
        for part in absolute.parts[1:]:
            current /= part
            try:
                metadata = os.lstat(current)
            except OSError as exc:
                raise JobError("Job state path cannot be safely inspected.") from exc
            if is_redirecting_metadata(metadata):
                raise JobError("Job state path contains a redirecting link or reparse point.")

    def _prepare_state_directory(self) -> None:
        assert self._state_dir is not None
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise JobError("Job state directory cannot be created.") from exc
        self._reject_redirecting_components(self._state_dir)

        try:
            metadata = os.lstat(self._state_dir)
        except OSError as exc:
            raise JobError("Job state directory cannot be safely inspected.") from exc
        if is_redirecting_metadata(metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise JobError("Job state path must reference a non-redirecting directory.")

        if os.name != "nt":
            try:
                self._state_dir.chmod(0o700)
            except OSError as exc:
                raise JobError("Job state directory permissions cannot be secured.") from exc

    def _state_path(self, job_id: str) -> Path:
        assert self._state_dir is not None
        return self._state_dir / f"{job_id}.job.json"

    @staticmethod
    def _validate_arguments(args: list[str] | None) -> list[str]:
        if args is None:
            return []
        if not isinstance(args, list):
            raise JobError("Job arguments must be a list of strings.")
        if len(args) > MAX_ARGUMENTS:
            raise JobError(f"At most {MAX_ARGUMENTS} job arguments are allowed.")

        total = 0
        validated: list[str] = []
        for argument in args:
            if not isinstance(argument, str):
                raise JobError("Job arguments must all be strings.")
            if "\x00" in argument:
                raise JobError("Job arguments may not contain NUL characters.")
            if any(ord(character) < 32 and character not in ("\t",) for character in argument):
                raise JobError("Job arguments contain unsupported control characters.")
            if len(argument) > MAX_ARGUMENT_CHARS:
                raise JobError(
                    f"Individual job arguments may not exceed {MAX_ARGUMENT_CHARS} characters."
                )
            total += len(argument)
            if total > MAX_TOTAL_ARGUMENT_CHARS:
                raise JobError(
                    f"Combined job arguments may not exceed {MAX_TOTAL_ARGUMENT_CHARS} characters."
                )
            validated.append(argument)
        return validated

    def _validate_start_request(
        self,
        project_id: str,
        executable: str,
        args: list[str] | None,
        cwd: str,
        timeout_seconds: int | None,
    ) -> tuple[list[str], str]:
        try:
            project = self._registry.require(project_id)
        except RegistryError as exc:
            raise JobError("Unknown project ID.") from exc
        if not project.permissions.execute:
            raise JobError("Process execution is not enabled for this project.")
        if not isinstance(executable, str) or not executable:
            raise JobError("Executable alias must be a non-empty string.")
        try:
            project.require_executable(executable)
        except RegistryError as exc:
            raise JobError("Executable is not allowlisted for this project.") from exc

        validated_args = self._validate_arguments(args)
        safe_cwd, normalized_cwd = _normalize_cwd(cwd)
        try:
            PathGuard(project.root).resolve_existing(normalized_cwd, expected="directory")
        except PathConfinementError as exc:
            raise JobError("Working directory is not safely confined to the project.") from exc

        if timeout_seconds is not None:
            if type(timeout_seconds) is not int:
                raise JobError("timeout_seconds must be an integer.")
            if not 1 <= timeout_seconds <= project.execution.max_timeout_seconds:
                raise JobError(
                    "timeout_seconds must be between 1 and the project's configured maximum."
                )

        return validated_args, safe_cwd

    def _active_job_count(self) -> int:
        return sum(1 for record in self._jobs.values() if record.status in _ACTIVE_STATUSES)

    async def start_job(
        self,
        project_id: str,
        executable: str,
        args: list[str] | None = None,
        cwd: str = ".",
        timeout_seconds: int | None = None,
    ) -> JobStartResult:
        """Validate and start one background process, returning immediately with a job ID."""
        validated_args, safe_cwd = self._validate_start_request(
            project_id,
            executable,
            args,
            cwd,
            timeout_seconds,
        )

        async with self._admission_lock:
            if self._active_job_count() >= MAX_ACTIVE_MANAGED_JOBS:
                raise JobError("Global managed-job capacity is currently reached.")
            self._prune_history_sync()
            job_id = uuid.uuid4().hex
            record = _JobRecord(
                job_id=job_id,
                project_id=project_id,
                executable=executable,
                cwd=safe_cwd,
                status="starting",
                created_at=_utc_now(),
                argument_count=len(validated_args),
            )
            self._jobs[job_id] = record

        await self._persist(record)
        record.task = asyncio.create_task(
            self._run_job(record, validated_args, timeout_seconds),
            name=f"local-mcp-job-{job_id}",
        )
        await asyncio.sleep(0)
        return JobStartResult(job=record.summary())

    async def _run_job(
        self,
        record: _JobRecord,
        args: list[str],
        timeout_seconds: int | None,
    ) -> None:
        record.status = "running"
        record.started_at = _utc_now()
        await self._persist(record)

        try:
            result = await self._execution.run_process(
                project_id=record.project_id,
                executable=record.executable,
                args=args,
                cwd=record.cwd,
                timeout_seconds=timeout_seconds,
            )
        except asyncio.CancelledError:
            record.status = "cancelled"
            record.finished_at = _utc_now()
            record.termination_reason = "cancelled"
            await self._persist(record)
            return
        except ExecutionError as exc:
            record.status = "failed"
            record.finished_at = _utc_now()
            record.error = str(exc)
            await self._persist(record)
            return
        except Exception:
            record.status = "failed"
            record.finished_at = _utc_now()
            record.error = "Managed process failed unexpectedly."
            await self._persist(record)
            return

        self._apply_result(record, result)
        await self._persist(record)
        self._prune_history_sync()

    @staticmethod
    def _apply_result(record: _JobRecord, result: ProcessResult) -> None:
        record.exit_code = result["exit_code"]
        record.stdout = result["stdout"]
        record.stderr = result["stderr"]
        record.termination_reason = result["termination_reason"]
        record.output_truncated = result["output_truncated"]
        record.finished_at = _utc_now()

        reason = result["termination_reason"]
        if reason == "timeout":
            record.status = "timeout"
        elif reason == "output_limit":
            record.status = "output_limit"
        elif result["exit_code"] == 0:
            record.status = "succeeded"
        else:
            record.status = "failed"

    def get_job(self, job_id: str) -> JobSummary:
        return self._require_job(job_id).summary()

    def list_jobs(
        self,
        project_id: str | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> JobListResult:
        if type(limit) is not int or not 1 <= limit <= MAX_LIST_LIMIT:
            raise JobError(f"limit must be between 1 and {MAX_LIST_LIMIT}.")
        if project_id is not None and not isinstance(project_id, str):
            raise JobError("project_id must be a string when supplied.")

        records = list(self._jobs.values())
        if project_id is not None:
            records = [record for record in records if record.project_id == project_id]
        records.sort(key=lambda record: (record.created_at, record.job_id), reverse=True)
        return JobListResult(jobs=[record.summary() for record in records[:limit]])

    def get_job_output(
        self,
        job_id: str,
        stream: Literal["stdout", "stderr"] = "stdout",
        offset: int = 0,
        max_chars: int = DEFAULT_OUTPUT_CHARS,
    ) -> JobOutputResult:
        record = self._require_job(job_id)
        if stream not in ("stdout", "stderr"):
            raise JobError("stream must be either 'stdout' or 'stderr'.")
        if type(offset) is not int or offset < 0:
            raise JobError("offset must be a non-negative integer.")
        if type(max_chars) is not int or not 1 <= max_chars <= MAX_OUTPUT_CHARS:
            raise JobError(f"max_chars must be between 1 and {MAX_OUTPUT_CHARS}.")

        text = record.stdout if stream == "stdout" else record.stderr
        if offset > len(text):
            raise JobError("offset exceeds the currently available stream length.")
        end = min(len(text), offset + max_chars)
        terminal = record.status in _TERMINAL_STATUSES
        return JobOutputResult(
            job_id=record.job_id,
            stream=stream,
            status=record.status,
            data=text[offset:end],
            offset=offset,
            next_offset=end,
            eof=end >= len(text),
            complete=terminal,
        )

    async def cancel_job(self, job_id: str) -> JobCancelResult:
        record = self._require_job(job_id)
        if record.status not in _ACTIVE_STATUSES or record.task is None:
            return JobCancelResult(
                job_id=record.job_id,
                accepted=False,
                status=record.status,
            )

        record.status = "cancelling"
        await self._persist(record)
        record.task.cancel()
        await asyncio.gather(record.task, return_exceptions=True)
        return JobCancelResult(
            job_id=record.job_id,
            accepted=True,
            status=record.status,
        )

    def _require_job(self, job_id: str) -> _JobRecord:
        if not isinstance(job_id, str) or not _JOB_ID_PATTERN.fullmatch(job_id):
            raise JobError("Unknown job ID.")
        try:
            return self._jobs[job_id]
        except KeyError as exc:
            raise JobError("Unknown job ID.") from exc

    async def _persist(self, record: _JobRecord) -> None:
        if self._state_dir is None:
            record.persistent = False
            return

        payload = record.payload()
        try:
            await asyncio.to_thread(self._write_payload_sync, record.job_id, payload)
        except (OSError, ValueError, TypeError):
            record.persistent = False
        else:
            record.persistent = True

    def _write_payload_sync(self, job_id: str, payload: dict[str, object]) -> None:
        assert self._state_dir is not None
        target = self._state_path(job_id)
        temporary = self._state_dir / f".{job_id}.{uuid.uuid4().hex}.tmp"
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > MAX_STATE_FILE_BYTES:
            raise ValueError("Job state exceeds the bounded state-file size.")

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            if os.name != "nt":
                target.chmod(0o600)
        finally:
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)

    def _load_state(self) -> None:
        assert self._state_dir is not None
        candidates: list[tuple[float, Path]] = []
        try:
            with os.scandir(self._state_dir) as iterator:
                for index, entry in enumerate(iterator):
                    if index >= MAX_STATE_FILES_TO_SCAN:
                        break
                    if not entry.name.endswith(".job.json"):
                        continue
                    job_id = entry.name.removesuffix(".job.json")
                    if not _JOB_ID_PATTERN.fullmatch(job_id):
                        continue
                    try:
                        metadata = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if (
                        is_redirecting_metadata(metadata)
                        or not stat.S_ISREG(metadata.st_mode)
                        or metadata.st_nlink != 1
                    ):
                        continue
                    candidates.append((metadata.st_mtime, Path(entry.path)))
        except OSError as exc:
            raise JobError("Job state directory cannot be scanned safely.") from exc

        candidates.sort(key=lambda item: item[0], reverse=True)
        for _, path in candidates[: self._history_limit]:
            record = self._read_record(path)
            if record is not None:
                self._jobs[record.job_id] = record

        recovered_at = _utc_now()
        for record in self._jobs.values():
            if record.status in _ACTIVE_STATUSES:
                record.status = "interrupted"
                record.finished_at = recovered_at
                record.termination_reason = "bridge_restart"
                record.error = "Bridge restarted before job supervision completed."
                try:
                    self._write_payload_sync(record.job_id, record.payload())
                except (OSError, ValueError, TypeError):
                    record.persistent = False

    def _read_record(self, path: Path) -> _JobRecord | None:
        try:
            metadata = os.lstat(path)
            if (
                is_redirecting_metadata(metadata)
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_size <= 0
                or metadata.st_size > MAX_STATE_FILE_BYTES
            ):
                return None
            raw = path.read_bytes()
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("schema_version") != JOB_SCHEMA_VERSION:
            return None

        try:
            job_id = payload["job_id"]
            project_id = payload["project_id"]
            executable = payload["executable"]
            cwd = payload["cwd"]
            status = payload["status"]
            created_at = payload["created_at"]
            argument_count = payload["argument_count"]
            stdout = payload.get("stdout", "")
            stderr = payload.get("stderr", "")
        except KeyError:
            return None

        if not isinstance(job_id, str) or not _JOB_ID_PATTERN.fullmatch(job_id):
            return None
        if path.name != f"{job_id}.job.json":
            return None
        if not all(isinstance(value, str) for value in (project_id, executable, cwd, created_at)):
            return None
        if status not in _ACTIVE_STATUSES | _TERMINAL_STATUSES:
            return None
        if type(argument_count) is not int or not 0 <= argument_count <= MAX_ARGUMENTS:
            return None
        if not isinstance(stdout, str) or not isinstance(stderr, str):
            return None

        optional_strings = ("started_at", "finished_at", "termination_reason", "error")
        for key in optional_strings:
            value = payload.get(key)
            if value is not None and not isinstance(value, str):
                return None
        exit_code = payload.get("exit_code")
        if exit_code is not None and type(exit_code) is not int:
            return None
        output_truncated = payload.get("output_truncated", False)
        if not isinstance(output_truncated, bool):
            return None

        return _JobRecord(
            job_id=job_id,
            project_id=project_id,
            executable=executable,
            cwd=cwd,
            status=status,
            created_at=created_at,
            argument_count=argument_count,
            started_at=payload.get("started_at"),
            finished_at=payload.get("finished_at"),
            exit_code=exit_code,
            termination_reason=payload.get("termination_reason"),
            output_truncated=output_truncated,
            stdout=stdout,
            stderr=stderr,
            error=payload.get("error"),
            persistent=True,
        )

    def _prune_history_sync(self) -> None:
        terminal = [record for record in self._jobs.values() if record.status in _TERMINAL_STATUSES]
        terminal.sort(key=lambda record: (record.created_at, record.job_id), reverse=True)
        for record in terminal[self._history_limit :]:
            self._jobs.pop(record.job_id, None)
            if self._state_dir is not None:
                with contextlib.suppress(OSError):
                    self._state_path(record.job_id).unlink(missing_ok=True)
