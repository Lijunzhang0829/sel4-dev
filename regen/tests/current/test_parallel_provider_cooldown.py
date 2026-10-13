"""Coordinator cooldown regression tests; no model or native processes run."""
from contextlib import ExitStack, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from regen.execution.check_tasks import CheckCancelled
from regen.infrastructure.codex_workspace_session import ProviderOverloaded, SessionStalled
from regen.infrastructure.effective_time_budget import EffectiveTimeBudget
from regen.infrastructure.progress_events import emit_event
from regen.planning import parallel_file_planner as planner


class ProviderCooldownTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.now = 0.
        self.waits = []
        self.states = {}
        self.attempts = {}
        self.paths = {}
        self.stages = []
        self.overview_reads = 0
        self.on_idle = None
        self.lock = threading.Lock()

    def seal(self, theory):
        with self.lock:
            self.states[theory] = 'sealed'
        return {'theory':theory, 'status':'sealed'}

    def exercise(self, callback, names=('A',), *, retries=1, workers=2,
                 http_retries=None, resume_authorization=None):
        self.states = dict.fromkeys(names, 'pending')
        test = self
        root = self.root
        binary = root/'codex'
        binary.write_text('fixture; never executed')

        class Overview:
            selected = list(names)
            run = {'run_id':'test', 'execution_input_kind':'refine-dataset',
                'checker_context_root':str(root), 'checker_context_id':'fixture'}
            options = {'model':'fixture', 'call_timeout':900, 'check_timeout':900,
                'candidate_timeout':120}

            def __init__(self, *args, **kwargs):
                pass

            def list_workspace(self):
                with test.lock:
                    test.overview_reads += 1
                    return {'theories':[{'theory':name, 'contracts':[name],
                        'status':state, 'selected':True,
                        'ready_for_proof_action':state == 'pending'}
                        for name, state in test.states.items()]}

        def session(root, theory, **kwargs):
            with self.lock:
                self.attempts[theory] = self.attempts.get(theory, 0) + 1
                self.paths.setdefault(theory, []).append(kwargs['directory'])
            return callback(theory)

        with ExitStack() as stack, redirect_stdout(io.StringIO()):
            def create_budget(ledger, **kwargs):
                stage = EffectiveTimeBudget(ledger, monotonic=lambda: self.now, **kwargs)
                self.budget = stage

                def idle_wait(seconds):
                    self.waits.append(seconds)
                    self.assertLessEqual(seconds, 1)
                    self.now += seconds
                    if self.on_idle:
                        self.on_idle(stage)
                    return stage.event.is_set()

                # Coordinator checks remain real. Disable only the background
                # watchdog, so simulated monotonic time never races its thread.
                stack.enter_context(patch.object(stage, 'start'))
                stack.enter_context(patch.object(stage.event, 'wait', side_effect=idle_wait))
                return stage

            stack.enter_context(patch.object(planner, 'WorkspaceController', Overview))
            stack.enter_context(patch.object(planner, 'run_theory_session', side_effect=session))
            stack.enter_context(patch.object(planner, 'monotonic', side_effect=lambda: self.now))
            stack.enter_context(patch('regen.infrastructure.effective_time_budget.EffectiveTimeBudget',
                side_effect=create_budget))
            stack.enter_context(patch('regen.infrastructure.audited_llm.model_settings', return_value={
                'codex_bin':str(binary),
                'codex_runtime_root':str(root/'codex-runtime'),
                'codex_sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}))
            stack.enter_context(patch('regen.preparation.checker_context.load_context',
                return_value={'checker_context_id':'fixture'}))
            policy = {'stages':self.stages, 'session_stall_retries':retries}
            if http_retries is not None:
                policy['provider_http_retries'] = http_retries
            return planner.run_parallel_workspace(root, allow_external_call=True, integrate=False,
                max_parallel_theories=workers,
                execution_policy=policy, resume_authorization=resume_authorization)

    def test_overloaded_lane_does_not_delay_ready_peer_or_reset_clock(self):
        peer_started = threading.Event()
        replacement_started = threading.Event()

        def session(theory):
            if theory == 'A':
                if self.attempts[theory] == 1:
                    raise ProviderOverloaded('fixture capacity')
                self.assertEqual(self.now, 30)
                emit_event('theory_generation_started')
                self.now += 7
            elif theory == 'B':
                emit_event('theory_generation_started')
                peer_started.set()
                self.assertTrue(replacement_started.wait(3), 'ready C was blocked by A cooldown')
            else:
                self.assertTrue(peer_started.wait(3))
                self.assertEqual(self.now, 0)
                self.assertFalse(self.waits, 'coordinator waited before refilling its free slot')
                self.assertEqual(self.attempts['A'], 1)
                self.now = 25
                replacement_started.set()
            return self.seal(theory)

        result = self.exercise(session, ('A','B','C'))
        self.assertEqual(result['status'], 'selected_theories_sealed')
        self.assertFalse(result['failures'])
        self.assertEqual(self.attempts, {'A':2, 'B':1, 'C':1})
        # The healthy peer's 25s plus A's retry 7s remain charged; the 5s
        # all-lanes-idle cooldown is excluded without resetting prior work.
        self.assertEqual(result['generation_budget']['effective_seconds'], 32)
        self.assertEqual(sum(self.waits), 5)
        self.assertNotEqual(*self.paths['A'])
        failure = json.loads((self.paths['A'][0]/'failure.json').read_text())
        self.assertTrue(failure['retry_scheduled'])

    def test_all_lanes_cooling_wait_then_retry_instead_of_exiting(self):
        starts = []

        def session(theory):
            starts.append(self.now)
            if self.attempts[theory] <= 3:
                raise ProviderOverloaded('fixture capacity')
            return self.seal(theory)

        result = self.exercise(session, retries=3)
        self.assertEqual(starts, [0, 30, 90, 210])
        self.assertEqual(result['session_attempts'], {'A':4})
        self.assertEqual(result['generation_budget']['effective_seconds'], 0)
        self.assertLess(self.overview_reads, 10, 'idle cooldown repeatedly replayed the workspace')

    def test_cooldown_keeps_explicit_stage_barrier(self):
        self.stages = [['A'], ['B']]
        order = []

        def session(theory):
            order.append((theory, self.now))
            if theory == 'A' and self.attempts[theory] == 1:
                raise ProviderOverloaded('fixture capacity')
            if theory == 'B':
                self.assertEqual(self.states['A'], 'sealed')
            return self.seal(theory)

        self.exercise(session, ('A','B'))
        self.assertEqual(order, [('A',0), ('A',30), ('B',30)])

    def test_retry_exhaustion_does_not_block_unstaged_ready_theory(self):
        def session(theory):
            if theory == 'A':
                raise ProviderOverloaded('fixture capacity')
            self.assertEqual(self.now, 0)
            return self.seal(theory)

        result = self.exercise(session, ('A','B'), workers=1)
        self.assertEqual(self.attempts, {'A':2, 'B':1})
        self.assertEqual(set(result['failures']), {'A'})
        self.assertEqual(result['failures']['A']['error_type'], 'ProviderOverloaded')

    def test_cleanup_failure_never_creates_cooldown_or_new_owner(self):
        def session(theory):
            error = ProviderOverloaded('fixture capacity')
            error.add_note('cleanup failed; owner may still be alive')
            raise error

        result = self.exercise(session)
        self.assertEqual(self.attempts, {'A':1})
        self.assertFalse(self.waits)
        self.assertIn('A', result['failures'])

    def test_idle_cooldown_honors_cancellation_without_retry(self):
        self.on_idle = lambda stage: stage.cancel('operator_pause')
        with self.assertRaises(CheckCancelled) as caught:
            self.exercise(lambda theory: (_ for _ in ()).throw(ProviderOverloaded('capacity')))
        self.assertEqual(self.attempts, {'A':1})
        self.assertEqual(self.waits, [1])
        self.assertEqual(caught.exception.regen_pause_result['reason'], 'operator_pause')
        self.assertEqual(caught.exception.regen_pause_result['generation_budget']['effective_seconds'], 0)

    def test_session_stalled_retry_stays_immediate_and_bounded(self):
        result = self.exercise(lambda theory: (_ for _ in ()).throw(SessionStalled('timeout')))
        self.assertEqual(self.attempts, {'A':2})
        self.assertFalse(self.waits)
        self.assertEqual(result['failures']['A']['error_type'], 'SessionStalled')


if __name__ == '__main__':
    unittest.main()
