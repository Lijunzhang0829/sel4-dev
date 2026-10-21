from copy import deepcopy
import unittest
from regen.execution.retry_policy import extra_round_available, required_context, stagnant

class RetryPolicyTest(unittest.TestCase):
    def state(self):
        return {'graph':{'nodes':[
            {'id':'c','statement':'lemma c: "True"','context':[], 'dependencies':['h']},
            {'id':'h','statement':'lemma h: "True"','context':[], 'dependencies':[]}]},
            'proofs':{},'rounds':{'c':5},'attempt_contexts':{'c':[]}}

    def test_only_certified_new_dependency_unlocks_one_round(self):
        s=self.state();self.assertFalse(extra_round_available(s,'c',5))
        s['proofs']['h']='by simp';self.assertTrue(extra_round_available(s,'c',5))
        s['attempt_contexts']['c']=required_context(s,'c');s['rounds']['c']=6
        self.assertFalse(extra_round_available(s,'c',5))
        s['rounds']['c']=5;self.assertFalse(extra_round_available(s,'c',5))

    def test_renaming_dependency_or_proof_does_not_reset_credit(self):
        s=self.state();s['proofs']['h']='by simp';s['attempt_contexts']['c']=required_context(s,'c')
        s['graph']['nodes'][1]['statement']='lemma renamed: "True"'
        s['proofs']['h']='by blast'
        self.assertFalse(extra_round_available(s,'c',5))
        s['graph']['nodes'][0]['dependencies']=[]
        self.assertFalse(extra_round_available(s,'c',5))

    def test_old_run_has_no_implicit_credit_and_zero_reserve_disables_it(self):
        s=self.state();s['proofs']['h']='by simp'
        self.assertFalse(extra_round_available(s,'c',5,0))
        del s['attempt_contexts'];self.assertFalse(extra_round_available(s,'c',5))

    def test_stagnation_requires_same_goal_and_premises(self):
        a={'check':{'diagnostic':'*** Failed (line 10 of "/a.thy"):\n*** goal: x = y'},'retrieved_premises':['p']}
        b=deepcopy(a);b['check']['diagnostic']=b['check']['diagnostic'].replace('10','20')
        self.assertTrue(stagnant([a,b]))
        b['retrieved_premises']=['q'];self.assertFalse(stagnant([a,b]))
        b['retrieved_premises']=['p'];b['check']['diagnostic']+=' different'
        self.assertFalse(stagnant([a,b]))
        self.assertFalse(stagnant([{'check':{'diagnostic':'timeout'}}]*2))

class ProverHandoffTest(unittest.TestCase):
    def run_prover(self, limit):
        import json, tempfile
        from pathlib import Path
        from unittest.mock import patch
        from regen.execution.lemma_prover import prove_node
        graph={'theory':'Demo','file_slot':'Demo.thy','nodes':[{'id':'c','name':'c','role':'contract','context':[],'statement':'lemma c: "True"','strategy':'direct','dependencies':[]}]}
        class Checker:
            def check_node(self,*args):
                return {'verdict':'red','diagnostic':'*** Failed to finish proof:\n*** goal: True','returncode':1}
        def call(provider,prompt,prefix,**kwargs):
            prefix.parent.mkdir(parents=True,exist_ok=True)
            prefix.with_suffix('.meta.json').write_text(json.dumps({'sealable_generation':True}))
            return '{"proof_body":"by simp","used_premise_ids":[]}'
        with tempfile.TemporaryDirectory() as tmp, \
             patch('regen.execution.lemma_prover.call_llm',side_effect=call) as calls, \
             patch('regen.execution.lemma_prover.retrieve_proof_premises',return_value=[]), \
             patch('regen.execution.lemma_prover.write_llm_module_log'), \
             patch('regen.execution.lemma_prover.attach_module_outputs'), \
             patch('regen.execution.lemma_prover.plan_local_outline',return_value={'sealable_generation':True}) as salvage:
            result=prove_node(graph,'c',{},checker=Checker(),premise_database=Path(tmp)/'db',output_dir=Path(tmp)/'theories/Demo',max_attempts=limit)
            return result,calls.call_count,salvage.call_count

    def test_repeated_residual_ends_round_before_five_candidates(self):
        result,calls,salvage=self.run_prover(5)
        self.assertEqual((calls,salvage),(2,0));self.assertTrue(result['early_handoff'])
        self.assertTrue(result['sealable_generation'])

    def test_final_attempt_does_not_duplicate_gra_diagnosis(self):
        result,calls,salvage=self.run_prover(1)
        self.assertEqual((calls,salvage),(1,0));self.assertEqual(result['status'],'red')

