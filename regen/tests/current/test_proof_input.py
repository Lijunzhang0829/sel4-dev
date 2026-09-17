import copy
import json
import unittest
from regen.execution.proof_input import project, diagnostic_page, evidence_id, InputBudgetExceeded

class InputViewTest(unittest.TestCase):
    def test_huge_diagnostic_deduplicated_and_raw_unchanged(self):
        text = 'goal: '+ 'x = y; ' * 90000
        candidate = {'proof_body': 'by simp', 'check': {'diagnostic': text}}
        payload = {'node': {'statement': 'lemma c: P', 'context': ['context X']}, 'best_previous_attempt': candidate, 'latest_attempt': candidate}
        old = copy.deepcopy(payload)
        encoded = project(payload); view = json.loads(encoded)
        self.assertLessEqual(len(encoded), 10000)
        self.assertEqual(payload, old)
        self.assertEqual(view['node'], payload['node'])
        self.assertEqual(view['latest_attempt'], {'same_as': 'best_previous_attempt'})
        obs = view['diagnostic_observations'][evidence_id(text)]
        self.assertFalse(obs['complete'])
        page = diagnostic_page(payload, evidence_id(text), 100)
        self.assertEqual(page['text'], text[100:1300])
        with self.assertRaises(ValueError): diagnostic_page(payload, '/etc/passwd', 0)
        with self.assertRaises(ValueError): diagnostic_page(payload, evidence_id(text), -1)

    def test_fixed_statement_never_truncated(self):
        with self.assertRaises(InputBudgetExceeded):
            project({'node': {'statement': 'x'*130000}})

    def test_same_feedback_has_one_complete_copy(self):
        text = 'goal: '+ 'P ' * 1000
        view = json.loads(project({'residual': text, 'check': {'diagnostic': text}}))
        self.assertEqual(len(view['diagnostic_observations']), 1)
        self.assertEqual(view['diagnostic_observations'][evidence_id(text)]['text'], text)

    def test_observation_round_trip_without_proof_intervention(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from regen.execution.lemma_prover import prove_node
        prior = {'attempts': [{'proof_body': 'by simp', 'check': {'diagnostic': 'goal: P'}}]}
        graph = {'theory':'Demo','file_slot':'Demo.thy','nodes':[{'id':'c','name':'c','role':'contract','statement':'lemma c: P','context':[],'strategy':'direct','dependencies':[]}]}
        prompts=[]
        def call(provider,prompt,prefix,**kw):
            prompts.append(json.loads(prompt.split('INPUT:\n',1)[1]))
            prefix.parent.mkdir(parents=True,exist_ok=True)
            prefix.with_suffix('.meta.json').write_text(json.dumps({'sealable_generation':True}))
            return json.dumps({'observation_request':{'diagnostic_id':evidence_id('goal: P'),'offset':0}}) if len(prompts)==1 else '{"proof_body":null}'
        with tempfile.TemporaryDirectory() as tmp, patch('regen.execution.lemma_prover.call_llm',side_effect=call), patch('regen.execution.lemma_prover.retrieve_proof_premises',return_value=[]), patch('regen.execution.lemma_prover.write_llm_module_log'):
            result=prove_node(graph,'c',{},checker=object(),premise_database=Path(tmp)/'db',output_dir=Path(tmp)/'theories/Demo',previous_round_feedback=prior,max_attempts=2)
        self.assertEqual(prompts[1]['recall_results'][0]['text'],'goal: P')
        self.assertEqual(result['status'],'red')
        self.assertEqual(result['attempts'][0]['attempt'],1)


class RoleProjectionTest(unittest.TestCase):
    def test_prover_full_prompt_bound_and_raw_audit(self):
        import tempfile
        from pathlib import Path
        from regen.execution.lemma_prover import proof_prompt
        text = 'goal: ' + 'P x ' * 100000
        attempt = {'proof_body':'by simp','check':{'diagnostic':text,'source':'audit-only-path', 'command_timings':[{'large':'x'*1000}]*100}}
        facts = [{'record_id':str(i),'canonical_name':f'Base.f{i}', 'exact_statement':f'lemma f{i}: "'+('P x '*80)+'"'} for i in range(40)]
        prior = {'attempts':[attempt]*5,'checked_history':[attempt]*4, 'retrieved_legal_premises':facts,'residual':text}
        original = copy.deepcopy(prior)
        node = {'id':'c','role':'contract','statement':'lemma c: P','strategy':'direct','context':['context X']}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'input.raw.json'
            prompt = proof_prompt(node,[],facts,attempt,latest_attempt=attempt,previous_round_feedback=prior,input_audit_path=path)
            raw = json.loads(path.read_text())
            selection = json.loads(path.with_suffix('.selection.json').read_text())
        self.assertLessEqual(len(prompt),10000)
        self.assertEqual(selection['prompt_chars'],len(prompt))
        self.assertEqual(raw['previous_round_feedback'],original)
        self.assertEqual(prior,original)
        self.assertNotIn('audit-only-path',prompt)
        view = json.loads(prompt.rsplit('INPUT:\n',1)[1])
        self.assertEqual(view['target']['statement'],node['statement'])
        self.assertEqual(view['target']['context'],node['context'])
        self.assertNotIn('attempts',view.get('previous_round_feedback',{}))
        self.assertNotIn('retrieved_legal_premises',view)
        self.assertEqual(set(view),{'target','recent_attempts','attempt_summaries'})

    def test_requested_premise_shares_budget_and_is_not_repeated(self):
        from regen.execution.lemma_prover import proof_prompt
        facts = [{'record_id':str(i),'canonical_name':f'Base.f{i}','exact_statement':'P '*400} for i in range(20)]
        prompt = proof_prompt({'id':'c','role':'contract','statement':'lemma c: P','strategy':'direct'},[],facts,None,
            observations=[{'request':{'premise_query':'f19'},'retrieved_record_ids':['19']}])
        view = json.loads(prompt.rsplit('INPUT:\n',1)[1])
        self.assertNotIn('retrieved_legal_premises',view)
        self.assertEqual(view['recall_results'][0]['request']['premise_query'],'f19')

    def test_gra_scope_preserves_connected_obligations_and_expands_ids(self):
        from regen.execution.graph_revision_agent import revision_prompt, merge_generated_metadata, assert_nodes_outside_scope_unchanged
        from regen.execution.proof_input import graph_id_aliases, replace_ids, graph_view_scope
        graph = {'theory':'T','revision':1,'nodes':[
            {'id':'contract:fixed','name':'c','role':'contract','statement':'lemma c: P','context':['context X'],'dependencies':['node:h']},
            {'id':'node:h','name':'h','role':'helper','statement':'lemma h: P','context':['context X'],'dependencies':[]},
            {'id':'node:unrelated','name':'u','role':'helper','statement':'lemma u: Q','context':[],'dependencies':[]}]}
        package = {'contracts':[{'contract_id':'contract:fixed','exact_statement':'lemma c: P'}]}
        before = copy.deepcopy(graph)
        prompt = revision_prompt(package,graph,{'node_id':'node:h','residual':'goal: P'},set())
        self.assertLessEqual(len(prompt),10000)
        view = json.loads(prompt.rsplit('INPUT:\n',1)[1])
        self.assertEqual(view['other_node_count'],1)
        self.assertEqual({x['id'] for x in view['local_graph']},{'contract:fixed','node:h'})
        self.assertEqual(view['local_graph'][0]['statement'],'lemma c: P')
        self.assertEqual(view['local_graph'][0]['context'],['context X'])
        aliases = graph_id_aliases(graph)
        decision = replace_ids({'generated_nodes':[{'id':'@1','dependencies':[]}], 'contract_dependencies':{'@0':['@1']}},aliases)
        self.assertEqual(decision['contract_dependencies'],{'contract:fixed':['node:h']})
        merged = merge_generated_metadata(graph,decision)
        self.assertIn(before['nodes'][2],merged['generated_nodes'])
        changed = copy.deepcopy(graph);changed['nodes'][2]['statement']='lemma u: False'
        with self.assertRaises(ValueError):
            assert_nodes_outside_scope_unchanged(graph,changed,graph_view_scope(graph,'node:h'))
        self.assertEqual(graph,before)

    def test_long_target_and_proof_are_not_dropped_to_fit_character_budget(self):
        from regen.execution.lemma_prover import proof_prompt
        node = {'id':'c','statement':'P'*11000,'context':[],'role':'contract','strategy':'direct'}
        proof = 'proof -\n' + 'have True by simp\n'*300 + 'qed'
        prompt = proof_prompt(node,[],[],None,recent_attempts=[{'proof_body':proof,'check':{'diagnostic':'goal: P'}}])
        view = json.loads(prompt.rsplit('INPUT:\n',1)[1])
        self.assertEqual(view['target']['statement'],node['statement'])
        self.assertEqual(view['recent_attempts'][0]['proof_body'],proof)
        self.assertGreater(len(prompt),10000)

    def test_transport_counts_instructions_and_blocks_alternate_backends(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from regen.infrastructure.llm_transcript import call_llm
        with tempfile.TemporaryDirectory() as tmp, patch('regen.infrastructure.llm_transcript.call_codex') as provider:
            for stage in ('stepwise-lemma-prover','local-proof-outline:c'):
                with self.assertRaises(InputBudgetExceeded):
                    call_llm('codex','x'*10001,Path(tmp)/'call',model='test',timeout=1,stage=stage)
            provider.assert_not_called()

    def test_context_alias_roundtrip_and_raw_audit_not_overwritten(self):
        import tempfile
        from pathlib import Path
        from regen.execution.proof_input import expand_graph_decision, render_prompt
        graph = {'nodes':[{'id':'node:h','context':['context X'],'dependencies':[]}]}
        expanded = expand_graph_decision({'generated_nodes':[{'id':'@0','context_ref':0}]},graph,'node:h')
        self.assertEqual(expanded['generated_nodes'][0],{'id':'node:h','context':['context X']})
        with self.assertRaises(ValueError):
            expand_graph_decision({'generated_nodes':[{'id':'@0','context_ref':99}]},graph,'node:h')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'input.raw.json'
            render_prompt('Proof',{'node':{'statement':'P'}},role='prover',audit_path=path)
            first = path.read_text()
            render_prompt('Proof',{'node':{'statement':'Q'}},role='prover',audit_path=path)
            self.assertEqual(path.read_text(),first)
            self.assertEqual(len(list(Path(tmp).glob('*.selection.json'))),2)

    def test_gra_preserves_semantic_failure_after_model_only_error(self):
        from regen.execution.graph_revision_agent import revision_prompt
        graph = {'theory':'T','revision':1,'nodes':[{'id':'c','name':'c','role':'contract','statement':'lemma c: P','context':[],'dependencies':[]}]}
        red = {'node_id':'c','residual':'model returned no proof',
               'checked_history':[{'proof_body':'by simp','check':{'diagnostic':'goal: P ==> Q'}}],
               'attempts':[{'proof_body':'','check':{'diagnostic':'invalid JSON','isabelle_called':False}}]}
        prompt = revision_prompt({'contracts':[{'contract_id':'c','exact_statement':'lemma c: P'}]},graph,red,set())
        view = json.loads(prompt.rsplit('INPUT:\n',1)[1])
        self.assertEqual(view['failure_feedback']['residual'],'goal: P ==> Q')


if __name__ == "__main__":
    unittest.main()
