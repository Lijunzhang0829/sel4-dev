"""Real local stdio peers validate clock evidence, without a model or prover."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from regen.infrastructure.codex_workspace_session import CodexWorkspaceSession
from regen.infrastructure.progress_events import event_scope, generation_exclusion
from regen.tests.current.test_codex_workspace_session import SERVER, TOOLS
from regen.tests.current.test_workspace_event_pump import SERVER as PUMP, TOOLS as PUMP_TOOLS


class EffectiveGenerationEventsTest(unittest.TestCase):
    def rows(self, root):
        return [json.loads(line) for line in (root/'runner-events.ndjson').read_text().splitlines()]

    def session(self, mode='normal'):
        return CodexWorkspaceSession(tools=TOOLS, allow_external_call=True,
            command=[sys.executable, '-u', '-c', SERVER, mode])

    def test_first_model_turn_to_close_not_each_continuation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with event_scope(root, run_id='run', theory='A'), self.session() as session:
                for _ in range(2):
                    session.run('x', lambda *args: {}, artifact_dir=root/'model', model='test', timeout=2)
            rows = [r for r in self.rows(root) if r['event'].startswith('theory_generation_')]
            self.assertEqual([r['event'] for r in rows],
                             ['theory_generation_started', 'theory_generation_stopped'])
            self.assertEqual(rows[0]['generation_id'], rows[1]['generation_id'])
            self.assertTrue(all(r['theory']=='A' and r['run_id']=='run' for r in rows))

    def test_confirmed_failure_stops_clock_before_abort_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with event_scope(root, run_id='run', theory='A'), self.session('timeout') as session:
                def abort():
                    self.assertEqual(self.rows(root)[-1]['event'], 'theory_generation_stopped')
                with self.assertRaises(TimeoutError):
                    session.run('x', lambda *args: {}, artifact_dir=root/'model', model='test',
                                timeout=.15, on_abort=abort)
                self.assertIsNone(session._abort_failure)

    def test_failed_bootstrap_never_starts_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with event_scope(root, run_id='run', theory='A'), self.session() as session, \
                    patch.object(session, '_start', side_effect=RuntimeError('setup failed')):
                with self.assertRaises(RuntimeError):
                    session.run('x', lambda *args: {}, artifact_dir=root/'model', model='test', timeout=2)
            self.assertFalse(any(r['event'].startswith('theory_generation_') for r in self.rows(root)))

    def test_explicit_stream_retry_has_bounded_exclusion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with event_scope(root, run_id='run', theory='A'), self.session('retrying') as session:
                session.run('x', lambda *args: {}, artifact_dir=root/'model', model='test', timeout=2)
            phases = [r for r in self.rows(root) if r.get('category')=='provider_stream_retry']
            self.assertEqual([r['event'] for r in phases],
                             ['generation_exclusion_started', 'generation_exclusion_finished'])
            self.assertEqual(phases[0]['phase_id'], phases[1]['phase_id'])

    def test_retry_followed_by_terminal_does_not_exclude_next_turn(self):
        server = SERVER.replace("if mode=='retrying':", "if mode in ('retrying','retry_terminal'):")
        server = server.replace("   send({'id':100+turn,", """   if mode=='retry_terminal':
    send({'method':'turn/completed','params':{'threadId':'thread-one','turn':{'id':tid,'status':'completed'}}})
    continue
   send({'id':100+turn,""")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = CodexWorkspaceSession(tools=TOOLS, allow_external_call=True,
                command=[sys.executable,'-u','-c',server,'retry_terminal'])
            with event_scope(root, run_id='run', theory='A'), session:
                for _ in range(2):
                    session.run('x', lambda *args: {}, artifact_dir=root/'model', model='test', timeout=2)
                    self.assertIsNone(session._stream_retry_phase)
            phases=[r for r in self.rows(root) if r.get('category')=='provider_stream_retry']
            self.assertEqual([r['event'] for r in phases],
                ['generation_exclusion_started','generation_exclusion_finished']*2)

    def test_timing_write_failure_does_not_prevent_exception_cleanup(self):
        from regen.infrastructure import progress_events
        original = progress_events.emit_event
        def fail(event, **fields):
            if event=='theory_generation_stopped':
                raise OSError('timing disk full')
            return original(event, **fields)
        with tempfile.TemporaryDirectory() as tmp, self.session('timeout') as session, \
                patch.object(progress_events, 'emit_event', side_effect=fail):
            with self.assertRaises(TimeoutError) as caught:
                session.run('x', lambda *args: {}, artifact_dir=tmp, model='test', timeout=.15)
            self.assertIsNotNone(session.process.poll())
            self.assertTrue(session._closed)
            self.assertTrue(any('timing disk full' in n for n in caught.exception.__notes__))

    def test_timing_write_failure_does_not_prevent_normal_close_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self.session()
            try:
                session.run('x', lambda *args: {}, artifact_dir=tmp, model='test', timeout=2)
                with patch('regen.infrastructure.progress_events.emit_event', side_effect=OSError('timing disk full')):
                    with self.assertRaisesRegex(OSError, 'timing disk full'):
                        session.close()
                self.assertIsNotNone(session.process.poll())
                self.assertTrue(session._closed)
            finally:
                session.close()

    def test_nested_preparation_failure_always_closes_both_scopes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with event_scope(root, run_id='run', theory='A'):
                with self.assertRaises(RuntimeError):
                    with generation_exclusion('runtime_setup'), generation_exclusion('heap_preparation'):
                        raise RuntimeError('disk full')
            phases = [r for r in self.rows(root) if r['event'].startswith('generation_exclusion_')]
            self.assertEqual([r['category'] for r in phases],
                             ['runtime_setup','heap_preparation','heap_preparation','runtime_setup'])
            self.assertEqual(phases[0]['phase_id'], phases[3]['phase_id'])
            self.assertEqual(phases[1]['phase_id'], phases[2]['phase_id'])

    def test_callback_reader_preserves_theory_and_overlapping_model_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = CodexWorkspaceSession(tools=PUMP_TOOLS, allow_external_call=True,
                command=[sys.executable, '-u', '-c', PUMP, 'pump', str(root)])
            with event_scope(root, run_id='run', theory='A'), session:
                def callback(*args):
                    (root/'callback-entered').touch()
                    deadline = time.monotonic()+2
                    while time.monotonic()<deadline:
                        if any(r['event']=='model_work_observed' for r in self.rows(root)):
                            return {}
                        time.sleep(.01)
                    self.fail('reader did not publish scoped model overlap')
                session.run('x', callback, artifact_dir=root/'model', model='test', timeout=3)
            phases = [r for r in self.rows(root) if r['event'].startswith('model_work_')]
            self.assertEqual([r['event'] for r in phases],
                             ['model_work_observed','model_work_observation_finished'])
            self.assertTrue(all(r['theory']=='A' and r['run_id']=='run' for r in phases))

    def test_model_terminal_closes_overlap_before_native_callback_returns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = CodexWorkspaceSession(tools=PUMP_TOOLS, allow_external_call=True,
                command=[sys.executable, '-u', '-c', PUMP, 'terminal_queued', str(root)])
            with event_scope(root, run_id='run', theory='A'), session:
                def callback(name, arguments, metadata):
                    if arguments['number']==1:
                        (root/'callback-entered').touch()
                        deadline=time.monotonic()+2
                        while time.monotonic()<deadline:
                            if any(r['event']=='model_work_observation_finished' for r in self.rows(root)):
                                self.assertFalse(session._model_work_observed)
                                return {}
                            time.sleep(.01)
                        self.fail('completed model retained its active lane')
                    return {}
                session.run('x', callback, artifact_dir=root/'model', model='test', timeout=3)


if __name__=='__main__':
    unittest.main()
