"""Projection work scales with accepted changes, not idle polling or repository size."""
from concurrent.futures import wait as wait_futures
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from regen.planning.batch_file_planner import _run_batch_file
from regen.planning.parallel_file_planner import run_parallel_workspace
from regen.planning.proof_workspace_agent import WorkspaceController
from regen.tests.current.test_proof_commits import fixture


class ProgressHotpathTests(unittest.TestCase):
    def test_cached_action_publishes_one_projection_on_success_rejection_and_pause(self):
        proposals = [
            {'action':'submit_batch', 'proofs':{'helper':'by simp'}},
            {'action':'invalid-action'},
            {'action':'pause', 'reason':'test boundary'},
        ]
        for proposal in proposals:
            with self.subTest(action=proposal['action']), tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                root = Path(temporary)
                directory = root/'theories/Demo'; directory.mkdir(parents=True)
                package, initial = fixture()
                initial.update(status='generating', runtime_policy={'mode':'file-planner-workspace-v1'})
                journal = Mock()
                journal.replay.return_value = deepcopy(initial)
                journal.append.side_effect = lambda event, state, evidence: deepcopy(state)
                checker = Mock(timeout=900, batch_incremental=True)
                checker.imports.return_value = ['Semantic_Base.Semantic_Root']
                runtime = ({'run_id':'test-run'}, package, directory, journal, checker, [])
                cache = {(str(root.resolve()), 'Demo', 900):runtime}
                stack.enter_context(patch('regen.execution.proof_session_runner.frozen_role_model', return_value='test'))
                projection = stack.enter_context(patch('regen.execution.repository_execution.progress', return_value={'test_progress':True}))
                setup = stack.enter_context(patch('regen.execution.repository_execution.setup', side_effect=AssertionError('cached runtime')))
                stack.enter_context(patch('regen.planning.batch_file_planner.validate_call_artifact', return_value={'sealable_generation':True}))
                stack.enter_context(patch('regen.infrastructure.progress_events.configure_event_monitor'))
                stack.enter_context(patch('regen.infrastructure.progress_events.emit_event'))
                def prepare(package, state, proposal):
                    candidate = deepcopy(state)
                    candidate['proofs'].update(proposal['proofs'])
                    return candidate
                stack.enter_context(patch('regen.execution.proof_batches.prepare_batch', side_effect=prepare))
                stack.enter_context(patch('regen.execution.proof_batches.check_batch', return_value={'check':{'verdict':'green'}}))
                result = _run_batch_file(root, 'Demo', max_requests=1, runtime_cache=cache,
                    decision_source=lambda *args, **kwargs:json.dumps(proposal))
                self.assertTrue(result['test_progress'])
                projection.assert_called_once_with(root)
                setup.assert_not_called()
                self.assertEqual([call.args[0] for call in journal.append.call_args_list],
                                 ['planner-request', 'batch' if proposal['action']=='submit_batch' else 'planner-feedback'])

    def controller(self, *, selected=None, providers=()):
        controller = WorkspaceController.__new__(WorkspaceController)
        controller.selected = ['Target'] if selected is None else selected
        controller.shared = SimpleNamespace(entries=dict.fromkeys(['Target','Provider','Unrelated']))
        package = {'repository_projection':{'schema_version':6,
                    'required_imports':[{'theory':name} for name in providers]}}
        states = {'Target':'generating', 'Provider':'sealed', 'Unrelated':'generating'}
        controller._state = Mock(side_effect=lambda name:(package, {'status':states[name]}))
        return controller, states

    def test_readiness_reads_only_target_and_hard_providers(self):
        controller, states = self.controller(providers=['Provider'])
        self.assertTrue(controller._theory_readiness('Target')['ready_for_proof_action'])
        self.assertEqual([call.args[0] for call in controller._state.call_args_list], ['Target','Provider'])
        states['Provider'] = 'generating'
        self.assertEqual(controller._theory_readiness('Target')['unsealed_providers'], ['Provider'])
        with self.assertRaisesRegex(ValueError, 'outside selected'):
            controller._theory_readiness('Unrelated')

    def test_readiness_preserves_blocking_for_missing_provider(self):
        controller, _ = self.controller(providers=['Missing'])
        self.assertEqual(controller._theory_readiness('Target')['unsealed_providers'], ['Missing'])
        controller._state.assert_called_once_with('Target')

    def test_completion_short_circuit_and_selected_boundary(self):
        controller, _ = self.controller(selected=['Target','Provider'])
        self.assertFalse(controller._all_selected_sealed('Target', {'status':'generating'}))
        controller._state.assert_not_called()
        self.assertTrue(controller._all_selected_sealed('Target', {'status':'sealed'}))
        controller._state.assert_called_once_with('Provider')

    def test_idle_polls_do_not_refresh_and_completion_refills_without_wave_barrier(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root/'codex'; binary.write_text('test fixture')
            states = dict.fromkeys(('A','B','C'), 'pending')
            scans = []; polls = []
            mutex = threading.Lock()
            release_a = threading.Event(); release_b = threading.Event()
            self.addCleanup(release_a.set); self.addCleanup(release_b.set)
            class Overview:
                selected = list(states)
                run = {'run_id':'test', 'execution_input_kind':'refine-dataset',
                       'checker_context_root':str(root), 'checker_context_id':'fixture'}
                options = {'model':'test', 'call_timeout':900, 'check_timeout':900, 'candidate_timeout':120}
                def __init__(self, *args, **kwargs): pass
                def list_workspace(self):
                    with mutex:
                        scans.append(dict(states))
                        return {'theories':[{'theory':name, 'status':state, 'selected':True,
                            'contracts':[name], 'ready_for_proof_action':state=='pending'}
                            for name,state in states.items()]}
            def session(root, theory, **kwargs):
                if theory == 'A':
                    self.assertTrue(release_a.wait(5), 'C should refill while A is active')
                elif theory == 'B':
                    self.assertTrue(release_b.wait(5))
                else:
                    self.assertEqual(states['A'], 'pending')
                    release_a.set()
                with mutex:
                    states[theory] = 'sealed'
                return {'theory':theory, 'status':'sealed'}
            def poll(active, **kwargs):
                polls.append(True)
                if len(polls) <= 3:
                    self.assertEqual(len(scans), 1, 'idle poll must not replay workspace')
                    return set(), set(active)
                release_b.set()
                return wait_futures(active, **kwargs)
            settings = {'codex_bin':str(binary), 'codex_sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}
            with patch('regen.planning.parallel_file_planner.WorkspaceController', Overview), patch(
                    'regen.planning.parallel_file_planner.run_theory_session', side_effect=session), patch(
                    'regen.planning.parallel_file_planner.wait', side_effect=poll), patch(
                    'regen.infrastructure.audited_llm.model_settings', return_value=settings), patch(
                    'regen.preparation.checker_context.load_context', return_value={'checker_context_id':'fixture'}):
                result = run_parallel_workspace(root, allow_external_call=True, integrate=False)
            self.assertEqual(result['status'], 'selected_theories_sealed')
            self.assertFalse(result['failures'])
            self.assertGreaterEqual(len(polls), 4)

    def test_free_slot_sees_provider_seal_before_its_session_returns(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root/'codex'; binary.write_text('test fixture')
            states = {'A':'pending', 'B':'pending'}
            started_b = threading.Event(); sealed_a = threading.Event()
            self.addCleanup(started_b.set)
            class Overview:
                selected = list(states)
                run = {'run_id':'test', 'execution_input_kind':'refine-dataset',
                       'checker_context_root':str(root), 'checker_context_id':'fixture'}
                options = {'model':'test', 'call_timeout':900, 'check_timeout':900, 'candidate_timeout':120}
                def __init__(self, *args, **kwargs): pass
                def list_workspace(self):
                    return {'theories':[{'theory':name, 'status':state, 'selected':True,
                        'contracts':[name], 'ready_for_proof_action':state=='pending' and
                        (name=='A' or states['A']=='sealed')}
                        for name,state in states.items()]}
            def session(root, theory, **kwargs):
                if theory == 'A':
                    states['A'] = 'sealed'; sealed_a.set()
                    self.assertTrue(started_b.wait(5), 'B must start before A returns')
                else:
                    started_b.set(); states['B'] = 'sealed'
                return {'theory':theory, 'status':'sealed'}
            polls = []
            def poll(active, **kwargs):
                if not polls:
                    polls.append(True)
                    self.assertTrue(sealed_a.wait(5))
                    return set(), set(active)
                return wait_futures(active, **kwargs)
            settings = {'codex_bin':str(binary), 'codex_sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}
            with patch('regen.planning.parallel_file_planner.WorkspaceController', Overview), patch(
                    'regen.planning.parallel_file_planner.run_theory_session', side_effect=session), patch(
                    'regen.planning.parallel_file_planner.wait', side_effect=poll), patch(
                    'regen.infrastructure.audited_llm.model_settings', return_value=settings), patch(
                    'regen.preparation.checker_context.load_context', return_value={'checker_context_id':'fixture'}):
                result = run_parallel_workspace(root, allow_external_call=True, integrate=False)
            self.assertEqual(result['status'], 'selected_theories_sealed')
            self.assertFalse(result['failures'])


if __name__ == '__main__':
    unittest.main()
