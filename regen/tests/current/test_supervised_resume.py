import json
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from copy import deepcopy
from regen.execution.repository_execution import resume_round_feedback
from regen.execution.proof_session_runner import node_log_directory
from regen.tests.current.test_proof_commits import fixture
from regen.tools.supervised_proof_run import supervise, worker
from unittest.mock import patch


class SupervisedResumeTest(unittest.TestCase):
    def test_only_charged_checker_bound_failure_is_recovered(self):
        _, state=fixture();key='helper';node=next(n for n in state['graph']['nodes'] if n['id']==key)
        state['rounds']={key:1};before=deepcopy(state)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);rd=node_log_directory(root,key)/'round-01';rd.mkdir(parents=True)
            source=root/'checked.thy';source.write_text(node['statement']+'\nby simp')
            (rd/'attempts.json').write_text(json.dumps([{'attempt':1,'proof_body':'by simp',
                'check':{'verdict':'red','returncode':1,'source':str(source),'diagnostic':'goal: unresolved'}}]))
            got=resume_round_feedback(root,state,key)
            self.assertTrue(got['recovered_after_interruption'])
            self.assertEqual(got['residual'],'goal: unresolved')
            self.assertEqual(state,before)
            state['rounds'][key]=0
            self.assertIsNone(resume_round_feedback(root,state,key))
            state['rounds'][key]=1;source.write_text('unrelated source')
            self.assertIsNone(resume_round_feedback(root,state,key))

    def test_supervisor_records_killed_child_and_clears_stale_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'runner-status.json').write_text(json.dumps({'running':True,'started_at':'2026-01-01T00:00:00+00:00'}))
            code=supervise(root,[sys.executable,'-c','import os,signal;os.kill(os.getpid(),signal.SIGKILL)'],cleanup=False,interval=.05)
            status=json.loads((root/'supervisor-status.json').read_text())
            self.assertEqual(code,-signal.SIGKILL)
            self.assertEqual(status['signal'],signal.SIGKILL)
            self.assertFalse(status['running'])
            self.assertFalse(json.loads((root/'runner-status.json').read_text())['running'])
            self.assertFalse(status['outcome_present'])

    def test_supervisor_preserves_normal_exit_and_prevents_relaunch(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.assertEqual(supervise(root,[sys.executable,'-c','print("done")'],cleanup=False,interval=.05),0)
            self.assertIn('done',(root/'runner.log').read_text())
            with self.assertRaises(RuntimeError):supervise(root,[sys.executable,'-c','pass'],cleanup=False)

    def test_worker_uses_public_locked_runner_signature_and_frozen_budgets(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'run';root.mkdir();source=Path(temp)/'source'
            policy_dir=source/'theories'/'Demo';policy_dir.mkdir(parents=True)
            policy={'provider':'codex','backend':'whole-proof','timeout':10,'max_attempts':5,
                'max_node_rounds':5,'max_node_revisions':8,'graph_timeout':10,
                'think_timeout':10,'node_timeout':10,'salvage_provider':None,'context_retry_rounds':1}
            (policy_dir/'runtime-policy.json').write_text(json.dumps(policy))
            (root/'run.json').write_text(json.dumps({'execution_models':{
                'File Planner':'planner','Graph Revision Agent':'gra','Lemma Prover':'prover'}}))
            with patch('regen.execution.proof_session_runner.run_file',autospec=True,return_value={'status':'failed'}) as run:
                worker(root,source,'Demo')
            self.assertEqual(run.call_args.kwargs['max_node_rounds'],5)
            self.assertEqual(json.loads((root/'OUTCOME.json').read_text())['status'],'failed')
