"""Local stops do not acquire theorem authority or release provider dependencies."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from regen.planning.theory_outcomes import current_outcome, handle_outcome, record_unresolved


class TheoryOutcomesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.state = {'status':'generating', 'graph':{'nodes':[
            {'id':'a','statement':'lemma a: "True"'},
            {'id':'b','statement':'lemma b: "False"'}]},
            'proofs':{'a':'by simp'}, 'accepted_order':['a'], 'rounds':{'b':3}}
        self.package = {'theory':'T','contracts':[
            {'contract_id':'a','exact_statement':'lemma a: "True"'},
            {'contract_id':'b','exact_statement':'lemma b: "False"'}]}
        self.c = SimpleNamespace(root=Path(self.temp.name),theory='T',
            run={'run_id':'test','implementation':{'frozen':'hash'}},
            _state=lambda _: (deepcopy(self.package),deepcopy(self.state)))

    def test_report_preserves_proofs_and_only_claims_unknown(self):
        before = deepcopy(self.state)
        result = handle_outcome(self.c, {'action':'report_unresolved',
            'entry_ids':['b'], 'reason':'I believe the hypothesis is missing',
            'classification':'missing_assumptions'}, {'call_id':'call'})
        self.assertEqual(result['status'],'unknown')
        self.assertTrue(result['theory_stop'])
        self.assertEqual(self.state,before)
        self.assertEqual(result['theory_outcome']['truth_status'],'undetermined')
        self.assertEqual(result['theory_outcome']['positive_proofs_added'],0)
        self.assertFalse(result['theory_outcome']['theory_sealed'])
        recovered = current_outcome(self.c)
        self.assertEqual(recovered,result['theory_outcome'])

    def test_stop_survives_new_controller_and_expires_on_accepted_context_change(self):
        record_unresolved(self.c,reason='No new native progress',origin='controller',
                          classification='no_progress')
        fresh = SimpleNamespace(**{k:v for k,v in vars(self.c).items() if k!='theory_outcome'})
        self.assertEqual(current_outcome(fresh)['status'],'unknown')
        self.state['proofs']['b']='checked later';self.state['accepted_order'].append('b')
        self.assertIsNone(current_outcome(fresh))

    def test_model_cannot_mark_false_or_rewrite_statement(self):
        for proposal in [
            {'action':'report_unresolved','reason':'false','status':'refuted'},
            {'action':'report_unresolved','reason':'false','entry_ids':['a']},
            {'action':'report_unresolved','reason':'false','entry_ids':['foreign']},
            {'action':'report_unresolved','reason':'false','entry_ids':['b','b']},
            {'action':'report_unresolved','reason':'false','classification':'certified_false'},
            {'action':'report_unresolved','reason':''},
        ]:
            with self.subTest(proposal=proposal),self.assertRaises(ValueError):
                handle_outcome(self.c,proposal,{})
        self.assertFalse(hasattr(self.c,'theory_outcome'))

    def test_completed_theory_cannot_be_stopped_as_unknown(self):
        self.state['status']='sealed'
        with self.assertRaises(ValueError):
            record_unresolved(self.c,reason='unresolved',origin='model')

    def test_receipt_tampering_is_rejected(self):
        result=record_unresolved(self.c,reason='unresolved',origin='model')
        path=Path(result['receipt']['directory'])/'events/000000001.json'
        value=json.loads(path.read_text());value['data']['status']='refuted'
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'chain mismatch'):current_outcome(self.c)

    def _native_controller(self):
        from regen.execution.check_executor import CheckExecutor
        self.c.executor=CheckExecutor(self.c.root/'check-tasks',workers=1)
        self.addCleanup(self.c.executor.close)
        self.c.task_ids=set();self.c.resources=None
        self.c._abort_requested=threading.Event()
        self.c._check_session_active=Mock()
        self.c._theory_readiness=lambda *a,**kw:{'ready_for_proof_action':True}
        self.c._runtime=lambda:[None,None,None,None,object()]
        return {'thread_id':'thread','turn_id':'turn','call_id':'negative-check'}

    def test_refutation_has_separate_receipt_and_no_positive_acceptance(self):
        metadata=self._native_controller();before=deepcopy(self.state)
        with patch('regen.execution.contract_refutation.check_refutation',
                   return_value={'status':'refuted','certificate':{'native_fixture':'separate'}}), patch(
                   'regen.execution.contract_refutation.validate_refutation_certificate',return_value=True) as validate:
            result=handle_outcome(self.c,{'action':'check_refutation','entry_id':'b','proof_body':'by simp'},metadata)
            self.assertEqual(result['status'],'refuted')
            self.assertTrue(result['theory_stop'])
            self.assertEqual(current_outcome(self.c)['status'],'refuted')
            self.state['graph']['nodes'].append({'id':'helper','statement':'lemma helper: \"True\"'})
            self.state['proofs']['helper']='by simp';self.state['accepted_order'].append('helper')
            self.assertEqual(current_outcome(self.c)['status'],'refuted')
            with self.assertRaisesRegex(ValueError,'cannot be replaced'):
                record_unresolved(self.c,reason='ignore the negative proof',origin='model')
            self.state.clear();self.state.update(deepcopy(before))
            self.assertGreaterEqual(validate.call_count,2)
        self.assertEqual(self.state,before)
        self.assertEqual(self.c.executor.get(result['task_id'])['kind'],'refutation')
        self.assertNotIn('b',self.state['proofs'])

    def test_unsafe_negative_proof_is_input_error_before_executor_work(self):
        metadata=self._native_controller()
        with self.assertRaises(ValueError):
            handle_outcome(self.c,{'action':'check_refutation','entry_id':'b','proof_body':'sorry'},metadata)
        self.assertEqual(self.c.executor.records,{})
        self.assertFalse(hasattr(self.c,'theory_outcome'))

    def test_abort_cancels_native_refutation_before_publishing_outcome(self):
        from regen.execution.check_tasks import CheckCancelled, check_cancelled
        from regen.planning.parallel_file_planner import TheoryController
        metadata=self._native_controller();started=threading.Event();escaped=[]
        def native(*args):
            started.set()
            for _ in range(300):
                check_cancelled()
                threading.Event().wait(0.01)
            raise AssertionError('native refutation was not cancelled')
        def call():
            try:
                handle_outcome(self.c,{'action':'check_refutation','entry_id':'b','proof_body':'by simp'},metadata)
            except BaseException as e:escaped.append(e)
        with patch('regen.execution.contract_refutation.check_refutation',side_effect=native):
            thread=threading.Thread(target=call);thread.start()
            self.assertTrue(started.wait(2))
            TheoryController.abort_pending(self.c)
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(escaped),1)
        self.assertIsInstance(escaped[0],CheckCancelled)
        self.assertFalse(hasattr(self.c,'theory_outcome'))
        self.assertTrue(all(r['status']=='cancelled' for r in self.c.executor.records.values()))

    def _legacy_completed_refutation(self, metadata):
        """The old executor completed before the controller wrote its outcome."""
        from regen.execution.proof_commits import digest
        from regen.planning.theory_outcomes import _snapshot
        binding = _snapshot(self.c)[2]
        task_id = digest({'kind':'refutation', 'binding':binding, 'call':metadata})
        row = self.c.executor.submit(kind='refutation', theory='T', binding=binding,
            request={'proposal':{'action':'check_refutation', 'entry_id':'b', 'proof_body':'by simp'},
                     'metadata':metadata}, owner='T', task_id=task_id,
            execute=lambda directory:{'status':'refuted','certificate':{'native_fixture':'separate'}})
        while row['status'] not in {'completed', 'cancelled', 'error'}:
            row = self.c.executor.wait([task_id], timeout=2)[0]
        self.assertEqual(row['status'], 'completed')
        return row

    def test_completed_negative_task_recovers_after_restart_once_without_native_work(self):
        metadata = self._native_controller()
        row = self._legacy_completed_refutation(metadata)
        self.c.executor.close()
        # The coordinator's overview has neither a per-theory executor nor an
        # active theory, unlike a live File Planner controller.
        fresh = SimpleNamespace(root=self.c.root, run=self.c.run, _state=self.c._state)
        before = deepcopy(self.state)
        with patch('regen.execution.contract_refutation.check_refutation') as native, patch(
                'regen.execution.contract_refutation.validate_refutation_certificate', return_value=True):
            result = current_outcome(fresh, 'T')
            self.assertEqual(result['status'], 'refuted')
            self.assertEqual(result['details']['task_id'], row['task_id'])
            self.assertTrue(result['details']['recovered_from_completed_task'])
            self.assertEqual(current_outcome(fresh, 'T'), result)
            native.assert_not_called()
        self.assertEqual(len(list((Path(result['receipt']['directory'])/'events').glob('*.json'))), 1)
        self.assertEqual(self.state, before)

    def test_recovery_ignores_foreign_binding_and_rejects_corrupt_completion(self):
        metadata = self._native_controller()
        row = self._legacy_completed_refutation(metadata)
        with patch('regen.execution.contract_refutation.validate_refutation_certificate', return_value=True):
            self.c.run['implementation']['frozen'] = 'changed'
            self.assertIsNone(current_outcome(self.c))
            self.c.run['implementation']['frozen'] = 'hash'
            path = self.c.executor.root/row['task_id']/'completion.json'
            completion = json.loads(path.read_text())
            completion['result_sha256'] = 'corrupted'
            path.write_text(json.dumps(completion))
            with self.assertRaisesRegex(ValueError, 'result binding mismatch'):
                current_outcome(self.c)
        self.assertFalse(hasattr(self.c, 'theory_outcome'))

    def test_incomplete_or_cancelled_negative_tasks_never_recover_an_outcome(self):
        metadata = self._native_controller()
        row = self._legacy_completed_refutation(metadata)
        path = self.c.executor.root/row['task_id']/'task.json'
        saved = json.loads(path.read_text())
        with patch('regen.execution.contract_refutation.validate_refutation_certificate') as validate:
            for status in ('running', 'interrupted', 'cancelled', 'error'):
                with self.subTest(status=status):
                    path.write_text(json.dumps({**saved, 'status':status}))
                    self.assertIsNone(current_outcome(self.c))
            validate.assert_not_called()
        self.assertFalse(hasattr(self.c, 'theory_outcome'))

    def test_completed_task_requires_certificate_revalidation_before_recovery(self):
        metadata = self._native_controller()
        self._legacy_completed_refutation(metadata)
        with patch('regen.execution.contract_refutation.validate_refutation_certificate',
                   side_effect=ValueError('certificate changed')):
            with self.assertRaisesRegex(ValueError, 'certificate changed'):
                current_outcome(self.c)
        self.assertFalse(hasattr(self.c, 'theory_outcome'))
        self.assertFalse((self.c.root/'theory-outcomes').exists())

    def test_abort_after_native_success_before_acceptance_publishes_nothing(self):
        from regen.execution.check_tasks import CheckCancelled
        from regen.planning.parallel_file_planner import TheoryController
        metadata = self._native_controller()
        validating = threading.Event(); release = threading.Event(); escaped = []
        def validate(*args):
            validating.set()
            if not release.wait(2):
                raise AssertionError('validation test barrier timed out')
        def call():
            try:
                handle_outcome(self.c, {'action':'check_refutation', 'entry_id':'b', 'proof_body':'by simp'}, metadata)
            except BaseException as error:
                escaped.append(error)
        with patch('regen.execution.contract_refutation.check_refutation',
                   return_value={'status':'refuted','certificate':{'native_fixture':'separate'}}), patch(
                   'regen.execution.contract_refutation.validate_refutation_certificate', side_effect=validate):
            thread = threading.Thread(target=call); thread.start()
            try:
                self.assertTrue(validating.wait(2))
                TheoryController.abort_pending(self.c)
            finally:
                release.set(); thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(escaped), 1)
        self.assertIsInstance(escaped[0], CheckCancelled)
        self.assertIsNone(current_outcome(self.c))
        self.assertFalse(hasattr(self.c, 'theory_outcome'))

    def test_accepted_refutation_survives_concurrent_cancellation(self):
        from regen.planning import theory_outcomes
        from regen.planning.parallel_file_planner import TheoryController
        metadata = self._native_controller()
        published = threading.Event(); release = threading.Event(); results = []; escaped = []
        original_publish = theory_outcomes._publish
        def publish(*args, **kwargs):
            result = original_publish(*args, **kwargs)
            published.set()
            if not release.wait(2):
                raise AssertionError('publication test barrier timed out')
            return result
        def call():
            try:
                results.append(handle_outcome(self.c,
                    {'action':'check_refutation', 'entry_id':'b', 'proof_body':'by simp'}, metadata))
            except BaseException as error:
                escaped.append(error)
        with patch('regen.execution.contract_refutation.check_refutation',
                   return_value={'status':'refuted','certificate':{'native_fixture':'separate'}}), patch(
                   'regen.execution.contract_refutation.validate_refutation_certificate', return_value=True), patch(
                   'regen.planning.theory_outcomes._publish', side_effect=publish):
            thread = threading.Thread(target=call); thread.start()
            cancelling = None
            try:
                self.assertTrue(published.wait(2))
                cancelling = threading.Thread(target=TheoryController.abort_pending, args=(self.c,))
                cancelling.start()
                self.assertTrue(self.c._abort_requested.wait(2))
            finally:
                release.set(); thread.join(2)
                if cancelling is not None:
                    cancelling.join(2)
            self.assertFalse(thread.is_alive())
            self.assertFalse(cancelling.is_alive())
            self.assertEqual(escaped, [])
            self.assertEqual(results[0]['status'], 'refuted')
            self.assertEqual(current_outcome(self.c), results[0]['theory_outcome'])

    def test_published_refutation_recovers_when_executor_completion_write_fails(self):
        from regen.execution import check_executor
        from regen.execution.check_tasks import NativePreparationError
        metadata = self._native_controller()
        durable = check_executor.durable
        def fail_result(path, *args, **kwargs):
            if Path(path).name == 'result.json':
                raise OSError('injected crash before executor result')
            return durable(path, *args, **kwargs)
        with patch('regen.execution.contract_refutation.check_refutation',
                   return_value={'status':'refuted','certificate':{'native_fixture':'separate'}}), patch(
                   'regen.execution.contract_refutation.validate_refutation_certificate', return_value=True), patch(
                   'regen.execution.check_executor.durable', side_effect=fail_result):
            with self.assertRaisesRegex(NativePreparationError, 'injected crash'):
                handle_outcome(self.c, {'action':'check_refutation', 'entry_id':'b', 'proof_body':'by simp'}, metadata)
            fresh = SimpleNamespace(root=self.c.root, run=self.c.run, _state=self.c._state)
            self.assertEqual(current_outcome(fresh, 'T')['status'], 'refuted')
        self.assertTrue(all(row['status'] == 'error' for row in self.c.executor.records.values()))
        self.assertNotIn('b', self.state['proofs'])

    def test_visible_outcome_survives_publication_io_error_and_cancellation(self):
        from regen.infrastructure.experiment_ledger import Ledger
        from regen.execution.check_tasks import NativePreparationError
        metadata = self._native_controller()
        append = Ledger.append
        def append_then_fail(ledger, kind, **data):
            result = append(ledger, kind, **data)
            if kind == 'theory_outcome':
                raise OSError('injected error after immutable publication')
            return result
        with patch('regen.execution.contract_refutation.check_refutation',
                   return_value={'status':'refuted','certificate':{'native_fixture':'separate'}}), patch(
                   'regen.execution.contract_refutation.validate_refutation_certificate', return_value=True), patch.object(
                   Ledger, 'append', append_then_fail):
            with self.assertRaisesRegex(NativePreparationError, 'immutable publication'):
                handle_outcome(self.c, {'action':'check_refutation', 'entry_id':'b', 'proof_body':'by simp'}, metadata)
            token = next(iter(self.c.executor.tokens.values()))
            self.assertTrue(token.published)
            self.assertFalse(token.cancel())
            self.assertEqual(current_outcome(self.c)['status'], 'refuted')

    def test_negative_red_or_unsupported_never_claims_refuted_or_stops_automatically(self):
        metadata=self._native_controller()
        for index,status in enumerate(['not_refuted','unsupported']):
            with self.subTest(status=status),patch('regen.execution.contract_refutation.check_refutation',
                    return_value={'status':status,'certificate':None}):
                result=handle_outcome(self.c,{'action':'check_refutation','entry_id':'b','proof_body':'by simp'},
                    {**metadata,'call_id':str(index)})
                self.assertFalse(result['theory_stop'])
                self.assertFalse(hasattr(self.c,'theory_outcome'))
                self.assertIsNone(current_outcome(self.c))

    def test_refutation_infrastructure_failure_cannot_become_an_outcome(self):
        from regen.execution.check_tasks import NativePreparationError
        metadata=self._native_controller();before=deepcopy(self.state)
        with patch('regen.execution.contract_refutation.check_refutation',return_value={
                'status':'infrastructure_error','framework_blocked':True,
                'failure_scope':'native_preparation','diagnostic':'native disk full','certificate':None}):
            with self.assertRaisesRegex(NativePreparationError,'native disk full'):
                handle_outcome(self.c,{'action':'check_refutation','entry_id':'b','proof_body':'by simp'},metadata)
        self.assertEqual(before,self.state)
        self.assertIsNone(current_outcome(self.c))
        self.assertFalse(hasattr(self.c,'theory_outcome'))

    def test_outcome_schemas_exclude_model_supplied_truth_or_target(self):
        from regen.planning.document_interface import tool_contracts
        from jsonschema import Draft202012Validator
        schemas=tool_contracts()['source_action_schemas']
        valid={'action':'check_refutation','entry_id':'b','proof_body':'by simp'}
        validator=Draft202012Validator(schemas['check_refutation'])
        self.assertTrue(validator.is_valid(valid))
        self.assertFalse(validator.is_valid({**valid,'negative_statement':'True'}))
        self.assertFalse(validator.is_valid({**valid,'status':'refuted'}))
        self.assertTrue(Draft202012Validator(schemas['report_unresolved']).is_valid(
            {'action':'report_unresolved','reason':'unresolved'}))


if __name__=='__main__': unittest.main()
