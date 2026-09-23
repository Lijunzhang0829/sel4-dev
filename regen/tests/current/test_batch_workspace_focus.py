"""New helper focus must not reject a well-formed exploratory proof batch."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.proof_batches import prepare_batch
from regen.planning.batch_file_planner import apply_feedback, _run_batch_file
from regen.planning.planner_workspace import validate_workspace
from regen.tests.current import test_batch_timings
from regen.tests.current.test_proof_commits import fixture


def proposal():
    return {'action':'submit_batch','reason':'Explore a useful intermediate statement',
            'graph':{'generated_nodes':[{'id':'new_helper','name':'new_helper','role':'helper',
                'statement':'lemma new_helper: "True | True"','context':[], 'dependencies':[],
                'strategy':'exploratory candidate for contract'}]},
            'proofs':{'new_helper':'by simp'},'workspace':{'focus_node_ids':['new_helper']}}


class NewHelperFocusTests(unittest.TestCase):
    def setUp(self):
        for name in ('configure_event_monitor','emit_event','close_event_monitor'):
            mocked=patch('regen.infrastructure.progress_events.'+name)
            mocked.start();self.addCleanup(mocked.stop)

    def test_green_and_red_focus_have_no_effect_on_proof_authority(self):
        package,state=fixture();request=proposal()
        candidate=prepare_batch(package,state,{k:v for k,v in request.items() if k not in {'action','workspace'}})
        self.assertEqual(validate_workspace(candidate,request['workspace']),request['workspace'])
        red=apply_feedback(state,request,{'status':'rejected_batch','check':{'verdict':'red'}},1)
        self.assertEqual(red['batch_workspace'],request['workspace'])
        self.assertEqual(red['proofs'],{})
        self.assertNotIn('new_helper',[n['id'] for n in red['graph']['nodes']])
        self.assertEqual(apply_feedback(red,request,{'status':'rejected_batch','check':{'verdict':'red'}},1),red)
        accepted=apply_feedback(candidate,request,{'status':'accepted'},1)
        self.assertIn('new_helper',accepted['proofs'])
        self.assertEqual(accepted['batch_workspace'],request['workspace'])

    def test_unknown_focus_still_rejected_and_bad_request_does_not_pollute_selection(self):
        package,state=fixture();request=proposal()
        request['workspace']['focus_node_ids']=['typo']
        candidate=prepare_batch(package,state,{k:v for k,v in request.items() if k not in {'action','workspace'}})
        with self.assertRaises(ValueError):validate_workspace(candidate,request['workspace'])
        after=apply_feedback(state,request,{'status':'rejected_request'},1)
        self.assertNotIn('batch_workspace',after)
        self.assertEqual(after['proofs'],{})

    def test_controller_checks_candidate_before_workspace_validation(self):
        case=test_batch_timings.BatchTimingTest()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                _,directory,journal,checker,call=case.execute(root, [json.dumps(proposal())],max_requests=1)
                with patch('regen.execution.proof_batches.check_batch',return_value={'check':{'verdict':'red','diagnostic':'native RED'}}) as check:
                    _run_batch_file(root,'Demo',max_requests=1)
                self.assertEqual(check.call_count,1)
                feedback=json.loads((directory/'batch-requests/000001/feedback.json').read_text())
                self.assertEqual(feedback['status'],'rejected_batch')
                self.assertEqual(journal.replay()['batch_workspace'],proposal()['workspace'])
        finally:
            case.doCleanups()

    def test_received_tools_do_not_dispatch_models_or_close_cached_worker(self):
        case=test_batch_timings.BatchTimingTest()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                _,directory,journal,checker,call=case.execute(root, [],max_requests=1)
                cache={}; received=[]
                def decide(prompt,payload,prefix,**identity):
                    received.append(identity)
                    return json.dumps({'action':'read_proofs','node_ids':['helper']})
                for _ in range(2):
                    _run_batch_file(root,'Demo',max_requests=1,decision_source=decide,runtime_cache=cache)
                self.assertEqual(len(received),2)
                self.assertEqual(call.call_count,0)
                self.assertEqual(checker.close_prefix.call_count,0)
                self.assertEqual([c.args[0] for c in journal.append.call_args_list].count('resume'),1)
                timing=json.loads((directory/'batch-requests/000002/timing.json').read_text())
                self.assertEqual(timing['model_attempts'],0)
                self.assertEqual(timing['dispatch_kind'],'dynamic_tool_call')
                self.assertFalse(timing['calls'][0]['provider_called'])
        finally:
            case.doCleanups()

    def test_received_action_failure_is_never_automatically_redispatched(self):
        case=test_batch_timings.BatchTimingTest()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                _,directory,journal,checker,call=case.execute(root, [],max_requests=1)
                calls=[]
                def decide(*args,**kwargs):
                    calls.append(1)
                    raise RuntimeError('tool source audit failed')
                with self.assertRaisesRegex(RuntimeError,'source audit'):
                    _run_batch_file(root,'Demo',max_requests=1,decision_source=decide,runtime_cache={})
                self.assertEqual(calls,[1])
                self.assertEqual(call.call_count,0)
        finally:
            case.doCleanups()
