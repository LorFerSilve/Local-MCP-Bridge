"""Project registry, permissions, and execution policy metadata.

The registry is the security boundary between MCP-facing project identifiers and
host-specific filesystem roots/executable paths. Absolute host paths are never
exposed through public project metadata.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from typing_extensions import TypedDict

PROJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
EXECUTABLE_ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
ABSOLUTE_MAX_TIMEOUT_SECONDS = 300
ABSOLUTE_MAX_OUTPUT_BYTES = 1_048_576
ABSOLUTE_MAX_CONCURRENT_JOBS = 4


class RegistryError(ValueError):
    """Raised when a project registry violates a security invariant."""


class PublicPermissions(TypedDict):
    """Permissions that may safely be exposed to an MCP client."""

    read: bool
    search: bool
    execute: bool
    git: bool


class PublicProject(TypedDict):
    """Project metadata safe for an MCP client."""

    id: str
    permissions: PublicPermissions
    allowed_executables: list[str]


@dataclass(frozen=True, slots=True)
class ProjectPermissions:
    """Capability flags attached to one authorized project."""

    read: bool = False
    search: bool = False
    execute: bool = False
    git: bool = False

    def as_public(self) -> PublicPermissions:
        """Return an MCP-safe permission representation."""
        return PublicPermissions(
            read=self.read,
            search=self.search,
            execute=self.execute,
            git=self.git,
        )


@dataclass(frozen=True, slots=True)
class ExecutableRule:
    """One locally configured executable alias.

    ``executable`` is either a simple command name resolved through a constrained
    PATH or a canonical absolute path when ``pinned`` is true. Only ``alias`` is
    ever exposed to MCP clients.
    """

    alias: str
    executable: str
    pinned: bool = False

    def __post_init__(self) -> None:
        if not EXECUTABLE_ALIAS_PATTERN.fullmatch(self.alias):
            raise RegistryError(
                "Executable aliases must contain only letters, digits, '.', '_', '+', or '-' "
                "and be at most 64 characters."
            )
        if not self.executable or "\x00" in self.executable:
            raise RegistryError("Executable targets must be non-empty strings.")

        if self.pinned:
            if not Path(self.executable).is_absolute():
                raise RegistryError("Pinned executable targets must be absolute paths.")
        else:
            windows = PureWindowsPath(self.executable)
            if (
                windows.drive
                or windows.is_absolute()
                or "/" in self.executable
                or "\\" in self.executable
                or any(character.isspace() for character in self.executable)
            ):
                raise RegistryError(
                    "Unpinned executable targets must be simple command names without paths."
                )


@dataclass(frozen=True, slots=True)
class ExecutionSettings:
    """Bounded synchronous execution policy for one project."""

    default_timeout_seconds: int = 60
    max_timeout_seconds: int = ABSOLUTE_MAX_TIMEOUT_SECONDS
    max_output_bytes: int = 262_144
    max_concurrent_jobs: int = 1

    def __post_init__(self) -> None:
        values = (
            self.default_timeout_seconds,
            self.max_timeout_seconds,
            self.max_output_bytes,
            self.max_concurrent_jobs,
        )
        if any(type(value) is not int for value in values):
            raise RegistryError("Execution limits must be integers.")
        if not 1 <= self.default_timeout_seconds <= self.max_timeout_seconds:
            raise RegistryError("Execution default timeout must be between 1 and max timeout.")
        if not 1 <= self.max_timeout_seconds <= ABSOLUTE_MAX_TIMEOUT_SECONDS:
            raise RegistryError(
                f"Execution max timeout may not exceed {ABSOLUTE_MAX_TIMEOUT_SECONDS} seconds."
            )
        if not 4_096 <= self.max_output_bytes <= ABSOLUTE_MAX_OUTPUT_BYTES:
            raise RegistryError(
                "Execution output limit must be between 4096 bytes and the hard 1 MiB ceiling."
            )
        if not 1 <= self.max_concurrent_jobs <= ABSOLUTE_MAX_CONCURRENT_JOBS:
            raise RegistryError(
                f"Execution concurrency must be between 1 and {ABSOLUTE_MAX_CONCURRENT_JOBS}."
            )


@dataclass(frozen=True, slots=True)
class ProjectRecord:
    """Internal project record containing canonical host-only configuration."""

    project_id: str
    root: Path
    permissions: ProjectPermissions
    allowed_executables: tuple[ExecutableRule, ...] = ()
    execution: ExecutionSettings = field(default_factory=ExecutionSettings)

    def as_public(self) -> PublicProject:
        """Return metadata without leaking local filesystem/executable paths."""
        return PublicProject(
            id=self.project_id,
            permissions=self.permissions.as_public(),
            allowed_executables=sorted(rule.alias for rule in self.allowed_executables),
        )

    def require_executable(self, alias: str) -> ExecutableRule:
        """Return an allowlisted executable rule without exposing alternative targets."""
        matches = [rule for rule in self.allowed_executables if rule.alias == alias]
        if not matches:
            raise RegistryError("Executable is not allowlisted for this project.")
        return matches[0]


def _roots_overlap(first: str, second: str) -> bool:
    """Return whether either normalized root contains the other."""
    try:
        common = os.path.commonpath((first, second))
    except ValueError:
        return False
    return common in (first, second)


class ProjectRegistry:
    """Immutable lookup boundary for explicitly authorized project roots."""

    def __init__(self, projects: Iterable[ProjectRecord] = ()) -> None:
        by_id: dict[str, ProjectRecord] = {}
        roots: list[str] = []

        for project in projects:
            if not PROJECT_ID_PATTERN.fullmatch(project.project_id):
                raise RegistryError(
                    "Project IDs must start with a lowercase letter and contain only "
                    "lowercase letters, digits, or hyphens (maximum 64 characters)."
                )
            if project.project_id in by_id:
                raise RegistryError(f"Duplicate project ID: {project.project_id}")

            root_key = os.path.normcase(str(project.root))
            if any(_roots_overlap(root_key, existing) for existing in roots):
                raise RegistryError(
                    "Project roots may not be identical, nested, or otherwise overlap."
                )

            aliases: set[str] = set()
            for rule in project.allowed_executables:
                alias_key = rule.alias.casefold() if os.name == "nt" else rule.alias
                if alias_key in aliases:
                    raise RegistryError(
                        f"Duplicate executable alias for project {project.project_id!r}."
                    )
                aliases.add(alias_key)

            by_id[project.project_id] = project
            roots.append(root_key)

        self._projects = by_id

    @classmethod
    def empty(cls) -> ProjectRegistry:
        """Return a fail-closed registry with no authorized roots."""
        return cls()

    def __len__(self) -> int:
        return len(self._projects)

    def list_public(self) -> list[PublicProject]:
        """Return stable MCP-safe project metadata ordered by project ID."""
        return [self._projects[key].as_public() for key in sorted(self._projects)]

    def get_public(self, project_id: str) -> PublicProject | None:
        """Return MCP-safe metadata for one project, if it exists."""
        project = self._projects.get(project_id)
        return project.as_public() if project is not None else None

    def require(self, project_id: str) -> ProjectRecord:
        """Return an internal project record or fail without revealing other projects."""
        try:
            return self._projects[project_id]
        except KeyError as exc:
            raise RegistryError("Unknown project ID.") from exc
