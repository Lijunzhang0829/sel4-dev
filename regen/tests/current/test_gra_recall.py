import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from regen.execution.graph_revision_agent import revision_prompt, revise_graph
from regen.execution.gra_input import graph_page


class GraRecallTest(unittest.TestCase):
    def graph(self):
        return {'theory':'Demo','file_slot':'Demo.thy','revision':1,'nodes':[
            {'id':'c','name':'c','role':'contract','statement':'lemma c: True','context':[],'dependencies':['h'],'consumers':[]},
            {'id':'h','name':'h','role':'helper','statement':'lemma h: True','context':[],'dependencies':[],'consumers':['c']},
            {'id':'other','name':'other_bridge','role':'helper','statement':'lemma other_bridge: True','context':[],'dependencies':[],'consumers':[]}]}

    def test_recent_full_proofs_no_preload_or_length_truncation(self):
        proof='proof -\n'+'have True by simp\n'*700+'qed'
        graph=self.graph(); package={'contracts':[{'contract_id':'c','exact_statement':'lemma c: True'}]}
        red={'node_id':'h','residual':'goal: True','attempts':[{'attempt':1,'proof_body':proof,'check':{'diagnostic':'goal: True'}}],
             'retrieved_legal_premises':[{'canonical_name':'unsolicited','exact_statement':'noise'}]}
        prompt=revision_prompt(package,graph,red,{'other'})
        view=json.loads(prompt.rsplit('INPUT:\n',1)[1])
        self.assertEqual(view['recent_attempts'][0]['proof_body'],proof)
        self.assertNotIn('unsolicited',prompt)
        self.assertGreater(len(prompt),10000)
        self.assertNotIn('other',{n['id'] for n in view['local_graph']})
        page=graph_page(graph,{'other'},{'node_ids':['other']})
        self.assertEqual(page['nodes'][0]['status'],'green')

    def test_graph_and_premise_observations_before_final_decision(self):
        graph=self.graph();package={'contracts':[{'contract_id':'c','exact_statement':'lemma c: True'}]}
        prompts=[]
        replies=[{'observation_request':{'premise_query':'bridge','scope':'invalid'}},
                 {'observation_request':{'graph_query':{'node_ids':['other']}}},
                 {'observation_request':{'premise_query':'other_bridge','scope':'local','mode':'exact'}},
                 {'observation_request':{'premise_query':'h','scope':'local','mode':'exact'}},
                 {'decision':'RETRY_PROOF','failure_class':'PROOF_BODY_ERROR','reason':'use the recalled fact',
                  'residual_evidence':['goal True'],'alternatives_considered':['no structural mismatch'],
                  'confidence':0.8,'repair_guidance':'use the recalled fact'}]
        def call(provider,prompt,prefix,**kwargs):
            prompts.append(json.loads(prompt.rsplit('INPUT:\n',1)[1]))
            prefix.parent.mkdir(parents=True,exist_ok=True)
            prefix.with_suffix('.meta.json').write_text('{"sealable_generation":true}')
            return json.dumps(replies[len(prompts)-1])
        with tempfile.TemporaryDirectory() as tmp, patch('regen.execution.graph_revision_agent.call_llm',side_effect=call), patch('regen.execution.graph_revision_agent.augment_decision_context',return_value={}), patch('regen.execution.graph_revision_agent.write_llm_module_log'), patch('regen.execution.graph_revision_agent.attach_module_outputs'):
            path=Path(tmp)/'theories/Demo'
            result,decision=revise_graph(package,graph,{'node_id':'h','residual':'goal: True'}, {'other'},output_dir=path,premise_database=Path(tmp)/'unused.db')
            self.assertEqual(len(list((path/'graph-revisions/decision-001').glob('observation-*.json'))),4)
            self.assertEqual(len(list((path/'graph-revisions/decision-001').glob('input*.selection.json'))),5)
        self.assertEqual(prompts[1]['recall_results'][0]['status'],'rejected_request')
        self.assertFalse(prompts[1]['gra_interaction_state']['facts_frozen'])
        self.assertEqual(result,graph)
        self.assertEqual(decision['decision'],'RETRY_PROOF')
        self.assertEqual(prompts[2]['observed_nodes'][0]['id'],'other')
        self.assertEqual(prompts[3]['recalled_facts'][0]['record_id'],'other')
        self.assertEqual(prompts[3]['recall_results'][-1]['queries'][0]['mode'], 'exact')
        self.assertEqual(prompts[4]['recalled_facts'],prompts[3]['recalled_facts'])
        self.assertEqual(prompts[4]['recall_results'][-1]['status'],'rejected_frozen')
        self.assertTrue(prompts[4]['gra_interaction_state']['facts_frozen'])

    def test_invalid_graph_and_json_are_proposal_rejections_not_proof_retries(self):
        from regen.tests.current.test_proof_commits import fixture
        from regen.execution.graph_revision_agent import ProposalRejected
        package,state=fixture();graph=state['graph']
        invalid={'decision':'REVISE_GRAPH','operation':'ADD','failure_class':'GRAPH_DEPENDENCY_GAP',
            'reason':'decompose','residual_evidence':['a concrete residual'],'alternatives_considered':[], 'confidence':0.8,
            'generated_nodes':[{'id':'child','name':'child','role':'helper','statement':'lemma child: "True | False"',
                'context':[],'dependencies':['missing'],'strategy':'direct'}]}
        for response in (json.dumps(invalid), 'not a JSON decision'):
            with self.subTest(response=response),tempfile.TemporaryDirectory() as tmp:
                def call(provider,prompt,prefix,**kwargs):
                    prefix.parent.mkdir(parents=True,exist_ok=True)
                    prefix.with_suffix('.meta.json').write_text('{"sealable_generation":true}')
                    return response
                with patch('regen.execution.graph_revision_agent.call_llm',side_effect=call), \
                     patch('regen.execution.graph_revision_agent.write_llm_module_log'), \
                     patch('regen.execution.graph_revision_agent.attach_module_outputs'):
                    with self.assertRaises(ProposalRejected) as caught:
                        revise_graph(package,graph,{'node_id':'helper','residual':'goal: P'},set(),output_dir=Path(tmp))
                self.assertIsInstance(caught.exception.proposal,dict)
                self.assertEqual(graph['revision'],1)

if __name__=='__main__': unittest.main()
