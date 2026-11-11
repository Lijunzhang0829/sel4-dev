"""Serial theory tools use real batch journals; synthetic native evidence never denotes a live experiment."""
from copy import deepcopy
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from regen.execution.check_executor import CheckExecutor, NativeResources
from regen.execution.proof_commits import digest, durable
from regen.execution.repository_execution import journal
from regen.planning.parallel_file_planner import TheoryController
from regen.tests.current import test_proof_workspace_agent as fixtures


class ParallelControllerTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.ProofWorkspaceAgentTest();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.root
        self.resources=NativeResources(2,1,2)
        self.executor=CheckExecutor(self.root/'tasks',workers=3)
        self.addCleanup(self.resources.close);self.addCleanup(self.executor.close)
        self.controller=TheoryController(self.root,'Target',resources=self.resources,executor=self.executor)
        self.addCleanup(self.controller.close)
        self.fixture.local_runtime(self.controller)
        self.metadata={'thread_id':'t','turn_id':'u','call_id':'one','transcript_path':'synthetic-test-transcript'}

    def wait(self, key):
        for _ in range(8):
            row=self.executor.wait([key],timeout=1)[0]
            if row['status'] in {'completed','error','cancelled'}:return row
        self.fail('background action did not finish')

    def test_serial_submit_returns_committed_result_once(self):
        row=self.controller.handle('submit_check',{'theory':'Target','kind':'proof_batch',
            'proposal':{'action':'submit_batch','proofs':{'target_contract':'by simp'}}},self.metadata)
        self.assertEqual(row['status'],'completed',row)
        result=self.wait(row['task_id'])
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(result['result']['feedback']['status'],'accepted')
        _,state=self.controller._state('Target')
        self.assertEqual(state['accepted_order'],['target_contract'])
        count=state['batch_requests']
        for _ in range(2):self.controller.handle('get_check_result',{'task_id':row['task_id']},{})
        self.assertEqual(self.controller._state('Target')[1]['batch_requests'],count)
        self.fixture.llm.assert_not_called()

    def test_synchronous_compatibility_uses_durable_check_executor(self):
        result=self.controller.handle('proof_action',{'theory':'Target',
            'proposal':{'action':'submit_batch','proofs':{'target_contract':'by simp'}}},self.metadata)
        self.assertEqual(result['feedback']['status'],'accepted')
        self.assertEqual(len(self.controller.task_ids),1)
        row=self.executor.get(next(iter(self.controller.task_ids)))
        self.assertEqual(row['status'],'completed')
        self.assertEqual(row['kind'],'proof_batch')

    def test_native_preparation_error_crosses_executor_without_feedback(self):
        from regen.execution.check_tasks import NativePreparationError
        with patch('regen.execution.proof_batches.check_batch', return_value={
                'check':{'verdict':'infrastructure_error','failure_scope':'native_preparation',
                         'diagnostic':'prefix deadline'}}):
            with self.assertRaisesRegex(NativePreparationError, 'prefix deadline'):
                self.controller.handle('proof_action', {'theory':'Target',
                    'proposal':{'action':'submit_batch','proofs':{'target_contract':'by simp'}}}, self.metadata)
        row = self.executor.get(next(iter(self.controller.task_ids)))
        self.assertEqual(row['error_type'], 'NativePreparationError')
        self.assertEqual(self.controller._state('Target')[1]['proofs'], {})

    def test_code_drift_crosses_executor_as_terminal_infrastructure_error(self):
        from regen.execution.check_tasks import ImplementationChangedError, NativePreparationError
        self.assertTrue(issubclass(ImplementationChangedError, NativePreparationError))
        with patch('regen.execution.proof_batches.check_batch',
                   side_effect=ImplementationChangedError('frozen code drift')):
            with self.assertRaisesRegex(ImplementationChangedError, 'frozen code drift'):
                self.controller.handle('proof_action', {'theory':'Target',
                    'proposal':{'action':'submit_batch','proofs':{'target_contract':'by simp'}}}, self.metadata)
        row = self.executor.get(next(iter(self.controller.task_ids)))
        self.assertEqual(row['status'], 'error')
        self.assertEqual(row['error_type'], 'ImplementationChangedError')
        self.assertEqual(self.controller._state('Target')[1]['proofs'], {})

    def test_native_evidence_mismatch_blocks_worker_without_recording_unknown(self):
        from regen.execution.proof_batches import NativeEvidenceError
        with patch('regen.execution.proof_batches.validate_batch_commit',
                   side_effect=NativeEvidenceError('declaration checker source mismatch')):
            result=self.controller.handle('proof_action',{'theory':'Target',
                'proposal':{'action':'submit_batch','proofs':{'target_contract':'by simp'}}},self.metadata)
        self.assertTrue(result['framework_blocked'])
        self.assertEqual(result['feedback']['status'],'infrastructure_error')
        self.assertEqual(result['feedback']['failure_scope'],'trusted_audit_target')
        self.assertEqual(self.controller._proof_work_version,0)
        self.assertEqual(self.controller._state('Target')[1]['proofs'],{})
        stopped=self.controller.handle('proof_action',{'theory':'Target','proposal':{
            'action':'report_unresolved','reason':'Source mismatch is not mathematical failure'}},self.metadata)
        self.assertTrue(stopped['framework_blocked'])
        self.assertNotIn('theory_stop',stopped)
        self.assertFalse((self.root/'theory-outcomes').exists())

    def test_framework_block_retains_native_failure_scope_on_initial_and_followup_tools(self):
        with patch('regen.planning.proof_workspace_agent.WorkspaceController.proof_action',return_value={
                'feedback':{'status':'infrastructure_error','framework_blocked':True,
                            'failure_scope':'native_preparation','native_failure_stage':'native-io',
                            'diagnostic':'native disk full'}}):
            result=self.controller.handle('proof_action',{'theory':'Target',
                'proposal':{'action':'submit_batch','proofs':{'target_contract':'by simp'}}},self.metadata)
        self.assertEqual(result['failure_scope'],'native_preparation')
        stopped=self.controller.handle('proof_action',{'theory':'Target','proposal':{
            'action':'report_unresolved','reason':'This is an infrastructure failure'}},self.metadata)
        self.assertEqual(stopped['failure_scope'],'native_preparation')
        self.assertEqual(stopped['native_failure_stage'],'native-io')
        self.assertNotIn('theory_stop',stopped)
        self.assertEqual(self.controller._state('Target')[1]['proofs'],{})

    def test_unresolved_action_stops_only_assigned_theory_without_proof_changes(self):
        before=deepcopy(self.controller._state('Target')[1])
        with self.assertRaisesRegex(ValueError,'only the assigned theory'):
            self.controller.handle('proof_action',{'theory':'Other','proposal':{
                'action':'report_unresolved','reason':'suspected false goal'}},self.metadata)
        result=self.controller.handle('proof_action',{'theory':'Target','proposal':{
            'action':'report_unresolved','reason':'suspected false goal',
            'classification':'suspected_counterexample'}},self.metadata)
        self.assertTrue(result['theory_stop'])
        self.assertEqual(result['theory_outcome']['status'],'unknown')
        self.assertEqual(result['theory_outcome']['truth_status'],'undetermined')
        self.assertEqual(before,self.controller._state('Target')[1])
        stopped=self.controller.handle('proof_action',{'theory':'Target','proposal':{
            'action':'submit_batch','proofs':{'target_contract':'by simp'}}},self.metadata)
        self.assertTrue(stopped['theory_stop'])
        self.assertEqual(len(self.controller.task_ids),0)

    def test_no_mathematical_pause_tool_in_new_controller(self):
        with self.assertRaisesRegex(ValueError,'continue local exploration'):
            self.controller.handle('proof_action',{'theory':'Target',
                'proposal':{'action':'pause','reason':'hard proof'}},self.metadata)

    def test_retrieval_task_audit_does_not_change_theory_context_paths(self):
        from regen.planning.batch_file_planner import execute_query,query_identity
        package,state=self.controller._state('Target')
        theory_dir=self.root/'theories'/'target'
        logical=theory_dir/'batches'/'async-task'
        audit=self.root/'tasks'/'query'
        audit.mkdir(parents=True)
        theory_dir.mkdir(parents=True,exist_ok=True)
        sealed=theory_dir/'sealed-premises.json'
        durable(sealed,{'records':[]})
        request={'premise_queries':[{'query':'helper','scope':'base'}]}
        run={'run_id':'test','premise_database':'db'}
        before=query_identity(run,state,logical,request)
        with patch('regen.execution.round_recall.is_fact_request',return_value=True),patch(
                'regen.execution.round_recall.select_round_facts',return_value={'records':[]}) as recall:
            execute_query(run,package,state,None,logical,request,audit_directory=audit)
        self.assertEqual(recall.call_args.kwargs['generated_records'],sealed)
        self.assertEqual(recall.call_args.kwargs['audit_path'],audit/'fact-selection.json')
        durable(sealed,{'records':[{'record_id':'changed'}]})
        self.assertNotEqual(query_identity(run,state,logical,request),before)
        self.assertFalse(logical.exists())

    def test_cancel_before_native_finishes_cannot_commit_green(self):
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        from regen.execution.proof_batches import check_batch
        def native(*args,**kwargs):
            entered.set();release.wait(3)
            return check_batch(*args,**kwargs)
        from concurrent.futures import ThreadPoolExecutor
        with patch('regen.execution.proof_batches.check_batch',side_effect=native), ThreadPoolExecutor(1) as caller:
            future=caller.submit(self.controller.handle,'submit_check',{'theory':'Target','kind':'proof_batch',
                'proposal':{'action':'submit_batch','proofs':{'target_contract':'by simp'}}},self.metadata)
            self.assertTrue(entered.wait(3))
            try:
                self.assertFalse(future.done())
                key=next(iter(self.controller.task_ids))
                self.controller.handle('cancel_check',{'task_id':key},{})
            finally:release.set()
            row=future.result(timeout=5)
        self.assertEqual(row['status'],'cancelled',row)
        self.assertEqual(self.controller._state('Target')[1]['proofs'],{})

    def test_statement_cannot_carry_proof_and_foreign_theory_rejected(self):
        with self.assertRaisesRegex(ValueError,'must not contain'):
            self.controller.submit({'theory':'Target','kind':'statement',
                'proposal':{'action':'submit_batch','proofs':{'target_contract':'by simp'}}},self.metadata)
        with self.assertRaisesRegex(ValueError,'assigned theory'):
            self.controller.submit({'theory':'Source','kind':'proof_batch',
                'proposal':{'action':'submit_batch'}},self.metadata)

    def test_stale_snapshot_rejected_before_proof_action(self):
        # Hold setup after submission, alter only graph identity before it returns.
        # No real run permits this second writer; this injects corruption for the gate.
        original=self.controller._state
        submitted=threading.Event()
        def state(theory):
            package,value=original(theory)
            if submitted.is_set():
                value=deepcopy(value);value['graph']['revision']+=1
            return package,value
        with patch.object(self.controller,'_state',side_effect=state),patch.object(
                self.controller,'_runtime',side_effect=submitted.set),patch(
                'regen.planning.proof_workspace_agent.WorkspaceController.proof_action') as action:
            row=self.controller.submit({'theory':'Target','kind':'proof_batch',
                'proposal':{'action':'submit_batch','proofs':{'target_contract':'by simp'}}},self.metadata)
            result=self.wait(row['task_id'])
        self.assertEqual(result['status'],'error',result)
        self.assertIn('stale',result['diagnostic'])
        action.assert_not_called()


class IntegrationGateTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.ProofWorkspaceAgentTest();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.root

    def test_unsealed_or_retired_contract_cannot_be_repository_success(self):
        from regen.execution.repository_integration import integration_manifest
        with self.assertRaisesRegex(ValueError,'sealed theory'):
            integration_manifest(self.root)

    def test_integration_rejection_is_durable_without_a_native_build(self):
        from regen.execution.repository_integration import integrate_repository
        with patch('regen.execution.isabelle_node_checker.IsabelleChecker.build_generated_session') as build:
            result=integrate_repository(self.root)
        self.assertFalse(result['repository_verified'])
        build.assert_not_called()
        task=self.root/'integration-tasks'/result['task_id']/'task.json'
        self.assertEqual(json.loads(task.read_text())['status'],'completed')

    def sealed_fixture(self):
        from regen.execution.repository_integration import integration_manifest
        from regen.execution.proof_batches import prepare_batch,check_batch
        from regen.execution.proof_session_runner import generated_checkpoint_theory
        from regen.tests.current.test_proof_batches import Checker
        from regen.execution.shared_proof_workspace import SharedProofWorkspace
        inputs=json.loads((self.root/'execution-input.json').read_text())
        for row in inputs['files']:
            row['contract_ids']=[c['contract_id'] for p in self.fixture.packages if p['theory']==row['theory'] for c in p['contracts']]
        # Fixture's list is also its identity, so bind the updated list explicitly.
        run=self.fixture.run
        run['base_import']='Semantic_Base.Semantic_Root'
        from regen.infrastructure.path_identity import source_identity
        for folder in ('isabelle','heaps','l4v','semantic'):
            (self.root/folder).mkdir(exist_ok=True)
        (self.root/'isabelle'/'binary').write_text('frozen test runtime')
        run.update(isabelle_dir=str(self.root/'isabelle'),
            isabelle_sha256=source_identity(self.root/'isabelle',allow_links=True),
            image='test',l4v=str(self.root/'l4v'),semantic_workspace=str(self.root/'semantic'),
            base_session='Semantic_Base',base_catalog='/semantic-base')
        run['session_schedule']['file_list_sha256']=digest(inputs['file_list'])
        durable(self.root/'run.json',run);durable(self.root/'execution-input.json',inputs)
        for package in self.fixture.packages:
            j=journal(self.root,run,package);old=j.replay()
            incoming={n['id']:'by simp' for n in old['graph']['nodes'] if n['id'] not in old['proofs']}
            candidate=prepare_batch(package,old,{'proofs':incoming})
            evidence=check_batch(Checker(),old,candidate);evidence['sealable_generation']=True
            j.append('batch',candidate,evidence)
            name=generated_checkpoint_theory(package['file_slot'])
            from regen.execution.isabelle_node_checker import IsabelleChecker
            renderer=object.__new__(IsabelleChecker);renderer.imports=lambda:[run['base_import']]
            source=renderer.complete_source(candidate['graph'],candidate['proofs'],theory_name=name)
            from regen.tests.current.seal_fixture import seal
            state=deepcopy(candidate);evidence=seal(state)
            j.append('seal',state,evidence)
            (self.root/'generated').mkdir(exist_ok=True)
            (self.root/'generated'/(name+'.thy')).write_text(source)

    def test_derived_scope_mutations_rejected_before_native_build(self):
        from regen.execution.repository_integration import _integrate_repository
        from regen.execution.shared_proof_workspace import SharedProofWorkspace
        self.sealed_fixture()
        path=self.root/'execution-input.json'
        original=json.loads(path.read_text())
        # Bind explicit contract IDs as production planners do.
        for reference,row in zip(original['file_list']['files'],original['files']):
            reference['contract_ids']=row['contract_ids']
        run=json.loads((self.root/'run.json').read_text())
        run['session_schedule']['file_list_sha256']=digest(original['file_list'])
        durable(self.root/'run.json',run)
        for mutation in ('omit_theory','slot','contracts','missing_contracts'):
            with self.subTest(mutation=mutation):
                inputs=deepcopy(original)
                if mutation=='omit_theory':inputs['files'].pop()
                elif mutation=='slot':inputs['files'][0]['file_slot']='different-slot'
                elif mutation=='contracts':inputs['files'][0]['contract_ids']=[]
                else:inputs['files'][0].pop('contract_ids')
                durable(path,inputs)
                with self.assertRaises(ValueError):SharedProofWorkspace(self.root)
                with patch('regen.execution.isabelle_node_checker.IsabelleChecker.build_generated_session') as build:
                    result=_integrate_repository(self.root)
                self.assertFalse(result['repository_verified'])
                build.assert_not_called()
        durable(path,original)
        self.assertEqual(len(SharedProofWorkspace(self.root).fixed_contract_ids),2)

    def test_generic_verified_seals_cover_exact_targets_and_published_sources(self):
        from regen.execution.repository_integration import integration_manifest
        self.sealed_fixture()
        _,manifest,_=integration_manifest(self.root)
        self.assertEqual(len(manifest['contracts']),2)
        from regen.execution.repository_integration import current_verification
        self.assertFalse(current_verification(self.root))
        artifact=self.root/'integration'/'fixture'/'result.json'
        result={'repository_verified':True,'manifest_sha256':digest(manifest)}
        durable(artifact,result)
        durable(self.root/'repository-verification.json',{'artifact':str(artifact),'result_sha256':digest(result)})
        self.assertTrue(current_verification(self.root))
        source=next((self.root/'generated').glob('*.thy'));source.write_text('tampered')
        with self.assertRaisesRegex(ValueError,'published source'):
            integration_manifest(self.root)
        self.assertFalse(current_verification(self.root))


