"""Synthetic controller/scheduler regressions; no generated/native proof credit."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from regen.execution.checker_policy import exports_available
from regen.planning.declaration_index import initial_index, accepted_declarations
from regen.planning.parallel_file_planner import TheoryController, _stage_eligible, run_theory_session
from regen.planning.theory_document import DocumentStore, render
from regen.planning.theory_outcomes import CONTRACT_POLICY, current_dispositions, record_unresolved


def fixture(root):
    package = {'theory':'Target', 'file_slot':'Target.thy', 'contracts':[
        {'contract_id':key, 'name':key, 'exact_statement':f'lemma {key}: "{prop}"',
         'contract_owned_context':[]} for key,prop in [('a','True'), ('b','False'), ('c','True')]]}
    state = {'status':'generating', 'graph':accepted_declarations(
        initial_index(package, run_id='contract-controller'), ['a']),
        'proofs':{'a':'by simp'}, 'accepted_order':['a'], 'rounds':{'b':2},
        'batch_requests':7, 'seal_id':None}
    run = {'run_id':'contract-controller', 'implementation':{},
           'contract_outcome_policy':CONTRACT_POLICY}
    return package, state, run


class ContractToolGuardsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package, self.state, run = fixture(self.root)
        self.c = TheoryController.__new__(TheoryController)
        self.c.root=self.root; self.c.run=run; self.c.theory='Target'
        self.c._state=lambda _: (self.package, self.state)
        self.c._tool_lock=threading.RLock(); self.c._check_session_active=lambda:None
        self.c.document_mode=False; self.c.stop_at_focus_contract=False
        self.c._handle=Mock(return_value={'executed':True})
        self.store=DocumentStore(self.root/'document', self.package, self.state, ['Main'])
        self.c._document=lambda **kw:(self.store, self.state)
        record_unresolved(self.c, reason='Synthetic fixed-contract disposition',
                          origin='model', entry_ids=['b'])

    def test_disposed_proof_writes_and_selected_checks_are_blocked_but_c_continues(self):
        for proposal in [
            {'action':'edit_proof','entry_id':'b','proof_body':'by simp'},
            {'action':'submit_batch','proofs':{'b':'by simp'}},
            {'action':'check_theory','entry_ids':['b','c']},
            {'action':'check_sketch','target_entry_ids':['b']},
        ]:
            with self.subTest(proposal=proposal),self.assertRaisesRegex(ValueError, 'disposed'):
                self.c.handle('proof_action', {'theory':'Target','proposal':proposal}, {})
        self.c._handle.assert_not_called()
        result=self.c.handle('proof_action', {'theory':'Target','proposal':{
            'action':'edit_proof','entry_id':'c','proof_body':'by simp'}}, {})
        self.assertTrue(result['executed'])
        self.assertEqual(self.state['rounds'], {'b':2})

    def test_disposed_slot_can_be_cleared_to_oops_or_read(self):
        for proposal in [{'action':'edit_proof','entry_id':'b','proof_body':'oops'},
                         {'action':'read_entry','entry_id':'b'}]:
            self.assertTrue(self.c.handle('proof_action',
                {'theory':'Target','proposal':proposal}, {})['executed'])

    def test_full_document_cannot_add_disposed_proof_but_can_preserve_failed_draft(self):
        source=self.store.read()['source'].replace('oops', 'by fail', 1)
        self.store._write(source, {'origin':'explicit test failed draft'})
        # Updating C while the historical failed B body is unchanged is legal;
        # the batch checker separately projects B to oops before checking C.
        self.c._guard_disposed_write({'action':'edit_theory',
            'source':source.replace('oops', 'by simp', 1)})
        with self.assertRaisesRegex(ValueError, 'must stay oops'):
            self.c._guard_disposed_write({'action':'edit_theory',
                'source':source.replace('by fail', 'by blast')})
        self.c._guard_disposed_write({'action':'edit_theory',
            'source':source.replace('by fail', 'oops')})

    def test_partial_finish_keeps_accepted_proofs_and_requires_every_contract_accounted(self):
        self.c.document_mode=True
        self.c.submit=Mock(return_value={'task_id':'fixture', 'status':'completed',
                                        'result':{'status':'exported_partial'}})
        # Call the production router rather than this test's handle stub.
        proposal={'action':'finish','source_sha256':self.store.read()['source_sha256']}
        with self.assertRaisesRegex(ValueError, 'every fixed contract'):
            TheoryController._handle(self.c,'proof_action',{'theory':'Target','proposal':proposal},{})
        self.state['proofs']['c']='by simp';self.state['accepted_order'].append('c')
        self.store._write(render(self.package,self.state,['Main']),{'origin':'accepted-state test fixture'})
        proposal['source_sha256']=self.store.read()['source_sha256']
        result=TheoryController._handle(self.c,'proof_action',{'theory':'Target','proposal':proposal},{})
        self.assertEqual(result['status'],'exported_partial')
        source=self.store.read()['source'].replace('by simp','by blast',1)
        self.store._write(source,{'origin':'changed accepted proof test fixture'})
        proposal['source_sha256']=self.store.read()['source_sha256']
        with self.assertRaisesRegex(ValueError,'preserves every accepted proof'):
            TheoryController._handle(self.c,'proof_action',{'theory':'Target','proposal':proposal},{})


class ContractSessionTests(unittest.TestCase):
    def scenario(self, *, finish_after_first=False, retry_unresolved=False, pre_disposed=False,
                 document_mode=True):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);package,state,run=fixture(root); test=self
        class Controller:
            options={'model':'test','call_timeout':10,'check_timeout':10,'candidate_timeout':10}
            model='test';task_ids=set()
            def __init__(self,*args,**kwargs):
                self.root=root;self.run=run;self.theory='Target';self.state=state
                self.theory_outcome=None;self._proof_work_version=0;test.controller=self
            def _state(self,theory):return deepcopy(package),deepcopy(self.state)
            def handle(self,*args):return {}
            def target_contract_green(self):return False
            def close(self):self.closed=True
        if pre_disposed:
            prior=Controller()
            record_unresolved(prior,reason='Prior search stopped only B',origin='model',entry_ids=['b'])
        class Session:
            def __init__(self,**kwargs):self.turns=0;self.prompts=[];test.session=self
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def run(self,prompt,handler,**kwargs):
                self.turns+=1;self.prompts.append(prompt)
                test.assertLessEqual(self.turns,4,'contract-local idle handling failed to converge')
                if finish_after_first and (self.turns==2 or pre_disposed):
                    test.assertEqual(set(current_dispositions(handler.__self__)),{'b'})
                    handler.__self__.state['proofs']['c']='synthetic accepted proof'
                    handler.__self__.state['status']='exported_partial'
                return {'status':'completed'}
        with patch('regen.planning.parallel_file_planner.TheoryController',Controller),patch(
                'regen.infrastructure.codex_workspace_session.CodexWorkspaceSession',Session),patch(
                'regen.infrastructure.workspace_transcript.audit_workspace_session',return_value={'terminal':True}):
            result=run_theory_session(root,'Target',resources=SimpleNamespace(policy={}),executor=object(),
                settings={'codex_bin':'fixture','reasoning_effort':'low'},directory=root/'session',
                no_progress_turn_limit=1,retry_unresolved=retry_unresolved,document_mode=document_mode)
        return result

    def test_idle_b_does_not_stop_theory_and_c_gets_its_own_turn(self):
        result=self.scenario(finish_after_first=True)
        self.assertEqual(result['status'],'exported_partial')
        self.assertEqual(self.session.turns,2)
        self.assertEqual(result['contract_dispositions']['b']['status'],'unknown')
        self.assertEqual(self.controller.state['rounds'],{'b':2})
        self.assertEqual(self.controller.state['batch_requests'],7)
        self.assertIn('"c"',self.session.prompts[1])

    def test_all_idle_contracts_get_dispositions_then_unpublished_is_explicit(self):
        result=self.scenario()
        self.assertEqual(result['status'],'unpublished')
        self.assertEqual(result['session_stop_reason'],'positive_exports_not_published')
        self.assertEqual(set(result['contract_dispositions']),{'b','c'})
        self.assertEqual(self.session.turns,3)
        self.assertEqual(self.controller.state['proofs'],{'a':'by simp'})
        self.assertTrue(result['all_contracts_disposed_or_proved'])

    def test_recovered_disposition_does_not_short_circuit_remaining_contract(self):
        result=self.scenario(finish_after_first=True,pre_disposed=True)
        self.assertEqual(result['status'],'exported_partial')
        self.assertEqual(self.session.turns,1)

    def test_contract_retry_flag_is_rejected_before_model_start(self):
        with self.assertRaisesRegex(ValueError,'explicit contract retry event'):
            self.scenario(retry_unresolved=True)
        self.assertFalse(hasattr(self,'session'))
        self.assertTrue(self.controller.closed)

    def test_contract_policy_rejects_graph_interface_before_model_start(self):
        with self.assertRaisesRegex(ValueError,'document File Planner interface'):
            self.scenario(document_mode=False)
        self.assertFalse(hasattr(self,'session'))
        self.assertTrue(self.controller.closed)


class ContractStageTests(unittest.TestCase):
    def test_partial_export_releases_stage_but_active_worker_keeps_its_stage(self):
        rows=[{'theory':'A','status':'exported_partial','selected':True},
              {'theory':'B','status':'not_started','selected':True}]
        policy={'stages':[['A'],['B']]}
        self.assertEqual(_stage_eligible(rows,policy),{'B'})
        self.assertEqual(_stage_eligible(rows,policy,active=['A']),{'A'})

    def test_disposed_but_unpublished_stage_releases_independent_work(self):
        rows=[{'theory':'A','status':'generating','selected':True,
               'pending_contract_ids':[], 'contract_dispositions':{'a':{'status':'refuted'}}},
              {'theory':'B','status':'not_started','selected':True}]
        self.assertEqual(_stage_eligible(rows,{'stages':[['A'],['B']]}),{'A','B'})
        self.assertFalse(exports_available(rows[0]['status']))
        rows[0]['pending_contract_ids']=['remaining']
        self.assertEqual(_stage_eligible(rows,{'stages':[['A'],['B']]}),{'A'})

    def schedule(self, *, unpublished=False, interface='document', resume=False):
        from regen.planning.parallel_file_planner import run_parallel_workspace
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);states={'A':'pending','B':'pending'};started=[]
            if unpublished:states['C']='pending'
            deps={'A':[], 'B':[] if unpublished else ['A'], 'C':['A']}
            class Overview:
                selected=list(states)
                run={'run_id':'stage-test','execution_input_kind':'refine-dataset',
                     'checker_context_root':str(root),'checker_context_id':'fixture',
                     'contract_outcome_policy':CONTRACT_POLICY}
                options={'model':'test','call_timeout':10,'check_timeout':10,'candidate_timeout':10}
                def __init__(self,*args,**kwargs):self.root=root
                def list_workspace(self):
                    return {'theories':[{'theory':name,'status':state,'selected':True,'contracts':[name],
                        'required_imports':deps[name],
                        'unsealed_providers':[p for p in deps[name] if not exports_available(states[p])],
                        'pending_contract_ids':[] if name=='A' and state=='generating' else [name],
                        'contract_dispositions':{'a':{'status':'refuted'}} if name=='A' and state=='generating' else {},
                        'ready_for_proof_action':state in {'pending','generating'} and all(exports_available(states[p]) for p in deps[name])}
                        for name,state in states.items()]}
            def run_session(_root,theory,**kwargs):
                if unpublished and theory=='A' and 'A' not in started:
                    started.append(theory);states[theory]='generating'
                    return {'theory':theory,'status':'unpublished'}
                started.append(theory);states[theory]='exported_partial' if theory=='A' else 'sealed'
                return {'theory':theory,'status':states[theory]}
            with patch('regen.planning.parallel_file_planner.WorkspaceController',Overview),patch(
                    'regen.planning.parallel_file_planner.run_theory_session',side_effect=run_session),patch(
                    'regen.infrastructure.audited_llm.model_settings',return_value={}),patch(
                    'regen.planning.parallel_file_planner.runtime_verified_settings',side_effect=lambda s:s),patch(
                    'regen.preparation.checker_context.load_context',return_value={'checker_context_id':'fixture'}),patch(
                    'regen.planning.theory_outcomes.current_outcome',return_value=None),patch(
                    'regen.execution.repository_integration.integrate_repository') as integrate:
                result=run_parallel_workspace(root,allow_external_call=True,integrate=True,max_parallel_theories=1,
                    file_planner_interface=interface,
                    execution_policy={'stages':[['A'],list(states)[1:]]})
                if resume:
                    first = result
                    result=run_parallel_workspace(root,allow_external_call=True,integrate=True,max_parallel_theories=1,
                        file_planner_interface=interface,
                        execution_policy={'stages':[['A'],list(states)[1:]]},
                        resume_authorization='Explicit synthetic test resume of unchanged run')
                integrate.assert_not_called()
            if resume:
                return started,first,result
            return started,result

    def test_partial_provider_and_downstream_run_without_repository_success(self):
        started,result=self.schedule()
        self.assertEqual(started,['A','B'])
        self.assertEqual(result['status'],'selected_theories_exported')
        self.assertEqual(result['failures'],{})
        self.assertFalse(result['repository_verified'])

    def test_unpublished_provider_keeps_hard_consumer_blocked_but_independent_stage_runs(self):
        started,result=self.schedule(unpublished=True)
        self.assertEqual(started,['A','B'])
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['failures']['A']['reason'],'positive_exports_not_published')
        self.assertEqual(result['blocked']['C']['unsealed_providers'],['A'])
        self.assertFalse(result['repository_verified'])

    def test_resume_retries_unpublished_prior_stage_and_releases_hard_consumer(self):
        started,first,result=self.schedule(unpublished=True,resume=True)
        self.assertEqual(started,['A','B','A','C'])
        self.assertEqual(first['failures']['A']['reason'],'positive_exports_not_published')
        self.assertEqual(first['blocked']['C']['unsealed_providers'],['A'])
        self.assertEqual(result['status'],'selected_theories_exported')
        self.assertEqual(result['failures'],{})
        self.assertEqual(result['blocked'],{})
        self.assertFalse(result['repository_verified'])
        self.assertEqual(result['generation_budget']['seconds'],first['generation_budget']['seconds'])
        self.assertGreaterEqual(result['generation_budget']['effective_seconds'],
                                first['generation_budget']['effective_seconds'])

    def test_parallel_graph_interface_is_rejected_for_contract_policy(self):
        with self.assertRaisesRegex(ValueError,'document File Planner interface'):
            self.schedule(interface='graph')


if __name__=='__main__':
    unittest.main()
