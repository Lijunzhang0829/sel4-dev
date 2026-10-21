"""Additive VERO imports participate in GREEN reuse and seal authority."""
import sys
import tempfile
import unittest
import json
from collections import OrderedDict
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
from unittest.mock import patch

from regen.execution.vero_checker_adapter import VeroCheckerAdapter
from regen.execution.vero_import_graph import effective_generated_imports
from regen.infrastructure.experiment_activity import experiment_summary
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.vero_experiment_observer import VeroObserver
from regen.tools.vero_serial_application import Service, bind_runner


VERO_SOURCE=Path(__file__).resolve().parents[3]/'vero-isabelle'/'src'
sys.path.insert(0,str(VERO_SOURCE))
from vero.isabelle.benchmark import digest, render, extract
PARENT='Vero_Dynamic_Parent'
CHILD='Vero_Dynamic_Child'


def theory(name, extra='', proof='by simp'):
    cid='c0001' if name==PARENT else 'c0002'
    return (f'theory {name}\nimports Vero_Support'
        + f'\n(* VERO START {name} imports *)\n{extra}\n'
          f'(* VERO END {name} imports *)\nbegin\n'
          f'lemma local_fact: True {proof}\n'
          f'vero_prove "{cid}" {cid}_prove: True\n'
          f'(* VERO START {cid} prove *)\nby simp\n'
          f'(* VERO END {cid} prove *)\n'
          f'vero_disprove "{cid}" {cid}_disprove: True\n'
          f'(* VERO START {cid} disprove *)\noops\n'
          f'(* VERO END {cid} disprove *)\nend\n')


class Stop:
    def __init__(self,ledger):
        self.ledger=ledger;self.sealed=set()
        self.event=SimpleNamespace(is_set=lambda:False)
    def check(self):pass
    def select(self,theory):pass
    def observe(self,*args,**kwargs):pass
    def mark_sealed(self,theory):self.sealed.add(theory)
    def invalidate_seal(self,theory,**data):
        self.ledger.append('seal_invalidated',theory=theory,**data)
        self.sealed.discard(theory)
    def begin_exclusion(self,reason):return reason
    def end_exclusion(self,handle):pass


class Native:
    checks=[]
    seals=[]
    def __init__(self,run_dir,work_dir,**kwargs):self.work_dir=work_dir
    def _command(self,source,*,session):return []
    def _check(self,source,theory,*,timeout):
        type(self).checks.append(theory)
        log=self.work_dir/(theory+'.log')
        cid='c0001' if theory==PARENT else 'c0002'
        log.write_text(f'VERO_RESULT {cid} prove clean\n')
        return {'returncode':0,'wall_ms':1000,
            'source':str(self.work_dir/(theory+'.thy')),'log':str(log)}
    def build_generated_session(self,theories):
        type(self).seals.append(theories[-1])
        log=self.work_dir/'seal.log';log.write_text('build complete\n')
        return {'returncode':0,'wall_ms':100,'log':str(log)}
    def close_prefix(self):pass


