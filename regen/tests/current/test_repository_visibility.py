"""Frozen import closure, schema-6 packages and schema-7 orchestration."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.execution import repository_execution as execution
from regen.execution.proof_commits import digest, durable
from regen.infrastructure.path_identity import source_identity
from regen.planning.import_contract import build_bundle_v5, build_bundle_v6
from regen.planning.plan_revisions import start_plan, append_revision
from regen.planning.repository_planner import _bind_direct_artifacts
from regen.tests.current.test_collaboration_plan import fixture


class RepositoryVisibilityTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ('heaps', 'generated', 'isabelle'):
            (self.root/name).mkdir()
        self.run = {
            'execution_input_kind': 'refine-dataset', 'implementation': {},
            'checker_context_root': str(self.root),
            'isabelle_dir': str(self.root/'isabelle'),
            'isabelle_sha256': source_identity(self.root/'isabelle', allow_links=True),
            'l4v': str(self.root), 'semantic_workspace': str(self.root),
            'base_session': 'Semantic_Base', 'base_import': 'Semantic_Base.Semantic_Root',
            'base_catalog': '/semantic-base', 'image': 'test-image',
        }
        self.states = [{'theory': name, 'file_slot': name+'.thy',
                        'status': 'generating' if name in ('C', 'Pending') else 'sealed'}
                       for name in ('A', 'B', 'C', 'Unrelated', 'Pending')]
        durable(self.root/'run.json', self.run)
        durable(self.root/'execution-input.json', {'files': self.states})
        self.records = [{'record_id': name, 'canonical_name': 'Regen_'+name+'.result',
                         'status': 'sealed', 'owner_theory': name, 'proof_body': 'synthetic example'}
                        for name in ('A', 'B', 'Unrelated')]
        durable(self.root/'generated-premises.json', {'records': self.records})
        for name, imports in [('A', []), ('B', ['Regen_A']), ('Unrelated', [])]:
            durable(self.root/'theories'/name/'imports.json', imports)
            (self.root/'generated'/('Regen_'+name+'.thy')).write_text(name)
        self.package = {'file_slot': 'C.thy', 'repository_projection': {
            'schema_version': 6, 'required_imports': [{'theory': 'B'}],
            'reuse_suggestions': [{'provider': 'Unrelated', 'consumer': 'C', 'reason': 'Possibly useful.'}],
        }}
        self.stack = __import__('contextlib').ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(execution, 'progress', return_value={'files': self.states}))
        self.stack.enter_context(patch.object(execution, 'package_for', return_value=self.package))
        self.stack.enter_context(patch.object(execution, 'journal'))
        self.build = self.stack.enter_context(patch(
            'regen.execution.isabelle_node_checker.IsabelleChecker.build_generated_session',
            return_value={'verdict': 'green'}))

    def test_transitive_sealed_records_match_source_closure_and_direct_imports_stay_frozen(self):
        result = execution.setup(self.root, 'C', 10)
        checker, imports = result[4:]
        self.assertEqual(imports, ['Regen_B'])
        self.assertEqual(execution.read(self.root/'theories/C/imports.json'), ['Regen_B'])
        rows = execution.read(self.root/'theories/C/sealed-premises.json')['records']
        self.assertEqual([row['record_id'] for row in rows], ['A', 'B'])
        self.assertTrue(all(row['proof_body'] == 'synthetic example' for row in rows))
        self.assertEqual(sorted(p.name for p in checker.generated_dir.glob('*.thy')), ['Regen_A.thy', 'Regen_B.thy'])
        self.assertEqual(checker.imports(), ['Generated_Refine.Regen_B'])
        self.build.assert_called_once_with(['Regen_B'])
        self.package['repository_projection']['required_imports'] = [{'theory': 'Unrelated'}]
        execution.setup(self.root, 'C', 10)
        self.assertEqual(execution.read(self.root/'theories/C/sealed-premises.json')['records'], rows)
        self.build.assert_called_once()

    def test_transitive_green_or_retired_provider_never_becomes_visible(self):
        for status in ('generating', 'retired', 'pending'):
            self.states[0]['status'] = status
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, 'not sealed'):
                execution.setup(self.root, 'C', 10)
            self.assertFalse((self.root/'theories/C/sealed-premises.json').exists())
        self.build.assert_not_called()

    def test_unfrozen_unknown_and_cyclic_transitive_imports_fail_closed(self):
        path = self.root/'theories/A/imports.json'
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'missing frozen imports'):
            execution.setup(self.root, 'C', 10)
        for imports, diagnostic in [(['Regen_Missing'], 'not sealed'),
                                    (['Regen_B'], 'cyclic'),
                                    (['Regen_Pending'], 'not sealed'),
                                    (['Regen_A', 'Regen_A'], 'invalid')]:
            durable(path, imports)
            with self.subTest(imports=imports), self.assertRaisesRegex(ValueError, diagnostic):
                execution.setup(self.root, 'C', 10)
        self.build.assert_not_called()


class NewSchemaExecutionTest(unittest.TestCase):
    def test_new_metadata_is_available_and_legacy_package_hash_is_unchanged(self):
        payload, proposal = fixture()
        artifacts = build_bundle_v6(payload, proposal)
        plan = artifacts['repository-plan.json']
        projection = artifacts['files/Access.json']
        package = execution.projection_package(projection, plan)
        for key in ('collaboration_groups', 'reuse_suggestions', 'context_profiles'):
            self.assertEqual(package[key], projection[key])
            self.assertEqual(package['repository_projection'][key], projection[key])
        self.assertEqual(package['support_contracts'], [])
        # Schema 5 has no new metadata: retain exactly its original body/hash.
        old = deepcopy(proposal)
        old['schema_version'] = 5
        old.pop('collaboration_groups'); old.pop('reuse_suggestions')
        legacy = build_bundle_v5(payload, old)
        legacy_plan = {**legacy['repository-plan.json'], 'plan_id': 'old-plan'}
        p = legacy['files/Access.json']
        body = {'theory': p['theory'], 'file_slot': p['file_slot'],
                'contracts': p['contracts'], 'support_contracts': [],
                'repository_projection': p, 'plan_id': 'old-plan', 'projection_sha256': digest(p)}
        self.assertEqual(execution.projection_package(p, legacy_plan),
                         {**body, 'package_payload_sha256': digest(body)})

    def test_schema7_initializes_against_verified_plan_revision(self):
        payload, proposal = fixture()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); output = root/'plan'; output.mkdir()
            artifacts = build_bundle_v6(payload, proposal)
            identity = start_plan(output/'planning', payload['benchmark']['id'], identity_token='test')
            current = append_revision(output/'planning',
                {**artifacts['repository-plan.json'], 'plan_id': identity['plan_id']})
            artifacts = _bind_direct_artifacts(artifacts, current, output)
            for name, value in artifacts.items(): durable(output/name, value)
            heaps = root/'assets/heaps'; heaps.mkdir(parents=True)
            context = {'premise_database_path': 'database', 'premise_audit_context_path': 'audit',
                'semantic_workspace': 'semantic', 'heaps': heaps, 'l4v': 'l4v',
                'isabelle': 'isabelle', 'image': 'test', 'isabelle_sha256': 'hash', 'budgets': {},
                'checker_context_id': 'context', 'base_session': 'Base', 'base_import': 'Base.Root',
                'base_catalog': 'catalog'}
            with patch('regen.preparation.checker_context.load_context', return_value=context):
                run = execution.init_run(root/'run', root, 'new-run', output/'file-generation-list.json')
            self.assertEqual(run['execution_input_kind'], 'refine-dataset')
            self.assertEqual(run['session_schedule']['plan_revision'], current['revision'])
            self.assertEqual(execution.progress(root/'run')['checked_contracts'], 0)
            package = execution.package_for(run, 'Access.thy')
            self.assertEqual(package['collaboration_groups'][0]['member_theories'], ['Access', 'Update'])

    def test_schema7_scheduler_uses_hard_imports_and_ignores_reciprocal_advice(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            durable(root/'run.json', {'session_schedule': {'source': str(root/'schedule.json')}})
            durable(root/'schedule.json', {'schema_version': 7})
            states = [{'theory': name, 'file_slot': name+'.thy', 'status': 'pending', 'order': order}
                      for order, name in enumerate(('Consumer', 'Provider'))]
            durable(root/'execution-input.json', {'files': states})
            def package(_run, slot):
                imports = [{'theory': 'Provider'}] if slot == 'Consumer.thy' else []
                return {'repository_projection': {'schema_version': 6, 'required_imports': imports,
                    'reuse_suggestions': [{'provider': 'Consumer', 'consumer': 'Provider', 'reason': 'Maybe'}]}}
            order = []
            def run_file(_root, name, **_options):
                order.append(name)
                entry = next(item for item in states if item['theory'] == name)
                entry['status'] = 'sealed'
                return dict(entry)
            with patch.object(execution, 'progress', side_effect=lambda _: {'files': deepcopy(states)}), \
                 patch.object(execution, 'package_for', side_effect=package), \
                 patch.object(execution, 'run_file', side_effect=run_file):
                result = execution.run_session(root, max_parallel_theories=1)
            self.assertEqual(order, ['Provider', 'Consumer'])
            self.assertEqual(result['blocked_theories'], [])


if __name__ == '__main__':
    unittest.main()
