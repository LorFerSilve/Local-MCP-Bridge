"""Project registry and public project metadata.

The registry is the security boundary between MCP-facing project identifiers and
host-specific filesystem roots. Absolute roots are intentionally never exposed
through public project metadata.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from typing_extensions import TypedDict

PROJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


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
class ProjectRecord:
    """Internal project record containing a canonical host filesystem root."""

    project_id: str
    root: Path
    permissions: ProjectPermissions

    def as_public(self) -> PublicProject:
        """Return metadata without leaking the local filesystem root."""
        return PublicProject(id=self.project_id, permissions=self.permissions.as_public())


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