class DynamicImportsTests(unittest.TestCase):
    def setup_adapter(self,root):
        rendered=root/'rendered';rendered.mkdir()
        (rendered/'Vero_Support.thy').write_text(
            'theory Vero_Support\nimports Main\nbegin\nend\n')
        (rendered/(PARENT+'.thy')).write_text(theory(PARENT))
        (rendered/(CHILD+'.thy')).write_text(theory(CHILD,PARENT))
        ledger=Ledger(root/'ledger','dynamic-imports')
        ledger.append('experiment_started')
        stop=Stop(ledger)
        body={'schema_version':2,'backend':'isabelle','mode':'proof',
            'base_import':'Main','contracts':[
                {'id':'c0001','module':PARENT,'statement':'lemma parent: True',
                    'context':[]},
                {'id':'c0002','module':CHILD,'statement':'lemma child: True',
                    'context':[]}],
            'planning':{'imports':{PARENT:[],CHILD:[]}}}
        bench={**body,'benchmark_id':digest(body)}
        observer=VeroObserver(ledger,stop,rendered,bench)
        adapter=object.__new__(VeroCheckerAdapter)
        adapter.root=root/'adapter'
        adapter.context=dict(image='image',l4v=root,semantic_workspace=root,
            isabelle=root,heaps=root,base_session='HOL',base_import='Main',
            base_catalog='/base',checker_context_id='frozen-context')
        adapter.timeout=900
        adapter.resources=SimpleNamespace(policy={'slots':1,'threads':4})
        adapter.lock=RLock();adapter.closed=False
        adapter.observer=observer;adapter.frozen_plan=None
        adapter._exact_green_checks=OrderedDict()
        adapter.set_frozen_plan({'imports':{PARENT:[],CHILD:[]},
            'plan_sha256':'p'*64,'allocation_sha256':'a'*64,
            'imports_sha256':'i'*64})
        Native.checks=[];Native.seals=[]
        return adapter,rendered,ledger,stop

    def setup_independent_service(self,root):
        """Two independently sealed theories with a real VERO slot extraction."""
        adapter,workspace,ledger,stop=self.setup_adapter(root)
        bench=adapter.observer.bench
        render(bench,workspace,{'c0001:prove':'by simp',
            'c0002:prove':'by simp'})
        with patch('regen.execution.vero_checker_adapter.IsabelleChecker',Native):
            adapter.check(workspace,PARENT)
            adapter.check(workspace,CHILD)
        self.assertEqual(experiment_summary(ledger)['current_valid_contract_ids'],
            ['c0001','c0002'])
        self.assertEqual(stop.sealed,{PARENT,CHILD})
        service=object.__new__(Service)
        service.workspace=workspace;service.output=root/'requests'
        service.modules={PARENT,CHILD};service.bench=bench
        service.checker=adapter
        service.frozen_plan={'imports':{PARENT:[],CHILD:[]}}
        service.extract=extract
        return service,workspace,ledger,stop

    def test_independent_parent_green_survives_child_extraction_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            service,workspace,ledger,stop=self.setup_independent_service(Path(temporary))
            child=workspace/(CHILD+'.thy')
            child.write_text(child.read_text().replace('end\n',
                '(* VERO START c0002 global_aux *)\nend\n',1))
            with patch.object(Service,'verify_plan',return_value=None):
                result=service.dispatch({'cmd':'progress'})
            self.assertIn(CHILD,result['file_errors'])
            self.assertEqual(experiment_summary(ledger)['current_valid_contract_ids'],
                ['c0001'])
            self.assertEqual(stop.sealed,{PARENT})
            invalid=[row['data'] for row in ledger.rows
                if row['kind']=='source_extraction_invalid']
            self.assertEqual(invalid[-1]['affected_theories'],[CHILD])
            restarted=VeroObserver(Ledger(Path(temporary)/'ledger','dynamic-imports'),
                Stop(ledger),workspace,service.bench)
            self.assertEqual(set(restarted.accepted_receipts),{'c0001'})

    def test_independent_parent_green_survives_child_malformed_import(self):
        with tempfile.TemporaryDirectory() as temporary:
            service,workspace,ledger,stop=self.setup_independent_service(Path(temporary))
            child=workspace/(CHILD+'.thy')
            marker=f'(* VERO START {CHILD} imports *)'
            child.write_text(child.read_text().replace(marker,
                marker+'\nUnknown_Theory\n',1))
            with patch.object(Service,'verify_plan',return_value=None):
                result=service.dispatch({'cmd':'progress'})
            self.assertIn('imports_error',result)
            self.assertEqual(experiment_summary(ledger)['current_valid_contract_ids'],
                ['c0001'])
            self.assertEqual(stop.sealed,{PARENT})
            invalid=[row['data'] for row in ledger.rows
                if row['kind']=='source_invalid']
            self.assertEqual(invalid[-1]['invalid_theories'],[CHILD])
            self.assertEqual(invalid[-1]['affected_theories'],[CHILD])
            restarted=VeroObserver(Ledger(Path(temporary)/'ledger','dynamic-imports'),
                Stop(ledger),workspace,service.bench)
            self.assertEqual(set(restarted.accepted_receipts),{'c0001'})

    def test_terminal_context_error_does_not_undo_scoped_preflight(self):
        with tempfile.TemporaryDirectory() as temporary:
            service,workspace,ledger,stop=self.setup_independent_service(Path(temporary))
            child=workspace/(CHILD+'.thy')
            marker=f'(* VERO START {CHILD} imports *)'
            child.write_text(child.read_text().replace(marker,
                marker+'\nUnknown_Theory\n',1))
            # VERO's terminal evaluator reports a repository-wide context
            # error. The runner has already attributed the malformed slot to
            # B, so that broad report must not revoke independent A again.
            report={'context_error':'invalid additive VERO imports',
                'contracts':{'c0001':{'status':'rejected'},
                    'c0002':{'status':'rejected'}},
                'modules':{PARENT:{'status':'rejected'},
                    CHILD:{'status':'rejected'}},
                'effective_imports':{PARENT:[],CHILD:[]}}
            runner=SimpleNamespace(Checker=object,CodexAgent=object,
                evaluate=lambda *args,**kwargs:report)
            with bind_runner(runner,service.checker,object):
                self.assertIs(runner.evaluate(),report)
            summary=experiment_summary(ledger)
            self.assertEqual(summary['current_valid_contract_ids'],['c0001'])
            self.assertEqual(summary['current_valid_sealed_theories'],[PARENT])
            self.assertEqual(stop.sealed,{PARENT})
            invalid=[row['data'] for row in ledger.rows
                if row['kind']=='source_invalid']
            self.assertEqual(len(invalid),1)
            self.assertEqual(invalid[0]['affected_theories'],[CHILD])
            self.assertFalse(any(row['kind']=='seal_invalidated'
                and row['data']['theory']==PARENT for row in ledger.rows))

    def test_local_check_does_not_revoke_unedited_sibling_seal(self):
        """Only real workspace edits, not local oops masking, revoke seals."""
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            adapter,raw,ledger,stop=self.setup_adapter(root)
            body={'schema_version':2,'backend':'isabelle','mode':'proof',
                'base_import':'Main','contracts':[
                    {'id':'c0001','module':PARENT,'statement':'lemma parent: True',
                        'context':[]},
                    {'id':'c0002','module':CHILD,'statement':'lemma child: True',
                        'context':[]}],
                'planning':{'imports':{PARENT:[],CHILD:[]}}}
            bench={**body,'benchmark_id':digest(body)}
            adapter.observer.bench=bench
            render(bench,raw,{'c0001:prove':'by simp','c0002:prove':'by simp'})
            local=root/'local'
            render(bench,local,{'c0002:prove':'by simp'})
            self.assertNotEqual((raw/(PARENT+'.thy')).read_bytes(),
                (local/(PARENT+'.thy')).read_bytes())
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',Native):
                adapter.check(raw,PARENT)
                self.assertEqual(stop.sealed,{PARENT})
                adapter.check(local,CHILD)
                self.assertEqual(stop.sealed,{PARENT,CHILD})
                self.assertEqual([r for r in ledger.rows
                    if r['kind']=='seal_invalidated'],[])
                # An already GREEN proof body is immutable outside a trusted
                # seal transaction, even when checking a different theory.
                parent=raw/(PARENT+'.thy')
                parent.write_text(parent.read_text().replace('by simp','by blast',1))
                with self.assertRaisesRegex(ValueError,'outside seal'):
                    adapter.check(local,CHILD)
            invalid=[r['data'] for r in ledger.rows
                if r['kind']=='seal_invalidated']
            self.assertEqual(invalid,[])
            self.assertEqual(stop.sealed,{PARENT,CHILD})

    def test_progress_revokes_stale_seal_but_retains_earlier_green_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);adapter,raw,ledger,stop=self.setup_adapter(root)
            body={'schema_version':2,'backend':'isabelle','mode':'proof',
                'base_import':'Main','contracts':[
                    {'id':'c0001','module':PARENT,'statement':'lemma parent: True',
                        'context':[]},
                    {'id':'c0002','module':CHILD,'statement':'lemma child: True',
                        'context':[]}],
                'planning':{'imports':{PARENT:[],CHILD:[]}}}
            bench={**body,'benchmark_id':digest(body)}
            adapter.observer.bench=bench
            render(bench,raw,{'c0001:prove':'by simp'})
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',Native):
                adapter.check(raw,PARENT)
            self.assertEqual(stop.sealed,{PARENT})
            parent=raw/(PARENT+'.thy')
            marker='(* VERO START c0001 disprove_aux *)'
            self.assertIn(marker,parent.read_text())
            parent.write_text(parent.read_text().replace(marker,
                marker+'\nlemma vero_aux_later: True by simp\n',1))
            service=object.__new__(Service)
            service.workspace=raw;service.output=root/'requests'
            service.modules={PARENT,CHILD};service.bench=bench
            service.checker=adapter
            service.frozen_plan={'imports':{PARENT:[],CHILD:[]}}
            service.extract=extract
            with patch.object(Service,'verify_plan',return_value=None):
                service.dispatch({'cmd':'progress'})
            self.assertEqual(stop.sealed,set())
            self.assertEqual(experiment_summary(ledger)['current_valid_contract_ids'],
                ['c0001'])

    def test_additive_parent_must_be_sealed_and_changes_revoke_child(self):
        with tempfile.TemporaryDirectory() as temporary:
            adapter,rendered,ledger,stop=self.setup_adapter(Path(temporary))
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',Native):
                blocked=adapter.check(rendered,CHILD)
                self.assertEqual(blocked['certification_blocked_by'],[PARENT])
                self.assertEqual(blocked['seal_status'],'waiting_for_imports')
                self.assertEqual(stop.sealed,set())
                adapter.check(rendered,PARENT)
                first=adapter.check(rendered,CHILD)
                same=adapter.check(rendered,CHILD)
                self.assertEqual(first['seal_status'],'sealed')
                self.assertTrue(same['reused_exact_check'])
                self.assertTrue(same['seal_reused'])
                self.assertEqual(stop.sealed,{PARENT,CHILD})
                previous_checks=len(Native.checks)
                previous_seals=len(Native.seals)
                (rendered/(PARENT+'.thy')).write_text(theory(PARENT,proof='by blast'))
                changed=adapter.check(rendered,CHILD)
                self.assertFalse(changed['reused_exact_check'])
                self.assertFalse(changed['seal_reused'])
                self.assertEqual(changed['certification_blocked_by'],[PARENT])
                self.assertEqual(changed['seal_status'],'waiting_for_imports')
                self.assertEqual(len(Native.checks),previous_checks+1)
                self.assertEqual(len(Native.seals),previous_seals)
                self.assertEqual(stop.sealed,set())
                invalid=[r['data'] for r in ledger.rows
                    if r['kind']=='seal_invalidated']
                self.assertEqual([(r['theory'],r['reason']) for r in invalid],
                    [(PARENT,'own_source_changed'),(CHILD,'import_source_changed')])
                adapter.check(rendered,PARENT)
                resealed=adapter.check(rendered,CHILD)
                self.assertEqual(resealed['seal_status'],'sealed')
                self.assertFalse(resealed['reused_exact_check'])
                self.assertEqual(stop.sealed,{PARENT,CHILD})
                (rendered/(PARENT+'.thy')).unlink()
                with self.assertRaisesRegex(ValueError,'closure source absent'):
                    adapter.check(rendered,CHILD)
            self.assertEqual(stop.sealed,set())
            self.assertEqual(experiment_summary(ledger)['current_valid_contract_ids'],[])
            missing=[r['data'] for r in ledger.rows if r['kind']=='source_missing']
            self.assertEqual(missing[-1]['affected_theories'],[CHILD,PARENT])

    def test_effective_imports_reject_header_disagreement(self):
        with tempfile.TemporaryDirectory() as temporary:
            adapter,rendered,_,_=self.setup_adapter(Path(temporary))
            path=rendered/(CHILD+'.thy')
            path.write_text(path.read_text().replace('imports Vero_Support',
                'imports Vero_Support '+PARENT,1))
            with self.assertRaisesRegex(ValueError,'rendered imports disagree'):
                adapter.check(rendered,CHILD)

    def test_original_workspace_progress_rejects_deleting_green_parent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            adapter,rendered,ledger,stop=self.setup_adapter(root)
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',Native):
                adapter.check(rendered,PARENT)
                adapter.check(rendered,CHILD)
            self.assertEqual(stop.sealed,{PARENT,CHILD})
            (rendered/(PARENT+'.thy')).unlink()
            service=object.__new__(Service)
            service.workspace=rendered;service.output=root/'requests'
            service.modules={PARENT,CHILD};service.bench=adapter.observer.bench
            service.checker=adapter
            service.frozen_plan={'imports':{PARENT:[],CHILD:[]}}
            from vero.isabelle.benchmark import extract
            service.extract=extract
            with patch.object(Service,'verify_plan',return_value=None):
                with self.assertRaisesRegex(ValueError,'outside seal'):
                    service.dispatch({'cmd':'progress'})
            self.assertEqual(stop.sealed,{PARENT,CHILD})
            self.assertEqual(experiment_summary(ledger)['current_valid_contract_ids'],
                ['c0001','c0002'])
            self.assertFalse(any(r['kind']=='source_missing' for r in ledger.rows))

    def test_cyclic_additive_imports_revoke_current_green_before_check(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            adapter,rendered,ledger,stop=self.setup_adapter(root)
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',Native):
                adapter.check(rendered,PARENT)
                adapter.check(rendered,CHILD)
            self.assertEqual(stop.sealed,{PARENT,CHILD})
            (rendered/(PARENT+'.thy')).write_text(theory(PARENT,CHILD))
            service=object.__new__(Service)
            service.workspace=rendered;service.output=root/'requests'
            service.modules={PARENT,CHILD};service.bench=adapter.observer.bench
            service.checker=adapter
            service.frozen_plan={'imports':{PARENT:[],CHILD:[]}}
            service.extract=lambda *args:{'slots':{
                PARENT+':imports':{'found':True,'body':CHILD},
                CHILD+':imports':{'found':True,'body':PARENT}},'file_errors':{}}
            with patch.object(Service,'verify_plan',return_value=None):
                with self.assertRaisesRegex(ValueError,'cycle'):
                    service.dispatch({'cmd':'check','module':CHILD})
            self.assertEqual(stop.sealed,set())
            summary=experiment_summary(ledger)
            self.assertEqual(summary['current_valid_contract_ids'],[])
            self.assertEqual(summary['current_valid_sealed_theories'],[])
            self.assertEqual(summary['historical_first_green_contract_ids'],
                ['c0001','c0002'])
            invalid=[r['data'] for r in ledger.rows if r['kind']=='source_invalid']
            self.assertEqual(len(invalid),1)
            self.assertEqual(invalid[0]['affected_theories'],[CHILD,PARENT])
            replay=VeroObserver(ledger,Stop(ledger),rendered,adapter.observer.bench)
            self.assertEqual(replay.seals,{})

    def test_malformed_raw_import_slot_preserves_independent_parent_on_progress(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            adapter,rendered,ledger,stop=self.setup_adapter(root)
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',Native):
                adapter.check(rendered,PARENT)
                adapter.check(rendered,CHILD)
            (rendered/(CHILD+'.thy')).write_text(theory(CHILD,'Unknown_Theory'))
            service=object.__new__(Service)
            service.workspace=rendered;service.output=root/'requests'
            service.modules={PARENT,CHILD};service.bench=adapter.observer.bench
            service.checker=adapter
            service.frozen_plan={'imports':{PARENT:[],CHILD:[]}}
            from vero.isabelle.benchmark import extract
            service.extract=extract
            with patch.object(Service,'verify_plan',return_value=None):
                service.dispatch({'cmd':'progress'})
            self.assertEqual(stop.sealed,{PARENT})
            summary=experiment_summary(ledger)
            self.assertEqual(summary['current_valid_contract_ids'],['c0001'])
            self.assertEqual(summary['historical_first_green_contract_ids'],
                ['c0001','c0002'])
            self.assertEqual(len([r for r in ledger.rows if r['kind']=='source_invalid']),1)

    def test_cyclic_additive_imports_revoke_on_progress(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            adapter,rendered,ledger,stop=self.setup_adapter(root)
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',Native):
                adapter.check(rendered,PARENT)
                adapter.check(rendered,CHILD)
            (rendered/(PARENT+'.thy')).write_text(theory(PARENT,CHILD))
            service=object.__new__(Service)
            service.workspace=rendered;service.output=root/'requests'
            service.modules={PARENT,CHILD};service.bench=adapter.observer.bench
            service.checker=adapter
            service.frozen_plan={'imports':{PARENT:[],CHILD:[]}}
            service.extract=lambda *args:{'slots':{
                PARENT+':imports':{'found':True,'body':CHILD},
                CHILD+':imports':{'found':True,'body':PARENT}},'file_errors':{}}
            with patch.object(Service,'verify_plan',return_value=None):
                result=service.dispatch({'cmd':'progress'})
            self.assertIn('cycle',result['imports_error'])
            self.assertEqual(stop.sealed,set())
            summary=experiment_summary(ledger)
            self.assertEqual(summary['current_valid_contract_ids'],[])
            self.assertEqual(summary['historical_first_green_contract_ids'],
                ['c0001','c0002'])

    def test_terminal_vero_context_error_revokes_without_tool_check(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            adapter,rendered,ledger,stop=self.setup_adapter(root)
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',Native):
                adapter.check(rendered,PARENT)
                adapter.check(rendered,CHILD)
            report={'context_error':'generated imports contain a cycle',
                'contracts':{'c0001':{'status':'rejected'},
                    'c0002':{'status':'rejected'}},'modules':{}}
            adapter.observer.evaluated(report)
            self.assertEqual(stop.sealed,set())
            summary=experiment_summary(ledger)
            self.assertEqual(summary['current_valid_contract_ids'],[])
            self.assertEqual(summary['current_valid_sealed_theories'],[])
            self.assertEqual(summary['historical_first_green_contract_ids'],
                ['c0001','c0002'])
            replay=VeroObserver(ledger,Stop(ledger),rendered,adapter.observer.bench)
            self.assertEqual(replay.seals,{})

    def test_direct_adapter_invalid_import_preserves_independent_parent_seal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            adapter,rendered,ledger,stop=self.setup_adapter(root)
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',Native):
                adapter.check(rendered,PARENT)
                adapter.check(rendered,CHILD)
                (rendered/(CHILD+'.thy')).write_text(theory(CHILD,'Unknown_Theory'))
                with self.assertRaisesRegex(ValueError,'invalid additive'):
                    adapter.check(rendered,CHILD)
            self.assertEqual(stop.sealed,{PARENT})
            summary=experiment_summary(ledger)
            self.assertEqual(summary['current_valid_contract_ids'],['c0001'])
            self.assertEqual(summary['historical_first_green_contract_ids'],
                ['c0001','c0002'])

    def test_terminal_evaluator_only_rejection_revokes_provider_and_consumer_seals(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            adapter,rendered,ledger,stop=self.setup_adapter(root)
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',Native):
                adapter.check(rendered,PARENT)
                adapter.check(rendered,CHILD)
            report={'context_error':None,
                'contracts':{'c0001':{'status':'rejected'},
                    'c0002':{'status':'blocked_dependency'}},
                'modules':{PARENT:{'status':'rejected'},
                    CHILD:{'status':'blocked_dependency'}},
                'effective_imports':{PARENT:[],CHILD:[PARENT]}}
            adapter.observer.evaluated(report)
            self.assertEqual(stop.sealed,set())
            summary=experiment_summary(ledger)
            # A terminal evaluator rejection revokes whole-theory seals but
            # does not erase individually evidenced unchanged GREEN proofs.
            self.assertEqual(summary['current_valid_contract_ids'],
                ['c0001','c0002'])
            self.assertEqual(summary['current_valid_sealed_theories'],[])
            self.assertEqual(summary['historical_first_green_contract_ids'],
                ['c0001','c0002'])
            replay=VeroObserver(ledger,Stop(ledger),rendered,adapter.observer.bench)
            self.assertEqual(replay.seals,{})

    def test_terminal_failed_seal_preserves_native_green(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            adapter,rendered,ledger,stop=self.setup_adapter(root)
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',Native):
                adapter.check(rendered,PARENT)
                adapter.check(rendered,CHILD)
            report={'context_error':None,
                'contracts':{'c0001':{'status':'proved'},
                    'c0002':{'status':'seal_failed'}},
                'modules':{PARENT:{'status':'ok'},
                    CHILD:{'status':'build_error','contract_status':'ok',
                        'seal_status':'seal_failed'}},
                'effective_imports':{PARENT:[],CHILD:[PARENT]}}
            adapter.observer.evaluated(report)
            summary=experiment_summary(ledger)
            self.assertEqual(summary['current_valid_contract_ids'],
                ['c0001','c0002'])
            self.assertEqual(stop.sealed,{PARENT,CHILD})

    def test_terminal_runner_observes_deleted_raw_parent_before_evaluation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            adapter,rendered,ledger,stop=self.setup_adapter(root)
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',Native):
                adapter.check(rendered,PARENT)
                adapter.check(rendered,CHILD)
            (rendered/(PARENT+'.thy')).unlink()
            report={'context_error':None,
                'contracts':{'c0001':{'status':'extraction_error'},
                    'c0002':{'status':'blocked_dependency'}},
                'modules':{PARENT:{'status':'rejected'},
                    CHILD:{'status':'blocked_dependency'}},
                'effective_imports':{PARENT:[],CHILD:[PARENT]}}
            runner=SimpleNamespace(Checker=object,CodexAgent=object,
                evaluate=lambda *args,**kwargs:report)
            with bind_runner(runner,adapter,object):
                with self.assertRaisesRegex(ValueError,'outside seal'):
                    runner.evaluate()
            self.assertEqual(stop.sealed,{PARENT,CHILD})
            missing=[r['data'] for r in ledger.rows if r['kind']=='source_missing']
            self.assertEqual(missing,[])
            summary=experiment_summary(ledger)
            self.assertEqual(summary['current_valid_contract_ids'],['c0001','c0002'])
            self.assertEqual(summary['current_valid_sealed_theories'],[CHILD,PARENT])

    def test_terminal_runner_invalid_raw_import_revokes_before_evaluator(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            adapter,rendered,ledger,stop=self.setup_adapter(root)
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',Native):
                adapter.check(rendered,PARENT)
                adapter.check(rendered,CHILD)
            (rendered/(CHILD+'.thy')).write_text(theory(CHILD,'Unknown_Theory'))
            def failed_evaluate(*args,**kwargs):
                self.assertEqual(stop.sealed,{PARENT})
                raise RuntimeError('original evaluator reached')
            runner=SimpleNamespace(Checker=object,CodexAgent=object,
                evaluate=failed_evaluate)
            with bind_runner(runner,adapter,object):
                with self.assertRaisesRegex(RuntimeError,'original evaluator reached'):
                    runner.evaluate()
            self.assertEqual(len([r for r in ledger.rows if r['kind']=='source_invalid']),1)
            self.assertEqual(experiment_summary(ledger)['current_valid_contract_ids'],
                ['c0001'])

    def test_duplicate_base_import_remains_accepted_additive_slot(self):
        frozen={PARENT:(),CHILD:(PARENT,)}
        snapshot={
            (PARENT+'.thy'):theory(PARENT).encode(),
            (CHILD+'.thy'):theory(CHILD,PARENT).replace(
                'imports Vero_Support','imports Vero_Support '+PARENT,1).encode()}
        self.assertEqual(effective_generated_imports(snapshot,frozen,
            require_rendered=True)[CHILD],(PARENT,))

    def test_original_vero_renderer_matches_effective_graph(self):
        from vero.isabelle.benchmark import prepare,render
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);inputs=root/'contracts.jsonl'
            inputs.write_text('\n'.join(json.dumps({
                'contract_id':str(index),'name':f't{index}',
                'owner_theory':owner,'exact_statement':f'lemma t{index}: "True"'})
                for index,owner in enumerate(('Provider','Consumer'))))
            bench=prepare(inputs,root/'bench',base_import='Main')
            rendered=root/'rendered'
            render(bench,rendered,{'Vero_Consumer:imports':'Vero_Provider'})
            snapshot={path.name:path.read_bytes() for path in rendered.glob('*.thy')}
            base=bench.get('planning',{}).get('imports',{})
            frozen={name:tuple(base.get(name,()))
                for name in ('Vero_Provider','Vero_Consumer')}
            self.assertEqual(effective_generated_imports(snapshot,frozen,
                require_rendered=True)['Vero_Consumer'],('Vero_Provider',))


if __name__=='__main__':unittest.main()
