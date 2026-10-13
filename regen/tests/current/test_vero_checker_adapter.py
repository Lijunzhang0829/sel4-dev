import tempfile
import unittest
import hashlib
import json
from pathlib import Path
from threading import RLock
from unittest.mock import Mock, patch
from regen.execution.vero_checker_adapter import VeroCheckerAdapter
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.vero_experiment_observer import VeroObserver


class VeroAdapterTests(unittest.TestCase):
    def adapter(self,root):
        checker=object.__new__(VeroCheckerAdapter)
        checker.root=root/'adapter'
        checker.context=dict(image='test',l4v=root,semantic_workspace=root,
            isabelle=root,base_session='HOL',base_import='Main',base_catalog='/base',
            checker_context_id='ctx')
        checker.timeout=3;checker.resources=Mock();checker.lock=RLock();checker.closed=False
        checker.frozen_plan=None
        return checker

    def test_reject_paths_symlinks_and_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);c=self.adapter(root)
            with self.assertRaises(ValueError):c.check(root,'../Escape')
            (root/'T.thy').symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):c.check(root,'T')
            c.closed=True
            with self.assertRaises(RuntimeError):c.check(root,'T')

    def test_native_failure_cannot_publish_audit_and_timeout_is_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);c=self.adapter(root)
            (root/'T.thy').write_text('theory T imports Main begin end')
            log=root/'native.log';log.write_text('VERO_RESULT c0001 prove clean\n')
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker') as factory:
                factory.return_value._check.return_value={'log':str(log),'returncode':124,'wall_ms':3000}
                result=c.check(root,'T',timeout=99)
                self.assertEqual(result['status'],'timeout')
                self.assertEqual(result['audited'],[])
                self.assertEqual(factory.return_value._check.call_args.kwargs['timeout'],3)
                factory.return_value.close_prefix.assert_called_once()

    def test_real_audit_names_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);c=self.adapter(root)
            (root/'T.thy').write_text('theory T imports Main begin end')
            log=root/'native.log';log.write_text('VERO_RESULT c0001 prove clean\nVERO_RESULT h0002 disprove clean\n')
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker') as factory:
                factory.return_value._check.return_value={'log':str(log),'returncode':0,'wall_ms':1}
                self.assertEqual(c.check(root,'T')['audited'],['c0001:prove','h0002:disprove'])

    def test_failed_final_build_preserves_native_green_but_blocks_integration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);c=self.adapter(root)
            (root/'Vero_All.thy').write_text('theory Vero_All imports Main begin end')
            log=root/'native.log';log.write_text('VERO_RESULT c0001 prove clean\n')
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker') as factory:
                factory.return_value._check.return_value={'log':str(log),'returncode':0,'wall_ms':1000}
                factory.return_value.build_generated_session.return_value={'log':str(log),'returncode':1,'wall_ms':2000}
                result=c.check(root,'Vero_All')
                self.assertEqual(result['status'],'build_error')
                self.assertEqual(result['contract_status'],'ok')
                self.assertEqual(result['seal_status'],'seal_failed')
                self.assertEqual(result['returncode'],0)
                self.assertEqual(result['seal_returncode'],1)
                self.assertEqual(result['audited'],['c0001:prove'])
                self.assertEqual(result['elapsed_seconds'],3)
                factory.return_value.close_prefix.assert_called_once()

    def test_all_contracts_green_auto_seal_and_later_repair_succeeds(self):
        class Stop:
            def __init__(self):self.event=Mock(is_set=Mock(return_value=False));self.calls=[]
            def check(self):pass
            def begin_exclusion(self,reason):self.calls.append(('begin',reason));return 'seal-handle'
            def end_exclusion(self,handle):self.calls.append(('end',handle))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);c=self.adapter(root)
            (root/'T.thy').write_text('theory T imports Main begin end')
            log=root/'native.log';log.write_text('VERO_RESULT c0001 prove clean\nVERO_RESULT c0002 prove clean\n')
            observer=Mock();observer.stop=Stop();observer.ledger=Mock()
            observer.bench={'contracts':[{'id':'c0001','module':'T'},{'id':'c0002','module':'T'}]}
            c.observer=observer
            failed={'log':str(log),'returncode':1,'wall_ms':2000}
            sealed={'log':str(log),'returncode':0,'wall_ms':2000}
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker') as factory:
                factory.return_value._check.return_value={'log':str(log),'returncode':0,'wall_ms':1000}
                factory.return_value.build_generated_session.side_effect=[failed,sealed]
                first=c.check(root,'T')
                second=c.check(root,'T')
            self.assertEqual(first['status'],'build_error')
            self.assertEqual(first['contract_status'],'ok')
            self.assertEqual(first['audited'],['c0001:prove','c0002:prove'])
            self.assertEqual(first['seal_status'],'seal_failed')
            self.assertEqual(second['seal_status'],'sealed')
            self.assertEqual(factory.return_value.build_generated_session.call_count,2)
            self.assertEqual(observer.stop.calls,[('begin','seal'),('end','seal-handle')]*2)
            published=[call.args[1] for call in observer.checked.call_args_list]
            self.assertEqual(len(published),2)
            self.assertTrue(all(r['contract_status']=='ok' and
                r['seal_status']=='not_attempted' and r['build_evidence'] is None
                for r in published))

    def test_successful_seal_is_reused_from_ledger_for_exact_frozen_closure(self):
        class Stop:
            def __init__(self):
                self.event=Mock(is_set=Mock(return_value=False));self.sealed=[]
            def check(self):pass
            def mark_sealed(self,theory):self.sealed.append(theory)
            def begin_exclusion(self,reason):return reason
            def end_exclusion(self,handle):pass
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('A','T','U'):
                (root/(name+'.thy')).write_text('theory '+name+' imports Main begin end')
            log=root/'native.log';log.write_text('VERO_RESULT c0001 prove clean\n')
            ledger=Ledger(root/'ledger','seal-recovery');stop=Stop()
            bench={'contracts':[{'id':'c0001','module':'T'}]}
            c=self.adapter(root)
            c.set_frozen_plan({'imports':{'A':[],'T':['A'],'U':[]},
                'plan_sha256':'p'*64,'imports_sha256':'i'*64,
                'allocation_sha256':'a'*64})
            snapshot={p.name:p.read_bytes() for p in root.glob('*.thy')}
            a_identity=c._seal_source_identity('A',snapshot)
            manifest=root/'A-manifest.json'
            manifest.write_text(json.dumps({name:hashlib.sha256(data).hexdigest()
                for name,data in snapshot.items()}))
            ledger.append('seal_attempt',theory='A',source_identity=a_identity,
                source_manifest=str(manifest))
            ledger.append('seal_result',theory='A',status='sealed',
                source_identity=a_identity,source_manifest=str(manifest),
                evidence={'returncode':0})
            observer=VeroObserver(ledger,stop,root,bench);observer.checked=Mock()
            c.observer=observer
            native={'log':str(log),'returncode':0,'wall_ms':1}
            sealed={'log':str(log),'returncode':0,'wall_ms':2}
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker') as factory:
                factory.return_value._check.return_value=native
                factory.return_value.build_generated_session.return_value=sealed
                first=c.check(root,'T')
                observer2=VeroObserver(ledger,stop,root,bench);observer2.checked=Mock()
                c.observer=observer2
                (root/'U.thy').write_text(
                    'theory U imports Main begin lemma changed: True by simp end')
                second=c.check(root,'T')
                self.assertEqual(factory.return_value.build_generated_session.call_count,1)
                self.assertFalse(first['seal_reused']);self.assertTrue(second['seal_reused'])
                self.assertEqual(second['seal_status'],'sealed')
                (root/'A.thy').write_text(
                    'theory A imports Main begin lemma changed: True by simp end')
                with self.assertRaisesRegex(ValueError,'own/support source changed'):
                    c.check(root,'T')
            self.assertGreaterEqual(stop.sealed.count('T'),2)
            self.assertEqual(sum(r['kind']=='seal_attempt' and r['data']['theory']=='T'
                for r in ledger.rows),1)
            self.assertEqual(sum(r['kind']=='seal_result' and r['data']['theory']=='T'
                for r in ledger.rows),1)
            self.assertEqual(sum(r['kind']=='sealed' for r in ledger.rows),1)
            self.assertEqual(sum(r['kind']=='seal_reused' for r in ledger.rows),1)

    def test_corrupt_successful_seal_receipt_is_rejected_on_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ledger=Ledger(root/'ledger','bad-seal')
            ledger.append('seal_result',theory='T',status='sealed',
                source_identity='a'*64,evidence={'returncode':1})
            stop=Mock();stop.theories={}
            with self.assertRaisesRegex(ValueError,'successful build evidence'):
                VeroObserver(ledger,stop,root,{'contracts':[]})

    def test_legacy_child_seal_is_superseded_when_unsealed_import_changes(self):
        class Stop:
            def __init__(self,ledger):
                self.ledger=ledger;self.event=Mock(is_set=Mock(return_value=False))
                self.theories={'A':{},'T':{}}
            def check(self):pass
            def mark_sealed(self,theory):pass
            def invalidate_seal(self,theory,**data):
                self.ledger.append('seal_invalidated',theory=theory,**data)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('A','T'):
                (root/(name+'.thy')).write_text('theory '+name+' imports Main begin end')
            c=self.adapter(root)
            c.set_frozen_plan({'imports':{'A':[],'T':['A']},
                'plan_sha256':'p'*64,'imports_sha256':'i'*64,
                'allocation_sha256':'a'*64})
            original={p.name:p.read_bytes() for p in root.glob('*.thy')}
            identity=c._seal_source_identity('T',original)
            manifest=root/'old-manifest.json'
            manifest.write_text(json.dumps({name:hashlib.sha256(data).hexdigest()
                for name,data in original.items()}))
            ledger=Ledger(root/'ledger','legacy-child')
            ledger.append('seal_attempt',theory='T',source_identity=identity,
                source_manifest=str(manifest))
            ledger.append('seal_result',theory='T',source_identity=identity,
                status='sealed',evidence={'returncode':0})
            stop=Stop(ledger);bench={'contracts':[{'id':'c0001','module':'T'}]}
            observer=VeroObserver(ledger,stop,root,bench);observer.checked=Mock()
            c.observer=observer
            (root/'A.thy').write_text(
                'theory A imports Main begin lemma changed: True by simp end')
            log=root/'native.log';log.write_text('VERO_RESULT c0001 prove clean\n')
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker') as factory:
                factory.return_value._check.return_value={
                    'log':str(log),'returncode':0,'wall_ms':1}
                result=c.check(root,'T')
                factory.return_value.build_generated_session.assert_not_called()
            self.assertEqual(result['seal_status'],'waiting_for_imports')
            self.assertEqual(result['status'],'blocked_dependency')
            self.assertEqual(result['certification_blocked_by'],['A'])
            invalid=[r for r in ledger.rows if r['kind']=='seal_invalidated']
            self.assertEqual(len(invalid),1)
            self.assertEqual(invalid[0]['data']['changed_sources'],['A.thy'])
            self.assertEqual(len([r for r in ledger.rows if r['kind']=='seal_result']),1)
            self.assertEqual(VeroObserver(ledger,stop,root,bench).seals,{})

    def test_native_green_is_published_before_seal_deadline_check(self):
        state={'published':False,'checks':0}
        class Stop:
            def __init__(self):
                self.event=Mock(is_set=Mock(return_value=False));self.calls=[]
            def check(self):
                state['checks']+=1
                if state['checks']>1 and not state['published']:
                    raise AssertionError('seal admission checked before native GREEN publication')
            def begin_exclusion(self,reason):self.calls.append(('begin',reason));return 'seal'
            def end_exclusion(self,handle):self.calls.append(('end',handle))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);c=self.adapter(root)
            (root/'T.thy').write_text('theory T imports Main begin end')
            log=root/'native.log';log.write_text('VERO_RESULT c0001 prove clean\n')
            observer=Mock();observer.stop=Stop();observer.ledger=Mock()
            observer.bench={'contracts':[{'id':'c0001','module':'T'}]}
            observer.checked.side_effect=lambda *_:state.update(published=True)
            c.observer=observer
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker') as factory:
                factory.return_value._check.return_value={'log':str(log),'returncode':0,'wall_ms':1}
                factory.return_value.build_generated_session.return_value={
                    'log':str(log),'returncode':0,'wall_ms':1}
                self.assertEqual(c.check(root,'T')['seal_status'],'sealed')
            self.assertTrue(state['published'])
            self.assertGreaterEqual(state['checks'],2)

    def test_old_activity_stop_without_exclusions_remains_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);c=self.adapter(root)
            (root/'T.thy').write_text('theory T imports Main begin end')
            log=root/'native.log';log.write_text('VERO_RESULT c0001 prove clean\n')
            class Stop:
                event=Mock(is_set=Mock(return_value=False))
                def check(self):pass
            observer=Mock();observer.stop=Stop();observer.ledger=Mock()
            observer.bench={'contracts':[{'id':'c0001','module':'T'}]};c.observer=observer
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker') as factory:
                factory.return_value._check.return_value={'log':str(log),'returncode':0,'wall_ms':1}
                factory.return_value.build_generated_session.return_value={'log':str(log),'returncode':0,'wall_ms':1}
                self.assertEqual(c.check(root,'T')['seal_status'],'sealed')
