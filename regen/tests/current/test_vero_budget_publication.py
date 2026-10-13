"""VERO accepts proof observations only through an atomic budget boundary."""
from pathlib import Path
import sys
import tempfile
import threading
from types import ModuleType
import unittest
from unittest.mock import patch

from regen.execution.check_tasks import CheckCancelled
from regen.execution.proof_commits import digest
from regen.infrastructure.effective_time_budget import EffectiveTimeBudget
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.vero_experiment_observer import VeroObserver


class VeroBudgetPublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.now=0.
        self.ledger=Ledger(self.root,'publication',clock=lambda:self.now)
        self.stop=EffectiveTimeBudget(self.ledger,1,monotonic=lambda:self.now)
        self.stop.register({'A':['c1']});self.stop.select('A')
        self.addCleanup(self.stop.close)
        self.observer=VeroObserver(self.ledger,self.stop,self.root,
            {'contracts':[{'id':'c1','module':'A'}]})
        self.checker=ModuleType('vero.isabelle.checker')
        self.checker.strip_comments=lambda source:source
        self.modules=patch.dict(sys.modules,{'vero.isabelle.checker':self.checker})
        self.modules.start();self.addCleanup(self.modules.stop)
        self.result={'audited':['c1:prove'],'status':'ok','contract_status':'ok',
            'native_evidence':{'returncode':0},'elapsed_seconds':0.1}
        self.source='lemma c1: True by simp\nlemma vero_aux_small: True by simp'

    def assert_no_publication(self):
        self.assertEqual(self.stop.snapshot()['green_contract_ids'],[])
        self.assertEqual(self.observer.nodes,{})
        self.assertFalse(any(r['kind']=='proof_commit' for r in self.ledger.rows))

    def test_cutoff_crossed_during_source_parsing_rejects_late_green(self):
        def parsing(source):
            self.now=2.
            return source
        self.checker.strip_comments=parsing
        with self.assertRaises(CheckCancelled):
            self.observer.checked('A',self.result,self.source)
        self.assert_no_publication()
        self.assertEqual(self.stop.reason,'effective_time_budget_reached')
        self.assertEqual(self.stop.snapshot()['effective_seconds'],2)

    def test_cutoff_crossed_during_checked_evidence_hashing_also_rejected(self):
        def hashing(value):
            self.now=2.
            return digest(value)
        with patch('regen.infrastructure.vero_experiment_observer.digest',side_effect=hashing):
            with self.assertRaises(CheckCancelled):
                self.observer.checked('A',self.result,self.source)
        self.assert_no_publication()

    def test_cutoff_crossed_during_evaluator_preparation_rejects_late_green(self):
        report={'contracts':{'c1':{'status':'proved'}},'modules':{'A':{'status':'ok'}}}
        def hashing(value):
            self.now=2.
            return digest(value)
        with patch('regen.infrastructure.vero_experiment_observer.digest',side_effect=hashing):
            with self.assertRaises(CheckCancelled):self.observer.evaluated(report)
        self.assert_no_publication()

    def test_authority_is_durable_before_green_observation(self):
        self.now=.5
        self.observer.checked('A',self.result,self.source)
        self.assertEqual(self.stop.snapshot()['green_contract_ids'],['c1'])
        kinds=[r['kind'] for r in self.ledger.rows]
        self.assertLess(kinds.index('proof_commit'),kinds.index('time_budget_green'))
        self.assertEqual(len(self.observer.nodes['A']),2)

    def test_failed_authority_write_does_not_grant_green_or_change_projection(self):
        append=self.ledger.append
        def failure(kind,**data):
            if kind=='proof_commit':raise OSError('injected durable write failure')
            return append(kind,**data)
        with patch.object(self.ledger,'append',side_effect=failure):
            with self.assertRaisesRegex(OSError,'durable write failure'):
                self.observer.checked('A',self.result,self.source)
        self.assert_no_publication()

    def test_watchdog_cannot_interleave_after_admission_before_green(self):
        append=self.ledger.append
        started=threading.Event();cancelled=threading.Event();threads=[]
        def cancel():
            started.set()
            self.stop.cancel('concurrent_operator_pause')
            cancelled.set()
        def contested(kind,**data):
            if kind=='proof_commit':
                thread=threading.Thread(target=cancel);threads.append(thread);thread.start()
                self.assertTrue(started.wait(1))
                self.assertFalse(cancelled.wait(.02),'publication did not hold the shared budget lock')
            return append(kind,**data)
        self.now=.5
        with patch.object(self.ledger,'append',side_effect=contested):
            self.observer.checked('A',self.result,self.source)
            for thread in threads:thread.join(1)
        self.assertTrue(cancelled.is_set())
        kinds=[r['kind'] for r in self.ledger.rows]
        self.assertLess(kinds.index('proof_commit'),kinds.index('time_budget_green'))
        self.assertLess(kinds.index('time_budget_green'),kinds.index('termination'))

    def test_evaluation_changes_projection_with_same_durable_order(self):
        self.now=.2
        report={'contracts':{'c1':{'status':'proved'}},'modules':{'A':{'status':'ok'}}}
        self.observer.evaluated(report)
        kinds=[r['kind'] for r in self.ledger.rows]
        self.assertLess(kinds.index('proof_commit'),kinds.index('time_budget_green'))
        self.now=.3
        self.observer.evaluated({'contracts':{'c1':{'status':'unproved'}},
            'modules':{'A':{'status':'failed'}}})
        self.assertEqual(self.observer.nodes['A'][0]['status'],'open')
        self.assertEqual([r for r in self.ledger.rows if r['kind']=='proof_commit'][-1]['data']['nodes'][0]['status'],'open')


if __name__=='__main__':unittest.main()
