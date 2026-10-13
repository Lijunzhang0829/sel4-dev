"""Real transport state machine with injected protocol events, no provider."""
from contextlib import redirect_stdout
import io
import threading
import unittest
from unittest.mock import Mock, patch

from regen.infrastructure.codex_workspace_session import (
    CodexWorkspaceSession, ProviderHTTPError, ProviderOverloaded, SessionProtocolError)


class HTTPTransportTests(unittest.TestCase):
    def setUp(self):
        self.output = redirect_stdout(io.StringIO()); self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def session(self, enabled=True):
        session = CodexWorkspaceSession(tools=[{'name':'query', 'description':'test',
            'inputSchema':{'type':'object'}}], http_retry_mode=enabled)
        session.thread_id = 't'; session.turn_id = 'u'
        return session

    def notification(self, thread='t'):
        return {'method':'error','params':{'threadId':thread,'turnId':'u','willRetry':True,
            'error':{'codexErrorInfo':{'responseStreamDisconnected':{'httpStatusCode':None}}}}}

    def terminal(self, thread='t'):
        return {'method':'turn/completed', 'params':{'threadId':thread,
            'turn':{'id':'u','status':'failed','error':{'message':'HTTP 503'}}}}

    def test_retrying_notification_keeps_existing_owner_alive(self):
        session = self.session()
        session._notification(self.notification())
        self.assertFalse(session._http_interrupted)
        self.assertIsNone(session._http_failure)
        self.assertIsNotNone(session._stream_retry_phase)
        session._notification({'method':'item/agentMessage/delta', 'params':{
            'threadId':'t','turnId':'u','itemId':'msg','delta':'recovered'}})
        self.assertIsNone(session._stream_retry_phase)

    def test_retrying_during_native_callback_does_not_abort_or_exclude_proof_work(self):
        session = self.session(); session._callback_executing = True
        session._on_abort = Mock()
        session._callback_notification(self.notification())
        session._on_abort.assert_not_called()
        self.assertIsNone(session._stream_retry_phase)
        session._end_model_callback()
        self.assertIsNotNone(session._stream_retry_phase)

    def test_reconnect_deadline_is_recoverable_not_session_stalled(self):
        session = self.session(); session._timeout = 1
        session._notification(self.notification())
        session._stream_retry_since -= 2
        with self.assertRaises(ProviderHTTPError):
            session._read_message()

    def test_005_terminal_stream_error_interrupts_open_items_without_clock_corruption(self):
        session=self.session();session._generation_active=True
        session._model_work_items={('t','u','reasoning','rs_005_a'),
                                   ('t','u','reasoning','rs_005_b')}
        seen=[]
        message={'method':'error','params':{'threadId':'t','turnId':'u',
            'willRetry':False,'error':{'codexErrorInfo':'other',
            'message':'stream disconnected before completion: An error occurred while processing your request'}}}
        with patch('regen.infrastructure.progress_events.emit_event',
                   side_effect=lambda event,**fields:seen.append((event,fields))):
            with self.assertRaises(ProviderHTTPError) as caught:
                session._notification(message)
            session._generation_stop('ProviderHTTPError')
        self.assertEqual(caught.exception.failure,
                         {'kind':'transport','http_status':None})
        self.assertEqual([event for event,_ in seen],
                         ['generation_provider_http_failure',
                          'generation_items_interrupted','theory_generation_stopped'])
        self.assertEqual(seen[1][1]['item_ids'],['rs_005_a','rs_005_b'])
        self.assertFalse(session._model_work_items)

    def test_007_terminal_overload_interrupts_open_message_without_clock_corruption(self):
        session=self.session();session._generation_active=True
        session._model_work_items={('t','u','agentMessage','msg_007')}
        seen=[]
        message={'method':'error','params':{'threadId':'t','turnId':'u',
            'willRetry':False,'error':{'codexErrorInfo':'serverOverloaded',
            'message':'The server is overloaded'}}}
        with patch('regen.infrastructure.progress_events.emit_event',
                   side_effect=lambda event,**fields:seen.append((event,fields))):
            with self.assertRaises(ProviderOverloaded):
                session._notification(message)
            session._generation_stop('ProviderOverloaded')
        self.assertEqual([event for event,_ in seen],
                         ['generation_items_interrupted','theory_generation_stopped'])
        self.assertEqual(seen[0][1]['item_ids'],['msg_007'])
        self.assertFalse(session._model_work_items)

    def test_old_default_preserves_legacy_reconnect(self):
        session = self.session(False)
        session._notification(self.notification())
        self.assertFalse(session._http_interrupted)

    def test_foreign_notification_cannot_spend_http_retry(self):
        session = self.session()
        with self.assertRaises(SessionProtocolError) as caught:
            session._notification(self.notification('foreign'))
        self.assertNotIsInstance(caught.exception, ProviderHTTPError)
        self.assertFalse(session._http_interrupted)

    def test_matching_bootstrap_rpc_error_is_classified(self):
        session = self.session(); session.thread_id = session.turn_id = None
        session._receive = Mock(return_value={'id':1,'error':{'data':{
            'codexErrorInfo':{'httpConnectionFailed':{'httpStatusCode':502}}}}})
        with self.assertRaises(ProviderHTTPError) as caught:
            session._await(1, 1)
        self.assertEqual(caught.exception.failure['http_status'], 502)

    def test_foreign_rpc_error_is_not_classified_as_ours(self):
        session = self.session()
        session._receive = Mock(side_effect=[{'id':99,'error':{'message':'HTTP 503'}},
                                             {'id':1,'result':{'ok':True}}])
        self.assertEqual(session._await(1, 1), {'ok':True})
        self.assertFalse(session._http_interrupted)

    def test_callback_failure_aborts_owned_work_and_is_not_deferred(self):
        session = self.session(); session._callback_executing = True
        session._callback_reader_stop = threading.Event()
        session._read_message = Mock(return_value=self.terminal())
        session._on_abort = Mock()
        session._pump_callback_events()
        session._on_abort.assert_called_once()
        self.assertIsInstance(session._callback_reader_error, ProviderHTTPError)
        self.assertIsNone(session._deferred_terminal)
        with self.assertRaises(ProviderHTTPError):
            session._check_callback_abort()

    def test_foreign_callback_terminal_is_protocol_error_not_retry(self):
        session = self.session(); session._callback_executing = True
        with self.assertRaises(SessionProtocolError) as caught:
            session._callback_notification(self.terminal('foreign'))
        self.assertNotIsInstance(caught.exception, ProviderHTTPError)

    def test_failed_turn_and_cleanup_keep_original_http_exception(self):
        session = self.session(); abort = Mock()
        session._start = Mock(); session._request = Mock(return_value=1)
        session._receive = Mock(side_effect=[{'id':1,'result':{'turn':{'id':'u'}}}, self.terminal()])
        with patch.object(session, 'close', side_effect=RuntimeError('owner not cleaned')):
            with self.assertRaises(ProviderHTTPError) as caught:
                session.run('fixture', Mock(), artifact_dir='/tmp/http-fixture-not-created',
                            model='fixture', on_abort=abort)
        self.assertTrue(any('cleanup failed' in note for note in caught.exception.__notes__))
        abort.assert_called_once()
        self.assertFalse(session._generation_active)

    def test_context_exit_preserves_primary_error_and_marks_unsafe_cleanup(self):
        session = self.session()
        with patch.object(session, 'close', side_effect=RuntimeError('cleanup')):
            with self.assertRaises(ProviderHTTPError) as caught:
                with session:
                    raise ProviderHTTPError({'kind':'http_status','http_status':503})
        self.assertTrue(caught.exception.__notes__)

    def test_cleanup_failure_without_primary_exception_is_not_hidden(self):
        session = self.session()
        with patch.object(session, 'close', side_effect=RuntimeError('cleanup')):
            with self.assertRaisesRegex(RuntimeError, 'cleanup'):
                with session:
                    pass


if __name__ == '__main__':
    unittest.main()
