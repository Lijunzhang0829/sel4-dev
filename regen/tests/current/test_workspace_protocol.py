"""Exercise editable-plan diagnostics, query paging and audited recovery."""
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import unittest
from unittest.mock import patch

from regen.planning import workspace_planner
from regen.planning.workspace_harness import WorkspaceHarness, diagnostics
from regen.planning.workspace_preflight import render
from regen.preparation.premise_database import digest
from regen.tests.current import test_workspace_planning as fixtures


class WorkspaceProtocolTest(unittest.TestCase):
    def setUp(self):
        f = fixtures.WorkspacePlanningTest()
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.f = f

    def test_schema5_required_imports_reach_projection_and_native_source(self):
        from regen.execution.repository_execution import projection_package
        from regen.planning.import_contract import required_imports
        from regen.preparation.refine_campaign import sha256_json
        p = copy.deepcopy(self.f.proposal)
        p['schema_version'] = 5
        for t in p['theories']:
            t['required_imports'] = t.pop('suggested_predecessors')
        a = p['theories'][0]
        b = copy.deepcopy(a)
        b.update(name='Provider', contract_ids=[], required_imports=[])
        b['helper_decisions'] = [{'id':'shared', 'kind':'propose_auxiliary_intent',
            'intent':'Shared property.', 'for_contracts':['C0001'], 'premise_ids':[]}]
        a['required_imports'] = [{'theory':'Provider', 'reason':'Required shared result.'}]
        p['theories'].append(b)
        result = self.f.h.dispatch({'action':'submit', 'proposal':p})
        self.assertTrue(result['valid'], result)
        canonical, bundle = self.f.h.artifacts()
        self.assertEqual(canonical['schema_version'], 5)
        plan = bundle['repository-plan.json']
        self.assertIn('required_import_edges', plan)
        self.assertNotIn('suggested_theory_edges', plan)
        listing = bundle['file-generation-list.json']
        self.assertEqual(listing['schema_version'], 6)
        for entry in listing['files']:
            projection = bundle[entry['projection']]
            self.assertEqual(entry['projection_sha256'], sha256_json(projection))
            package = projection_package(projection, {**plan, 'plan_id':'test'})
            self.assertNotIn('suggested_providers', package['repository_projection'])
            self.assertEqual(required_imports(projection), projection['required_imports'])
        dest = self.f.root/'schema5-source'
        render(self.f.h.view.payload, p, dest, 'Base')
        self.assertIn('imports "Base" "Provider"', (dest/(a['name']+'.thy')).read_text())
        text = workspace_planner.instruction(self.f.h.view.payload)
        self.assertIn('required_imports', text)
        self.assertNotIn('suggested_predecessors', text)

    def test_schema5_rejects_legacy_field_and_reports_current_error_path(self):
        p = copy.deepcopy(self.f.proposal)
        p['schema_version'] = 5
        self.assertFalse(self.f.h.dispatch({'action':'validate','proposal':p})['valid'])
        for t in p['theories']:
            t['required_imports'] = t.pop('suggested_predecessors')
        p['theories'][0]['required_imports'] = [{'theory':'Missing','reason':'Needed.'}]
        result = self.f.h.dispatch({'action':'validate','proposal':p})
        self.assertIn('/theories/0/required_imports/0/theory', [e['path'] for e in result['errors']])

    def test_unknown_providers_contexts_and_uninspected_facts_reported_together(self):
        p = copy.deepcopy(self.f.proposal)
        t = p['theories'][0]
        t['context_profile_ids'] = ['missing-context']
        t['suggested_predecessors'] = [{'theory':'missing-provider','reason':'Needs a result.'}]
        t['helper_decisions'] = [{'id':'bad-id','kind':'reuse_existing_fact','intent':'Reuse.',
                                 'for_contracts':['missing-contract'],'premise_ids':['p1']}]
        result = self.f.h.dispatch({'action':'submit','proposal':p})
        self.assertFalse(result['valid'])
        paths = {e['path'] for e in result['errors']}
        self.assertIn('/theories/0/suggested_predecessors/0/theory', paths)
        self.assertIn('/theories/0/context_profile_ids', paths)
        self.assertIn('/theories/0/helper_decisions/0/premise_ids', paths)
        self.assertIn('/theories/0/helper_decisions/0/id', paths)
        self.f.native.assert_not_called()

    def test_provider_cycle_reports_actual_chain_not_priority_fix(self):
        p = copy.deepcopy(self.f.proposal)
        a = p['theories'][0]
        b = copy.deepcopy(a); b.update(name='Generated_Other',contract_ids=[])
        b['helper_decisions'] = [{'id':'aux','kind':'propose_auxiliary_intent','intent':'Shared result.',
                                 'for_contracts':['C0001'],'premise_ids':[]}]
        a['suggested_predecessors'] = [{'theory':b['name'],'reason':'Needs provider.'}]
        b['suggested_predecessors'] = [{'theory':a['name'],'reason':'Needs provider.'}]
        p['theories'].append(b)
        errors = diagnostics(self.f.h.view.payload,p)
        self.assertIn([a['name'],b['name'],a['name']], [e.get('cycle') for e in errors])

    def test_checker_entry_collision_cannot_publish_or_overwrite_a_theory(self):
        p = copy.deepcopy(self.f.proposal);p['theories'][0]['name']='Planner_Preflight_All'
        result = self.f.h.dispatch({'action':'submit','proposal':p})
        self.assertFalse(result['valid']);self.f.native.assert_not_called()
        with self.assertRaisesRegex(ValueError,'reserved'):
            render(self.f.h.view.payload,p,self.f.root/'render','Base')

    def test_relation_evidence_must_match_both_endpoints(self):
        payload = copy.deepcopy(self.f.h.view.payload)
        payload['contract_relation_candidates'] = [{'relation_id':'r','left_contract_id':'C0001','right_contract_id':'C0002'}]
        p = copy.deepcopy(self.f.proposal)
        p['contract_relations'] = [{'left_contract_id':'C0001','right_contract_id':'C0003',
                                    'relation':'shared-invariant','evidence_ids':['r']}]
        errors = diagnostics(payload,p)
        self.assertTrue(any(e['path']=='/contract_relations/0/evidence_ids' for e in errors))

    def test_definition_paging_literal_names_and_lexical_navigation(self):
        h = self.f.h
        database = h.queries.database
        with sqlite3.connect(database) as db:
            row = list(db.execute('SELECT * FROM premises WHERE record_id="p1"').fetchone())
            row[0]='p3'; row[1]=row[2]='Base.map_extra_def'
            row[4]='map extra xs = other xs'
            db.execute('INSERT INTO premises VALUES (?,?,?,?,?,?,?,?,?,?)',row)
            private=list(row);private[0]='p4';private[1]=private[2]='Demo_R.map_private_def';private[7]='Demo_R'
            db.execute('INSERT INTO premises VALUES (?,?,?,?,?,?,?,?,?,?)',private)
        payload = json.loads(self.f.input.read_text());payload['parent_premises']['database_sha256']=digest(database)
        self.f.input.write_text(json.dumps(payload))
        (self.f.input.parent/'parent-premises-manifest.json').write_text(json.dumps({'schema_version':2,'database_sha256':digest(database)}))
        h = WorkspaceHarness(self.f.input,self.f.root/'paged',self.f.native)
        first = h.dispatch({'action':'definitions','values':['map'],'limit':1})
        self.assertEqual(first['next_offset'],1)
        second = h.dispatch({'action':'definitions','values':['map'],'limit':1,'offset':first['next_offset']})
        self.assertEqual(second['next_offset'],2)
        hidden=h.dispatch({'action':'definitions','values':['map'],'limit':1,'offset':2})
        self.assertEqual(hidden['records'],[]);self.assertEqual(hidden['withheld_records'],1)
        self.assertIsNone(hidden['next_offset'])
        self.assertNotIn('p4',h.state['inspected_premises'])
        both=h.dispatch({'action':'definitions','values':['map']})
        self.assertEqual(len(both['records']),2);self.assertEqual(both['withheld_records'],1)
        self.assertNotEqual(first['records'][0]['record_id'],second['records'][0]['record_id'])
        self.assertEqual(h.dispatch({'action':'definitions','values':['%']})['records'],[])
        self.assertEqual(h.dispatch({'action':'definitions','values':['map_']})['records'][0]['record_id'],'p1')
        self.assertIn('other',second['mentioned_identifiers']['p3'])
        self.assertFalse(h.dispatch({'action':'definitions','values':['map'],'offset':-1})['valid'])

    def test_snapshot_nonregular_file_does_not_block(self):
        fifo = self.f.root/'fifo';os.mkfifo(fifo)
        self.assertIsNone(workspace_planner.snapshot_file(fifo))

    def test_preflight_receipt_tampering_rejected_before_artifact_publication(self):
        h = self.f.h
        self.assertTrue(h.dispatch({'action':'submit','proposal':self.f.proposal})['submitted'])
        cache = h.output/'preflight'/h.submitted['preflight_sha256']/'result.json'
        cache.write_text('{}')
        with self.assertRaisesRegex(ValueError,'integrity'):
            h.artifacts()

    def test_input_rebinding_cannot_reuse_old_query_receipts(self):
        payload=json.loads(self.f.input.read_text());payload['contracts'][0]['exact_statement']='lemma changed: "False"'
        self.f.input.write_text(json.dumps(payload))
        with self.assertRaises(FileExistsError):
            WorkspaceHarness(self.f.input,self.f.h.output,self.f.native)

    def test_default_entry_uses_workspace_and_old_controller_fixture_remains_iterative(self):
        policy=json.loads(Path('regen/config/repository-planning-policy.json').read_text())
        self.assertEqual(policy['interaction'],'agent-workspace')
        self.assertEqual(self.f.f.policy['interaction'],'agent-iterative')
        self.assertNotIn('max_attempts',policy['model'])

    def test_timeout_discards_submission_and_never_retries_same_output(self):
        output=self.f.root/'timeout-runner'
        policy=json.loads(Path('regen/config/repository-planning-policy.json').read_text())
        policy['_allow_external_call']=True
        settings={'codex_bin':sys.executable,'codex_sha256':digest(Path(sys.executable))}
        with patch.object(workspace_planner,'model_settings',return_value=settings), \
             patch.object(workspace_planner,'native_checker',return_value=self.f.native), \
             patch.object(workspace_planner,'call_worker',return_value=(
                 {'timed_out':True,'returncode':124},output/'unused')) as call:
            with self.assertRaisesRegex(RuntimeError,'fresh plan'):
                workspace_planner.run_agent({},self.f.input,policy,output)
            with self.assertRaisesRegex(ValueError,'fresh-only'):
                workspace_planner.run_agent({},self.f.input,policy,output)
            self.assertEqual(call.call_count,1)
        self.assertFalse((output/'control/submitted.json').exists())
        self.assertFalse(list((output/'control').glob('checkpoint-*')))
        self.assertFalse((output/'snapshots').exists())
        self.assertEqual(json.loads((output/'planning-attempt.json').read_text())['status'],'failed')

    def test_completed_submission_cannot_restart_model_in_same_directory(self):
        output=self.f.root/'runner'
        policy=json.loads(Path('regen/config/repository-planning-policy.json').read_text())
        policy['model']['model']='test-model';policy['_allow_external_call']=True
        settings={'codex_bin':sys.executable,'codex_sha256':digest(Path(sys.executable))}
        def worker(settings,policy,out,public,workspace,api,text,number):
            # Synthetic provider transcript; this test makes no model call.
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
                client.connect(str(api/'planner.sock'))
                request={'action':'submit','proposal':self.f.proposal}
                client.sendall(json.dumps(request).encode()+b'\n');client.shutdown(socket.SHUT_WR)
                with client.makefile('rb') as stream:response=json.loads(stream.readline())
            self.assertTrue(response['submitted'])
            prefix=out/f'transcript/workspace-agent-{number:06d}'
            prefix.parent.mkdir(parents=True,exist_ok=True)
            data={'.prompt.txt':text,'.ndjson':json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'done'}})+'\n',
                  '.final.txt':'done','.stderr.txt':'',
                  '.audit.json':json.dumps({'observed_item_types':['agent_message'],'forbidden_item_types':[],'invalid_json_lines':0})}
            meta={'state':'complete','returncode':0,'timed_out':False,'provider':'codex-workspace-audited',
                  'scope':'repository-planning-only','model':'test-model','reasoning_effort':'medium',
                  'codex_sha256':settings['codex_sha256']}
            for suffix,content in data.items():
                Path(str(prefix)+suffix).write_text(content)
                key={'.ndjson':'raw','.prompt.txt':'prompt','.final.txt':'final','.stderr.txt':'stderr','.audit.json':'audit'}[suffix]
                meta[key+'_sha256']=hashlib.sha256(content.encode()).hexdigest()
            Path(str(prefix)+'.meta.json').write_text(json.dumps(meta))
            return meta,prefix
        with patch.object(workspace_planner,'model_settings',return_value=settings), patch.object(workspace_planner,'native_checker',return_value=self.f.native), patch.object(workspace_planner,'call_worker',side_effect=worker) as call:
            first=workspace_planner.run_agent({},self.f.input,policy,output)
            with self.assertRaisesRegex(ValueError,'fresh-only'):
                workspace_planner.run_agent({},self.f.input,policy,output)
            self.assertEqual(call.call_count,1)
            Path(str(first['plan_prefix'])+'.final.txt').write_text('tampered')
            with self.assertRaisesRegex(ValueError,'fresh-only'):
                workspace_planner.run_agent({},self.f.input,policy,output)
            self.assertEqual(call.call_count,1)


if __name__=='__main__':unittest.main()
