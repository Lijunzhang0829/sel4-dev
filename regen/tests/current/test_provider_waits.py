"""Dependency waits and review decisions never add proofs or reset budgets."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import digest
from regen.infrastructure.experiment_ledger import Ledger
from regen.planning.provider_waits import record_wait,current_wait,claim_ready_wait
from regen.planning.theory_outcomes import current_outcome,record_unresolved,reopen_unresolved
from regen.planning.review_handoff import (review_context,record_review_decision,
    require_review_decisions,install_review_handoff,validate_handoff)


class ProviderWaitTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.run={'run_id':'fixture'}
        self.packages={name:{'theory':name,'file_slot':name+'.thy','plan_id':'p',
            'contracts':[{'contract_id':name.lower(),'name':name.lower()}],
            'repository_projection':{'required_imports':[]}}
            for name in ('A','B','C')}
        self.states={name:{'graph':{'nodes':[]},'proofs':{},'accepted_order':[],
            'status':'generating','seal_id':None} for name in self.packages}
        self.c=self.controller('A')

    def controller(self,theory):
        return SimpleNamespace(root=self.root,run=self.run,theory=theory,
            shared=SimpleNamespace(entries={name:{'file_slot':name+'.thy'} for name in self.packages}),
            _state=lambda name:(self.packages[name],self.states[name]),
            _effective_providers=lambda package,state:package['repository_projection']['required_imports'])

    def wait(self,theory='A',providers=None,**extra):
        return record_wait(self.controller(theory),{'action':'wait_for_providers',
            'providers':providers or ['B'],'entry_ids':[theory.lower()],
            'reason':'Need its operation correspondence',**extra},{'call_id':'fixture'})

    def seal(self,name):
        self.states[name].update(status='sealed',seal_id='seal-'+name)

    def test_wait_survives_controller_restart_and_resumes_once_with_provider_version(self):
        before=deepcopy(self.states)
        receipt=self.wait()
        self.assertTrue(receipt['theory_wait'])
        self.assertEqual(self.states,before)
        fresh=self.controller('A')
        self.assertFalse(current_wait(fresh)['ready'])
        with self.assertRaisesRegex(ValueError,'not ready'):claim_ready_wait(fresh)
        self.seal('B')
        self.assertTrue(current_wait(fresh)['ready'])
        resumed=claim_ready_wait(fresh)
        self.assertEqual(resumed['providers_current'][0]['seal_id'],'seal-B')
        self.assertIsNone(current_wait(fresh))
        self.assertIsNone(claim_ready_wait(fresh))
        self.assertEqual(self.states['A'],before['A'])
        self.assertFalse((self.root/'effective-generation-budget').exists())

    def test_wait_cycles_include_hard_dependencies_and_other_waits(self):
        self.wait()
        with self.assertRaisesRegex(ValueError,'cycle'):self.wait('B',['A'])
        self.packages['C']['repository_projection']['required_imports']=['A']
        with self.assertRaisesRegex(ValueError,'cycle'):self.wait('B',['C'])

    def test_reject_ready_self_foreign_and_duplicate_providers(self):
        for providers in [['A'],['foreign'],['B','B']]:
            with self.subTest(providers=providers),self.assertRaises(ValueError):self.wait(providers=providers)
        self.seal('B')
        with self.assertRaisesRegex(ValueError,'already exported'):self.wait()

    def test_different_provider_package_is_not_a_wakeup(self):
        self.wait();self.packages['B']['contracts'][0]['name']='changed'
        with self.assertRaisesRegex(ValueError,'package changed'):current_wait(self.c)

    def test_provider_unknown_retains_wait_and_never_becomes_available(self):
        self.wait()
        record_unresolved(self.controller('B'),reason='local gap',origin='model')
        wait=current_wait(self.c)
        self.assertFalse(wait['ready'])
        self.assertEqual(wait['unavailable_providers'],['B'])
        self.assertIsNone(current_outcome(self.c))

    def test_unknown_authorized_reopen_is_one_time_and_new_unknown_still_stops(self):
        before=deepcopy(self.states)
        first=record_unresolved(self.c,reason='harness bug',origin='model')
        reopened=reopen_unresolved(self.c,'A',authorization='user-request',reason='repaired harness')
        self.assertEqual(reopened['data']['outcome_event_id'],first['receipt']['event_id'])
        self.assertIsNone(current_outcome(self.controller('A')))
        with self.assertRaisesRegex(ValueError,'current UNKNOWN'):
            reopen_unresolved(self.c,'A',authorization='same',reason='repeat')
        second=record_unresolved(self.controller('A'),reason='still missing proof',origin='model')
        self.assertEqual(current_outcome(self.c)['receipt'],second['receipt'])
        self.assertEqual(before,self.states)
        self.assertEqual(len(Ledger(self.root/'theory-outcomes'/digest('A'),'fixture').rows),3)

    def handoff(self):
        from regen.planning.plan_review import digest as plan_digest
        plan={'plan_id':'p','theories':[{'name':n,'contracts':p['contracts']} for n,p in self.packages.items()]}
        issue={'id':'I1','theories':['A','B'],'contract_ids':['a','b'],'evidence':'missing B interface'}
        handoff={'schema_version':1,'policy':'review-obligations-v1','plan_sha256':plan_digest(plan),
            'theories':{n:{'assessment':'inspect exact premises','obligations':[]}
                        for n in self.packages}}
        handoff['theories']['A']['obligations']=[{'issue_id':'stable-issue','issue':issue,
            'disposition':None,'status':'unresolved'}]
        return plan,handoff

    def install(self):
        plan,handoff=self.handoff()
        install_review_handoff(self.c,handoff,plan=plan,authorization='explicit-recovery')
        return handoff

    def test_legacy_handoff_does_not_rewrite_package_or_claim_decision(self):
        before=deepcopy(self.packages);self.install()
        context=review_context(self.c)
        self.assertEqual(before,self.packages)
        self.assertTrue(context['obligations'][0]['requires_generation_decision'])
        with self.assertRaisesRegex(ValueError,'record_review_decision'):require_review_decisions(self.c)
        with self.assertRaisesRegex(ValueError,'already installed'):self.install()

    def test_handoff_rejects_plan_hash_and_contract_drift(self):
        plan,handoff=self.handoff();handoff['plan_sha256']='wrong'
        with self.assertRaisesRegex(ValueError,'frozen plan'):validate_handoff(handoff,plan)
        plan,handoff=self.handoff();self.packages['A']['contracts']=[{'contract_id':'different'}]
        with self.assertRaises(ValueError):install_review_handoff(self.c,handoff,plan=plan,authorization='x')

    def decision(self,choice='local',**extra):
        return record_review_decision(self.c,{'action':'record_review_decision','issue_id':'stable-issue',
            'decision':choice,'reason':'Check operation premises in the target context',
            'entry_ids':['a'],'obligations':['Prove the branch preservation predicate'],**extra},{})

    def test_local_decision_records_task_without_positive_proof(self):
        self.install();before=deepcopy(self.states)
        self.decision();require_review_decisions(self.c)
        self.assertEqual(before,self.states)
        self.assertEqual(review_context(self.c)['obligations'][0]['generation_decision']['positive_proofs_added'],0)

    def test_import_disposition_requires_accepted_dependency_not_a_candidate_name(self):
        self.install()
        with self.assertRaisesRegex(ValueError,'accepted provider imports'):
            self.decision('import',providers=['B'])
        self.packages['A']['repository_projection']['required_imports']=['B']
        with self.assertRaisesRegex(ValueError,'accepted provider imports'):
            self.decision('import',providers=['B'])
        from regen.execution.proof_commits import durable
        durable(self.root/'theories/A/imports.json',['Regen_B'])
        with self.assertRaisesRegex(ValueError,'certified provider exports'):
            self.decision('import',providers=['B'])
        self.seal('B')
        self.decision('import',providers=['B'])

    def test_import_review_becomes_pending_again_after_provider_import_is_removed(self):
        from regen.execution.proof_commits import durable
        self.install();self.seal('B')
        path=self.root/'theories/A/imports.json'
        durable(path,['Regen_B']);self.decision('import',providers=['B'])
        require_review_decisions(self.c)
        durable(path,[])
        issue=review_context(self.c)['obligations'][0]
        self.assertTrue(issue['requires_generation_decision'])
        self.assertEqual(issue['decision_invalid_reason'],'selected_provider_no_longer_imported')
        with self.assertRaisesRegex(ValueError,'record_review_decision'):
            require_review_decisions(self.c)
        self.decision('local');require_review_decisions(self.c)

    def test_transitive_import_review_is_rechecked_when_provider_closure_changes(self):
        from regen.execution.proof_commits import durable
        self.install();self.seal('B');self.seal('C')
        durable(self.root/'theories/A/imports.json',['Regen_B'])
        path=self.root/'theories/B/imports.json'
        durable(path,['Regen_C']);self.decision('import',providers=['C'])
        require_review_decisions(self.c)
        durable(path,[])
        with self.assertRaisesRegex(ValueError,'record_review_decision'):
            require_review_decisions(self.c)

    def test_disposed_contract_cannot_create_a_provider_wait(self):
        from regen.planning.theory_outcomes import CONTRACT_POLICY
        self.run['contract_outcome_policy']=CONTRACT_POLICY
        self.packages['A']['contracts'][0].update(exact_statement='lemma a: "False"',
                                                contract_owned_context=[])
        record_unresolved(self.c,reason='Synthetic local search exhausted',origin='model',entry_ids=['a'])
        with self.assertRaisesRegex(ValueError,'terminal dispositions cannot wait'):
            self.wait()
        self.assertIsNone(current_wait(self.c))
        self.assertFalse(hasattr(self.c,'provider_wait'))

    def test_wait_disposition_resumes_as_explicit_pending_choice(self):
        self.install()
        self.wait(review_issue_ids=['stable-issue'])
        self.assertEqual(review_context(self.c)['obligations'][0]['generation_decision']['decision'],'wait')
        self.seal('B');claim_ready_wait(self.c)
        with self.assertRaisesRegex(ValueError,'record_review_decision'):require_review_decisions(self.c)
        self.decision('local');require_review_decisions(self.c)

    def test_wait_unlocks_provider_and_its_hard_dependencies_across_priority_stages(self):
        from regen.planning.parallel_file_planner import _stage_eligible
        rows=[{'theory':'A','status':'generating','selected':True,
               'provider_wait':{'providers':[{'theory':'B'}]}},
              {'theory':'B','status':'pending','selected':True,'required_imports':['C']},
              {'theory':'C','status':'pending','selected':True}]
        self.assertEqual(_stage_eligible(rows,{'stages':[['A'],['B'],['C']]}),{'A','B','C'})


class ProviderWaitControllerTests(unittest.TestCase):
    def test_real_controller_stops_worker_without_outcome_or_proof_change(self):
        from regen.tests.current.test_parallel_file_planner import ParallelControllerTests
        fixture=ParallelControllerTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        controller=fixture.controller
        before=deepcopy(controller._state('Target')[1])
        result=controller.handle('proof_action',{'theory':'Target','proposal':{
            'action':'wait_for_providers','providers':['Source'],'entry_ids':['target_contract'],
            'reason':'Await its fixed contract export'}},fixture.metadata)
        self.assertTrue(result['theory_wait'])
        self.assertNotIn('theory_stop',result)
        self.assertIsNone(current_outcome(controller))
        self.assertEqual(before,controller._state('Target')[1])
        next_result=controller.handle('proof_action',{'theory':'Target','proposal':{
            'action':'submit_batch','proofs':{'target_contract':'by simp'}}},fixture.metadata)
        self.assertTrue(next_result['theory_wait'])
        self.assertEqual(before,controller._state('Target')[1])
        rows=controller.list_workspace()['theories']
        self.assertFalse(next(r for r in rows if r['theory']=='Target')['ready_for_proof_action'])



class ProviderWaitContinuationTests(unittest.TestCase):
    def test_custom_run_id_keeps_proof_directory_unique_and_preserves_stop(self):
        from regen.tests.current.test_theory_outcome_continuation import TheoryOutcomeContinuationTests
        from regen.execution import repository_execution as execution
        from regen.execution.proof_commits import durable
        fixture=TheoryOutcomeContinuationTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.unknown('Committed')
        destination=fixture.root/'new-experiment'/'proof'
        def initialize(dest,context,run_id,schedule,**options):
            dest.parent.mkdir(parents=True,exist_ok=True)
            run=fixture.initialize(dest)
            run['run_id']=run_id;durable(dest/'run.json',run)
            return run
        with patch.object(execution,'init_run',side_effect=initialize),patch.object(execution,'progress',return_value={}):
            run=execution.fork_workspace_continuation(fixture.source,destination,run_id='new-experiment')
        self.assertEqual(run['run_id'],'new-experiment')
        self.assertEqual(current_outcome(fixture.controller(destination,run,'Committed'))['status'],'unknown')
        for identity in ['',fixture.run['run_id']]:
            with self.assertRaisesRegex(ValueError,'run_id'):
                execution.fork_workspace_continuation(fixture.source,fixture.root/'invalid',run_id=identity)

    def test_wait_and_decisions_rebind_in_continuation_with_source_receipts(self):
        from regen.planning.provider_waits import carry_coordination
        from regen.execution.proof_commits import durable
        from regen.tests.current.test_shared_proof_workspace import setup_run
        from regen.planning.proof_workspace_agent import WorkspaceController
        from regen.execution.shared_proof_workspace import SharedProofWorkspace
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';destination=root/'destination'
            run,packages,_,_=setup_run(source)
            def view(path):
                obj=WorkspaceController.__new__(WorkspaceController)
                obj.root=path;obj.shared=SharedProofWorkspace(path);obj.run=obj.shared.run
                obj.document_mode=True;obj.selected=list(obj.shared.entries);obj.theory='Target'
                return obj
            old=view(source)
            wait=record_wait(old,{'action':'wait_for_providers','providers':['Source'],
                'entry_ids':['target_contract'],'reason':'Need the checked Source export'}, {})
            # This fixture copies equal verified journals; production first performs
            # fork_workspace_continuation's full acceptance replay before this step.
            shutil.copytree(source,destination)
            shutil.rmtree(destination/'provider-waits')
            rebound=deepcopy(run);rebound['contract_package_dir']=str(destination/'packages')
            durable(destination/'run.json',rebound)
            carry_coordination(source,destination)
            current=current_wait(view(destination),'Target')
            self.assertEqual(current['binding']['run_sha256'],digest(rebound))
            self.assertEqual(current['continuation_source']['events'][-1]['event_id'],wait['provider_wait']['receipt_id'])
            self.assertFalse(current['ready'])
            self.assertTrue((destination/'coordination-continuation.json').exists())



class ProviderWaitSchedulingTests(unittest.TestCase):
    def schedule(self,provider_succeeds=True):
        import hashlib
        from regen.planning.parallel_file_planner import run_parallel_workspace
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);binary=root/'codex';binary.write_text('fixture')
            states={'A':'pending','B':'pending'};waiting=[False];started=[]
            class Overview:
                selected=['A','B'];run={'run_id':'test','execution_input_kind':'refine-dataset',
                    'checker_context_root':str(root),'checker_context_id':'fixture'}
                options={'model':'test','call_timeout':10,'check_timeout':10,'candidate_timeout':10}
                def __init__(self,*a,**k):self.root=root
                def list_workspace(self):
                    rows=[]
                    for name,status in states.items():
                        row={'theory':name,'status':status,'selected':True,'contracts':[name],
                            'required_imports':[],'unsealed_providers':[],
                            'ready_for_proof_action':status=='pending'}
                        if name=='A' and waiting[0]:
                            row['provider_wait']={'providers':[{'theory':'B'}],'receipt_id':'wait',
                                'unavailable_providers':[] if states['B']=='sealed' else ['B']}
                            row['ready_for_proof_action']=states['B']=='sealed' and status=='pending'
                        rows.append(row)
                    return {'theories':rows}
            def session(root,theory,**kw):
                started.append(theory)
                if theory=='A' and not waiting[0]:
                    waiting[0]=True
                    return {'theory':'A','status':'waiting_for_provider'}
                if theory=='B' and not provider_succeeds:
                    return {'theory':'B','status':'unknown'}
                states[theory]='sealed'
                return {'theory':theory,'status':'sealed'}
            settings={'codex_bin':str(binary),'codex_runtime_root':str(root/'runtime'),
                      'codex_sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}
            with patch('regen.planning.parallel_file_planner.WorkspaceController',Overview),patch(
                    'regen.planning.parallel_file_planner.run_theory_session',side_effect=session),patch(
                    'regen.infrastructure.audited_llm.model_settings',return_value=settings),patch(
                    'regen.preparation.checker_context.load_context',return_value={'checker_context_id':'fixture'}):
                result=run_parallel_workspace(root, budget_mode="effective-time",allow_external_call=True,integrate=False,max_parallel_theories=1)
            return started,result

    def test_wait_releases_slot_then_provider_seal_resumes_consumer(self):
        started,result=self.schedule()
        self.assertEqual(started,['A','B','A'])
        self.assertEqual(result['status'],'selected_theories_sealed')
        self.assertEqual(result['failures'],{})

    def test_provider_unknown_reports_blocked_wait_without_spin_or_false_success(self):
        started,result=self.schedule(False)
        self.assertEqual(started,['A','B'])
        self.assertEqual(result['status'],'partial')
        self.assertFalse(result['repository_verified'])
        self.assertEqual(result['blocked']['A']['reason'],'waiting_for_provider')
        self.assertEqual(result['blocked']['A']['provider_failures']['B']['status'],'unknown')


if __name__=='__main__':unittest.main()
