"""Observation order/cache/selection regressions without model or native calls."""
from copy import deepcopy
import tempfile
from pathlib import Path
import unittest
from regen.execution.proof_commits import digest,durable
from regen.planning.async_observations import merge,cached_query
from regen.planning.batch_file_planner import planner_input
from regen.tests.current.test_proof_commits import fixture


class ObservationMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.package,self.state=fixture()

    def row(self,number,proposal,result):
        key=str(number)
        durable(self.root/key/'result.json',result)
        return dict(task_id=key,submission_sequence=number,submitted_at=100-number,
                    request={'proposal':proposal},result=result,result_sha256=digest(result))

    def query(self,number,start):
        request={'premise_queries':[{'query':str(start),'mode':'exact'}]}
        result={'status':'available','kind':'fact_selection','query_identity':'q'+str(number),
            'records':[{'record_id':'f'+str(i),'exact_statement':'True'} for i in range(start,start+15)]}
        return self.row(number,{'action':'query','request':request},result)

    def test_reads_survive_input_projection_and_memory_persistence(self):
        state=merge(self.state,[self.query(1,0),self.query(2,15)],self.root)
        row=self.row(3,{'action':'observe'}, {'status':'available','observations':[
            {'request':{'action':'read_facts','record_ids':['f0']},'result':{'status':'available','records':[state['batch_facts'][0]]}},
            {'request':{'action':'read_queries','query_ids':['q1']},'result':{'status':'available','queries':[{'query_id':'q1'}]}}]})
        state=merge(state,[row],self.root)
        durable(self.root/'memory.json',state)
        import json
        restored=json.loads((self.root/'memory.json').read_text())
        payload=planner_input(self.package,restored)
        self.assertIn('f0',[f['record_id'] for f in payload['recalled_facts']])
        self.assertEqual(len(payload['query_index']),2)
        self.assertTrue(payload['requested_queries'])
        self.assertNotIn('cache',payload['query_memory'][0])
        self.assertEqual(restored.get('batch_requests',0),0)
        self.assertEqual(restored['proofs'],{})

    def test_old_workspace_cannot_override_new_after_persistence(self):
        old=self.row(1,{'action':'observe','workspace':{'summary':'old'}},{'status':'available','observations':[]})
        new=self.row(2,{'action':'observe','workspace':{'summary':'new'}},{'status':'available','observations':[]})
        state=merge(self.state,[new],self.root)
        state=merge(deepcopy(state),[old,new],self.root)
        self.assertEqual(state['batch_workspace'],{'summary':'new'})
        self.assertEqual(state['async_selection_versions']['batch_workspace'][0],2)
        # A later proof action can supersede both observations.
        state['batch_workspace']={'summary':'proof choice'}
        state['async_selection_versions']['batch_workspace']=[3,0,'proof-task']
        self.assertEqual(merge(state,[old,new],self.root)['batch_workspace'],{'summary':'proof choice'})

    def test_failed_result_never_selects_and_nested_reads_use_newer_order(self):
        a=self.row(1,{'action':'read_facts'},{'status':'available','records':[{'record_id':'old'}]})
        b=self.row(2,{'action':'read_facts'},{'status':'available','records':[{'record_id':'new'}]})
        state=merge(self.state,[b],self.root)
        state=merge(state,[a],self.root)
        self.assertEqual(state['batch_requested_fact_ids'],['new'])
        failed=self.row(3,{'action':'read_facts'},{'status':'unavailable','records':[{'record_id':'bad'}]})
        self.assertEqual(merge(state,[failed],self.root)['batch_requested_fact_ids'],['new'])

    def test_diagnostic_pages_and_proof_states_survive_grouped_reads(self):
        observations=[
            {'request':{'action':'read_diagnostic'},'result':{'status':'available','diagnostic_page':{'text':'failure A'}}},
            {'request':{'action':'read_diagnostic'},'result':{'status':'available','diagnostic_page':{'text':'failure B'}}},
            {'request':{'action':'read_proof_state'},'result':{'status':'available','proof_state':{'goal':'True'}}},
        ]
        row=self.row(1,{'action':'observe'},{'status':'available','observations':observations})
        state=merge(self.state,[row],self.root)
        self.assertEqual(state['batch_diagnostic_pages'],[{'text':'failure A'},{'text':'failure B'}])
        self.assertEqual(state['batch_requested_proof_states'],[{'goal':'True'}])
        self.assertEqual(state.get('batch_requests',0),0)

    def test_cached_task_feedback_requires_bound_intact_receipt(self):
        row=self.query(1,0);state=merge(self.state,[row],self.root)
        self.assertEqual(cached_query(state,'q1')[1],'1')
        self.assertEqual(cached_query(state,'different-context'),(None,None))
        (self.root/'1'/'result.json').write_text('{}')
        self.assertEqual(cached_query(state,'q1'),(None,None))
