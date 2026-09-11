"""Repository validation helpers for Phase 7 Git synchronization."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from local_mcp_bridge.registry import GitSettings, ProjectRecord
from local_mcp_bridge.security.paths import is_redirecting_metadata
from local_mcp_bridge.tools.git_runner import GitCommandResult, GitCommandRunner, GitRunnerError

_HASH_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")
_DANGEROUS_PREFIXES = (
    "alias.",
    "credential.",
    "filter.",
    "hook.",
    "include.",
    "includeif.",
    "submodule.",
    "url.",
)
_DANGEROUS_EXACT = {
    "core.alternaterefscommand",
    "core.attributesfile",
    "core.excludesfile",
    "core.fsmonitor",
    "core.gitproxy",
    "core.hookspath",
    "core.sshcommand",
    "diff.external",
    "interactive.difffilter",
}


class GitRepositoryError(RuntimeError):
    """Raised when repository state cannot be trusted for Phase 7."""


class GitRepository:
    """Bind trusted Git policy to one authorized working tree."""

    def __init__(
        self,
        project: ProjectRecord,
        settings: GitSettings,
        runner: GitCommandRunner,
    ) -> None:
        self.project = project
        self.settings = settings
        self.runner = runner

    @property
    def remote_ref(self) -> str:
        return f"refs/remotes/{self.settings.remote}/{self.settings.branch}"

    async def run(
        self,
        args: list[str],
        *,
        allowed_exit_codes: tuple[int, ...] = (0,),
    ) -> GitCommandResult:
        try:
            return await self.runner.run(
                self.project.root,
                args,
                timeout_seconds=self.settings.timeout_seconds,
                max_output_bytes=self.settings.max_output_bytes,
                allowed_exit_codes=allowed_exit_codes,
            )
        except GitRunnerError as exc:
            raise GitRepositoryError(str(exc)) from exc

    @staticmethod
    def _safe_metadata_file(path: Path, *, optional: bool = False) -> None:
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            if optional:
                return
            raise GitRepositoryError("Required Git metadata is missing.") from None
        except OSError as exc:
            raise GitRepositoryError("Git metadata cannot be safely inspected.") from exc
        if (
            is_redirecting_metadata(metadata)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink > 1
        ):
            raise GitRepositoryError(
                "Git metadata contains a redirecting, hard-linked, or non-regular file."
            )

    @staticmethod
    def _safe_metadata_directory(path: Path) -> None:
        try:
            metadata = os.lstat(path)
        except OSError as exc:
            raise GitRepositoryError("Required Git metadata directory is unavailable.") from exc
        if is_redirecting_metadata(metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise GitRepositoryError("Git metadata contains a redirecting directory.")

    def _validate_metadata_layout(self) -> None:
        dot_git = self.project.root / ".git"
        self._safe_metadata_directory(dot_git)
        self._safe_metadata_directory(dot_git / "objects")
        self._safe_metadata_directory(dot_git / "refs")
        self._safe_metadata_file(dot_git / "HEAD")
        self._safe_metadata_file(dot_git / "config")
        self._safe_metadata_file(dot_git / "index", optional=True)

        alternates = dot_git / "objects" / "info" / "alternates"
        try:
            os.lstat(alternates)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise GitRepositoryError("Git object alternates cannot be safely inspected.") from exc
        else:
            raise GitRepositoryError("External Git object alternates are not supported.")

    @staticmethod
    def _config_key_is_dangerous(key: str) -> bool:
        normalized = key.casefold()
        if normalized in _DANGEROUS_EXACT:
            return True
        if normalized.startswith(_DANGEROUS_PREFIXES):
            return True
        if normalized.startswith(("merge.", "http.", "protocol.")):
            return True
        return normalized.startswith("remote.") and normalized.endswith(
            (".proxy", ".uploadpack", ".receivepack", ".vcs")
        )

    async def _validate_local_config(self) -> None:
        result = await self.run(
            ["config", "--local", "--no-includes", "--name-only", "--list"]
        )
        try:
            keys = result.stdout.decode("utf-8").splitlines()
        except UnicodeError as exc:
            raise GitRepositoryError("Repository-local Git config is not valid UTF-8.") from exc
        if any(self._config_key_is_dangerous(key.strip()) for key in keys if key.strip()):
            raise GitRepositoryError(
                "Repository-local Git config contains a disallowed Phase 7 capability."
            )

        remote_key = f"remote.{self.settings.remote}.url"
        remote = await self.run(
            ["config", "--local", "--no-includes", "--get-all", remote_key],
            allowed_exit_codes=(0, 1),
        )
        if remote.returncode != 0:
            raise GitRepositoryError("Configured Git remote is missing from the repository.")
        urls = [line for line in remote.stdout.decode("utf-8", errors="replace").splitlines() if line]
        if urls != [self.settings.remote_url]:
            raise GitRepositoryError(
                "Repository remote URL does not match the trusted Phase 7 policy."
            )

    async def validate(self) -> None:
        """Verify local Git metadata, exact working-tree root, and local config."""
        self._validate_metadata_layout()
        top = await self.run(["rev-parse", "--show-toplevel"])
        try:
            reported = Path(top.stdout.decode("utf-8").strip()).resolve(strict=True)
            expected = self.project.root.resolve(strict=True)
        except (UnicodeError, OSError, RuntimeError) as exc:
            raise GitRepositoryError("Git working-tree root cannot be safely verified.") from exc
        if os.path.normcase(str(reported)) != os.path.normcase(str(expected)):
            raise GitRepositoryError(
                "Git working tree does not exactly match the authorized project root."
            )
        await self._validate_local_config()

    async def current_branch(self) -> str | None:
        result = await self.run(
            ["symbolic-ref", "--quiet", "--short", "HEAD"],
            allowed_exit_codes=(0, 1),
        )
        if result.returncode != 0:
            return None
        return result.stdout.decode("utf-8", errors="replace").strip() or None

    async def rev_parse(self, revision: str, *, optional: bool = False) -> str | None:
        result = await self.run(
            ["rev-parse", "--verify", "--quiet", revision],
            allowed_exit_codes=(0, 1) if optional else (0,),
        )
        if result.returncode != 0:
            return None
        value = result.stdout.decode("ascii", errors="replace").strip()
        if not _HASH_PATTERN.fullmatch(value):
            raise GitRepositoryError("Git returned an invalid object ID.")
        return value

    async def worktree_counts(self) -> tuple[int, int, int]:
        """Return staged, unstaged, and untracked counts without exposing paths."""
        result = await self.run(
            [
                "--no-optional-locks",
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=normal",
                "--ignore-submodules=all",
            ]
        )
        records = result.stdout.split(b"\x00")
        staged = 0
        unstaged = 0
        untracked = 0
        index = 0
        while index < len(records):
            record = records[index]
            index += 1
            if not record:
                continue
            if len(record) < 3:
                raise GitRepositoryError("Git status returned malformed porcelain output.")
            x = chr(record[0])
            y = chr(record[1])
            if x == "?" and y == "?":
                untracked += 1
            else:
                staged += int(x != " ")
                unstaged += int(y != " ")
            if x in {"R", "C"} or y in {"R", "C"}:
                if index < len(records):
                    index += 1
        return staged, unstaged, untracked
