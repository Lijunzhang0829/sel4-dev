import tempfile
from pathlib import Path
import hashlib
import json
import re
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.experiment_activity import experiment_summary
from regen.infrastructure.vero_experiment_observer import VeroObserver
from regen.execution.vero_checker_adapter import VeroCheckerAdapter
from regen.execution.vero_import_graph import effective_generated_imports
from regen.tools.vero_serial_application import (
    FORMAL_ACTIVE_WALL_SECONDS,FORMAL_EXPECTED_CONTRACTS,
    ACTIVE_WALL_BUDGET_POLICY,EFFECTIVE_BUDGET_POLICY,CHECKPOINT_KIND,
    RecoverableAgentFailure,ProviderRecoveryPause,create_fresh_plan,
    expected_runtime_config,run_with_agent_takeover,interrupted_result,
    classify_agent_invocation,provider_auth_binding,
    Service,snapshot,bind_runner,fixed_inventory_binding,formal_configuration_binding,
    frozen_plan_binding,validate_resume_artifacts,validate_resume_ledger,
    SEAL_MIGRATION_FROM,SEAL_MIGRATION_ID,record_seal_migration,
    CHECKER_LIFECYCLE_MIGRATION_FROM,CHECKER_LIFECYCLE_MIGRATION_ID,
    record_checker_lifecycle_migration,RESOURCE_POOL_MIGRATION_FROM,
    record_resource_pool_migration,record_provider_auth_migration,
    record_runner_reopen_migration,
    PROVIDER_AUTH_MIGRATION_SEED,PROVIDER_AUTH_MIGRATION_BASE,
)

VERO_SOURCE=Path(__file__).resolve().parents[3]/'vero-isabelle'/'src'
sys.path.insert(0,str(VERO_SOURCE))
from vero.isabelle.benchmark import prepare,render,extract
from vero.isabelle.planning import compile_plan


