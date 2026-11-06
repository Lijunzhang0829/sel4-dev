"""Only an exact native watchdog abort can carry a legacy clock fault, without refund."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import digest, durable
from regen.infrastructure.effective_time_budget import EffectiveTimeBudget, POLICY
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.parallel_generation_budget import (
    carry_parallel_budget, review_parallel_budget_clock,
)
from regen.tools.parallel_repair_continuation import (
    native_watchdog_repair_experiment, native_watchdog_repair_review, prepare_native_watchdog,
)


class NativeWatchdogContinuationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.source = Path(tmp.name)/'source'; self.target = Path(tmp.name)/'target'
        self.run = {'run_id':'source'}
        self.policy = {'model':'gpt-6-astra', 'candidate_timeout':300}
        self.write(self.source/'run.json', self.run)
        self.write(self.source/'parallel-workspace-policy.json', self.policy)
        self.write(self.target/'run.json', {'run_id':'target'})
        self.write(self.target/'workspace-continuation.json', {'source_run':str(self.source)})
        ledger = Ledger(self.source/'effective-generation-budget', 'source')
        ledger.append('parallel_budget_binding', policy=POLICY, run_sha256=digest(self.run),
            contracts_sha256=digest({'A':['a']}), configuration_sha256=digest(self.policy))
        now = [0.]
        stage = EffectiveTimeBudget(ledger, seconds=100, monotonic=lambda:now[0])
        stage.register({'A':['a']}); stage.select('A', worker_id='A')
        now[0] = 20.
        stage.observe('a', {'status':'green'}, kind='validation', theory='A')
        self.event = ledger.append('generation_clock_untrusted', theory='A', evidence={
            'reason':'session_stopped_with_open_model_item', 'generation_id':'generation',
            'thread_id':'thread', 'turn_id':'turn', 'item_ids':['comment']})
        stage.cancel('effective_clock_evidence_error'); stage.close()
        self.source_rows = ledger.rows
        self.transcript = self.source/'parallel-sessions/session/A/session-transcript.jsonl'
        self.check_path = self.source/'theories/A/batch-requests/000001/check.json'
        self.task_path = self.source/'check-tasks/task/task.json'
        self.native = self.source/'check-tasks/native/isabelle/worker'
        self.native.mkdir(parents=True)
        (self.native/'7.request').write_text('candidate text')
        (self.native/'7.log').write_text('native processing\nTIMEOUT\n')
        self.diagnostic = 'TIMEOUT\nincremental batch worker timeout waiting for 7.response'
        proposal = {'action':'check_theory', 'source_sha256':'full-saved-draft', 'entry_ids':['a']}
        self.raw_request = {'id':8, 'method':'item/tool/call', 'params':{
            'threadId':'thread', 'turnId':'turn', 'callId':'call', 'tool':'proof_action',
            'arguments':{'theory':'A', 'proposal':proposal}}}
        self.task = {'theory':'A', 'owner':'A', 'status':'error', 'kind':'proof_batch',
            'binding':{'run_id':'source'}, 'error_type':'NativePreparationError',
            'diagnostic':self.diagnostic, 'finished_at':self.event['time_unix']-.1,
            'request':{'proposal':proposal, 'metadata':{'thread_id':'thread', 'turn_id':'turn',
                'raw_request':self.raw_request, 'transcript_path':str(self.transcript)}}}
        self.write(self.task_path, self.task)
        source = 'theory A\nbegin\nend\n'
        self.saved = {'document_source':source, 'document_source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'check':{'verdict':'infrastructure_error', 'failure_scope':'native_preparation',
                'native_failure_stage':'candidate-response-watchdog', 'timeout_scope':'candidate-watchdog',
                'timed_out':True, 'framework_blocked':True, 'diagnostic':self.diagnostic,
                'log':str(self.native/'7.log'), 'worker':'worker',
                'delta_sha256':hashlib.sha256(b'candidate text').hexdigest()}}
        self.write(self.check_path, self.saved)
        self.write(self.check_path.parent/'model-proposal.json', proposal)
        self.write(self.check_path.parent/'document-source.json', {'source':source})
        self.write(self.source/'theories/A/execution-stop.json', {
            'theory':'A', 'status':'infrastructure_error', 'exception_type':'NativePreparationError',
            'reason':self.diagnostic, 'request_number':1})
        self.rows = [
            {'direction':'in', 'message':{'method':'item/started', 'params':{
                'threadId':'thread', 'turnId':'turn', 'item':{'type':'agentMessage', 'id':'comment'}}}},
            {'direction':'in', 'message':self.raw_request},
            {'direction':'local', 'message':{'event':'session_failed', 'nonsealable':True,
                'exception_type':'NativePreparationError', 'message':self.diagnostic}},
        ]
        self.save_transcript()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def save_transcript(self):
        self.transcript.parent.mkdir(parents=True, exist_ok=True)
        self.transcript.write_text(''.join(json.dumps({
            'time_unix':self.event['time_unix']+.1, **row})+'\n' for row in self.rows))

    def review(self):
        return review_parallel_budget_clock(self.source)

    def test_exact_watchdog_review_keeps_uncertainty_and_binds_all_witnesses(self):
        result = self.review()
        witness = result['inherited_reviews'][0]
        self.assertEqual(witness['policy'], 'verified-native-watchdog-interruption-no-refund-v1')
        self.assertEqual(witness['task_sha256'], digest(self.task))
        self.assertEqual(witness['check_receipt_sha256'], digest(self.saved))
        self.assertEqual(witness['item_ids'], ['comment'])
        self.assertTrue(witness['historical_uncertainty_retained'])
        self.assertFalse(witness['mathematical_verdict_created'])
        self.assertEqual(witness['refund_seconds'], 0)
        self.assertEqual(Ledger(self.source/'effective-generation-budget', 'source').rows, self.source_rows)

    def test_carry_preserves_all_charged_time_green_and_source_uncertainty(self):
        result = carry_parallel_budget(self.source, self.target, authorization='user-approved',
            clock_review=self.review()['clock_review'])
        self.assertEqual(result['carried_effective_seconds'], 20.)
        self.assertEqual(result['carried_worker_seconds'], 20.)
        self.assertEqual(result['total_limit_seconds'], 100.)
        self.assertEqual(result['refund_seconds'], 0)
        rows = Ledger(self.target/'effective-generation-budget', 'target').rows
        self.assertEqual(sum(row['kind']=='time_budget_green' for row in rows), 1)
        self.assertEqual(sum(row['kind']=='generation_clock_untrusted' for row in rows), 1)
        self.assertEqual(Ledger(self.source/'effective-generation-budget', 'source').rows, self.source_rows)
        self.assertTrue((self.source/'native-watchdog-budget-transfer.json').exists())
        self.assertEqual(review_parallel_budget_clock(self.target)['clock_review']['disposition'],
                         'retain_all_charged_time_no_step_refund')

    def test_same_source_allowance_cannot_fund_two_descendants(self):
        review = self.review()['clock_review']
        carry_parallel_budget(self.source, self.target, authorization='user-approved', clock_review=review)
        target = self.target.with_name('other')
        self.write(target/'run.json', {'run_id':'other'})
        self.write(target/'workspace-continuation.json', {'source_run':str(self.source)})
        with self.assertRaisesRegex(ValueError, 'already claimed'):
            carry_parallel_budget(self.source, target, authorization='user-approved', clock_review=review)

    def test_native_response_that_arrives_later_requires_new_review(self):
        (self.native/'7.response').write_text('RED')
        with self.assertRaisesRegex(ValueError, 'request/response identity'):
            self.review()

    def test_native_request_tampering_is_rejected(self):
        (self.native/'7.request').write_text('different candidate')
        with self.assertRaisesRegex(ValueError, 'request/response identity'):
            self.review()

    def test_returned_tool_result_is_not_an_unreturned_watchdog_abort(self):
        self.rows.insert(2, {'direction':'out', 'message':{'id':8, 'result':{'success':False}}})
        self.save_transcript()
        with self.assertRaisesRegex(ValueError, 'abort does not match'):
            self.review()

    def test_completed_model_item_cannot_be_claimed_open(self):
        self.rows.insert(1, {'direction':'in', 'message':{'method':'item/completed', 'params':{
            'threadId':'thread', 'turnId':'turn', 'item':{'type':'agentMessage','id':'comment'}}}})
        self.save_transcript()
        with self.assertRaisesRegex(ValueError, 'abort does not match'):
            self.review()

    def test_terminal_turn_cannot_be_reclassified_as_a_watchdog_interruption(self):
        self.rows.insert(2, {'direction':'in', 'message':{'method':'turn/completed', 'params':{
            'threadId':'thread', 'turn':{'id':'turn', 'status':'completed'}}}})
        self.save_transcript()
        with self.assertRaisesRegex(ValueError, 'terminal turn'):
            self.review()

    def test_unrelated_session_error_is_rejected(self):
        self.rows[-1]['message']['exception_type'] = 'ValueError'
        self.save_transcript()
        with self.assertRaisesRegex(ValueError, 'abort does not match'):
            self.review()

    def test_projection_or_model_proposal_tampering_is_rejected(self):
        self.write(self.check_path.parent/'model-proposal.json', {'action':'check_theory','source_sha256':'other'})
        with self.assertRaisesRegex(ValueError, 'saved document check'):
            self.review()

    def test_red_or_generic_timeout_cannot_enter_native_watchdog_review(self):
        self.saved['check']['native_failure_stage'] = 'prefix-startup-or-alignment'
        self.write(self.check_path, self.saved)
        with self.assertRaisesRegex(ValueError, 'unreturned infrastructure failure'):
            self.review()

    def test_raw_and_parsed_transcript_mismatch_is_rejected(self):
        original = self.rows[0]['message']
        self.rows[0]['message'] = {'parsed':original, 'raw_json':'{}'}
        self.save_transcript()
        with self.assertRaisesRegex(ValueError, 'raw transcript differs'):
            self.review()


class NativeWatchdogRepairExperimentTests(unittest.TestCase):
    def test_relocates_only_identity_and_policy_without_extending_any_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'source'; source.mkdir(); target = Path(tmp)/'target'
            command = ['/usr/bin/python3','-m','regen.execution.proof_session_runner','parallel-workspace',
                '--run-dir',str(source),'--planner-model','gpt-6-astra','--effective-seconds','43200',
                '--candidate-timeout','300','--native-slots','1','--planning-budget','old-planning',
                '--execution-policy','old-policy','--allow-external-call']
            original = dict(command=command, cwd='/old/application', model='gpt-6-astra',
                reasoning_effort='low', effective_seconds=43200, include_planning=True)
            (source/'experiment.json').write_text(json.dumps(original))
            (source/'parallel-workspace-policy.json').write_text(json.dumps({'model':'gpt-6-astra','candidate_timeout':300}))
            new = native_watchdog_repair_experiment(source,target,Path('/new/application'),'authorized repair')
            args = new['command']
            self.assertNotIn('--planning-budget',args)
            self.assertEqual(args[args.index('--candidate-timeout')+1], '300')
            self.assertEqual(args[args.index('--effective-seconds')+1], '43200')
            self.assertEqual(args[args.index('--planner-model')+1], 'gpt-6-astra')
            self.assertEqual(args[args.index('--native-slots')+1], '1')
            self.assertEqual(args[args.index('--run-dir')+1], str(target))
            self.assertEqual(args[args.index('--resume-authorization')+1], 'authorized repair')
            self.assertFalse(new['historical_costs_reset'])
            self.assertFalse(new['planning_repeated'])
            self.assertEqual(json.loads((source/'experiment.json').read_text()), original)
            original['command'] += ['--extend-effective-seconds','100']
            (source/'experiment.json').write_text(json.dumps(original))
            with self.assertRaisesRegex(ValueError,'source command/configuration'):
                native_watchdog_repair_experiment(source,target,Path('/new/application'),'authorized repair')


class NativeWatchdogImplementationReviewTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name); self.source = self.root/'source'; self.source.mkdir()
        self.app = self.root/'application'; self.app.mkdir()
        for name in ('execution','planning','infrastructure','analysis'):
            (self.app/'regen'/name).mkdir(parents=True)
        self.code = self.app/'regen/execution/check.py'; self.code.write_text('repaired checker')
        self.config = self.root/'models.json'; self.config.write_text('{"model":"gpt-6-astra"}')
        config_hash = hashlib.sha256(self.config.read_bytes()).hexdigest()
        self.run = {'run_id':'source', 'model_config_path':str(self.config),
            'execution_models':{'File Planner':'gpt-6-astra'},
            'implementation':{'regen/execution/check.py':hashlib.sha256(b'old checker').hexdigest(),
                              str(self.config):config_hash}}
        (self.source/'run.json').write_text(json.dumps(self.run))
        (self.source/'experiment.json').write_text(json.dumps({
            'cwd':str(self.root/'old-application'), 'model':'gpt-6-astra','reasoning_effort':'low'}))
        self.manifest = self.root/'manifest.json'
        self.manifest.write_text(json.dumps({'files':{'regen/execution/check.py':{
            'sha256':hashlib.sha256(self.code.read_bytes()).hexdigest()}}}))
        self.settings = patch('regen.infrastructure.audited_llm.model_settings', return_value={
            'role_models':{'File Planner':'gpt-6-astra'},'reasoning_effort':'low'})
        self.config_path = patch('regen.infrastructure.audited_llm.model_config_path', return_value=self.config)
        self.settings.start(); self.config_path.start()
        self.addCleanup(self.settings.stop); self.addCleanup(self.config_path.stop)

    def review(self):
        return native_watchdog_repair_review(self.source,self.app,self.manifest,authorization='repair authorized')

    def test_exact_review_binds_source_and_replacement_manifest(self):
        result = self.review()
        self.assertEqual(result['source_run_sha256'], digest(self.run))
        self.assertEqual(result['source_implementation_sha256'], digest(self.run['implementation']))
        self.assertEqual(result['target_application_manifest_sha256'],hashlib.sha256(self.manifest.read_bytes()).hexdigest())
        self.assertEqual(result['implementation_changes'],[{
            'path':'regen/execution/check.py',
            'source_sha256':self.run['implementation']['regen/execution/check.py'],
            'target_sha256':hashlib.sha256(self.code.read_bytes()).hexdigest()}])
        self.assertEqual(result['refund_seconds'],0)
        self.assertFalse(result['limits_changed'])

    def test_unlisted_target_implementation_cannot_ride_on_review(self):
        (self.app/'regen/planning/unlisted.py').write_text('unreviewed code')
        with self.assertRaisesRegex(ValueError,'not covered'):
            self.review()

    def test_model_effort_change_is_not_a_harness_repair(self):
        with patch('regen.infrastructure.audited_llm.model_settings', return_value={
                'role_models':{'File Planner':'gpt-6-astra'},'reasoning_effort':'medium'}):
            with self.assertRaisesRegex(ValueError,'source model and reasoning effort'):
                self.review()

    def test_changed_model_configuration_content_is_rejected(self):
        replacement = self.root/'other-models.json'; replacement.write_text('{"model":"different"}')
        with patch('regen.infrastructure.audited_llm.model_config_path', return_value=replacement):
            with self.assertRaisesRegex(ValueError,'exact model configuration content'):
                self.review()

    def test_stale_implementation_review_is_rejected_before_fork_or_service_access(self):
        supplied = self.root/'review.json'; supplied.write_text('{"source":"other"}')
        with patch('regen.tools.parallel_repair_continuation.native_watchdog_repair_review', return_value={'source':'expected'}), \
             patch('regen.tools.parallel_workspace_service.status') as status, \
             patch('regen.execution.repository_execution.fork_workspace_continuation') as fork:
            with self.assertRaisesRegex(ValueError,'exact source and repaired release'):
                prepare_native_watchdog(self.source,self.root/'target',self.manifest,
                    authorization='repair authorized',implementation_review=supplied)
            status.assert_not_called(); fork.assert_not_called()
        self.assertFalse((self.root/'target').exists())


if __name__ == '__main__':
    unittest.main()
