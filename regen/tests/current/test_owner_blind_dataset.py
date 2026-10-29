import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.preparation.owner_blind_dataset import anonymous_payload, vero_benchmark
from regen.preparation.checker_context import load_context
from regen.preparation.refine_campaign import sha256_json
from regen.planning.plan_review import analyze, review_input
from regen.planning.workspace_harness import short_view
from regen.tests.current import test_iterative_repository_planner as fixtures


class AnonymousDatasetTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.IterativeRepositoryPlannerTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.source = json.loads(self.fixture.input.read_text())

    def test_canonical_review_and_projection_inputs_do_not_recover_original_owner(self):
        payload, mapping = anonymous_payload(self.source)
        view = short_view(payload)
        cid = payload['contracts'][0]['contract_id']
        plan = {'schema_version': 6, 'theories': [
            {'name': 'Generated_New', 'contract_ids': [cid], 'required_imports': []}]}
        packet = review_input(plan, payload['contracts'], analyze(plan, payload['contracts']))
        for value in (payload, packet, view.payload):
            text = json.dumps(value)
            self.assertNotIn(self.fixture.cid, text)
            self.assertNotIn('Demo_R', text)
        self.assertEqual(mapping[cid], self.fixture.cid)
        self.assertEqual(payload['contracts'][0]['exact_statement'], self.source['contracts'][0]['exact_statement'])
        self.assertEqual(payload['contracts'][0]['contract_owned_context'], self.source['contracts'][0]['contract_owned_context'])

    def test_original_owner_reassignment_cannot_change_canonical_dataset(self):
        changed = copy.deepcopy(self.source)
        old = changed['contracts'][0]['contract_id']
        new = 'contract:Refine.Other_R:example:secret'
        changed['contracts'][0].update(contract_id=new, owner_theory='Refine.Other_R', owner_file_slot='Other_R.thy')
        changed['contract_cards'][0].update(contract_id=new, owner_theory='Refine.Other_R')
        changed['owner_groups'] = {'Refine.Other_R': [new]}
        changed['theory_import_graph'] = {'Refine.Other_R': ['Refine.Hidden_R']}
        self.assertNotEqual(old, new)
        self.assertEqual(anonymous_payload(self.source)[0], anonymous_payload(changed)[0])

    def test_vero_export_removes_owner_identity_without_changing_target_content(self):
        payload, mapping = anonymous_payload(self.source)
        bench = vero_benchmark(payload, checker_context_id='context', base_import='Semantic_Root')
        self.assertEqual(len(bench['contracts']), len(payload['contracts']))
        self.assertEqual({c['module'] for c in bench['contracts']}, {'Vero_Fixed_Targets'})
        for original, public in zip(payload['contracts'], bench['contracts']):
            self.assertEqual(public['source_id'], original['contract_id'])
            self.assertIn(public['source_id'], mapping)
            self.assertEqual(public['statement'], original['exact_statement'])
            self.assertEqual(public['context'], original['contract_owned_context'])
        text = json.dumps(bench)
        self.assertNotIn(self.fixture.cid, text)
        self.assertNotIn('Demo_R', text)
        with self.assertRaisesRegex(ValueError, 'anonymous canonical'):
            vero_benchmark(self.source, checker_context_id='context', base_import='Semantic_Root')

    def test_generic_controller_actually_uses_owner_blind_public_view(self):
        from regen.planning.generic_codex_agent import Controller
        payload, _ = anonymous_payload(self.source)
        path = self.fixture.input.parent/'repository-planning-input.json'
        path.write_text(json.dumps(payload))
        controller = Controller.__new__(Controller)
        controller.dataset = path.parent
        controller.config = {'threads': 4}
        controller.planning_root = self.fixture.root/'generic-owner-blind-view'
        with patch('regen.planning.generic_codex_agent.native_checker', return_value=lambda *args: None), \
             patch('regen.planning.workspace_harness.PlanningInputQueries') as queries:
            queries.return_value.database_version = 2
            queries.return_value.input = {'parent_premises': {'database_sha256': 'fixture'}}
            harness = controller._planner()
        self.assertEqual(harness.view.payload['contract_visibility'], 'owner-blind-v1')
        self.assertTrue(all('owner_theory' not in c for c in harness.view.payload['contracts']))
        self.assertNotIn(self.fixture.cid, json.dumps(harness.view.payload))

    def test_crefine_enters_real_planner_but_mixed_target_or_foreign_data_cannot(self):
        from regen.planning.repository_planner import run_prepared
        payload, _ = anonymous_payload(self.source)
        payload['benchmark']['target_session'] = 'CRefine'
        path = self.fixture.root/'anonymous-crefine.json'
        path.write_text(json.dumps(payload))
        policy = copy.deepcopy(self.fixture.policy)
        policy['stage'] = 'CRefine'
        result = run_prepared(path, policy, self.fixture.root/'crefine-prepare-only', prepare_only=True)
        self.assertEqual(result['verdict'], 'prepared')
        self.assertFalse(result['external_call_performed'])
        policy['stage'] = 'Refine'
        with self.assertRaisesRegex(ValueError, 'target differs'):
            run_prepared(path, policy, self.fixture.root/'wrong-target', prepare_only=True)
        policy['stage'] = 'CRefine'
        payload['benchmark']['cross_experiment_inputs'] = ['generated-refine-result']
        path.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, 'independent'):
            run_prepared(path, policy, self.fixture.root/'cross-experiment', prepare_only=True)


class CRefineContextTests(unittest.TestCase):
    def test_target_type_pair_and_identity_remain_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = {k: k for k in ('l4v', 'semantic_workspace', 'heaps', 'isabelle')}
            for path in paths.values(): (root/path).mkdir()
            for name in ('parent-premises.sqlite', 'audit.json'): (root/name).touch()
            (root/'audit.json').write_text(json.dumps({'campaign_id':'test',
                'semantic_base_seal_id':'test-base', 'records':[]}))
            core = {'target_session': 'CRefine', 'artifact_type': 'crefine-checker-context',
                    'paths': paths, 'premise_database': 'parent-premises.sqlite', 'premise_audit_context': 'audit.json'}
            def save(value):
                (root/'checker-context.json').write_text(json.dumps({**value, 'checker_context_id': 'checker-context:'+sha256_json(value)[:20]}))
            save(core)
            with patch('regen.preparation.premise_database.verify_database', return_value=1):
                self.assertEqual(load_context(root)['target_session'], 'CRefine')
                save({**core, 'artifact_type': 'refine-checker-context'})
                with self.assertRaises(ValueError): load_context(root)
                save({**core, 'target_session': 'Other'})
                with self.assertRaises(ValueError): load_context(root)


if __name__ == '__main__': unittest.main()
