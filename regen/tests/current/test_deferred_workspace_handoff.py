"""One-shot handoff boundary tests; no services, providers or Isabelle."""
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.tools import deferred_workspace_handoff as handoff


class DeferredHandoffTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root/'source'
        self.source.mkdir()
        self.sessions = self.source/'parallel-sessions/s'
        self.config = {'source_run':str(self.source), 'destination':str(self.root/'next'),
                       'sessions':str(self.sessions), 'wait_theories':['A','B'],
                       'priority_theories':['FailedA','FailedB'], 'application':str(self.root/'app'),
                       'status_path':str(self.root/'status.json'), 'poll_seconds':1}

    def put(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def attempt(self, theory, *, number=1, running=False, failure=False, retry=False):
        directory = self.sessions/theory
        if number>1:
            directory /= 'retry-'+str(number-1).zfill(3)
        self.put(directory/'attempt.json', {'attempt':number})
        self.put(directory/'runner-status.json', {'running':running, 'stopped_at':None if running else 'now'})
        self.put(directory/('failure.json' if failure else 'result.json'),
                 {'status':'infrastructure_error' if failure else 'sealed', 'retry_scheduled':retry})
        return directory

    def test_waits_for_both_theories(self):
        self.attempt('A')
        self.assertFalse(handoff.settled(self.sessions, ['A','B'])[0])
        self.attempt('B')
        self.assertTrue(handoff.settled(self.sessions, ['A','B'])[0])

    def test_result_does_not_override_running_monitor(self):
        self.attempt('A', running=True)
        self.assertFalse(handoff.settled(self.sessions, ['A'])[0])

    def test_automatic_retry_must_finish_before_gate(self):
        self.attempt('A', failure=True, retry=True)
        self.assertFalse(handoff.settled(self.sessions, ['A'])[0])
        self.attempt('A', number=2, running=True)
        self.assertFalse(handoff.settled(self.sessions, ['A'])[0])
        self.attempt('A', number=2)
        self.assertTrue(handoff.settled(self.sessions, ['A'])[0])

    def test_terminal_failure_allows_switch_not_infinite_wait(self):
        self.attempt('A', failure=True)
        self.assertTrue(handoff.settled(self.sessions, ['A'])[0])

    def test_gate_evidence_does_not_copy_provider_history(self):
        p=self.attempt('A')
        self.put(p/'result.json', {'status':'sealed','provider_turns':['large history']})
        _, evidence=handoff.settled(self.sessions,['A'])
        self.assertNotIn('provider_turns',evidence['A']['receipt'])

    def test_manifest_preserves_model_timeouts_resources_and_sets_priority(self):
        destination=Path(self.config['destination']); destination.mkdir()
        command=['/usr/bin/python3','-u','-m','regen.execution.proof_session_runner',
                 'parallel-workspace','--run-dir',str(self.source),'--planner-model','gpt-6-astra',
                 '--call-timeout','900','--execution-policy','/old','--allow-external-call']
        self.put(self.source/'experiment.json', {'command':command,'cwd':'/old-app',
                     'resource_requirements':{'min_available_memory_bytes':123}})
        self.put(self.source/'parallel-workspace-policy.json',
                 {'execution_policy':{'stages':[], 'session_stall_retries':1}})
        value=handoff.make_experiment(self.config)
        self.assertEqual(value['cwd'],self.config['application'])
        self.assertEqual(value['resource_requirements'],{'min_available_memory_bytes':123})
        args=value['command']
        self.assertEqual(args[args.index('--run-dir')+1],str(destination))
        self.assertEqual(args[args.index('--planner-model')+1],'gpt-6-astra')
        self.assertEqual(args[args.index('--call-timeout')+1],'900')
        self.assertEqual(handoff.read(destination/'handoff-execution-policy.json'),
                         {'stages':[['FailedA','FailedB']], 'session_stall_retries':1})

    def test_source_stopped_early_does_not_fork_or_restart(self):
        with patch.object(handoff,'validate'), patch.object(handoff.service,'status',
                return_value={'identity_verified':True,'running':False}), \
                patch.object(handoff,'switch') as switch:
            with self.assertRaisesRegex(RuntimeError,'before watched theories'):
                handoff.run(self.config)
            switch.assert_not_called()

    def test_existing_destination_is_not_overwritten(self):
        Path(self.config['destination']).mkdir()
        with patch.object(handoff,'validate'), patch.object(handoff.service,'stop') as stop:
            with self.assertRaisesRegex(ValueError,'already exists'):
                handoff.switch(self.config,{})
            stop.assert_not_called()

    def test_switch_stops_and_locks_before_fork_then_launches(self):
        events=[]
        destination=Path(self.config['destination'])
        def fork(*args):events.append('fork');destination.mkdir()
        with ExitStack() as stack:
            stack.enter_context(patch.object(handoff,'validate'))
            stack.enter_context(patch.object(handoff,'write_status'))
            stack.enter_context(patch.object(handoff.service,'status',
                return_value={'identity_verified':True,'running':True}))
            stack.enter_context(patch.object(handoff.service,'stop',
                side_effect=lambda *_:events.append('stop') or {'running':False}))
            stack.enter_context(patch('regen.execution.proof_session_runner.acquire_run_mutation_lock',
                side_effect=lambda *a,**k:events.append('lock') or 'token'))
            stack.enter_context(patch('regen.execution.proof_session_runner.release_run_mutation_lock',
                side_effect=lambda *_:events.append('unlock')))
            stack.enter_context(patch('regen.execution.repository_execution.fork_workspace_continuation',side_effect=fork))
            stack.enter_context(patch.object(handoff,'make_experiment',return_value={}))
            stack.enter_context(patch.object(handoff.service,'_resource_admission'))
            stack.enter_context(patch.object(handoff.service,'start',
                side_effect=lambda *_:events.append('start') or {'status':'running'}))
            handoff.switch(self.config,{})
        self.assertEqual(events,['stop','lock','fork','unlock','start'])

    def test_unsettled_checker_receipt_blocks_fork(self):
        self.put(self.source/'check-tasks/task/task.json',{'status':'running'})
        with patch.object(handoff,'validate'), patch.object(handoff,'write_status'), \
             patch.object(handoff.service,'status',return_value={'identity_verified':True,'running':False}), \
             patch('regen.execution.proof_session_runner.acquire_run_mutation_lock',return_value='token'), \
             patch('regen.execution.proof_session_runner.release_run_mutation_lock'), \
             patch('regen.execution.repository_execution.fork_workspace_continuation') as fork:
            with self.assertRaisesRegex(RuntimeError,'unsettled source'):
                handoff.switch(self.config,{})
            fork.assert_not_called()


if __name__=='__main__':
    unittest.main()
