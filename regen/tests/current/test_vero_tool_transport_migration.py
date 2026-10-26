"""A diagnostic transport repair preserves proof, input and budget bindings."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from regen.execution.proof_commits import digest
from regen.infrastructure.experiment_ledger import Ledger
from regen.tools.vero_serial_application import (
    EFFECTIVE_BUDGET_POLICY, PREFIX_REVALIDATION_MIGRATION_FROM,
    PREFIX_REVALIDATION_MIGRATION_ID, TOOL_TRANSPORT_MIGRATION_FROM,
    TOOL_TRANSPORT_MIGRATION_ID, expected_runtime_config, frozen_plan_binding,
    prior_prefix_migration, record_prefix_revalidation_migration,
    record_tool_transport_migration, tool_transport_migration_binding,
    validate_resume_artifacts,
)


class VeroToolTransportMigrationTests(unittest.TestCase):
    def fixture(self, root, *, record_predecessor=True):
        (root / 'workspace').mkdir()
        (root / 'run').mkdir()
        planned = {'benchmark_id': 'b', 'contracts': [{'id': 'c1', 'module': 'Vero_A'}]}
        plan = {'modules': [{'name': 'Vero_A', 'contracts': ['c1'], 'imports': []}],
                'execution_order': ['Vero_A']}
        frozen = frozen_plan_binding(planned, plan)
        base = {'client_path': '/fixed/codex', 'model': 'gpt-5.6-sol',
            'reasoning_effort': 'xhigh', 'budget_mode': 'effective-time',
            'effective_seconds_limit': 172800, 'include_planning_time': True,
            'activity_policy': EFFECTIVE_BUDGET_POLICY,
            'application': '/frozen/regen/tools/vero_serial_application.py',
            'application_sha256': PREFIX_REVALIDATION_MIGRATION_FROM,
            'application_dependencies': {
                'execution/vero_checker_adapter.py': 'a' * 64,
                'infrastructure/vero_experiment_observer.py': 'b' * 64,
                'infrastructure/effective_time_budget.py': 'c' * 64},
            'checker_context_sha256': '1' * 64,
            'benchmark_sha256': '2' * 64,
            'native_slots': 1, 'shared_host_slots': 2}
        old = {**base, 'frozen_plan': frozen}
        prefix = {**base, 'application': '/prefix-v3/regen/tools/vero_serial_application.py',
            'application_sha256': TOOL_TRANSPORT_MIGRATION_FROM,
            'application_dependencies': {**base['application_dependencies'],
                'execution/vero_checker_adapter.py': 'd' * 64,
                'infrastructure/vero_experiment_observer.py': 'e' * 64,
                'execution/vero_prefix_revalidation.py': 'f' * 64}}
        new = {**prefix, 'application': '/transport/regen/tools/vero_serial_application.py',
            'application_sha256': '3' * 64,
            'application_dependencies': {**prefix['application_dependencies'],
                'tools/vero_application_tool.py': '4' * 64,
                'tools/vero_tool_protocol.py': '5' * 64}}
        artifacts = {'binding.json': old, 'accepted-plan.json': plan,
            'planned-benchmark.json': planned,
            'config.json': expected_runtime_config(root, old, frozen)}
        for name, value in artifacts.items():
            (root / name).write_text(json.dumps(value))
        ledger = Ledger(root, root.name)
        if record_predecessor:
            record_prefix_revalidation_migration(ledger, old, prefix)
        return old, prefix, new, ledger

    def test_resume_uses_recorded_prefix_v3_without_rewriting_frozen_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, _, new, ledger = self.fixture(root)
            before = {p.name: p.read_bytes() for p in root.glob('*.json')}
            restored = validate_resume_artifacts(root, new, tool_transport_migration=True)
            self.assertEqual(restored[3], old['frozen_plan'])
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.glob('*.json')})
            self.assertEqual(len(Ledger(root, root.name).rows), len(ledger.rows))
            event = tool_transport_migration_binding(old, new, prior_prefix_migration(ledger))
            self.assertEqual(event['original_application_sha256'], TOOL_TRANSPORT_MIGRATION_FROM)
            self.assertEqual(event['predecessor_migration_sha256'], digest(ledger.rows[0]['data']))
            self.assertTrue(event['clock_and_usage_preserved'])
            self.assertTrue(event['frozen_inputs_and_plan_preserved'])

    def test_resume_requires_explicit_transport_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, _, new, _ = self.fixture(root)
            with self.assertRaises(ValueError):
                validate_resume_artifacts(root, new)

    def test_missing_prefix_predecessor_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, _, new, ledger = self.fixture(root, record_predecessor=False)
            with self.assertRaisesRegex(ValueError, 'requires the audited prefix repair'):
                prior_prefix_migration(ledger)
            with self.assertRaises(ValueError):
                validate_resume_artifacts(root, new, tool_transport_migration=True)
            with self.assertRaises(ValueError):
                record_tool_transport_migration(ledger, old, new)
            self.assertEqual(len(ledger.rows), 0)

    def test_duplicate_prefix_predecessors_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, _, new, ledger = self.fixture(root)
            ledger.append('implementation_migration', **deepcopy(ledger.rows[0]['data']))
            with self.assertRaisesRegex(ValueError, 'requires the audited prefix repair'):
                prior_prefix_migration(ledger)
            with self.assertRaises(ValueError):
                validate_resume_artifacts(root, new, tool_transport_migration=True)
            with self.assertRaises(ValueError):
                record_tool_transport_migration(ledger, old, new)

    def test_wrong_predecessor_release_or_frozen_binding_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, _, new, ledger = self.fixture(Path(tmp))
            prior = prior_prefix_migration(ledger)
            changes = {'migration_id': 'other-migration',
                'new_application_sha256': '9' * 64,
                'original_application_sha256': '8' * 64,
                'frozen_binding_sha256': '7' * 64}
            for key, value in changes.items():
                with self.subTest(key=key), self.assertRaises(ValueError):
                    tool_transport_migration_binding(old, new, {**prior, key: value})
            changed_old = {**old, 'application_sha256': '6' * 64}
            changed_prior = {**prior, 'original_application_sha256': '6' * 64,
                             'frozen_binding_sha256': digest(changed_old)}
            with self.assertRaises(ValueError):
                tool_transport_migration_binding(changed_old, new, changed_prior)

    def test_checker_budget_and_existing_dependency_changes_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, prefix, new, ledger = self.fixture(Path(tmp))
            for name in prefix['application_dependencies']:
                changed = deepcopy(new)
                changed['application_dependencies'][name] = '9' * 64
                with self.subTest(dependency=name), self.assertRaises(ValueError):
                    tool_transport_migration_binding(old, changed, prior_prefix_migration(ledger))

    def test_dependency_additions_and_removals_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, _, new, ledger = self.fixture(Path(tmp))
            variants = []
            extra = deepcopy(new)
            extra['application_dependencies']['execution/other_checker.py'] = '9' * 64
            variants.append(('extra', extra))
            for name in new['application_dependencies']:
                changed = deepcopy(new)
                del changed['application_dependencies'][name]
                variants.append(('missing:' + name, changed))
            for label, changed in variants:
                with self.subTest(change=label), self.assertRaises(ValueError):
                    tool_transport_migration_binding(old, changed, prior_prefix_migration(ledger))

    def test_model_budget_inputs_checker_context_and_capacity_are_frozen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, _, new, _ = self.fixture(root)
            changes = {'model': 'other', 'reasoning_effort': 'medium',
                'effective_seconds_limit': 200000, 'include_planning_time': False,
                'checker_context_sha256': '9' * 64, 'benchmark_sha256': '8' * 64,
                'native_slots': 2, 'shared_host_slots': 3}
            for key, value in changes.items():
                with self.subTest(key=key), self.assertRaises(ValueError):
                    validate_resume_artifacts(root, {**new, key: value},
                                              tool_transport_migration=True)

    def test_modified_frozen_plan_or_benchmark_is_rejected(self):
        for name in ('accepted-plan.json', 'planned-benchmark.json'):
            with self.subTest(artifact=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _, _, new, _ = self.fixture(root)
                path = root / name
                value = json.loads(path.read_text())
                if name == 'accepted-plan.json':
                    value['modules'][0]['contracts'] = ['c2']
                else:
                    value['contracts'][0]['id'] = 'c2'
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    validate_resume_artifacts(root, new, tool_transport_migration=True)

    def test_runtime_config_budget_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, _, new, _ = self.fixture(root)
            path = root / 'config.json'
            config = json.loads(path.read_text())
            config['exploration_limits']['effective_seconds_limit'] = 200000
            path.write_text(json.dumps(config))
            with self.assertRaises(ValueError):
                validate_resume_artifacts(root, new, tool_transport_migration=True)

    def test_transport_flag_cannot_combine_other_migrations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, _, new, _ = self.fixture(root)
            for flag in ('prefix_revalidation_migration', 'seal_migration',
                         'checker_lifecycle_migration', 'resource_pool_migration',
                         'provider_auth_migration'):
                with self.subTest(flag=flag), self.assertRaisesRegex(ValueError, 'cannot combine'):
                    validate_resume_artifacts(root, new, tool_transport_migration=True,
                                              **{flag: True})

    def test_record_is_idempotent_and_rejects_changed_transport_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, _, new, ledger = self.fixture(Path(tmp))
            first = record_tool_transport_migration(ledger, old, new)
            second = record_tool_transport_migration(ledger, old, new)
            self.assertEqual(first['event_id'], second['event_id'])
            self.assertEqual([r['data']['migration_id'] for r in ledger.rows],
                             [PREFIX_REVALIDATION_MIGRATION_ID, TOOL_TRANSPORT_MIGRATION_ID])
            variants = [{**new, 'application_sha256': '9' * 64},
                        {**new, 'application': '/other/vero_serial_application.py'}]
            changed = deepcopy(new)
            changed['application_dependencies']['tools/vero_tool_protocol.py'] = '8' * 64
            variants.append(changed)
            for changed in variants:
                with self.subTest(binding=changed), self.assertRaisesRegex(ValueError, 'differs'):
                    record_tool_transport_migration(ledger, old, changed)
            self.assertEqual(len(ledger.rows), 2)

    def test_duplicate_transport_records_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, _, new, ledger = self.fixture(Path(tmp))
            first = record_tool_transport_migration(ledger, old, new)
            ledger.append('implementation_migration', **deepcopy(first['data']))
            with self.assertRaisesRegex(ValueError, 'differs'):
                record_tool_transport_migration(ledger, old, new)


if __name__ == '__main__':
    unittest.main()
