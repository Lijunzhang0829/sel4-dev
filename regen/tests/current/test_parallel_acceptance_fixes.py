"""Regression cases from the independent parallel-pipeline acceptance review.

Native process results are mocked; real journals, files and task dispatch are used.
"""
from copy import deepcopy
from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import Mock, patch

from regen.execution.check_executor import CURRENT_RESOURCES, NativeResources
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.proof_commits import commit_paths, durable
from regen.execution.repository_execution import journal
from regen.execution.shared_proof_workspace import SharedProofWorkspace
from regen.tests.current import test_parallel_file_planner as fixtures
from regen.tests.current.test_shared_proof_workspace import setup_run


class AcceptanceFixes(unittest.TestCase):
    def integration_fixture(self):
        case=fixtures.IntegrationGateTests();case.setUp();self.addCleanup(case.doCleanups)
        case.sealed_fixture()
        return case

    def test_reader_accepts_old_complete_prefix_during_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);run,packages,_,source=setup_run(root)
            workspace=SharedProofWorkspace(root)
            def append(directory):
                paths,head=commit_paths(directory)
                if Path(directory).parent.name=='Source':
                    state=deepcopy(source);state['reason']='concurrent writer'
                    journal(root,run,packages[0]).append('planner-feedback',state,{})
                return paths,head
            with patch('regen.execution.shared_proof_workspace.commit_paths',side_effect=append):
                result=workspace.index(theory='Source')
            self.assertEqual(len(result['records']),2)
            self.assertEqual(len(workspace.index(theory='Source')['records']),2)

    def test_operator_managed_environment_does_not_rescan_toolchain(self):
        from regen.execution.repository_integration import _integrate_repository,current_verification
        case=self.integration_fixture()
        with patch('regen.infrastructure.path_identity.source_identity',side_effect=AssertionError('no runtime environment hashing')):
            with patch.object(IsabelleChecker,'build_generated_session',return_value={'verdict':'green','returncode':0}):
                self.assertTrue(_integrate_repository(case.root)['repository_verified'])
            self.assertTrue(current_verification(case.root))

    def test_legacy_input_refused_before_starting_agents(self):
        from regen.planning.parallel_file_planner import run_parallel_workspace
        with tempfile.TemporaryDirectory() as tmp:
            overview=Mock();overview.run={'execution_input_kind':'legacy-environment'};overview.selected=['A','B']
            with patch('regen.planning.parallel_file_planner.WorkspaceController',return_value=overview),patch(
                    'regen.planning.parallel_file_planner.run_theory_session') as agent:
                with self.assertRaisesRegex(ValueError,'isolated refine-dataset'):
                    run_parallel_workspace(tmp,allow_external_call=True)
            agent.assert_not_called()

    def test_cold_check_and_build_close_hot_resident_before_launch(self):
        for action in ('check','build'):
            with self.subTest(action=action),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                checker=IsabelleChecker(root,root/'check',heaps_dir=root,l4v_dir=root,semantic_workspace=root)
                hot=Mock();checker._batch_worker=hot
                pool=NativeResources(1,1,1);marker=CURRENT_RESOURCES.set(pool)
                def launch(command,**kwargs):
                    hot.close.assert_called_once()
                    self.assertIsNone(checker._batch_worker)
                    self.assertEqual(len(pool.residents),1)
                    return CompletedProcess(command,0,'','')
                try:
                    with patch('regen.execution.isabelle_node_checker.native_run',side_effect=launch):
                        if action=='check':checker._check('theory X imports Main begin end','X')
                        else:checker.build_generated_session(['X'])
                finally:
                    pool.close();CURRENT_RESOURCES.reset(marker)

    def test_query_read_and_proof_workspace_share_durable_observations(self):
        case=fixtures.ParallelControllerTests();case.setUp();self.addCleanup(case.doCleanups)
        ctl=case.controller;checker=ctl._runtime()[4]
        checker.generated_dir=case.root/'generated';checker.generated_dir.mkdir(exist_ok=True)
        for key,value in dict(run_dir=case.root,image='test',architecture='ARM',timeout=30,
                previous_theories=[],heaps_dir=case.root/'heaps',l4v_dir=case.root/'l4v',
                semantic_workspace=case.root/'semantic',base_session='Pure',base_import='Pure',
                base_catalog=[],isabelle_dir=case.root/'isabelle',import_all_previous=True).items():
            setattr(checker,key,value)
        clone=Mock();clone._command=lambda *a,**k:[]
        fact={'record_id':'fact-1','canonical_name':'Nat.add_commute','exact_statement':'x + y = y + x','context':[]}
        def call(proposal,number):
            return ctl.handle('proof_action',{'theory':'Target','proposal':proposal},
                              {**case.metadata,'call_id':str(number)})
        with patch('regen.execution.isabelle_node_checker.IsabelleChecker',return_value=clone),patch(
                'regen.planning.batch_file_planner.batch_query',return_value={'kind':'fact_selection','records':[fact],'queries':[]}):
            query=call({'action':'query','request':{'premise_queries':[{'query':'Nat.add_commute','mode':'exact'}]}},1)
            self.assertIn('task_id',query)
            self.assertEqual(call({'action':'read_facts','record_ids':['fact-1']},2)['observations'][0]['result']['records'],[fact])
            query_id=query['observations'][0]['result']['query_identity']
            self.assertEqual(call({'action':'read_queries','query_ids':[query_id]},3)['observations'][0]['result']['queries'][0]['query_id'],query_id)
        result=call({'action':'submit_batch','proofs':{'target_contract':'by simp'},
                     'workspace':{'record_ids':['fact-1']}},4)
        self.assertEqual(result['feedback']['status'],'accepted')
        _,state=ctl._state('Target')
        self.assertEqual(state['batch_workspace']['record_ids'],['fact-1'])
        self.assertEqual(state['batch_requests'],1)  # Reads never charge mathematical decisions.
        ctl.close()
        from regen.planning.parallel_file_planner import TheoryController
        resumed=TheoryController(case.root,'Target',resources=case.resources,executor=case.executor)
        self.addCleanup(resumed.close)
        self.assertEqual(resumed._state('Target')[1]['batch_facts'],[fact])
