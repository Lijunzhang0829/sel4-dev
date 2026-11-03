"""Planning lifecycle and trusted event mirroring; no model or native prover."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from regen.execution.check_tasks import CheckCancelled
from regen.infrastructure.progress_events import (
    current_event_monitor, event_scope, observe_events)
from regen.infrastructure.reviewed_planning_budget import ReviewedPlanningBudget
from regen.tools import reviewed_planning


class PlanningMonitorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    @staticmethod
    def read(path):
        return json.loads(path.read_text())

    @staticmethod
    def events(root):
        return [json.loads(line) for line in
                (root/'runner-events.ndjson').read_text().splitlines()]

    def invoke(self, run):
        output = self.root/'planning'
        argv = ['reviewed_planning', '--input', str(self.root/'input.json'),
                '--output', str(output), '--allow-external-call']
        with patch('sys.argv', argv), patch.object(reviewed_planning, '_run', side_effect=run):
            reviewed_planning.main()
        return output

    def test_serial_stages_without_budget_are_monitored_until_trusted_freeze(self):
        def run(_args, root):
            for stage in ('draft', 'review', 'revision', 'final-review'):
                reviewed_planning.run_command(root, stage, ['test.stage'])
            # Only the controller's checked publication completes the workflow.
            reviewed_planning.write(root/'frozen-plan.json', {'state': 'frozen'})

        def child(command, *, stdout, **kwargs):
            stdout.write('The model claims the complete repository is verified.\n')
            return subprocess.CompletedProcess(command, 0)

        with patch.object(reviewed_planning.subprocess, 'run', side_effect=child):
            output = self.invoke(run)
        status = self.read(output/'runner-status.json')
        self.assertEqual(status['generation_phase'], 'planning')
        self.assertEqual(status['workflow_stage'], 'frozen')
        self.assertEqual(status['workflow_state'], 'completed')
        self.assertFalse(status['running'])
        self.assertEqual(status['stop_reason'], 'completed')
        self.assertNotIn('repository_verified', status)
        transitions = [(e['workflow_stage'], e['workflow_state']) for e in self.events(output)
                       if e['event'] == 'workflow_stage_changed']
        expected = [('preparation', 'running')]
        for stage in ('draft', 'review', 'revision', 'final-review'):
            expected.extend([(stage, 'running'), (stage, 'completed')])
        self.assertEqual(transitions, expected + [('frozen', 'completed')])
        self.assertIsNone(current_event_monitor())

    def test_failed_child_prose_cannot_complete_plan_and_parent_scope_is_restored(self):
        def run(_args, root):
            reviewed_planning.run_command(root, 'revision', ['test.stage'])

        def child(command, *, stdout, **kwargs):
            stdout.write('Plan frozen successfully.\n')
            raise subprocess.CalledProcessError(7, command)

        with event_scope(self.root/'outer', run_id='outer') as outer:
            with patch.object(reviewed_planning.subprocess, 'run', side_effect=child), \
                    self.assertRaises(subprocess.CalledProcessError):
                self.invoke(run)
            self.assertIs(current_event_monitor(), outer)
        output = self.root/'planning'
        status = self.read(output/'runner-status.json')
        self.assertEqual(status['workflow_stage'], 'revision')
        self.assertEqual(status['workflow_state'], 'failed')
        self.assertEqual(status['stop_reason'], 'failed')
        self.assertFalse((output/'frozen-plan.json').exists())
        self.assertFalse(any(e.get('workflow_stage') == 'frozen' for e in self.events(output)))

    def test_budget_cancellation_remains_paused_after_scope_exit(self):
        def run(_args, root):
            reviewed_planning.write(root/'status.json', {'state':'running', 'stage':'review'})
            raise CheckCancelled('effective_time_budget_reached')

        with self.assertRaises(CheckCancelled):
            self.invoke(run)
        status = self.read(self.root/'planning/runner-status.json')
        self.assertEqual(status['workflow_stage'], 'review')
        self.assertEqual(status['workflow_state'], 'paused')
        self.assertEqual(status['stop_reason'], 'paused')
        self.assertFalse(status['running'])

    def test_preparation_failure_is_monitored_before_any_stage_starts(self):
        with self.assertRaisesRegex(ValueError, 'bad binding'):
            self.invoke(lambda *_: (_ for _ in ()).throw(ValueError('bad binding')))
        status = self.read(self.root/'planning/runner-status.json')
        self.assertEqual(status['workflow_stage'], 'preparation')
        self.assertEqual(status['workflow_state'], 'failed')

    def make_budget(self):
        root = self.root/'budgeted'
        root.mkdir()
        budget = ReviewedPlanningBudget(root, seconds=5, binding={
            'input_sha256':'test-input', 'model':'test-model', 'reasoning_effort':'medium'})
        self.addCleanup(lambda: budget.close(failed=True))
        budget.start()
        return budget

    def test_reader_captures_caller_monitor_and_accounts_trusted_events_once(self):
        budget = self.make_budget()
        program = """import json
print('Model prose: workflow completed', flush=True)
for name, extra in [('planning_request_started', {}),
                    ('planning_request_finished', {'outcome':'success'})]:
    print('[regen-event] '+json.dumps({'event':name, 'request_id':'one',
          'run_id':'child', 'generation_phase':'proof', **extra}), flush=True)
"""
        emitted_threads = []
        observer_events = []
        with event_scope(budget.root, run_id='parent', phase='planning') as monitor:
            original = monitor.emit
            def emit(event, fields):
                emitted_threads.append(threading.current_thread().name)
                return original(event, fields)
            with patch.object(monitor, 'emit', side_effect=emit), \
                    patch.object(budget, 'request_event', wraps=budget.request_event) as account, \
                    observe_events(lambda *args: observer_events.append(args)), \
                    patch('regen.infrastructure.progress_events.emit_event',
                          side_effect=AssertionError('mirroring must not re-emit accounting events')):
                budget.run([sys.executable, '-u', '-c', program], io.StringIO())
            self.assertEqual(account.call_count, 2)
            self.assertEqual(emitted_threads, ['planning-budget-events'] * 2)
            self.assertEqual(observer_events, [])
            self.assertNotIn('workflow_state', monitor.status)
            self.assertEqual(budget.requests['one']['outcome'], 'success')
            self.assertIsNone(budget.active_request)
        events = self.events(budget.root)
        mirrored = [e for e in events if e['event'].startswith('planning_request_')]
        self.assertEqual(len(mirrored), 2)
        self.assertTrue(all(e['run_id'] == 'parent' and e['generation_phase'] == 'planning'
                            for e in mirrored))
        for name in ('planning_request_started', 'planning_request_finished'):
            self.assertEqual(sum(row['kind'] == name for row in budget.ledger.rows), 1)

    def test_rejected_accounting_event_is_not_mirrored_as_valid_progress(self):
        budget = self.make_budget()
        program = """import json
print('[regen-event] '+json.dumps({'event':'planning_request_finished',
      'request_id':'never-started', 'outcome':'success'}), flush=True)
"""
        with event_scope(budget.root, run_id='parent', phase='planning'), \
                self.assertRaises((ValueError, CheckCancelled)):
            budget.run([sys.executable, '-u', '-c', program], io.StringIO())
        self.assertFalse(any(e['event'] == 'planning_request_finished'
                             for e in self.events(budget.root)))
        self.assertEqual(budget.stage.reason, 'planning_clock_event_error')


if __name__ == '__main__':
    unittest.main()
