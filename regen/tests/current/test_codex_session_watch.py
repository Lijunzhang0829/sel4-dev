"""Root/child separation and incremental reading of observable rollout metadata."""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import timedelta
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.infrastructure.codex_session_watch import ProgressState, RolloutReader, main, timestamp

START = timestamp('2026-09-22T11:00:00Z')
THREAD = '01a0b123-a3c4-7270-ab12-7b44be82c9a7'


def event(seconds, kind, payload):
    return {'timestamp': (START+timedelta(seconds=seconds)).isoformat(), 'type': kind, 'payload': payload}


def started(seconds=0, turn='turn-one'):
    return event(seconds, 'event_msg', {'type': 'task_started', 'turn_id': turn})


class SessionWatchTests(unittest.TestCase):
    def state(self):
        state = ProgressState(THREAD)
        state.consume(started())
        return state

    def test_subagent_notifications_do_not_mask_stalled_root(self):
        state = self.state()
        state.consume(event(20, 'response_item', {'type': 'function_call_output', 'call_id': 'done', 'output': 'SECRET OUTPUT'}))
        state.consume(event(399, 'event_msg', {'type': 'item_completed', 'thread_id': THREAD,
            'turn_id': 'turn-one', 'item': {'type': 'SubAgentActivity', 'kind': 'completed',
                'agent_thread_id': 'child-thread', 'agent_path': '/root/helper'}}))
        state.consume(event(400, 'response_item', {'type': 'agent_message', 'author': '/root/helper',
            'recipient': '/root', 'content': 'PRIVATE MESSAGE'}))
        out = state.snapshot(START+timedelta(seconds=410), 300)
        self.assertEqual(out['status'], 'no_observable_main_progress')
        self.assertEqual(out['seconds_without_main_event'], 390)
        self.assertTrue(out['recent_subagent_activity'])
        self.assertEqual(len(out['subagents']), 1)
        self.assertEqual(out['subagents'][0]['state'], 'finished')
        self.assertNotIn('SECRET OUTPUT', json.dumps(out))
        self.assertNotIn('PRIVATE MESSAGE', json.dumps(out))

    def test_reasoning_counts_only_metadata_and_tracks_tool_lifecycle(self):
        state = self.state()
        state.consume(event(10, 'response_item', {'type': 'reasoning', 'summary': 'HIDDEN CHAIN', 'encrypted_content': 'SECRET'}))
        state.consume(event(20, 'response_item', {'type': 'custom_tool_call', 'call_id': 'call-a',
            'name': 'exec', 'input': 'SECRET TOKEN'}))
        pending = state.snapshot(START+timedelta(seconds=400), 300)
        self.assertEqual(pending['status'], 'no_observable_main_progress')
        self.assertEqual(pending['pending_tools'][0]['call_id'], 'call-a')
        state.consume(event(401, 'response_item', {'type': 'custom_tool_call_output', 'call_id': 'call-a', 'output': 'PRIVATE'}))
        final = state.snapshot(START+timedelta(seconds=402), 300)
        self.assertEqual(final['status'], 'active')
        self.assertEqual(final['pending_tools'], [])
        self.assertEqual(final['main_events_in_turn'], 3)
        self.assertNotIn('SECRET', json.dumps(final))
        self.assertNotIn('HIDDEN CHAIN', json.dumps(final))

    def test_terminals_match_turn_and_new_turn_resets_old_completion(self):
        state = self.state()
        state.consume(event(20, 'event_msg', {'type': 'task_complete', 'turn_id': 'turn-one'}))
        self.assertEqual(state.snapshot(START+timedelta(seconds=999), 300)['status'], 'task_complete')
        state.consume(started(1000, 'turn-two'))
        state.consume(event(1010, 'event_msg', {'type': 'task_complete', 'turn_id': 'turn-one'}))
        self.assertEqual(state.snapshot(START+timedelta(seconds=1020), 300)['status'], 'active')
        state.consume(event(1030, 'event_msg', {'type': 'turn_aborted', 'turn_id': 'turn-two', 'reason': 'SECRET USER MESSAGE'}))
        out = state.snapshot(START+timedelta(seconds=2000), 300)
        self.assertEqual(out['status'], 'turn_aborted')
        self.assertEqual(out['turn_id'], 'turn-two')
        self.assertNotIn('SECRET USER MESSAGE', json.dumps(out))

    def test_usage_settings_and_user_input_are_not_root_progress(self):
        state = self.state()
        for kind, payload in [('token_usage_record', {'usage': {'tokens': 5}}),
                              ('event_msg', {'type': 'token_count'}),
                              ('event_msg', {'type': 'thread_settings_applied'}),
                              ('response_item', {'type': 'message', 'role': 'user', 'content': 'hello'})]:
            state.consume(event(399, kind, payload))
        out = state.snapshot(START+timedelta(seconds=400), 300)
        self.assertEqual(out['status'], 'no_observable_main_progress')
        self.assertIsNone(out['last_main_event_at'])

    def test_foreign_thread_progress_does_not_advance_main_timer(self):
        state = self.state()
        state.consume(event(399, 'event_msg', {'type': 'web_search_end', 'thread_id': 'foreign'}))
        out = state.snapshot(START+timedelta(seconds=400), 300)
        self.assertEqual(out['status'], 'no_observable_main_progress')
        self.assertEqual(out['ignored_foreign_events'], 1)

    def test_as_of_replay_excludes_later_interrupt_and_active_turn(self):
        state = ProgressState(THREAD); cutoff = START+timedelta(seconds=400)
        for item in [started(), event(401, 'event_msg', {'type': 'turn_aborted', 'turn_id': 'turn-one'}),
                     started(500, 'turn-two')]:
            state.consume(item, as_of=cutoff)
        self.assertEqual(state.snapshot(cutoff, 300)['status'], 'no_observable_main_progress')
        self.assertEqual(state.turn_id, 'turn-one')

    def test_cursor_only_reads_appends_and_mtime_is_not_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'session.jsonl'
            path.write_text(json.dumps(started())+'\n')
            reader = RolloutReader(path); first = reader.poll()
            self.assertGreater(first, 0)
            os.utime(path, None)
            self.assertEqual(reader.poll(), 0)
            self.assertEqual(reader.snapshot(START+timedelta(seconds=400), 300)['status'], 'no_observable_main_progress')
            appended = (json.dumps(event(401, 'response_item', {'type': 'message', 'role': 'assistant', 'content': 'PRIVATE'}))+'\n').encode()
            with path.open('ab') as stream:
                stream.write(appended[:20])
            self.assertEqual(reader.poll(), 20)
            self.assertEqual(reader.state.main_event_count, 0)
            with path.open('ab') as stream:
                stream.write(appended[20:])
            self.assertEqual(reader.poll(), len(appended)-20)
            self.assertEqual(reader.state.main_event_count, 1)
            self.assertEqual(reader.poll(), 0)

    def test_rotation_invalid_lines_and_oversized_records_are_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'session.jsonl'; path.write_text(json.dumps(started())+'\n')
            reader = RolloutReader(path); reader.poll()
            replacement = Path(tmp)/'new'; replacement.write_text(json.dumps(started(20, 'turn-new'))+'\n')
            replacement.replace(path)
            reader.poll()
            self.assertEqual(reader.resets, 1)
            self.assertEqual(reader.state.turn_id, 'turn-new')
            with path.open('ab') as stream:
                stream.write(b'not json\n'+b'x'*500+b'\n')
            with patch('regen.infrastructure.codex_session_watch.MAX_LINE_BYTES', 200):
                reader.poll()
            self.assertEqual(reader.invalid_lines, 1)
            self.assertEqual(reader.oversized_lines, 1)

    def test_cli_writes_metadata_only_and_leaves_source_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'session.jsonl'; status = Path(tmp)/'status.json'
            source = json.dumps(started())+'\n'+json.dumps(event(5, 'response_item', {
                'type': 'reasoning', 'summary': 'TOP SECRET CHAIN'}))+'\n'
            path.write_text(source)
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(['--session-file', str(path), '--as-of', '2026-09-22T11:10:00Z', '--status-file', str(status)])
            self.assertEqual(code, 0)
            self.assertEqual(path.read_text(), source)
            self.assertNotIn('TOP SECRET CHAIN', output.getvalue()+status.read_text())
            self.assertEqual(json.loads(status.read_text())['status'], 'no_observable_main_progress')


