"""Document vocabulary preserves source, identities, references and evidence."""
from copy import deepcopy
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from regen.planning.document_interface import public_view, internal_request, observation_instructions
from regen.planning.observation_memory import merge
from regen.planning.declaration_index import initial_index, accepted_declarations
from regen.tests.current.test_proof_commits import fixture
from regen.tests.current import test_theory_document as document_fixtures


class DocumentInterfaceTests(unittest.TestCase):
    def test_selectors_and_source_refs_roundtrip_without_rewriting_ids_or_proofs(self):
        raw={'source_refs':[{'run_id':'graph-node-run','node_id':'graph','node_sha256':'abc',
                            'theory':'T','proof_sha256':'def','commit_id':'ghi'}],
             'proofs':{'graph':'using nodes by simp','node_id':'by simp'},
             'request':{'context_node_id':'node_ids'},'node_results':[{'node_id':'graph'}]}
        shown=public_view(raw)
        self.assertEqual(shown['source_refs'][0]['entry_id'],'graph')
        self.assertEqual(shown['proofs'],raw['proofs'])
        self.assertEqual(internal_request(shown),raw)
        with self.assertRaises(ValueError):internal_request({'entry_ids':['a'],'node_ids':['b']})

    def test_observation_prompt_uses_document_selectors(self):
        prompt=observation_instructions()
        self.assertIn('context_entry_id',prompt)
        self.assertIn('entry_ids',prompt)
        for old in ('context_node_id','node_ids','graph node','submit_batch','dependencies'):
            self.assertNotIn(old,prompt)

    def test_receipt_memory_uses_neutral_fields_for_new_documents(self):
        package,state=fixture()
        state['graph']=accepted_declarations(initial_index(package,run_id='doc-test'),[])
        with tempfile.TemporaryDirectory() as tmp:
            new=merge(state,[],Path(tmp))
        self.assertIn('observation_tasks',new)
        self.assertIn('observation_queries',new)
        self.assertFalse(any(k.startswith('async_') for k in new))
        self.assertEqual(new['proofs'],state['proofs'])


class DocumentBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.base=document_fixtures.DocumentControllerTests();self.base.setUp();self.addCleanup(self.base.doCleanups)
        self.controller=self.base.controller

    def test_reported_capacity_distinguishes_writer_and_global_resources(self):
        result=self.controller.handle('list_workspace',{}, {})
        self.assertNotIn('worker_count',result)
        self.assertEqual(result['assigned_writer_count'],1)
        self.assertEqual(result['max_parallel_theories'],self.base.executor.worker_capacity)
        self.assertEqual(result['native_slots'],1)
        self.assertEqual(result['theory_tool_execution'],'serial')

    def test_entry_selectors_are_normalized_before_read_execution(self):
        def observe(kind,proposal,*args):
            request=proposal['reads'][0]
            self.assertEqual(request['node_ids'],['target_contract'])
            return {'status':'available','node_results':[{'node_id':'target_contract'}]}
        with patch.object(self.controller,'_observe',side_effect=observe):
            result=self.base.call({'action':'read_proofs','entry_ids':['target_contract']})
        self.assertEqual(result['entry_results'][0]['entry_id'],'target_contract')

    def test_progress_artifacts_use_document_names(self):
        draft=self.base.read()['document']
        saved=self.base.call({'action':'edit_theory','base_sha256':draft['source_sha256'],
                            'edits':[{'old':'oops','new':'by simp'}]})
        accepted=self.base.call({'action':'check_theory','source_sha256':saved['source_sha256']})
        self.assertEqual(accepted['feedback']['status'],'accepted')
        from regen.execution.repository_execution import _progress_unlocked
        path=self.base.fixture.root/'execution-input.json'
        data=json.loads(path.read_text())
        for row in data['files']:
            package=next(p for p in self.base.fixture.packages if p['theory']==row['theory'])
            row['contract_ids']=[c['contract_id'] for c in package['contracts']]
        path.write_text(json.dumps(data))
        run_path=self.base.fixture.root/'run.json'
        run=json.loads(run_path.read_text())
        run['session_progress_ledger']=str(self.base.fixture.root/'session-progress.json')
        run_path.write_text(json.dumps(run))
        result=_progress_unlocked(self.base.fixture.root)
        target=next(row for row in result['files'] if row['theory']=='Target')
        self.assertIn('document_revision',target)
        self.assertNotIn('graph_revision',target)
        directory=self.base.fixture.root/'theories'/'Target'
        self.assertTrue((directory/'declaration-index.json').is_file())
        self.assertFalse((directory/'active-graph.json').exists())
        progress=json.loads((directory/'progress.json').read_text())
        self.assertIn('declaration_index',progress)
        self.assertNotIn('graph',progress)
        self.assertEqual(progress['proofs'],{'target_contract':'by simp'})
