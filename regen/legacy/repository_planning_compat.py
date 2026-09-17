"""Compatibility names for replaying repository plans created before the rename."""
from __future__ import annotations

from pathlib import Path


LEGACY_POLICY_ARTIFACT = 'session-planning-policy'
LEGACY_AUDIT_FILENAME = 'session-planning-audit.json'
LEGACY_STAGE = 'Session-Planning'
LEGACY_BUDGET_KEY = 'session_planner_context_chars'


def policy_artifact_supported(value: object, current: str) -> bool:
    return value in {current, LEGACY_POLICY_ARTIFACT}


def audit_path(output: Path, current: str) -> Path:
    current_path = output / current
    legacy_path = output / LEGACY_AUDIT_FILENAME
    return current_path if current_path.is_file() or not legacy_path.is_file() else legacy_path


def transcript_stage_supported(stage: object, policy_artifact: object, current: str) -> bool:
    if stage == current:
        return True
    return policy_artifact == LEGACY_POLICY_ARTIFACT and stage == LEGACY_STAGE


def context_budget(manifest: dict, current: str) -> int:
    budgets = manifest['budgets']
    if current in budgets:
        return int(budgets[current])
    return int(budgets[LEGACY_BUDGET_KEY])
