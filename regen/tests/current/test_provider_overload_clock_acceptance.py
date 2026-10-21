"""A terminal capacity error may interrupt an open model item without corrupting time."""
import unittest
from unittest.mock import patch

from regen.infrastructure.codex_workspace_session import (
    CodexWorkspaceSession, ProviderOverloaded)


class ProviderOverloadClockTests(unittest.TestCase):
    def test_terminal_overload_closes_open_message_as_interrupted(self):
        session = CodexWorkspaceSession(tools=[{'name':'query', 'description':'test',
            'inputSchema':{'type':'object'}}], http_retry_mode=True)
        session.thread_id = 'thread'; session.turn_id = 'turn'
        session._generation_active = True
        session._model_work_items = {('thread', 'turn', 'agentMessage', 'msg_007')}
        message = {'method':'error','params':{'threadId':'thread','turnId':'turn',
            'willRetry':False,'error':{'codexErrorInfo':'serverOverloaded'}}}
        seen = []
        with patch('regen.infrastructure.progress_events.emit_event',
                   side_effect=lambda event, **data: seen.append((event, data))):
            with self.assertRaises(ProviderOverloaded):
                session._notification(message)
            session._generation_stop('ProviderOverloaded')
        self.assertEqual([event for event, _ in seen],
                         ['generation_items_interrupted', 'theory_generation_stopped'])
        self.assertEqual(seen[0][1]['item_ids'], ['msg_007'])
        self.assertFalse(session._model_work_items)


if __name__ == '__main__':
    unittest.main()
