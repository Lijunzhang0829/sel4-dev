import tempfile
import unittest
from pathlib import Path

from regen.legacy.repository_planning_compat import (
    audit_path,
    context_budget,
    policy_artifact_supported,
    transcript_stage_supported,
)


class RepositoryPlanningCompatibilityTest(unittest.TestCase):
    def test_old_policy_and_stage_are_read_only_compatible(self):
        self.assertTrue(policy_artifact_supported(
            'session-planning-policy', 'repository-planning-policy',
        ))
        self.assertTrue(transcript_stage_supported(
            'Session-Planning', 'session-planning-policy', 'Repository-Planning',
        ))
        self.assertFalse(transcript_stage_supported(
            'Session-Planning', 'repository-planning-policy', 'Repository-Planning',
        ))

    def test_old_budget_key_is_a_fallback(self):
        self.assertEqual(context_budget(
            {'budgets': {'session_planner_context_chars': 123}}, 'repository_planner_context_chars',
        ), 123)

    def test_current_audit_name_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / 'session-planning-audit.json'
            legacy.write_text('{}')
            self.assertEqual(audit_path(root, 'repository-planning-audit.json'), legacy)
            current = root / 'repository-planning-audit.json'
            current.write_text('{}')
            self.assertEqual(audit_path(root, current.name), current)


if __name__ == '__main__':
    unittest.main()
