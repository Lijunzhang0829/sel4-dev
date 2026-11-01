"""No budget refund, no duplicate GREEN, no charge for stopped continuation gaps."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from regen.execution.proof_commits import durable, digest
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.effective_time_budget import EffectiveTimeBudget, POLICY
from regen.infrastructure.parallel_generation_budget import (
    carry_parallel_budget, initialize_parallel_budget, review_parallel_budget_clock,
)


class ParallelBudgetContinuationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.source=Path(self.tmp.name)/'source';self.target=Path(self.tmp.name)/'target'
        self.run={'run_id':'source'};self.new={'run_id':'target'}
        self.policy={'strategy':'helper-closure','native_slots':2,'candidate_timeout':120,
                     'max_parallel_theories':3,'resident_limit':3,
                     'generation_strategy':{'name':'helper-closure-v1','instructions':'old frozen policy'}}
        self.theories={'A':['a'],'B':['b']}
        durable(self.source/'run.json',self.run)
        durable(self.target/'run.json',self.new)
        durable(self.source/'parallel-workspace-policy.json',self.policy)
        durable(self.target/'workspace-continuation.json',{'source_run':str(self.source)})
        ledger=Ledger(self.source/'effective-generation-budget','source')
        ledger.append('parallel_budget_binding',policy=POLICY,run_sha256=digest(self.run),
            contracts_sha256=digest(self.theories),configuration_sha256=digest(self.policy))
        now=[0.]
        stage=EffectiveTimeBudget(ledger,seconds=100,monotonic=lambda:now[0])
        stage.register(self.theories)
        stage.select('A',worker_id='A');stage.select('B',worker_id='B')
        now[0]=20.
        stage.observe('a',{'status':'green'},kind='validation',theory='A')
        event=ledger.append('generation_clock_untrusted',evidence={
            'reason':'tool_callback_finished_with_open_model_item'})
        stage.cancel('effective_clock_evidence_error');stage.close()
        self.review={'source_tail_event_id':ledger.rows[-1]['event_id'],
            'resolved_event_ids':[event['event_id']],
            'disposition':'retain_all_charged_time_no_step_refund'}
        self.source_rows=ledger.rows

    def carry(self,**kw):
        return carry_parallel_budget(self.source,self.target,authorization='user-approved',
                                     clock_review={**self.review,**kw})

    def test_carry_restores_union_worker_green_and_no_offline_charge(self):
        result=self.carry()
        self.assertEqual(result['carried_effective_seconds'],20)
        self.assertEqual(result['carried_worker_seconds'],40)
        ledger=Ledger(self.target/'effective-generation-budget','target')
        now=[10000.]
        resumed=EffectiveTimeBudget(ledger,seconds=100,resume_authorization='user-approved',
                                    monotonic=lambda:now[0])
        self.addCleanup(resumed.close)
        resumed.register(self.theories,baseline_green=['a'])
        now[0]+=500
        self.assertEqual(resumed.snapshot()['effective_seconds'],20)
        self.assertEqual(resumed.snapshot()['green_contract_ids'],['a'])
        resumed.select('B',worker_id='B');now[0]+=5
        self.assertEqual(resumed.snapshot()['effective_seconds'],25)
        self.assertEqual(resumed.snapshot()['seconds'],100)
        self.assertEqual(sum(r['kind']=='time_budget_green' for r in ledger.rows),1)
        self.assertEqual(Ledger(self.source/'effective-generation-budget','source').rows,self.source_rows)
        with self.assertRaisesRegex(ValueError,'already exists'):self.carry()

    def test_extended_budget_resume_keeps_original_base_argument(self):
        source=Ledger(self.source/'effective-generation-budget','source')
        extended=EffectiveTimeBudget(source,seconds=100,resume_authorization='extend',
                                     extension_seconds=50,monotonic=lambda:100.)
        extended.cancel('operator_pause');extended.close()
        review={**self.review,'source_tail_event_id':source.rows[-1]['event_id']}
        carried=carry_parallel_budget(self.source,self.target,authorization='continue',clock_review=review)
        self.assertEqual(carried['total_limit_seconds'],150)
        target=Ledger(self.target/'effective-generation-budget','target')
        before=list(target.rows)
        with self.assertRaisesRegex(ValueError,'binding changed'):
            EffectiveTimeBudget(target,seconds=150,resume_authorization='continue')
        self.assertEqual(target.rows,before)
        resumed=EffectiveTimeBudget(target,seconds=100,resume_authorization='continue',
                                    monotonic=lambda:10000.)
        self.addCleanup(resumed.close)
        self.assertEqual(resumed.snapshot()['seconds'],150)
        self.assertEqual(resumed.snapshot()['effective_seconds'],20)
        self.assertEqual(resumed.snapshot()['base_seconds'],100)

    def test_review_cannot_silently_drop_uncertainty_or_refund_step(self):
        for kw in ({'resolved_event_ids':[]},{'source_tail_event_id':'wrong'},
                   {'disposition':'refund_last_step'}):
            with self.subTest(kw=kw),self.assertRaisesRegex(ValueError,'exact source history'):
                self.carry(**kw)
        self.assertFalse((self.target/'effective-generation-budget').exists())

    def test_checkpoint_rollback_is_not_full_continuation(self):
        durable(self.target/'checkpoint.json',{})
        with self.assertRaisesRegex(ValueError,'full latest'):self.carry()

    def test_partial_import_cannot_start_generation(self):
        ledger=Ledger(self.target/'effective-generation-budget','target')
        ledger.append('parallel_budget_continuation_started',source_run=str(self.source))
        with self.assertRaisesRegex(ValueError,'incomplete'):
            initialize_parallel_budget(self.target,SimpleNamespace(run=self.new),self.policy)

    def test_authorized_strategy_update_preserves_budget_and_records_binding(self):
        from regen.planning.parallel_file_planner import generation_strategy
        strategy=generation_strategy('helper-closure-v1')
        result=carry_parallel_budget(self.source,self.target,authorization='user-approved',
            clock_review=self.review,target_strategy=strategy)
        self.assertEqual(result['carried_effective_seconds'],20)
        rows=Ledger(self.target/'effective-generation-budget','target').rows
        binding=next(r['data'] for r in rows if r['kind']=='parallel_budget_binding')
        self.assertEqual(binding['configuration_sha256'],digest({**self.policy,'generation_strategy':strategy}))
        change=next(r['data'] for r in rows if r['kind']=='parallel_budget_strategy_update')
        self.assertEqual(change['seconds_unchanged'],100)
        self.assertEqual(change['effective_seconds_unchanged'],20)

    def test_strategy_update_cannot_smuggle_another_policy(self):
        with self.assertRaisesRegex(ValueError,'frozen implementation'):
            carry_parallel_budget(self.source,self.target,authorization='user-approved',
                clock_review=self.review,target_strategy={'name':'baseline'})

    def test_native_slots_migration_preserves_budget_and_binds_only_requested_slots(self):
        result=carry_parallel_budget(self.source,self.target,authorization='user-approved',
            clock_review=self.review,target_native_slots=3)
        rows=Ledger(self.target/'effective-generation-budget','target').rows
        binding=next(r['data'] for r in rows if r['kind']=='parallel_budget_binding')
        self.assertEqual(binding['configuration_sha256'],digest({**self.policy,'native_slots':3}))
        self.assertEqual(result['carried_effective_seconds'],20)
        self.assertEqual(result['carried_worker_seconds'],40)
        change=result['native_slots_update']
        self.assertEqual(change['seconds_unchanged'],100)
        self.assertEqual(change['refund_seconds'],0)
        self.assertEqual(change['previous_native_slots'],2)
        self.assertEqual(Ledger(self.source/'effective-generation-budget','source').rows,self.source_rows)

    def test_native_slots_migration_rejects_invalid_or_unprovisioned_slots(self):
        for value in [True,0,-1,2,4,3.0,'3']:
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'frozen worker'):
                carry_parallel_budget(self.source,self.target,authorization='user-approved',
                    clock_review=self.review,target_native_slots=value)
        self.assertFalse((self.target/'effective-generation-budget').exists())

    def test_candidate_timeout_increase_binds_launch_configuration_without_reset(self):
        result=carry_parallel_budget(self.source,self.target,authorization='user-approved',
            clock_review=self.review,target_candidate_timeout=300)
        target_policy={**self.policy,'candidate_timeout':300}
        rows=Ledger(self.target/'effective-generation-budget','target').rows
        binding=next(r['data'] for r in rows if r['kind']=='parallel_budget_binding')
        self.assertEqual(binding['configuration_sha256'],digest(target_policy))
        change=next(r['data'] for r in rows if r['kind']=='parallel_budget_candidate_timeout_update')
        self.assertEqual(change,result['candidate_timeout_update'])
        self.assertEqual(change['source_tail_event_id'],self.review['source_tail_event_id'])
        self.assertEqual(change['previous_configuration_sha256'],digest(self.policy))
        self.assertEqual(change['target_configuration_sha256'],digest(target_policy))
        self.assertEqual((change['previous_candidate_timeout'],change['target_candidate_timeout']),(120,300))
        self.assertEqual((change['seconds_unchanged'],change['remaining_effective_seconds']),(100,80))
        overview=SimpleNamespace(run=self.new)
        selected=[{'theory':name,'contracts':contracts} for name,contracts in self.theories.items()]
        for changed in ({**target_policy,'candidate_timeout':120},
                        {**target_policy,'model':'different-model'},
                        {**target_policy,'native_slots':1},
                        {**target_policy,'generation_strategy':{'name':'different'}}):
            with self.subTest(changed=changed),self.assertRaisesRegex(ValueError,'configuration binding changed'):
                initialize_parallel_budget(self.target,overview,changed,seconds=100,
                    rows=selected,resume_authorization='user-approved')
        with self.assertRaisesRegex(ValueError,'budget binding changed'):
            initialize_parallel_budget(self.target,overview,target_policy,seconds=200,
                rows=selected,resume_authorization='user-approved')
        resumed,_=initialize_parallel_budget(self.target,overview,target_policy,seconds=100,
            rows=selected,resume_authorization='user-approved')
        self.addCleanup(resumed.close)
        self.assertEqual(resumed.snapshot()['effective_seconds'],20)
        self.assertEqual(resumed.snapshot()['worker_seconds'],40)
        self.assertEqual(resumed.snapshot()['green_contract_ids'],['a'])
        self.assertEqual(Ledger(self.source/'effective-generation-budget','source').rows,self.source_rows)

    def test_candidate_timeout_increase_requires_explicit_authorization_and_typed_increase(self):
        for authorization in (None,'','  '):
            with self.subTest(authorization=authorization),self.assertRaisesRegex(ValueError,'authorization'):
                carry_parallel_budget(self.source,self.target,authorization=authorization,
                    clock_review=self.review,target_candidate_timeout=300)
        for value in (True,False,0,-1,300.,'300'):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'positive integer'):
                carry_parallel_budget(self.source,self.target,authorization='user-approved',
                    clock_review=self.review,target_candidate_timeout=value)
        for value in (120,119):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'must increase'):
                carry_parallel_budget(self.source,self.target,authorization='user-approved',
                    clock_review=self.review,target_candidate_timeout=value)
        self.assertFalse((self.target/'effective-generation-budget').exists())

    def test_candidate_timeout_cannot_restart_an_exhausted_budget(self):
        ledger=Ledger(self.source/'effective-generation-budget','source')
        checkpoint=next(r for r in reversed(ledger.rows) if r['kind']=='time_budget_checkpoint')
        ledger.append('time_budget_checkpoint',boundary='exhausted',state={
            **checkpoint['data']['state'],'effective_seconds':100,'reason':'effective_budget_exhausted'})
        review={**self.review,'source_tail_event_id':ledger.rows[-1]['event_id']}
        with self.assertRaisesRegex(ValueError,'exhausted experiment budget'):
            carry_parallel_budget(self.source,self.target,authorization='user-approved',
                clock_review=review,target_candidate_timeout=300)
        self.assertFalse((self.target/'effective-generation-budget').exists())

    def test_candidate_timeout_preserves_planning_provider_credits_and_raw_history(self):
        source=self.source.parent/'accounted-source';target=self.target.parent/'accounted-target'
        run={'run_id':'accounted-source'};new={'run_id':'accounted-target'}
        durable(source/'run.json',run);durable(target/'run.json',new)
        durable(source/'parallel-workspace-policy.json',self.policy)
        durable(target/'workspace-continuation.json',{'source_run':str(source)})
        ledger=Ledger(source/'effective-generation-budget',run['run_id'])
        ledger.append('parallel_budget_binding',policy=POLICY,run_sha256=digest(run),
            contracts_sha256=digest(self.theories),configuration_sha256=digest(self.policy))
        now=[0.]
        stage=EffectiveTimeBudget(ledger,seconds=100,include_planning=True,monotonic=lambda:now[0])
        stage.begin_planning();now[0]=5.;stage.end_planning()
        stage.register(self.theories);stage.select('A',worker_id='A');stage.select('B',worker_id='B')
        stage.begin_provider_request('A','failed')
        now[0]=15.;stage.finish_provider_request('A','failed',outcome='failed',failure={'kind':'no_complete_response'})
        stage.begin_provider_request('A','retry')
        now[0]=17.;stage.finish_provider_request('A','retry')
        credit=stage.credit_provider_request('failed','retry')
        now[0]=20.;stage.observe('a',{'status':'green'},theory='A')
        stage.cancel('operator_pause');stage.close()
        original_rows=list(ledger.rows)
        original=next(r['data']['state'] for r in reversed(ledger.rows) if r['kind']=='time_budget_checkpoint')
        review={'source_tail_event_id':ledger.rows[-1]['event_id'],'resolved_event_ids':[],
                'disposition':'retain_all_charged_time_no_step_refund'}
        carried=carry_parallel_budget(source,target,authorization='user-approved',clock_review=review,
                                      target_candidate_timeout=300)
        migrated=Ledger(target/'effective-generation-budget',new['run_id'])
        copied=next(r['data']['state'] for r in reversed(migrated.rows) if r['kind']=='time_budget_checkpoint')
        self.assertEqual(copied,original)
        self.assertEqual(carried['candidate_timeout_update']['planning_seconds_unchanged'],5)
        self.assertEqual(carried['candidate_timeout_update']['provider_request_accounting_sha256'],
                         digest(original['provider_request_accounting']))
        resumed=EffectiveTimeBudget(migrated,seconds=100,include_planning=True,
            resume_authorization='user-approved',monotonic=lambda:10000.)
        self.addCleanup(resumed.close)
        resumed.register(self.theories,baseline_green=['a'])
        snapshot=resumed.snapshot()
        for key in ('effective_seconds','worker_seconds','planning_seconds','raw_effective_seconds',
                    'raw_worker_seconds','excluded_failed_request_seconds','excluded_failed_request_worker_seconds'):
            self.assertEqual(snapshot[key],original[key],key)
        self.assertEqual(resumed.provider_requests.credits['failed'],credit)
        self.assertEqual(snapshot['green_contract_ids'],['a'])
        self.assertEqual(sum(r['kind']=='time_budget_green' for r in migrated.rows),1)
        self.assertEqual(Ledger(source/'effective-generation-budget',run['run_id']).rows,original_rows)


class InheritedClockReviewTests(unittest.TestCase):
    def setUp(self):
        # Exercise the real legacy review/import protocol, including transcripts,
        # immutable source chains and pending request accounting.
        from regen.tests.current.test_provider_feedback_continuation import LegacyFeedbackContinuationTests
        fixture=LegacyFeedbackContinuationTests()
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.carry()
        self.fixture=fixture;self.source=fixture.target;self.target=fixture.root/'next'
        self.policy={**fixture.policy,'execution_policy':{
            **fixture.policy['execution_policy'],'provider_feedback_retries':1}}
        durable(self.source/'parallel-workspace-policy.json',self.policy)
        self.prepare(self.target,self.source)

    @staticmethod
    def prepare(target,source):
        durable(target/'run.json',{'run_id':target.name})
        durable(target/'workspace-continuation.json',{'source_run':str(source)})

    def test_audited_legacy_uncertainty_survives_two_continuations_without_reimport(self):
        from regen.infrastructure.incremental_log import read_artifact
        from regen.infrastructure.provider_feedback_continuation import IMPORT_BOUNDARY
        source=Ledger(self.source/'effective-generation-budget','target')
        original=next(row['data']['state'] for row in reversed(source.rows)
                      if row['kind']=='time_budget_checkpoint')
        reviewed=review_parallel_budget_clock(self.source)
        self.assertEqual(len(reviewed['inherited_reviews']),1)
        evidence=reviewed['inherited_reviews'][0]
        legacy=read_artifact(self.source/'provider-feedback-continuation.json')
        self.assertEqual(evidence['legacy_provider_feedback_review_id'],legacy['review_id'])
        original_event=next(row for row in self.fixture.source_rows if row['kind']=='generation_clock_untrusted')
        self.assertEqual(evidence['event_mappings'][0]['source_event_id'],original_event['event_id'])
        self.assertNotEqual(evidence['event_mappings'][0]['event_id'],original_event['event_id'])
        for source_dir,target_dir in [(self.source,self.target),(self.target,self.target.parent/'third')]:
            if not target_dir.exists():self.prepare(target_dir,source_dir)
            reviewed=review_parallel_budget_clock(source_dir)
            result=carry_parallel_budget(source_dir,target_dir,authorization='user-approved-repair',
                                         clock_review=reviewed['clock_review'])
            self.assertEqual(result['inherited_clock_reviews'],reviewed['inherited_reviews'])
            durable(target_dir/'parallel-workspace-policy.json',self.policy)
            ledger=Ledger(target_dir/'effective-generation-budget',target_dir.name)
            state=next(row['data']['state'] for row in reversed(ledger.rows)
                       if row['kind']=='time_budget_checkpoint')
            self.assertEqual(state,original)
            self.assertEqual(sum(row['kind']=='time_budget_checkpoint'
                and row['data'].get('boundary')==IMPORT_BOUNDARY for row in ledger.rows),1)
            self.assertFalse((target_dir/'provider-feedback-continuation.json').exists())
            self.assertTrue(any(row['kind']=='parallel_budget_clock_review_inherited' for row in ledger.rows))
        self.assertEqual(Ledger(self.source/'effective-generation-budget','target').rows,source.rows)

    def test_new_uncertainty_cannot_borrow_an_old_review(self):
        ledger=Ledger(self.source/'effective-generation-budget','target')
        prior=next(row for row in ledger.rows if row['kind']=='generation_clock_untrusted')
        ledger.append('generation_clock_untrusted',**prior['data'])
        with self.assertRaisesRegex(ValueError,'new unsupported uncertainty'):
            review_parallel_budget_clock(self.source)
        self.assertFalse((self.target/'effective-generation-budget').exists())

    def test_changed_ancestor_history_invalidates_inherited_review(self):
        ledger=Ledger(self.fixture.source/'effective-generation-budget','source')
        ledger.append('operator_note',note='source has changed since migration')
        with self.assertRaisesRegex(ValueError,'exact source history'):
            review_parallel_budget_clock(self.source)

    def test_changed_copied_record_is_rejected_even_with_a_valid_envelope_chain(self):
        import shutil
        ledger=Ledger(self.source/'effective-generation-budget','target')
        rows=ledger.rows
        shutil.rmtree(ledger.root)
        forged=Ledger(ledger.root,'target')
        for row in rows:
            forged.clock=lambda row=row:row['time_unix']
            data=row['data']
            if row['kind']=='generation_clock_untrusted':
                data={**data,'evidence':{**data['evidence'],'item_ids':['forged-item']}}
            forged.append(row['kind'],**data)
        with self.assertRaisesRegex(ValueError,'copied history changed'):
            review_parallel_budget_clock(self.source)

    def test_changed_legacy_receipt_or_transcript_cannot_authorize_inheritance(self):
        from regen.infrastructure.incremental_log import read_artifact
        receipt=self.source/'provider-feedback-continuation.json'
        original=read_artifact(receipt)
        durable(receipt,{**original,'review_id':'forged-review'})
        with self.assertRaisesRegex(ValueError,'legacy feedback review receipt changed'):
            review_parallel_budget_clock(self.source)
        durable(receipt,original)
        self.fixture.rows['A'][0]['message']['raw_json']+=' '
        self.fixture.save_transcript('A')
        with self.assertRaisesRegex(ValueError,'legacy feedback review receipt changed'):
            review_parallel_budget_clock(self.source)


if __name__=='__main__':unittest.main()
