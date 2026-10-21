"""Grouped retrieval retains bindings and every selected observation on recovery."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from regen.execution.proof_commits import durable, digest
from regen.planning.batch_file_planner import (
    apply_feedback, planner_input, cached_query_feedback, execute_query, restore_batch_memory,
)
from regen.planning.planner_workspace import observe
from regen.tests.current.test_proof_commits import fixture


class ObserveIntegrationTests(unittest.TestCase):
    def test_grouped_queries_have_separate_audits_and_bound_recoverable_cache(self):
        package, state = fixture()
        original = deepcopy(state)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); directory = root/'batch-requests/000001'
            directory.mkdir(parents=True)
            proposal = {'action':'observe','reads':[
                {'action':'query','request':{'premise_queries':[{'query':name}]}}
                for name in ('first', 'second')]}
            def query(run, package, state, checker, directory, request, *, audit_path):
                name = request['premise_queries'][0]['query']
                page = {'kind':'fact_selection','records':[{'record_id':name,'exact_statement':name}]}
                durable(audit_path,page,immutable=True)
                return page
            with patch('regen.planning.batch_file_planner.batch_query',side_effect=query):
                feedback = observe(root,state,proposal,Mock(),query_handler=lambda request,index:
                    execute_query({'run_id':'test'},package,state,Mock(),directory,request,index))
            self.assertEqual(state,original)
            for index in (1,2):
                self.assertTrue((directory/f'fact-selection-{index:02d}.json').is_file())
            durable(directory/'proposal.json',proposal)
            durable(directory/'feedback.json',feedback)
            new = apply_feedback(state,proposal,feedback,1);new['batch_requests']=1
            view = planner_input(package,new)
            self.assertEqual(len(view['query_memory']),2)
            self.assertEqual({f['record_id'] for f in view['recalled_facts']},{'first','second'})
            self.assertEqual(len(new['batch_history']),1)
            identity=feedback['observations'][0]['result']['query_identity']
            self.assertEqual(cached_query_feedback(new,root/'batch-requests/000002',identity),
                             (feedback['observations'][0]['result'],1))
            restored=restore_batch_memory(root,new,accepted_results={})
            self.assertEqual(restored,new)
            # A changed sibling also invalidates the enclosing audited result.
            tampered=deepcopy(feedback);tampered['observations'][1]['result']['records']=[]
            durable(directory/'feedback.json',tampered)
            self.assertEqual(cached_query_feedback(new,root/'batch-requests/000002',identity),(None,None))
            self.assertEqual(new['proofs'],{})

    def test_invalid_child_workspace_is_isolated_before_feedback_recovery(self):
        package, state = fixture()
        state['batch_facts']=[{'record_id':'known','exact_statement':'P'}]
        proposal={'action':'observe','reads':[
            {'action':'read_facts','record_ids':['known'],'workspace':{'focus_node_ids':['invalid']}},
            {'action':'read_facts','record_ids':['known']}]}
        feedback=observe(Path('/unused'),state,proposal,Mock())
        self.assertEqual(feedback['observations'][0]['result']['status'],'unavailable')
        new=apply_feedback(state,proposal,feedback,1)
        self.assertEqual(new['batch_requested_fact_ids'],['known'])
        self.assertEqual(new.get('batch_workspace'),state.get('batch_workspace'))

    def test_same_kind_pages_and_fact_selections_survive_other_decisions(self):
        package,state=fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);directory=root/'batch-requests/000001'
            original={'status':'rejected_batch','check':{'diagnostic':'abcdefgh'}}
            candidate={'action':'submit_batch','proofs':{'contract':'by simp'}}
            durable(directory/'proposal.json',candidate);durable(directory/'feedback.json',original)
            state=apply_feedback(state,candidate,original,1)
            state['batch_facts']=[{'record_id':x,'exact_statement':x} for x in ('A','B')]
            proposal={'action':'observe','reads':[
                {'action':'read_diagnostic','request_number':1,'offset':0,'limit':4},
                {'action':'read_diagnostic','request_number':1,'offset':4,'limit':4},
                {'action':'read_facts','record_ids':['A']},
                {'action':'read_facts','record_ids':['B']}]}
            feedback=observe(root,state,proposal,Mock())
            new=apply_feedback(state,proposal,feedback,2)
            self.assertEqual([x['text'] for x in new['batch_diagnostic_pages']],['abcd','efgh'])
            self.assertEqual(new['batch_requested_fact_ids'],['A','B'])
            self.assertEqual(apply_feedback(new,proposal,feedback,2),new)
            other=apply_feedback(new,{'action':'query'},{'status':'available','kind':'fact_selection','records':[]},3)
            self.assertEqual(planner_input(package,other)['diagnostic_pages'],new['batch_diagnostic_pages'])
            self.assertEqual(other['batch_requested_fact_ids'],['A','B'])
            later=apply_feedback(other,{'action':'read_diagnostic'},{'status':'available',
                'diagnostic_page':{'request_number':1,'text':'efgh'}},4)
            self.assertEqual(len(later['batch_diagnostic_pages']),1)
            self.assertEqual(apply_feedback(later,proposal,feedback,2)['batch_diagnostic_pages'],later['batch_diagnostic_pages'])

if __name__=='__main__':unittest.main()
