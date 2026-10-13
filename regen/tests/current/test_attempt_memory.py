from copy import deepcopy
import json
import unittest
from regen.planning.attempt_memory import compact_target_memory, expand_target_memory, target_attempts


class AttemptMemoryTests(unittest.TestCase):
    def test_absent_memory_does_not_change_state(self):
        state = {'budget_used':3}
        self.assertEqual(compact_target_memory(state), {'budget_used':3})

    def test_round_trip_and_dedup(self):
        attempt = {'request_number':7, 'candidate':{'proofs':{'a':'x'*10000}},
                   'feedback':{'status':'rejected_batch'}}
        old = {'batch_target_memory':{str(i):deepcopy(attempt) for i in range(122)},
               'cost':123, 'proofs':{}, 'budget_used':7}
        new = compact_target_memory(deepcopy(old))
        self.assertEqual(len(new['batch_attempt_objects']), 1)
        self.assertEqual(expand_target_memory(new), old)
        self.assertLess(len(json.dumps(new)), len(json.dumps(old))/20)
        self.assertEqual(compact_target_memory(deepcopy(new)), new)

    def test_missing_and_corrupt_fail_closed(self):
        new = compact_target_memory({'batch_target_memory':{'a':{'request_number':1}}})
        bad = deepcopy(new); bad['batch_attempt_objects'] = {}
        with self.assertRaises(ValueError): list(target_attempts(bad))
        next(iter(new['batch_attempt_objects'].values()))['request_number'] = 2
        with self.assertRaises(ValueError): list(target_attempts(new))

    def test_mixed_legacy_refs_and_target_replacement(self):
        new = compact_target_memory({'batch_target_memory':{'a':{'request_number':1}}})
        new['batch_target_memory']['b'] = {'request_number':2}
        compact_target_memory(new)
        self.assertEqual(len(new['batch_attempt_objects']),2)
        new['batch_target_memory'].pop('a')
        compact_target_memory(new)
        self.assertEqual(len(new['batch_attempt_objects']),1)

    def test_feedback_update_preserves_other_target_and_model_view(self):
        from regen.planning.batch_file_planner import apply_feedback, planner_input
        from regen.tests.current.test_proof_commits import fixture
        package, state = fixture()
        state = apply_feedback(state,
            {'action':'submit_batch','proofs':{'helper':'by simp','another':'by simp'}},
            {'status':'rejected_batch','check':{'diagnostic':'first'}}, 1)
        before = deepcopy(state)
        updated = apply_feedback(state,
            {'action':'submit_batch','proofs':{'helper':'by blast'}},
            {'status':'rejected_batch','check':{'diagnostic':'second'}}, 2)
        self.assertEqual(state,before)
        resolved = dict(target_attempts(updated))
        self.assertEqual(resolved['helper']['request_number'],2)
        self.assertEqual(resolved['another']['request_number'],1)
        self.assertEqual(planner_input(package,updated),
                         planner_input(package,expand_target_memory(updated)))
        older = apply_feedback(updated,
            {'action':'submit_batch','proofs':{'helper':'by simp'}},
            {'status':'rejected_batch'}, 1)
        self.assertEqual(dict(target_attempts(older))['helper']['request_number'],2)
