from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from regen.planning.publication_admission import accepted_document_binding, complete_document_budget
from regen.planning.theory_document import candidate, render, block, header
from regen.tests.current.test_proof_commits import fixture
from regen.tests.current.test_proof_batches import Checker
from regen.tests.current import test_theory_document


class PublicationAdmissionTests(unittest.TestCase):
    def test_order_only_uses_certified_order_without_accepting_changed_proofs(self):
        package, initial = fixture()
        imports = Checker().imports()
        source = render(package, initial, imports).replace('oops', 'by simp')
        state = candidate(package, initial, source, imports)
        nodes = {n['id']:n for n in state['graph']['nodes']}
        reordered = header(package['theory'], imports) + ''.join(
            block(nodes[k],state['proofs'][k]) for k in reversed(state['accepted_order'])) + 'end\n'
        receipt = accepted_document_binding(package,state,reordered,imports)
        self.assertEqual(receipt['published_order'], state['accepted_order'])
        self.assertNotEqual(receipt['published_order'], receipt['requested_order'])
        self.assertIsNone(accepted_document_binding(package,state,reordered.replace('by simp','by blast',1),imports))
        altered = reordered.replace('lemma helper: "True & True"','lemma helper: "True | False"')
        self.assertIsNone(accepted_document_binding(package,state,altered,imports))
        self.assertIsNone(accepted_document_binding(package,state,reordered.replace('by simp','oops',1),imports))

    def test_whole_budget_is_scoped_and_exception_safe(self):
        checker = Checker(); checker.timeout=900; checker.batch_candidate_timeout=300
        with self.assertRaisesRegex(RuntimeError,'native failure'):
            with complete_document_budget(checker):
                self.assertEqual(checker.batch_candidate_timeout,900)
                raise RuntimeError('native failure')
        self.assertEqual(checker.batch_candidate_timeout,300)
        del checker.batch_candidate_timeout
        with complete_document_budget(checker):self.assertEqual(checker.batch_candidate_timeout,900)
        self.assertFalse(hasattr(checker,'batch_candidate_timeout'))

    def test_reordered_finish_skips_candidate_replay_keeps_draft_and_defers_failed_seal(self):
        f=test_theory_document.DocumentControllerTests();f.setUp();self.addCleanup(f.doCleanups)
        draft=f.read()['document']
        extra={'id':'extra','name':'extra','role':'helper','statement':'lemma extra: "True"','context':[]}
        source=draft['source'].replace('oops','by simp').rsplit('end',1)[0]+block(extra,'by simp')+'end\n'
        saved=f.call({'action':'edit_theory','base_sha256':draft['source_sha256'],'source':source})
        f.call({'action':'check_theory','source_sha256':saved['source_sha256']})
        package,state=f.controller._state('Target'); nodes={n['id']:n for n in state['graph']['nodes']}
        reordered=header(package['theory'],f.checkers['Target'].imports())+''.join(
            block(nodes[k],state['proofs'][k]) for k in reversed(state['accepted_order']))+'end\n'
        saved=f.call({'action':'edit_theory','base_sha256':saved['source_sha256'],'source':reordered})
        before=deepcopy(state['proofs'])
        with patch('regen.planning.theory_document.check_document',side_effect=AssertionError('unnecessary candidate replay')), \
             patch('regen.planning.batch_file_planner.seal_theory', return_value=(None,{'stage':'complete',
                 'check':{'verdict':'red','diagnostic':'TIMEOUT'}})) as seal:
            result=f.call({'action':'finish','source_sha256':saved['source_sha256']})
        seal.assert_called_once()
        self.assertIn('publication_deferred',result)
        self.assertEqual(f.controller._state('Target')[1]['proofs'],before)
        self.assertEqual(f.read()['document']['source'],reordered)
        blocked=f.call({'action':'edit_proof','entry_id':'target_contract','proof_body':'by blast',
                        'expected_hash':saved['source_sha256']})
        self.assertIn('publication_deferred',blocked)
        # Rereading a historical failed receipt in a new session must not
        # re-trigger the scheduling decision.
        f.controller.publication_deferred = None
        f.controller._publication_tasks.clear()
        row=f.controller.executor.get(result['task_id'])
        viewed=f.controller._consume_task(row,{'kind':'seal','proposal':{'action':'finish'}})
        self.assertNotIn('publication_deferred',viewed['result'])
        self.assertIsNone(f.controller.publication_deferred)

    def test_warm_worker_receives_whole_budget_per_request(self):
        from regen.tests.current.test_batch_timeouts import BatchTimeoutTest
        from regen.execution.proof_batches import check_batch
        setup=BatchTimeoutTest()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old,new,checker=setup.setup_case(root)
            checker.timeout=900;checker.batch_candidate_timeout=300
            transaction=Mock(return_value=('TIMEOUT\nfixture',''))
            with patch('regen.execution.batch_checker.BatchWorker',setup.worker_factory(root,transaction)):
                with complete_document_budget(checker):evidence=check_batch(checker,old,new)
            self.assertEqual(transaction.call_args.kwargs['fields']['candidate_timeout'],900)
            self.assertEqual(evidence['check']['timeout_seconds'],900)
            self.assertEqual(checker.batch_candidate_timeout,300)

    def test_publication_failure_ends_provider_session_without_unknown(self):
        from regen.tests.current.test_parallel_file_planner import SchedulerTests
        f=SchedulerTests()
        def failed_finish(turn):
            f.empty_controller.state['proofs']['goal']='by simp'
            f.empty_controller.publication_deferred={'reason':'publication_check_failed'}
        with tempfile.TemporaryDirectory() as tmp:
            result=f._empty_turn_scenario(tmp,on_turn=failed_finish)
        self.assertEqual(result['status'],'unpublished')
        self.assertEqual(result['session_stop_reason'],'publication_check_failed')
        self.assertEqual(f.empty_session.turns,1)
        self.assertIsNone(f.empty_controller.theory_outcome)
        self.assertEqual(f.empty_controller.state['proofs'],{'goal':'by simp'})