class SchedulerTests(unittest.TestCase):
    def test_model_turn_end_continues_same_session_until_checker_seal(self):
        import tempfile
        from regen.planning.parallel_file_planner import run_theory_session
        class Controller:
            options={'model':'test','call_timeout':10,'check_timeout':10,'candidate_timeout':10}
            run={'run_id':'test'}
            model='test'
            task_ids=set()
            state={'status':'generating','graph':{'nodes':[{'id':'goal'}]},'proofs':{}}
            closed=False
            def __init__(self,*args,**kwargs):self.root=Path(args[0])
            def handle(self,name,args,metadata):
                if name=='list_workspace':return {'theories':[]}
                return {}
            def _state(self,theory):return {},self.state
            def target_contract_green(self):return False
            def close(self):self.closed=True
        class Session:
            prompts=[]
            instance=None
            def __init__(self,**kwargs):
                self.tool_call_count=0;self.turns=0;Session.instance=self
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def run(self,prompt,handler,**kwargs):
                self.prompts.append(prompt);self.turns+=1;self.tool_call_count+=1
                if self.turns==2:handler.__self__.state['status']='sealed'
                return {'status':'completed','turn_id':str(self.turns)}
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)/'session'
            with patch('regen.planning.parallel_file_planner.TheoryController',Controller),patch(
                    'regen.infrastructure.codex_workspace_session.CodexWorkspaceSession',Session),patch(
                    'regen.infrastructure.workspace_transcript.audit_workspace_session',
                    return_value={'terminal':True}):
                result=run_theory_session(Path(temporary),'Target',
                    resources=type('Resources',(),{'policy':{}})(),executor=object(),
                    settings={'codex_bin':'codex','reasoning_effort':'medium'},directory=directory)
        self.assertEqual(result['status'],'sealed')
        self.assertEqual(result['continuation_turns'],1)
        self.assertEqual(Session.instance.turns,2)
        self.assertIn('previous model turn ended',Session.prompts[1])

    def _empty_turn_scenario(self, temporary, *, progress_field=False,
                             focus=False, on_turn=None, cancel_event=None, effective_budget=False,
                             no_progress_turn_limit=3, retained_outcome=None, retry_unresolved=False):
        """Synthetic provider; never claim these fixtures establish live GREEN."""
        from types import SimpleNamespace
        from regen.planning.parallel_file_planner import run_theory_session
        from regen.infrastructure.codex_workspace_session import SessionCancelled
        test = self
        class Controller:
            options={'model':'test','call_timeout':10,'check_timeout':10,'candidate_timeout':10}
            run={'run_id':'test'};model='test';task_ids=set()
            def __init__(self,*args,**kwargs):
                test.empty_controller=self
                self.root=Path(temporary)
                self.state={'status':'generating','graph':{'nodes':[{'id':'goal'}]},'proofs':{}}
                self.closed=False;self.theory_outcome=None;self._proof_work_version=0
            def handle(self,name,args,metadata):
                return {'theories':[]} if name=='list_workspace' else {}
            def _state(self,theory):return {},self.state
            def target_contract_green(self):return focus and 'goal' in self.state['proofs']
            def _runtime(self):
                return None,None,None,SimpleNamespace(replay_snapshot=lambda:
                    {'commit_id':'synthetic-green','records':[{}]})
            def close(self):self.closed=True
        class Session:
            # No tool_call_count: continuation must not depend on this counter.
            def __init__(self,**kwargs):
                test.empty_session=self
                self.options=kwargs
                self.turns=0;self.closed=False;self.prompts=[]
            def __enter__(self):return self
            def __exit__(self,*args):self.closed=True
            def run(self,prompt,handler,**kwargs):
                self.turns+=1;self.prompts.append(prompt)
                test.assertLessEqual(self.turns,7,'continuation failed to stop')
                test.assertIs(kwargs['cancel_event'],cancel_event)
                if cancel_event is not None and cancel_event.is_set():
                    raise SessionCancelled('workspace coordinator cancelled')
                if on_turn is not None:on_turn(self.turns)
                if self.turns==6 and on_turn is None:
                    if focus:handler.__self__.state['proofs']['goal']='by simp'
                    else:handler.__self__.state['status']='sealed'
                result={'status':'completed','turn_id':str(self.turns)}
                if progress_field:result['progress_made']=False
                return result
        def unresolved(controller, **kwargs):
            controller.theory_outcome = {'status':'unknown','reason':kwargs['reason'],
                                        'receipt':'synthetic-unresolved-receipt'}
            return controller.theory_outcome
        with patch('regen.planning.parallel_file_planner.TheoryController',Controller),patch(
                'regen.infrastructure.codex_workspace_session.CodexWorkspaceSession',Session),patch(
                'regen.infrastructure.workspace_transcript.audit_workspace_session',
                return_value={'terminal':not focus}) as audit,patch(
                'regen.planning.theory_outcomes.record_unresolved',side_effect=unresolved),patch(
                'regen.planning.theory_outcomes.current_outcome',return_value=retained_outcome):
            result=run_theory_session(Path(temporary),'Target',
                resources=type('Resources',(),{'policy':{}})(),executor=object(),
                settings={'codex_bin':'codex','reasoning_effort':'medium'},
                directory=Path(temporary)/'session',cancel_event=cancel_event,
                focus_contract_id='goal' if focus else None,stop_at_focus_contract=focus,
                effective_budget=effective_budget,no_progress_turn_limit=no_progress_turn_limit,
                retry_unresolved=retry_unresolved)
            if result.get('retained_from_prior_invocation'):
                audit.assert_not_called()
                self.assertFalse(hasattr(self, 'empty_session'))
                return result
            self.assertEqual(audit.call_args.kwargs['require_terminal'],
                not focus and result['status'] not in {'framework_blocked','unknown','refuted'})
            return result

    def test_direct_session_respects_retained_unknown_without_starting_model(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            result=self._empty_turn_scenario(temporary,retained_outcome={
                'status':'unknown','receipt':'synthetic-prior-stop'})
        self.assertEqual(result['status'],'unknown')
        self.assertTrue(result['retained_from_prior_invocation'])
        self.assertEqual(result['provider_turns'],[])
        self.assertTrue(self.empty_controller.closed)

    def test_direct_retry_cannot_bypass_retained_refutation(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            result=self._empty_turn_scenario(temporary,retry_unresolved=True,retained_outcome={
                'status':'refuted','receipt':'synthetic-negative-certificate'})
        self.assertEqual(result['status'],'refuted')
        self.assertEqual(result['provider']['status'],'not_started_retained_theory_outcome')
        self.assertFalse(self.empty_controller.state['proofs'])

    def test_direct_explicit_unknown_retry_does_not_latch_old_tool_stop(self):
        import tempfile
        def complete(turn):
            self.assertIsNone(self.empty_controller.theory_outcome)
            self.empty_controller.state['status']='sealed'
        with tempfile.TemporaryDirectory() as temporary:
            result=self._empty_turn_scenario(temporary,retry_unresolved=True,on_turn=complete,
                retained_outcome={'status':'unknown','receipt':'synthetic-prior-stop'})
            policy=json.loads((Path(temporary)/'session/policy.json').read_text())
        self.assertEqual(result['status'],'sealed')
        self.assertEqual(self.empty_session.turns,1)
        self.assertTrue(policy['retry_unresolved'])

    def test_trusted_framework_block_preserves_state_and_stops_auto_continue(self):
        import tempfile
        def block(turn):
            self.empty_controller.state['proofs']['goal'] = 'by simp'
            self.empty_controller.state['batch_feedback'] = {
                'status':'seal_failed','framework_blocked':True,
                'failure_scope':'trusted_audit_target'}
        with tempfile.TemporaryDirectory() as temporary:
            result = self._empty_turn_scenario(temporary, on_turn=block, effective_budget=True)
        self.assertEqual(result['status'], 'framework_blocked')
        self.assertEqual(result['theory_status'], 'generating')
        self.assertEqual(self.empty_session.turns, 1)
        self.assertEqual(self.empty_controller.state['proofs'], {'goal':'by simp'})
        self.assertTrue(self.empty_controller.closed)

    def test_repeated_final_messages_stop_unknown_without_changing_accepted_state(self):
        import tempfile
        for progress_field in (False,True):
            with self.subTest(progress_field=progress_field),tempfile.TemporaryDirectory() as temporary:
                result=self._empty_turn_scenario(temporary,progress_field=progress_field)
                self.assertEqual(result['status'],'unknown')
                self.assertEqual(result['theory_status'],'generating')
                self.assertEqual(result['continuation_turns'],2)
                self.assertEqual(self.empty_session.turns,3)
                self.assertEqual(self.empty_controller.state['proofs'],{})
                self.assertTrue(self.empty_session.closed)
                self.assertTrue(self.empty_controller.closed)
                policy=json.loads((Path(temporary)/'session'/'policy.json').read_text())
                self.assertEqual(policy['progress_policy']['no_progress_turn_limit'],3)
                self.assertEqual(policy['progress_policy']['repeated_result_limit'],8)

    def test_native_work_resets_idle_counter_without_relying_on_model_progress_flag(self):
        import tempfile
        def progress(turn):
            if turn in (2,4):self.empty_controller._proof_work_version += 1
            if turn==6:self.empty_controller.state['status']='sealed'
        with tempfile.TemporaryDirectory() as temporary:
            result=self._empty_turn_scenario(temporary,progress_field=True,on_turn=progress)
        self.assertEqual(result['status'],'sealed')
        self.assertEqual(result['continuation_turns'],5)

    def test_focus_journal_green_remains_completion_authority(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            result=self._empty_turn_scenario(temporary,progress_field=True,focus=True,
                                             no_progress_turn_limit=8)
        self.assertEqual(result['status'],'target_green_completed')
        self.assertEqual(result['theory_status'],'generating')
        self.assertEqual(result['target_authority']['journal_commit_id'],'synthetic-green')

    def test_global_budget_keeps_local_idle_stop_active_without_transport_changes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            result=self._empty_turn_scenario(temporary,progress_field=True,effective_budget=True)
            policy=json.loads((Path(temporary)/'session'/'policy.json').read_text())
        self.assertEqual(result['status'],'unknown')
        self.assertEqual(self.empty_session.turns,3)
        self.assertEqual(self.empty_session.options['progress_timeout'],0)
        self.assertEqual(policy['progress_policy']['no_progress_turn_limit'],3)
        self.assertTrue(policy['progress_policy']['local_stop_preserves_global_budget'])

    def test_explicit_refuted_outcome_stops_session_without_sealing(self):
        import tempfile
        def refute(turn):
            self.empty_controller.theory_outcome={'status':'refuted',
                'reason':'checked negation','receipt':'synthetic-negative-certificate'}
        with tempfile.TemporaryDirectory() as temporary:
            result=self._empty_turn_scenario(temporary,on_turn=refute,effective_budget=True)
        self.assertEqual(result['status'],'refuted')
        self.assertEqual(result['theory_status'],'generating')
        self.assertEqual(self.empty_session.turns,1)
        self.assertEqual(result['theory_outcome']['receipt'],'synthetic-negative-certificate')

    def test_budgeted_coordinator_always_uses_global_budget_session_policy(self):
        from contextlib import nullcontext
        from unittest.mock import Mock
        from regen.planning.parallel_file_planner import _run_budgeted_theory_session
        bridge=Mock();bridge.worker.return_value=nullcontext()
        with patch('regen.planning.parallel_file_planner.run_theory_session',return_value='done') as run:
            self.assertEqual(_run_budgeted_theory_session(bridge,Path('/run'),'Target'),'done')
        run.assert_called_once_with(Path('/run'),'Target',effective_budget=True)
        bridge.worker.assert_called_once_with('Target')

    def test_many_no_progress_turns_remain_externally_cancellable(self):
        import tempfile
        from regen.infrastructure.codex_workspace_session import SessionCancelled
        cancel=threading.Event()
        def cancel_after_six(turn):
            if turn==6:cancel.set()
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(SessionCancelled):
                self._empty_turn_scenario(temporary,progress_field=True,
                    cancel_event=cancel,on_turn=cancel_after_six,no_progress_turn_limit=8)
            self.assertFalse((Path(temporary)/'session'/'result.json').exists())
        self.assertEqual(self.empty_session.turns,7)
        self.assertTrue(self.empty_session.closed)
        self.assertTrue(self.empty_controller.closed)

    def test_many_no_progress_turns_preserve_session_guards_and_http_errors(self):
        import tempfile
        from regen.infrastructure.codex_workspace_session import (
            ProviderHTTPError,SessionProtocolError,SessionStalled)
        errors=(SessionStalled('no information deadline'),
                SessionProtocolError('mismatched turn'),
                ProviderHTTPError({'kind':'http_status','http_status':503}))
        for error in errors:
            def fail_after_six(turn):
                if turn==6:raise error
            with self.subTest(error=type(error).__name__),tempfile.TemporaryDirectory() as temporary:
                with self.assertRaises(type(error)) as caught:
                    self._empty_turn_scenario(temporary,progress_field=True,on_turn=fail_after_six,no_progress_turn_limit=8)
                self.assertIs(caught.exception,error)
                self.assertEqual(self.empty_session.turns,6)
                self.assertTrue(self.empty_session.closed)
                self.assertTrue(self.empty_controller.closed)

    def test_many_no_progress_turns_cannot_bypass_effective_budget(self):
        import tempfile
        from regen.execution.check_tasks import CheckCancelled
        from regen.infrastructure.effective_time_budget import EffectiveTimeBudget,LIMIT_REASON
        from regen.infrastructure.experiment_ledger import Ledger
        now=[0.]
        with tempfile.TemporaryDirectory() as temporary:
            budget=EffectiveTimeBudget(Ledger(Path(temporary)/'budget','fixture'),
                seconds=6,monotonic=lambda:now[0])
            budget.register({'Target':['goal']});budget.select('Target')
            def charge_turn(turn):
                now[0]=float(turn)
                budget.check()
            try:
                with self.assertRaises(CheckCancelled):
                    self._empty_turn_scenario(temporary,progress_field=True,
                        cancel_event=budget.event,on_turn=charge_turn,no_progress_turn_limit=8)
                self.assertEqual(self.empty_session.turns,6)
                self.assertTrue(self.empty_session.closed)
                self.assertTrue(self.empty_controller.closed)
                self.assertTrue(budget.event.is_set())
                self.assertEqual(budget.reason,LIMIT_REASON)
                self.assertEqual(budget.snapshot()['effective_seconds'],6)
                self.assertFalse((Path(temporary)/'session'/'result.json').exists())
            finally:budget.close()

    def test_completed_theory_refills_slot_without_waiting_for_slow_peer(self):
        import tempfile
        import hashlib
        from regen.planning.parallel_file_planner import run_parallel_workspace
        from regen.execution.proof_session_runner import acquire_run_mutation_lock, release_run_mutation_lock
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);binary=root/'codex';binary.write_text('fixture')
            states={name:'pending' for name in ('A','B','C')}
            mutex=threading.Lock();b_started=threading.Event();c_started=threading.Event()
            class Overview:
                selected=['A','B','C'];run={'run_id':'test','execution_input_kind':'refine-dataset',
                    'checker_context_root':str(root),'checker_context_id':'fixture'}
                options={'model':'test','call_timeout':10,'check_timeout':10,'candidate_timeout':10}
                def __init__(self,*args,**kwargs):self.root=root
                def list_workspace(self):
                    with mutex:
                        return {'theories':[{'theory':name,'status':state,'selected':True,'contracts':[name],
                            'ready_for_proof_action':state=='pending'} for name,state in states.items()]}
            def session(root,theory,**kwargs):
                if theory=='A':self.assertTrue(b_started.wait(3))
                if theory=='B':b_started.set();self.assertTrue(c_started.wait(3))
                if theory=='C':c_started.set()
                with mutex:states[theory]='sealed'
                return {'theory':theory,'status':'sealed'}
            settings={'codex_bin':str(binary),'codex_runtime_root':str(root/'codex-runtime'),
                      'codex_sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}
            with patch('regen.planning.parallel_file_planner.WorkspaceController',Overview),patch(
                    'regen.planning.parallel_file_planner.run_theory_session',side_effect=session),patch(
                    'regen.infrastructure.audited_llm.model_settings',return_value=settings),patch(
                    'regen.preparation.checker_context.load_context',return_value={'checker_context_id':'fixture'}):
                result=run_parallel_workspace(root,allow_external_call=True,budget_mode="effective-time",integrate=False,
                                              max_parallel_theories=2,resident_limit=2)
            self.assertTrue(c_started.is_set())
            self.assertEqual(result['status'],'selected_theories_sealed')
            self.assertFalse(result['repository_verified'])
            token=acquire_run_mutation_lock(root,operation='after-test');release_run_mutation_lock(token)

class TheoryOutcomeSchedulingTests(unittest.TestCase):
    def _schedule(self, *, retained_status=None, retry_unresolved=False):
        import tempfile
        import hashlib
        from regen.planning.parallel_file_planner import run_parallel_workspace
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);binary=root/'codex';binary.write_text('fixture')
            states={name:'pending' for name in ('A','B','C','D')}
            dependencies={'A':[], 'B':[], 'C':['B'], 'D':['A']}
            started=[]
            class Overview:
                selected=list(states)
                run={'run_id':'test','execution_input_kind':'refine-dataset',
                     'checker_context_root':str(root),'checker_context_id':'fixture'}
                options={'model':'test','call_timeout':10,'check_timeout':10,'candidate_timeout':10}
                def __init__(self,*args,**kwargs):self.root=root
                def list_workspace(self):
                    return {'theories':[{'theory':name,'status':state,'selected':True,
                        'contracts':[name], 'required_imports':dependencies[name],
                        'unsealed_providers':[p for p in dependencies[name] if states[p]!='sealed'],
                        'ready_for_proof_action':state=='pending' and all(states[p]=='sealed' for p in dependencies[name])}
                        for name,state in states.items()]}
            def session(root,theory,**kwargs):
                started.append(theory)
                if theory=='A':
                    return {'theory':theory,'status':'unknown','theory_outcome':{
                        'status':'unknown','reason':'proof search exhausted','receipt':'synthetic'}}
                states[theory]='sealed'
                return {'theory':theory,'status':'sealed'}
            settings={'codex_bin':str(binary),'codex_runtime_root':str(root/'codex-runtime'),
                      'codex_sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}
            with patch('regen.planning.parallel_file_planner.WorkspaceController',Overview),patch(
                    'regen.planning.parallel_file_planner.run_theory_session',side_effect=session),patch(
                    'regen.infrastructure.audited_llm.model_settings',return_value=settings),patch(
                    'regen.preparation.checker_context.load_context',return_value={'checker_context_id':'fixture'}),patch(
                    'regen.planning.theory_outcomes.current_outcome',side_effect=lambda controller,theory:
                        {'status':retained_status,'receipt':'prior-authority'} if theory=='A' and retained_status else None):
                result=run_parallel_workspace(root,allow_external_call=True,budget_mode="effective-time",integrate=False,max_parallel_theories=1,
                    execution_policy={'retry_unresolved':retry_unresolved})
            return started,states,result

    def test_stopped_root_releases_serial_slot_to_ready_deeper_theory(self):
        started,states,result=self._schedule()
        self.assertEqual(started,['A','B','C'])
        self.assertEqual(result['failures']['A']['reason'],'theory_unknown')
        self.assertEqual(result['blocked']['D']['unsealed_providers'],['A'])
        self.assertEqual(result['status'],'partial')
        self.assertFalse(result['repository_verified'])
        self.assertEqual(states['A'],'pending')

    def test_recovered_outcome_is_not_automatically_retried_or_treated_as_sealed(self):
        for status in ['unknown','refuted']:
            with self.subTest(status=status):
                started,states,result=self._schedule(retained_status=status)
                self.assertEqual(started,['B','C'])
                self.assertTrue(result['failures']['A']['retained_from_prior_invocation'])
                self.assertEqual(result['failures']['A']['status'],status)
                self.assertEqual(result['blocked']['D']['unsealed_providers'],['A'])
                self.assertFalse(result['repository_verified'])

    def test_explicit_retry_policy_only_revisits_unknown_outcomes(self):
        started,_,result=self._schedule(retained_status='unknown',retry_unresolved=True)
        self.assertEqual(started,['A','B','C'])
        started,_,result=self._schedule(retained_status='refuted',retry_unresolved=True)
        self.assertEqual(started,['B','C'])
        self.assertEqual(result['failures']['A']['status'],'refuted')

    def test_only_new_native_candidate_receipts_count_as_proof_work(self):
        from types import SimpleNamespace
        controller=TheoryController.__new__(TheoryController)
        controller._proof_work_seen=set();controller._proof_work_version=0
        full={'result':{'check':{'verdict':'red'}},'binding':{'state_sha256':'fixed'}}
        controller.executor=SimpleNamespace(get=lambda key:full)
        row={'status':'completed','task_id':'one'}
        arguments={'kind':'proof_batch','proposal':{'action':'check_theory','source_sha256':'first'}}
        controller._record_proof_work(row,arguments)
        self.assertEqual(controller._proof_work_version,1)
        controller._record_proof_work(row,{**arguments,'proposal':{**arguments['proposal'],'reason':'new explanation'}})
        self.assertEqual(controller._proof_work_version,1)
        controller._record_proof_work(row,{'kind':'observe','proposal':{'action':'read_proof_state'}})
        self.assertEqual(controller._proof_work_version,1)
        controller._record_proof_work(row,{**arguments,'proposal':{**arguments['proposal'],'source_sha256':'second'}})
        self.assertEqual(controller._proof_work_version,2)
        full['result']={'feedback':{'status':'rejected_request'}}
        controller._record_proof_work(row,{**arguments,'proposal':{**arguments['proposal'],'source_sha256':'third'}})
        self.assertEqual(controller._proof_work_version,2)
        for scope in ('native_preparation','native_transport','trusted_audit_target'):
            full['result']={'feedback':{'check':{'verdict':'red','failure_scope':scope}}}
            controller._record_proof_work(row,{**arguments,'proposal':{**arguments['proposal'],'source_sha256':scope}})
            self.assertEqual(controller._proof_work_version,2)
        full['result']={'framework_blocked':True,'check':{'verdict':'green'}}
        controller._record_proof_work(row,{**arguments,'proposal':{**arguments['proposal'],'source_sha256':'blocked'}})
        self.assertEqual(controller._proof_work_version,2)
        full['result']={'feedback':{'cached_failure':True,'check':{'verdict':'red'}}}
        controller._record_proof_work(row,{**arguments,'proposal':{**arguments['proposal'],'source_sha256':'cached-new-source'}})
        self.assertEqual(controller._proof_work_version,2)
        controller._proof_work_seen.clear()  # A fresh worker must not count old cached work either.
        controller._record_proof_work(row,arguments)
        self.assertEqual(controller._proof_work_version,2)

    def test_execution_policy_binds_finite_idle_limit(self):
        from regen.planning.parallel_file_planner import _execution_policy
        self.assertEqual(_execution_policy(None,['A'])['no_progress_turn_limit'],3)
        self.assertEqual(_execution_policy({'no_progress_turn_limit':7},['A'])['no_progress_turn_limit'],7)
        for invalid in [0,True,None,101,1.5]:
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                _execution_policy({'no_progress_turn_limit':invalid},['A'])
