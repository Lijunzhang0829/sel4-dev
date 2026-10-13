import tempfile
from pathlib import Path
import json
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.vero_experiment_observer import VeroObserver
from regen.tools.vero_serial_application import (
    FORMAL_ACTIVE_WALL_SECONDS,FORMAL_EXPECTED_CONTRACTS,
    ACTIVE_WALL_BUDGET_POLICY,CHECKPOINT_KIND,
    RecoverableAgentFailure,create_fresh_plan,expected_runtime_config,
    run_with_agent_takeover,
    Service,snapshot,bind_runner,fixed_inventory_binding,formal_configuration_binding,
    frozen_plan_binding,validate_resume_artifacts,validate_resume_ledger,
    SEAL_MIGRATION_FROM,SEAL_MIGRATION_ID,record_seal_migration,
)


class VeroSerialTests(unittest.TestCase):
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
            self.assertEqual(stop.events,[('begin','planning'),('end','p')])
            self.assertEqual(sum(r['kind']=='plan_rejected' for r in ledger.rows),1)

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

    def test_agent_error_and_interruption_use_runner_owned_takeover(self):
        class Stop:
            def check(self):pass
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);journal=root/'iterations.json';ledger=Ledger(root/'ledger','r')
            calls=[]
            def load(path):return json.loads(Path(path).read_text())
            def save(path,value):Path(path).write_text(json.dumps(value))
            def first(recovering):
                calls.append(recovering)
                if len(calls)==1:
                    save(journal,{'status':'agent_error','iterations':[
                        {'index':0,'agent':{'ok':False}}]})
                    return {'status':'agent_error'}
                return {'status':'complete'}
            result=run_with_agent_takeover(first,journal_path=journal,ledger=ledger,
                stop=Stop(),load=load,save=save)
            self.assertEqual(result['status'],'complete');self.assertEqual(calls,[False,True])
            self.assertEqual(load(journal)['formal_takeover_count'],1)
            self.assertEqual(sum(r['kind']=='agent_provider_takeover' for r in ledger.rows),1)
            calls.clear()
            def interrupted(recovering):
                calls.append(recovering)
                if len(calls)==1:raise RecoverableAgentFailure('lost') from OSError('provider')
                return {'status':'complete'}
            result=run_with_agent_takeover(interrupted,journal_path=journal,ledger=ledger,
                stop=Stop(),load=load,save=save)
            self.assertEqual(result['status'],'complete');self.assertEqual(calls,[False,True])

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
                self.assertIs(runner.Checker({}),checker)
                self.assertIs(runner.CodexAgent,agent)
                with self.assertRaises(RuntimeError):
                    with bind_runner(runner,checker,agent):pass
                raise ValueError('test')
        self.assertEqual((runner.Checker,runner.CodexAgent),original)

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
