import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from regen.execution.file_planner_recall import recall_for_node
from regen.execution.proof_commits import digest
from regen.planning.batch_file_planner import batch_query
from regen.tests.current import test_premise_database_v2 as fixtures


class FilePlannerRecallTest(unittest.TestCase):
    setUp = fixtures.PremiseDatabaseV2Tests.setUp

    def recall(self, request, records=()):
        catalog = self.root/'sealed.json'
        catalog.write_text(json.dumps({'records':list(records)}))
        return recall_for_node(self.db, {'id':'target','statement':'map id xs = xs','context':[]},
            {'goal_query':True, **request}, generated_records=catalog,
            audit_path=self.root/'audit.json')

    def example(self, **changes):
        return {'record_id':'sealed-map', 'canonical_name':'Sealed.map_identity',
            'exact_statement':'map id xs = xs', 'context':['context X'],
            'source_class':'generated_theorem', 'status':'sealed', 'theory_seal_id':'seal-1',
            'proof_commit_id':'000001','proof_body':'by (induct xs) simp_all',
            'proof_sha256':digest('by (induct xs) simp_all'), **changes}

    def test_target_recalls_upstream_and_imported_sealed_helpers(self):
        page=self.recall({},[self.example(), self.example(record_id='draft',status='planned'),
            self.example(record_id='human',source_class='deleted_human_proof')])
        ids={r['record_id'] for r in page['records']}
        self.assertIn('p1',ids)
        self.assertIn('sealed-map',ids)
        self.assertNotIn('draft',ids)
        self.assertNotIn('human',ids)
        self.assertEqual(json.loads((self.root/'audit.json').read_text())['target'],'map id xs = xs')

    def test_examples_require_body_provenance_and_matching_digest(self):
        page=self.recall({'purpose':'proof_example','proof_hint':'induct xs'},
            [self.example(),self.example(record_id='bad',proof_sha256='wrong'),
             self.example(record_id='legacy',proof_body=None),self.example(record_id='no-commit',proof_commit_id=None)])
        self.assertEqual([r['record_id'] for r in page['records']],['sealed-map'])
        self.assertEqual(page['records'][0]['context'],['context X'])
        self.assertIn('induct xs',page['records'][0]['proof_body'])

    def test_upstream_has_no_fabricated_proof_examples(self):
        self.assertEqual(self.recall({'purpose':'proof_example','scope':'upstream'})['records'],[])

    def test_invalid_query_and_unavailable_vectors_fail_explicitly(self):
        for request in ({'scope':'local'},{'proof_state_query':True},
                        {'proof_hint':'induct'},{'use_vectors':'yes'}, {'use_vectors':True}):
            with self.subTest(request=request),self.assertRaises(ValueError):
                self.recall(request)

    def test_controller_uses_graph_target_and_filtered_catalog_no_local_facts(self):
        directory=self.root/'theory/batch-requests/000001'
        directory.mkdir(parents=True)
        node={'id':'n','statement':'map id xs = xs','context':[]}
        state={'graph':{'nodes':[node]},'proofs':{'n':'by simp'}}
        with patch('regen.execution.file_planner_recall.recall_for_node',return_value={}) as recall:
            batch_query({'premise_database':str(self.db)}, {}, state, Mock(), directory,
                        {'goal_query':True,'context_node_id':'n'})
            self.assertEqual(recall.call_args.args[1],node)
            self.assertEqual(recall.call_args.kwargs['generated_records'],self.root/'theory/sealed-premises.json')
        with patch('regen.execution.round_recall.select_round_facts',return_value={}) as select:
            batch_query({'premise_database':str(self.db)}, {}, state, Mock(), directory,
                        {'premise_query':'map','scope':'all'})
            self.assertEqual(select.call_args.kwargs['local_facts'],())
        for request in ({'goal_query':True},{'goal_query':True,'context_node_id':'unknown'},
                        {'premise_query':'map','scope':'local'}):
            with self.subTest(request=request),self.assertRaises(ValueError):
                batch_query({'premise_database':str(self.db)}, {},state,Mock(),directory,request)
