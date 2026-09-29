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
        calls=[]
        def state(theory):
            package,value=original(theory)
            calls.append(1)
            if len(calls)>=4:
                value=deepcopy(value);value['graph']['revision']+=1
            return package,value
        with patch.object(self.controller,'_state',side_effect=state),patch.object(
                self.controller,'_runtime',return_value=None),patch(
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
            evidence={'impact':{'verdict':'pass'},'dependencies':{'verdict':'green'},
                      'complete':{'verdict':'green'},'build':{'verdict':'green'},
                      'source_text':source,'source_hash':digest(source)}
            state=deepcopy(candidate);state.update(status='sealed',seal_id='fixture-seal')
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
            def __init__(self,*args,**kwargs):pass
            def handle(self,name,args,metadata):
                if name=='list_workspace':return {'theories':[]}
                return {}
            def _state(self,theory):return {},self.state
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

    def test_two_empty_turns_are_a_continuation_error(self):
        import tempfile
        from regen.planning.parallel_file_planner import run_theory_session, TheoryContinuationError
        class Controller:
            options={'model':'test','call_timeout':10,'check_timeout':10,'candidate_timeout':10}
            run={'run_id':'test'};model='test';task_ids=set()
            state={'status':'generating','graph':{'nodes':[{'id':'goal'}]},'proofs':{}}
            def __init__(self,*args,**kwargs):pass
            def handle(self,name,args,metadata):
                return {'theories':[]} if name=='list_workspace' else {}
            def _state(self,theory):return {},self.state
            def close(self):pass
        class Session:
            tool_call_count=0
            def __init__(self,**kwargs):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def run(self,*args,**kwargs):return {'status':'completed'}
        with tempfile.TemporaryDirectory() as temporary,patch(
                'regen.planning.parallel_file_planner.TheoryController',Controller),patch(
                'regen.infrastructure.codex_workspace_session.CodexWorkspaceSession',Session):
            with self.assertRaisesRegex(TheoryContinuationError,'two consecutive turns'):
                run_theory_session(Path(temporary),'Target',
                    resources=type('Resources',(),{'policy':{}})(),executor=object(),
                    settings={'codex_bin':'codex','reasoning_effort':'medium'},
                    directory=Path(temporary)/'session')

    def test_read_only_calls_do_not_reset_idle_turns(self):
        import tempfile
        from regen.planning.parallel_file_planner import run_theory_session, TheoryContinuationError
        class Controller:
            options={'model':'test','call_timeout':10,'check_timeout':10,'candidate_timeout':10}
            run={'run_id':'test'};model='test';task_ids=set()
            state={'status':'generating','graph':{'nodes':[{'id':'goal'}]},'proofs':{}}
            def __init__(self,*args,**kwargs):pass
            def handle(self,*args):return {}
            def _state(self,theory):return {},self.state
            def close(self):pass
        class Session:
            tool_call_count=0
            def __init__(self,**kwargs):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def run(self,*args,**kwargs):
                self.tool_call_count+=1
                return {'status':'completed','progress_made':False}
        with tempfile.TemporaryDirectory() as temporary,patch(
                'regen.planning.parallel_file_planner.TheoryController',Controller),patch(
                'regen.infrastructure.codex_workspace_session.CodexWorkspaceSession',Session):
            with self.assertRaisesRegex(TheoryContinuationError,'two consecutive turns'):
                run_theory_session(Path(temporary),'Target',resources=type('R',(),{'policy':{}})(),
                    executor=object(),settings={'codex_bin':'codex','reasoning_effort':'medium'},
                    directory=Path(temporary)/'session')

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
                def __init__(self,*args,**kwargs):pass
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
            settings={'codex_bin':str(binary),'codex_sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}
            with patch('regen.planning.parallel_file_planner.WorkspaceController',Overview),patch(
                    'regen.planning.parallel_file_planner.run_theory_session',side_effect=session),patch(
                    'regen.infrastructure.audited_llm.model_settings',return_value=settings),patch(
                    'regen.preparation.checker_context.load_context',return_value={'checker_context_id':'fixture'}):
                result=run_parallel_workspace(root,allow_external_call=True,integrate=False)
            self.assertTrue(c_started.is_set())
            self.assertEqual(result['status'],'selected_theories_sealed')
            self.assertFalse(result['repository_verified'])
            token=acquire_run_mutation_lock(root,operation='after-test');release_run_mutation_lock(token)
