import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from regen.execution.premise_recall import recall_premises
from regen.execution.lemma_prover import prove_node


class PremiseRecallTest(unittest.TestCase):
    def make_database(self, path):
        with sqlite3.connect(path) as connection:
            connection.execute('CREATE TABLE premises (record_id TEXT, canonical_name TEXT, exact_statement TEXT, owner_theory TEXT, owner_session TEXT, source_class TEXT, attributes_json TEXT)')
            for key,source in [('parent','frozen_parent'),('definition','retained_semantic_export'),('forbidden','deleted_human_proof')]:
                connection.execute('INSERT INTO premises VALUES (?,?,?,?,?,?,?)',
                    (key,'Base.bridge_'+key,'lemma bridge_'+key+': "True"','Base','Semantic_Base',source,'[]'))

    def test_three_scopes_exclude_unsealed_and_forbidden_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'facts.db'; self.make_database(db)
            generated=Path(tmp)/'generated.json'
            generated.write_text(json.dumps({'records':[
                {'record_id':'sealed','canonical_name':'Sealed.bridge','exact_statement':'lemma bridge: True','source_class':'generated_theorem','theory_seal_id':'seal-1'},
                {'record_id':'draft','canonical_name':'Draft.bridge','exact_statement':'lemma bridge: False','source_class':'generated_theorem'}]}))
            local=[{'id':'green','name':'local_bridge','statement':'lemma local_bridge: True','context':['context X'],'proof_sha256':'accepted-proof'}]
            args={'generated_records':generated,'local_facts':local,'mode':'search'}
            all_results=recall_premises(db,'bridge',**args)
            self.assertEqual({x['record_id'] for x in all_results['records']},{'parent','definition','sealed','green'})
            for scope,expected in [('upstream',{'parent','definition'}),('sealed',{'sealed'}),('local',{'green'})]:
                self.assertEqual({x['record_id'] for x in recall_premises(db,'bridge',scope=scope,**args)['records']},expected)
            with self.assertRaises(ValueError): recall_premises(db,'bridge',scope='deleted',**args)

    def test_recall_keeps_long_statement_and_pages_by_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'facts.db';self.make_database(db)
            statement='lemma long_bridge: "'+('True & '*1000)+'True"'
            with sqlite3.connect(db) as connection:
                connection.execute('INSERT INTO premises VALUES (?,?,?,?,?,?,?)',('long','Base.long_bridge',statement,'Base','HOL','frozen_parent','[]'))
            page=recall_premises(db,'Base.long_bridge',scope='upstream',page_size=1)
            self.assertEqual(page['records'][0]['exact_statement'],statement)
            self.assertIsNone(page['next_offset'])
            self.assertEqual(page['effective_mode'], 'exact')
            page=recall_premises(db,'bridge',scope='upstream',mode='search',page_size=1)
            self.assertIsNotNone(page['next_offset'])
            next_page=recall_premises(db,'bridge',scope='upstream',mode='search',page_size=1,offset=page['next_offset'])
            self.assertNotEqual(next_page['records'][0]['record_id'],page['records'][0]['record_id'])

    def insert_facts(self, db, rows):
        with sqlite3.connect(db) as connection:
            connection.executemany('INSERT INTO premises VALUES (?,?,?,?,?,?,?)',
                [(key, name, statement, 'Base', 'Base', 'frozen_parent', '[]')
                 for key, name, statement in rows])

    def test_exact_lookup_does_not_fill_or_lose_late_alphabetical_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'facts.db'; self.make_database(db)
            self.insert_facts(db, [(str(i), 'AAA.noise_'+str(i), 'readCell x = x') for i in range(600)]
                + [('exact', 'ZZZ.readCell', 'readCell x = x')])
            page=recall_premises(db, 'readCell', mode='exact')
            self.assertEqual([x['record_id'] for x in page['records']], ['exact'])
            self.assertEqual(page['matched_candidates'], 1)
            self.assertIsNone(page['next_offset'])
            self.assertEqual(recall_premises(db, 'premise:missing', mode='exact')['records'], [])
            self.assertEqual(recall_premises(db, 'readcell', mode='exact')['records'], [])
            self.assertEqual(recall_premises(db, 'Other.readCell', mode='exact')['records'], [])

    def test_definition_checks_constant_lhs_and_reports_missing_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'facts.db'; self.make_database(db)
            self.insert_facts(db, [
                ('def', "Base.obj_at'_real_def", "obj_at' P p = readCell p"),
                ('body', "Base.obj_at'_consumer_def", "consumer p = obj_at' P p"),
                ('split', 'Base.select.simps(1)', 'select (Some x) = x')])
            page=recall_premises(db, "obj_at'_def")
            self.assertFalse(page['exact_match_found'])
            self.assertEqual(page['effective_mode'], 'definition')
            self.assertEqual([x['record_id'] for x in page['records']], ['def'])
            self.assertEqual(recall_premises(db, "obj_at'_def", mode='exact')['records'], [])
            self.assertEqual([x['record_id'] for x in recall_premises(db, 'select', mode='definition')['records']], ['split'])
            self.assertEqual(recall_premises(db, 'readCell_def')['records'], [])
            batch=recall_premises(db, "obj_at'_def readCell_def")
            self.assertEqual(batch['effective_mode'], 'definition_batch')
            self.assertEqual([x['record_id'] for x in batch['records']], ['def'])
            self.assertEqual([x['matched_candidates'] for x in batch['lookup_results']], [1, 0])

    def test_search_rejects_generic_component_overlap_and_preserves_apostrophe(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'facts.db'; self.make_database(db)
            self.insert_facts(db, [
                ('getter', 'Base.getCell_def', 'getCell x = readCell x'),
                ('noise', 'Base.object_type_return', 'object_type x = return x'),
                ('reference', 'Base.other_operation_def', 'other_operation x = getCell x'),
                ('thread', 'Base.getThread_corres', 'corres (getThread x) (getThread x)'),
                ('prime', "Base.obj_at'_def", "obj_at' x = True"),
                ('plain', 'Base.obj_at_def', 'obj_at x = False')])
            self.assertEqual([x['record_id'] for x in recall_premises(db, 'getCell', mode='search')['records']], ['getter'])
            self.assertEqual(recall_premises(db, 'getCell_return')['records'], [])
            self.assertEqual([x['record_id'] for x in recall_premises(db, "obj_at'", mode='search')['records']], ['prime'])
            self.assertEqual(recall_premises(db, 'get_cell readCell', mode='search')['records'], [])
            page=recall_premises(db, 'getCell readCell', mode='search')
            self.assertEqual([x['record_id'] for x in page['records']], ['getter'])
            self.assertEqual(page['records'][0]['match_kind'], 'search')
            self.assertEqual(recall_premises(db, 'getCell missingFact', mode='search')['records'], [])

    def test_search_does_not_merge_distinct_operation_spellings(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'facts.db'; self.make_database(db)
            self.insert_facts(db,[('abstract','Base.get_cell_def','get_cell x = x'),
                                  ('concrete','Base.getCell_def','getCell x = x'),
                                  ('qualified','Other.getCell_def','getCell x = x')])
            self.assertEqual([r['record_id'] for r in recall_premises(db,'get_cell',mode='search')['records']],['abstract'])
            self.assertEqual({r['record_id'] for r in recall_premises(db,'getCell',mode='search')['records']},{'concrete','qualified'})
            self.assertEqual([r['record_id'] for r in recall_premises(db,'Other.getCell',mode='definition')['records']],['qualified'])

    def test_missing_catalog_is_not_created_and_literal_underscore_is_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'missing.db'
            with self.assertRaises(FileNotFoundError): recall_premises(db, 'cell', scope='upstream')
            self.assertFalse(db.exists())
            self.make_database(db)
            self.insert_facts(db, [('wrong','Base.readXcell','readXcell x = x'),
                                  ('right','Base.read_cell','read_cell x = x')])
            self.assertEqual([x['record_id'] for x in recall_premises(db, 'read_cell', mode='exact')['records']], ['right'])
            for kwargs in ({'mode':'typo'}, {'mode':'exact','query':'two names'},
                           {'page_size':6}, {'offset':True}):
                args={'query':'cell', **kwargs}
                with self.assertRaises(ValueError): recall_premises(db, **args)

    def test_prover_has_no_preload_and_local_recall_excludes_unproved_nodes(self):
        graph={'theory':'Demo','file_slot':'Demo.thy','context_policy':'committed-prefix-v1','accepted_order':['green'],'nodes':[
            {'id':'green','name':'green_bridge','role':'helper','statement':'lemma green_bridge: True','context':[],'dependencies':[],'strategy':'direct'},
            {'id':'pending','name':'pending_bridge','role':'helper','statement':'lemma pending_bridge: False','context':[],'dependencies':[],'strategy':'direct'},
            {'id':'target','name':'target','role':'contract','statement':'lemma target: True','context':[],'dependencies':[],'strategy':'direct'}]}
        prompts=[]
        def call(provider,prompt,prefix,**kwargs):
            prompts.append(json.loads(prompt.rsplit('INPUT:\n',1)[1]))
            prefix.parent.mkdir(parents=True,exist_ok=True)
            prefix.with_suffix('.meta.json').write_text('{"sealable_generation":true}')
            if len(prompts)==1:
                return '{"observation_request":{"premise_query":"green_bridge","scope":"local","mode":"exact"}}'
            return '{"proof_body":null}'
        with tempfile.TemporaryDirectory() as tmp, patch('regen.execution.lemma_prover.call_llm',side_effect=call), patch('regen.execution.lemma_prover.retrieve_proof_premises') as preload, patch('regen.execution.lemma_prover.write_llm_module_log'):
            result=prove_node(graph,'target',{'green':'by simp'},checker=object(),premise_database=Path(tmp)/'not-opened.db',output_dir=Path(tmp)/'theories/Demo',max_attempts=2)
            preload.assert_not_called()
        self.assertNotIn('recalled_facts',prompts[0])
        self.assertNotIn('recall_results',prompts[0])
        self.assertIn('interaction_state',prompts[0])
        self.assertEqual(prompts[0]['recent_attempts'],[])
        self.assertEqual([x['record_id'] for x in prompts[1]['recalled_facts']],['green'])
        self.assertEqual(prompts[1]['recall_results'][0]['queries'][0]['mode'], 'exact')
        self.assertEqual(result['status'],'red')


if __name__=='__main__': unittest.main()
