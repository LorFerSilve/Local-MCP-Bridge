"""Hardened subprocess runner used only by Phase 7 Git synchronization tools."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from local_mcp_bridge.security.paths import file_identity, is_redirecting_metadata

_STREAM_CHUNK_BYTES = 65_536
_TERMINATION_GRACE_SECONDS = 5
_WINDOWS_GIT_NAMES = ("git.exe", "git.com", "git")
_SAFE_ENVIRONMENT_KEYS = (
    "LANG",
    "LC_ALL",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "WINDIR",
)


class GitRunnerError(RuntimeError):
    """Raised when Git cannot be resolved or a bounded invocation fails."""


@dataclass(frozen=True, slots=True)
class GitCommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass(slots=True)
class _Capture:
    remaining: int
    stdout: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)
    limit_reached: asyncio.Event = field(default_factory=asyncio.Event)


def _is_inside(root: Path, candidate: Path) -> bool:
    return candidate == root or root in candidate.parents


def _safe_path_entries(project_root: Path) -> list[str]:
    entries: list[str] = []
    seen: set[str] = set()
    for raw in os.environ.get("PATH", "").split(os.pathsep):
        raw = raw.strip().strip('"')
        if not raw:
            continue
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            continue
        try:
            metadata = os.lstat(candidate)
            if is_redirecting_metadata(metadata):
                continue
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if not resolved.is_dir() or _is_inside(project_root, resolved):
            continue
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        seen.add(key)
        entries.append(str(resolved))
    return entries


def _validate_git_binary(path: Path) -> os.stat_result:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise GitRunnerError("Git executable cannot be safely inspected.") from exc
    if is_redirecting_metadata(metadata) or not stat.S_ISREG(metadata.st_mode):
        raise GitRunnerError("Git executable is not a safe regular file.")
    if os.name != "nt" and not os.access(path, os.X_OK):
        raise GitRunnerError("Git executable is not executable by the bridge user.")
    return metadata


def _resolve_git(project_root: Path) -> tuple[Path, os.stat_result, list[str]]:
    path_entries = _safe_path_entries(project_root)
    names = _WINDOWS_GIT_NAMES if os.name == "nt" else ("git",)
    for directory in path_entries:
        for name in names:
            candidate = Path(directory) / name
            try:
                resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if _is_inside(project_root, resolved):
                continue
            try:
                metadata = _validate_git_binary(resolved)
            except GitRunnerError:
                continue
            child_path = [str(resolved.parent), *path_entries]
            deduped: list[str] = []
            seen: set[str] = set()
            for entry in child_path:
                key = os.path.normcase(entry)
                if key not in seen:
                    seen.add(key)
                    deduped.append(entry)
            return resolved, metadata, deduped
    raise GitRunnerError("Git is unavailable on the constrained PATH.")


def _child_environment(path_entries: list[str]) -> dict[str, str]:
    environment: dict[str, str] = {}
    for key in _SAFE_ENVIRONMENT_KEYS:
        value = os.environ.get(key)
        if value is not None:
            environment[key] = value
    environment["PATH"] = os.pathsep.join(path_entries)
    if os.name == "nt":
        environment["PATHEXT"] = ".COM;.EXE"
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_SYSTEM"] = os.devnull
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_ALLOW_PROTOCOL"] = "https"
    environment["GIT_PROTOCOL_FROM_USER"] = "0"
    environment["GIT_PAGER"] = "cat"
    environment["PAGER"] = "cat"
    environment["GIT_MERGE_AUTOEDIT"] = "no"
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    return environment


async def _capture_stream(
    stream: asyncio.StreamReader | None,
    target: bytearray,
    capture: _Capture,
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


async def _terminate(process: asyncio.subprocess.Process) -> None:
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


async def _collect(process: asyncio.subprocess.Process, capture: _Capture) -> None:
    stdout_task = asyncio.create_task(_capture_stream(process.stdout, capture.stdout, capture))
    stderr_task = asyncio.create_task(_capture_stream(process.stderr, capture.stderr, capture))
    wait_task = asyncio.create_task(process.wait())
    limit_task = asyncio.create_task(capture.limit_reached.wait())
    tasks = (stdout_task, stderr_task, wait_task, limit_task)
    try:
        done, _ = await asyncio.wait((wait_task, limit_task), return_when=asyncio.FIRST_COMPLETED)
        if limit_task in done and capture.limit_reached.is_set():
            await _terminate(process)
            raise GitRunnerError("Git output exceeded the configured safety limit.")
        await asyncio.gather(stdout_task, stderr_task)
        if capture.limit_reached.is_set():
            await _terminate(process)
            raise GitRunnerError("Git output exceeded the configured safety limit.")
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class GitCommandRunner:
    """Resolve Git outside the project and execute a fixed argv under bounded policy."""

    _FIXED_CONFIG = (
        "-c",
        f"core.hooksPath={os.devnull}",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "credential.helper=",
        "-c",
        "core.askPass=",
        "-c",
        "submodule.recurse=false",
        "-c",
        "fetch.recurseSubmodules=false",
        "-c",
        "gc.auto=0",
        "-c",
        "maintenance.auto=false",
    )

    async def run(
        self,
        project_root: Path,
        args: list[str],
        *,
        timeout_seconds: int,
        max_output_bytes: int,
        allowed_exit_codes: tuple[int, ...] = (0,),
    ) -> GitCommandResult:
        git_path, metadata, path_entries = _resolve_git(project_root)
        expected_identity = file_identity(metadata)
        current = _validate_git_binary(git_path)
        if file_identity(current) != expected_identity:
            raise GitRunnerError("Git executable identity changed during authorization.")

        capture = _Capture(remaining=max_output_bytes)
        try:
            process = await asyncio.create_subprocess_exec(
                str(git_path),
                *self._FIXED_CONFIG,
                *args,
                cwd=str(project_root),
                env=_child_environment(path_entries),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=os.name == "posix",
                creationflags=(
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
                ),
            )
        except (OSError, ValueError) as exc:
            raise GitRunnerError("Git could not be started under the constrained policy.") from exc

        try:
            try:
                await asyncio.wait_for(_collect(process, capture), timeout=timeout_seconds)
            except TimeoutError as exc:
                await _terminate(process)
                raise GitRunnerError("Git operation exceeded the configured timeout.") from exc
            except asyncio.CancelledError:
                await _terminate(process)
                raise
        finally:
            if process.returncode is None:
                await _terminate(process)

        returncode = process.returncode if process.returncode is not None else -1
        if returncode not in allowed_exit_codes:
            raise GitRunnerError("Git operation failed under the configured policy.")
        return GitCommandResult(
            returncode=returncode,
            stdout=bytes(capture.stdout),
            stderr=bytes(capture.stderr),
        )
