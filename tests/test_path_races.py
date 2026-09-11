"""Race-focused regression tests for Phase 4 path confinement."""

from pathlib import Path

import pytest

from local_mcp_bridge.security.paths import (
    PathConfinementError,
    PathGuard,
    normalize_relative_path,
)


def test_directory_replacement_during_enumeration_is_detected(tmp_path: Path) -> None:
    """A directory swapped before post-enumeration verification must be rejected."""
    directory = tmp_path / "tree"
    displaced = tmp_path / "tree-old"
    directory.mkdir()
    (directory / "safe.txt").write_text("safe", encoding="utf-8")

    class SwappingGuard(PathGuard):
        def __init__(self, root: Path) -> None:
            super().__init__(root)
            self.resolve_calls = 0

        def resolve_existing(self, relative_path, *, expected):  # type: ignore[no-untyped-def]
            self.resolve_calls += 1
            if self.resolve_calls == 2:
                directory.rename(displaced)
                directory.mkdir()
                (directory / "replacement.txt").write_text("replacement", encoding="utf-8")
            return super().resolve_existing(relative_path, expected=expected)

    guard = SwappingGuard(tmp_path)

    with pytest.raises(PathConfinementError, match="identity changed"):
        guard.snapshot_directory(normalize_relative_path("tree"), max_entries=20)
