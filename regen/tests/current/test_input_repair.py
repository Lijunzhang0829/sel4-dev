import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from regen.execution.repository_execution import node_retry_guidance
from regen.execution.lemma_prover import proof_prompt
from regen.planning.premise_retrieval import retrieve_proof_premises


class InputRepairTests(unittest.TestCase):
    def test_guidance_does_not_cross_nodes_or_context_revisions(self):
        node={'id':'a','statement':'lemma a: True','context':[]}
        state={'retry_guidance':{'retrieval_query':'query','repair_guidance':'repair','reason':'noise'},
               'retry_guidance_binding':{'node_id':'a','statement':node['statement'],'context':[]}}
        self.assertEqual(node_retry_guidance(state,node),'query\nrepair')
        for changed in ({**node,'id':'b'},{**node,'statement':'lemma a: False'},{**node,'context':['context C begin']}):
            self.assertEqual(node_retry_guidance(state,changed),'')
        state.pop('retry_guidance_binding')
        self.assertEqual(node_retry_guidance(state,node),'')

    def test_definitions_survive_noisy_query_and_large_records(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'p.sqlite3'
            with sqlite3.connect(db) as c:
                c.execute('CREATE TABLE premises(record_id TEXT,canonical_name TEXT,exact_statement TEXT,owner_theory TEXT,owner_session TEXT,source_class TEXT,attributes_json TEXT)')
                for name,statement in [('Legal.lookupAround2_def','lookupAround2 x m = (let t = lookupAround x m in t)'),('Legal.lookupAround_def','lookupAround x m = m x'),('HOL.Let_def','Let s f = f s'),('Bad.lookupAround2Xdef','irrelevant'),('Noise.noise','loadObject '*2000)]:
                    c.execute('INSERT INTO premises VALUES(?,?,?,?,?,?,?)',(name,name,statement,'Legal','Parent','frozen_parent','[]'))
            records=retrieve_proof_premises(db,'loadObject '*100,goal_statement='lemma target: "lookupAround2 ptr m = z"')
            names=[r['canonical_name'] for r in records]
            self.assertIn('Legal.lookupAround2_def',names)
            self.assertIn('Legal.lookupAround_def',names)
            self.assertIn('HOL.Let_def',names)
            self.assertNotIn('Noise.noise',names)
            self.assertLessEqual(sum(len(r['exact_statement']) for r in records),20000)

    def test_target_context_is_in_prompt(self):
        n={'id':'x','role':'helper','statement':'lemma x: True','strategy':'simp','context':['context Arch begin']}
        p=proof_prompt(n,[],[],None,checker_context={'base_import':'Base'})
        d=json.loads(p.split('INPUT:\n')[1])
        self.assertEqual(d['target']['context'],n['context'])
        self.assertNotIn('checker_context',d)
