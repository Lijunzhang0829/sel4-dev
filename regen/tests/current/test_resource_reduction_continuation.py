"""Resource-only continuation carries exact accepted history and all paid time."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import digest
from regen.infrastructure.effective_time_budget import EffectiveTimeBudget, POLICY
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.parallel_generation_budget import (
    carry_parallel_budget, initialize_parallel_budget, review_parallel_budget_clock,
)
from regen.tests.current import test_native_watchdog_continuation as native_tests
from regen.tools import parallel_repair_continuation as repair


RESOURCES = {'max_parallel_theories':1, 'resident_limit':1, 'native_slots':1}


class ResourceReductionBudgetTests(unittest.TestCase):
    def setUp(self):
        self.fixture = native_tests.NativeWatchdogContinuationTests(methodName='test_exact_watchdog_review_keeps_uncertainty_and_binds_all_witnesses')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        self.policy = {**f.policy, 'max_parallel_theories':2, 'resident_limit':2, 'native_slots':1}
        f.write(f.source/'parallel-workspace-policy.json',self.policy)
        # Recreate the small synthetic ledger with the complete original policy.
        rows = Ledger(f.source/'effective-generation-budget','source').rows
        shutil.rmtree(f.source/'effective-generation-budget')
        original = Ledger(f.source/'effective-generation-budget','source')
        for row in rows:
            data = dict(row['data'])
            if row['kind'] == 'parallel_budget_binding':
                data['configuration_sha256'] = digest(self.policy)
            original.clock = lambda row=row:row['time_unix']
            original.append(row['kind'], **data)
        carry_parallel_budget(f.source, f.target, authorization='previous watchdog repair',
            clock_review=review_parallel_budget_clock(f.source)['clock_review'])
        f.write(f.target/'parallel-workspace-policy.json',self.policy)
        now = [1000.]
        stage = EffectiveTimeBudget(Ledger(f.target/'effective-generation-budget','target'),
            seconds=100, resume_authorization='previous watchdog repair', monotonic=lambda:now[0])
        stage.register({'A':['a']}, baseline_green=['a']); stage.select('A',worker_id='A')
        now[0] += 5.
        stage.cancel('memory_pressure'); stage.close()
        self.source = f.target
        self.source_rows = Ledger(self.source/'effective-generation-budget','target').rows
        self.destination = self.source.with_name('reduced')
        f.write(self.destination/'run.json',{'run_id':'reduced'})
        f.write(self.destination/'workspace-continuation.json',{'source_run':str(self.source)})
        self.clock_review = review_parallel_budget_clock(self.source)['clock_review']

    def carry(self, **kw):
        return carry_parallel_budget(self.source,self.destination,authorization='user lower resources',
            clock_review=self.clock_review,target_resources=RESOURCES,**kw)

    def test_preserves_cost_green_uncertainty_and_validates_resource_lineage(self):
        receipt = self.carry()
        self.assertEqual((receipt['carried_effective_seconds'],receipt['carried_worker_seconds']), (25.,25.))
        self.assertEqual(receipt['total_limit_seconds'],100.)
        self.assertEqual(receipt['resource_reduction_update']['target_resources'],RESOURCES)
        self.assertEqual(receipt['resource_reduction_update']['refund_seconds'],0)
        reduced_policy = {**self.policy,**RESOURCES}
        rows = Ledger(self.destination/'effective-generation-budget','reduced').rows
        self.assertEqual(rows[0]['data']['configuration_sha256'],digest(reduced_policy))
        self.assertEqual(sum(row['kind']=='time_budget_green' for row in rows),1)
        self.assertEqual(sum(row['kind']=='generation_clock_untrusted' for row in rows),1)
        self.assertEqual(Ledger(self.source/'effective-generation-budget','target').rows,self.source_rows)
        self.assertTrue((self.source/'resource-reduction-budget-transfer.json').exists())
        self.assertFalse((self.source/'native-watchdog-budget-transfer.json').exists())
        reviewed = review_parallel_budget_clock(self.destination)
        self.assertEqual(reviewed['clock_review']['disposition'],'retain_all_charged_time_no_step_refund')

    def test_only_reduced_policy_can_resume_without_free_time_or_duplicate_green(self):
        from types import SimpleNamespace
        self.carry()
        policy = {**self.policy,**RESOURCES}
        overview = SimpleNamespace(run={'run_id':'reduced'})
        rows = [{'theory':'A','contracts':['a']}]
        with self.assertRaisesRegex(ValueError,'configuration binding changed'):
            initialize_parallel_budget(self.destination,overview,self.policy,seconds=100,
                rows=rows,resume_authorization='user lower resources')
        stage, _ = initialize_parallel_budget(self.destination,overview,policy,seconds=100,
            rows=rows,resume_authorization='user lower resources')
        self.addCleanup(stage.close)
        self.assertEqual(stage.snapshot()['effective_seconds'],25.)
        self.assertEqual(stage.snapshot()['green_contract_ids'],['a'])
        self.assertEqual(stage.snapshot()['seconds'],100.)

    def test_claimed_old_source_cannot_resume_in_place(self):
        from types import SimpleNamespace
        self.carry()
        before = Ledger(self.source/'effective-generation-budget','target').rows
        with self.assertRaisesRegex(ValueError,'do not resume the old source'):
            initialize_parallel_budget(self.source,SimpleNamespace(run={'run_id':'target'}),
                self.policy,seconds=100,rows=[{'theory':'A','contracts':['a']}],
                resume_authorization='accidental old-source restart')
        self.assertEqual(Ledger(self.source/'effective-generation-budget','target').rows,before)

    def test_resource_reduction_cannot_fund_two_descendants(self):
        self.carry()
        other = self.destination.with_name('duplicate')
        self.fixture.write(other/'run.json',{'run_id':'duplicate'})
        self.fixture.write(other/'workspace-continuation.json',{'source_run':str(self.source)})
        with self.assertRaisesRegex(ValueError,'already claimed'):
            carry_parallel_budget(self.source,other,authorization='user lower resources',
                clock_review=self.clock_review,target_resources=RESOURCES)

    def test_increase_unchanged_or_malformed_resource_limits_are_rejected(self):
        invalid = [dict(RESOURCES,native_slots=2),dict(RESOURCES,resident_limit=3),
            dict(RESOURCES,max_parallel_theories=True),dict(RESOURCES,resident_limit=0),
            dict(RESOURCES,native_slots='1'),dict(RESOURCES,native_slots=1.),
            {'max_parallel_theories':1},
            {k:self.policy[k] for k in RESOURCES}, dict(RESOURCES,model='different')]
        for resources in invalid:
            with self.subTest(resources=resources),self.assertRaises(ValueError):
                carry_parallel_budget(self.source,self.destination,authorization='user lower resources',
                    clock_review=self.clock_review,target_resources=resources)
        self.assertFalse((self.source/'resource-reduction-budget-transfer.json').exists())

    def test_other_policy_migrations_cannot_ride_on_resource_reduction(self):
        for change in ({'target_native_slots':2},{'target_candidate_timeout':900},
                       {'target_strategy':{}},{'target_execution_policy':{}}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                self.carry(**change)
        self.assertFalse((self.source/'resource-reduction-budget-transfer.json').exists())

    def test_copied_resource_change_is_audited_against_claim_and_original_policy(self):
        self.carry()
        claim = self.source/'resource-reduction-budget-transfer.json'
        value = json.loads(claim.read_text()); value['target_configuration_sha256']='tampered'
        claim.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'transfer claim changed'):
            review_parallel_budget_clock(self.destination)


class ResourceReductionToolTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.source = Path(tmp.name)/'source'; self.source.mkdir()
        self.target = Path(tmp.name)/'new/proof'
        self.policy = {'model':'gpt-6-astra','candidate_timeout':300,
            'max_parallel_theories':2,'resident_limit':2,'native_slots':1}
        command = ['/usr/bin/python3','-m','regen.execution.proof_session_runner','parallel-workspace',
            '--run-dir',str(self.source),'--planner-model','gpt-6-astra','--effective-seconds','43200',
            '--candidate-timeout','300','--max-parallel-theories','2','--resident-limit','2',
            '--native-slots','1','--execution-policy','old-policy','--resume-authorization','old-user']
        self.original = dict(command=command,cwd='/old/application',model='gpt-6-astra',
            reasoning_effort='low',effective_seconds=43200,include_planning=True)
        (self.source/'experiment.json').write_text(json.dumps(self.original))
        (self.source/'parallel-workspace-policy.json').write_text(json.dumps(self.policy))

    def test_command_changes_exact_resource_counts_but_retains_model_budget_and_timeout(self):
        new = repair.resource_reduction_experiment(self.source,self.target,Path('/new/application'),
            'user requested one worker',RESOURCES)
        args = new['command']
        for flag in ('--max-parallel-theories','--resident-limit','--native-slots'):
            self.assertEqual(args[args.index(flag)+1],'1')
        for flag in ('--planner-model','--effective-seconds','--candidate-timeout'):
            self.assertEqual(args[args.index(flag)+1],self.original['command'][self.original['command'].index(flag)+1])
        self.assertNotIn('native_watchdog_repair_policy',new)
        self.assertEqual(new['resource_reduction_policy'],'resource-reduction-continuation-v1')
        self.assertEqual(json.loads((self.source/'experiment.json').read_text()),self.original)

    def test_cli_requires_all_explicit_resources_and_passes_unique_run_id(self):
        args = ['repair','--source',str(self.source),'--output',str(self.target),'--manifest','/manifest',
            '--authorization','lower resources','--resource-reduction','--implementation-review','/review',
            '--max-parallel-theories','1','--resident-limit','1','--native-slots','1','--run-id','run-003']
        with patch('sys.argv',args), patch.object(repair,'prepare_resource_reduction',return_value={}) as prepare, \
             contextlib.redirect_stdout(io.StringIO()):
            repair.main()
        self.assertEqual(prepare.call_args.kwargs['target_resources'],RESOURCES)
        self.assertEqual(prepare.call_args.kwargs['run_id'],'run-003')
        with patch('sys.argv',args[:-4]), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):repair.main()

    def test_prepare_writes_exact_reduced_policy_and_preserves_source_execution_policy(self):
        review_path = self.source.parent/'review.json'
        run = {'implementation':{'module':'hash'}}
        review = {'target_implementation_sha256':digest(run['implementation']),
            'policy':'resource-reduction-continuation-v1','target_application_manifest_sha256':'manifest'}
        policy = {**self.policy,'execution_policy':{'retry_unresolved':False}}
        def read(path):
            if path == review_path:return review
            if path == self.source/'parallel-workspace-policy.json':return policy
            raise AssertionError(path)
        with patch.object(repair,'read_artifact',side_effect=read), \
             patch.object(repair,'resource_reduction_repair_review',return_value=review), \
             patch.object(repair,'resource_reduction_experiment',return_value={}), \
             patch('regen.tools.parallel_workspace_service.status',return_value={
                 'running':False,'identity_verified':True,'status':'paused'}), \
             patch('regen.execution.proof_session_runner.acquire_run_mutation_lock',return_value='lock'), \
             patch('regen.execution.proof_session_runner.release_run_mutation_lock') as release, \
             patch('regen.infrastructure.parallel_generation_budget.review_parallel_budget_clock',return_value={
                 'clock_review':{'source_tail_event_id':'tail'}}), \
             patch('regen.execution.repository_execution.fork_workspace_continuation',return_value=run) as fork, \
             patch('regen.infrastructure.parallel_generation_budget.carry_parallel_budget',return_value={'refund_seconds':0}) as carry:
            result = repair.prepare_resource_reduction(self.source,self.target,self.source.parent/'manifest',
                authorization='user lower resources',implementation_review=review_path,
                target_resources=RESOURCES,run_id='run-003')
        fork.assert_called_once_with(self.source,self.target,run_id='run-003')
        self.assertEqual(carry.call_args.kwargs['target_resources'],RESOURCES)
        self.assertEqual(json.loads((self.target/'parallel-workspace-policy.json').read_text()),{**policy,**RESOURCES})
        self.assertEqual(json.loads((self.target/'harness-repair-execution-policy.json').read_text()),policy['execution_policy'])
        self.assertEqual(result['state'],'prepared')
        self.assertEqual(result['application_manifest_sha256'],'manifest')
        release.assert_called_once_with('lock')


class ResourceReductionCleanClaimTests(unittest.TestCase):
    def test_plain_followup_cannot_bypass_claim_without_clock_uncertainty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root/'source'; destination = root/'reduced'; other = root/'other'
            policy = {'max_parallel_theories':2,'resident_limit':2,'native_slots':1}
            def write(path, value):
                path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(value))
            run = {'run_id':'source'}
            write(source/'run.json',run); write(source/'parallel-workspace-policy.json',policy)
            for target in (destination,other):
                write(target/'run.json',{'run_id':target.name})
                write(target/'workspace-continuation.json',{'source_run':str(source)})
            ledger = Ledger(source/'effective-generation-budget','source')
            ledger.append('parallel_budget_binding',policy=POLICY,run_sha256=digest(run),
                contracts_sha256=digest({'A':['a']}),configuration_sha256=digest(policy))
            stage = EffectiveTimeBudget(ledger,seconds=100,monotonic=lambda:0.)
            stage.register({'A':['a']}); stage.cancel('memory_pressure'); stage.close()
            review = review_parallel_budget_clock(source)
            self.assertEqual(review['inherited_reviews'],[])
            carry_parallel_budget(source,destination,authorization='lower resources',
                clock_review=review['clock_review'],target_resources=RESOURCES)
            with self.assertRaisesRegex(ValueError,'already claimed'):
                carry_parallel_budget(source,other,authorization='ordinary continuation',
                    clock_review=review['clock_review'])


class ResourceReductionImplementationReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = native_tests.NativeWatchdogImplementationReviewTests(
            methodName='test_exact_review_binds_source_and_replacement_manifest')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.source, self.app, self.manifest = self.fixture.source, self.fixture.app, self.fixture.manifest
        (self.source/'parallel-workspace-policy.json').write_text(json.dumps({
            'max_parallel_theories':2,'resident_limit':2,'native_slots':1}))

    def test_resource_only_review_rejects_proof_execution_code_replacement(self):
        with self.assertRaisesRegex(ValueError,'cannot replace proof execution implementation'):
            repair.resource_reduction_repair_review(self.source,self.app,self.manifest,
                authorization='lower resources',target_resources=RESOURCES)

    def test_resource_only_review_allows_budget_binding_update_with_exact_manifest(self):
        import hashlib
        self.fixture.code.write_text('old checker')
        budget = self.app/'regen/infrastructure/parallel_generation_budget.py'
        budget.write_text('new resource binding')
        files = {str(path.relative_to(self.app)):{'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
                 for path in (self.fixture.code,budget)}
        self.manifest.write_text(json.dumps({'files':files}))
        result = repair.resource_reduction_repair_review(self.source,self.app,self.manifest,
            authorization='lower resources',target_resources=RESOURCES)
        self.assertEqual(result['policy'],'resource-reduction-continuation-v1')
        self.assertEqual(result['target_resources'],RESOURCES)
        self.assertTrue(result['limits_changed'])
        self.assertEqual([row['path'] for row in result['implementation_changes']],
                         ['regen/infrastructure/parallel_generation_budget.py'])


if __name__ == '__main__':
    unittest.main()
