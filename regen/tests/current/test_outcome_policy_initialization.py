"""Fresh defaults, frozen legacy continuations, and budget-preserving upgrades."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import shutil
import unittest
from unittest.mock import patch

from regen.execution import repository_execution as execution
from regen.execution.checker_policy import (
    CONTRACT_OUTCOME_POLICY, NATIVE_ATTEMPTS_POLICY, contract_policy_binding,
    continuation_outcome_arguments, new_outcome_policy_binding,
    validate_outcome_upgrade_readiness,
)
from regen.execution.proof_commits import durable, digest
from regen.infrastructure.effective_time_budget import EffectiveTimeBudget, CHECKPOINT_KIND, POLICY
from regen.infrastructure.experiment_ledger import Ledger
from regen.planning.parallel_file_planner import generation_strategy
from regen.planning.theory_outcomes import record_unresolved, current_outcome, pending_contract_ids
from regen.tests.current import test_repository_planner as planner_fixtures
from regen.tests.current import test_continuation_providers as continuation_fixtures


class OutcomePolicyInitializationTest(unittest.TestCase):
    def initialize(self, **options):
        fixture = planner_fixtures.RepositoryPlannerTest()
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        fixture.call([fixture.proposal],allow=True)
        return execution.init_run(fixture.root/'proof',fixture.root,'policy-test',
                                  fixture.output/'file-generation-list.json',**options)

    def test_fresh_repository_defaults_are_frozen_in_run_and_commit_binding(self):
        run = self.initialize()
        self.assertEqual(run['contract_outcome_policy'],CONTRACT_OUTCOME_POLICY)
        self.assertEqual(run['unresolved_evidence_policy'],NATIVE_ATTEMPTS_POLICY)
        self.assertEqual(contract_policy_binding(run),new_outcome_policy_binding())
        self.assertIn('regen/planning/unresolved_evidence.py',run['implementation'])

    def test_explicit_legacy_none_preserves_both_missing_fields(self):
        run = self.initialize(contract_outcome_policy=None)
        self.assertNotIn('contract_outcome_policy',run)
        self.assertNotIn('unresolved_evidence_policy',run)
        self.assertEqual(contract_policy_binding(run),{})

    def test_old_contract_policy_does_not_silently_acquire_strict_evidence(self):
        old = {'contract_outcome_policy':CONTRACT_OUTCOME_POLICY}
        self.assertEqual(new_outcome_policy_binding(**continuation_outcome_arguments(old)),old)
        self.assertEqual(new_outcome_policy_binding(**continuation_outcome_arguments({})),{})

    def test_strict_evidence_cannot_be_enabled_for_legacy_theory_policy(self):
        with self.assertRaisesRegex(ValueError,'requires contract-dispositions'):
            new_outcome_policy_binding(None,NATIVE_ATTEMPTS_POLICY)
        with self.assertRaisesRegex(ValueError,'unsupported unresolved'):
            new_outcome_policy_binding(CONTRACT_OUTCOME_POLICY,'invented')

    def test_ordinary_continuation_explicitly_pins_legacy_policy(self):
        fixture = continuation_fixtures.ContinuationProvidersTest()
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        with patch.object(execution,'init_run',side_effect=fixture.initialize) as initialize, \
             patch.object(execution,'progress',return_value={}):
            run = execution.fork_workspace_continuation(fixture.source,fixture.root/'continued')
        self.assertIsNone(initialize.call_args.kwargs['contract_outcome_policy'])
        self.assertIsNone(initialize.call_args.kwargs['unresolved_evidence_policy'])
        self.assertNotIn('contract_outcome_policy',run)


class OutcomePolicyUpgradeTest(unittest.TestCase):
    def setUp(self):
        self.fixture = continuation_fixtures.ContinuationProvidersTest()
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.source,self.root = self.fixture.source,self.fixture.root
        self.destination = self.root/'strict-continuation'
        self.policy = {'file_planner_interface':'document',
            'execution_policy':{'retry_unresolved':False},
            'generation_strategy':generation_strategy('sketch-reviewed-v1')}
        durable(self.source/'parallel-workspace-policy.json',self.policy)
        ledger = Ledger(self.source/'effective-generation-budget',self.fixture.run['run_id'])
        theories = {name:[name] for name in self.fixture.packages}
        ledger.append('parallel_budget_binding',policy=POLICY,
            run_sha256=digest(self.fixture.run),configuration_sha256=digest(self.policy),
            contracts_sha256=digest(theories))
        now = [0.]
        stage = EffectiveTimeBudget(ledger,seconds=100,monotonic=lambda:now[0])
        stage.register(theories);stage.select('Target',worker_id='Target')
        now[0] = 17.
        stage.cancel('operator_pause');stage.close()
        self.source_rows = deepcopy(ledger.rows)
        self.source_run = (self.source/'run.json').read_bytes()

    def initialize(self,destination,_input,run_id,_schedule,**options):
        destination.mkdir()
        shutil.copytree(self.source/'packages',destination/'packages')
        shutil.copy2(self.source/'execution-input.json',destination/'execution-input.json')
        run = {**self.fixture.run,'run_id':run_id,'contract_package_dir':str(destination/'packages'),
               **new_outcome_policy_binding(options['contract_outcome_policy'],options['unresolved_evidence_policy'])}
        if options.get('outcome_policy_upgrade') is not None:
            run['outcome_policy_upgrade'] = options['outcome_policy_upgrade']
        durable(destination/'run.json',run)
        return run

    def upgrade(self, **options):
        with patch('regen.tools.parallel_workspace_service.status',return_value={
                    'running':False,'identity_verified':True,'status':'stopped'}), \
             patch.object(execution,'init_run',side_effect=self.initialize), \
             patch.object(execution,'progress',return_value={}):
            return execution.fork_unresolved_policy_upgrade(self.source,self.destination,
                authorization='explicit test policy upgrade',**options)

    def test_upgrade_preserves_green_seals_memory_and_all_budget_counters(self):
        run = self.upgrade()
        self.assertEqual(run['unresolved_evidence_policy'],NATIVE_ATTEMPTS_POLICY)
        validate_outcome_upgrade_readiness(run,self.destination)
        for name,package in self.fixture.packages.items():
            state = execution.journal(self.destination,run,package).replay(repair=False)
            self.assertEqual(state['proofs'],{'helper':'by simp'})
            self.assertEqual(state['batch_requests'],319)
            self.assertEqual(state['batch_notes'],'preserve memory')
            self.assertEqual(state['status'],'generating' if name=='Target' else 'sealed')
        rows = Ledger(self.destination/'effective-generation-budget',run['run_id']).rows
        state = next(r['data']['state'] for r in reversed(rows) if r['kind']==CHECKPOINT_KIND)
        self.assertEqual(state['effective_seconds'],17)
        self.assertEqual(state['worker_seconds'],17)
        self.assertEqual(state['seconds'],100)
        self.assertEqual((self.source/'run.json').read_bytes(),self.source_run)
        self.assertEqual(Ledger(self.source/'effective-generation-budget',self.fixture.run['run_id']).rows,self.source_rows)

    def test_legacy_theory_unknown_is_preserved_as_history_not_mass_dispositions(self):
        package = self.fixture.packages['Target']
        state = execution.journal(self.source,self.fixture.run,package).replay(repair=False)
        controller = SimpleNamespace(root=self.source,run=self.fixture.run,theory='Target',
            _state=lambda _:(deepcopy(package),deepcopy(state)))
        old = record_unresolved(controller,reason='One helper failed',origin='controller')
        run = self.upgrade()
        archive = execution.read(self.destination/'legacy-outcomes-policy-upgrade.json')
        self.assertEqual(archive['outcomes'][0]['outcome'],old)
        self.assertFalse((self.destination/'contract-dispositions').exists())
        controller.root=self.destination;controller.run=run
        self.assertIsNone(current_outcome(controller))
        self.assertEqual(set(pending_contract_ids(controller)),
                         {c['contract_id'] for c in package['contracts']}-set(state['proofs']))

    def test_completed_refutation_is_rejected_without_reconciling_source(self):
        directory = self.source/'check-tasks'/'legacy-negative-check'
        durable(directory/'task.json', {'kind':'refutation', 'status':'completed', 'theory':'Target'})
        durable(directory/'result.json', {'status':'refuted'})
        before = {str(path.relative_to(self.source)):path.read_bytes()
                  for path in self.source.rglob('*') if path.is_file() and not path.name.endswith('.lock')}
        with patch.object(execution, '_continuation_outcome') as outcome:
            with self.assertRaisesRegex(ValueError, 'completed refutation'):
                self.upgrade()
            outcome.assert_not_called()
        self.assertFalse(self.destination.exists())
        after = {str(path.relative_to(self.source)):path.read_bytes()
                 for path in self.source.rglob('*') if path.is_file() and not path.name.endswith('.lock')}
        self.assertEqual(before, after)

    def test_partial_preparation_cannot_start_without_completed_budget_receipt(self):
        with patch('regen.infrastructure.parallel_generation_budget.carry_parallel_budget',
                   side_effect=ValueError('simulated transfer interruption')):
            with self.assertRaisesRegex(ValueError,'transfer interruption'):
                self.upgrade()
        run=execution.read(self.destination/'run.json')
        with self.assertRaisesRegex(ValueError,'incomplete'):
            validate_outcome_upgrade_readiness(run,self.destination)
        self.assertEqual((self.source/'run.json').read_bytes(),self.source_run)

    def test_tampered_prepared_budget_claim_is_rejected(self):
        run = self.upgrade()
        path = self.destination/'outcome-policy-upgrade.json'
        receipt = execution.read(path);receipt['budget']['carried_effective_seconds']=0
        durable(path,receipt)
        with self.assertRaisesRegex(ValueError,'budget transfer'):
            validate_outcome_upgrade_readiness(run,self.destination)

    def test_live_source_or_non_document_mode_rejected_before_destination(self):
        with patch('regen.tools.parallel_workspace_service.status',return_value={
                   'running':True,'identity_verified':True,'status':'running'}):
            with self.assertRaisesRegex(ValueError,'verifiably exited'):
                execution.fork_unresolved_policy_upgrade(self.source,self.destination,authorization='test')
        self.assertFalse(self.destination.exists())
        durable(self.source/'parallel-workspace-policy.json',{**self.policy,'file_planner_interface':'graph'})
        with self.assertRaisesRegex(ValueError,'document mode'):
            self.upgrade()
        self.assertFalse(self.destination.exists())

    def test_unauthorized_upgrade_rejected_before_reading_or_creating_state(self):
        with self.assertRaisesRegex(ValueError,'authorization'):
            execution.fork_unresolved_policy_upgrade(self.source,self.destination,authorization='')
        self.assertFalse(self.destination.exists())


if __name__ == '__main__':
    unittest.main()
