"""A missing frozen source revokes current VERO proof validity durably."""
import tempfile
import unittest
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
from unittest.mock import patch

from regen.execution.vero_checker_adapter import VeroCheckerAdapter
from regen.infrastructure.experiment_activity import experiment_summary
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.vero_experiment_observer import VeroObserver
from regen.tools.vero_serial_application import Service


class SourceMissingTests(unittest.TestCase):
    def test_service_observes_missing_original_workspace_before_rendering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);workspace=root/'workspace';workspace.mkdir()
            (workspace/'B.thy').write_text('theory B\nimports A\nbegin\nend\n')
            ledger=Ledger(root/'ledger','missing-original')
            ledger.append('proof_commit',theory='B',nodes=[
                {'uid':'c0002','kind':'contract','theory':'B','status':'green'}])
            class Stop:
                theories={}
                def check(self):pass
                def select(self,theory):pass
            observer=VeroObserver(ledger,Stop(),workspace,
                {'contracts':[{'id':'c0002','module':'B'}]})
            service=object.__new__(Service)
            service.workspace=workspace;service.output=root/'requests'
            service.modules={'A','B'};service.bench=observer.bench
            service.checker=SimpleNamespace(observer=observer)
            service.frozen_plan={'imports':{'A':[],'B':['A']}}
            service.extract=lambda *args:{'slots':{},'file_errors':{'A':'missing'}}
            with patch.object(Service,'verify_plan',return_value=None):
                with self.assertRaisesRegex(ValueError,'checker cannot audit seal readiness'):
                    service.dispatch({'cmd':'begin-seal','module':'B'})
            self.assertEqual(experiment_summary(ledger)['current_valid_contract_ids'],[])
            event=[r for r in ledger.rows if r['kind']=='source_missing']
            self.assertEqual(len(event),1)
            self.assertEqual(event[0]['data']['affected_theories'],['A','B'])

    def test_adapter_records_missing_dependency_before_identity_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);rendered=root/'rendered';rendered.mkdir()
            (rendered/'B.thy').write_text('theory B\nimports A\nbegin\nend\n')
            (rendered/'Vero_Support.thy').write_text(
                'theory Vero_Support\nimports Main\nbegin\nend\n')
            ledger=Ledger(root/'ledger','missing-adapter')
            ledger.append('proof_commit',theory='B',nodes=[
                {'uid':'c0002','kind':'contract','theory':'B','status':'green'}])
            observer=VeroObserver(ledger,object(),root/'workspace',
                {'contracts':[{'id':'c0002','module':'B'}]})
            adapter=object.__new__(VeroCheckerAdapter)
            adapter.lock=RLock();adapter.closed=False;adapter.observer=observer
            adapter.frozen_plan={'imports':{'A':(),'B':('A',)},
                'plan_sha256':'p'*64,'allocation_sha256':'a'*64,
                'imports_sha256':'i'*64}
            with self.assertRaisesRegex(ValueError,'frozen seal closure source absent'):
                adapter.check(rendered,'B')
            self.assertEqual(experiment_summary(ledger)['current_valid_contract_ids'],[])
            self.assertEqual(len([r for r in ledger.rows if r['kind']=='source_missing']),1)

    def test_missing_parent_revokes_parent_and_child_without_erasing_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            ledger=Ledger(root/'ledger','source-missing')
            ledger.append('experiment_started')
            for theory,uid in (('A','c0001'),('B','c0002')):
                ledger.append('proof_commit',theory=theory,nodes=[
                    {'uid':uid,'kind':'contract','theory':theory,'status':'green'}])
                ledger.append('seal_result',theory=theory,status='sealed')

            class Stop:
                theories={}
                def mark_sealed(self,theory):pass
                def invalidate_seal(self,theory,**data):
                    ledger.append('seal_invalidated',theory=theory,**data)

            observer=VeroObserver(ledger,Stop(),root/'workspace',
                {'contracts':[{'id':'c0001','module':'A'},
                              {'id':'c0002','module':'B'}]})
            observer.seals={name:{'source_identity':identity}
                for name,identity in (('A','a'*64),('B','b'*64))}
            missing=observer.source_missing({'B.thy':b'theory B\nend\n'},
                {'A':[],'B':['A']},'B',support_required=False)
            self.assertEqual(missing,['A.thy'])
            self.assertEqual(observer.seals,{})
            events=[r for r in ledger.rows if r['kind']=='source_missing']
            self.assertEqual(len(events),1)
            self.assertEqual(events[0]['data']['affected_theories'],['A','B'])
            result=experiment_summary(ledger)
            self.assertEqual(result['historical_first_green_contract_ids'],
                             ['c0001','c0002'])
            self.assertEqual(result['current_valid_contract_ids'],[])
            self.assertEqual(result['current_valid_sealed_theories'],[])
            self.assertEqual(result['curve'][-1]['contracts_green'],0)


if __name__=='__main__':unittest.main()
