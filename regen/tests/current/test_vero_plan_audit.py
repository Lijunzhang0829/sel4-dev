import copy
import unittest

from regen.planning.vero_plan_audit import POLICY, audit_vero_plan


class VeroPlanAuditTests(unittest.TestCase):
    def setUp(self):
        self.benchmark = {
            'schema_version': 2,
            'benchmark_id': 'crefine-fixture',
            'contracts': [{'id': name} for name in ('c1', 'c2', 'c3', 'c4')],
        }
        self.plan = {
            'version': 1,
            'source_benchmark_id': 'crefine-fixture',
            'architecture_policy': POLICY,
            'modules': [
                {'name': 'Vero_State', 'contracts': ['c1'], 'imports': [],
                 'rationale': 'state provider'},
                {'name': 'Vero_Operations', 'contracts': ['c2', 'c3'],
                 'imports': ['Vero_State'], 'rationale': 'state consumers'},
                {'name': 'Vero_Entry', 'contracts': ['c4'],
                 'imports': ['Vero_Operations'], 'rationale': 'entry consumer'},
            ],
            'execution_order': ['Vero_State', 'Vero_Operations', 'Vero_Entry'],
            'helper_provider_intents': [{
                'id': 'StateRelations', 'provider': 'Vero_State',
                'consumers': ['Vero_Operations', 'Vero_Entry'],
                'contract_ids': ['c2', 'c4'],
                'rationale': 'Shared state relation consequences may be useful downstream.',
            }],
        }

    def test_declared_provider_is_reachable_without_changing_imports(self):
        before = copy.deepcopy(self.plan)
        audit = audit_vero_plan(self.benchmark, self.plan, require_policy=True)
        self.assertEqual(self.plan, before)
        self.assertEqual(audit['verdict'], 'pass')
        self.assertEqual(audit['local_global_aux_slots'], 3)
        self.assertTrue(audit['helper_provider_intents'][0]['reachable'])
        self.assertEqual(audit['critical_path']['theories'],
                         ['Vero_State', 'Vero_Operations', 'Vero_Entry'])
        self.assertFalse(audit['model_called'])
        self.assertFalse(audit['mutates_plan'])

    def test_unreachable_provider_or_unrelated_contract_is_rejected(self):
        bad = copy.deepcopy(self.plan)
        bad['modules'][2]['imports'] = []
        with self.assertRaisesRegex(ValueError, 'not imported'):
            audit_vero_plan(self.benchmark, bad, require_policy=True)
        bad = copy.deepcopy(self.plan)
        bad['helper_provider_intents'][0]['contract_ids'] = ['c1']
        with self.assertRaisesRegex(ValueError, 'declared consumers'):
            audit_vero_plan(self.benchmark, bad, require_policy=True)

    def test_new_plan_requires_policy_but_legacy_plan_remains_auditable(self):
        old = copy.deepcopy(self.plan)
        old.pop('architecture_policy')
        old.pop('helper_provider_intents')
        with self.assertRaisesRegex(ValueError, 'architecture_policy'):
            audit_vero_plan(self.benchmark, old, require_policy=True)
        audit = audit_vero_plan(self.benchmark, old)
        self.assertEqual(audit['warnings'][0]['kind'], 'legacy-plan')


if __name__ == '__main__':
    unittest.main()
