"""Terminal failure cannot erase durable status or heartbeat's stage identity."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from regen.infrastructure import progress_events as events


class ProgressEventTests(unittest.TestCase):
    def test_broken_terminal_preserves_transition_heartbeat_and_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            events.configure_event_monitor(root,run_id='test',heartbeat_seconds=0)
            try:
                with patch('builtins.print',side_effect=BrokenPipeError('terminal closed')):
                    events.emit_event('llm_started',stage='file-planner-batch')
                    before=(root/'runner-status.json').read_bytes()
                    for name in ('runner_heartbeat','llm_heartbeat','isabelle_heartbeat','isabelle_batch_heartbeat'):
                        events.emit_event(name)
                    self.assertEqual(before,(root/'runner-status.json').read_bytes())
                    status=json.loads((root/'runner-status.json').read_text())
                    self.assertEqual(status['active_event']['event'],'llm_started')
                    self.assertNotIn('last_heartbeat_at',status)
                    events.close_event_monitor('test-finished')
                status=json.loads((root/'runner-status.json').read_text())
                self.assertFalse(status['running'])
                self.assertEqual([json.loads(x)['event'] for x in (root/'runner-events.ndjson').read_text().splitlines()],
                    ['llm_started','runner_monitor_stopped'])
            finally:
                events.close_event_monitor('test-cleanup')

if __name__=='__main__':unittest.main()
