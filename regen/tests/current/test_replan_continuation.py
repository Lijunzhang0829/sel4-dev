"""Plan advice never promotes an old proof into an unchecked import context."""
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from regen.execution import repository_execution as execution
from regen.execution import replan_continuation as replan
from regen.execution.proof_commits import durable, digest
from regen.tests.current import test_continuation_providers as fixtures
from regen.tests.current import test_theory_outcome_continuation as outcome_fixtures


class ReplannedContinuationTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ContinuationProvidersTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.source = self.fixture.root, self.fixture.source
        self.packages = deepcopy(self.fixture.packages)
        for package in self.packages.values():
            package['plan_id'] = 'new-reviewed-plan'
            package['repository_projection']['planning_advice'] = 'revised allocation'
            self.rehash(package)

    @staticmethod
    def rehash(package):
        package['package_payload_sha256'] = digest({k:v for k,v in package.items()
                                                   if k != 'package_payload_sha256'})

    def initialize(self,destination,_input,run_id,schedule,**_kwargs):
        destination.mkdir()
        run = {**self.fixture.run,'run_id':run_id,
               'session_schedule':{'source':str(schedule),'plan_id':'new-reviewed-plan'},
               'contract_package_dir':str(destination/'packages')}
        durable(destination/'run.json',run)
        for package in self.packages.values():
            durable(destination/'packages'/(package['file_slot']+'.json'),package)
        durable(destination/'execution-input.json',{'files':self.fixture.files,'package_hashes':{
            p['file_slot']:p['package_payload_sha256'] for p in self.packages.values()}})
        return run

    def fork(self):
        destination = self.root/'replanned'
        with patch.object(replan,'_packages',return_value=self.packages), \
             patch.object(execution,'init_run',side_effect=self.initialize), \
             patch.object(execution,'progress',return_value={}):
            run = replan.fork_replanned_workspace(self.source,destination,self.root/'new-plan/list.json',
                run_id='replanned',authorization='explicit test authorization')
        return destination,run

    def test_new_packages_replay_old_green_and_seal_evidence(self):
        destination,run = self.fork()
        for name in ('Base','Provider','Target'):
            state = execution.journal(destination,run,self.packages[name]).replay(repair=False)
            self.assertEqual(state['proofs'],{'helper':'by simp'})
            self.assertEqual(state['batch_requests'],319)
            self.assertEqual(state['status'],'generating' if name=='Target' else 'sealed')
        receipt = execution.read(destination/'replanning-continuation.json')
        self.assertEqual(receipt['pending_imports'],{})
        self.assertFalse(receipt['budget_reset'])
        self.assertEqual(run['session_schedule']['plan_id'],'new-reviewed-plan')

    def test_import_change_is_draft_only_and_preserves_certified_context(self):
        package = self.packages['Target']
        package['repository_projection']['suggested_predecessors'] = [{'theory':'Base'}]
        self.rehash(package)
        destination,run = self.fork()
        state = execution.journal(destination,run,package).replay(repair=False)
        self.assertEqual(state['proofs'],{'helper':'by simp'})
        self.assertNotIn('context_revision',state)
        self.assertEqual(execution.read(destination/'theories/Target/imports.json'),['Regen_Provider'])
        text = (destination/'theories/Target/document/draft.thy').read_text()
        self.assertIn('"Generated_Refine.Regen_Base"',text)
        self.assertIn('by simp',text)
        receipt = execution.read(destination/'replanning-continuation.json')
        self.assertEqual(receipt['pending_imports']['Target']['desired'],['Regen_Base'])
        self.assertFalse(receipt['pending_imports']['Target']['accepted_proofs_reused_in_new_context'])
        self.assertEqual(replan.pending_replanned_providers(destination,run,package,state),['Base'])
        transitioned = {**state,'context_revision':1}
        self.assertEqual(replan.pending_replanned_providers(destination,run,package,transitioned),[])

    def test_corrupt_old_proof_rejected_before_destination(self):
        path = self.source/'theories/Target/acceptance/commits/000002.json'
        old = execution.read(path); old['state']['proofs']['helper']='by blast'
        durable(path,old)
        with self.assertRaisesRegex(ValueError,'source chain'):
            self.fork()
        self.assertFalse((self.root/'replanned').exists())

    def test_changed_contracts_rejected_before_destination(self):
        self.packages['Target']['contracts'] = [{'contract_id':'new','name':'new'}]
        self.rehash(self.packages['Target'])
        with self.assertRaisesRegex(ValueError,'fixed contracts'):
            self.fork()
        self.assertFalse((self.root/'replanned').exists())

    def test_changed_sealed_imports_rejected_before_destination(self):
        self.packages['Provider']['repository_projection']['suggested_predecessors'] = [{'theory':'Target'}]
        self.rehash(self.packages['Provider'])
        with self.assertRaisesRegex(ValueError,'sealed imports'):
            self.fork()
        self.assertFalse((self.root/'replanned').exists())

    def test_rehashed_source_package_cannot_change_proof_authority(self):
        destination,run = self.fork()
        record = execution.read(destination/'theories/Target/acceptance/commits/000001.json')
        origin = record['evidence']['continuation']
        origin['replanned_from_package']['repository_projection']['planning_advice']='forged'
        self.rehash(origin['replanned_from_package'])
        with self.assertRaisesRegex(ValueError,'source chain'):
            execution.continuation_state(self.packages['Target'],origin)

    def test_removed_theory_rejected_before_destination(self):
        del self.packages['Target']
        with self.assertRaisesRegex(ValueError,'theory allocation'):
            self.fork()
        self.assertFalse((self.root/'replanned').exists())

    def test_unknown_preserved_then_explicitly_reopened_under_new_plan(self):
        self.fixture = outcome_fixtures.TheoryOutcomeContinuationTests()
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.root,self.source = self.fixture.root,self.fixture.source
        old = self.fixture.unknown('Committed')
        self.packages = deepcopy(self.fixture.packages)
        for package in self.packages.values():
            package['plan_id']='new-reviewed-plan'; self.rehash(package)
        destination,run = self.fork()
        from regen.planning.theory_outcomes import current_outcome
        from regen.infrastructure.experiment_ledger import Ledger
        view = self.fixture.controller(destination,run,'Committed')
        self.assertIsNone(current_outcome(view))
        events = Ledger(destination/'theory-outcomes'/digest('Committed'),run['run_id']).rows
        self.assertEqual([e['kind'] for e in events],['theory_outcome','theory_outcome_reopened'])
        self.assertEqual(events[0]['data']['metadata']['replanning_source_outcome'],old)
        self.assertEqual(current_outcome(self.fixture.controller(self.source,self.fixture.run,'Committed')),old)

    def test_readiness_waits_on_new_plan_even_with_an_old_context_revision(self):
        package = self.packages['Target']
        package['repository_projection']['suggested_predecessors']=[{'theory':'Base'}]
        self.rehash(package)
        destination,run = self.fork()
        from types import SimpleNamespace
        from regen.planning.proof_workspace_agent import WorkspaceController
        controller = SimpleNamespace(root=destination,run=run,shared=SimpleNamespace(entries={
            e['theory']:e for e in self.fixture.files}))
        for state in ({}, {'context_imports':['Regen_Provider'],'context_revision':0}):
            result = WorkspaceController._effective_providers(controller,package,state)
            self.assertIn('Base',result)
        result = WorkspaceController._effective_providers(controller,package,
            {'context_imports':['Regen_Provider'],'context_revision':1})
        self.assertEqual(result,['Provider'])

    def test_pending_imports_survive_an_ordinary_later_continuation(self):
        package = self.packages['Target']
        package['repository_projection']['suggested_predecessors']=[{'theory':'Base'}]
        self.rehash(package)
        destination,run = self.fork()
        original = execution.read(destination/'replanning-continuation.json')
        later = self.root/'later'
        def initialize_later(target,*_args,**_kwargs):
            import shutil
            target.mkdir()
            shutil.copytree(destination/'packages',target/'packages')
            durable(target/'execution-input.json',execution.read(destination/'execution-input.json'))
            newrun = {**run,'run_id':'later','contract_package_dir':str(target/'packages')}
            durable(target/'run.json',newrun)
            return newrun
        with patch.object(execution,'init_run',side_effect=initialize_later), \
             patch.object(execution,'progress',return_value={}):
            newrun = execution.fork_workspace_continuation(destination,later)
        state = execution.journal(later,newrun,package).replay(repair=False)
        receipt = execution.read(later/'replanning-continuation.json')
        self.assertEqual(receipt['pending_imports'],original['pending_imports'])
        self.assertEqual(replan.pending_replanned_providers(later,newrun,package,state),['Base'])
        self.assertEqual((later/'theories/Target/document/draft.thy').read_text(),
                         (destination/'theories/Target/document/draft.thy').read_text())

    def test_pending_imports_survive_a_second_plan_with_unchanged_requirements(self):
        package = self.packages['Target']
        package['repository_projection']['suggested_predecessors']=[{'theory':'Base'}]
        self.rehash(package)
        destination,run = self.fork()
        self.source = destination; self.fixture.run = run
        # Reset only the test destination identity; real source remains untouched.
        later = self.root/'twice'
        for value in self.packages.values():
            value['plan_id']='second-plan'; self.rehash(value)
        with patch.object(replan,'_packages',return_value=self.packages), \
             patch.object(execution,'init_run',side_effect=self.initialize), \
             patch.object(execution,'progress',return_value={}):
            newrun = replan.fork_replanned_workspace(destination,later,self.root/'next-plan/list.json',
                run_id='twice',authorization='second explicit request')
        receipt = execution.read(later/'replanning-continuation.json')
        self.assertIn('Target',receipt['pending_imports'])
        state = execution.journal(later,newrun,self.packages['Target']).replay(repair=False)
        self.assertEqual(replan.pending_replanned_providers(later,newrun,self.packages['Target'],state),['Base'])


if __name__ == '__main__':
    unittest.main()
