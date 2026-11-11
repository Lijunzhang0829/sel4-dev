"""Exercise real coordinator/monitor/budget boundaries with fake theory workers."""
from contextlib import ExitStack, contextmanager, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from regen.infrastructure.progress_events import emit_event
from regen.planning import parallel_file_planner as planner


class GenerationMonitorCoordinatorTests(unittest.TestCase):
    @contextmanager
    def fixture(self, names=('A', 'B', 'C'), *, dependencies=None, initial='pending',
                fail_final_read=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root/'codex'
            binary.write_text('fixture; never executed')
            states = dict.fromkeys(names, initial)
            dependencies = dependencies or dict.fromkeys(names, ())
            mutex = threading.Lock()
            reads = 0

            class Overview:
                selected = list(names)
                run = {'run_id':'test', 'execution_input_kind':'refine-dataset',
                       'checker_context_root':str(root), 'checker_context_id':'fixture'}
                options = {'model':'fixture', 'call_timeout':10, 'check_timeout':10,
                           'candidate_timeout':10}

                def __init__(self, *args, **kwargs):
                    self.root = root

                def list_workspace(self):
                    nonlocal reads
                    with mutex:
                        reads += 1
                        if fail_final_read and reads > 1:
                            raise RuntimeError('final workspace read failed')
                        return {'theories':[
                            {'theory':name, 'contracts':[name], 'selected':True,
                             'status':status, 'checked_contracts':int(status == 'sealed'),
                             'checked_auxiliaries':0, 'required_imports':list(dependencies[name]),
                             'unsealed_providers':[provider for provider in dependencies[name]
                                                   if states[provider] != 'sealed'],
                             'ready_for_proof_action':status == 'pending' and all(
                                 states[provider] == 'sealed' for provider in dependencies[name])}
                            for name, status in states.items()]}

            settings = {'codex_bin':str(binary), 'codex_runtime_root':str(root/'codex-runtime'),
                        'codex_sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}
            with ExitStack() as stack, redirect_stdout(io.StringIO()):
                stack.enter_context(patch.object(planner, 'WorkspaceController', Overview))
                stack.enter_context(patch('regen.infrastructure.audited_llm.model_settings',
                                          return_value=settings))
                stack.enter_context(patch('regen.preparation.checker_context.load_context',
                                          return_value={'checker_context_id':'fixture'}))
                yield root, states, mutex

    def records(self, root):
        directories = list((root/'parallel-sessions').iterdir())
        self.assertEqual(len(directories), 1)
        directory = directories[0]
        status = json.loads((directory/'runner-status.json').read_text())
        events = [json.loads(line) for line in (directory/'runner-events.ndjson').read_text().splitlines()]
        return status, events

    def test_parallel_frontier_waits_for_provider_and_reports_integration_result(self):
        for verified in (False, True):
            with self.subTest(verified=verified), self.fixture(
                    dependencies={'A':(), 'B':(), 'C':('B',)}) as (root, states, mutex):
                a_started = threading.Event()
                b_started = threading.Event()
                c_started = threading.Event()
                order = []

                def session(run_dir, theory, **kwargs):
                    emit_event('theory_generation_started', theory=theory)
                    try:
                        with mutex:
                            order.append(('started', theory))
                        if theory == 'A':
                            a_started.set()
                            self.assertTrue(b_started.wait(3), 'B did not overlap A')
                            self.assertTrue(c_started.wait(3), 'C did not refill the freed B slot')
                        elif theory == 'B':
                            b_started.set()
                            self.assertTrue(a_started.wait(3), 'A did not overlap B')
                        else:
                            with mutex:
                                self.assertEqual(states['B'], 'sealed')
                            c_started.set()
                        with mutex:
                            states[theory] = 'sealed'
                            order.append(('sealed', theory))
                        return {'theory':theory, 'status':'sealed'}
                    finally:
                        emit_event('theory_generation_stopped', theory=theory)

                def integrate(run_dir, **kwargs):
                    with mutex:
                        self.assertEqual(set(states.values()), {'sealed'})
                    status, _ = self.records(root)
                    self.assertEqual(status['workflow_stage'], 'integration')
                    self.assertEqual(status['workflow_state'], 'running')
                    return {'repository_verified':verified}

                with patch.object(planner, 'run_theory_session', side_effect=session), \
                        patch('regen.execution.repository_integration.integrate_repository',
                              side_effect=integrate) as integration:
                    result = planner.run_parallel_workspace(root, budget_mode="effective-time", allow_external_call=True,
                                                            max_parallel_theories=2)
                integration.assert_called_once()
                self.assertLess(order.index(('sealed', 'B')), order.index(('started', 'C')))
                status, events = self.records(root)
                snapshots = [event['workspace'] for event in events
                             if event['event'] == 'workflow_workspace_changed']
                first = next(snapshot for snapshot in snapshots
                             if snapshot['active_theories'] == ['A', 'B'])
                self.assertFalse(first['theories']['C']['ready_for_proof_action'])
                self.assertEqual(first['theories']['C']['unsealed_providers'], ['B'])
                self.assertTrue(any(set(snapshot['active_theories']) == {'A', 'C'}
                                    for snapshot in snapshots))
                self.assertEqual(status['workspace']['active_theories'], [])
                self.assertTrue(all(row['status'] == 'sealed'
                                    for row in status['workspace']['theories'].values()))
                self.assertEqual(status['workflow_stage'], 'repository')
                self.assertEqual(status['workflow_state'], 'completed' if verified else 'paused')
                self.assertFalse(status['running'])
                decisions = [event for event in events if event['event'] == 'workflow_stage_changed']
                self.assertEqual(decisions[-1]['repository_verified'], verified)
                self.assertEqual(decisions[-1]['outcome'], result['status'])
                self.assertEqual(result['generation_budget']['finished'], verified)

    def test_integration_exception_closes_failed_monitor_without_success(self):
        with self.fixture(names=('A',), initial='sealed') as (root, _, _):
            with patch('regen.execution.repository_integration.integrate_repository',
                       side_effect=RuntimeError('integration fixture failed')):
                with self.assertRaisesRegex(RuntimeError, 'integration fixture failed'):
                    planner.run_parallel_workspace(root, budget_mode="effective-time", allow_external_call=True)
            status, events = self.records(root)
            self.assertFalse(status['running'])
            self.assertEqual(status['workflow_stage'], 'integration')
            self.assertEqual(status['workflow_state'], 'failed')
            self.assertFalse(any(event.get('repository_verified') for event in events))

    def test_final_workspace_read_exception_does_not_leave_workflow_running(self):
        with self.fixture(names=('A',), initial='sealed', fail_final_read=True) as (root, _, _):
            with self.assertRaisesRegex(RuntimeError, 'final workspace read failed'):
                planner.run_parallel_workspace(root, budget_mode="effective-time", allow_external_call=True)
            status, events = self.records(root)
            self.assertFalse(status['running'])
            self.assertEqual(status['workflow_state'], 'failed')
            self.assertFalse(any(event.get('repository_verified') for event in events))


if __name__ == '__main__':
    unittest.main()
