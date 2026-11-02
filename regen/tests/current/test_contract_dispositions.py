"""Contract-local dispositions do not stop unrelated fixed proof obligations.

Native transport is an explicitly synthetic fixture; no test claims an Isabelle
proof. The tests exercise real task journals, receipts and certificate replay.
"""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from regen.execution.proof_commits import digest
from regen.planning.theory_outcomes import (
    CONTRACT_POLICY, _publish, _snapshot, contract_dispositions_enabled,
    current_dispositions, current_outcome, handle_outcome, pending_contract_ids,
    record_unresolved, validate_dispositions,
)
from regen.tests.current.test_theory_outcome_continuation import SyntheticNativeChecker


class ContractDispositionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = {'theory':'T', 'contracts':[
            {'contract_id':key, 'exact_statement':f'lemma {key}: "{prop}"',
             'contract_owned_context':[]}
            for key, prop in [('a', 'True'), ('b', 'False'), ('c', 'True')]]}
        self.state = {'status':'generating', 'graph':{'nodes':[
            {'id':c['contract_id'], 'statement':c['exact_statement']}
            for c in self.package['contracts']]}, 'proofs':{'a':'by simp'},
            'accepted_order':['a'], 'rounds':{'b':3}, 'batch_requests':4}
        self.c = SimpleNamespace(root=self.root, theory='T',
            run={'run_id':'new-contract-run', 'contract_outcome_policy':CONTRACT_POLICY,
                 'implementation':{'frozen':'hash'}},
            _state=lambda _: (deepcopy(self.package), deepcopy(self.state)))

    def certificate(self, key='b'):
        from regen.execution.contract_refutation import check_refutation
        contract = next(c for c in self.package['contracts'] if c['contract_id'] == key)
        return check_refutation(SyntheticNativeChecker(self.root, key), contract,
            'by simp', self.root/('negative-'+key))['certificate']

    def native_controller(self):
        from regen.execution.check_executor import CheckExecutor
        self.c.executor = CheckExecutor(self.root/'check-tasks', workers=1)
        self.addCleanup(self.c.executor.close)
        self.c.task_ids = set(); self.c.resources = None
        self.c._abort_requested = threading.Event(); self.c._check_session_active = Mock()
        self.c._theory_readiness = lambda *a, **kw:{'ready_for_proof_action':True}
        checker = SyntheticNativeChecker(self.root, 'b')
        self.c._runtime = lambda:[None, None, None, None, checker]
        return {'thread_id':'thread', 'turn_id':'turn', 'call_id':'negative-b'}

    def refute(self):
        return _publish(self.c, status='refuted', reason='Synthetic native transport fixture',
            origin='native_checker', details={'certificate':self.certificate()}, entry_ids=['b'])

    def unknown(self, ids=('c',)):
        return record_unresolved(self.c, reason='Search budget exhausted for this contract',
            origin='model', entry_ids=list(ids))

    def test_policy_is_explicit_and_unknown_versions_fail_closed(self):
        self.assertFalse(contract_dispositions_enabled({}))
        self.assertTrue(contract_dispositions_enabled(self.c.run))
        with self.assertRaisesRegex(ValueError, 'unsupported'):
            contract_dispositions_enabled({'contract_outcome_policy':'invented'})

    def test_a_proved_b_refuted_c_remains_pending_then_can_be_proved(self):
        before = deepcopy(self.state)
        metadata = self.native_controller(); certificate = self.certificate()
        with patch('regen.execution.contract_refutation.check_refutation',
                   return_value={'status':'refuted', 'certificate':certificate}):
            result = handle_outcome(self.c,
                {'action':'check_refutation', 'entry_id':'b', 'proof_body':'by simp'}, metadata)
        self.assertEqual(self.state, before)
        self.assertFalse(result['theory_stop'])
        self.assertTrue(result['contract_stop'])
        self.assertEqual(result['remaining_contract_ids'], ['c'])
        self.assertIsNone(current_outcome(self.c))
        self.assertFalse(hasattr(self.c, 'theory_outcome'))
        # Simulate a later accepted independent GREEN; the real commit path is
        # covered by controller/native integration tests, not forged here.
        self.state['proofs']['c'] = 'by simp'; self.state['accepted_order'].append('c')
        self.assertEqual(pending_contract_ids(self.c), [])
        self.assertEqual(current_dispositions(self.c)['b']['status'], 'refuted')
        self.assertEqual(self.state['rounds'], before['rounds'])
        self.assertEqual(self.state['batch_requests'], before['batch_requests'])

    def test_unknown_only_stops_explicit_contract_and_survives_unrelated_green(self):
        result = handle_outcome(self.c, {'action':'report_unresolved', 'entry_ids':['b'],
            'reason':'Missing premise suspected', 'classification':'missing_assumptions'}, {})
        self.assertFalse(result['theory_stop'])
        self.assertEqual(result['remaining_contract_ids'], ['c'])
        self.assertEqual(result['contract_disposition']['truth_status'], 'undetermined')
        self.state['proofs']['c'] = 'by simp'; self.state['accepted_order'].append('c')
        fresh = SimpleNamespace(**vars(self.c))
        self.assertEqual(current_dispositions(fresh)['b']['status'], 'unknown')
        self.assertEqual(pending_contract_ids(fresh), [])

    def test_model_must_select_unresolved_targets_explicitly(self):
        with self.assertRaisesRegex(ValueError, 'explicit'):
            handle_outcome(self.c, {'action':'report_unresolved', 'reason':'cannot prove'}, {})
        self.assertEqual(current_dispositions(self.c), {})

    def test_multiple_independent_dispositions_do_not_overwrite_each_other(self):
        first = self.refute(); second = self.unknown()
        outcomes = current_dispositions(self.c)
        self.assertEqual(set(outcomes), {'b', 'c'})
        self.assertEqual(outcomes['b'], first); self.assertEqual(outcomes['c'], second)
        self.assertEqual(second['receipt']['sequence'], 2)
        self.assertEqual(second['receipt']['parent'], first['receipt']['event_id'])
        self.assertIsNone(current_outcome(self.c))
        with self.assertRaisesRegex(ValueError, 'terminal disposition'):
            self.unknown(('b',))

    def test_no_disposition_can_overlap_a_positive_proof(self):
        outcome = self.unknown(('b',))
        self.state['proofs']['b'] = 'not an authorized retry'
        with self.assertRaisesRegex(ValueError, 'positive proof'):
            current_dispositions(self.c)
        with self.assertRaisesRegex(ValueError, 'positive proof'):
            validate_dispositions(self.package, self.state, {'b':outcome})

    def test_unknown_group_receipt_cannot_be_partially_omitted(self):
        outcome = self.unknown(('b', 'c'))
        self.assertTrue(validate_dispositions(self.package, self.state, {'b':outcome, 'c':outcome}))
        with self.assertRaisesRegex(ValueError, 'omitted'):
            validate_dispositions(self.package, self.state, {'b':outcome})

    def test_self_contained_export_validation_binds_receipt_statement_and_imports(self):
        outcome = self.refute()
        self.assertTrue(validate_dispositions(self.package, self.state, {'b':outcome},
            expected_imports=['Main'], expected_binding=_snapshot(self.c)[2]))
        with self.assertRaisesRegex(ValueError, 'import binding'):
            validate_dispositions(self.package, self.state, {'b':outcome}, expected_imports=['Foreign'])
        changed = deepcopy(self.package); changed['contracts'][1]['exact_statement'] = 'lemma b: "True"'
        with self.assertRaisesRegex(ValueError, 'statement/context'):
            validate_dispositions(changed, self.state, {'b':outcome})
        changed = deepcopy(outcome); changed['reason'] = 'changed after publication'
        with self.assertRaisesRegex(ValueError, 'receipt hash'):
            validate_dispositions(self.package, self.state, {'b':changed})
        binding = _snapshot(self.c)[2]; binding['run_id'] = 'foreign'
        with self.assertRaisesRegex(ValueError, 'binding mismatch'):
            validate_dispositions(self.package, self.state, {'b':outcome}, expected_binding=binding)

    def test_disposition_journal_tampering_fails_before_recovery(self):
        outcome = self.unknown()
        path = Path(outcome['receipt']['directory'])/'events/000000001.json'
        event = json.loads(path.read_text()); event['data']['status'] = 'refuted'
        path.write_text(json.dumps(event))
        with self.assertRaisesRegex(ValueError, 'chain mismatch'):
            current_dispositions(self.c)

    def test_repeat_refutation_does_not_repeat_native_check_or_publish(self):
        metadata = self.native_controller(); certificate = self.certificate()
        proposal = {'action':'check_refutation', 'entry_id':'b', 'proof_body':'by simp'}
        with patch('regen.execution.contract_refutation.check_refutation',
                   return_value={'status':'refuted', 'certificate':certificate}) as native:
            first = handle_outcome(self.c, proposal, metadata)
            count = len(self.c.executor.records)
            second = handle_outcome(self.c, proposal, {**metadata, 'call_id':'second'})
            self.assertEqual(native.call_count, 1)
        self.assertEqual(second['contract_disposition'], first['contract_disposition'])
        self.assertTrue(second['retained_from_prior_invocation'])
        self.assertEqual(len(self.c.executor.records), count)
        self.assertFalse(second['theory_stop'])

    def test_completed_native_task_recovers_once_after_unrelated_positive_progress(self):
        metadata = self.native_controller(); binding = _snapshot(self.c)[2]
        certificate = self.certificate()
        task_id = digest({'kind':'refutation', 'binding':binding, 'call':metadata})
        row = self.c.executor.submit(kind='refutation', theory='T', binding=binding,
            request={'proposal':{'action':'check_refutation', 'entry_id':'b', 'proof_body':'by simp'},
                     'metadata':metadata}, owner='T', task_id=task_id,
            execute=lambda _: {'status':'refuted', 'certificate':certificate})
        while row['status'] not in {'completed', 'error', 'cancelled'}:
            row = self.c.executor.wait([task_id], timeout=2)[0]
        self.assertEqual(row['status'], 'completed')
        self.c.executor.close()
        self.state['proofs']['c'] = 'by simp'; self.state['accepted_order'].append('c')
        fresh = SimpleNamespace(root=self.root, run=self.c.run, _state=self.c._state)
        with patch('regen.execution.contract_refutation.check_refutation') as native:
            result = current_dispositions(fresh, 'T')
            self.assertEqual(current_dispositions(fresh, 'T'), result)
            native.assert_not_called()
        self.assertEqual(result['b']['details']['task_id'], task_id)
        self.assertTrue(result['b']['details']['recovered_from_completed_task'])
        events = Path(result['b']['receipt']['directory'])/'events'
        self.assertEqual(len(list(events.glob('*.json'))), 1)

    def test_dispositions_remain_observable_after_partial_export(self):
        outcome = self.refute(); self.unknown()
        self.state['status'] = 'exported_partial'
        self.assertEqual(current_dispositions(self.c)['b'], outcome)
        with self.assertRaisesRegex(ValueError, 'completed theory'):
            self.unknown(('b',))

    def test_native_origin_cannot_be_claimed_by_unknown_or_missing_certificate(self):
        with self.assertRaises(ValueError):
            _publish(self.c, status='refuted', reason='unsupported claim', origin='model',
                details={}, entry_ids=['b'])
        with self.assertRaises(ValueError):
            _publish(self.c, status='refuted', reason='no certificate', origin='native_checker',
                details={}, entry_ids=['b'])
        self.assertEqual(current_dispositions(self.c), {})

    def test_cancel_before_native_acceptance_does_not_dispose_contract(self):
        from regen.execution.check_tasks import CheckCancelled
        from regen.planning.parallel_file_planner import TheoryController
        metadata = self.native_controller(); certificate = self.certificate()
        validating, release = threading.Event(), threading.Event()
        errors = []
        def barrier(*args, **kwargs):
            validating.set()
            if not release.wait(2):
                raise AssertionError('validation barrier timed out')
        def call():
            try:
                handle_outcome(self.c,
                    {'action':'check_refutation', 'entry_id':'b', 'proof_body':'by simp'}, metadata)
            except BaseException as error:
                errors.append(error)
        with patch('regen.execution.contract_refutation.check_refutation',
                   return_value={'status':'refuted', 'certificate':certificate}), patch(
                   'regen.execution.contract_refutation.validate_refutation_certificate', side_effect=barrier):
            worker = threading.Thread(target=call); worker.start()
            try:
                self.assertTrue(validating.wait(2))
                TheoryController.abort_pending(self.c)
            finally:
                release.set(); worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1); self.assertIsInstance(errors[0], CheckCancelled)
        self.assertEqual(current_dispositions(self.c), {})

    def test_published_disposition_survives_task_completion_write_failure(self):
        from regen.execution import check_executor
        from regen.execution.check_tasks import NativePreparationError
        metadata = self.native_controller(); certificate = self.certificate()
        durable = check_executor.durable
        def fail_result(path, *args, **kwargs):
            if Path(path).name == 'result.json':
                raise OSError('injected completion write failure')
            return durable(path, *args, **kwargs)
        with patch('regen.execution.contract_refutation.check_refutation',
                   return_value={'status':'refuted', 'certificate':certificate}), patch(
                   'regen.execution.check_executor.durable', side_effect=fail_result):
            with self.assertRaisesRegex(NativePreparationError, 'completion write'):
                handle_outcome(self.c,
                    {'action':'check_refutation', 'entry_id':'b', 'proof_body':'by simp'}, metadata)
        fresh = SimpleNamespace(root=self.root, run=self.c.run, _state=self.c._state)
        result = current_dispositions(fresh, 'T')
        self.assertEqual(result['b']['status'], 'refuted')
        self.assertEqual(pending_contract_ids(fresh, 'T'), ['c'])
        self.assertTrue(all(row['status'] == 'error' for row in self.c.executor.records.values()))

    def test_visible_publication_preserves_acceptance_after_ledger_io_error(self):
        from regen.execution.check_tasks import NativePreparationError
        from regen.infrastructure.experiment_ledger import Ledger
        metadata = self.native_controller(); certificate = self.certificate()
        append = Ledger.append
        def append_then_fail(ledger, kind, **data):
            result = append(ledger, kind, **data)
            if kind == 'contract_disposition':
                raise OSError('injected error after immutable disposition')
            return result
        with patch('regen.execution.contract_refutation.check_refutation',
                   return_value={'status':'refuted', 'certificate':certificate}), patch.object(
                   Ledger, 'append', append_then_fail):
            with self.assertRaisesRegex(NativePreparationError, 'immutable disposition'):
                handle_outcome(self.c,
                    {'action':'check_refutation', 'entry_id':'b', 'proof_body':'by simp'}, metadata)
        token = next(iter(self.c.executor.tokens.values()))
        self.assertTrue(token.published); self.assertFalse(token.cancel())
        self.assertEqual(current_dispositions(self.c)['b']['status'], 'refuted')


if __name__ == '__main__':
    unittest.main()
