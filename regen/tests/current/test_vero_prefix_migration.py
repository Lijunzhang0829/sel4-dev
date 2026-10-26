"""The checker repair cannot alter frozen experiment inputs or accounting."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from regen.infrastructure.experiment_ledger import Ledger
from regen.tools.vero_serial_application import (
    EFFECTIVE_BUDGET_POLICY, PREFIX_REVALIDATION_MIGRATION_FROM,
    expected_runtime_config, frozen_plan_binding, prefix_revalidation_migration_binding,
    record_prefix_revalidation_migration, validate_resume_artifacts,
)


class VeroPrefixMigrationTests(unittest.TestCase):
    def bindings(self):
        old = {'client_path': '/fixed/codex', 'model': 'gpt-5.6-sol',
            'reasoning_effort': 'xhigh', 'budget_mode': 'effective-time',
            'effective_seconds_limit': 172800, 'include_planning_time': True,
            'activity_policy': EFFECTIVE_BUDGET_POLICY,
            'application': '/frozen/regen/tools/vero_serial_application.py',
            'application_sha256': PREFIX_REVALIDATION_MIGRATION_FROM,
            'application_dependencies': {'execution/vero_checker_adapter.py': 'a' * 64,
                'infrastructure/vero_experiment_observer.py': 'b' * 64,
                'infrastructure/effective_time_budget.py': 'c' * 64}}
        new = {**old, 'application': '/repair/regen/tools/vero_serial_application.py',
            'application_sha256': 'd' * 64,
            'application_dependencies': {**old['application_dependencies'],
                'execution/vero_checker_adapter.py': 'e' * 64,
                'infrastructure/vero_experiment_observer.py': 'f' * 64,
                'execution/vero_prefix_revalidation.py': '0' * 64}}
        return old, new

    def test_migration_retains_frozen_artifacts_and_rejects_input_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'workspace').mkdir()
            (root / 'run').mkdir()
            planned = {'benchmark_id': 'b', 'contracts': [{'id': 'c1', 'module': 'Vero_A'}]}
            plan = {'modules': [{'name': 'Vero_A', 'contracts': ['c1'], 'imports': []}],
                    'execution_order': ['Vero_A']}
            frozen = frozen_plan_binding(planned, plan)
            old, new = self.bindings()
            files = {'binding.json': {**old, 'frozen_plan': frozen},
                'accepted-plan.json': plan, 'planned-benchmark.json': planned,
                'config.json': expected_runtime_config(root, old, frozen)}
            for name, value in files.items():
                (root / name).write_text(json.dumps(value))
            before = {p.name: p.read_bytes() for p in root.glob('*.json')}
            restored = validate_resume_artifacts(root, new, prefix_revalidation_migration=True)
            self.assertEqual(restored[3], frozen)
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.glob('*.json')})
            for key, value in [('model', 'other'), ('effective_seconds_limit', 200000),
                               ('include_planning_time', False)]:
                with self.subTest(key=key), self.assertRaises(ValueError):
                    validate_resume_artifacts(root, {**new, key: value},
                        prefix_revalidation_migration=True)
            with self.assertRaises(ValueError):
                validate_resume_artifacts(root, new, prefix_revalidation_migration=True,
                                         seal_migration=True)

    def test_unrelated_dependency_or_source_release_change_is_rejected(self):
        old, new = self.bindings()
        changed = deepcopy(new)
        changed['application_dependencies']['infrastructure/effective_time_budget.py'] = '9' * 64
        with self.assertRaises(ValueError):
            prefix_revalidation_migration_binding(old, changed)
        with self.assertRaises(ValueError):
            prefix_revalidation_migration_binding({**old, 'application_sha256': '9' * 64}, new)

    def test_audit_event_is_idempotent_and_binds_new_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp), 'migration')
            old, new = self.bindings()
            first = record_prefix_revalidation_migration(ledger, old, new)
            self.assertEqual(first['event_id'],
                record_prefix_revalidation_migration(ledger, old, new)['event_id'])
            self.assertEqual(len(ledger.rows), 1)
            with self.assertRaises(ValueError):
                record_prefix_revalidation_migration(ledger, old,
                    {**new, 'application_sha256': '8' * 64})


if __name__ == '__main__':
    unittest.main()
