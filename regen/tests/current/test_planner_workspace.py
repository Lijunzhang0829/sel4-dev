from copy import deepcopy
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock,patch
from regen.execution.proof_commits import durable,digest
from regen.planning.planner_workspace import observe,intermediate_target,proof_state,consumer_feedback
from regen.planning.batch_file_planner import apply_feedback,planner_input,batch_query
from regen.tests.current.test_proof_commits import fixture

class WorkspaceTests(unittest.TestCase):
    def setup_attempt(self,directory):
        package,state=fixture();proposal={'action':'submit_batch','proofs':{'contract':'by simp'}}
        feedback={'status':'rejected_batch','check':{'diagnostic':'RED\nFailed to finish proof:\ngoal (2 subgoals):\n 1. A ==> B\n 2. C ==> D\nAt command "by"',
            'native_proof_state_full':{'origin':'before_failed_command','goals':['before_goal'], 'local_assumptions':['local_H'],'executed_commands':[]}}}
        d=directory/'batch-requests/000001';durable(d/'proposal.json',proposal);durable(d/'feedback.json',feedback)
        state=apply_feedback(state,proposal,feedback,1);state['batch_facts']=[{'record_id':'fact','exact_statement':'A ==> B'}]
        return package,state
    def test_compound_reads_persist_and_partial_failure_does_not_erase_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);package,state=self.setup_attempt(d)
            request={'action':'observe','reads':[{'action':'read_attempts','request_numbers':[1]},
                {'action':'read_facts','record_ids':['fact']},{'action':'read_proofs','node_ids':['not-green']}]}
            feedback=observe(d,state,request,Mock());new=apply_feedback(state,request,feedback,2)
            self.assertEqual(feedback['observations'][2]['result']['status'],'unavailable')
            after=apply_feedback(new,{'action':'query'},{'status':'available','kind':'fact_selection','records':[]},3)
            self.assertEqual(after['batch_requested_attempts'][0]['request_number'],1)
            self.assertEqual(after['batch_requested_fact_ids'],['fact'])
            self.assertEqual(apply_feedback(new,request,feedback,2),new)
    def test_exact_residual_and_native_state_are_distinct_and_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);_,s=self.setup_attempt(d)
            q={'proof_state_query':{'request_number':1,'goal_index':2},'context_node_id':'contract'}
            node,evidence=intermediate_target(d,s,q);self.assertEqual(node['statement'],'C ==> D')
            q['proof_state_query'].update(origin='before_command',goal_index=1)
            node,_=intermediate_target(d,s,q);self.assertEqual(node['statement'],'local_H\nbefore_goal')
            preview=proof_state(d,s,{'request_number':1})['proof_state']
            self.assertIn('text_excerpt',preview['diagnostic']['goals'][0])
            durable(d/'batch-requests/000001/feedback.json',{})
            with self.assertRaises(ValueError):intermediate_target(d,s,q)
    def test_recall_forwards_residual_not_entry_goal_and_no_arbitrary_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);p,s=self.setup_attempt(d);request_dir=d/'batch-requests/000002'
            q={'proof_state_query':{'request_number':1,'goal_index':2},'context_node_id':'contract'}
            with patch('regen.execution.file_planner_recall.recall_for_node',return_value={}) as call:
                batch_query({'premise_database':'db'},p,s,Mock(),request_dir,q)
                self.assertEqual(call.call_args.args[1]['statement'],'C ==> D')
                self.assertEqual(call.call_args.kwargs['observation']['request_number'],1)
            q['proof_state_query']['text']='fabricated'
            with self.assertRaises(ValueError):batch_query({'premise_database':'db'},p,s,Mock(),request_dir,q)
    def test_workspace_selection_and_consumer_trial_gap_are_observations_not_proofs(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);p,s=self.setup_attempt(d)
            action={'action':'query','workspace':{'focus_node_ids':['contract'],'record_ids':['fact'],'summary':'inspect residual'}}
            s=apply_feedback(s,action,{'status':'available'},2);s['batch_requests']=20
            view=planner_input(p,s)
            self.assertEqual(view['workspace']['record_ids'],['fact'])
            self.assertEqual(view['consumer_feedback']['pending_contracts'][0]['requests_since_trial'],19)
            self.assertEqual(s['proofs'],{})

    def test_inventory_distinguishes_navigation_excerpt_from_full_visible_diagnostic(self):
        from regen.planning.batch_file_planner import prepare_prompt
        from regen.planning.planner_workspace import observation_inventory
        payload={'last_proof_feedback':{'request_number':112,'feedback':{'check':{'diagnostic':'x'*2823}}},
                 'target_memory':{'contract':{'diagnostic_excerpt':'x'*1800,'diagnostic_excerpt_incomplete':True}}}
        view,_=prepare_prompt(payload)
        item=view['observation_inventory']['diagnostics'][0]
        self.assertEqual(item['request_number'],112)
        self.assertTrue(item['complete'])
        self.assertEqual(item['end'],2823)
