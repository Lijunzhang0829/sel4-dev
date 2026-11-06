"""New proof outcomes preserve pending work; synthetic sessions confer no proof credit."""
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from regen.execution.checker_policy import PROOF_OR_REFUTATION_POLICY
from regen.planning.document_interface import tool_contracts
from regen.planning.parallel_file_planner import TheoryController, run_theory_session
from regen.tests.current import test_contract_isolation_controller as fixtures


def policy():
    return {'contract_outcome_policy': 'contract-dispositions-v1',
            'unresolved_evidence_policy': PROOF_OR_REFUTATION_POLICY}


class ProofOrRefutationPlannerTests(unittest.TestCase):
    def test_help_exposes_only_checked_outcomes_and_legacy_help_stays_available(self):
        help = tool_contracts(run=policy())
        self.assertNotIn('report_unresolved', help['source_action_schemas'])
        self.assertIn('check_refutation', help['source_action_schemas'])
        self.assertFalse(any(item['proposal']['action'] == 'report_unresolved'
                             for item in help['proof_action_examples']))
        self.assertIn('leaves the contract pending', help['semantics']['outcomes'])
        self.assertIn('experiment budget', help['semantics']['outcomes'])
        self.assertIn('report_unresolved', tool_contracts()['source_action_schemas'])
        self.assertIn('report_unresolved', tool_contracts(run={**policy(),
            'unresolved_evidence_policy': 'native-attempts-v1'})['source_action_schemas'])

    def test_runtime_help_uses_frozen_policy(self):
        controller = TheoryController.__new__(TheoryController)
        controller.run = policy()
        controller.document_mode = True
        result = controller._handle('proof_tool_help', {}, {})
        self.assertNotIn('report_unresolved', result['source_action_schemas'])

    def test_disabled_report_does_not_require_review_or_execute_work(self):
        controller = TheoryController.__new__(TheoryController)
        controller.run = policy()
        controller.stop_at_focus_contract = False
        controller._tool_lock = threading.RLock()
        controller._check_session_active = Mock()
        controller._handle = Mock()
        with patch('regen.planning.review_handoff.require_review_decisions') as review:
            with self.assertRaisesRegex(ValueError, 'disables report_unresolved'):
                controller.handle('proof_action', {'proposal': {
                    'action': 'report_unresolved', 'entry_ids': ['fixed'],
                    'reason': 'two failed candidates'}}, {})
        review.assert_not_called()
        controller._handle.assert_not_called()

    def test_idle_session_pauses_without_unknown_and_preserves_other_targets(self):
        scenario = fixtures.ContractSessionTests()
        self.addCleanup(scenario.doCleanups)
        original = fixtures.fixture
        def proof_policy(root):
            package, state, run = original(root)
            return package, state, {**run, **policy()}
        with patch.object(fixtures, 'fixture', side_effect=proof_policy), patch(
                'regen.planning.theory_outcomes.record_unresolved',
                side_effect=AssertionError('idle cannot create an UNKNOWN')):
            result = scenario.scenario()
        self.assertEqual(result['status'], 'unpublished')
        self.assertEqual(result['session_stop_reason'], 'planner_idle_with_pending_contracts')
        self.assertEqual(result['pending_contract_ids'], ['b', 'c'])
        self.assertEqual(result['contract_dispositions'], {})
        self.assertEqual(scenario.session.turns, 3)
        self.assertEqual(scenario.controller.state['proofs'], {'a': 'by simp'})
        self.assertIn('Try this still-pending contract next: b.', scenario.session.prompts[1])
        self.assertIn('Try this still-pending contract next: c.', scenario.session.prompts[2])
        self.assertNotIn('report_unresolved requires explicit entry_ids', scenario.session.prompts[0])
        self.assertNotIn('report_unresolved/check_refutation', scenario.session.prompts[0])
        self.assertNotIn('Use report_unresolved for', scenario.session.prompts[0])
        self.assertNotIn('Before finish or report_unresolved', scenario.session.prompts[0])
        self.assertIn('report_unresolved is disabled', scenario.session.prompts[0])
        self.assertNotIn('minimum', scenario.session.prompts[1])

    def test_native_rejections_and_candidate_timeouts_continue_same_file_planner(self):
        """Exercise the production continuation loop with disclosed checker fixtures."""
        test = self
        package = {'theory': 'Target', 'file_slot': 'Target.thy', 'contracts': [
            {'contract_id': 'fixed', 'name': 'fixed',
             'exact_statement': 'lemma fixed: "True"', 'contract_owned_context': []}]}
        state = {'status': 'generating', 'graph': {'nodes': [
            {'id': 'fixed', 'name': 'fixed', 'role': 'contract',
             'statement': 'lemma fixed: "True"', 'context': []}]}, 'proofs': {}}
        class Controller:
            options = {'model': 'fixture', 'call_timeout': 10,
                       'check_timeout': 10, 'candidate_timeout': 10}
            run = {'run_id': 'synthetic', **policy()}
            model = 'fixture'
            task_ids = set()
            def __init__(self, root, *args, **kwargs):
                self.root = root; self.theory = 'Target'; self.state = deepcopy(state)
                self.theory_outcome = None; self._proof_work_version = 0
                self.checked = []
            def _state(self, theory): return deepcopy(package), deepcopy(self.state)
            def target_contract_green(self): return False
            def close(self): pass
            def handle(self, name, arguments, metadata):
                if name == 'list_workspace': return {'assigned_theory': 'Target'}
                self.checked.append(arguments['candidate'])
                self._proof_work_version += 1
                if len(self.checked) < 3:
                    return {'status': 'rejected_batch', 'check': {
                        'verdict': 'red', 'timed_out': len(self.checked) == 2,
                        'diagnostic': 'Failed to finish proof' if len(self.checked) == 1
                                      else 'Candidate execution deadline'}}
                self.state['proofs']['fixed'] = 'synthetic accepted proof'
                self.state['status'] = 'sealed'
                return {'status': 'accepted'}
        class Session:
            def __init__(self, **kwargs): self.turns = 0; self.owner = None
            def __enter__(self): test.session = self; return self
            def __exit__(self, *args): pass
            def run(self, prompt, handler, **kwargs):
                self.turns += 1
                if self.owner is None: self.owner = handler.__self__
                test.assertIs(handler.__self__, self.owner)
                result = handler('proof_action', {'candidate': self.turns}, {})
                if self.turns < 3:
                    test.assertEqual(result['status'], 'rejected_batch')
                else:
                    test.assertIn('repair the proof', prompt)
                return {'status': 'completed'}
        with tempfile.TemporaryDirectory() as temporary, patch(
                'regen.planning.parallel_file_planner.TheoryController', Controller), patch(
                'regen.infrastructure.codex_workspace_session.CodexWorkspaceSession', Session), patch(
                'regen.infrastructure.workspace_transcript.audit_workspace_session',
                return_value={'terminal': True}), patch(
                'regen.planning.theory_outcomes.record_unresolved',
                side_effect=AssertionError('a native rejection cannot create UNKNOWN')):
            root = Path(temporary)
            result = run_theory_session(root, 'Target', resources=SimpleNamespace(policy={}),
                executor=object(), settings={'codex_bin': 'fixture', 'reasoning_effort': 'low'},
                directory=root/'session', no_progress_turn_limit=1)
        self.assertEqual(self.session.turns, 3)
        self.assertEqual(result['status'], 'sealed')
        self.assertEqual(result['contract_dispositions'], {})
        self.assertEqual(result['pending_contract_ids'], [])

    def test_global_budget_cancels_worker_with_pending_targets_without_unknown(self):
        from regen.execution.check_tasks import CheckCancelled
        from regen.infrastructure.effective_time_budget import EffectiveTimeBudget, LIMIT_REASON
        from regen.infrastructure.experiment_ledger import Ledger
        from regen.planning.theory_outcomes import current_dispositions, pending_contract_ids
        scenario = fixtures.ContractSessionTests()
        self.addCleanup(scenario.doCleanups)
        original = fixtures.fixture
        def proof_policy(root):
            package, state, run = original(root)
            return package, state, {**run, **policy()}
        now = [0.]
        with tempfile.TemporaryDirectory() as temporary:
            budget = EffectiveTimeBudget(Ledger(Path(temporary)/'budget', 'fixture'),
                seconds=2, monotonic=lambda: now[0])
            self.addCleanup(budget.close)
            budget.register({'Target': ['a', 'b', 'c']}, baseline_green=['a'])
            budget.select('Target', worker_id='Target')
            def charge_turn(turn, controller):
                now[0] = float(turn)
                budget.check()
            with patch.object(fixtures, 'fixture', side_effect=proof_policy), patch(
                    'regen.planning.theory_outcomes.record_unresolved',
                    side_effect=AssertionError('a global budget pause cannot create UNKNOWN')):
                with self.assertRaises(CheckCancelled):
                    scenario.scenario(on_turn=charge_turn)
            self.assertEqual(scenario.session.turns, 2)
            self.assertTrue(scenario.controller.closed)
            self.assertTrue(budget.event.is_set())
            snapshot = budget.snapshot()
            self.assertEqual(snapshot['reason'], LIMIT_REASON)
            self.assertTrue(snapshot['paused'])
            self.assertEqual(snapshot['effective_seconds'], 2)
            self.assertEqual(current_dispositions(scenario.controller), {})
            self.assertEqual(pending_contract_ids(scenario.controller), ['b', 'c'])
            self.assertEqual(scenario.controller.state['proofs'], {'a': 'by simp'})
            self.assertFalse((scenario.controller.root/'contract-dispositions').exists())
            self.assertFalse((scenario.controller.root/'session/result.json').exists())
