"""Explicit diagnostic clock, real journals, fake proof evidence; no native/model."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.execution import repository_execution as execution
from regen.execution.fact_context import manifest
from regen.execution.proof_commits import digest,durable
from regen.execution.proof_session_runner import acquire_run_mutation_lock,release_run_mutation_lock
from regen.infrastructure.effective_time_budget import EffectiveTimeBudget
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.parallel_generation_budget import initialize_parallel_budget
from regen.planning.proof_workspace_agent import WorkspaceController
from regen.tests.current.test_proof_commits import fixture,check
from regen.tools.checkpoint_trial_budget import initialize_checkpoint_trial_budget


class CheckpointTrialBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.source=self.base/'source'
        self.package,self.initial=fixture()
        self.theory=self.package['theory']
        self.package['package_payload_sha256']=digest(self.package)
        files=[{'theory':self.theory,'file_slot':self.package['file_slot']}]
        listing={'files':files}
        self.source_run={'run_id':'source','execution_mode':'repository-projection-v1',
            'execution_input_kind':'refine-dataset','environment_run':str(self.base/'environment'),
            'contract_package_dir':str(self.source/'packages'),'implementation':{},
            'execution_models':{'File Planner':'gpt-6-astra'},
            'session_schedule':{'source':'unused-schedule','file_list_sha256':digest(listing),'plan_id':'fixture'}}
        durable(self.source/'run.json',self.source_run)
        self.inputs={'file_list':listing,'files':files,
            'package_hashes':{self.package['file_slot']:self.package['package_payload_sha256']}}
        durable(self.source/'execution-input.json',self.inputs)
        durable(self.source/'packages'/(self.package['file_slot']+'.json'),self.package)
        durable(self.source/'theories'/self.theory/'imports.json',[])
        self.initial['batch_requests']=7
        self.source_journal=execution.journal(self.source,self.source_run,self.package)
        self.source_journal.append('initial',self.initial,{'check':check()})
        self.accept('helper')
        source_ledger=Ledger(self.source/'effective-generation-budget','source')
        now=[0.]
        stage=EffectiveTimeBudget(source_ledger,seconds=100,monotonic=lambda:now[0])
        stage.register({self.theory:['contract']});stage.select(self.theory)
        now[0]=17.;stage.cancel('operator_pause');stage.close()
        self.root,self.overview,self.checkpoint=self.fork('trial')
        self.binding={'mode':'parallel-file-planner-v1','selected_theories':[self.theory],
                      'native_slots':1,'model':'gpt-6-astra','generation_strategy':{'name':'baseline','instructions':''}}

    def accept(self,uid):
        old=self.source_journal.replay()
        new=deepcopy(old);new['proofs'][uid]='by simp';new['accepted_order'].append(uid)
        new['graph']=execution.context_graph(old['graph'],new['accepted_order'])
        self.source_journal.append('green',new,{'node_id':uid,'check':check(),
            'sealable_generation':True,'visibility':manifest(old['graph'],old['proofs'],uid)})

    def fork(self,name):
        root=self.base/name
        selected=self.source_journal.replay_snapshot()['commit_id']
        checkpoint={'source_run':str(self.source),'theories':{self.theory:{'commit_id':selected}}}
        def initialize(destination,*args,**kwargs):
            run={**self.source_run,'run_id':name,'contract_package_dir':str(destination/'packages')}
            durable(destination/'run.json',run)
            durable(destination/'execution-input.json',self.inputs)
            durable(destination/'packages'/(self.package['file_slot']+'.json'),self.package)
            return run
        with patch.object(execution,'init_run',side_effect=initialize),patch.object(execution,'progress',return_value={}):
            execution.fork_workspace_continuation(self.source,root,checkpoint=checkpoint)
        overview=WorkspaceController(root,theories=[self.theory],model='gpt-6-astra')
        self.addCleanup(overview.close)
        return root,overview,checkpoint

    def prepare(self,**kwargs):
        return initialize_checkpoint_trial_budget(self.root,self.overview,self.binding,
            target_contract_id=kwargs.pop('target_contract_id','contract'),
            authorization=kwargs.pop('authorization','explicit diagnostic test authorization'),
            seconds=kwargs.pop('seconds',900),**kwargs)

    def files(self,root):
        return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}

    def test_incremental_clock_keeps_source_and_acceptance_bytes_and_normal_runner_gate(self):
        before_source=self.files(self.source)
        accepted=self.files(self.root/'theories')
        result=self.prepare()
        self.assertEqual(result['scope'],'incremental_checkpoint_diagnostic_only')
        self.assertFalse(result['historical_costs_reset'])
        self.assertTrue(result['not_complete_experiment_cost'])
        self.assertEqual(result['inheritance'][self.theory]['inherited_requests'],7)
        self.assertEqual(result['inheritance'][self.theory]['inherited_helper_or_theorem_green'],1)
        self.assertEqual(self.files(self.source),before_source)
        self.assertEqual(self.files(self.root/'theories'),accepted)
        ledger=Ledger(self.root/'effective-generation-budget',self.overview.run['run_id'])
        data=next(r['data'] for r in ledger.rows if r['kind']=='parallel_budget_binding')
        self.assertEqual(data['configuration_sha256'],digest(self.binding))
        with self.assertRaisesRegex(ValueError,'explicit resume authorization'):
            initialize_parallel_budget(self.root,self.overview,self.binding,seconds=900)
        stage,bridge=initialize_parallel_budget(self.root,self.overview,self.binding,seconds=900,
            resume_authorization='launch approved diagnostic only')
        self.addCleanup(stage.close)
        self.assertEqual(stage.snapshot()['effective_seconds'],0)
        self.assertFalse(stage.snapshot()['gate_open'])
        self.assertEqual(stage.snapshot()['remaining_seconds'],900)
        self.assertEqual(self.files(self.source),before_source)
        with self.assertRaisesRegex(ValueError,'fresh unused fork'):
            self.prepare()

    def test_changed_policy_is_rejected_by_ordinary_runner(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError,'configuration binding changed'):
            initialize_parallel_budget(self.root,self.overview,{**self.binding,'native_slots':2},
                seconds=900,resume_authorization='test')

    def test_authorized_two_hour_window_resumes_with_source_and_costs_unchanged(self):
        before_source=self.files(self.source)
        accepted=self.files(self.root/'theories')
        receipt=self.prepare(seconds=7200)
        self.assertEqual(receipt['seconds'],7200)
        self.assertFalse(receipt['historical_costs_reset'])
        stage,_=initialize_parallel_budget(self.root,self.overview,self.binding,seconds=7200,
            resume_authorization='explicitly authorized two-hour diagnostic')
        self.addCleanup(stage.close)
        self.assertEqual(stage.snapshot()['remaining_seconds'],7200)
        self.assertEqual(self.files(self.source),before_source)
        self.assertEqual(self.files(self.root/'theories'),accepted)

    def test_partial_preparation_with_a_clock_checkpoint_is_not_runnable(self):
        append=Ledger.append
        def interrupt(ledger,kind,**fields):
            if kind=='checkpoint_diagnostic_window_prepared':
                raise OSError('simulated crash before prepared marker')
            return append(ledger,kind,**fields)
        with patch.object(Ledger,'append',new=interrupt):
            with self.assertRaisesRegex(OSError,'simulated crash'):
                self.prepare()
        ledger=Ledger(self.root/'effective-generation-budget',self.overview.run['run_id'])
        self.assertTrue(any(row['kind']=='time_budget_checkpoint' for row in ledger.rows))
        with self.assertRaisesRegex(ValueError,'incomplete checkpoint diagnostic'):
            initialize_parallel_budget(self.root,self.overview,self.binding,seconds=900,
                resume_authorization='must not repair partial preparation')
        with self.assertRaisesRegex(ValueError,'fresh unused fork'):
            self.prepare()

    def test_duplicate_prepared_markers_do_not_weaken_normal_runner_gate(self):
        result=self.prepare()
        ledger=Ledger(self.root/'effective-generation-budget',self.overview.run['run_id'])
        ledger.append('checkpoint_diagnostic_window_prepared',
                      authorization_event_id=result['authorization_event_id'],authorization_sha256='unbound')
        with self.assertRaisesRegex(ValueError,'incomplete checkpoint diagnostic'):
            initialize_parallel_budget(self.root,self.overview,self.binding,seconds=900,
                resume_authorization='test')

    def test_existing_runner_lock_prevents_clock_creation(self):
        token=acquire_run_mutation_lock(self.root,operation='parallel-workspace')
        try:
            with self.assertRaisesRegex(RuntimeError,'another runner'):
                self.prepare()
        finally:
            release_run_mutation_lock(token)
        self.assertFalse((self.root/'effective-generation-budget').exists())

    def test_post_fork_journal_request_is_not_a_fresh_checkpoint(self):
        log=execution.journal(self.root,self.overview.run,self.package)
        state=log.replay();state['batch_requests']+=1
        log.append('planner-request',state,{'request_number':8})
        before=self.files(self.root/'theories')
        with self.assertRaisesRegex(ValueError,'untouched continuation'):
            self.prepare()
        self.assertEqual(self.files(self.root/'theories'),before)
        self.assertFalse((self.root/'effective-generation-budget').exists())

    def test_launch_transcript_and_edit_artifacts_reject_before_budget_writes(self):
        for number,relative in enumerate(('service-launch.json','parallel-sessions/x/session-transcript.jsonl',
                                         'theories/'+self.theory+'/document/last-edit.json')):
            root,overview,_=self.fork('used-'+str(number))
            durable(root/relative,{'fixture':'already dispatched'})
            with self.subTest(relative=relative),self.assertRaisesRegex(ValueError,'fresh unused|post-fork'):
                initialize_checkpoint_trial_budget(root,overview,self.binding,target_contract_id='contract',
                    authorization='test')
            self.assertFalse((root/'effective-generation-budget').exists())

    def test_already_proved_fixed_target_rejects_initialization(self):
        self.accept('contract')
        root,overview,_=self.fork('already-green')
        with self.assertRaisesRegex(ValueError,'fixed unproved contract'):
            initialize_checkpoint_trial_budget(root,overview,self.binding,target_contract_id='contract',
                authorization='test')
        self.assertFalse((root/'effective-generation-budget').exists())

    def test_checkpoint_authority_mismatch_rejected(self):
        path=self.base/'different-checkpoint.json'
        different=deepcopy(self.checkpoint);different['theories'][self.theory]['commit_id']='wrong'
        durable(path,different)
        with self.assertRaisesRegex(ValueError,'authorized checkpoint'):
            self.prepare(checkpoint_path=path)
        self.assertFalse((self.root/'effective-generation-budget').exists())

    def test_no_authorization_no_unbounded_window_no_unknown_contract(self):
        for kwargs in ({'authorization':''},{'seconds':0},{'seconds':10801},
                       {'seconds':float('inf')},{'seconds':True},{'target_contract_id':'missing'}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):
                self.prepare(**kwargs)
        self.assertFalse((self.root/'effective-generation-budget').exists())


if __name__=='__main__':
    unittest.main()
