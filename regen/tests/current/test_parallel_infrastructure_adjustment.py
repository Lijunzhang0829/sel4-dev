"""Fault-interval correction retains healthy lanes, normal work and source history."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from regen.execution.proof_commits import digest, durable
from regen.infrastructure.effective_time_budget import EffectiveTimeBudget, POLICY, budget_journal_summary
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.parallel_generation_budget import carry_parallel_budget
from regen.infrastructure.runtime_budget_correction import (
    CORRECTION_BOUNDARY, ERROR_TEXT, apply_runtime_correction,
    interval_correction, plan_terminal_runtime_correction)
from regen.tests.current import test_parallel_effective_budget as fixtures


class InfrastructureCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.source = self.root/'source'; self.target = self.root/'target'
        self.run = {'run_id': 'source'}; self.new = {'run_id': 'target'}
        self.policy = {'strategy': 'and-or', 'native_slots': 2}
        self.theories = {'A': ['a'], 'B': ['b']}
        durable(self.source/'run.json', self.run); durable(self.target/'run.json', self.new)
        durable(self.source/'parallel-workspace-policy.json', self.policy)
        durable(self.target/'workspace-continuation.json', {'source_run': str(self.source)})
        self.now = [0.]
        self.ledger = Ledger(self.source/'effective-generation-budget', 'source', clock=lambda: 1000+self.now[0])
        self.ledger.append('parallel_budget_binding', policy=POLICY, run_sha256=digest(self.run),
            contracts_sha256=digest(self.theories), configuration_sha256=digest(self.policy))
        self.stage = EffectiveTimeBudget(self.ledger, seconds=100, checkpoint_seconds=1,
                                         monotonic=lambda: self.now[0])
        self.stage.register(self.theories)
        self.stage.select('A', worker_id='A'); self.stage.select('B', worker_id='B')
        for second in (5, 10, 15, 20, 25):
            self.now[0] = second; self.stage.check()
        self.now[0] = 30; self.stage.cancel(); self.stage.close()
        self.transcript('A', 10); self.transcript('B', 20)

    def transcript(self, theory, fault, good=None, quote_only=False):
        path = self.source/'parallel-sessions'/'invocation'/theory/'session-transcript.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        def call(call_id, begin, end, action, response, tool='proof_action'):
            params = {'callId': call_id, 'tool': tool, 'arguments': {'theory': theory, 'proposal': {'action': action}}}
            rows.extend([
                {'time_unix': 1000+begin, 'direction': 'in', 'message': {'raw_json': json.dumps({'method': 'item/tool/call', 'params': params})}},
                {'time_unix': 1000+begin, 'direction': 'local', 'message': {'event': 'tool_started', 'call_id': call_id}},
                {'time_unix': 1000+end, 'direction': 'local', 'message': {'event': 'tool_finished', 'call_id': call_id, 'response': response}},
            ])
        if quote_only:
            rows.append({'time_unix': 1000+fault, 'direction': 'in', 'message': {'text': ERROR_TEXT}})
        else:
            call('failure', fault-1, fault, 'edit_theory', {'success': False,
                'contentItems': [{'text': json.dumps({'error_type': 'ValueError', 'message': ERROR_TEXT})}]})
        if good:
            begin, end, action, payload, tool = good
            call('success', begin, end, action, {'success': True, 'contentItems': [{'text': json.dumps(payload)}]}, tool)
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        return path

    def carry(self):
        return carry_parallel_budget(self.source, self.target, authorization='authorized', clock_review={
            'source_tail_event_id': self.ledger.rows[-1]['event_id'], 'resolved_event_ids': [],
            'disposition': 'retain_all_charged_time_no_step_refund'})

    def test_healthy_peer_retained_union_not_sum(self):
        proposal = plan_terminal_runtime_correction(self.source)
        self.assertAlmostEqual(proposal['adjustment']['effective_seconds'], 10)
        self.assertAlmostEqual(proposal['adjustment']['worker_seconds'], 30)
        self.assertEqual(proposal['adjustment']['theory_seconds'], {'A': 20., 'B': 10.})
        self.assertEqual(proposal['corrected_state']['effective_seconds'], 20)
        self.assertEqual(proposal['corrected_state']['worker_seconds'], 30)

    def test_successful_query_is_still_charged(self):
        self.transcript('A', 10, good=(22, 24, 'query', {'status': 'available'}, 'proof_action'))
        proposal = plan_terminal_runtime_correction(self.source)
        self.assertEqual(proposal['adjustment']['effective_seconds'], 8)
        self.assertEqual(proposal['adjustment']['worker_seconds'], 28)
        self.assertEqual(proposal['adjustment']['theory_seconds']['A'], 18)
        self.assertEqual(len(proposal['retained_successful_tool_intervals']), 1)

    def test_successful_read_is_conservatively_retained(self):
        self.transcript('A', 10, good=(14, 15, None, {'document': 'fixture'}, 'read_theory'))
        proposal = plan_terminal_runtime_correction(self.source)
        self.assertEqual(proposal['adjustment']['effective_seconds'], 10)
        self.assertEqual(proposal['adjustment']['worker_seconds'], 29)

    def test_rpc_success_with_business_integrity_error_is_not_success(self):
        self.transcript('A', 10, good=(22, 24, 'check_theory',
            {'status': 'error', 'diagnostic': ERROR_TEXT}, 'proof_action'))
        self.assertEqual(plan_terminal_runtime_correction(self.source)['adjustment']['effective_seconds'], 10)

    def test_successful_edit_refuses_terminal_outage_assumption(self):
        self.transcript('A', 10, good=(22, 24, 'edit_theory', {'status': 'edited'}, 'proof_action'))
        with self.assertRaisesRegex(ValueError, 'successful proof action'):
            plan_terminal_runtime_correction(self.source)

    def test_model_quoted_error_is_not_trusted_failure(self):
        self.transcript('A', 10, quote_only=True); self.transcript('B', 20, quote_only=True)
        with self.assertRaisesRegex(ValueError, 'no trusted'):
            plan_terminal_runtime_correction(self.source)

    def test_atomic_append_idempotent_resume_and_source_unchanged(self):
        proposal = plan_terminal_runtime_correction(self.source); original = list(self.ledger.rows)
        self.carry()
        row = apply_runtime_correction(self.source, self.target, authorization='authorized', proposal=proposal)
        self.assertEqual(row['data']['boundary'], CORRECTION_BOUNDARY)
        repeated = apply_runtime_correction(self.source, self.target, authorization='authorized', proposal=proposal)
        self.assertEqual(row, repeated)
        out = Ledger(self.target/'effective-generation-budget', 'target')
        self.assertEqual(sum(r['data'].get('boundary') == CORRECTION_BOUNDARY for r in out.rows), 1)
        self.assertEqual(budget_journal_summary(out)['effective_seconds'], 20)
        self.now[0] = 10000
        resumed = EffectiveTimeBudget(out, seconds=100, resume_authorization='authorized', monotonic=lambda: self.now[0])
        self.addCleanup(resumed.close)
        self.now[0] += 500
        self.assertEqual(resumed.snapshot()['effective_seconds'], 20)
        resumed.select('A', worker_id='A'); self.now[0] += 5
        self.assertEqual(resumed.snapshot()['effective_seconds'], 25)
        self.assertEqual(Ledger(self.source/'effective-generation-budget', 'source').rows, original)

    def test_stale_or_tampered_proposal_rejected_without_append(self):
        proposal = plan_terminal_runtime_correction(self.source); self.carry()
        proposal['corrected_state']['effective_seconds'] = 0
        with self.assertRaisesRegex(ValueError, 'proposal or source evidence changed'):
            apply_runtime_correction(self.source, self.target, authorization='authorized', proposal=proposal)
        self.assertEqual(Ledger(self.target/'effective-generation-budget', 'target').rows[-1]['kind'], 'parallel_budget_continuation_completed')

    def test_never_apply_to_original_or_launched_run(self):
        proposal = plan_terminal_runtime_correction(self.source)
        with self.assertRaisesRegex(ValueError, 'distinct'):
            apply_runtime_correction(self.source, self.source, authorization='authorized', proposal=proposal)
        self.carry(); durable(self.target/'service-launch.json', {})
        with self.assertRaisesRegex(ValueError, 'already been launched'):
            apply_runtime_correction(self.source, self.target, authorization='authorized', proposal=proposal)

    def test_nonmonotonic_or_ambiguous_charged_clock_rejected(self):
        intervals = [{'theory': 'A', 'start_unix': 1011., 'end_unix': 1029.}]
        rows = deepcopy(self.ledger.rows)
        chosen = next(r for r in rows if r['kind'] == 'time_budget_checkpoint' and r['time_unix'] == 1015)
        chosen['data']['state']['effective_seconds'] += 2
        with self.assertRaisesRegex(ValueError, 'rates or wall/monotonic drift'):
            interval_correction(rows, intervals)

    def test_green_inside_unavailable_interval_refuses_refund(self):
        rows = deepcopy(self.ledger.rows)
        rows.append({'kind': 'time_budget_green', 'time_unix': 1025., 'data': {'theory': 'A', 'contract_id': 'a'}})
        with self.assertRaisesRegex(ValueError, 'accepted contract'):
            interval_correction(rows, [{'theory': 'A', 'start_unix': 1010., 'end_unix': 1030.}])

    def test_healthy_peer_green_and_single_bad_lane_never_refund_union(self):
        rows = deepcopy(self.ledger.rows)
        rows.append({'kind': 'time_budget_green', 'time_unix': 1025., 'data': {'theory': 'B', 'contract_id': 'b'}})
        result = interval_correction(rows, [{'theory': 'A', 'start_unix': 1010., 'end_unix': 1030.}])
        self.assertEqual(result['effective_seconds'], 0)
        self.assertEqual(result['worker_seconds'], 20)

    def test_overlapping_intervals_cannot_double_refund(self):
        intervals = [{'theory': 'A', 'start_unix': 1010., 'end_unix': 1030.},
                     {'theory': 'A', 'start_unix': 1015., 'end_unix': 1025.},
                     {'theory': 'B', 'start_unix': 1020., 'end_unix': 1030.}]
        result = interval_correction(self.ledger.rows, intervals)
        self.assertEqual(result['effective_seconds'], 10)
        self.assertEqual(result['worker_seconds'], 30)

    def test_previously_excluded_lane_time_is_not_refunded_again(self):
        now = [0.]; ledger = Ledger(self.root/'excluded', 'excluded', clock=lambda: 1000+now[0])
        stage = EffectiveTimeBudget(ledger, seconds=100, checkpoint_seconds=1, monotonic=lambda: now[0])
        stage.register(self.theories); stage.select('A', worker_id='A'); stage.select('B', worker_id='B')
        now[0] = 10; handle = stage.begin_exclusion('prefix_preparation', worker_id='A')
        now[0] = 20; stage.end_exclusion(handle)
        now[0] = 30; stage.cancel(); stage.close()
        result = interval_correction(ledger.rows, [
            {'theory': 'A', 'start_unix': 1010., 'end_unix': 1030.},
            {'theory': 'B', 'start_unix': 1020., 'end_unix': 1030.}])
        self.assertEqual(result['worker_seconds'], 20)
        self.assertEqual(result['effective_seconds'], 10)


class RuntimeFailureClockTests(unittest.TestCase):
    setUp = fixtures.ParallelEffectiveBudgetTests.setUp
    event = fixtures.ParallelEffectiveBudgetTests.event

    def test_integrity_failure_pauses_at_receipt_before_cleanup(self):
        self.event('theory_generation_started', 'A'); self.event('theory_generation_started', 'B')
        self.now[0] = 10
        self.event('generation_runtime_integrity_failure', 'A', generation_id='fixture', error_type='CodexRuntimeError', reason='missing frozen binary')
        self.assertEqual(self.stage.reason, 'runtime_integrity_failure')
        self.assertNotIn('A', self.stage.workers)
        self.now[0] = 1000
        state = self.stage.snapshot()
        self.assertEqual(state['effective_seconds'], 10)
        self.assertEqual(state['worker_seconds'], 20)
        self.assertFalse(state['gate_open'])
        self.assertTrue(state['paused'])

    def test_bootstrap_integrity_failure_is_zero_time(self):
        self.now[0] = 10
        self.event('generation_runtime_integrity_failure', 'A', error_type='CodexRuntimeError', reason='mismatch')
        self.now[0] = 30
        self.assertEqual(self.stage.snapshot()['effective_seconds'], 0)


if __name__ == '__main__':
    unittest.main()
