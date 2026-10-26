"""Legacy silent tails stay charged until their bound replacements respond."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from regen.execution.proof_commits import digest, durable
from regen.infrastructure.effective_time_budget import CHECKPOINT_KIND, POLICY
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.incremental_log import read_artifact
from regen.infrastructure.parallel_generation_budget import carry_parallel_budget
from regen.infrastructure.provider_feedback_continuation import (
    plan_legacy_feedback_import, append_legacy_feedback_import,
)
from regen.infrastructure.provider_request_accounting import ProviderRequestAccounting
from regen.planning.parallel_file_planner import _feedback_recovery_state


class LegacyFeedbackContinuationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.source = self.root/'source'; self.target = self.root/'target'
        self.run = {'run_id':'source'}; self.new = {'run_id':'target'}
        self.policy = {'execution_policy':{'stages':[], 'session_stall_retries':0}}
        durable(self.source/'run.json', self.run)
        durable(self.source/'parallel-workspace-policy.json', self.policy)
        self.rows = {}
        for theory, start, kind in [('A',11,'SessionStalled'), ('B',21,'SessionCancelled')]:
            folder = self.source/'parallel-sessions'/'cohort'/theory
            durable(folder/'attempt.json', {'attempt':1,'theory':theory})
            durable(folder/'runner-status.json', {'running':False,'stopped_at':'stopped','theory':theory})
            call = {'threadId':'thread-'+theory,'turnId':'turn-'+theory,'callId':'call-'+theory,
                'tool':'proof_action','arguments':{'theory':theory,'proposal':{'action':'edit_theory'}}}
            output = {'success':True,'contentItems':[]}
            def row(t,direction,message): return {'time_unix':t,'direction':direction,'message':message}
            self.rows[theory] = [
                row(start-1,'in',{'raw_json':json.dumps({'method':'item/tool/call','id':1,'params':call})}),
                row(start-.1,'local',{'event':'tool_finished','call_id':call['callId'],'response':output}),
                row(start,'out',{'id':1,'result':output}),
                row(start+.01,'in',{'raw_json':json.dumps({'method':'item/completed','params':{
                    'threadId':call['threadId'],'turnId':call['turnId'],
                    'item':{'id':call['callId'],'type':'dynamicToolCall'}}})}),
                row(111,'local',{'event':'session_failed','nonsealable':True,'exception_type':kind,
                    'message':'no new tool information or proof action within progress deadline'
                        if kind=='SessionStalled' else 'workspace coordinator cancelled'}),
                row(111.5,'local',{'event':'session_closed','nonsealable':True}),
            ]
            self.save_transcript(theory)
        ledger = Ledger(self.source/'effective-generation-budget','source',clock=lambda:0.)
        ledger.append('parallel_budget_binding', policy=POLICY,run_sha256=digest(self.run),
            contracts_sha256=digest({'A':['a'],'B':['b']}),configuration_sha256=digest(self.policy))
        ledger.append('time_budget_registered',contract_owners={'a':'A','b':'B'},theory_ids=['A','B'],
            allocation_sha256=digest({'a':'A','b':'B'}),baseline_green=[])
        for timestamp in [10.,20.,30.,60.,90.,110.]:
            ledger.clock = lambda timestamp=timestamp:timestamp
            ledger.append(CHECKPOINT_KIND,boundary='periodic',state=self.state(timestamp))
        ledger.clock = lambda:110.9
        ledger.append('generation_clock_untrusted',theory='A', evidence={
            'reason':'session_stopped_with_open_model_item','thread_id':'thread-A',
            'turn_id':'turn-A','item_ids':['unclosed-item']})
        ledger.clock = lambda:112.
        ledger.append(CHECKPOINT_KIND,boundary='close',state=self.state(110.,paused=True))
        self.source_rows = deepcopy(ledger.rows)

    @staticmethod
    def state(seconds,paused=False):
        return {'policy':POLICY,'seconds':172800.,'base_seconds':172800.,'checkpoint_seconds':30.,
            'effective_seconds':seconds,'worker_seconds':(seconds-3)*2,
            'theory_seconds':{'A':seconds-3,'B':seconds-3},'planning_seconds':3.,
            'include_planning':True,'planning_active':False,'gate_open':not paused,'paused':paused,
            'finished':False,'unclean_recoveries':0,'next_exclusion':0,
            'reason':'effective_clock_evidence_error' if paused else None,'finish_reason':None}

    def save_transcript(self,theory):
        path = self.source/'parallel-sessions'/'cohort'/theory/'session-transcript.jsonl'
        path.write_text(''.join(json.dumps(row)+'\n' for row in self.rows[theory]))

    def prepare_target(self):
        durable(self.target/'run.json',self.new)
        durable(self.target/'workspace-continuation.json',{'source_run':str(self.source)})

    def carry(self,proposal=None,**kwargs):
        if not self.target.exists():self.prepare_target()
        proposal = proposal or plan_legacy_feedback_import(self.source)
        return carry_parallel_budget(self.source,self.target,authorization='user-approved-retry',
            clock_review=proposal['clock_review'],provider_feedback_review=proposal,
            target_execution_policy={**self.policy['execution_policy'],'provider_feedback_retries':1},**kwargs)

    def test_review_uses_complete_brackets_and_retains_shutdown(self):
        proposal = plan_legacy_feedback_import(self.source)
        self.assertEqual(proposal['refund_seconds'],0)
        self.assertEqual(len(proposal['requests']),2)
        self.assertEqual(sum(s['seconds'] for s in proposal['segments']),90)
        self.assertTrue(proposal['boundary_review']['partial_boundary_brackets_retained'])
        self.assertEqual(proposal['boundary_review']['brackets'][-1]['end_unix'],110)
        self.assertEqual(Ledger(self.source/'effective-generation-budget','source').rows,self.source_rows)

    def test_carry_keeps_all_costs_then_success_credits_union_only_once(self):
        result = self.carry()
        self.assertEqual(result['carried_effective_seconds'],110)
        ledger=Ledger(self.target/'effective-generation-budget','target')
        state=[r['data']['state'] for r in ledger.rows if r['kind']==CHECKPOINT_KIND][-1]
        self.assertEqual(state['effective_seconds'],110)
        self.assertEqual(state['planning_seconds'],3)
        self.assertTrue(state['include_planning'])
        self.assertEqual(state['excluded_failed_request_seconds'],0)
        accounting=ProviderRequestAccounting.restore(state['provider_request_accounting'],
            effective_seconds=110,worker_seconds=214,theory_seconds={'A':107,'B':107})
        for theory in ['A','B']:
            pending=_feedback_recovery_state(ledger.rows,theory)
            self.assertEqual(pending['retries'],1);self.assertFalse(pending['started'])
            accounting.begin(theory,theory,'retry-'+theory)
            accounting.finish(theory,'retry-'+theory,'success')
            receipt=accounting.credit(pending['failed_request_id'],'retry-'+theory)
            self.assertEqual(receipt['effective_seconds'],0 if theory=='A' else 80)
            self.assertEqual(accounting.credit(pending['failed_request_id'],'retry-'+theory),receipt)
        self.assertEqual(accounting.summary()['excluded_failed_request_seconds'],80)
        self.assertEqual(accounting.raw_effective_seconds,110)
        self.assertEqual(Ledger(self.source/'effective-generation-budget','source').rows,self.source_rows)

    def test_changed_transcript_invalidates_prepared_review(self):
        proposal=plan_legacy_feedback_import(self.source)
        self.rows['A'][0]['message']['raw_json']=self.rows['A'][0]['message']['raw_json']+' '
        self.save_transcript('A')
        with self.assertRaisesRegex(ValueError,'review or source evidence changed'):self.carry(proposal)
        self.assertFalse((self.source/'provider-feedback-transfer.json').exists())

    def test_model_feedback_in_tail_is_never_silence(self):
        self.rows['A'].insert(-2,{'time_unix':100.,'direction':'in','message':{'raw_json':json.dumps({
            'method':'item/agentMessage/delta','params':{'delta':'real response'}})}})
        self.save_transcript('A')
        with self.assertRaisesRegex(ValueError,'feedback exists'):plan_legacy_feedback_import(self.source)

    def test_cancelled_workers_without_primary_stall_cannot_import(self):
        self.rows['A'][-2]['message'].update(exception_type='SessionCancelled',message='workspace coordinator cancelled')
        self.save_transcript('A')
        with self.assertRaisesRegex(ValueError,'stalled coordinator peer'):plan_legacy_feedback_import(self.source)

    def test_missing_tool_completion_and_live_owner_are_rejected(self):
        self.rows['A'][1]['message']['response']={'success':False}
        self.save_transcript('A')
        with self.assertRaisesRegex(ValueError,'trusted tool completion'):plan_legacy_feedback_import(self.source)
        durable(self.source/'parallel-sessions/cohort/A/runner-status.json',
            {'running':True,'stopped_at':None,'theory':'A'})
        with self.assertRaisesRegex(ValueError,'stopped first-attempt'):plan_legacy_feedback_import(self.source)

    def test_unsupported_uncertainty_and_policy_change_are_rejected(self):
        proposal=plan_legacy_feedback_import(self.source);self.prepare_target()
        with self.assertRaisesRegex(ValueError,'one-resend policy change'):
            carry_parallel_budget(self.source,self.target,authorization='user',clock_review=proposal['clock_review'],
                provider_feedback_review=proposal,target_execution_policy={'session_stall_retries':2})
        with self.assertRaisesRegex(ValueError,'unsupported uncertainty'):
            carry_parallel_budget(self.source,self.target,authorization='user',clock_review=proposal['clock_review'])

    def test_second_destination_cannot_spend_same_legacy_tail(self):
        self.carry();self.target=self.root/'another';self.new={'run_id':'another'}
        with self.assertRaisesRegex(ValueError,'already claimed'):self.carry()

    def test_reimport_or_launched_target_is_rejected(self):
        proposal=plan_legacy_feedback_import(self.source);self.carry(proposal)
        with self.assertRaisesRegex(ValueError,'untouched complete budget carry'):
            append_legacy_feedback_import(self.source,self.target,authorization='user',proposal=proposal)
        durable(self.target/'service-launch.json',{})
        with self.assertRaisesRegex(ValueError,'unlaunched continuation'):
            append_legacy_feedback_import(self.source,self.target,authorization='user',proposal=proposal)

    def test_prepared_command_preserves_plan_model_budget_and_skips_planning_claim(self):
        from regen.tools.provider_feedback_continuation import replacement_experiment
        source_command=['/usr/bin/python3','-m','regen.execution.proof_session_runner','parallel-workspace',
            '--run-dir',str(self.source),'--planner-model','gpt-5.6-sol','--effective-seconds','172800',
            '--planning-budget','/old/planning','--allow-external-call']
        durable(self.source/'experiment.json',{'command':source_command,'model':'gpt-5.6-sol',
            'reasoning_effort':'xhigh','effective_seconds':172800,'include_planning':True,
            'contract_visibility':'owner-blind-v1','planning_budget_receipt_sha256':'original-hash'})
        result=replacement_experiment(self.source,self.target,self.root/'application','user')
        self.assertNotIn('--planning-budget',result['command'])
        self.assertIn('--resume-authorization',result['command'])
        self.assertEqual(result['planning_budget_receipt_sha256'],'original-hash')
        self.assertEqual(result['effective_seconds'],172800)
        self.assertFalse(result['historical_costs_reset']);self.assertFalse(result['planning_repeated'])
        self.assertEqual(read_artifact(self.source/'experiment.json')['command'],source_command)


if __name__=='__main__':unittest.main()
