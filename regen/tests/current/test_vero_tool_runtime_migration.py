"""The runtime repair must extend exactly the already audited transport chain."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import digest
from regen.infrastructure.experiment_ledger import Ledger
from regen.tests.current import test_vero_tool_transport_migration as transport_tests
from regen.tools.vero_serial_application import (
    PREFIX_REVALIDATION_MIGRATION_ID, TOOL_TRANSPORT_MIGRATION_ID,
    TOOL_RUNTIME_MIGRATION_ID, TOOL_RUNTIME_MIGRATION_FROM,
    main, prior_tool_transport_migration, record_tool_runtime_migration,
    record_tool_transport_migration, tool_runtime_migration_binding,
    validate_resume_artifacts,
)


class VeroToolRuntimeMigrationTests(unittest.TestCase):
    def fixture(self, root, *, record_transport=True):
        old, _, transport, ledger = transport_tests.VeroToolTransportMigrationTests().fixture(root)
        transport['application_sha256'] = TOOL_RUNTIME_MIGRATION_FROM
        if record_transport:
            record_tool_transport_migration(ledger, old, transport)
        new = {**transport,
            'application': '/runtime-v2/regen/tools/vero_serial_application.py',
            'application_sha256': '6' * 64}
        return old, transport, new, ledger

    def test_resume_preserves_frozen_artifacts_and_full_predecessor_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, _, new, ledger = self.fixture(root)
            before = {p.name: p.read_bytes() for p in root.glob('*.json')}
            restored = validate_resume_artifacts(root, new, tool_runtime_migration=True)
            self.assertEqual(restored[3], old['frozen_plan'])
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.glob('*.json')})
            self.assertEqual(len(Ledger(root, root.name).rows), 2)
            result = record_tool_runtime_migration(ledger, old, new)
            self.assertEqual(result['data']['predecessor_migration_sha256'],
                             digest(ledger.rows[1]['data']))
            self.assertEqual(result['data']['frozen_binding_sha256'], digest(old))
            self.assertTrue(result['data']['clock_and_usage_preserved'])
            self.assertTrue(result['data']['frozen_inputs_and_plan_preserved'])
            self.assertEqual(result['data']['original_application_dependencies'],
                             result['data']['new_application_dependencies'])

    def test_resume_requires_explicit_runtime_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, _, new, _ = self.fixture(root)
            with self.assertRaises(ValueError):
                validate_resume_artifacts(root, new)
            # The old flag cannot silently replace its recorded v1 event.
            old = json.loads((root / 'binding.json').read_text())
            with self.assertRaises(ValueError):
                record_tool_transport_migration(Ledger(root, root.name), old, new)

    def test_missing_or_duplicate_predecessors_are_rejected_without_appending(self):
        for mode in ('missing', 'duplicate-prefix', 'duplicate-transport'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                old, _, new, ledger = self.fixture(root, record_transport=mode != 'missing')
                if mode != 'missing':
                    which = 0 if mode == 'duplicate-prefix' else 1
                    ledger.append('implementation_migration', **ledger.rows[which]['data'])
                count = len(ledger.rows)
                with self.assertRaises(ValueError):
                    validate_resume_artifacts(root, new, tool_runtime_migration=True)
                with self.assertRaises(ValueError):
                    record_tool_runtime_migration(ledger, old, new)
                self.assertEqual(len(ledger.rows), count)

    def test_reversed_predecessor_order_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, _, new, ledger = self.fixture(Path(tmp))
            prefix, transport = deepcopy(ledger.rows)
            prefix['sequence'], transport['sequence'] = 2, 1
            ledger.rows = [transport, prefix]
            with self.assertRaises(ValueError):
                prior_tool_transport_migration(ledger)
            with self.assertRaises(ValueError):
                record_tool_runtime_migration(ledger, old, new)

    def test_modified_prefix_event_is_rejected_including_original_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, _, new, ledger = self.fixture(Path(tmp))
            prefix, transport = prior_tool_transport_migration(ledger)
            changes = {'migration_id': 'other', 'frozen_binding_sha256': '9' * 64,
                'original_application_sha256': '8' * 64,
                'new_application_sha256': '7' * 64,
                'original_application_dependencies': {},
                'new_application_dependencies': {}, 'clock_and_usage_preserved': False}
            for key, value in changes.items():
                with self.subTest(key=key), self.assertRaises(ValueError):
                    tool_runtime_migration_binding(old, new, {**prefix, key: value}, transport)

    def test_modified_transport_event_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, _, new, ledger = self.fixture(Path(tmp))
            prefix, transport = prior_tool_transport_migration(ledger)
            changes = {'migration_id': 'other', 'predecessor_migration_sha256': '9' * 64,
                'frozen_binding_sha256': '8' * 64, 'original_application_sha256': '7' * 64,
                'new_application_sha256': '6' * 64,
                'original_application_dependencies': {},
                'new_application_dependencies': {}, 'frozen_inputs_and_plan_preserved': False}
            for key, value in changes.items():
                with self.subTest(key=key), self.assertRaises(ValueError):
                    tool_runtime_migration_binding(old, new, prefix, {**transport, key: value})

    def test_all_dependency_changes_additions_and_removals_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, _, new, ledger = self.fixture(Path(tmp))
            prefix, transport = prior_tool_transport_migration(ledger)
            variants = []
            for name in new['application_dependencies']:
                changed, removed = deepcopy(new), deepcopy(new)
                changed['application_dependencies'][name] = '9' * 64
                del removed['application_dependencies'][name]
                variants.extend([(name, changed), ('removed:' + name, removed)])
            extra = deepcopy(new)
            extra['application_dependencies']['extra.py'] = '9' * 64
            variants.append(('extra', extra))
            for name, changed in variants:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    tool_runtime_migration_binding(old, changed, prefix, transport)

    def test_frozen_inputs_budget_model_plan_and_capacity_are_immutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, _, new, ledger = self.fixture(root)
            changes = {'model': 'other', 'reasoning_effort': 'medium',
                'effective_seconds_limit': 200000, 'include_planning_time': False,
                'checker_context_sha256': '9' * 64, 'benchmark_sha256': '8' * 64,
                'native_slots': 2, 'shared_host_slots': 3,
                'client_path': '/other/client', 'frozen_plan': {}}
            for key, value in changes.items():
                changed = {**new, key: value}
                with self.subTest(key=key):
                    with self.assertRaises(ValueError):
                        validate_resume_artifacts(root, changed, tool_runtime_migration=True)
                    with self.assertRaises(ValueError):
                        record_tool_runtime_migration(ledger, old, changed)
            self.assertEqual(len(ledger.rows), 2)

    def test_changed_frozen_plan_benchmark_and_runtime_config_are_rejected(self):
        for name in ('accepted-plan.json', 'planned-benchmark.json', 'config.json'):
            with self.subTest(artifact=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _, _, new, _ = self.fixture(root)
                path = root / name
                value = json.loads(path.read_text())
                if name == 'accepted-plan.json':
                    value['modules'][0]['contracts'] = ['c2']
                elif name == 'planned-benchmark.json':
                    value['contracts'][0]['id'] = 'c2'
                else:
                    value['agent']['env']['PYTHONPATH'] = '/edited-frozen-config'
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    validate_resume_artifacts(root, new, tool_runtime_migration=True)

    def test_runtime_migration_cannot_combine_other_migrations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, _, new, _ = self.fixture(root)
            for flag in ('prefix_revalidation_migration', 'tool_transport_migration',
                         'seal_migration', 'checker_lifecycle_migration',
                         'resource_pool_migration', 'provider_auth_migration'):
                with self.subTest(flag=flag), self.assertRaisesRegex(ValueError, 'cannot combine'):
                    validate_resume_artifacts(root, new, tool_runtime_migration=True,
                                              **{flag: True})

    def test_record_is_idempotent_and_rejects_changed_server_at_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, _, new, ledger = self.fixture(root)
            first = record_tool_runtime_migration(ledger, old, new)
            second = record_tool_runtime_migration(ledger, old, new)
            self.assertEqual(first['event_id'], second['event_id'])
            self.assertEqual([r['data']['migration_id'] for r in ledger.rows],
                [PREFIX_REVALIDATION_MIGRATION_ID, TOOL_TRANSPORT_MIGRATION_ID,
                 TOOL_RUNTIME_MIGRATION_ID])
            for changed in ({**new, 'application_sha256': '9' * 64},
                            {**new, 'application': '/other/server.py'}):
                with self.subTest(binding=changed):
                    with self.assertRaisesRegex(ValueError, 'differs'):
                        record_tool_runtime_migration(ledger, old, changed)
                    with self.assertRaisesRegex(ValueError, 'differs'):
                        validate_resume_artifacts(root, changed, tool_runtime_migration=True)
            self.assertEqual(len(ledger.rows), 3)

    def test_duplicate_runtime_records_rejected_at_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, _, new, ledger = self.fixture(root)
            first = record_tool_runtime_migration(ledger, old, new)
            ledger.append('implementation_migration', **first['data'])
            with self.assertRaisesRegex(ValueError, 'differs'):
                record_tool_runtime_migration(ledger, old, new)
            with self.assertRaisesRegex(ValueError, 'differs'):
                validate_resume_artifacts(root, new, tool_runtime_migration=True)

    def test_cli_requires_resume_and_rejects_every_other_migration_flag(self):
        required = [arg for name in ('vero-source', 'benchmark', 'dataset', 'output',
                                    'client', 'auth', 'image') for arg in ('--' + name, '/unused')]
        required += ['--expected-contracts', '309', '--tool-runtime-migration']
        variants = [[]] + [['--resume', '--' + name] for name in (
            'prefix-revalidation-migration', 'tool-transport-migration',
            'seal-dependency-migration', 'checker-lifecycle-migration',
            'resource-pool-migration', 'provider-auth-migration', 'runner-reopen-migration')]
        for extra in variants:
            with self.subTest(extra=extra), patch('sys.argv', ['vero'] + required + extra), \
                    patch('sys.stderr'), patch('regen.tools.vero_serial_application.run') as run:
                with self.assertRaises(SystemExit) as raised:
                    main()
                self.assertEqual(raised.exception.code, 2)
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