if __name__ == '__main__':
    unittest.main()


class ClientDiagnosticTests(unittest.TestCase):
    def test_same_turn_only_and_no_private_log_contents(self):
        from regen.infrastructure.codex_session_watch import client_diagnostics
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp)/'Codex.log'
            log.write_text('2026-09-22 07:00:20.000 [warning] idle timeout waiting for websocket turn_id=turn-one retries=2 SECRET\n'
                           '2026-09-22 07:00:30.000 [warning] idle timeout waiting for SSE turn_id=foreign SECRET\n')
            result = client_diagnostics(log, 'turn-one')
            self.assertEqual(len(result['matched_events']), 1)
            self.assertEqual(result['matched_events'][0]['category'], 'websocket_idle_timeout')
            self.assertEqual(result['matched_events'][0]['retry'], 2)
            self.assertNotIn('SECRET', json.dumps(result))
            self.assertEqual(result['request_sent'], 'unknown')
            self.assertEqual(client_diagnostics(log, 'turn-one', as_of=START)['matched_events'], [])

    def test_returned_tool_boundary_and_later_response(self):
        state = ProgressState(THREAD)
        state.consume(started())
        state.consume(event(10, 'response_item', {'type':'function_call', 'name':'exec', 'call_id':'a'}))
        state.consume(event(20, 'response_item', {'type':'function_call_output', 'call_id':'a', 'output':'SECRET'}))
        out = state.snapshot(START+timedelta(seconds=400), 300)
        self.assertEqual(out['diagnostic_boundary'], 'tool_returned_no_later_event')
        self.assertEqual(out['last_tool_result']['tool_name'], 'exec')
        state.consume(event(401, 'response_item', {'type':'message', 'role':'assistant', 'content':'SECRET'}))
        self.assertEqual(state.snapshot(START+timedelta(seconds=402),300)['diagnostic_boundary'], 'awaiting_next_observable_event')