class CheckerReuseTest(unittest.TestCase):
    def test_exact_green_reuse_is_one_shot_and_checks_evidence_bytes(self):
        import tempfile
        from pathlib import Path
        from regen.execution.isabelle_node_checker import IsabelleChecker
        from regen.tests.current.test_proof_commits import fixture
        from regen.execution.repository_execution import context_graph
        _,s=fixture();g=context_graph(s['graph'],[])
        class Checker(IsabelleChecker):
            count=0
            def _check(self,source_text,label,**kwargs):
                self.count+=1
                src=self.work_dir/(label+'.thy');src.write_text(source_text)
                log=self.work_dir/(label+'.log');log.write_text('checked')
                return {'verdict':'green','source':str(src),'log':str(log),'wall_ms':100,'returncode':0}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);c=Checker(root,root/'check',heaps_dir=root,l4v_dir=root,semantic_workspace=root)
            first=c.check_node(g,{'helper':'by simp'},'helper')
            self.assertTrue(c.check_node(g,{'helper':'by simp'},'helper')['reused_check'])
            self.assertEqual(c.count,1)
            c.check_node(g,{'helper':'by simp'},'helper');self.assertEqual(c.count,2)
            Path(first['log']).write_text('changed')
            c.check_node(g,{'helper':'by simp'},'helper');self.assertEqual(c.count,3)
            c.check_node(g,{'helper':'by blast'},'helper');self.assertEqual(c.count,4)

class ContextRetryIntegrationTest(unittest.TestCase):
    def test_helper_green_unlocks_exhausted_contract_and_replays(self):
        import json
        from unittest.mock import patch
        from regen.tests.current import test_repository_planner as fixtures
        from regen.execution import repository_execution as execution
        from regen.execution.isabelle_node_checker import IsabelleChecker
        from regen.planning.proof_graph import build_graph
        f=fixtures.RepositoryPlannerTest();f.setUp();self.addCleanup(f.doCleanups)
        f.call([f.proposal],allow=True)
        root=f.root/'execution';run=execution.init_run(root,f.root,'r',f.output/'file-generation-list.json')
        package=execution.package_for(run,'Generated_Read.thy')
        graph=build_graph(package,{'generated_nodes':[]},run_id='r',revision=1)
        key=graph['nodes'][0]['id'];calls=[]
        class Checker(IsabelleChecker):
            def _check(self,source_text,label,**kwargs):
                source=self.work_dir/(label+'.thy');source.write_text(source_text)
                log=self.work_dir/(label+'.log');log.write_text('test checker')
                return {'verdict':'green','returncode':0,'source':str(source),'log':str(log),'diagnostic':''}
            def build_generated_session(self,*args):return {'verdict':'green'}
            build_sealed_session = build_generated_session
        checker=Checker(f.root,root/'checks',heaps_dir=root,l4v_dir=f.fixture.l4v,semantic_workspace=root)
        checker._persistent_disabled=True  # This fixture mocks the cold checker, not Docker.
        def prove(graph,node,proofs,**kwargs):
            calls.append(node)
            if len(calls)==1:return {'status':'red','node_id':node,'attempts':[],'residual':'missing bridge','sealable_generation':True}
            return {'status':'green','proof_body':'by simp','sealable_generation':True}
        def revise(p,g,*args,**kwargs):
            self.assertEqual(kwargs['decision_context']['proof_retry_limit'],1)
            payload={'generated_nodes':[{'id':'bridge','name':'bridge','role':'helper','statement':'lemma bridge: "True"','context':[],'dependencies':[],'consumers':[key],'strategy':'simp'}], 'contract_dependencies':{key:['bridge']}}
            return build_graph(p,payload,run_id='r',revision=2),{'decision':'REVISE_GRAPH','operation':'ADD','reason':'missing bridge'}
        def audit(**kwargs):
            checker.last_complete_audit={'verdict':'green','complete_theory_audit':{'proof_dependencies':{}}}
            return {'verdict':'pass'}
        from regen.tests.current.seal_fixture import mock_bind
        self.enterContext(patch('regen.execution.seal_evidence.bind_seal',side_effect=mock_bind))
        with patch.object(execution,'checker_for',return_value=checker), \
             patch('regen.execution.proof_session_runner.frozen_role_model',return_value='model'), \
             patch('regen.planning.file_planner.plan_file',return_value=graph), \
             patch('regen.execution.lemma_prover.prove_node',side_effect=prove), \
             patch('regen.execution.graph_revision_agent.revise_graph',side_effect=revise), \
             patch('regen.analysis.retained_semantic_impact.audit_theory',side_effect=audit):
            result=execution.run_file(root,'Generated_Read',provider='codex',planner_model='model',revision_model='model',prover_model='model',lemma_backend='whole-proof',timeout=5,max_attempts=1,max_node_rounds=1,max_node_revisions=1)
        self.assertEqual(result['status'],'sealed');self.assertEqual(calls,[key,'bridge',key])
        state=execution.journal(root,run,package).replay()
        self.assertEqual(state['rounds'],{key:2,'bridge':1})
