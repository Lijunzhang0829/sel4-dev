from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from regen.execution.proof_commits import durable
from regen.planning.batch_file_planner import apply_feedback
from regen.planning.batch_routes import expand_reuse,node_results,route_signals
from regen.execution.proof_batches import prepare_batch
from regen.tests.current.test_proof_commits import fixture

class BatchRouteTests(unittest.TestCase):
    def test_completed_prefix_is_not_partial_acceptance(self):
        p,old=fixture();candidate=deepcopy(old);candidate['proofs']={'helper':'by simp','contract':'by simp'};candidate['accepted_order']=['helper','contract']
        evidence={'check':{'verdict':'red','log_content':'REGEN_NODE helper\nREGEN_TIMING_END helper 1.0\nREGEN_NODE contract\n'}}
        outcomes=node_results(old,candidate,evidence)
        self.assertEqual([x['status'] for x in outcomes],['completed_uncommitted','failed'])
        self.assertFalse(any(x['accepted'] for x in outcomes));self.assertEqual(old['proofs'],{})
    def test_exact_reuse_requires_fresh_check_and_bound_original_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);package,state=fixture();prior={'action':'submit_batch','proofs':{'helper':'by simp','contract':'by simp'}}
            feedback={'status':'rejected_batch','check':{'verdict':'red','diagnostic':'failure'}}
            durable(d/'batch-requests/000001/proposal.json',prior);durable(d/'batch-requests/000001/feedback.json',feedback)
            state=apply_feedback(state,prior,feedback,1)
            expanded=expand_reuse(d,state,{'action':'submit_batch','reuse_proofs':[{'request_number':1,'node_ids':['helper']}],'proofs':{}})
            candidate=prepare_batch(package,state,{k:v for k,v in expanded.items() if k not in ['action','reuse_origin']})
            self.assertEqual(candidate['proofs'],{'helper':'by simp'});self.assertEqual(state['proofs'],{})
            with self.assertRaises(ValueError):expand_reuse(d,state,{'reuse_proofs':[{'request_number':1,'node_ids':['helper']}],'proofs':{'helper':'by blast'}})
            durable(d/'batch-requests/000001/proposal.json',{'proofs':{'helper':'forged'}})
            with self.assertRaises(ValueError):expand_reuse(d,state,{'reuse_proofs':[{'request_number':1,'node_ids':['helper']}]})
    def test_route_counts_are_advisory_and_costs_not_double_counted_per_node(self):
        _,state=fixture();proposal={'action':'submit_batch','proofs':{'helper':'by simp','contract':'by simp'}}
        feedback={'status':'rejected_batch','check':{'verdict':'red','diagnostic':'failure'},'cost_seconds':{'model':2,'action':4}}
        state=apply_feedback(state,proposal,feedback,1);state=apply_feedback(state,proposal,feedback,2)
        signals=route_signals(state)
        self.assertEqual(signals['multi_node_batches'],2)
        self.assertEqual(signals['recorded_cost_seconds'],{'model':4,'action':8})
        self.assertEqual(list(signals['repeated_candidate_signatures'].values()),[2])
        self.assertNotIn('limit',signals)
