"""Discarding an optional route preserves goals, proof authority and history."""
from copy import deepcopy
import unittest
from regen.planning.sketch_check import authorize, load_groups
from regen.planning.sketch_workflow import diagnose_failure, workflow_view
from regen.planning.theory_document import block, header, parse
from regen.tests.current import test_sketch_semantic_review as fixtures


class RouteChoiceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SemanticReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = self.fixture.store

    def withdraw(self, remove_helpers=True):
        current = self.store.read()
        entries, proofs = parse(current['source'], self.store.package, self.store.imports)
        kept = [n for n in entries if not remove_helpers or n['role']=='contract']
        source = header(self.store.package['theory'], self.store.imports) + ''.join(
            block(n, 'by simp' if n['role']=='contract' else proofs.get(n['id']))
            for n in kept) + 'end\n'
        return self.store.edit({'action':'edit_theory', 'base_sha256':current['source_sha256'],
            'source':source, 'sketches':[{'target_entry_id':'contract','helper_ids':[]}]}, {})

    def test_honest_retained_burden_can_continue_with_formal_plan(self):
        row = self.fixture.row()
        row.update(burden='unchanged', reduction='The recursive mechanism remains; only conjuncts are separated.',
                   proof_plan='Keep this route to reuse its conjunction proof; prove it normally.')
        state = deepcopy(self.store.state)
        result = self.fixture.review(row)
        self.assertEqual(result['retained_burden_helper_ids'], ['helper'])
        self.assertFalse(result['difficulty_certified'])
        self.assertIn('withdraw', result['route_options'])
        authorize(self.store, self.store.read()['source'], ['helper'])
        self.assertEqual(self.store.state, state)

    def test_retained_burden_without_formal_plan_is_not_authorized(self):
        row = self.fixture.row()
        row.update(burden='unchanged', proof_mode='diagnostic')
        with self.assertRaisesRegex(ValueError, 'isolated burden'):
            self.fixture.review(row)

    def test_withdraw_preserves_failure_and_fixed_parent(self):
        failure = self.fixture.base.fail()
        diagnose_failure(self.store, failure, 'known_fact',
            'The optional rule is too narrow; abandon this route and prove the fixed parent directly.')
        state = deepcopy(self.store.state)
        saved = self.withdraw()
        self.assertEqual(saved['status'], 'draft_saved')
        self.assertEqual(load_groups(self.store), [])
        self.assertEqual(authorize(self.store, self.store.read()['source'], ['contract']), [])
        self.assertEqual(self.store.state, state)
        history = workflow_view(self.store)['pending_failures']
        self.assertEqual(history[0]['failure_id'], failure)
        self.assertFalse(history[0]['active_in_draft'])
        self.assertEqual(history[0]['diagnosis']['classification'], 'known_fact')

    def test_withdraw_before_diagnosis_rejected_atomically(self):
        self.fixture.base.fail()
        before = self.store.path.read_text()
        with self.assertRaisesRegex(ValueError, 'diagnose failure'):
            self.withdraw()
        self.assertEqual(self.store.path.read_text(), before)

    def test_withdraw_cannot_leave_unproved_orphan(self):
        before = self.store.path.read_text()
        with self.assertRaisesRegex(ValueError, 'new helpers require a parent sketch'):
            self.withdraw(remove_helpers=False)
        self.assertEqual(self.store.path.read_text(), before)

    def test_withdraw_cannot_remove_accepted_helper(self):
        self.store.state['proofs']['helper'] = 'by simp'
        before = self.store.path.read_text()
        with self.assertRaisesRegex(ValueError, 'cannot remove accepted declarations'):
            self.withdraw()
        self.assertEqual(self.store.path.read_text(), before)

    def test_revising_same_route_still_requires_local_repair(self):
        failure = self.fixture.base.fail()
        diagnose_failure(self.store, failure, 'known_fact', 'Retry with the appropriate known rule.')
        before = self.store.path.read_text()
        with self.assertRaisesRegex(ValueError, 'attempt the local proof repair'):
            self.store.edit({'action':'edit_theory','base_sha256':self.store.read()['source_sha256'],
                'source':before.replace('"True & True"','"True"')}, {})
        self.assertEqual(self.store.path.read_text(), before)


if __name__ == '__main__':
    unittest.main()
