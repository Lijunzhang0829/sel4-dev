"""Local failure injection only; no provider or Docker call is performed."""
import errno
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from regen.execution.persistent_checker import PrefixWorker
from regen.execution.proof_commits import ProofCommits
from regen.execution.repository_execution import validator
from regen.tests.current.test_proof_commits import fixture, check
from regen.planning import batch_file_planner as planner


def provider_case(module, *, transient, succeeds_on_retry=False):
    package, initial = fixture()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); directory = root/'theories/Demo'; directory.mkdir(parents=True)
        journal = ProofCommits(directory/'acceptance', {}, validator(package))
        journal.append('initial', initial, {'check':check()})
        checker = Mock(timeout=900, batch_incremental=True)
        checker.imports.return_value = ['Semantic_Base.Semantic_Root']
        setup = ({'run_id':'recovery-test','execution_models':{'File Planner':'test'}},
                 package, directory, journal, checker, [])
        responses = [RuntimeError('injected dispatch failure'),
            json.dumps({'action':'pause','reason':'synthetic test completed'}) if succeeds_on_retry
            else RuntimeError('injected dispatch failure')]
        with patch('regen.execution.repository_execution.setup', return_value=setup), \
             patch('regen.execution.repository_execution.progress', return_value={}), \
             patch('regen.execution.proof_session_runner.frozen_role_model', return_value='test'), \
             patch.object(module, 'call_llm', side_effect=responses) as calls, \
             patch.object(module, 'validate_call_artifact', return_value={'sealable_generation':True}), \
             patch('regen.infrastructure.provider_recovery.transient_failure', return_value=transient), \
             patch.object(module.time, 'sleep') as backoff:
            try:
                result = module._run_batch_file(root,'Demo',max_requests=1)
                raised = None
            except RuntimeError as error:
                result = {}; raised = str(error)
        state = journal.replay()
        assert state['graph']==initial['graph'] and state['proofs']==initial['proofs']
        assert state['batch_requests']==1
        return {'dispatches':calls.call_count,'backoffs':backoff.call_count,
            'backoff_seconds':[x.args[0] for x in backoff.call_args_list],
            'result':result, 'raised':raised,'charged_requests':state['batch_requests'],
            'proof_state_preserved':True,'worker_cleanup_calls':checker.close_prefix.call_count}


def cleanup_case(worker_class, *, touch_fails=True, removal_fails=False):
    with tempfile.TemporaryDirectory() as tmp:
        worker = object.__new__(worker_class)
        worker.root = Path(tmp); worker.closed = False; worker.name = 'test-owned-worker'
        alive = [True]
        worker.process = Mock()
        worker.process.poll.side_effect = lambda: None if alive[0] else 0
        def wait(timeout):
            if alive[0]:raise subprocess.TimeoutExpired('owned-worker', timeout)
            return 0
        worker.process.wait.side_effect = wait
        worker.log = Mock()
        def remove(*args, **kwargs):
            if removal_fails:raise OSError('Docker unavailable')
            alive[0] = False
            return subprocess.CompletedProcess(args[0], 0)
        with patch.object(Path, 'touch', side_effect=OSError(errno.ENOSPC,'disk full') if touch_fails else None), \
             patch('regen.execution.persistent_checker.subprocess.run', side_effect=remove) as removal, \
             patch('regen.execution.persistent_checker.atexit.unregister') as unregister:
            try:worker.close(); error = None
            except OSError as failure:error = str(failure)
            first = {'error':error, 'closed':worker.closed,'alive':alive[0],
                'removal_calls':removal.call_count,'log_closed':worker.log.close.call_count,
                'unregistered':unregister.call_count}
            if removal_fails:
                # A later cleanup attempt must remain possible after daemon recovery.
                removal_fails = False
                alive[0] = False
                worker.close()
                first['retry_closed'] = worker.closed
                first['retry_log_closed'] = worker.log.close.call_count
            elif not alive[0]:
                worker.close()
                first['idempotent_removal_calls'] = removal.call_count
                first['idempotent_log_close_calls'] = worker.log.close.call_count
            return first


class RecoveryFailuresTest(unittest.TestCase):
    def test_nontransient_failure_does_not_dispatch_again(self):
        result = provider_case(planner, transient=False)
        self.assertEqual(result['dispatches'],1)
        self.assertEqual(result['backoffs'],0)
        self.assertIsNotNone(result['raised'])
        self.assertEqual(result['worker_cleanup_calls'],1)

    def test_transient_failure_retries_once_then_recovers(self):
        result = provider_case(planner, transient=True, succeeds_on_retry=True)
        self.assertEqual(result['dispatches'],2)
        self.assertEqual(result['backoff_seconds'],[10])
        self.assertEqual(result['result']['status'],'paused')
        self.assertIsNone(result['raised'])

    def test_two_transient_failures_preserve_recoverable_pause(self):
        result = provider_case(planner, transient=True)
        self.assertEqual(result['dispatches'],2)
        self.assertEqual(result['backoff_seconds'],[10])
        self.assertEqual(result['result']['failure_scope'],'provider_transport')
        self.assertTrue(result['result']['recoverable'])
        self.assertIsNone(result['raised'])

    def test_disk_full_does_not_leak_worker(self):
        result = cleanup_case(PrefixWorker)
        self.assertIsNone(result['error'])
        self.assertTrue(result['closed']);self.assertFalse(result['alive'])
        self.assertEqual(result['removal_calls'],1)
        self.assertEqual(result['log_closed'],1)
        self.assertEqual(result['unregistered'],1)
        self.assertEqual(result['idempotent_log_close_calls'],1)

    def test_failed_removal_remains_retryable(self):
        result = cleanup_case(PrefixWorker, removal_fails=True)
        self.assertIsNotNone(result['error'])
        self.assertFalse(result['closed']);self.assertTrue(result['alive'])
        self.assertEqual(result['unregistered'],0)
        self.assertTrue(result['retry_closed'])
        self.assertEqual(result['retry_log_closed'],1)

    def test_normal_cleanup_unchanged(self):
        result = cleanup_case(PrefixWorker, touch_fails=False)
        self.assertTrue(result['closed']);self.assertFalse(result['alive'])
        self.assertEqual(result['removal_calls'],1)
