"""Planning-only revisions do not mutate or rebuild the semantic environment."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import unittest
from regen.tests.current import test_theory_plan
from regen.planning import repository_planner as planner
from regen.planning import planning_freeze as freeze
from regen.preparation.environment_gate import read_json


class PlanningFreezeTest(unittest.TestCase):
    setUp = test_theory_plan.TheoryPlanTest.setUp
    def test_new_policy_binding_replay_and_tampering(self):
        f = self.f
        # This policy has never been frozen by the environment.
        f.policy = deepcopy(f.policy)
        f.policy['rules']['priority'] = 'Explicit new policy revision'
        f.policy_hash = 'new-policy-source-digest'
        old_manifest = (f.root / 'Environment-Freeze/campaign-manifest.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'frozen input'):
            f.call([self.proposal], allow=True)
        original = planner.run
        def run(*args, **kwargs):
            return original(*args, **kwargs, freeze_planning=True)
        with patch.object(planner, 'run', side_effect=run):
            result, calls = f.call([self.proposal], allow=True)
        self.assertEqual(calls, 1)
        self.assertEqual(result['contracts'], 2)
        self.assertEqual(old_manifest, (f.root / 'Environment-Freeze/campaign-manifest.json').read_bytes())
        self.assertEqual(f.call(verify_only=True)[1], 0)
        planner.load_file_projection(f.root, f.output, 'Zeta')
        source = next((f.output / 'Planning-Freeze/code').rglob('*.py'))
        source.write_text('corrupt')
        with self.assertRaisesRegex(ValueError, 'snapshot drift'):
            planner.load_file_projection(f.root, f.output, 'Zeta')

    def test_live_code_change_requires_new_output(self):
        f = self.f
        manifest = read_json(f.root / 'Environment-Freeze/campaign-manifest.json')
        freeze.bind(f.output, f.payload, manifest, create=True)
        with patch.object(freeze, 'code_files', return_value={}):
            freeze.bind(f.output, f.payload, manifest)  # saved evidence remains readable
            with self.assertRaisesRegex(ValueError, 'implementation changed'):
                freeze.bind(f.output, f.payload, manifest, check_live=True)
        altered = deepcopy(f.payload); altered['policy_sha256'] = 'different'
        with self.assertRaisesRegex(ValueError, 'policy/budget mismatch'):
            freeze.bind(f.output, altered, manifest)

    def test_prepare_only_saves_input_without_call(self):
        f = self.f
        with patch.object(planner, 'call_codex', side_effect=AssertionError('no model call allowed')):
            result = planner.run(f.root, f.gate, f.index, f.policy, f.policy_hash, f.output,
                                 freeze_planning=True, prepare_only=True)
        self.assertEqual(result['verdict'], 'prepared')
        self.assertFalse(result['external_call_performed'])
        self.assertTrue((f.output / 'planner-input.json').is_file())
        self.assertFalse((f.output / 'repository-plan.json').exists())
        self.assertEqual(f.call([self.proposal], allow=True)[1], 1)
