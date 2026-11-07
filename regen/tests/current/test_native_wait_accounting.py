"""Timeout corrections preserve union, model, prefix, and recovery costs."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from regen.infrastructure.effective_time_budget import EffectiveTimeBudget
from regen.tests.current.test_effective_time_budget import MemoryLedger
from regen.execution.native_progress import read_progress
from regen.planning.parallel_file_planner import validate_native_selection

BINDING = {'prefix_sha256':'a'*64, 'request_sha256':'b'*64}


class NativeWaitBudgetTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.
        self.ledger = MemoryLedger()
        self.stage = EffectiveTimeBudget(self.ledger, 1000, monotonic=lambda:self.now)
        self.stage.register({'A':['a'], 'B':['b']})
        self.stage.select('A', worker_id='A')

    def advance(self, n): self.now += n

    def test_timeout_excludes_only_charged_non_model_wait(self):
        s=self.stage; self.advance(5)
        s.begin_native_request('A','r',BINDING);self.advance(10)
        s.set_model_overlap(True,worker_id='A');self.advance(3)
        s.set_model_overlap(False,worker_id='A')
        with s.exclude('prefix_preparation',worker_id='A'):self.advance(20)
        self.advance(2)
        receipt=s.finish_native_request('A','r','watchdog')
        self.assertEqual(receipt['effective_seconds'],12)
        self.assertEqual(s.snapshot()['effective_seconds'],8)
        self.assertEqual(s.snapshot()['raw_effective_seconds'],20)
        s.finish_native_request('A','r','watchdog')
        self.assertEqual(s.snapshot()['effective_seconds'],8)

    def test_success_and_interruption_are_not_refunded(self):
        for outcome in ['completed','interrupted']:
            s=self.stage;s.begin_native_request('A',outcome,BINDING);self.advance(10)
            s.finish_native_request('A',outcome,outcome)
        self.assertEqual(s.snapshot()['effective_seconds'],20)

    def test_other_lane_keeps_union_and_own_cost(self):
        s=self.stage;s.select('B',worker_id='B')
        s.begin_native_request('A','r',BINDING);self.advance(10)
        r=s.finish_native_request('A','r','timeout')
        self.assertEqual(r['effective_seconds'],0)
        self.assertEqual(r['worker_seconds'],10)
        snap=s.snapshot();self.assertEqual(snap['effective_seconds'],10)
        self.assertEqual(snap['worker_seconds'],10)
        self.assertEqual(snap['theories']['B']['effective_seconds'],10)
        self.assertEqual(snap['theories']['A']['effective_seconds'],0)

    def test_two_failed_lanes_refund_union_once(self):
        s=self.stage;s.select('B',worker_id='B')
        s.begin_native_request('A','a',BINDING);s.begin_native_request('B','b',BINDING)
        self.advance(10);s.finish_native_request('A','a','watchdog');s.finish_native_request('B','b','timeout')
        snap=s.snapshot();self.assertEqual(snap['effective_seconds'],0)
        self.assertEqual(snap['worker_seconds'],0)

    def test_prefix_remains_excluded_despite_waiting_message(self):
        s=self.stage
        with s.exclude('prefix_preparation',worker_id='A'):
            s.set_model_overlap(True,worker_id='A');self.advance(100)
            s.set_model_overlap(False,worker_id='A')
        self.assertEqual(s.snapshot()['effective_seconds'],0)

    def test_correction_and_unfinished_request_survive_resume(self):
        s=self.stage;s.begin_native_request('A','old',BINDING);self.advance(10)
        s.finish_native_request('A','old','watchdog')
        s.begin_native_request('A','interrupted',BINDING);self.advance(4);s.close()
        resumed=EffectiveTimeBudget(self.ledger,1000,monotonic=lambda:self.now,
                                   resume_authorization='test recovery')
        self.assertEqual(resumed.snapshot()['effective_seconds'],4)
        self.assertEqual(len(resumed.native_requests.credits),1)
        self.assertFalse(resumed.native_requests.active)

    def test_bad_binding_cannot_open_credit_interval(self):
        with self.assertRaises(ValueError):self.stage.begin_native_request('A','r',{})
        self.assertFalse(self.stage.native_requests.active)


class NativeProgressTests(unittest.TestCase):
    def test_stale_or_foreign_progress_is_not_trusted(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'1.progress.xml').write_text(
                '<native_progress worker="w" request="1" token="old" stage="candidate_execute"/>')
            self.assertFalse(read_progress(p,'1','w','new')['available'])
            self.assertEqual(read_progress(p,'1','w','old')['stage'],'candidate_execute')

    def test_empty_or_duplicate_selection_rejected_before_native_work(self):
        for ids in [[],['a','a'],[''],[None],False]:
            with self.subTest(ids=ids),self.assertRaises(ValueError):
                validate_native_selection({'action':'check_theory','entry_ids':ids})
        validate_native_selection({'action':'check_theory'})

    def test_invalid_selection_never_reaches_runtime_or_imports(self):
        from regen.planning.parallel_file_planner import TheoryController
        controller=object.__new__(TheoryController)
        controller.theory='A';controller._check_session_active=Mock()
        controller._runtime=Mock(side_effect=AssertionError('runtime must not be prepared'))
        controller._apply_pending_imports=Mock(side_effect=AssertionError('imports must not change'))
        with self.assertRaisesRegex(ValueError,'nonempty'):
            controller._submit_task({'theory':'A','kind':'proof_batch',
                'proposal':{'action':'check_theory','entry_ids':[]}}, {})
        controller._runtime.assert_not_called();controller._apply_pending_imports.assert_not_called()


if __name__=='__main__':unittest.main()
