"""Evidence-bound native outage accounting; no models or native processes."""
import tempfile
import unittest
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.normal_runtime_budget import NormalRuntimeBudget
from regen.infrastructure.parallel_generation_budget import ParallelGenerationBudgetBridge

BINDING={'prefix_sha256':'a'*64,'request_sha256':'b'*64,'worker':'native-worker','request':'1','action':'check','imports_sha256':'c'*64}
def receipt(uid='r',**changes):
    return {'policy':'native-no-response-after-cleanup-v1','request_id':uid,
            'worker':'native-worker','prefix_sha256':'a'*64,'request_sha256':'b'*64,
            'imports_sha256':'c'*64,'response_missing':True,'native_response_received':False,'worker_discarded':True,**changes}

class NativeInterruptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.now=0.;self.ledger=Ledger(self.tmp.name,'normal-native',clock=lambda:self.now)
        self.s=NormalRuntimeBudget(self.ledger,1000,monotonic=lambda:self.now)
        self.addCleanup(self.s.close);self.s.register({'A':['a'],'B':['b']});self.s.select('A',worker_id='A')

    def test_single_worker_wait_and_matching_rebuild_excluded_once(self):
        s=self.s;self.now=5;s.begin_native_request('A','r',BINDING)
        self.now=315;r=s.finish_native_request('A','r','watchdog',interruption_evidence=receipt())
        self.assertEqual(r['effective_seconds'],310)
        self.assertEqual(s.snapshot()['effective_seconds'],5)
        s.finish_native_request('A','r','watchdog',interruption_evidence=receipt())
        self.now=320
        h=s.begin_exclusion('prefix_preparation',worker_id='A',prefix_sha256='a'*64,imports_sha256='c'*64)
        self.now=420;s.end_exclusion(h);self.now=425
        self.assertEqual(s.snapshot()['effective_seconds'],15)
        self.assertEqual(s.snapshot()['excluded_experiment_seconds'],410)
        h=s.begin_exclusion('prefix_preparation',worker_id='A',prefix_sha256='a'*64,imports_sha256='c'*64)
        self.now=435;s.end_exclusion(h)
        self.assertEqual(s.snapshot()['effective_seconds'],25)

    def test_native_timeout_and_unconfirmed_watchdog_stay_charged(self):
        for i,(outcome,evidence) in enumerate([('timeout',receipt()),('watchdog',None),
                ('watchdog',receipt('2',response_missing=False,native_response_received=True)),
                ('watchdog',receipt('3',worker_discarded=False))]):
            uid=str(i);self.s.begin_native_request('A',uid,BINDING);self.now+=10
            self.s.finish_native_request('A',uid,outcome,interruption_evidence=evidence)
        self.assertEqual(self.s.snapshot()['effective_seconds'],40)

    def test_model_overlap_is_retained_and_healthy_peer_keeps_union(self):
        s=self.s;s.begin_native_request('A','r',BINDING);self.now=10
        s.set_model_overlap(True,worker_id='A');self.now=20
        s.set_model_overlap(False,worker_id='A');s.select('B',worker_id='B');self.now=30
        s.finish_native_request('A','r','watchdog',interruption_evidence=receipt())
        state=s.snapshot();self.assertEqual(state['effective_seconds'],20)
        self.assertEqual(state['worker_seconds'],20)
        self.assertEqual(state['theories']['B']['effective_seconds'],10)
        h=s.begin_exclusion('prefix_preparation',worker_id='A',prefix_sha256='a'*64,imports_sha256='c'*64)
        self.now=40;s.end_exclusion(h)
        self.assertEqual(s.snapshot()['effective_seconds'],30)

    def test_confirmed_failure_overlap_is_not_double_credited(self):
        s=self.s;s.begin_native_request('A','r',BINDING);self.now=10
        h=s.begin_exclusion('provider_interruption',worker_id='A',confirmed=True,evidence_id='failure')
        self.now=20;s.end_exclusion(h);self.now=30
        s.finish_native_request('A','r','watchdog',interruption_evidence=receipt())
        self.assertEqual(s.snapshot()['effective_seconds'],0)
        self.assertEqual(s.snapshot()['excluded_experiment_seconds'],30)

    def test_changed_prefix_recovery_is_normal_work_and_resume_preserves_credit(self):
        s=self.s;s.begin_native_request('A','r',BINDING);self.now=10
        s.finish_native_request('A','r','watchdog',interruption_evidence=receipt())
        s.cancel('operator_pause');s.close();self.now=100
        resumed=NormalRuntimeBudget(self.ledger,1000,monotonic=lambda:self.now,resume_authorization='resume')
        self.addCleanup(resumed.close);resumed.select('A',worker_id='A')
        self.assertEqual(resumed.snapshot()['effective_seconds'],0)
        h=resumed.begin_exclusion('prefix_preparation',worker_id='A',prefix_sha256='c'*64)
        self.now=110;resumed.end_exclusion(h)
        self.assertEqual(resumed.snapshot()['effective_seconds'],10)

    def test_real_bridge_receipt_and_recovery_phase_bindings(self):
        bridge=ParallelGenerationBudgetBridge(self.s);self.addCleanup(bridge.close)
        bridge.handle('native_request_started',{'theory':'A','request_id':'r','binding':BINDING})
        self.now=10
        bridge.handle('native_request_finished',{'theory':'A','request_id':'r','outcome':'watchdog',
                                                'interruption_evidence':receipt()})
        bridge.handle('generation_exclusion_started',{'theory':'A','category':'prefix_preparation',
                                                      'phase_id':'prefix','prefix_sha256':'a'*64,'imports_sha256':'c'*64})
        self.now=20
        bridge.handle('generation_exclusion_finished',{'theory':'A','phase_id':'prefix'})
        self.assertEqual(self.s.snapshot()['effective_seconds'],0)

