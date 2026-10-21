import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import durable
from regen.planning.batch_file_planner import apply_feedback, planner_input, query_identity
from regen.tests.current.test_proof_commits import fixture


class QueryMemoryTest(unittest.TestCase):
    def test_same_stat_equal_size_rewrite_invalidates_query_memory(self):
        _, state=fixture()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);directory=root/'batch-requests/000001';directory.mkdir(parents=True)
            db=root/'premises.sqlite';db.write_bytes(b'one')
            frozen=db.stat();original=Path.stat
            def same_stat(path,*args,**kwargs):
                return frozen if path==db else original(path,*args,**kwargs)
            with patch.object(Path,'stat',autospec=True,side_effect=same_stat):
                run={'premise_database':str(db)}
                before=query_identity(run,state,directory,{})
                db.write_bytes(b'two')
                self.assertNotEqual(before,query_identity(run,state,directory,{}))

    def test_query_identity_tracks_environment_not_catalog_rewrite(self):
        _, state = fixture()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); request_dir = root/'batch-requests/000001'
            request_dir.mkdir(parents=True)
            db = root/'premises.sqlite'; db.write_bytes(b'one')
            catalog = root/'sealed-premises.json'
            durable(catalog, {'records':[]})
            run = {'premise_database':str(db), 'run_id':'one'}
            request = {'goal_query':True, 'context_node_id':'helper'}
            first = query_identity(run, state, request_dir, request)
            durable(catalog, {'records':[]})
            self.assertEqual(first, query_identity(run, state, request_dir, request))
            changed = {**request, 'scope':'sealed'}
            self.assertNotEqual(first, query_identity(run, state, request_dir, changed))
            next(n for n in state['graph']['nodes'] if n['id'] == 'helper')['context'] = ['context changed']
            self.assertNotEqual(first, query_identity(run, state, request_dir, request))
            next(n for n in state['graph']['nodes'] if n['id'] == 'helper')['context'] = []
            db.write_bytes(b'two')
            self.assertNotEqual(first, query_identity(run, state, request_dir, request))

    def test_query_memory_groups_exact_identity_and_preserves_empty_results(self):
        package, state = fixture()
        proposal = {'action':'query', 'request':{'context_node_id':'helper','premise_query':'missing'}}
        feedback = {'kind':'fact_selection', 'status':'available', 'records':[], 'query_identity':'same'}
        state = apply_feedback(state, proposal, feedback, 1)
        state = apply_feedback(state, proposal, feedback, 2)
        state['batch_requests'] = 2
        view = planner_input(package, state)
        self.assertEqual(view['query_memory'][0]['requests'], [1,2])
        self.assertEqual(view['query_memory'][0]['returned_record_ids'], [])
        self.assertTrue(view['query_memory'][0]['identity_bound'])
        self.assertEqual(view['progress_signals']['requests_since_proof_submission'], 2)
        self.assertEqual(state['proofs'], {})

    def test_unloaded_fact_remains_complete_and_readable_by_id(self):
        package, state = fixture()
        facts = [{'record_id':str(i), 'canonical_name':'fact_'+str(i),
                  'exact_statement':'lemma fact_'+str(i)+': "True"', 'context':['context C']}
                 for i in range(40)]
        state['batch_facts'] = facts
        view = planner_input(package, state)
        self.assertEqual(len(view['recalled_facts']), 24)
        self.assertEqual(len(view['recalled_fact_index']), 40)
        state = apply_feedback(state, {'action':'read_facts','record_ids':['0']},
                               {'status':'available','records':[facts[0]]}, 1)
        view = planner_input(package, state)
        self.assertIn(facts[0], view['recalled_facts'])
        self.assertEqual(state['batch_facts'], facts)

    def test_graph_only_acceptance_is_not_new_proof_progress(self):
        package, state = fixture()
        state = apply_feedback(state, {'action':'submit_batch','proofs':{},'graph':{}},
                               {'status':'accepted','new_green':[]}, 10)
        state['batch_requests'] = 10
        signals = planner_input(package, state)['progress_signals']
        self.assertEqual(signals['requests_since_accepted_proof_batch'], 10)
        self.assertEqual(signals['requests_since_proof_submission'], 10)

    def test_cache_requires_bound_feedback_bytes_and_never_accepts_proofs(self):
        from regen.planning.batch_file_planner import cached_query_feedback
        _, state = fixture()
        proposal = {'action':'query','request':{'premise_query':'x'}}
        feedback = {'kind':'fact_selection','status':'available','records':[], 'query_identity':'bound'}
        state = apply_feedback(state, proposal, feedback, 1)
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)/'batch-requests/000002'; directory.mkdir(parents=True)
            first = directory.parent/'000001'; first.mkdir()
            durable(first/'feedback.json', feedback)
            page, number = cached_query_feedback(state, directory, 'bound')
            self.assertEqual(page, feedback)
            self.assertEqual(number, 1)
            self.assertEqual(cached_query_feedback(state, directory, 'different'), (None,None))
            durable(first/'feedback.json', {**feedback,'records':[{'record_id':'tampered'}]})
            self.assertEqual(cached_query_feedback(state, directory, 'bound'), (None,None))
            self.assertEqual(state['proofs'], {})

    def test_feedback_file_cannot_claim_uncommitted_acceptance(self):
        from regen.planning.batch_file_planner import restore_batch_memory
        _, state = fixture(); state['batch_requests'] = 1
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); directory = root/'batch-requests/000001'; directory.mkdir(parents=True)
            durable(directory/'proposal.json', {'action':'submit_batch','proofs':{'helper':'by simp'}})
            durable(directory/'feedback.json', {'status':'accepted'})
            restored = restore_batch_memory(root, state)
            self.assertEqual(restored['batch_history'][0]['status'], 'checked_not_committed')
            self.assertEqual(restored['proofs'], {})

    def test_long_diagnostic_is_labelled_excerpt_with_complete_attempt_retained(self):
        package, state = fixture()
        diagnostic = 'remaining goals\n' + 'x' * 5000
        state = apply_feedback(state, {'action':'submit_batch','proofs':{'helper':'by simp'}},
                               {'status':'rejected_batch','check':{'diagnostic':diagnostic}}, 1)
        view = planner_input(package, state)
        summary = view['target_memory']['helper']
        self.assertTrue(summary['diagnostic_excerpt_incomplete'])
        self.assertEqual(summary['read_action'], {'action':'read_attempts','request_numbers':[1]})
        from regen.planning.attempt_memory import target_attempts
        self.assertEqual(dict(target_attempts(state))['helper']['feedback']['check']['diagnostic'], diagnostic)

    def test_compact_query_index_keeps_every_group_readable(self):
        package, state = fixture()
        for i in range(30):
            state = apply_feedback(state, {'action':'query','request':{'premise_query':str(i)}},
                                   {'kind':'fact_selection','status':'available','records':[]}, i+1)
        view = planner_input(package, state)
        self.assertEqual(len(view['query_memory']), 8)
        self.assertEqual(len(view['query_index']), 30)
        self.assertEqual(len({q['query_id'] for q in view['query_index']}), 30)
