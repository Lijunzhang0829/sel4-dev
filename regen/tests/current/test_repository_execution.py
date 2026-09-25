from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from regen.tests.current import test_repository_planner as fixtures
from regen.execution import repository_execution as execution
from regen.execution import graph_revision_agent as gra
from regen.execution.proof_commits import durable
from regen.execution.fact_context import manifest
from regen.tests.current.test_proof_commits import fixture
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.planning.file_planner import planning_environment,planner_prompt

class RepositoryExecutionTest(unittest.TestCase):
    def test_actual_bundle_to_runner_free_grouping_and_auxiliary_only(self):
        f=fixtures.RepositoryPlannerTest();f.setUp();self.addCleanup(f.doCleanups)
        f.call([f.proposal],allow=True)
        run_dir=f.root/'proof-run'
        with patch('regen.execution.repository_execution.read',wraps=execution.read) as reader:
            # The fixture toolchain identity already has a real frozen hash.
            run=execution.init_run(run_dir,f.root,'r',f.output/'file-generation-list.json')
        progress=execution.progress(run_dir)
        self.assertEqual(len(progress['files']),3)
        self.assertEqual(progress['contracts'],2)
        first=execution.package_for(run,'Generated_Bridges.thy')
        self.assertEqual(first['contracts'],[])
        self.assertEqual(len(first['support_contracts']),2)
        text=planner_prompt(first,planning_environment(first))
        self.assertIn('small_bridge',text)
        self.assertIn('support_contracts_are_premises',text)
        path=run_dir/'packages/Generated_Bridges.thy.json';data=json.loads(path.read_text());data['contracts']=[{}];path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'drift'):execution.package_for(run,first['file_slot'])

    def test_shared_optional_context_and_insertion_does_not_change_prefix(self):
        p,s=fixture();g=execution.context_graph(s['graph'],['helper']);proofs={'helper':'by simp','contract':'by (rule helper[THEN conjunct1])'}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            checker=IsabelleChecker(root,root/'checks',heaps_dir=root,l4v_dir=root,semantic_workspace=root)
            before=checker._source(g,proofs,theory_name='Test',target='contract')
            self.assertIn('lemma helper',before)
            self.assertEqual([n['id'] for n in manifest(g,{'helper':'by simp'},'contract')['facts']],['helper'])
            self.assertFalse(manifest(g,{'helper':'by simp'},'contract')['facts'][0]['required'])
            modified=deepcopy(g);modified['nodes'].append({'id':'a','name':'a','role':'helper','statement':'lemma a: "False | True"','context':[],'dependencies':[],'consumers':[]})
            modified['execution_order'].insert(0,'a')
            self.assertEqual(checker._source(modified,proofs,theory_name='Test',target='contract'),before)

    def test_split_metadata_retains_parent(self):
        p,s=fixture()
        decision={'operation':'SPLIT','generated_nodes':[{'id':'child','name':'child'}]}
        merged=gra.merge_generated_metadata(s['graph'],decision,'helper')
        self.assertEqual({n['id'] for n in merged['generated_nodes']},{'helper','child'})

    def test_runner_retains_failed_auxiliary_with_failure_evidence(self):
        from regen.execution.proof_commits import freeze_evidence
        from regen.planning.proof_graph import build_graph
        f=fixtures.RepositoryPlannerTest();f.setUp();self.addCleanup(f.doCleanups);f.call([f.proposal],allow=True)
        root=f.root/'execution';run=execution.init_run(root,f.root,'r',f.output/'file-generation-list.json')
        p=execution.package_for(run,'Generated_Bridges.thy')
        _,initial=fixture(False)
        graph=build_graph(p,{'generated_nodes':initial['graph']['nodes']},run_id='r',revision=1)
        class Checker(IsabelleChecker):
            def _check(self,source_text,label,**kwargs):
                source=self.work_dir/(label+'.thy');source.write_text(source_text)
                log=self.work_dir/(label+'.log');log.write_text('checked by test stub')
                return {'verdict':'green','returncode':0,'source':str(source),'log':str(log),'diagnostic':''}
        c=Checker(f.root,root/'checks',heaps_dir=root,l4v_dir=f.fixture.l4v,semantic_workspace=root)
        c._persistent_disabled=True  # Mocked cold checker fixture.
        def checker(*args,**kwargs):return c
        def revise(package, graph, *args, **kwargs):
            from regen.execution.premise_recall import recall_premises
            # Exercise the catalog passed across the repository Runner/GRA boundary.
            database = kwargs.get('premise_database')
            self.assertEqual(database, Path(run['premise_database']))
            page = recall_premises(database, 'True', scope='upstream')
            self.assertIsInstance(page['records'], list)
            return graph, {'decision':'ABANDON_GENERATION','reason':'exhausted'}
        with patch.object(execution,'checker_for',side_effect=checker), \
             patch('regen.execution.proof_session_runner.frozen_role_model',return_value='model'), \
             patch('regen.planning.file_planner.plan_file',return_value=graph), \
             patch('regen.execution.lemma_prover.prove_node',return_value={'status':'red','node_id':'helper','residual':'failed','attempts':[],'sealable_generation':True}), \
             patch('regen.execution.graph_revision_agent.revise_graph',side_effect=revise):
            result=execution.run_file(root,'Generated_Bridges',provider='codex',planner_model='model',revision_model='model',prover_model='model',lemma_backend='whole-proof',timeout=5,max_attempts=1,max_node_rounds=1,max_node_revisions=1)
        self.assertEqual(result['status'],'failed')
        state=execution.journal(root,run,p).replay()
        self.assertEqual(state['rounds'],{'helper':1})
        self.assertEqual([node['id'] for node in state['graph']['nodes']],['helper'])
        self.assertTrue(state['failure_detail']['failed_helper_retained'])
        self.assertEqual(json.loads((root/'generated-premises.json').read_text())['records'],[])
        self.assertEqual(execution.progress(root)['proved_contracts'],0)
        (root/'session-progress-ledger.json').unlink()
        (root/'theories/Generated_Bridges/progress.json').unlink()
        self.assertEqual(execution.progress(root)['files'][0]['status'],'failed')

    def test_green_and_seal_commits_restore_publication(self):
        from regen.planning.proof_graph import build_graph
        f=fixtures.RepositoryPlannerTest();f.setUp();self.addCleanup(f.doCleanups);f.call([f.proposal],allow=True)
        root=f.root/'execution';run=execution.init_run(root,f.root,'r',f.output/'file-generation-list.json')
        p=execution.package_for(run,'Generated_Read.thy')
        graph=build_graph(p,{'generated_nodes':[]},run_id='r',revision=1)
        class Checker(IsabelleChecker):
            def _check(self,source_text,label,**kwargs):
                source=self.work_dir/(label+'.thy');source.write_text(source_text)
                log=self.work_dir/(label+'.log');log.write_text('test checker')
                return {'verdict':'green','returncode':0,'source':str(source),'log':str(log),'diagnostic':''}
            def build_generated_session(self,*args):return {'verdict':'green'}
        c=Checker(f.root,root/'checks',heaps_dir=root,l4v_dir=f.fixture.l4v,semantic_workspace=root)
        c._persistent_disabled=True  # Mocked cold checker fixture.
        def audit(**kwargs):
            c.last_complete_audit={'verdict':'green','complete_theory_audit':{'proof_dependencies':{}}}
            return {'verdict':'pass'}
        with patch.object(execution,'checker_for',return_value=c), \
             patch('regen.execution.proof_session_runner.frozen_role_model',return_value='model'), \
             patch('regen.planning.file_planner.plan_file',return_value=graph), \
             patch('regen.execution.lemma_prover.prove_node',return_value={'status':'green','proof_body':'by simp','sealable_generation':True}), \
             patch('regen.analysis.retained_semantic_impact.audit_theory',side_effect=audit):
            result=execution.run_file(root,'Generated_Read',provider='codex',planner_model='model',revision_model='model',prover_model='model',lemma_backend='whole-proof',timeout=5,max_attempts=1,max_node_rounds=1,max_node_revisions=1)
        self.assertEqual(result['status'],'sealed')
        d=root/'theories/Generated_Read'
        (d/'acceptance/HEAD.json').unlink();(d/'active-graph.json').unlink()
        (root/'generated-premises.json').write_text('{"records":[{"record_id":"forged"}]}')
        (root/'generated/Regen_Generated_Read.thy').unlink()
        state=execution.progress(root)
        self.assertEqual(state['proved_contracts'],1)
        records=json.loads((root/'generated-premises.json').read_text())['records']
        self.assertEqual(len(records),1);self.assertNotEqual(records[0]['record_id'],'forged')
        self.assertFalse((d/'acceptance/HEAD.json').exists())  # shared progress must not repair another writer's HEAD
        self.assertTrue((root/'generated/Regen_Generated_Read.thy').exists())

    def test_gra_split_candidate_preserves_parent_and_is_not_committed(self):
        from regen.infrastructure.llm_transcript import artifact_paths
        p,s=fixture();graph=s['graph']
        decision={'decision':'REVISE_GRAPH','operation':'SPLIT','reason':'decompose proof',
            'generated_nodes':[{'id':'child','name':'child','role':'helper','statement':'lemma child: "True | True"','context':[],'dependencies':[],'strategy':'simp'}]}
        def call(provider,prompt,prefix,**kwargs):
            for name,path in artifact_paths(prefix).items():
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(json.dumps({'sealable_generation':True}) if name=='meta' else '{}')
            return json.dumps(decision)
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(gra,'call_llm',side_effect=call), \
             patch.object(gra,'validate_decision'), \
             patch.object(gra,'write_llm_module_log'), \
             patch.object(gra,'attach_module_outputs'):
            root=Path(temp)/'theories/Demo'
            revised,_=gra.revise_graph(p,graph,{'status':'red','node_id':'helper','residual':'split','attempts':[]},set(),output_dir=root)
            nodes={n['id']:n for n in revised['nodes']}
            self.assertEqual(nodes['helper']['statement'],next(n for n in graph['nodes'] if n['id']=='helper')['statement'])
            self.assertIn('child',nodes['helper']['dependencies'])
            transaction=json.loads((root/'graph-revisions/decision-001/transaction.json').read_text())
            self.assertFalse(transaction['applied']);self.assertTrue(transaction['certification_pending'])

    def test_repository_checker_imports_every_visible_sealed_theory(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            c=IsabelleChecker(root,root/'checks',heaps_dir=root,l4v_dir=root,semantic_workspace=root,
                previous_theories=['Regen_A','Regen_B'],import_all_previous=True)
            self.assertEqual(c.imports(),['Generated_Refine.Regen_A','Generated_Refine.Regen_B'])