if __name__=='__main__':unittest.main()

class NativeResponseBoundaryTests(unittest.TestCase):
    def worker(self, root):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from regen.execution.batch_checker import BatchWorker
        w=object.__new__(BatchWorker);w.root=root;w.name='test-native';w.index=0;w.base=''
        w.closed=False;w.checker=SimpleNamespace(timeout=1,batch_candidate_timeout=1)
        w.process=Mock();w.process.poll.return_value=None
        (root/'worker.log').write_text('native log')
        return w

    def test_native_timeout_published_during_cleanup_is_not_missing_response(self):
        from pathlib import Path
        from unittest.mock import patch,Mock
        from regen.infrastructure.progress_events import observe_events
        with tempfile.TemporaryDirectory() as tmp:
            w=self.worker(Path(tmp));events=[]
            def close():
                (w.root/'1.response').write_text('TIMEOUT\nCandidate execution and audit exceeded the time budget')
                w.closed=True
            w.close=Mock(side_effect=close)
            ticks=iter([0,2,3])
            with observe_events(lambda name,fields:events.append((name,fields))),patch(
                    'regen.execution.batch_checker.time.monotonic',side_effect=lambda:next(ticks)):
                answer,_=w.transaction('check','candidate',timeout=1)
            self.assertTrue(answer.startswith('TIMEOUT\n'))
            completion=next(v for k,v in events if k=='native_request_finished')
            self.assertEqual(completion['outcome'],'timeout')
            self.assertIsNone(completion['interruption_evidence'])

    def test_no_response_after_cleanup_emits_bound_evidence(self):
        from pathlib import Path
        from unittest.mock import patch,Mock
        from regen.infrastructure.progress_events import observe_events
        with tempfile.TemporaryDirectory() as tmp:
            w=self.worker(Path(tmp));events=[]
            w.close=Mock(side_effect=lambda:setattr(w,'closed',True))
            ticks=iter([0,2])
            with observe_events(lambda name,fields:events.append((name,fields))),patch(
                    'regen.execution.batch_checker.time.monotonic',side_effect=lambda:next(ticks)):
                with self.assertRaises(TimeoutError):w.transaction('check','candidate',timeout=1)
            completion=next(v for k,v in events if k=='native_request_finished')
            self.assertEqual(completion['outcome'],'watchdog')
            evidence=completion['interruption_evidence']
            self.assertTrue(evidence['response_missing']);self.assertTrue(evidence['worker_discarded'])
            self.assertEqual(evidence['request_id'],completion['request_id'])

    def test_late_green_cannot_be_accepted_or_claimed_missing(self):
        from pathlib import Path
        from unittest.mock import patch,Mock
        from regen.infrastructure.progress_events import observe_events
        with tempfile.TemporaryDirectory() as tmp:
            w=self.worker(Path(tmp));events=[]
            def close():
                (w.root/'1.response').write_text('GREEN');w.closed=True
            w.close=Mock(side_effect=close);ticks=iter([0,2])
            with observe_events(lambda name,fields:events.append((name,fields))),patch(
                    'regen.execution.batch_checker.time.monotonic',side_effect=lambda:next(ticks)):
                with self.assertRaises(TimeoutError):w.transaction('check','candidate',timeout=1)
            completion=next(v for k,v in events if k=='native_request_finished')
            self.assertFalse(completion['interruption_evidence']['response_missing'])

class NativeContextBoundaryTests(unittest.TestCase):
    setUp = NativeInterruptionTests.setUp
    def test_same_prefix_with_changed_imports_is_not_outage_recovery(self):
        s=self.s;s.begin_native_request('A','r',BINDING);self.now=10
        s.finish_native_request('A','r','watchdog',interruption_evidence=receipt())
        h=s.begin_exclusion('prefix_preparation',worker_id='A',prefix_sha256='a'*64,imports_sha256='d'*64)
        self.now=20;s.end_exclusion(h)
        self.assertEqual(s.snapshot()['effective_seconds'],10)

    def test_model_work_during_bound_recovery_remains_charged(self):
        s=self.s;s.begin_native_request('A','r',BINDING);self.now=10
        s.finish_native_request('A','r','watchdog',interruption_evidence=receipt())
        h=s.begin_exclusion('prefix_preparation',worker_id='A',prefix_sha256='a'*64,imports_sha256='c'*64)
        self.now=20;s.set_model_overlap(True,worker_id='A');self.now=30
        s.set_model_overlap(False,worker_id='A');self.now=40;s.end_exclusion(h)
        self.assertEqual(s.snapshot()['effective_seconds'],10)
        self.assertEqual(s.snapshot()['excluded_experiment_seconds'],30)
