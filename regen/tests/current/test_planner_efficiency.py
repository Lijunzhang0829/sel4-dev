from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
from regen.execution.proof_commits import digest, durable
from regen.planning.batch_file_planner import apply_feedback
from regen.planning.planner_efficiency import (expand_edits, failure_identity, reused_failure,
    next_proof_observation, deterministic_red)
from regen.tests.current.test_proof_commits import fixture
from regen.tests.current.test_proof_batches import Checker
from regen.tests.current.test_batch_timings import BatchTimingTest


class EfficiencyTests(unittest.TestCase):
    def history(self, root, body='apply simp\nby blast'):
        _, s = fixture()
        p = {'action':'submit_batch','proofs':{'helper':body}}
        f = {'status':'rejected_batch','failure_identity':'key','check':{'verdict':'red',
            'returncode':1,'timed_out':False,'diagnostic':'Failed to finish proof:\ngoal (1 subgoal):\n 1. x = y\nAt command "done"'}}
        durable(root/'batch-requests/000001/proposal.json',p)
        durable(root/'batch-requests/000001/feedback.json',f)
        return apply_feedback(s,p,f,1),p,f

    def test_exact_failure_reused_but_not_accepted(self):
        with tempfile.TemporaryDirectory() as t:
            s,p,f=self.history(Path(t));old=deepcopy(s)
            result=reused_failure(Path(t),s,'key')
            self.assertTrue(result['cached_failure']);self.assertEqual(result['reused_request'],1)
            self.assertEqual(s,old);self.assertEqual(result['check']['verdict'],'red')
            self.assertIsNone(reused_failure(Path(t),s,'different'))
            self.assertIsNone(reused_failure(Path(t),s,'key',retry_reason='explicit environment health check'))
            durable(Path(t)/'batch-requests/000001/feedback.json',{'check':{'verdict':'green'}})
            with self.assertRaises(ValueError):reused_failure(Path(t),s,'key')

    def test_timeout_and_worker_error_not_memoized(self):
        for c in [{'verdict':'red','returncode':124,'timed_out':True,'diagnostic':'Failed to finish proof'},
                  {'verdict':'red','returncode':1,'diagnostic':'worker pipe closed'},
                  {'verdict':'green','returncode':0,'diagnostic':'Failed to finish proof'}]:
            self.assertFalse(deterministic_red(c))

    def test_identity_changes_with_environment_prefix_or_timeout(self):
        _,s=fixture();c=Checker();run={'implementation':{'x':'1'}}
        key=failure_identity(run,c,s,s)
        self.assertNotEqual(key,failure_identity({'implementation':{'x':'2'}},c,s,s))
        changed=deepcopy(s);changed['proofs']['helper']='by simp'
        self.assertNotEqual(key,failure_identity(run,c,changed,s))
        c.batch_candidate_timeout=121
        self.assertNotEqual(key,failure_identity(run,c,s,s))

    def test_draft_patch_preserves_text_and_runs_normal_validation(self):
        from regen.execution.proof_batches import prepare_batch
        with tempfile.TemporaryDirectory() as t:
            s,p,f=self.history(Path(t));package,_=fixture()
            selection={'request_number':1,'node_id':'helper','proof_sha256':digest(p['proofs']['helper']),
                'edits':[{'old':'by blast','new':'by simp'}]}
            proposal={'action':'submit_batch','proof_edits':[selection]}
            out=expand_edits(Path(t),s,proposal)
            self.assertEqual(out['proofs']['helper'],'apply simp\nby simp')
            selection['edits'][0]['new']='sorry'
            out=expand_edits(Path(t),s,proposal)
            with self.assertRaises(ValueError):prepare_batch(package,s,{'proofs':out['proofs']})
            selection['proof_sha256']='bad'
            with self.assertRaises(ValueError):expand_edits(Path(t),s,proposal)

    def test_ambiguous_edits_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            s,p,f=self.history(Path(t),'apply simp\napply simp\ndone')
            proposal={'proof_edits':[{'request_number':1,'node_id':'helper',
                'proof_sha256':digest(p['proofs']['helper']),'edits':[{'old':'apply simp','new':'apply auto'}]}]}
            with self.assertRaises(ValueError):expand_edits(Path(t),s,proposal)

    def test_selected_goal_read_returns_whole_long_goal(self):
        from regen.planning.planner_workspace import read_observation
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);s,p,f=self.history(root)
            text='P ' + 'x ' * 1800
            f['check']['diagnostic']='Failed to finish proof:\ngoal (1 subgoal):\n 1. '+text+'\nAt command "done"'
            durable(root/'batch-requests/000001/feedback.json',f)
            s=apply_feedback(s,p,f,1)
            v=read_observation(root,s,{'action':'read_proof_state','request_number':1,'goal_indices':[1]},None)
            self.assertEqual(v['proof_state']['diagnostic']['goals'][0]['text'],text.rstrip())
            with self.assertRaises(ValueError):read_observation(root,s,
                {'action':'read_proof_state','request_number':1,'goal_indices':[2]},None)

    def test_residual_auto_observation_complete_not_truncated(self):
        with tempfile.TemporaryDirectory() as t:
            s,p,f=self.history(Path(t));v=next_proof_observation(s)
            self.assertEqual(v['complete_diagnostic_goals'][0]['text'],'x = y')
            self.assertEqual(v['proof_hashes']['helper'],digest(p['proofs']['helper']))
            s['batch_proof_feedback']['feedback']['check']['diagnostic_excerpt']={'incomplete':True}
            self.assertNotIn('complete_diagnostic_goals',next_proof_observation(s))


class RecoveryTests(BatchTimingTest):
    def test_controller_skips_second_identical_native_failure(self):
        with tempfile.TemporaryDirectory() as t:
            proposal=json.dumps({'action':'submit_batch','proofs':{'helper':'by simp'}})
            red={'check':{'verdict':'red','returncode':1,'timed_out':False,
                         'diagnostic':'Failed to finish proof:\ngoal (1 subgoal):\n 1. False'}}
            run,d,j,c,call=self.execute(Path(t),[proposal,proposal],checked=red)
            with patch('regen.execution.proof_batches.check_batch',return_value=red) as check:
                result=run()
            self.assertEqual(result['status'],'checkpoint')
            self.assertEqual(check.call_count,1)
            second=json.loads((d/'batch-requests/000002/feedback.json').read_text())
            self.assertTrue(second['cached_failure'])
            self.assertEqual(second['reused_request'],1)
            self.assertEqual(j.replay()['proofs'],{})

    def test_two_transport_failures_pause_then_resume_without_reset(self):
        with tempfile.TemporaryDirectory() as t:
            run,d,j,c,call=self.execute(Path(t),[])
            def failure(provider,prompt,prefix,**kwargs):
                durable(prefix.with_suffix('.meta.json'),{'timed_out':True,'returncode':124})
                raise RuntimeError('transport timeout')
            call.side_effect=failure
            with patch('regen.planning.batch_file_planner.time.sleep') as sleep:
                result=run()
            self.assertEqual(result['status'],'paused');self.assertTrue(result['recoverable'])
            sleep.assert_called_once_with(10)
            self.assertEqual(j.replay()['batch_requests'],1)
            self.assertEqual(j.replay()['status'],'paused')
            call.side_effect=lambda *a,**k:json.dumps({'action':'pause','reason':'test recovery checkpoint'})
            run()
            self.assertEqual(j.replay()['batch_requests'],2)
