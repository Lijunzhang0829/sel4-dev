"""Shared lifecycle observations stay separate from proof/budget authority."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from regen.infrastructure.generation_monitor import GenerationMonitor
from regen.infrastructure import progress_events as events


class GenerationMonitorTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    @staticmethod
    def read_status(root):
        return json.loads((root/'runner-status.json').read_text())

    def test_shared_schema_preserves_native_phase_and_scope_identity(self):
        for phase in ('planning', 'proof'):
            root = self.root/phase
            with events.event_scope(root,run_id='run',phase=phase) as monitor:
                self.assertIsInstance(monitor, GenerationMonitor)
                monitor.transition('preflight' if phase == 'planning' else 'integration')
                monitor.emit('checker_result', {'phase':'native','stage':'native-check',
                                               'run_id':'child','generation_phase':'wrong'})
                status = self.read_status(root)
                self.assertEqual(status['generation_phase'], phase)
                self.assertEqual(status['last_event']['phase'], 'native')
                self.assertEqual(status['last_event']['run_id'], 'run')
                self.assertEqual(status['last_event']['generation_phase'], phase)
                self.assertNotEqual(status['workflow_stage'], 'native-check')
            self.assertFalse(self.read_status(root)['running'])
            self.assertNotIn('repository_verified', self.read_status(root))

    def test_parallel_children_and_nested_checks_cannot_close_parent_scope(self):
        barrier = threading.Barrier(2)
        def worker(theory):
            root = self.root/theory
            with events.event_scope(root,run_id='run',theory=theory) as child:
                barrier.wait(timeout=3)
                with events.event_scope(root/'check',run_id='run',theory=theory,task_id='check'):
                    events.emit_event('check_finished', status='green')
                    events.close_event_monitor('nested-callback')
                self.assertIs(events.current_event_monitor(), child)
                self.assertTrue(self.read_status(root)['running'])
            return self.read_status(root)
        with patch('builtins.print'), events.event_scope(self.root/'parent',run_id='run') as parent:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(worker, ['A','B']))
            self.assertIs(events.current_event_monitor(), parent)
            self.assertTrue(self.read_status(self.root/'parent')['running'])
            self.assertEqual({item['theory'] for item in results}, {'A','B'})
            self.assertTrue(all(not item['running'] for item in results))
        self.assertIsNone(events.current_event_monitor())

    def test_workspace_keeps_waiting_consumer_and_updates_only_on_change(self):
        monitor = GenerationMonitor(self.root,run_id='run')
        rows = [{'theory':name,'status':'pending','selected':True,
                 'ready_for_proof_action':name != 'C', 'checked_contracts':0,
                 'unsealed_providers':['A','B'] if name == 'C' else []}
                for name in ('A','B','C')]
        self.assertTrue(monitor.update_workspace(rows,active=['B','A']))
        before = (self.root/'runner-events.ndjson').read_bytes()
        self.assertFalse(monitor.update_workspace(rows,active=['A','B']))
        self.assertEqual(before,(self.root/'runner-events.ndjson').read_bytes())
        current = self.read_status(self.root)['workspace']
        self.assertEqual(current['active_theories'], ['A','B'])
        self.assertEqual(current['theories']['C']['unsealed_providers'], ['A','B'])
        monitor.emit('check_finished', {'theory':'A','status':'green'})
        self.assertEqual(self.read_status(self.root)['workspace']['theories']['A']['checked_contracts'],0)
        for row in rows[:2]:
            row.update(status='sealed',checked_contracts=1)
        rows[2].update(ready_for_proof_action=True,unsealed_providers=[])
        self.assertEqual(monitor.status['workspace']['theories']['A']['checked_contracts'],0)
        monitor.update_workspace(rows,active=['C'])
        current = self.read_status(self.root)['workspace']
        self.assertEqual(current['active_theories'], ['C'])
        self.assertEqual(current['theories']['A']['checked_contracts'],1)
        monitor.close()
        self.assertNotIn('repository_verified',self.read_status(self.root))

    def test_shared_sink_does_not_replace_publication_guard_or_duplicate_events(self):
        observed, publications = [], []
        @contextmanager
        def guard(record):
            publications.append(record)
            raise RuntimeError('budget cutoff')
            yield
        with events.observe_events(lambda event, fields: observed.append(event),publication_guard=guard), \
                events.event_scope(self.root,run_id='run') as monitor, patch('builtins.print'):
            monitor.transition('proof_construction')
            events.emit_event('check_finished',status='green')
            with self.assertRaisesRegex(RuntimeError,'budget cutoff'):
                with events.proof_publication({'commit_id':'candidate'}):
                    self.fail('publication guard was bypassed')
        self.assertEqual(observed,['check_finished'])
        self.assertEqual(publications,[{'commit_id':'candidate'}])
        self.assertNotIn('repository_verified',self.read_status(self.root))


if __name__ == '__main__':
    unittest.main()
