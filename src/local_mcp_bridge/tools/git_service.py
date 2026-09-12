"""Dedicated non-destructive Git synchronization service for Phase 7."""

from __future__ import annotations

import asyncio

from typing_extensions import TypedDict

from local_mcp_bridge.registry import ProjectRegistry, RegistryError
from local_mcp_bridge.tools.git_repository import GitRepository, GitRepositoryError
from local_mcp_bridge.tools.git_runner import GitCommandRunner


class GitError(ValueError):
    """Raised when a Git request is unauthorized or cannot be completed safely."""


class GitStatusResult(TypedDict):
    project_id: str
    branch: str | None
    configured_branch: str
    head: str
    clean: bool
    staged: int
    unstaged: int
    untracked: int
    remote_tracking_present: bool
    ahead: int | None
    behind: int | None


class GitFetchResult(TypedDict):
    project_id: str
    remote: str
    branch: str
    previous_remote_head: str | None
    remote_head: str
    changed: bool


class GitSyncResult(TypedDict):
    project_id: str
    branch: str
    previous_head: str
    head: str
    updated: bool
    remote_head: str


class GitService:
    """Expose only status, trusted fetch, and clean fast-forward synchronization."""

    def __init__(
        self,
        registry: ProjectRegistry,
        runner: GitCommandRunner | None = None,
    ) -> None:
        self._registry = registry
        self._runner = runner or GitCommandRunner()
        self._locks: dict[str, asyncio.Lock] = {}

    def _require_repository(self, project_id: str) -> GitRepository:
        try:
            project = self._registry.require(project_id)
        except RegistryError as exc:
            raise GitError("Unknown project ID.") from exc
        if not project.permissions.git or project.git is None:
            raise GitError("Git synchronization is not enabled for this project.")
        return GitRepository(project, project.git, self._runner)

    def _lock(self, project_id: str) -> asyncio.Lock:
        return self._locks.setdefault(project_id, asyncio.Lock())

    @staticmethod
    def _clean(counts: tuple[int, int, int]) -> bool:
        return sum(counts) == 0

    async def _status_locked(self, repository: GitRepository) -> GitStatusResult:
        branch = await repository.current_branch()
        head = await repository.rev_parse("HEAD")
        if head is None:
            raise GitError("Repository has no valid HEAD commit.")
        staged, unstaged, untracked = await repository.worktree_counts()
        remote_head = await repository.rev_parse(repository.remote_ref, optional=True)
        ahead: int | None = None
        behind: int | None = None
        if remote_head is not None:
            counts = await repository.run(
                ["rev-list", "--left-right", "--count", f"HEAD...{repository.remote_ref}"]
            )
            parts = counts.stdout.decode("ascii", errors="replace").split()
            if len(parts) != 2 or not all(part.isdigit() for part in parts):
                raise GitError("Git returned invalid ahead/behind metadata.")
            ahead, behind = int(parts[0]), int(parts[1])
        return GitStatusResult(
            project_id=repository.project.project_id,
            branch=branch,
            configured_branch=repository.settings.branch,
            head=head,
            clean=(staged + unstaged + untracked) == 0,
            staged=staged,
            unstaged=unstaged,
            untracked=untracked,
            remote_tracking_present=remote_head is not None,
            ahead=ahead,
            behind=behind,
        )

    async def git_status(self, project_id: str) -> GitStatusResult:
        repository = self._require_repository(project_id)
        lock = self._lock(project_id)
        if lock.locked():
            raise GitError("A Git operation is already active for this project.")
        async with lock:
            try:
                await repository.validate()
                return await self._status_locked(repository)
            except GitRepositoryError as exc:
                raise GitError(str(exc)) from exc

    async def _fetch_locked(self, repository: GitRepository) -> GitFetchResult:
        previous = await repository.rev_parse(repository.remote_ref, optional=True)
        refspec = (
            f"refs/heads/{repository.settings.branch}:"
            f"refs/remotes/{repository.settings.remote}/{repository.settings.branch}"
        )
        await repository.run(
            [
                "fetch",
                "--no-tags",
                "--no-recurse-submodules",
                "--no-write-fetch-head",
                repository.settings.remote_url,
                refspec,
            ]
        )
        current = await repository.rev_parse(repository.remote_ref)
        if current is None:
            raise GitError("Fetch completed without the configured remote branch.")
        return GitFetchResult(
            project_id=repository.project.project_id,
            remote=repository.settings.remote,
            branch=repository.settings.branch,
            previous_remote_head=previous,
            remote_head=current,
            changed=previous != current,
        )

    async def git_fetch(self, project_id: str) -> GitFetchResult:
        repository = self._require_repository(project_id)
        lock = self._lock(project_id)
        if lock.locked():
            raise GitError("A Git operation is already active for this project.")
        async with lock:
            try:
                await repository.validate()
                result = await self._fetch_locked(repository)
                await repository.validate()
                return result
            except GitRepositoryError as exc:
                raise GitError(str(exc)) from exc

    async def _require_sync_preconditions(self, repository: GitRepository) -> None:
        if await repository.current_branch() != repository.settings.branch:
            raise GitError("Fast-forward sync requires the configured branch to be checked out.")
        if not self._clean(await repository.worktree_counts()):
            raise GitError("Fast-forward sync refuses a dirty working tree.")

    async def git_sync_fast_forward(self, project_id: str) -> GitSyncResult:
        repository = self._require_repository(project_id)
        lock = self._lock(project_id)
        if lock.locked():
            raise GitError("A Git operation is already active for this project.")
        async with lock:
            try:
                await repository.validate()
                await self._require_sync_preconditions(repository)
                fetched = await self._fetch_locked(repository)
                await repository.validate()
                await self._require_sync_preconditions(repository)

                previous = await repository.rev_parse("HEAD")
                if previous is None:
                    raise GitError("Repository has no valid HEAD commit.")
                target = fetched["remote_head"]
                if previous == target:
                    return GitSyncResult(
                        project_id=repository.project.project_id,
                        branch=repository.settings.branch,
                        previous_head=previous,
                        head=previous,
                        updated=False,
                        remote_head=target,
                    )

                ancestor = await repository.run(
                    ["merge-base", "--is-ancestor", "HEAD", repository.remote_ref],
                    allowed_exit_codes=(0, 1),
                )
                if ancestor.returncode != 0:
                    raise GitError(
                        "Remote branch is not a fast-forward of local HEAD; "
                        "Phase 7 will not merge, rewind, or rewrite history."
                    )

                await repository.run(
                    [
                        "merge",
                        "--ff-only",
                        "--no-edit",
                        "--no-stat",
                        "--no-overwrite-ignore",
                        repository.remote_ref,
                    ]
                )
                current = await repository.rev_parse("HEAD")
                if current != target:
                    raise GitError("Fast-forward sync did not reach the verified remote head.")
                return GitSyncResult(
                    project_id=repository.project.project_id,
                    branch=repository.settings.branch,
                    previous_head=previous,
                    head=current,
                    updated=True,
                    remote_head=target,
                )
            except GitRepositoryError as exc:
                raise GitError(str(exc)) from exc
