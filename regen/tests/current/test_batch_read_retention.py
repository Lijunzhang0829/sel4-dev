"""Explicit detail reads survive intervening work until deliberately replaced."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from regen.planning.batch_file_planner import apply_feedback, planner_input, restore_batch_memory
from regen.tests.current.test_proof_commits import fixture


class BatchReadRetentionTest(unittest.TestCase):
    def detail(self, kind, identity):
        if kind == 'attempts':
            return {'request_number':identity,
                    'candidate':{'action':'submit_batch', 'proofs':{'helper':'by simp'}},
                    'feedback':{'status':'rejected_batch', 'check':{'diagnostic':'remaining goal ' + 'x'*8000}}}
        return {'query_id':'query:'+str(identity), 'request':{'premise_query':'fact_'+str(identity)},
                'requests':[identity], 'returned_record_ids':['premise:'+str(identity)],
                'last_status':'available', 'identity_bound':True}

    def read(self, state, kind, identities, number):
        details = [self.detail(kind, identity) for identity in identities]
        proposal = {'action':'read_'+kind,
                    'request_numbers' if kind == 'attempts' else 'query_ids':
                        identities if kind == 'attempts' else ['query:'+str(i) for i in identities]}
        feedback = {'status':'available', kind:details}
        return apply_feedback(state, proposal, feedback, number), proposal, feedback

    def assert_visible(self, package, state, kind, identities):
        self.assertEqual(planner_input(package, state)['requested_'+kind],
                         [self.detail(kind, identity) for identity in identities])

    def test_attempt_detail_survives_query_then_fact_read_without_truncation(self):
        package, state = fixture()
        state, _, _ = self.read(state, 'attempts', [39], 40)
        state = apply_feedback(state, {'action':'query','request':{'premise_query':'needed'}},
                               {'status':'available','kind':'fact_selection','records':[]}, 41)
        self.assert_visible(package, state, 'attempts', [39])
        state = apply_feedback(state, {'action':'read_facts','record_ids':['premise:one']},
                               {'status':'available','records':[{'record_id':'premise:one','exact_statement':'P'}]}, 42)
        self.assert_visible(package, state, 'attempts', [39])
        self.assertGreater(len(planner_input(package, state)['requested_attempts'][0]['feedback']['check']['diagnostic']), 8000)

    def test_query_detail_survives_other_actions_and_attempt_read(self):
        package, state = fixture()
        state, _, _ = self.read(state, 'queries', [10], 40)
        state = apply_feedback(state, {'action':'query','request':{'premise_query':'other'}},
                               {'status':'available','kind':'fact_selection','records':[]}, 41)
        state, _, _ = self.read(state, 'attempts', [39], 42)
        self.assert_visible(package, state, 'queries', [10])
        self.assert_visible(package, state, 'attempts', [39])

    def test_later_success_replaces_selection_instead_of_accumulating(self):
        for kind in ('attempts', 'queries'):
            with self.subTest(kind=kind):
                package, state = fixture()
                state, _, _ = self.read(state, kind, [1,2,3,4], 20)
                state, _, feedback = self.read(state, kind, [5], 21)
                self.assert_visible(package, state, kind, [5])
                feedback[kind].clear()
                self.assert_visible(package, state, kind, [5])

    def test_failed_reads_do_not_erase_last_successful_selection(self):
        for kind in ('attempts', 'queries'):
            with self.subTest(kind=kind):
                package, state = fixture()
                state, _, _ = self.read(state, kind, [1], 20)
                state = apply_feedback(state, {'action':'read_'+kind},
                                       {'status':'rejected_request','diagnostic':'invalid ID'}, 21)
                self.assert_visible(package, state, kind, [1])

    def test_older_feedback_cannot_replace_newer_successful_selection(self):
        for kind in ('attempts', 'queries'):
            with self.subTest(kind=kind):
                package, state = fixture()
                state, _, _ = self.read(state, kind, [1], 20)
                state, _, _ = self.read(state, kind, [2], 30)
                state, _, _ = self.read(state, kind, [99], 25)
                self.assert_visible(package, state, kind, [2])

    def test_artifact_restore_preserves_both_selections_and_is_idempotent(self):
        package, state = fixture()
        state['batch_requests'] = 44
        _, read_attempt, attempt_feedback = self.read(state, 'attempts', [39], 1)
        _, read_query, query_feedback = self.read(state, 'queries', [10], 2)
        artifacts = [(read_attempt, attempt_feedback), (read_query, query_feedback),
            ({'action':'query','request':{'premise_query':'later'}},
             {'status':'available','kind':'fact_selection','records':[]}),
            ({'action':'read_facts','record_ids':['premise:one']},
             {'status':'available','records':[{'record_id':'premise:one','exact_statement':'P'}]})]
        original = deepcopy(state)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for number, (proposal, feedback) in enumerate(artifacts, 41):
                request = root/'batch-requests'/f'{number:06d}'
                request.mkdir(parents=True)
                (request/'proposal.json').write_text(json.dumps(proposal))
                (request/'feedback.json').write_text(json.dumps(feedback))
            restored = restore_batch_memory(root, state)
            self.assert_visible(package, restored, 'attempts', [39])
            self.assert_visible(package, restored, 'queries', [10])
            self.assertEqual(restore_batch_memory(root, restored), restored)
        self.assertEqual(state, original)
        self.assertEqual(restored['batch_requested_fact_ids'], ['premise:one'])
        self.assertEqual(restored['proofs'], {})
        self.assertEqual(restored['batch_requests'], 44)


if __name__ == '__main__':
    unittest.main()
