import tempfile
import unittest
import hashlib
import json
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
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

    def test_contract_receipt_is_stable_across_later_sibling_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);c=self.adapter(root)
            c.observer=SimpleNamespace(bench={'contracts':[
                {'id':'c0001','module':'T'}, {'id':'c0002','module':'T'}]})
            c.set_frozen_plan({'imports':{'A':[],'T':['A']},
                'plan_sha256':'p'*64,'imports_sha256':'i'*64,
                'allocation_sha256':'a'*64})
            first=('theory T imports Vero_Support A begin\n'
                '(* VERO START c0001 prove *)\nby simp\n'
                '(* VERO END c0001 prove *)\n'
                '(* VERO START c0001 disprove *)\noops\n'
                '(* VERO END c0001 disprove *)\n')
            second=('(* VERO START c0002 prove *)\nby simp\n'
                '(* VERO END c0002 prove *)\n'
                '(* VERO START c0002 disprove *)\noops\n'
                '(* VERO END c0002 disprove *)\nend\n')
            snapshot={'A.thy':b'theory A imports Main begin end',
                'Vero_Support.thy':b'theory Vero_Support imports Main begin end',
                'T.thy':(first+second).encode()}
            imports={'A':(),'T':('A',)}
            original=c._contract_receipts('T',snapshot,imports)
            later={**snapshot,'T.thy':(first+second.replace('by simp','by blast')).encode()}
            changed=c._contract_receipts('T',later,imports)
            self.assertEqual(original['c0001'],changed['c0001'])
            self.assertNotEqual(original['c0002']['proof_body_sha256'],
                changed['c0002']['proof_body_sha256'])
            self.assertEqual(original['c0001']['proof_body_sha256'],
                hashlib.sha256(b'by simp').hexdigest())
            edited_import={**snapshot,'A.thy':snapshot['A.thy']+b'\nlemma changed: True by simp'}
            affected=c._contract_receipts('T',edited_import,imports)
            self.assertEqual(original['c0001']['prefix_sha256'],
                affected['c0001']['prefix_sha256'])
            self.assertNotEqual(original['c0001']['context_identity'],
                affected['c0001']['context_identity'])

    def test_native_result_rejected_when_raw_workspace_changes_in_flight(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);workspace=root/'workspace';workspace.mkdir()
            immutable=root/'snapshot';immutable.mkdir()
            source=b'theory T imports Main begin end\n'
            (workspace/'T.thy').write_bytes(source)
            (immutable/'T.thy').write_bytes(source)
            c=self.adapter(root)
            observer=Mock();observer.workspace=workspace
            observer.audit_workspace_snapshot=immutable
            observer.bench={'contracts':[{'id':'c0001','module':'T'}]}
            c.observer=observer
            log=root/'native.log';log.write_text('VERO_RESULT c0001 prove clean\n')
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker') as factory:
                def native(*args,**kwargs):
                    (workspace/'T.thy').write_bytes(source+b'(* concurrent edit *)\n')
                    return {'log':str(log),'returncode':0,'wall_ms':1}
                factory.return_value._check.side_effect=native
                with self.assertRaisesRegex(ValueError,'raw workspace changed'):
                    c.check(workspace,'T')
                observer.checked.assert_not_called()
                factory.return_value.build_generated_session.assert_not_called()
            # Missing/new planned files are also mismatches, even when the
            # rendered candidate was created before the raw edit.
            (workspace/'T.thy').unlink()
            with self.assertRaisesRegex(ValueError,'raw workspace changed'):
                c.check(immutable,'T')
            (immutable/'T.thy').unlink()
            (workspace/'T.thy').write_bytes(source)
            with self.assertRaisesRegex(ValueError,'raw workspace changed'):
                c.check(workspace,'T')

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
            def __init__(self,ledger):
                self.event=Mock(is_set=Mock(return_value=False));self.sealed=[]
                self.ledger=ledger
            def check(self):pass
            def mark_sealed(self,theory):self.sealed.append(theory)
            def invalidate_seal(self,theory,**data):
                self.ledger.append('seal_invalidated',theory=theory,**data)
            def begin_exclusion(self,reason):return reason
            def end_exclusion(self,handle):pass
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('A','T','U'):
                (root/(name+'.thy')).write_text('theory '+name+' imports Main begin end')
            log=root/'native.log';log.write_text('VERO_RESULT c0001 prove clean\n')
            ledger=Ledger(root/'ledger','seal-recovery');stop=Stop(ledger)
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
                changed=c.check(root,'T')
                self.assertEqual(changed['seal_status'],'waiting_for_imports')
                self.assertEqual(changed['certification_blocked_by'],['A'])
                self.assertEqual(observer2.seals,{})
                self.assertEqual([(r['data']['theory'],r['data']['reason'])
                    for r in ledger.rows if r['kind']=='seal_invalidated'],
                    [('A','own_source_changed'),('T','import_source_changed')])
            self.assertGreaterEqual(stop.sealed.count('T'),2)
            self.assertEqual(sum(r['kind']=='seal_attempt' and r['data']['theory']=='T'
                for r in ledger.rows),1)
            self.assertEqual(sum(r['kind']=='seal_result' and r['data']['theory']=='T'
                for r in ledger.rows),1)
            self.assertEqual(sum(r['kind']=='sealed' for r in ledger.rows),1)
            self.assertEqual(sum(r['kind']=='seal_reused' for r in ledger.rows),1)

    def test_changed_parent_invalidates_all_sealed_descendants_even_when_checking_sibling(self):
        class Stop:
            def __init__(self,ledger):
                self.ledger=ledger;self.theories={name:{} for name in ('A','B','C','X')}
                self.sealed=set()
            def mark_sealed(self,theory):self.sealed.add(theory)
            def invalidate_seal(self,theory,**data):
                self.ledger.append('seal_invalidated',theory=theory,**data)
                self.sealed.remove(theory)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            imports={'A':[],'B':['A'],'C':['B'],'X':[]}
            for name in imports:
                (root/(name+'.thy')).write_text('theory '+name+' imports Main begin end')
            c=self.adapter(root)
            c.set_frozen_plan({'imports':imports,'plan_sha256':'p'*64,
                'imports_sha256':'i'*64,'allocation_sha256':'a'*64})
            original={p.name:p.read_bytes() for p in root.glob('*.thy')}
            manifest=root/'manifest.json'
            manifest.write_text(json.dumps({name:hashlib.sha256(data).hexdigest()
                for name,data in original.items()}))
            ledger=Ledger(root/'ledger','all-descendants');stop=Stop(ledger)
            for name in ('A','B','C'):
                ledger.append('seal_result',theory=name,status='sealed',
                    source_identity=c._seal_source_identity(name,original),
                    source_manifest=str(manifest),evidence={'returncode':0})
            observer=VeroObserver(ledger,stop,root,{'contracts':[]})
            self.assertEqual(stop.sealed,{'A','B','C'})
            (root/'A.thy').write_text(
                'theory A imports Main begin lemma changed: True by simp end')
            changed={p.name:p.read_bytes() for p in root.glob('*.thy')}
            self.assertEqual(observer.reconcile_seals('X',changed,
                c._seal_source_identity,imports),[])
            invalid=[r['data'] for r in ledger.rows if r['kind']=='seal_invalidated']
            self.assertEqual([(r['theory'],r['reason']) for r in invalid],
                [('A','own_source_changed'),('B','import_source_changed'),
                    ('C','import_source_changed')])
            self.assertTrue(all(r['changed_sources']==['A.thy'] for r in invalid))
            self.assertEqual(stop.sealed,set())
            # Rechecks and journal replay never charge or invalidate twice.
            observer.reconcile_seals('X',changed,c._seal_source_identity,imports)
            recovered=VeroObserver(ledger,stop,root,{'contracts':[]})
            self.assertEqual(recovered.seals,{})
            self.assertEqual(sum(r['kind']=='seal_invalidated' for r in ledger.rows),3)

    def test_support_source_removal_invalidates_receipt_without_losing_history(self):
        class Stop:
            def __init__(self,ledger):
                self.ledger=ledger;self.theories={'A':{}};self.sealed=set()
            def mark_sealed(self,theory):self.sealed.add(theory)
            def invalidate_seal(self,theory,**data):
                self.ledger.append('seal_invalidated',theory=theory,**data)
                self.sealed.remove(theory)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'A.thy').write_text('theory A imports Main begin end')
            (root/'Vero_Support.thy').write_text('theory Vero_Support imports Main begin end')
            c=self.adapter(root)
            c.set_frozen_plan({'imports':{'A':[]},'plan_sha256':'p'*64,
                'imports_sha256':'i'*64,'allocation_sha256':'a'*64})
            original={p.name:p.read_bytes() for p in root.glob('*.thy')}
            manifest=root/'manifest.json'
            manifest.write_text(json.dumps({name:hashlib.sha256(data).hexdigest()
                for name,data in original.items()}))
            ledger=Ledger(root/'ledger','support-removal');stop=Stop(ledger)
            ledger.append('seal_result',theory='A',status='sealed',
                source_identity=c._seal_source_identity('A',original),
                source_manifest=str(manifest),evidence={'returncode':0})
            observer=VeroObserver(ledger,stop,root,{'contracts':[]})
            changed={'A.thy':original['A.thy']}
            observer.reconcile_seals('A',changed,c._seal_source_identity,{'A':[]})
            self.assertEqual(stop.sealed,set())
            invalid=[r['data'] for r in ledger.rows if r['kind']=='seal_invalidated']
            self.assertEqual(len(invalid),1)
            self.assertEqual(invalid[0]['reason'],'support_source_changed')
            self.assertEqual(invalid[0]['changed_sources'],['Vero_Support.thy'])
            self.assertEqual(sum(r['kind']=='seal_result' for r in ledger.rows),1)

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
