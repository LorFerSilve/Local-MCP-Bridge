"""Controlled one-shot process execution for explicitly authorized projects.

Phase 5 intentionally exposes no shell-string primitive. A caller selects a local
project, an allowlisted executable alias, an argv vector, and an optional confined
working directory. Child output, runtime, environment inheritance, and concurrency
are bounded locally.

This is not a kernel sandbox: an executable that runs project code can still use the
OS privileges of the bridge process. ``execute=true`` is therefore a high-trust,
explicit opt-in and should only be granted to projects whose code is trusted to run.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import signal
import stat
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

from typing_extensions import TypedDict

from local_mcp_bridge.registry import (
    ExecutableRule,
    ProjectRecord,
    ProjectRegistry,
    RegistryError,
)
from local_mcp_bridge.security.paths import (
    PathConfinementError,
    PathGuard,
    file_identity,
    is_redirecting_metadata,
    normalize_relative_path,
)

MAX_ARGUMENTS = 64
MAX_ARGUMENT_CHARS = 4_096
MAX_TOTAL_ARGUMENT_CHARS = 16_384
_STREAM_CHUNK_BYTES = 65_536
_TERMINATION_GRACE_SECONDS = 5

_SHELL_NAMES = {
    "bash",
    "bash.exe",
    "cmd",
    "cmd.exe",
    "dash",
    "fish",
    "ksh",
    "powershell",
    "powershell.exe",
    "pwsh",
    "pwsh.exe",
    "sh",
    "sh.exe",
    "wsl",
    "wsl.exe",
    "zsh",
}
_SHELL_SCRIPT_SUFFIXES = {".bat", ".cmd", ".ps1", ".psm1"}
_SAFE_ENVIRONMENT_KEYS = (
    "LANG",
    "LC_ALL",
    "PATHEXT",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "WINDIR",
)


class ExecutionError(ValueError):
    """Raised when an execution request is invalid or not authorized."""


class ProcessResult(TypedDict):
    """Bounded result for one completed/terminated process invocation."""

    project_id: str
    executable: str
    cwd: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    termination_reason: Literal["exited", "timeout", "output_limit"]
    output_truncated: bool


@dataclass(slots=True)
class _OutputCapture:
    remaining: int
    stdout: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)
    limit_reached: asyncio.Event = field(default_factory=asyncio.Event)


class ExecutionService:
    """Policy-enforced synchronous-style execution over async subprocesses."""

    def __init__(self, registry: ProjectRegistry) -> None:
        self._registry = registry
        self._semaphores: dict[str, asyncio.Semaphore] = {}

    def _require_project(self, project_id: str) -> ProjectRecord:
        try:
            project = self._registry.require(project_id)
        except RegistryError as exc:
            raise ExecutionError("Unknown project ID.") from exc
        if not project.permissions.execute:
            raise ExecutionError("Process execution is not enabled for this project.")
        return project

    @staticmethod
    def _validate_arguments(args: list[str] | None) -> list[str]:
        if args is None:
            return []
        if not isinstance(args, list):
            raise ExecutionError("Process arguments must be a list of strings.")
        if len(args) > MAX_ARGUMENTS:
            raise ExecutionError(f"At most {MAX_ARGUMENTS} process arguments are allowed.")

        total = 0
        validated: list[str] = []
        for argument in args:
            if not isinstance(argument, str):
                raise ExecutionError("Process arguments must all be strings.")
            if "\x00" in argument:
                raise ExecutionError("Process arguments may not contain NUL characters.")
            if any(ord(character) < 32 and character not in ("\t",) for character in argument):
                raise ExecutionError("Process arguments contain unsupported control characters.")
            if len(argument) > MAX_ARGUMENT_CHARS:
                raise ExecutionError(
                    f"Individual process arguments may not exceed {MAX_ARGUMENT_CHARS} characters."
                )
            total += len(argument)
            if total > MAX_TOTAL_ARGUMENT_CHARS:
                raise ExecutionError(
                    "Combined process arguments may not exceed "
                    f"{MAX_TOTAL_ARGUMENT_CHARS} characters."
                )
            validated.append(argument)
        return validated

    @staticmethod
    def _relative_text(path: PurePosixPath) -> str:
        return "." if path.parts == (".",) else path.as_posix()

    @staticmethod
    def _is_inside(root: Path, candidate: Path) -> bool:
        return candidate == root or root in candidate.parents

    @staticmethod
    def _reject_shell_target(path: Path) -> None:
        name = path.name.casefold()
        suffix = path.suffix.casefold()
        if name in _SHELL_NAMES or suffix in _SHELL_SCRIPT_SUFFIXES:
            raise ExecutionError("Shell executables and shell scripts are unavailable in Phase 5.")

    @staticmethod
    def _validate_executable_file(path: Path) -> os.stat_result:
        try:
            metadata = os.lstat(path)
        except OSError as exc:
            raise ExecutionError("Allowlisted executable cannot be safely inspected.") from exc

        if is_redirecting_metadata(metadata) or not stat.S_ISREG(metadata.st_mode):
            raise ExecutionError("Allowlisted executable is not a safe regular file.")
        if os.name != "nt" and not os.access(path, os.X_OK):
            raise ExecutionError("Allowlisted executable is not executable by the bridge user.")
        return metadata

    @classmethod
    def _safe_path_entries(cls, project_root: Path) -> list[str]:
        entries: list[str] = []
        seen: set[str] = set()

        for raw_entry in os.environ.get("PATH", "").split(os.pathsep):
            raw_entry = raw_entry.strip().strip('"')
            if not raw_entry:
                continue
            candidate = Path(raw_entry).expanduser()
            if not candidate.is_absolute():
                continue
            try:
                metadata = os.lstat(candidate)
                if is_redirecting_metadata(metadata):
                    continue
                resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if not resolved.is_dir() or cls._is_inside(project_root, resolved):
                continue

            key = os.path.normcase(str(resolved))
            if key in seen:
                continue
            seen.add(key)
            entries.append(str(resolved))

        return entries

    @classmethod
    def _resolve_executable(
        cls,
        project: ProjectRecord,
        rule: ExecutableRule,
    ) -> tuple[Path, os.stat_result, list[str]]:
        if rule.alias.casefold() in _SHELL_NAMES:
            raise ExecutionError("Shell executables are not available through Phase 5.")

        safe_path_entries = cls._safe_path_entries(project.root)

        if rule.pinned:
            candidate = Path(rule.executable)
            try:
                resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise ExecutionError("Pinned executable cannot be resolved.") from exc
            cls._reject_shell_target(resolved)
            metadata = cls._validate_executable_file(resolved)
        else:
            safe_path = os.pathsep.join(safe_path_entries)
            resolved_text = shutil.which(rule.executable, path=safe_path)
            if resolved_text is None:
                raise ExecutionError(
                    "Allowlisted executable is unavailable on the constrained PATH."
                )
            try:
                resolved = Path(resolved_text).resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise ExecutionError("Allowlisted executable cannot be resolved.") from exc
            if cls._is_inside(project.root, resolved):
                raise ExecutionError(
                    "PATH-resolved executables may not originate from inside the project root."
                )
            cls._reject_shell_target(resolved)
            metadata = cls._validate_executable_file(resolved)

        child_path_entries = [str(resolved.parent), *safe_path_entries]
        deduplicated: list[str] = []
        seen: set[str] = set()
        for entry in child_path_entries:
            key = os.path.normcase(entry)
            if key not in seen:
                seen.add(key)
                deduplicated.append(entry)

        return resolved, metadata, deduplicated

    @staticmethod
    def _build_child_environment(path_entries: list[str]) -> dict[str, str]:
        environment: dict[str, str] = {}
        for key in _SAFE_ENVIRONMENT_KEYS:
            value = os.environ.get(key)
            if value is not None:
                environment[key] = value

        environment["PATH"] = os.pathsep.join(path_entries)
        environment["PYTHONNOUSERSITE"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONUTF8"] = "1"
        return environment

    @staticmethod
    def _validate_timeout(project: ProjectRecord, timeout_seconds: int | None) -> int:
        if timeout_seconds is None:
            return project.execution.default_timeout_seconds
        if type(timeout_seconds) is not int:
            raise ExecutionError("timeout_seconds must be an integer.")
        if not 1 <= timeout_seconds <= project.execution.max_timeout_seconds:
            raise ExecutionError(
                "timeout_seconds must be between 1 and the project's configured maximum."
            )
        return timeout_seconds

    @staticmethod
    async def _capture_stream(
        stream: asyncio.StreamReader | None,
        target: bytearray,
        capture: _OutputCapture,
    ) -> None:
        if stream is None:
            return

        while True:
            chunk = await stream.read(_STREAM_CHUNK_BYTES)
            if not chunk:
                return

            remaining = capture.remaining
            if remaining <= 0:
                capture.limit_reached.set()
                return

            if len(chunk) > remaining:
                target.extend(chunk[:remaining])
                capture.remaining = 0
                capture.limit_reached.set()
                return

            target.extend(chunk)
            capture.remaining -= len(chunk)

    @staticmethod
    async def _terminate_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return

        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except (ProcessLookupError, OSError):
            pass

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(process.wait(), timeout=_TERMINATION_GRACE_SECONDS)

    async def _collect_process(
        self,
        process: asyncio.subprocess.Process,
        capture: _OutputCapture,
    ) -> Literal["exited", "output_limit"]:
        stdout_task = asyncio.create_task(
            self._capture_stream(process.stdout, capture.stdout, capture)
        )
        stderr_task = asyncio.create_task(
            self._capture_stream(process.stderr, capture.stderr, capture)
        )
        wait_task = asyncio.create_task(process.wait())
        limit_task = asyncio.create_task(capture.limit_reached.wait())
        tasks = (stdout_task, stderr_task, wait_task, limit_task)

        try:
            done, _ = await asyncio.wait(
                (wait_task, limit_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if limit_task in done and capture.limit_reached.is_set():
                await self._terminate_process(process)
                reason: Literal["exited", "output_limit"] = "output_limit"
            else:
                reason = "exited"

            await asyncio.gather(stdout_task, stderr_task)
            if capture.limit_reached.is_set():
                reason = "output_limit"
            return reason
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def _sanitize_output(data: bytes) -> str:
        text = data.decode("utf-8", errors="replace")
        sanitized: list[str] = []
        for character in text:
            codepoint = ord(character)
            if character in ("\n", "\r", "\t") or (
                codepoint >= 32 and not 127 <= codepoint <= 159
            ):
                sanitized.append(character)
            else:
                sanitized.append(f"\\x{codepoint:02x}")
        return "".join(sanitized)

    @staticmethod
    def _redact_project_root(text: str, root: Path) -> str:
        variants = {
            str(root),
            root.as_posix(),
            str(root).replace("\\", "/"),
            str(root).replace("/", "\\"),
        }
        variants.discard("")
        for variant in sorted(variants, key=len, reverse=True):
            if os.name == "nt":
                text = re.sub(re.escape(variant), "<project-root>", text, flags=re.IGNORECASE)
            else:
                text = text.replace(variant, "<project-root>")
        return text

    async def run_process(
        self,
        project_id: str,
        executable: str,
        args: list[str] | None = None,
        cwd: str = ".",
        timeout_seconds: int | None = None,
    ) -> ProcessResult:
        """Run one allowlisted process with bounded argv, cwd, env, time, and output."""
        project = self._require_project(project_id)
        if not isinstance(executable, str) or not executable:
            raise ExecutionError("Executable alias must be a non-empty string.")

        try:
            rule = project.require_executable(executable)
        except RegistryError as exc:
            raise ExecutionError("Executable is not allowlisted for this project.") from exc

        validated_args = self._validate_arguments(args)
        timeout = self._validate_timeout(project, timeout_seconds)

        try:
            relative_cwd = normalize_relative_path(cwd)
            guard = PathGuard(project.root)
            resolved_cwd = guard.resolve_existing(relative_cwd, expected="directory")
        except PathConfinementError as exc:
            raise ExecutionError(
                "Working directory is not safely confined to the project."
            ) from exc

        resolved_executable, executable_metadata, child_path = self._resolve_executable(
            project,
            rule,
        )
        expected_identity = file_identity(executable_metadata)

        current_metadata = self._validate_executable_file(resolved_executable)
        if file_identity(current_metadata) != expected_identity:
            raise ExecutionError("Executable identity changed during authorization.")

        environment = self._build_child_environment(child_path)
        semaphore = self._semaphores.setdefault(
            project_id,
            asyncio.Semaphore(project.execution.max_concurrent_jobs),
        )
        capture = _OutputCapture(remaining=project.execution.max_output_bytes)

        async with semaphore:
            started = time.monotonic()
            try:
                process = await asyncio.create_subprocess_exec(
                    str(resolved_executable),
                    *validated_args,
                    cwd=str(resolved_cwd),
                    env=environment,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=os.name == "posix",
                    creationflags=(
                        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                        if os.name == "nt"
                        else 0
                    ),
                )
            except (OSError, ValueError) as exc:
                raise ExecutionError("Allowlisted executable could not be started.") from exc

            try:
                try:
                    reason = await asyncio.wait_for(
                        self._collect_process(process, capture),
                        timeout=timeout,
                    )
                except TimeoutError:
                    await self._terminate_process(process)
                    reason = "timeout"
                except asyncio.CancelledError:
                    await self._terminate_process(process)
                    raise
            finally:
                duration_ms = max(0, round((time.monotonic() - started) * 1000))

        stdout = self._redact_project_root(
            self._sanitize_output(bytes(capture.stdout)),
            project.root,
        )
        stderr = self._redact_project_root(
            self._sanitize_output(bytes(capture.stderr)),
            project.root,
        )
        return ProcessResult(
            project_id=project_id,
            executable=rule.alias,
            cwd=self._relative_text(relative_cwd),
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            termination_reason=reason,
            output_truncated=reason == "output_limit",
        )
