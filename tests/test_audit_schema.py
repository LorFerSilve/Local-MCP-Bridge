"""Focused negative tests for the fixed Phase 8 audit event schema."""

from pathlib import Path

import pytest

from local_mcp_bridge.audit import AuditError, AuditLogger


def test_arbitrary_audit_action_is_rejected(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path / "audit")

    with pytest.raises(AuditError, match="fixed metadata schema"):
        logger.record("caller.supplied.secret-text", "success", project_id="demo")


def test_unknown_audit_outcome_is_rejected(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path / "audit")

    with pytest.raises(AuditError, match="outcome"):
        logger.record("git.status", "maybe", project_id="demo")
