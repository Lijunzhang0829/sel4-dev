import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,Mock
from regen.execution.lemma_prover import prove_node
from regen.execution.observation_context import working_context,restore_working_set
from regen.execution.gra_input import gra_view


def fact(key):
    return {'record_id':key,'canonical_name':'Base.'+key,'exact_statement':'lemma '+key+': True',
            'owner_theory':'Base','source_class':'frozen_parent','score':1}


class ObservationContextTest(unittest.TestCase):
    def run_prover(self,replies,prior=None,query_budget=2,verdict='green',max_attempts=1):
        prompts=[];queries=[]
        graph={'theory':'Demo','file_slot':'Demo.thy','nodes':[{'id':'c','name':'c','role':'contract','statement':'lemma c: True','context':[],'strategy':'direct','dependencies':[]}]}
        def call(provider,prompt,prefix,**kwargs):
            prompts.append(json.loads(prompt.rsplit('INPUT:\n',1)[1]))
            prefix.parent.mkdir(parents=True,exist_ok=True)
            prefix.with_suffix('.meta.json').write_text('{"sealable_generation":true}')
            return json.dumps(replies[len(prompts)-1])
        def recall(db,query,**kwargs):
            queries.append(query)
            return {'query':query,'scope':'upstream','records':[fact(query)],'next_offset':None}
        checker=Mock();checker.base_import='Base';checker.check_node.return_value={'verdict':verdict,'diagnostic':'goal: True' if verdict=='red' else ''}
        with tempfile.TemporaryDirectory() as tmp, patch('regen.execution.lemma_prover.call_llm',side_effect=call), patch('regen.execution.premise_recall.recall_premises',side_effect=recall), patch('regen.execution.lemma_prover.write_llm_module_log'), patch('regen.execution.lemma_prover.attach_module_outputs'):
            result=prove_node(graph,'c',{},checker=checker,premise_database=Path(tmp)/'unused',output_dir=Path(tmp)/'theories/Demo',max_attempts=max_attempts,max_observations=query_budget,previous_round_feedback=prior)
        return result,prompts,queries,checker

    def test_one_batch_then_proof_with_one_proof_slot(self):
        result,prompts,queries,checker=self.run_prover([
            {'observation_request':{'premise_queries':[{'query':'a'},{'query':'b'}]}},
            {'proof_body':'by simp','used_premise_ids':['a','b']}])
        self.assertEqual(result['status'],'green')
        self.assertEqual(checker.check_node.call_count,1)
        self.assertEqual(result['attempts'][0]['attempt'],1)
        self.assertEqual([p['interaction_state']['proof_attempts_remaining'] for p in prompts],[1,1])
        self.assertEqual([p['interaction_state']['queries_remaining'] for p in prompts],[2,1])
        self.assertTrue(prompts[-1]['interaction_state']['facts_frozen'])
        self.assertEqual([x['record_id'] for x in prompts[-1]['recalled_facts']],['a','b'])
        self.assertEqual(prompts[-1]['recalled_facts'][0]['exact_statement'],fact('a')['exact_statement'])

    def test_second_fact_request_cannot_change_frozen_round(self):
        result,prompts,queries,_=self.run_prover([
            {'observation_request':{'premise_query':'a'}},
            {'observation_request':{'premise_query':'b'}},
            {'proof_body':'by simp'}])
        self.assertEqual(result['status'],'green')
        self.assertEqual(queries,['a'])
        self.assertEqual(prompts[1]['recalled_facts'],prompts[2]['recalled_facts'])
        self.assertEqual(prompts[2]['recall_results'][-1]['status'],'rejected_frozen')

    def test_first_proof_locks_empty_selection(self):
        result,prompts,queries,checker=self.run_prover([
            {'proof_body':'by simp'},
            {'observation_request':{'premise_query':'a'}},
            {'proof_body':'by blast'}],max_attempts=2,verdict='red')
        self.assertEqual(queries,[])
        self.assertEqual(checker.check_node.call_count,2)
        self.assertTrue(prompts[1]['interaction_state']['facts_frozen'])
        self.assertNotIn('recalled_facts',prompts[2])
        self.assertEqual(result['observation_requests'][0]['status'],'rejected_frozen')

    def test_invalid_query_is_repaired_without_consuming_proof_slot(self):
        from regen.execution.premise_recall import recall_premises
        with patch('regen.execution.round_recall.select_round_facts',side_effect=[
            ValueError('premise scope must be all/upstream/sealed/local'),
            {'kind':'fact_selection','status':'frozen','records':[fact('a')],'queries':[]}]) as choose:
            result,prompts,queries,checker=self.run_prover([
                {'observation_request':{'premise_query':'a','scope':'typo'}},
                {'observation_request':{'premise_query':'a','scope':'upstream'}},
                {'proof_body':'by simp'}])
        self.assertEqual(result['status'],'green')
        self.assertEqual(checker.check_node.call_count,1)
        self.assertEqual(len(result['attempts']),1)
        self.assertFalse(prompts[1]['interaction_state']['facts_frozen'])
        self.assertEqual(prompts[1]['recall_results'][0]['status'],'rejected_request')
        self.assertEqual(prompts[2]['recalled_facts'][0]['record_id'],'a')
        self.assertEqual(choose.call_count,2)

    def test_query_exhaustion_is_not_empty_proof_and_gra_sees_reason(self):
        result,prompts,queries,checker=self.run_prover([
            {'observation_request':{'premise_query':'a'}},
            {'observation_request':{'premise_query':'b'}}],query_budget=1)
        self.assertEqual(result['failure_scope'],'interaction')
        self.assertEqual(result['failure_class'],'observation_budget_exhausted')
        self.assertEqual(result['attempts'],[])
        self.assertIsNone(result['last_model_error'])
        self.assertEqual(queries,['a']);checker.check_node.assert_not_called()
        view=gra_view({'current_graph':{'nodes':[]},'failed_node':result})
        self.assertEqual(view['failure_feedback']['failure_class'],'observation_budget_exhausted')
        self.assertEqual(view['prover_query_count'],2)
        self.assertNotIn('recalled_facts',view)
        self.assertNotIn('recall_results',view)

    def test_cross_round_fact_memory_resets_but_baseline_survives(self):
        result,_,_,_=self.run_prover([
            {'observation_request':{'premise_query':'a'}},
            {'proof_body':'by simp'}],verdict='red')
        from regen.execution.proof_session_runner import compact_round_feedback
        previous=compact_round_feedback({'statement':'lemma c: True','dependencies':[]},result,1)
        resumed,prompts,queries,_=self.run_prover([{'proof_body':'by simp'}],prior=previous)
        self.assertEqual(resumed['status'],'green')
        self.assertEqual(queries,[])
        self.assertNotIn('recalled_facts',prompts[0])
        self.assertNotIn('recall_results',prompts[0])
        self.assertEqual(prompts[0]['repair_baseline']['proof_body'],'by simp')
        self.assertEqual(prompts[0]['recent_attempts'],[])
        self.assertEqual(len(prompts[0]['previous_rounds']),1)

    def test_revoked_local_fact_cannot_reenter_through_saved_page(self):
        stale={**fact('old'),'source_class':'same_theory_green','context':[]}
        records,pages,rejected=restore_working_set({'observation_facts':[stale],
            'observation_pages':[{'records':[stale]}]},[],None)
        self.assertEqual(records,[]);self.assertEqual(rejected,['old'])
        self.assertNotIn('recalled_facts',working_context(pages,records))

    def test_all_graph_and_diagnostic_observations_persist(self):
        pages=[{'nodes':[{'id':'a','statement':'A'}]},
               {'diagnostic_id':'d','offset':0,'text':'first page'},
               {'nodes':[{'id':'b','statement':'B'}]},
               {'diagnostic_id':'d','offset':1200,'text':'second page'}]
        view=working_context(pages)
        self.assertEqual([x['id'] for x in view['observed_nodes']],['a','b'])
        self.assertEqual([x['text'] for x in view['recall_results'] if 'text' in x],['first page','second page'])

if __name__=='__main__': unittest.main()
