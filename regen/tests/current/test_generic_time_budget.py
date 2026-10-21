"""Generic adapter: budget binding, durable continuation and trusted cutoffs.

Model/native doubles drive the real runner, ledger, clock and event bridge. These
tests do not call an external model or claim a native proof acceptance result.
"""
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.generic_continuation import exclusive_run, continuation_ownership, prepare, prompt
from regen.infrastructure.incremental_log import enable, read_artifact, write_artifact
from regen.infrastructure.progress_events import emit_event, generation_exclusion
from regen.planning.generic_codex_agent import instructions
from regen.tools.generic_serial_application import budget_options, run


class GenericTimeBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def test_new_prompt_has_global_budget_no_contract_inactivity_stop(self):
        value=instructions(3600,effective_seconds=172800)
        self.assertIn('172800',value)
        self.assertIn('no-GREEN inactivity cap',value)
        self.assertNotIn('or when any theory accumulates',value)
        self.assertIn('never reset or extend the budget',value)
        self.assertIn('any theory accumulates 3600',instructions())

    def test_authorization_and_positive_extension_are_explicit(self):
        args=SimpleNamespace(budget_mode='effective-time',effective_seconds=172800)
        self.assertEqual(budget_options(args),('effective-time',172800,None,None))
        args.continue_from='source'
        with self.assertRaisesRegex(ValueError,'resume-authorization'):budget_options(args)
        args.resume_authorization='operator approval'
        args.extend_effective_seconds=86400
        self.assertEqual(budget_options(args)[3],86400)
        args.extend_effective_seconds=float('nan')
        with self.assertRaisesRegex(ValueError,'positive and finite'):budget_options(args)
        del args.continue_from
        with self.assertRaisesRegex(ValueError,'require --continue-from'):budget_options(args)

    def test_owner_lock_rejects_live_source_and_can_be_reacquired(self):
        with exclusive_run(self.root):
            with self.assertRaisesRegex(ValueError,'active owner'):
                with exclusive_run(self.root,source=True):pass
        with exclusive_run(self.root,source=True):pass

    def test_continuation_lineage_rejects_concurrent_budget_branches(self):
        source,target,dataset,config,_=self.fixture(accepted=False)
        prepare(target,source,dataset,config,3600,effective_seconds=100,
            resume_authorization='approved')
        write_artifact(target/'launch.json',{'config':config})
        with exclusive_run(target):pass
        with continuation_ownership(target) as lineage:
            self.assertEqual(set(lineage),{target,source})
            with self.assertRaisesRegex(ValueError,'active owner'):
                with continuation_ownership(source):pass
            with self.assertRaisesRegex(ValueError,'active owner'):
                with continuation_ownership(target):pass
        with continuation_ownership(source):pass
        with continuation_ownership(target):pass

    def test_continuation_lineage_rejects_cycles_and_relative_paths(self):
        source,_,_,_,_=self.fixture(accepted=False)
        write_artifact(source/'continuation-origin.json',{'source_root':str(source)})
        with self.assertRaisesRegex(ValueError,'cyclic'):
            with continuation_ownership(source):pass
        write_artifact(source/'continuation-origin.json',{'source_root':'relative'})
        with self.assertRaisesRegex(ValueError,'absolute source root'):
            with continuation_ownership(source):pass

    def test_independent_process_cannot_resume_a_live_lineage(self):
        source,_,_,_,_=self.fixture(accepted=False)
        script=('import sys\n'
            'from regen.infrastructure.generic_continuation import continuation_ownership\n'
            'try:\n'
            '  with continuation_ownership(sys.argv[1]): pass\n'
            'except ValueError as error:\n'
            '  sys.exit(0 if "active owner" in str(error) else 7)\n'
            'sys.exit(8)\n')
        with continuation_ownership(source):
            result=subprocess.run([sys.executable,'-c',script,str(source)],
                capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)

    def fixture(self, *, checkpoint=True, accepted=True, terminal=True):
        source=self.root/'old';source.mkdir();enable(source)
        with exclusive_run(source):pass
        dataset=self.root/'dataset';dataset.mkdir()
        config=dict(model='gpt-6-astra',effort='medium',threads=4,
            transport_idle_timeout=900,preflight_seconds=900,check_timeout=900,
            candidate_timeout=120,run_id='old',budget_mode='effective-time',
            time_budget_policy='global-effective-generation-budget-v1',
            application_sha256='implementation',model_config_sha256='models',
            planning_input_sha256='fixed-input')
        write_artifact(source/'launch.json',{'config':config,'dataset':str(dataset),
            'effective_seconds':100,'inactivity_seconds':3600})
        history=Ledger(source,'old')
        history.append('experiment_started')
        if checkpoint:
            history.append('time_budget_checkpoint',boundary='checkpoint',state={
                'effective_seconds':21.,'base_seconds':100.,'seconds':100.})
        history.append('usage_snapshot',thread_id='old-thread',total={'totalTokens':40},
            delta={'totalTokens':40},scope={})
        if accepted:
            current={'epoch':1,'run':str(source/'proof-runs/0001'),
                'plan_id':'plan','allocation':{'A':['a']}}
            write_artifact(source/'current-plan.json',current)
            write_artifact(source/'plans/0001/frozen.json',{'accepted':True})
            history.append('plan_accepted',**current)
        if terminal:
            history.append('experiment_finished',passed=False)
            write_artifact(source/'result.json',{'passed':False})
        target=self.root/'next';target.mkdir();enable(target)
        return source,target,dataset,config,history

    def test_fork_preserves_exact_clock_and_usage_events_after_unclean_exit(self):
        source,target,dataset,config,old=self.fixture(terminal=False)
        with patch('regen.execution.repository_execution.fork_workspace_continuation',
                side_effect=lambda a,b:b.mkdir(parents=True)):
            ledger,binding=prepare(target,source,dataset,config,3600,
                effective_seconds=100,resume_authorization='resume after crash')
        self.assertEqual(ledger.rows,old.rows)
        self.assertEqual(ledger.run_id,'old')
        self.assertFalse(binding['source_clean_shutdown'])
        self.assertEqual(binding['time_budget_base_seconds'],100)
        self.assertEqual(Ledger(source,'old').rows,old.rows)

    def test_active_wall_continuation_keeps_frozen_basis_and_costs(self):
        source,target,dataset,config,old=self.fixture(accepted=False,terminal=False)
        config={**config,'budget_mode':'active-wall',
            'time_budget_policy':'global-active-wall-budget-v1'}
        launch=read_artifact(source/'launch.json')
        launch.update(config=config,effective_seconds=None,wall_seconds=100)
        write_artifact(source/'launch.json',launch)
        ledger,binding=prepare(target,source,dataset,config,3600,
            wall_seconds=100,resume_authorization='resume after outage')
        self.assertEqual(ledger.rows,old.rows)
        self.assertEqual(binding['budget_mode'],'active-wall')
        self.assertEqual(binding['time_budget_base_seconds'],100)
        with self.assertRaisesRegex(ValueError,'time-budget mode differs'):
            prepare(self.root/'wrong',source,dataset,
                {**config,'budget_mode':'effective-time'},3600,
                effective_seconds=100,resume_authorization='resume')

    def test_legacy_clock_cannot_restart_time_at_zero(self):
        source,target,dataset,config,_=self.fixture()
        launch=read_artifact(source/'launch.json')
        launch['config']={k:v for k,v in config.items() if k!='budget_mode'}
        write_artifact(source/'launch.json',launch)
        with self.assertRaisesRegex(ValueError,'legacy inactivity history'):
            prepare(target,source,dataset,config,3600,effective_seconds=100,
                resume_authorization='approved')

    def test_base_allowance_and_executable_bindings_cannot_silently_change(self):
        source,target,dataset,config,_=self.fixture()
        for key in ('application_sha256','model_config_sha256','planning_input_sha256'):
            with self.assertRaisesRegex(ValueError,key):
                prepare(target,source,dataset,{**config,key:'different'},3600,
                    effective_seconds=100,resume_authorization='approved')
        with self.assertRaisesRegex(ValueError,'base effective budget differs'):
            prepare(target,source,dataset,config,3600,effective_seconds=200,
                resume_authorization='approved')

    def test_checkpoint_required_before_any_fork(self):
        source,target,dataset,config,_=self.fixture(checkpoint=False)
        with patch('regen.execution.repository_execution.fork_workspace_continuation') as fork:
            with self.assertRaisesRegex(ValueError,'persisted effective-time checkpoint'):
                prepare(target,source,dataset,config,3600,effective_seconds=100,
                    resume_authorization='approved')
            fork.assert_not_called()

    def test_unaccepted_planning_restarts_without_discarding_costs(self):
        source,target,dataset,config,old=self.fixture(accepted=False,terminal=False)
        ledger,binding=prepare(target,source,dataset,config,3600,effective_seconds=100,
            resume_authorization='approved')
        self.assertEqual(ledger.rows,old.rows)
        self.assertTrue(binding['planning_restarted'])
        self.assertIn('Restart repository planning',prompt(binding))
        self.assertFalse((target/'current-plan.json').exists())

    def test_accepted_plan_missing_pointer_is_not_reset_as_unplanned(self):
        source,target,dataset,config,_=self.fixture(accepted=False)
        Ledger(source,'old').append('plan_accepted',epoch=1)
        with self.assertRaisesRegex(ValueError,'accepted plan pointer missing'):
            prepare(target,source,dataset,config,3600,effective_seconds=100,
                resume_authorization='approved')

    def test_cli_defaults_to_48_hours_without_token_option(self):
        from regen.tools.generic_serial_application import main
        argv=['generic','--dataset','dataset','--output','output','--model-config','cfg',
            '--auth','auth','--model','gpt-6-astra','--reasoning','medium','--expected-contracts','287']
        with patch('sys.argv',argv),patch('regen.tools.generic_serial_application.run',
                return_value={'passed':True,'elapsed_seconds':0}) as invoke,redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as stopped:main()
        self.assertEqual(stopped.exception.code,False)
        args=invoke.call_args.args[0]
        self.assertEqual(args.budget_mode,'effective-time')
        self.assertEqual(args.effective_seconds,172800)
        self.assertFalse(hasattr(args,'token_budget'))

    def test_resume_restores_last_theory_attribution_from_small_transition(self):
        from regen.infrastructure.effective_time_budget import EffectiveTimeBudget
        from regen.planning.generic_codex_agent import Controller
        ledger=Ledger(self.root/'clock','same')
        first=EffectiveTimeBudget(ledger,100,monotonic=lambda:0)
        first.register({'A':['a'],'B':['b']});first.select('B');first.close()
        restored=EffectiveTimeBudget(ledger,100,resume_authorization='resume',monotonic=lambda:0)
        self.addCleanup(restored.close)
        proof=self.root/'proof';proof.mkdir()
        (proof/'execution-input.json').write_text(json.dumps({'files':[{'theory':'A'},{'theory':'B'}]}))
        controller=Controller.__new__(Controller)
        controller.ledger=ledger;controller.stage=restored
        controller.current={'run':str(proof),'epoch':1,'allocation':{'A':['a'],'B':['b']}}
        controller.register_activity_plan()
        self.assertEqual(restored.active_theory,'B')

    def test_real_runner_pauses_at_cumulative_limit_after_excluded_preparation(self):
        from regen.infrastructure.effective_time_budget import EffectiveTimeBudget
        client=self.root/'client';client.write_bytes(b'test-client')
        auth=self.root/'auth';auth.write_text('{}')
        cfg=self.root/'model-config';cfg.write_text('{}')
        (self.root/'repository-planning-input.json').write_text(json.dumps({'contracts':[{'contract_id':'a'}]}))
        args=SimpleNamespace(output=str(self.root/'out'),model_config=str(cfg),
            auth=str(auth),dataset=str(self.root),model='gpt-6-astra',reasoning='medium',
            idle_timeout=900,check_timeout=900,inactivity_seconds=3600,
            budget_mode='effective-time',effective_seconds=60,provider_retry_limit=0)
        now=[0.];stages=[];controllers=[]
        class Controller:
            def __init__(self,output,dataset,ledger,stage,config):
                self.ledger=ledger;self.scope={};self.resources=object()
                self.executor=Mock();self.planner=Mock();self.stage=stage
                stage.register({'A':['a']});stage.select('A')
                now[0]+=1000
                controllers.append(self)
            def handle(self,*args):pass
            def close(self):pass
        class Session:
            def __init__(self,**kwargs):
                self.thread_id='11111111-1111-1111-1111-111111111111'
                self.turn_id='t';self._usage={'total':{'totalTokens':101}}
            def __enter__(self):return self
            def __exit__(self,*args):emit_event('theory_generation_stopped')
            def run(self,*args,**kwargs):
                emit_event('theory_generation_started')
                now[0]+=20
                with generation_exclusion('prefix_preparation'):now[0]+=1000
                stages[0].observe('a',{'status':'green'},kind='validation',theory='A')
                now[0]+=40
                stages[0].check()
                raise AssertionError('budget failed to stop model turn')
        def make_stage(ledger,seconds,**kwargs):
            stage=EffectiveTimeBudget(ledger,seconds,monotonic=lambda:now[0],**kwargs)
            stage.start=Mock();stages.append(stage);return stage
        with redirect_stdout(io.StringIO()), \
             patch('regen.tools.generic_serial_application.model_settings',return_value={
                'codex_bin':str(client),'codex_sha256':hashlib.sha256(client.read_bytes()).hexdigest(),
                'role_models':{'File Planner':'gpt-6-astra'}}), \
             patch('regen.tools.generic_serial_application.Controller',Controller), \
             patch('regen.tools.generic_serial_application.CodexWorkspaceSession',Session), \
             patch('regen.infrastructure.effective_time_budget.EffectiveTimeBudget',make_stage):
            report=run(args)
        self.assertFalse(report['passed'])
        self.assertIn('generation_clock',report,report)
        self.assertTrue(report['generation_clock']['paused'])
        self.assertEqual(report['generation_clock']['effective_seconds'],60)
        self.assertNotIn('AssertionError',str(report.get('error')))
        self.assertEqual(report['generation_clock']['seconds'],60)
        with exclusive_run(self.root/'out',source=True):pass

    def test_interrupt_resume_cutoff_and_extension_keep_time_and_token_ledger(self):
        from regen.infrastructure.effective_time_budget import EffectiveTimeBudget
        from regen.infrastructure.experiment_activity import observed_usage
        client=self.root/'client';client.write_bytes(b'test-client')
        auth=self.root/'auth';auth.write_text('{}')
        cfg=self.root/'model-config';cfg.write_text('{}')
        (self.root/'repository-planning-input.json').write_text(json.dumps({'contracts':[{'contract_id':'a'}]}))
        base=dict(model_config=str(cfg),auth=str(auth),dataset=str(self.root),
            model='gpt-6-astra',reasoning='medium',idle_timeout=900,check_timeout=900,
            inactivity_seconds=3600,budget_mode='effective-time',effective_seconds=60,
            provider_retry_limit=0)
        now=[0.];stages=[];controllers=[];sessions=[]
        class Controller:
            def __init__(self,output,dataset,ledger,stage,config):
                self.ledger=ledger;self.scope={};self.resources=object()
                self.executor=Mock();self.planner=Mock();self.stage=stage
                stage.register({'A':['a']});stage.select('A')
                now[0]+=1000;controllers.append(self)
            def handle(self,*args):pass
            def close(self):pass
            def observe_updates(self):pass
        class Session:
            def __init__(self,**kwargs):
                self.index=len(sessions);sessions.append(self)
                self.thread_id=f'{self.index+1:08d}-1111-1111-1111-111111111111'
                self.turn_id='turn';self._usage={'total':{'totalTokens':100*(self.index+1)}}
            def __enter__(self):return self
            def __exit__(self,*args):emit_event('theory_generation_stopped')
            def run(self,*args,**kwargs):
                emit_event('theory_generation_started')
                if self.index:
                    # This real run must retain the source ownership throughout
                    # model execution, not just while copying its checkpoint.
                    with self_outer.assertRaises(ValueError):
                        with continuation_ownership(self_outer.root/'one'):pass
                now[0]+=(20,40,10)[self.index]
                if self.index==0:raise RuntimeError('test infrastructure interruption')
                stages[-1].check()
                controllers[-1].ledger.append('integrated',verified=True)
                return {'usage':self._usage}
        def make_stage(ledger,seconds,**kwargs):
            stage=EffectiveTimeBudget(ledger,seconds,monotonic=lambda:now[0],**kwargs)
            stage.start=Mock();stages.append(stage);return stage
        self_outer=self
        with redirect_stdout(io.StringIO()), \
             patch('regen.tools.generic_serial_application.model_settings',return_value={
                'codex_bin':str(client),'codex_sha256':hashlib.sha256(client.read_bytes()).hexdigest(),
                'role_models':{'File Planner':'gpt-6-astra'}}), \
             patch('regen.tools.generic_serial_application.application_signature',return_value='frozen-test-code'), \
             patch('regen.tools.generic_serial_application.Controller',Controller), \
             patch('regen.tools.generic_serial_application.CodexWorkspaceSession',Session), \
             patch('regen.infrastructure.effective_time_budget.EffectiveTimeBudget',make_stage):
            first=run(SimpleNamespace(**base,output=str(self.root/'one')))
            now[0]+=100000  # Process downtime is not effective generation.
            second=run(SimpleNamespace(**base,output=str(self.root/'two'),
                continue_from=str(self.root/'one'),resume_authorization='recover accepted workspace'))
            now[0]+=100000
            third=run(SimpleNamespace(**base,output=str(self.root/'three'),
                continue_from=str(self.root/'two'),resume_authorization='extend after saved cutoff',
                extend_effective_seconds=60))
        self.assertEqual(first['stop_reason'],'infrastructure_error',first)
        self.assertEqual(first['status'],'paused')
        self.assertTrue(first['resumable'])
        self.assertEqual(first['generation_clock']['effective_seconds'],20)
        self.assertEqual(second['stop_reason'],'effective_time_budget_reached',second)
        self.assertEqual(second['generation_clock']['effective_seconds'],60)
        self.assertTrue(third['passed'],third)
        self.assertEqual(third['status'],'completed')
        self.assertEqual(third['generation_clock']['effective_seconds'],70)
        self.assertEqual(third['generation_clock']['seconds'],120)
        launch=read_artifact(self.root/'three/launch.json')
        self.assertEqual(launch['effective_seconds'],60)
        ledger=Ledger(self.root/'three','one')
        self.assertEqual(observed_usage(ledger)['total_tokens'],600)
        self.assertEqual(sum(r['kind']=='time_budget_extended' for r in ledger.rows),1)
        self.assertEqual(sum(r['kind']=='experiment_paused' for r in ledger.rows),2)
        self.assertEqual(sum(r['kind']=='experiment_finished' for r in ledger.rows),1)
        self.assertTrue(any(r['kind']=='termination' and
            r['data']['reason']=='effective_time_budget_reached' for r in ledger.rows))

    def test_clock_close_disk_failure_still_closes_controller_and_resets_environment(self):
        from regen.infrastructure.effective_time_budget import EffectiveTimeBudget
        from regen.execution.check_executor import CURRENT_RESOURCES
        client=self.root/'client';client.write_bytes(b'test-client')
        auth=self.root/'auth';auth.write_text('{}')
        cfg=self.root/'model-config';cfg.write_text('{}')
        (self.root/'repository-planning-input.json').write_text(json.dumps({'contracts':[{'contract_id':'a'}]}))
        args=SimpleNamespace(output=str(self.root/'out'),model_config=str(cfg),
            auth=str(auth),dataset=str(self.root),model='gpt-6-astra',reasoning='medium',
            idle_timeout=900,check_timeout=900,inactivity_seconds=3600,
            budget_mode='effective-time',effective_seconds=60)
        controllers=[]
        class Controller:
            def __init__(self,output,dataset,ledger,stage,config):
                self.ledger=ledger;self.scope={};self.resources=object()
                self.executor=Mock();self.planner=Mock();self.close=Mock()
                stage.register({'A':['a']});stage.select('A');controllers.append(self)
            def handle(self,*args):pass
        class Session:
            def __init__(self,**kwargs):
                self.thread_id='11111111-1111-1111-1111-111111111111'
                self.turn_id='turn';self._usage={'total':{'totalTokens':10}}
            def __enter__(self):return self
            def __exit__(self,*args):emit_event('theory_generation_stopped')
            def run(self,*args,**kwargs):
                emit_event('theory_generation_started')
                raise RuntimeError('injected provider failure before close')
        resources_before=CURRENT_RESOURCES.get()
        with redirect_stdout(io.StringIO()),patch.dict(os.environ,{'REGEN_MODEL_CONFIG':'original-binding'}), \
             patch('regen.tools.generic_serial_application.model_settings',return_value={
                'codex_bin':str(client),'codex_sha256':hashlib.sha256(client.read_bytes()).hexdigest(),
                'role_models':{'File Planner':'gpt-6-astra'}}), \
             patch('regen.tools.generic_serial_application.Controller',Controller), \
             patch('regen.tools.generic_serial_application.CodexWorkspaceSession',Session), \
             patch.object(EffectiveTimeBudget,'close',side_effect=OSError('disk full saving final clock')):
            report=run(args)
            self.assertEqual(os.environ['REGEN_MODEL_CONFIG'],'original-binding')
            self.assertIs(CURRENT_RESOURCES.get(),resources_before)
        controllers[0].close.assert_called_once()
        self.assertFalse(report['passed'])
        self.assertFalse(report['resumable'])
        self.assertTrue(report['recovery_requires_reconciliation'])
        self.assertEqual(report['clock_persistence_error']['type'],'OSError')
        self.assertEqual(report['status'],'paused')
        with exclusive_run(self.root/'out',source=True):pass


if __name__=='__main__':unittest.main()