class VeroSerialTests(unittest.TestCase):
    @staticmethod
    def guarded_service(root):
        """A real schema-v2 VERO render with host-owned GREEN evidence."""
        sourcefile=root/'contracts.jsonl'
        sourcefile.write_text(json.dumps({'contract_id':'fixture-c0001',
            'name':'tiny','owner_theory':'A',
            'exact_statement':'lemma tiny: "True"'})+'\n')
        source_bench=prepare(sourcefile,root/'source-benchmark',base_import='Main')
        plan={'version':1,'source_benchmark_id':source_bench['benchmark_id'],
            'modules':[{'name':'Vero_A','contracts':['c0001'],
                'imports':[],'rationale':'one contract'}],
            'execution_order':['Vero_A']}
        bench=compile_plan(source_bench,plan)
        workspace=root/'workspace';workspace.mkdir()
        render(bench,workspace,{'c0001:prove':'by simp'})
        source=workspace/'Vero_A.thy'
        def write(body):
            text=source.read_text()
            pattern=(r'(\(\* VERO START c0001 prove \*\))'
                r'.*?(\(\* VERO END c0001 prove \*\))')
            changed,count=re.subn(pattern,lambda match:
                match.group(1)+'\n'+body+'\n'+match.group(2),text,
                count=1,flags=re.S)
            if count!=1:raise AssertionError('missing proof slot')
            source.write_text(changed)
        class Stop:
            event=SimpleNamespace(is_set=lambda:False)
            def check(self):pass
            def select(self,theory):pass
            def observe(self,*args,**kwargs):pass
        ledger=Ledger(root/'ledger','guard-test')
        observer=VeroObserver(ledger,Stop(),workspace,bench)
        service=object.__new__(Service)
        service.bench=bench;service.workspace=workspace
        service.dataset=root;service.output=root/'requests'
        service.modules={'Vero_A'}
        service.frozen_plan=frozen_plan_binding(bench,plan)
        adapter=object.__new__(VeroCheckerAdapter)
        adapter.observer=observer
        adapter.frozen_plan=service.frozen_plan
        adapter.root=root/'adapter';adapter.root.mkdir()
        adapter.context={'checker_context_id':'fixture-checker-context'}
        service.checker=adapter
        service.extract=extract
        return service,observer,source,write

    @staticmethod
    def accepted_receipt(service):
        """Compute a real per-contract certificate identity from VERO render."""
        snapshot={path.name:path.read_bytes()
            for path in service.workspace.glob('*.thy')}
        imports=effective_generated_imports(snapshot,
            service.frozen_plan['imports'],require_rendered=True)
        return service.checker._contract_receipts(
            'Vero_A',snapshot,imports)['c0001']

    @classmethod
    def seed_native_green(cls,service,observer,*,origin='prior-native-receipt'):
        """Seed an exact successful native theory check for seal-admission tests."""
        snapshot={path.name:path.read_bytes()
            for path in service.workspace.glob('*.thy')}
        imports=effective_generated_imports(snapshot,
            service.frozen_plan['imports'],require_rendered=True)
        receipt=cls.accepted_receipt(service)
        observer.accepted_receipts['c0001']=receipt
        observer.accepted_origins['c0001']=origin
        observer.ledger.append('proof_commit',theory='Vero_A',
            nodes=[{'uid':'c0001','kind':'contract','theory':'Vero_A',
                'status':'green','accepted_receipt':receipt,
                'accepted_evidence_event_id':origin}],
            source_identity=service.checker._seal_source_identity(
                'Vero_A',snapshot,imports),evidence={'returncode':0})
        return receipt

    def test_begin_seal_requires_host_green_and_scope_cannot_authorize_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            service,observer,source,write=self.guarded_service(Path(tmp))
            with self.assertRaisesRegex(ValueError,'all theory contracts GREEN'):
                service.dispatch({'cmd':'begin-seal','module':'Vero_A'})
            self.seed_native_green(service,observer)
            # Agent-declared accounting phase is never a seal-edit grant.
            service.dispatch({'cmd':'scope','target_ids':['c0001'],
                'phase':'seal'})
            write('by auto')
            with self.assertRaisesRegex(ValueError,'outside seal'):
                service.dispatch({'cmd':'begin-seal','module':'Vero_A'})
            self.assertFalse(observer.seal_transactions)
            self.assertTrue(any(row['kind']=='green_proof_edit_rejected'
                for row in observer.ledger.rows))
            write('by simp')
            opened=service.dispatch({'cmd':'begin-seal','module':'Vero_A'})
            self.assertEqual(opened['status'],'seal_edit_active')
            self.assertEqual(observer.seal_transactions,{'Vero_A'})
            self.assertEqual(observer.ledger.rows[-1]['kind'],'seal_edit_begin')

    def test_abort_seal_requires_current_transaction_and_restored_green_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            service,observer,source,write=self.guarded_service(Path(tmp))
            self.seed_native_green(service,observer)
            opened=service.dispatch({'cmd':'begin-seal','module':'Vero_A'})
            transaction=opened['seal_transaction_id']
            write('by auto')
            # A failed candidate must not be laundered into ordinary
            # generation merely by closing its seal edit window.
            with self.assertRaisesRegex(ValueError,'restore|GREEN|accepted|body'):
                service.dispatch({'cmd':'abort-seal','module':'Vero_A',
                    'transaction_id':transaction})
            self.assertEqual(observer.seal_transactions,{'Vero_A'})
            write('by simp')
            with self.assertRaisesRegex(ValueError,'transaction|stale|mismatch'):
                service.dispatch({'cmd':'abort-seal','module':'Vero_A',
                    'transaction_id':'stale-transaction'})
            closed=service.dispatch({'cmd':'abort-seal','module':'Vero_A',
                'transaction_id':transaction})
            self.assertEqual(closed['status'],'seal_edit_abandoned')
            self.assertFalse(observer.seal_transactions)
            self.assertEqual(observer.ledger.rows[-1]['kind'],'seal_edit_end')
            self.assertEqual(observer.ledger.rows[-1]['data']['status'],'abandoned')
            replay=VeroObserver(Ledger(Path(tmp)/'ledger','guard-test'),
                SimpleNamespace(),service.workspace,service.bench)
            self.assertFalse(replay.seal_transactions)
            write('by auto')
            with self.assertRaisesRegex(ValueError,'outside seal'):
                service.dispatch({'cmd':'check','module':'Vero_A'})

    def test_missing_file_during_seal_candidate_keeps_immutable_old_green(self):
        with tempfile.TemporaryDirectory() as tmp:
            service,observer,source,write=self.guarded_service(Path(tmp))
            old=self.accepted_receipt(service)
            self.seed_native_green(service,observer)
            service.dispatch({'cmd':'begin-seal','module':'Vero_A'})
            source.unlink()
            # A missing candidate is a failed speculative seal draft. The
            # independently accepted prior proof remains available to restore.
            service.dispatch({'cmd':'progress'})
            self.assertEqual(observer.accepted_receipts.get('c0001'),old)
            self.assertEqual(observer.seal_transactions,{'Vero_A'})

    def test_runner_context_error_during_seal_keeps_old_accepted_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            service,observer,source,write=self.guarded_service(Path(tmp))
            old=self.accepted_receipt(service)
            self.seed_native_green(service,observer)
            service.dispatch({'cmd':'begin-seal','module':'Vero_A'})
            marker='(* VERO START Vero_A imports *)'
            self.assertIn(marker,source.read_text())
            source.write_text(source.read_text().replace(
                marker,marker+'\nUnknown_Theory\n',1))
            report={'context_error':'invalid imports',
                'modules':{'Vero_A':{'status':'rejected'}},
                'contracts':{'c0001':{'status':'extraction_error'}}}
            runner=SimpleNamespace(Checker=object(),CodexAgent=object(),
                evaluate=lambda *args,**kwargs:report)
            with bind_runner(runner,service.checker,object()):
                self.assertIs(runner.evaluate(),report)
            self.assertEqual(observer.accepted_receipts.get('c0001'),old)
            self.assertEqual(observer.seal_transactions,{'Vero_A'})
            self.assertEqual(experiment_summary(observer.ledger)
                ['current_valid_contract_ids'],['c0001'])

    def test_begin_seal_rejects_stale_green_after_preceding_helper_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            service,observer,source,write=self.guarded_service(Path(tmp))
            old=source.read_text()
            self.seed_native_green(service,observer)
            # The accepted proof text is untouched, but the context before it
            # has changed. Add a genuine auxiliary slot so VERO's canonical
            # extraction and render keep the changed checked prefix.
            start='(* VERO START c0001 prove_aux *)'
            self.assertIn(start,old)
            source.write_text(old.replace(start,start+'\n'
                'lemma vero_aux_new: True by simp\n',1))
            with self.assertRaisesRegex(ValueError,'GREEN|prefix|context'):
                service.dispatch({'cmd':'begin-seal','module':'Vero_A'})
            self.assertFalse(observer.seal_transactions)

    def test_begin_seal_requires_latest_exact_native_theory_green(self):
        with tempfile.TemporaryDirectory() as tmp:
            service,observer,source,write=self.guarded_service(Path(tmp))
            old=self.seed_native_green(service,observer)
            # This declaration follows the accepted proof, so its addition
            # leaves that contract's independent GREEN receipt intact.
            marker='(* VERO START c0001 disprove_aux *)'
            self.assertIn(marker,source.read_text())
            source.write_text(source.read_text().replace(marker,
                marker+'\nlemma vero_aux_later: True by simp\n',1))
            with self.assertRaisesRegex(ValueError,'exact native GREEN theory check'):
                service.dispatch({'cmd':'begin-seal','module':'Vero_A'})
            self.assertEqual(observer.accepted_receipts['c0001'],old)
            self.assertFalse(observer.seal_transactions)

    def test_begin_seal_rejects_latest_native_failure_with_retained_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            service,observer,source,write=self.guarded_service(Path(tmp))
            old=self.seed_native_green(service,observer)
            source_bytes={path.name:path.read_bytes()
                for path in service.workspace.glob('*.thy')}
            imports=effective_generated_imports(source_bytes,
                service.frozen_plan['imports'],require_rendered=True)
            identity=service.checker._seal_source_identity(
                'Vero_A',source_bytes,imports)
            observer.ledger.append('proof_commit',theory='Vero_A',
                nodes=[{'uid':'c0001','kind':'contract','theory':'Vero_A',
                    'status':'green','accepted_receipt':old},
                    {'uid':'helper:Vero_A:vero_aux_later','kind':'helper',
                    'theory':'Vero_A','status':'open'}],
                source_identity=identity,evidence={'returncode':1})
            with self.assertRaisesRegex(ValueError,'exact native GREEN theory check'):
                service.dispatch({'cmd':'begin-seal','module':'Vero_A'})
            self.assertEqual(observer.accepted_receipts['c0001'],old)
            self.assertFalse(observer.seal_transactions)

    def test_progress_and_runner_reconcile_earlier_helper_without_native_check(self):
        for terminal in (False,True):
            with self.subTest(terminal=terminal),tempfile.TemporaryDirectory() as tmp:
                service,observer,source,write=self.guarded_service(Path(tmp))
                observer.accepted_receipts['c0001']=self.accepted_receipt(service)
                observer.accepted_origins['c0001']='prior-native-receipt'
                marker='(* VERO START c0001 prove_aux *)'
                source.write_text(source.read_text().replace(marker,
                    marker+'\nlemma vero_aux_changed: True by simp\n',1))
                if terminal:
                    report={'context_error':None,
                        'modules':{'Vero_A':{'status':'rejected'}},
                        'contracts':{'c0001':{'status':'extraction_error'}}}
                    runner=SimpleNamespace(Checker=object(),CodexAgent=object(),
                        evaluate=lambda *args,**kwargs:report)
                    with bind_runner(runner,service.checker,object()):
                        self.assertIs(runner.evaluate(),report)
                else:
                    service.dispatch({'cmd':'progress'})
                self.assertNotIn('c0001',observer.accepted_receipts)
                self.assertTrue(any(row['kind']=='contract_green_invalidated'
                    and row['data'].get('target_id')=='c0001'
                    for row in observer.ledger.rows))

    def test_balanced_extra_marker_cannot_hide_changed_green_prefix(self):
        for terminal in (False,True):
            with self.subTest(terminal=terminal),tempfile.TemporaryDirectory() as tmp:
                service,observer,source,_=self.guarded_service(Path(tmp))
                self.seed_native_green(service,observer)
                marker='(* VERO START c0001 prove_aux *)'
                extra=('\n(* VERO START stray global_aux *)\n'
                    'lemma vero_aux_stray: True by simp\n'
                    '(* VERO END stray global_aux *)\n')
                source.write_text(source.read_text().replace(marker,
                    marker+'\nlemma vero_aux_changed: True by simp\n',1)+extra)
                self.assertEqual(extract(service.bench,service.workspace)['extras'],
                    ['stray:global_aux'])
                if terminal:
                    report={'context_error':None,
                        'modules':{'Vero_A':{'status':'rejected'}},
                        'contracts':{'c0001':{'status':'rejected'}}}
                    runner=SimpleNamespace(Checker=object(),CodexAgent=object(),
                        evaluate=lambda *args,**kwargs:report)
                    with bind_runner(runner,service.checker,object()):
                        self.assertIs(runner.evaluate(),report)
                else:
                    service.dispatch({'cmd':'progress'})
                summary=experiment_summary(observer.ledger)
                self.assertEqual(summary['current_valid_contract_ids'],[])
                self.assertEqual(summary['historical_first_green_contract_ids'],
                    ['c0001'])
                self.assertNotIn('c0001',observer.accepted_receipts)

    def test_runner_no_native_evaluator_edit_revokes_prior_green(self):
        with tempfile.TemporaryDirectory() as tmp:
            service,observer,source,_=self.guarded_service(Path(tmp))
            self.seed_native_green(service,observer)
            service.checker.root=Path(tmp)/'native-audit'
            service.checker.root.mkdir()
            marker='(* VERO START c0001 prove_aux *)'
            report={'context_error':None,
                'modules':{'Vero_A':{'status':'rejected'}},
                'contracts':{'c0001':{'status':'rejected'}}}
            def no_native_evaluator(*args,**kwargs):
                source.write_text(source.read_text().replace(marker,
                    marker+'\nlemma vero_aux_changed: True by simp\n',1))
                return report
            runner=SimpleNamespace(Checker=object(),CodexAgent=object(),
                evaluate=no_native_evaluator)
            with bind_runner(runner,service.checker,object()):
                with self.assertRaisesRegex(ValueError,'raw workspace changed'):
                    runner.evaluate()
            summary=experiment_summary(observer.ledger)
            self.assertEqual(summary['current_valid_contract_ids'],[])
            self.assertEqual(summary['historical_first_green_contract_ids'],
                ['c0001'])
            self.assertNotIn('c0001',observer.accepted_receipts)
            self.assertTrue(any(row['kind']=='contract_green_invalidated'
                and row['data'].get('target_id')=='c0001'
                for row in observer.ledger.rows))

    def test_service_check_no_native_evaluator_edit_revokes_prior_green(self):
        with tempfile.TemporaryDirectory() as tmp:
            service,observer,source,_=self.guarded_service(Path(tmp))
            self.seed_native_green(service,observer)
            service.checker.root=Path(tmp)/'native-audit'
            service.checker.root.mkdir()
            marker='(* VERO START c0001 prove_aux *)'
            report={'context_error':None,
                'modules':{'Vero_A':{'status':'rejected'}},
                'contracts':{'c0001':{'status':'rejected'}}}
            def no_native_evaluator(*args,**kwargs):
                source.write_text(source.read_text().replace(marker,
                    marker+'\nlemma vero_aux_changed: True by simp\n',1))
                return report
            with patch('vero.isabelle.checker.evaluate',side_effect=no_native_evaluator):
                with self.assertRaisesRegex(ValueError,'raw workspace changed'):
                    service.dispatch({'cmd':'check','module':'Vero_A'})
            summary=experiment_summary(observer.ledger)
            self.assertEqual(summary['current_valid_contract_ids'],[])
            self.assertEqual(summary['historical_first_green_contract_ids'],
                ['c0001'])
            self.assertNotIn('c0001',observer.accepted_receipts)
            self.assertTrue(any(row['kind']=='contract_green_invalidated'
                and row['data'].get('target_id')=='c0001'
                for row in observer.ledger.rows))

    def test_green_body_edit_or_deletion_is_rejected_before_progress_reconciliation(self):
        with tempfile.TemporaryDirectory() as tmp:
            service,observer,source,write=self.guarded_service(Path(tmp))
            observer.accepted_receipts['c0001']=self.accepted_receipt(service)
            write('by auto')
            with self.assertRaisesRegex(ValueError,'outside seal'):
                service.dispatch({'cmd':'progress'})
            with self.assertRaisesRegex(ValueError,'outside seal'):
                service.dispatch({'cmd':'check','module':'Vero_A'})
            self.assertIn('c0001',observer.accepted_receipts)
            write('by simp')
            source.unlink()
            with self.assertRaisesRegex(ValueError,'outside seal'):
                service.dispatch({'cmd':'progress'})
            self.assertIn('c0001',observer.accepted_receipts)
            self.assertFalse(any(row['kind']=='source_missing'
                for row in observer.ledger.rows))

    def test_runner_final_evaluation_rejects_deleted_green_source_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            service,observer,source,write=self.guarded_service(Path(tmp))
            observer.accepted_receipts['c0001']=self.accepted_receipt(service)
            source.unlink()
            original_evaluate=Mock(return_value={'modules':{},'contracts':{}})
            runner=SimpleNamespace(Checker=object(),CodexAgent=object(),
                evaluate=original_evaluate)
            checker=SimpleNamespace(observer=observer,
                frozen_plan=service.frozen_plan)
            with bind_runner(runner,checker,object()):
                with self.assertRaisesRegex(ValueError,'outside seal'):
                    runner.evaluate()
            original_evaluate.assert_not_called()
            self.assertIn('c0001',observer.accepted_receipts)

    def test_seal_candidate_native_green_does_not_replace_old_receipt_before_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            service,observer,source,write=self.guarded_service(root)
            old=self.accepted_receipt(service)
            self.seed_native_green(service,observer)
            service.dispatch({'cmd':'begin-seal','module':'Vero_A'})
            write('by auto')
            candidate=self.accepted_receipt(service)
            checker=ModuleType('vero.isabelle.checker')
            checker.strip_comments=lambda source:source
            isabelle=ModuleType('vero.isabelle');isabelle.checker=checker
            vero=ModuleType('vero');vero.isabelle=isabelle
            native={'audited':['c0001:prove'],'status':'ok',
                'contract_status':'ok','seal_status':'not_attempted',
                'native_evidence':{'returncode':0},
                'build_evidence':None,'elapsed_seconds':1,
                'contract_receipts':{'c0001':candidate}}
            with patch.dict(sys.modules,{'vero':vero,'vero.isabelle':isabelle,
                    'vero.isabelle.checker':checker}):
                observer.checked('Vero_A',native,source.read_text())
            # This native check is necessary, but the candidate can still
            # fail the separate theory seal/build. It must remain speculative.
            self.assertEqual(observer.accepted_receipts['c0001'],old)
            self.assertEqual(observer.accepted_origins['c0001'],
                'prior-native-receipt')

    def test_service_check_and_runner_use_immutable_raw_audit_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            service,observer,source,write=self.guarded_service(root)
            observed=[]
            benchmark=ModuleType('vero.isabelle.benchmark')
            benchmark.extract=service.extract
            benchmark.render=render
            slots=ModuleType('vero.isabelle.slots')
            from vero.isabelle.slots import block,enabled
            slots.block=block;slots.enabled=enabled
            slots.context=lambda bench,bodies:bench
            slots.schedule=lambda bench:{'c0001:prove':('Vero_A','proof')}
            planning=ModuleType('vero.isabelle.planning')
            planning.dependencies=lambda effective,theory:set()
            checker=ModuleType('vero.isabelle.checker')
            checker.filled=lambda body:bool(body and body!='oops')
            def evaluate(*args,**kwargs):
                frozen=Path(observer.audit_workspace_snapshot)
                self.assertNotEqual(frozen,observer.workspace)
                before=(frozen/'Vero_A.thy').read_bytes()
                self.assertIn(b'by simp',before)
                write('by auto')
                self.assertEqual((frozen/'Vero_A.thy').read_bytes(),before)
                observed.append(frozen)
                return {'modules':{},'contracts':{}}
            checker.evaluate=evaluate
            isabelle=ModuleType('vero.isabelle')
            isabelle.benchmark=benchmark;isabelle.slots=slots
            isabelle.planning=planning;isabelle.checker=checker
            vero=ModuleType('vero');vero.isabelle=isabelle
            modules={'vero':vero,'vero.isabelle':isabelle,
                'vero.isabelle.benchmark':benchmark,
                'vero.isabelle.slots':slots,
                'vero.isabelle.planning':planning,
                'vero.isabelle.checker':checker}
            observer.evaluated=Mock()
            with patch.dict(sys.modules,modules):
                with self.assertRaisesRegex(ValueError,'raw workspace changed'):
                    service.dispatch({'cmd':'check','module':'Vero_A'})
            self.assertEqual(len(observed),1)
            self.assertIsNone(getattr(observer,'audit_workspace_snapshot',None))

            # The original runner's turn-end evaluation bypasses the tool
            # service; its binding must establish the same immutable raw view.
            write('by simp')
            runner=SimpleNamespace(Checker=object(),CodexAgent=object(),
                evaluate=evaluate)
            bound_checker=SimpleNamespace(observer=observer,
                frozen_plan=service.frozen_plan)
            with patch.dict(sys.modules,modules),bind_runner(runner,bound_checker,object()):
                runner.evaluate()
            self.assertEqual(len(observed),2)
            self.assertFalse(observed[-1].exists())
            self.assertIsNone(getattr(observer,'audit_workspace_snapshot',None))

    def test_observer_notifies_clock_after_usage_is_durable(self):
        class Stop:
            def __init__(self):self.updated=0
            def usage_updated(self):self.updated+=1
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ledger=Ledger(root/'ledger','usage');stop=Stop()
            observer=VeroObserver(ledger,stop,root/'workspace',{'contracts':[]})
            events=root/'agent_events.jsonl'
            events.write_text('\n'.join((json.dumps({'type':'thread.started','thread_id':'abc'}),
                json.dumps({'type':'turn.completed','usage':{'input_tokens':8,
                    'output_tokens':2,'total_tokens':10}})))+'\n')
            observer.usage_events(events,'generation-1',final=True)
            self.assertEqual(stop.updated,1)
            snapshots=[r for r in ledger.rows if r['kind']=='usage_snapshot']
            self.assertEqual(snapshots[-1]['data']['total']['totalTokens'],10)

    def test_create_fresh_plan_writes_input_once_and_retries_rejected_plan(self):
        class Stop:
            def __init__(self):self.events=[]
            def begin_exclusion(self,reason):self.events.append(('begin',reason));return 'p'
            def end_exclusion(self,handle):self.events.append(('end',handle))
            def check(self):pass
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ledger=Ledger(root/'ledger','fresh-plan');stop=Stop()
            bench={'benchmark_id':'b','contracts':[{'id':'c1','module':'Original'}]}
            calls=[]
            class Agent:
                def __init__(self,config,log_dir):self.log_dir=Path(log_dir)
                def run(self,*,sandbox_dir,instruction_file):
                    calls.append(1);workspace=Path(sandbox_dir)
                    plan={'modules':[{'name':'Vero_A','contracts':[] if len(calls)==1 else ['c1'],
                        'imports':[]}],'execution_order':['Vero_A']}
                    (workspace/'PLAN.json').write_text(json.dumps(plan))
                    return {'ok':True}
            def compile_plan(input_bench,plan):
                if plan['modules'][0]['contracts']!=['c1']:raise ValueError('missing c1')
                return {'benchmark_id':'planned','contracts':[{'id':'c1','module':'Vero_A'}]}
            config={'agent':{}}
            plan,planned=create_fresh_plan(root=root,bench=bench,config=config,
                Agent=Agent,stop=stop,ledger=ledger,compile_plan=compile_plan)
            self.assertEqual(len(calls),2);self.assertEqual(planned['benchmark_id'],'planned')
            self.assertEqual(json.loads((root/'planning-workspace/benchmark.json').read_text()),bench)
            self.assertEqual(stop.events,[])
            self.assertEqual(sum(r['kind']=='plan_rejected' for r in ledger.rows),1)

    def test_planning_provider_transient_retries_but_auth_pauses(self):
        class Stop:
            def begin_exclusion(self,reason):return reason
            def end_exclusion(self,handle):pass
            def note_provider_failure(self):pass
            def check(self):pass
        failure={'category':'transient','failure':{'kind':'http_status',
            'http_status':503},'evidence':'turn.failed'}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ledger=Ledger(root/'ledger','planning');calls=[]
            clock=[0.0]
            class Agent:
                def __init__(self,config,log_dir):self.log_dir=Path(log_dir)
                def run(self,*,sandbox_dir,instruction_file):
                    calls.append(self.log_dir)
                    self.log_dir.mkdir(parents=True)
                    (self.log_dir/'state.json').write_text('{"status":"finished"}')
                    if len(calls)==1:
                        return {'ok':False,'returncode':1,'provider_failure':failure}
                    self.assert_plan_workspace(sandbox_dir)
                    (Path(sandbox_dir)/'PLAN.json').write_text(json.dumps({
                        'modules':[{'name':'Vero_A','contracts':['c1'],'imports':[]}],
                        'execution_order':['Vero_A']}))
                    return {'ok':True}
                @staticmethod
                def assert_plan_workspace(workspace):
                    assert (Path(workspace)/'benchmark.json').is_file()
            benchmark={'benchmark_id':'b','contracts':[{'id':'c1','module':'Original'}]}
            plan,planned=create_fresh_plan(root=root,bench=benchmark,config={'agent':{}},
                Agent=Agent,stop=Stop(),ledger=ledger,
                compile_plan=lambda b,p:{'benchmark_id':'planned',
                    'contracts':[{'id':'c1','module':'Vero_A'}]},
                clock=lambda:clock[0],
                sleep=lambda seconds:clock.__setitem__(0,clock[0]+seconds))
            self.assertEqual(len(calls),2)
            self.assertEqual(planned['contracts'][0]['module'],'Vero_A')
            self.assertEqual(clock[0],30)
            self.assertEqual(sum(r['kind']=='planning_provider_recovery_scheduled'
                for r in ledger.rows),1)

        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ledger=Ledger(root/'ledger','planning-auth')
            class AuthAgent:
                def __init__(self,config,log_dir):self.log_dir=Path(log_dir)
                def run(self,**kwargs):
                    self.log_dir.mkdir(parents=True)
                    (self.log_dir/'state.json').write_text('{"status":"finished"}')
                    return {'ok':False,'provider_failure':{
                        'category':'auth','failure':{'kind':'http_status',
                        'http_status':401},'evidence':'turn.failed'}}
            with self.assertRaisesRegex(ProviderRecoveryPause,
                    'provider_authentication_required'):
                create_fresh_plan(root=root,bench=benchmark,config={'agent':{}},
                    Agent=AuthAgent,stop=Stop(),ledger=ledger,
                    compile_plan=lambda b,p:None)
            self.assertFalse((root/'planning-0002').exists())

    def test_resume_artifacts_are_exactly_bound_and_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'workspace').mkdir();(root/'run').mkdir()
            planned={'benchmark_id':'b','contracts':[{'id':'c1','module':'Vero_A'}]}
            plan={'modules':[{'name':'Vero_A','contracts':['c1'],'imports':[]}],
                'execution_order':['Vero_A']}
            frozen=frozen_plan_binding(planned,plan)
            base={'client_path':'/fixed/codex',
                'activity_policy':ACTIVE_WALL_BUDGET_POLICY}
            values={'binding.json':{**base,'frozen_plan':frozen},
                'accepted-plan.json':plan,'planned-benchmark.json':planned,
                'config.json':expected_runtime_config(root,base,frozen)}
            for name,value in values.items():(root/name).write_text(json.dumps(value))
            restored=validate_resume_artifacts(root,base)
            self.assertEqual(restored[1],planned);self.assertEqual(restored[3],frozen)
            config=values['config.json'];config['agent']['model']='wrong'
            (root/'config.json').write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError,'configuration changed'):
                validate_resume_artifacts(root,base)
            config=expected_runtime_config(root,base,frozen)
            config['exploration_limits']['token_limit']=1
            (root/'config.json').write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError,'configuration changed'):
                validate_resume_artifacts(root,base)

    def test_provider_auth_policy_is_frozen_without_secret_material(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);seed=root/'auth.json'
            seed.write_text('{"refresh_token":"never-log-this"}')
            args=SimpleNamespace(auth=str(seed),auth_state_base=str(root/'private'),
                auth_lease_dir=str(root/'host-leases'))
            binding=provider_auth_binding(args)
            self.assertEqual(binding['auth_policy'],'durable-private-home-v1')
            self.assertEqual(binding['provider_recovery_window_seconds'],1800)
            self.assertNotIn('never-log-this',repr(binding))
            config=expected_runtime_config(root,
                {'client_path':'/fixed/codex',**binding},{})
            self.assertEqual(config['provider']['auth_state_base'],str(root/'private'))
            self.assertEqual(config['provider']['auth_lease_dir'],str(root/'host-leases'))
            self.assertEqual(config['provider']['recovery_window_seconds'],1800)
            self.assertNotIn('never-log-this',repr(config))
            (root/'workspace').mkdir();(root/'run').mkdir()
            plan={'modules':[{'name':'Vero_A','contracts':['c1'],'imports':[]}],
                'execution_order':['Vero_A']}
            planned={'benchmark_id':'b','contracts':[{'id':'c1','module':'Vero_A'}]}
            frozen=frozen_plan_binding(planned,plan)
            base={'client_path':'/fixed/codex',
                'activity_policy':ACTIVE_WALL_BUDGET_POLICY,**binding}
            files={'binding.json':{**base,'frozen_plan':frozen},
                'accepted-plan.json':plan,'planned-benchmark.json':planned,
                'config.json':expected_runtime_config(root,base,frozen)}
            for name,value in files.items():(root/name).write_text(json.dumps(value))
            self.assertEqual(validate_resume_artifacts(root,base)[3],frozen)
            with self.assertRaisesRegex(ValueError,'binding or frozen plan changed'):
                validate_resume_artifacts(root,{**base,
                    'auth_lease_dir':str(root/'different-leases')})
            legacy={key:value for key,value in base.items()
                if key not in binding}
            legacy['application_sha256']=SEAL_MIGRATION_FROM
            (root/'binding.json').write_text(json.dumps({**legacy,
                'frozen_plan':frozen}))
            with self.assertRaisesRegex(ValueError,'binding or frozen plan changed'):
                validate_resume_artifacts(root,{**base,
                    'application_sha256':'new-version'},seal_migration=True)

    def test_agent_failure_classification_ignores_proof_output_and_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'agent_events.jsonl').write_text('\n'.join(json.dumps(event) for event in (
                {'type':'item.completed','item':{'aggregated_output':'HTTP 401 secret=abc'}},
                {'type':'turn.failed','error':{'message':
                    'HTTP 503 Service Unavailable token=never-log-this'}}))+'\n')
            (root/'agent.stderr').write_text('')
            result=classify_agent_invocation(root)
            self.assertEqual(result['category'],'transient')
            self.assertEqual(result['failure']['http_status'],503)
            self.assertNotIn('never-log-this',repr(result))

    def test_resume_preflight_accepts_budget_pause_but_rejects_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger=Ledger(tmp,'resume-state')
            with self.assertRaisesRegex(ValueError,'active-wall budget state'):
                validate_resume_ledger(ledger)
            state={'policy':ACTIVE_WALL_BUDGET_POLICY,'finished':False,'reason':None}
            ledger.append(CHECKPOINT_KIND,boundary='close',state=state)
            self.assertEqual(validate_resume_ledger(ledger),state)
            ledger.append(CHECKPOINT_KIND,boundary='paused',state={**state,
                'paused':True,'reason':'active_wall_budget_reached'})
            with self.assertRaisesRegex(ValueError,'terminal'):
                validate_resume_ledger(ledger)
            ledger.append(CHECKPOINT_KIND,boundary='finished',state={**state,'finished':True})
            with self.assertRaisesRegex(ValueError,'cannot be resumed'):
                validate_resume_ledger(ledger)

    def test_seal_migration_preserves_frozen_artifacts_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'workspace').mkdir();(root/'run').mkdir()
            planned={'benchmark_id':'b','contracts':[{'id':'c1','module':'Vero_A'}]}
            plan={'modules':[{'name':'Vero_A','contracts':['c1'],'imports':[]}],
                'execution_order':['Vero_A']}
            frozen=frozen_plan_binding(planned,plan)
            current={'client_path':'/fixed/codex',
                'activity_policy':ACTIVE_WALL_BUDGET_POLICY,
                'application_sha256':'new-version'}
            original={**current,'application_sha256':SEAL_MIGRATION_FROM}
            files={'binding.json':{**original,'frozen_plan':frozen},
                'accepted-plan.json':plan,'planned-benchmark.json':planned,
                'config.json':expected_runtime_config(root,original,frozen)}
            for name,value in files.items():(root/name).write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,'binding or frozen plan changed'):
                validate_resume_artifacts(root,current)
            self.assertEqual(validate_resume_artifacts(root,current,
                seal_migration=True)[3],frozen)
            ledger=Ledger(root,'seal-migration')
            first=record_seal_migration(ledger)
            self.assertEqual(record_seal_migration(ledger)['event_id'],first['event_id'])
            self.assertEqual(first['data']['migration_id'],SEAL_MIGRATION_ID)
            self.assertEqual(len(ledger.rows),1)
            changed={**current,'model':'wrong'}
            with self.assertRaisesRegex(ValueError,'binding or frozen plan changed'):
                validate_resume_artifacts(root,changed,seal_migration=True)

    def test_checker_lifecycle_migration_preserves_frozen_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'workspace').mkdir();(root/'run').mkdir()
            planned={'benchmark_id':'b','contracts':[{'id':'c1','module':'Vero_A'}]}
            plan={'modules':[{'name':'Vero_A','contracts':['c1'],'imports':[]}],
                'execution_order':['Vero_A']}
            frozen=frozen_plan_binding(planned,plan)
            current={'client_path':'/fixed/codex',
                'activity_policy':ACTIVE_WALL_BUDGET_POLICY,
                'application_sha256':'checker-fixed-version'}
            original={**current,'application_sha256':CHECKER_LIFECYCLE_MIGRATION_FROM}
            files={'binding.json':{**original,'frozen_plan':frozen},
                'accepted-plan.json':plan,'planned-benchmark.json':planned,
                'config.json':expected_runtime_config(root,original,frozen)}
            for name,value in files.items():(root/name).write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,'binding or frozen plan changed'):
                validate_resume_artifacts(root,current)
            self.assertEqual(validate_resume_artifacts(root,current,
                checker_lifecycle_migration=True)[3],frozen)
            ledger=Ledger(root,'checker-migration')
            first=record_checker_lifecycle_migration(ledger)
            self.assertEqual(record_checker_lifecycle_migration(ledger)['event_id'],first['event_id'])
            self.assertEqual(first['data']['migration_id'],CHECKER_LIFECYCLE_MIGRATION_ID)
            self.assertEqual(len(ledger.rows),1)
            with self.assertRaisesRegex(ValueError,'binding or frozen plan changed'):
                validate_resume_artifacts(root,{**current,'model':'wrong'},
                    checker_lifecycle_migration=True)

    def test_agent_error_and_interruption_use_runner_owned_takeover(self):
        class Stop:
            def check(self):pass
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);journal=root/'iterations.json';ledger=Ledger(root/'ledger','r')
            calls=[];clock=[0.0]
            def now():return clock[0]
            def sleep(seconds):clock[0]+=seconds
            failure={'category':'transient','failure':{'kind':'http_status',
                'http_status':503},'evidence':'turn.failed'}
            def load(path):return json.loads(Path(path).read_text())
            def save(path,value):Path(path).write_text(json.dumps(value))
            def first(recovering):
                calls.append(recovering)
                if len(calls)==1:
                    save(journal,{'status':'agent_error','iterations':[
                        {'index':0,'agent':{'ok':False,'provider_failure':failure}}]})
                    return {'status':'agent_error'}
                return {'status':'complete'}
            result=run_with_agent_takeover(first,journal_path=journal,ledger=ledger,
                stop=Stop(),load=load,save=save,clock=now,sleep=sleep)
            self.assertEqual(result['status'],'complete');self.assertEqual(calls,[False,True])
            self.assertEqual(load(journal)['formal_takeover_count'],1)
            self.assertEqual(sum(r['kind']=='agent_provider_takeover' for r in ledger.rows),1)
            calls.clear()
            def interrupted(recovering):
                calls.append(recovering)
                if len(calls)==1:
                    raise RecoverableAgentFailure('lost',classification=failure,
                        checkpoint_verified=True) from OSError('provider')
                return {'status':'complete'}
            result=run_with_agent_takeover(interrupted,journal_path=journal,ledger=ledger,
                stop=Stop(),load=load,save=save,clock=now,sleep=sleep)
            self.assertEqual(result['status'],'complete');self.assertEqual(calls,[False,True])

    def test_auth_and_unknown_provider_errors_pause_without_replaying_checkpoint(self):
        class Stop:
            def check(self):pass
        for category,status,reason in [('auth',401,'provider_authentication_required'),
                ('unknown',None,'provider_failure_unclassified')]:
            with self.subTest(category=category),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);journal=root/'iterations.json';ledger=Ledger(root/'ledger','pause')
                failure=({'category':category,'failure':{'kind':'http_status',
                    'http_status':status},'evidence':'turn.failed'} if status else
                    {'category':'unknown','failure':None,'evidence':'none'})
                calls=[]
                def run_once(recovering):
                    calls.append(recovering)
                    journal.write_text(json.dumps({'status':'agent_error','iterations':[
                        {'index':0,'agent':{'ok':False,'provider_failure':failure}}]}))
                    return {'status':'agent_error'}
                def load(path):return json.loads(Path(path).read_text())
                with self.assertRaises(ProviderRecoveryPause) as raised:
                    run_with_agent_takeover(run_once,journal_path=journal,
                        ledger=ledger,stop=Stop(),load=load,
                        save=lambda p,v:Path(p).write_text(json.dumps(v)))
                self.assertEqual(raised.exception.reason,reason)
                self.assertEqual(calls,[False])
                self.assertEqual(load(journal)['status'],'agent_error')
                self.assertFalse(any(row['kind']=='agent_provider_takeover'
                    for row in ledger.rows))

    def test_explicit_resume_reopens_closed_agent_error_without_replay(self):
        class Stop:
            def check(self):pass
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);journal=root/'iterations.json';ledger=Ledger(root/'ledger','reopen')
            old={'status':'agent_error','iterations':[{'index':0,'agent':{
                'ok':False,'provider_failure':{'category':'unknown',
                    'failure':None,'evidence':'turn.failed'},'elapsed_seconds':12}}]}
            journal.write_text(json.dumps(old));calls=[]
            def load(path):return json.loads(Path(path).read_text())
            def save(path,value):Path(path).write_text(json.dumps(value))
            def run_once(recovering):
                calls.append(recovering)
                self.assertEqual(load(journal)['status'],'running')
                self.assertEqual(len(load(journal)['iterations']),1)
                return {'status':'complete'}
            with patch('regen.tools.vero_serial_application._agent_checkpoint_verified',
                    return_value=True):
                result=run_with_agent_takeover(run_once,journal_path=journal,
                    ledger=ledger,stop=Stop(),load=load,save=save,resume=True,
                    reopen_agent_error=True)
            self.assertEqual(result['status'],'complete')
            self.assertEqual(calls,[True])
            self.assertEqual(load(journal)['iterations'][0]['agent']['elapsed_seconds'],12)
            self.assertEqual(sum(r['kind']=='operator_agent_error_reopened'
                for r in ledger.rows),1)
            journal.write_text(json.dumps(old))
            with patch('regen.tools.vero_serial_application._agent_checkpoint_verified',
                    return_value=False):
                with self.assertRaisesRegex(ValueError,'verified terminated call'):
                    run_with_agent_takeover(run_once,journal_path=journal,
                        ledger=ledger,stop=Stop(),load=load,save=save,resume=True,
                        reopen_agent_error=True)

    def test_transient_recovery_exclusion_closes_at_next_generation_boundary(self):
        class Stop:
            def check(self):pass
        class Gate:
            def __init__(self):self.active=False;self.events=[]
            def begin(self,reason):self.active=True;self.events.append('begin')
            def close(self):self.active=False;self.events.append('close')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);journal=root/'iterations.json';ledger=Ledger(root/'ledger','timing')
            failure={'category':'transient','failure':{'kind':'http_status',
                'http_status':503},'evidence':'turn.failed'}
            clock=[0.0];gate=Gate();calls=[]
            def run_once(recovering):
                calls.append(recovering)
                if not recovering:
                    journal.write_text(json.dumps({'status':'agent_error',
                        'iterations':[{'index':0,'proved_specs':3,'agent':{
                            'ok':False,'provider_failure':failure}}]}))
                    return {'status':'agent_error'}
                self.assertTrue(gate.active)
                self.assertEqual(json.loads(journal.read_text())['iterations'][0]
                    ['proved_specs'],3)
                gate.close()  # the real Agent.run calls generation_started here
                return {'status':'complete'}
            result=run_with_agent_takeover(run_once,journal_path=journal,
                ledger=ledger,stop=Stop(),load=lambda p:json.loads(Path(p).read_text()),
                save=lambda p,v:Path(p).write_text(json.dumps(v)),
                recovery_gate=gate,clock=lambda:clock[0],
                sleep=lambda seconds:clock.__setitem__(0,clock[0]+seconds))
            self.assertEqual(result['status'],'complete')
            self.assertEqual(calls,[False,True])
            self.assertEqual(gate.events[:2],['begin','close'])
            self.assertEqual(json.loads(journal.read_text())['formal_takeover_count'],1)

    def test_consecutive_transient_recovery_pauses_at_frozen_window(self):
        class Stop:
            def check(self):pass
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);journal=root/'iterations.json';ledger=Ledger(root/'ledger','window')
            failure={'category':'transient','failure':{'kind':'http_status',
                'http_status':503},'evidence':'turn.failed'}
            clock=[0.0];calls=[]
            def run_once(recovering):
                calls.append(recovering)
                if recovering:clock[0]=1790.0
                journal.write_text(json.dumps({'status':'agent_error','iterations':[
                    {'index':len(calls)-1,'agent':{'ok':False,
                        'provider_failure':failure}}]}))
                return {'status':'agent_error'}
            with self.assertRaisesRegex(ProviderRecoveryPause,
                    'provider_recovery_window_exhausted'):
                run_with_agent_takeover(run_once,journal_path=journal,
                    ledger=ledger,stop=Stop(),
                    load=lambda p:json.loads(Path(p).read_text()),
                    save=lambda p,v:Path(p).write_text(json.dumps(v)),
                    clock=lambda:clock[0],
                    sleep=lambda seconds:clock.__setitem__(0,clock[0]+seconds))
            self.assertEqual(calls,[False,True])
            self.assertEqual(sum(r['kind']=='agent_provider_takeover'
                for r in ledger.rows),1)

    def test_classified_provider_pause_is_resumable_not_infrastructure_error(self):
        class Stop:
            reason=None
            event=SimpleNamespace(is_set=lambda:False)
            def cancel(self,reason):self.reason=reason
        stop=Stop()
        result=interrupted_result(stop,ProviderRecoveryPause(
            'provider_authentication_required'))
        self.assertEqual(result['status'],'paused')
        self.assertEqual(result['stop_reason'],'provider_authentication_required')
        self.assertTrue(result['resumable'])

    def test_entry_uses_active_wall_policy(self):
        self.assertEqual(ACTIVE_WALL_BUDGET_POLICY,
            'global-active-wall-budget-v1')

    def test_formal_inventory_gate_binds_exact_contracts(self):
        bench={'contracts':[{'id':'c1','module':'A'}]}
        with self.assertRaisesRegex(ValueError,'expected 287, found 1'):
            fixed_inventory_binding(bench,287)
        binding=fixed_inventory_binding(bench,1)
        self.assertEqual(binding['fixed_contract_count'],1)
        self.assertEqual(len(binding['fixed_contract_inventory_sha256']),64)

    def test_formal_configuration_binding_is_exact_and_persistable(self):
        args=SimpleNamespace(model='gpt-5.6-sol',reasoning='medium',
            expected_contracts=287,
            active_wall_seconds=172800)
        binding=formal_configuration_binding(args)
        self.assertEqual(binding,{'model':'gpt-5.6-sol','reasoning_effort':'medium',
            'expected_contract_count':287,'token_limit':None,
            'active_wall_seconds_limit':172800})
        self.assertEqual(FORMAL_EXPECTED_CONTRACTS,287)
        self.assertEqual(FORMAL_ACTIVE_WALL_SECONDS,172800)
        for field,value in (('expected_contracts',286),('active_wall_seconds',1),
                ('model','other'),('reasoning','high')):
            bad=SimpleNamespace(**vars(args));setattr(bad,field,value)
            with self.assertRaisesRegex(ValueError,'formal VERO experiment requires'):
                formal_configuration_binding(bad)
        legacy=SimpleNamespace(**vars(args),token_limit=1_000_000_000)
        with self.assertRaisesRegex(ValueError,'legacy token-limit'):
            formal_configuration_binding(legacy)

    def test_shared_native_policy_is_frozen_only_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            args=SimpleNamespace(model='gpt-5.6-sol',reasoning='medium',
                expected_contracts=287,active_wall_seconds=172800,
                shared_host_lock_dir=str(root/'native'),shared_host_slots=2,
                shared_host_cleanup_timeout=45)
            policy=formal_configuration_binding(args)
            self.assertEqual(policy['shared_host_lock_dir'],str(root/'native'))
            self.assertEqual(policy['shared_host_slots'],2)
            self.assertEqual(expected_runtime_config(root,
                {'client_path':'/fixed/codex',**policy},{})['checker'],
                {key:policy[key] for key in ('shared_host_lock_dir',
                    'shared_host_slots','shared_host_cleanup_timeout')})
            default=SimpleNamespace(**{key:value for key,value in vars(args).items()
                if not key.startswith('shared_host_')})
            self.assertNotIn('shared_host_lock_dir',formal_configuration_binding(default))
            self.assertEqual(expected_runtime_config(root,
                {'client_path':'/fixed/codex',**formal_configuration_binding(default)},
                {})['checker'],{})
            (root/'workspace').mkdir();(root/'run').mkdir()
            planned={'benchmark_id':'b','contracts':[{'id':'c1','module':'Vero_A'}]}
            plan={'modules':[{'name':'Vero_A','contracts':['c1'],'imports':[]}],
                  'execution_order':['Vero_A']}
            frozen=frozen_plan_binding(planned,plan)
            base={'client_path':'/fixed/codex',
                  'activity_policy':ACTIVE_WALL_BUDGET_POLICY,**policy}
            for name,value in {'binding.json':{**base,'frozen_plan':frozen},
                    'accepted-plan.json':plan,'planned-benchmark.json':planned,
                    'config.json':expected_runtime_config(root,base,frozen)}.items():
                (root/name).write_text(json.dumps(value))
            self.assertEqual(validate_resume_artifacts(root,base)[3],frozen)
            with self.assertRaisesRegex(ValueError,'binding or frozen plan changed'):
                validate_resume_artifacts(root,{**base,'shared_host_slots':1})

    def test_two_native_slots_are_frozen_for_new_run_only(self):
        args=SimpleNamespace(model='gpt-5.6-sol',reasoning='medium',
            expected_contracts=309,effective_seconds=172800,
            budget_mode='effective-time',native_slots=2)
        binding=formal_configuration_binding(args)
        self.assertEqual(binding['native_slots'],2)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            base={'client_path':'/fixed/codex',**binding}
            self.assertEqual(expected_runtime_config(root,base,{})['checker'],
                {'native_slots':2})
            (root/'workspace').mkdir();(root/'run').mkdir()
            plan={'modules':[{'name':'Vero_A','contracts':['c1'],'imports':[]}],
                'execution_order':['Vero_A']}
            planned={'benchmark_id':'b','contracts':[{'id':'c1','module':'Vero_A'}]}
            frozen=frozen_plan_binding(planned,plan)
            base['activity_policy']=EFFECTIVE_BUDGET_POLICY
            for name,value in {'binding.json':{**base,'frozen_plan':frozen},
                    'accepted-plan.json':plan,'planned-benchmark.json':planned,
                    'config.json':expected_runtime_config(root,base,frozen)}.items():
                (root/name).write_text(json.dumps(value))
            self.assertEqual(validate_resume_artifacts(root,base)[3],frozen)
            with self.assertRaisesRegex(ValueError,'binding or frozen plan changed'):
                validate_resume_artifacts(root,{**base,'native_slots':1})
        args.native_slots=1
        self.assertNotIn('native_slots',formal_configuration_binding(args))
        args.native_slots=3
        with self.assertRaisesRegex(ValueError,'native_slots'):
            formal_configuration_binding(args)

    def test_audited_resource_pool_migration_preserves_old_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'workspace').mkdir();(root/'run').mkdir()
            plan={'modules':[{'name':'Vero_A','contracts':['c1'],'imports':[]}],
                'execution_order':['Vero_A']}
            planned={'benchmark_id':'b','contracts':[{'id':'c1','module':'Vero_A'}]}
            frozen=frozen_plan_binding(planned,plan)
            old={'client_path':'/fixed/codex','activity_policy':EFFECTIVE_BUDGET_POLICY,
                'budget_mode':'effective-time','effective_seconds_limit':172800,
                'application_sha256':RESOURCE_POOL_MIGRATION_FROM,
                'native_slots':2,'shared_host_lock_dir':str(root/'pool'),
                'shared_host_slots':2,'shared_host_cleanup_timeout':30}
            new={k:v for k,v in old.items() if k!='native_slots'}
            new['application_sha256']='new-application'
            for name,value in {'binding.json':{**old,'frozen_plan':frozen},
                    'accepted-plan.json':plan,'planned-benchmark.json':planned,
                    'config.json':expected_runtime_config(root,old,frozen)}.items():
                (root/name).write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,'binding or frozen plan changed'):
                validate_resume_artifacts(root,new)
            self.assertEqual(validate_resume_artifacts(root,new,
                resource_pool_migration=True)[3],frozen)
            ledger=Ledger(root,'resource-migration')
            first=record_resource_pool_migration(ledger,{**old,'frozen_plan':frozen},new)
            self.assertEqual(record_resource_pool_migration(ledger,
                {**old,'frozen_plan':frozen},new)['event_id'],first['event_id'])
            self.assertEqual(first['data']['new_resources']['native_slots'],1)
            self.assertEqual(len(ledger.rows),1)
            with self.assertRaisesRegex(ValueError,'two-to-one slot binding'):
                validate_resume_artifacts(root,{**new,'shared_host_slots':1},
                    resource_pool_migration=True)

    def test_audited_auth_migration_preserves_old_state_and_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'workspace').mkdir();(root/'run').mkdir()
            plan={'modules':[{'name':'Vero_A','contracts':['c1'],'imports':[]}],
                'execution_order':['Vero_A']}
            planned={'benchmark_id':'b','contracts':[{'id':'c1','module':'Vero_A'}]}
            frozen=frozen_plan_binding(planned,plan)
            old={'client_path':'/fixed/codex','activity_policy':EFFECTIVE_BUDGET_POLICY,
                'budget_mode':'effective-time','effective_seconds_limit':172800,
                'application_sha256':RESOURCE_POOL_MIGRATION_FROM,
                'native_slots':2,'shared_host_lock_dir':str(root/'pool'),
                'shared_host_slots':2,'shared_host_cleanup_timeout':30,
                'auth_seed_path_sha256':'old-seed','auth_state_base':str(root/'old-auth'),
                'auth_lease_dir':str(root/'leases')}
            new={k:v for k,v in old.items() if k!='native_slots'}
            new.update(application_sha256='new-application',
                auth_seed_path_sha256=hashlib.sha256(str(
                    Path(PROVIDER_AUTH_MIGRATION_SEED).resolve()).encode()).hexdigest(),
                auth_state_base=PROVIDER_AUTH_MIGRATION_BASE)
            for name,value in {'binding.json':{**old,'frozen_plan':frozen},
                    'accepted-plan.json':plan,'planned-benchmark.json':planned,
                    'config.json':expected_runtime_config(root,old,frozen)}.items():
                (root/name).write_text(json.dumps(value))
            self.assertEqual(validate_resume_artifacts(root,new,
                resource_pool_migration=True,provider_auth_migration=True)[3],frozen)
            with self.assertRaisesRegex(ValueError,'audited independent VERO login'):
                validate_resume_artifacts(root,{**new,'auth_state_base':'/wrong'},
                    resource_pool_migration=True,provider_auth_migration=True)
            ledger=Ledger(root,'auth-migration')
            source={**old,'frozen_plan':frozen}
            resource=record_resource_pool_migration(ledger,source,new)
            event=record_provider_auth_migration(ledger,source,new,resource)
            self.assertEqual(record_provider_auth_migration(ledger,source,new,
                resource)['event_id'],event['event_id'])
            self.assertTrue(event['data']['old_auth_state_preserved'])
            reopened=record_runner_reopen_migration(ledger,source,new,event)
            self.assertEqual(record_runner_reopen_migration(ledger,source,new,
                event)['event_id'],reopened['event_id'])
            self.assertEqual(len(ledger.rows),3)

    def test_scope_supports_shared_theories_and_rejects_false_attribution(self):
        observer=SimpleNamespace(stop=SimpleNamespace(check=lambda:None),scope=None,
            ledger=SimpleNamespace(append=lambda *args,**kwargs:None),selected=[])
        observer.select_theory=lambda theory:observer.selected.append(theory)
        service=object.__new__(Service)
        service.checker=SimpleNamespace(observer=observer)
        service.bench={'contracts':[{'id':'c1','module':'A'},{'id':'c2','module':'B'}]}
        service.modules={'A','B'}
        result=service.dispatch({'cmd':'scope','target_ids':['c2'],'phase':'proof'})
        self.assertEqual(result['theory'],'B')
        self.assertEqual(result['theories'],['B'])
        self.assertEqual(result['allocation'],'declared-single-target')
        self.assertEqual(observer.selected,['B'])
        with self.assertRaisesRegex(ValueError,'do not belong'):
            service.dispatch({'cmd':'scope','theory':'A','target_ids':['c2'],'phase':'proof'})
        shared=service.dispatch({'cmd':'scope','theories':['B','A'],
            'target_ids':['c1','c2'],'phase':'proof'})
        self.assertEqual(shared['theories'],['A','B'])
        self.assertIsNone(shared['theory'])
        self.assertEqual(shared['allocation'],'shared')
        unattributed=service.dispatch({'cmd':'scope','target_ids':[],'phase':'analysis'})
        self.assertEqual(unattributed['theories'],[])
        self.assertEqual(unattributed['allocation'],'unattributed')
        with self.assertRaisesRegex(ValueError,'unowned target'):
            service.dispatch({'cmd':'scope','target_ids':['helper:A:h'],'phase':'helper'})
        helper=service.dispatch({'cmd':'scope','theory':'A',
            'target_ids':['helper:A:h'],'phase':'helper'})
        self.assertEqual(helper['allocation'],'declared-single-target')

    def test_shared_workspace_edit_does_not_require_per_theory_clock_scope(self):
        class Stop:
            def __init__(self):self.reason=None
            def observe(self,*args,**kwargs):pass
            def cancel(self,reason):self.reason=reason
            def check(self):
                if self.reason:raise RuntimeError(self.reason)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);workspace=root/'workspace';workspace.mkdir()
            source=workspace/'A.thy';source.write_text('first')
            stop=Stop();ledger=Ledger(root/'ledger','scope-audit')
            observer=VeroObserver(ledger,stop,workspace,
                {'contracts':[{'id':'c1','module':'A'}]})
            benchmark=ModuleType('vero.isabelle.benchmark')
            benchmark.extract=lambda *args:{'slots':{'c1:prove':{'found':True,'body':'oops'}}}
            slots=ModuleType('vero.isabelle.slots');slots.schedule=lambda bench:{'c1:prove':('A','proof')}
            isabelle=ModuleType('vero.isabelle');isabelle.benchmark=benchmark;isabelle.slots=slots
            vero=ModuleType('vero');vero.isabelle=isabelle
            modules={'vero':vero,'vero.isabelle':isabelle,
                'vero.isabelle.benchmark':benchmark,'vero.isabelle.slots':slots}
            with patch.dict(sys.modules,modules):
                observer.drafts()
                source.write_text('second version')
                observer.drafts()
            self.assertIsNone(stop.reason)
            self.assertFalse(any(r['kind']=='generation_clock_untrusted' for r in ledger.rows))

    def test_frozen_plan_binds_allocation_imports_and_service_benchmark(self):
        bench={'benchmark_id':'b','contracts':[{'id':'c1','module':'Vero_A'},{'id':'c2','module':'Vero_B'}]}
        plan={'version':1,'modules':[{'name':'Vero_A','contracts':['c1'],'imports':[]},
            {'name':'Vero_B','contracts':['c2'],'imports':['Vero_A']}],
            'execution_order':['Vero_A','Vero_B']}
        binding=frozen_plan_binding(bench,plan)
        self.assertNotEqual(binding['allocation_sha256'],binding['imports_sha256'])
        service=object.__new__(Service);service.bench=bench;service.frozen_plan=None
        service.checker=SimpleNamespace(set_frozen_plan=Mock())
        service.freeze_plan(binding);service.verify_plan()
        service.checker.set_frozen_plan.assert_called_once_with(binding)
        service.bench={**bench,'contracts':[{'id':'c1','module':'Vero_B'},{'id':'c2','module':'Vero_B'}]}
        with self.assertRaisesRegex(ValueError,'changed after freeze'):service.verify_plan()

    def test_frozen_plan_rejects_nonmatching_planned_benchmark(self):
        bench={'benchmark_id':'b','contracts':[{'id':'c1','module':'Vero_A'}]}
        plan={'modules':[{'name':'Vero_B','contracts':['c1'],'imports':[]}], 'execution_order':['Vero_B']}
        with self.assertRaisesRegex(ValueError,'differs'):frozen_plan_binding(bench,plan)

    def test_snapshot_rejects_symlink_and_copies_regular_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);workspace=root/'workspace';workspace.mkdir()
            (workspace/'Vero_T.thy').write_text('theory Vero_T imports Main begin end')
            snapshot(workspace,root/'first',{'Vero_T'})
            self.assertEqual((root/'first/Vero_T.thy').read_bytes(),(workspace/'Vero_T.thy').read_bytes())
            (workspace/'Vero_T.thy').unlink()
            (workspace/'Vero_T.thy').symlink_to('/etc/passwd')
            with self.assertRaises(OSError):snapshot(workspace,root/'second',{'Vero_T'})

    def test_production_output_name_cannot_overflow_unix_socket_path(self):
        root=Path('/data/zljj/vero-a-unified')/('very-long-release-name-'*4)
        endpoint_id=__import__('hashlib').sha256(str(root).encode()).hexdigest()[:20]
        socket_path=Path('/tmp')/('regen-vero-endpoint-'+endpoint_id)/'checker.sock'
        self.assertLess(len(str(socket_path).encode()),108)

    def test_binding_is_exclusive_and_restored_after_failure(self):
        runner=SimpleNamespace(Checker=object(),CodexAgent=object())
        original=(runner.Checker,runner.CodexAgent)
        checker=object();agent=object()
        with self.assertRaisesRegex(ValueError,'test'):
            with bind_runner(runner,checker,agent):
                self.assertIs(runner.Checker({}).native,checker)
                self.assertIs(runner.CodexAgent,agent)
                with self.assertRaises(RuntimeError):
                    with bind_runner(runner,checker,agent):pass
                raise ValueError('test')
        self.assertEqual((runner.Checker,runner.CodexAgent),original)

    def test_runner_close_does_not_close_host_owned_checker(self):
        runner=SimpleNamespace(Checker=object(),CodexAgent=object())
        checker=Mock()
        with bind_runner(runner,checker,object()):
            first=runner.Checker({})
            first.close()
            checker.close.assert_not_called()
            second=runner.Checker({})
            self.assertIs(second.native,checker)
            second.check('workspace','Vero_A')
            checker.check.assert_called_once_with('workspace','Vero_A')

    def test_successful_seal_is_validated_and_marks_clock_sealed(self):
        class Stop:
            def __init__(self):
                self.event=SimpleNamespace(is_set=lambda:False)
                self.selected=[];self.observed=[];self.sealed=[]
            def select(self,theory):self.selected.append(theory)
            def observe(self,*args,**kwargs):self.observed.append((args,kwargs))
            def mark_sealed(self,theory):self.sealed.append(theory)
        with tempfile.TemporaryDirectory() as tmp:
            ledger=Ledger(tmp,'observer-test');stop=Stop()
            bench={'contracts':[{'id':'c1','module':'Vero_A'}]}
            observer=VeroObserver(ledger,stop,Path(tmp)/'workspace',bench)
            checker=ModuleType('vero.isabelle.checker')
            checker.strip_comments=lambda source:source
            isabelle=ModuleType('vero.isabelle');isabelle.checker=checker
            vero=ModuleType('vero');vero.isabelle=isabelle
            result={'audited':['c1:prove'],'status':'ok','contract_status':'ok',
                'seal_status':'sealed','native_evidence':{'returncode':0},
                'build_evidence':{'returncode':0},'elapsed_seconds':1,
                'source_identity':'a'*64}
            with patch.dict(sys.modules,{'vero':vero,'vero.isabelle':isabelle,
                    'vero.isabelle.checker':checker}):
                observer.checked('Vero_A',result,'theory Vero_A imports Main begin end')
                observer.seal_completed('Vero_A',result)
            self.assertEqual(stop.selected,['Vero_A'])
            self.assertEqual(stop.sealed,['Vero_A'])
            self.assertEqual(stop.observed[0][0][0],'c1')
            self.assertEqual(stop.observed[0][0][1]['status'],'green')
