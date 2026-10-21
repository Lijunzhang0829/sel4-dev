"""A confirmed HTTP fault stops only its lane at receipt, not healthy work."""
from contextlib import redirect_stdout
import io
import unittest

from regen.infrastructure.codex_workspace_session import CodexWorkspaceSession, ProviderHTTPError
from regen.tests.current import test_parallel_effective_budget as fixtures


class HTTPClockTests(unittest.TestCase):
    setUp = fixtures.ParallelEffectiveBudgetTests.setUp
    event = fixtures.ParallelEffectiveBudgetTests.event
    elapsed = fixtures.ParallelEffectiveBudgetTests.elapsed

    def test_fault_and_cleanup_excluded_but_peer_keeps_running(self):
        session = CodexWorkspaceSession(tools=[{'name':'query', 'description':'fixture',
            'inputSchema':{'type':'object'}}], http_retry_mode=True)
        session.thread_id = 't'; session.turn_id = 'u'; session._cancel_event = self.stage.event
        self.event('theory_generation_started', 'B')
        with redirect_stdout(io.StringIO()), self.bridge.worker('A'):
            session._generation_start()
            session._model_work_items.add(('t','u','reasoning','item'))
            self.now[0] = 10
            with self.assertRaises(ProviderHTTPError):
                session._notification({'method':'error','params':{'threadId':'t','turnId':'u',
                    'willRetry':False,'error':{'codexErrorInfo':{
                        'responseStreamDisconnected':{'httpStatusCode':None}}}}})
            self.assertFalse(self.stage.event.is_set())
            self.now[0] = 20
            session._generation_stop('ProviderHTTPError')
        self.event('theory_generation_stopped', 'B')
        self.now[0] = 80
        state = self.stage.snapshot()
        self.assertEqual(state['effective_seconds'], 20)
        self.assertEqual(state['worker_seconds'], 30)
        self.assertEqual(state['theory_seconds'], {'A':10, 'B':20})
        kinds = [row['kind'] for row in self.stage.ledger.rows]
        self.assertIn('generation_provider_http_failure', kinds)
        self.assertIn('generation_items_interrupted', kinds)
        self.assertNotIn('generation_clock_untrusted', kinds)
        self.assertFalse(session._model_work_items)

    def test_reconnect_wait_excluded_without_pausing_healthy_peer(self):
        session = CodexWorkspaceSession(tools=[{'name':'query', 'description':'fixture',
            'inputSchema':{'type':'object'}}], http_retry_mode=True)
        session.thread_id = 't'; session.turn_id = 'u'
        self.event('theory_generation_started', 'B')
        with redirect_stdout(io.StringIO()), self.bridge.worker('A'):
            session._generation_start()
            self.now[0] = 10
            session._notification({'method':'error','params':{'threadId':'t','turnId':'u',
                'willRetry':True,'error':{'codexErrorInfo':{
                    'responseStreamDisconnected':{'httpStatusCode':None}}}}})
            self.now[0] = 40
            session._finish_stream_retry()
            self.now[0] = 50
            session._generation_stop('completed')
        self.event('theory_generation_stopped', 'B')
        state = self.stage.snapshot()
        self.assertEqual(state['effective_seconds'], 50)
        self.assertEqual(state['theory_seconds'], {'A':20, 'B':50})
        self.assertFalse(state['paused'])

    def test_bootstrap_http_fault_has_zero_generation_cost(self):
        session = CodexWorkspaceSession(tools=[{'name':'query', 'description':'fixture',
            'inputSchema':{'type':'object'}}], http_retry_mode=True)
        with redirect_stdout(io.StringIO()), self.bridge.worker('A'):
            self.now[0] = 10
            with self.assertRaises(ProviderHTTPError):
                session._raise_http_failure({'message':'HTTP 503'}, source='rpc_error', request_id=1)
            self.now[0] = 90
        self.assertEqual(self.elapsed(), 0)
        self.assertFalse(self.stage.event.is_set())


if __name__ == '__main__':
    unittest.main()
