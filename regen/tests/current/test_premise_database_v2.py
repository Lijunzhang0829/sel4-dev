import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from regen.preparation.premise_database import SCHEMA, digest, validated_records, verify_database
from regen.execution.proof_state_recall import build_vectors, recall_for_proof_state
from regen.execution.round_recall import is_fact_request, select_round_facts


class PremiseDatabaseV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root/'parent-premises.sqlite'
        self.rows = [dict(record_id='p1', canonical_name='Base.map_id', declaration_name='Base.map_id',
            fact_index=0, exact_statement='map id xs = xs', owner_theory='Base',
            source_class='frozen_parent', attributes=['simp']),
            dict(record_id='p2', canonical_name='Base.plus_zero', declaration_name='Base.plus_zero',
            fact_index=0, exact_statement='n + 0 = n', owner_theory='Base',
            source_class='frozen_parent', attributes=[])]
        self.facts = {(r['declaration_name'],0):(hashlib.sha1(r['exact_statement'].encode()).hexdigest(),
                     'Lib.Base',False,0,'a'*40) for r in self.rows}
        accepted,rejected=validated_records(self.rows,{'Lib.Base':'Lib'},self.facts,{'Lib'})
        self.assertFalse(rejected)
        with sqlite3.connect(self.db) as db:
            db.executescript(SCHEMA)
            db.executemany('INSERT INTO premises VALUES (?,?,?,?,?,?,?,?,?,?)',[tuple(r.values()) for r in accepted])
            db.execute("INSERT INTO premise_search(premise_search) VALUES ('rebuild')")
        (self.root/'parent-premises-manifest.json').write_text(json.dumps({'schema_version':2,'database_sha256':digest(self.db)}))

    def test_native_origin_is_mandatory_and_not_inferred(self):
        for index,value,reason in [(1,'Unknown.Base','unregistered_or_wrapper_theory'),
                                   (2,True,'dynamic_collection_origin_unresolved'),
                                   (3,1,'hidden_hypotheses'),(0,'bad','statement_mismatch')]:
            witness=list(self.facts[('Base.map_id',0)])
            witness[index]=value
            accepted,rejected=validated_records(self.rows[:1],{'Lib.Base':'Lib'},
                {('Base.map_id',0):tuple(witness)},{'Lib'})
            self.assertFalse(accepted)
            self.assertEqual(rejected[0]['reason'],reason)
        accepted,rejected=validated_records(self.rows,{'Lib.Base':'Refine'},self.facts,{'Refine'})
        self.assertFalse(accepted)
        self.assertTrue(all(r['reason']=='illegal_source_session' for r in rejected))

    def test_retained_semantic_facts_require_semantic_certificate(self):
        row={**self.rows[0],'source_class':'retained_semantic_export'}
        accepted,_=validated_records([row],{'Lib.Base':'Lib'},self.facts,{'Lib'})
        self.assertFalse(accepted)
        accepted,_=validated_records([row],{'Lib.Base':'Semantic_Base'},self.facts,{'Lib'})
        self.assertEqual(len(accepted),1)

    def test_retrieval_complete_statement_and_no_match(self):
        result=recall_for_proof_state(self.db,'map id xs = xs')
        self.assertEqual(result['records'][0]['record_id'],'p1')
        self.assertEqual(result['records'][0]['exact_statement'],'map id xs = xs')
        self.assertEqual(recall_for_proof_state(self.db,'nonexistent_xyz')['records'],[])
        self.assertEqual(recall_for_proof_state(self.db,'map',attributes=['simp'])['records'][0]['record_id'],'p1')

    def test_database_tampering_rejected(self):
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE premises SET owner_session='Refine'")
        with self.assertRaisesRegex(ValueError,'integrity'):
            recall_for_proof_state(self.db,'map')

    def test_vectors_and_stale_or_tampered_index(self):
        index=self.root/'premise-vectors.sqlite'
        metadata=build_vectors(self.db,index,fields=['exact_statement'])
        self.assertEqual(metadata['fields'],['exact_statement'])
        result=recall_for_proof_state(self.db,'map id xs',vector_index=index)
        self.assertEqual(result['records'][0]['record_id'],'p1')
        with sqlite3.connect(index) as db:
            meta={**metadata,'database_sha256':'stale'}
            db.execute('UPDATE metadata SET value=?',(json.dumps(meta),))
        with self.assertRaisesRegex(ValueError,'integrity'):
            recall_for_proof_state(self.db,'map',vector_index=index)
        index.with_suffix('.sqlite.sha256').write_text(digest(index))
        with self.assertRaisesRegex(ValueError,'stale'):
            recall_for_proof_state(self.db,'map',vector_index=index)

    def test_explicit_state_request_uses_harness_state_and_freezes_audit(self):
        request={'proof_state_query':True}
        self.assertTrue(is_fact_request(request))
        with self.assertRaisesRegex(ValueError,'trusted'):
            select_round_facts(self.db,request,audit_path=self.root/'missing.json')
        result=select_round_facts(self.db,request,proof_state='map id xs = xs',audit_path=self.root/'audit.json')
        self.assertEqual(result['status'],'frozen')
        self.assertEqual(result['records'][0]['record_id'],'p1')
        with self.assertRaises(FileExistsError):
            select_round_facts(self.db,request,proof_state='map',audit_path=self.root/'audit.json')
        with self.assertRaises(ValueError):
            select_round_facts(self.db,{'proof_state_query':'untrusted'},proof_state='map',audit_path=self.root/'bad.json')

    def test_v2_planner_queries_use_minimal_schema(self):
        from regen.preparation.planning_input import PlanningInputQueries
        payload={'artifact_type':'repository-planning-input','contracts':[],
                 'parent_premises':{'database':self.db.name,'database_sha256':digest(self.db)},
                 'context_profiles':[{'context_profile_id':'base'}],
                 'planner_interfaces':{'search_parent_premises':{'max_results':50},
                     'inspect_parent_premises':{'max_ids':50},'resolve_symbols':{'max_symbols':64}}}
        path=self.root/'repository-planning-input.json'
        path.write_text(json.dumps(payload))
        q=PlanningInputQueries(path)
        self.assertEqual(q.search_parent_premises('map',context_profile_ids=['base'])[0]['record_id'],'p1')
        self.assertEqual(q.resolve_symbols(['Base.map_id'],'base')[0]['status'],'resolved')
        record=q.inspect_parent_premises(['p1'])[0]
        self.assertIn('proposition_digest',record)
        self.assertNotIn('logical_shape',record)

    def test_repository_inspection_rejects_proof_state_mode(self):
        from regen.planning.repository_planner import execute_inspection
        contract={'contract_id':'c1','exact_statement':'map id xs = xs','contract_owned_context':[]}
        payload={'artifact_type':'repository-planning-input','contracts':[contract],
                 'parent_premises':{'database':self.db.name,'database_sha256':digest(self.db)},
                 'context_profiles':[{'context_profile_id':'base'}],
                 'planner_interfaces':{'inspect_contracts':{'max_ids':32},
                     'inspect_parent_premises':{'max_ids':50}}}
        path=self.root/'repository-planning-input.json';path.write_text(json.dumps(payload))
        request={'schema_version':1,'contract_batches':[['c1']],
                 'premise_searches':[{'query':'','mode':'proof_state','contract_ids':['c1'],
                      'context_profile_ids':['base'],'attributes':[],'limit':5}]}
        with self.assertRaisesRegex(ValueError,'lemma search'):
            execute_inspection(payload,request,path)

    def test_schema_downgrade_cannot_bypass_integrity(self):
        with sqlite3.connect(self.db) as db: db.execute('PRAGMA user_version=0')
        with self.assertRaisesRegex(ValueError,'integrity'): verify_database(self.db)

    def test_vector_fields_are_allowlisted(self):
        with self.assertRaises(ValueError):
            build_vectors(self.db,self.root/'bad.sqlite',fields=['human_proof'])


if __name__=='__main__': unittest.main()
