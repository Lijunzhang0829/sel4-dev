"""Failure injection for planning audit, timeout rollback and exact credit."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from regen.infrastructure import reviewed_planning_budget as budgeting
from regen.infrastructure.audited_llm import audit_events
from regen.infrastructure.llm_transcript import call_codex, validate_call_artifact
from regen.infrastructure.planning_retry import retry_planning_request, PlanningFeedbackTimeout
from regen.tests.current import test_audited_provider_lifecycle as lifecycle


class PlanningGapRepairs(unittest.TestCase):
    def test_malformed_provider_stream_is_rejected_despite_success_exit_and_final(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = lifecycle.AuditedProviderLifecycleTest().fake(root, 'success')
            fake.write_text(fake.read_text().replace("print('live stderr'", "print('{broken',flush=True)\nprint('live stderr'"))
            prefix = root/'call'
            with patch.dict(os.environ, {'REGEN_CODEX_BIN':str(fake)}):
                with self.assertRaisesRegex(RuntimeError, 'malformed'):
                    call_codex('test', prefix, timeout=5, clean_runtime=True)
            meta = json.loads(prefix.with_suffix('.meta.json').read_text())
            self.assertEqual(meta['returncode'], 0)
            self.assertFalse(meta['sealable_generation'])
            with self.assertRaises(ValueError):
                validate_call_artifact(prefix)
            # Non-object JSON is malformed too, not an unobserved tool event.
            prefix.with_suffix('.ndjson').write_text('[]\nnull\n')
            self.assertEqual(audit_events(prefix.with_suffix('.ndjson'))['parse_errors'], 2)

    def test_only_timeout_resends_once_and_schema_errors_do_not(self):
        attempts = []
        def request(number):
            attempts.append(number)
            raise PlanningFeedbackTimeout('no feedback')
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(PlanningFeedbackTimeout, 'two consecutive'):
            retry_planning_request(request, retry_gap=0)
        self.assertEqual(attempts, [0,1])
        attempts.clear()
        def bad(number):
            attempts.append(number)
            raise ValueError('bad audit')
        with redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
            retry_planning_request(bad, retry_gap=0)
        self.assertEqual(attempts, [0])

    def test_planning_retry_credit_excludes_only_charged_failed_time_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = [0.]
            original = budgeting.EffectiveTimeBudget
            with patch.object(budgeting, 'EffectiveTimeBudget',
                    side_effect=lambda *a,**kw:original(*a,**kw,monotonic=lambda:now[0])):
                budget = budgeting.ReviewedPlanningBudget(Path(tmp), seconds=100, binding={})
            try:
                budget.stage.begin_planning()
                event = budget.request_event
                event('planning_request_started', {'request_id':'failed'})
                now[0] = 10
                exclusion = budget.stage.begin_exclusion('provider_recovery')
                now[0] = 15
                budget.stage.end_exclusion(exclusion)
                event('planning_request_finished', {'request_id':'failed','outcome':'timeout'})
                event('planning_request_started', {'request_id':'success','retry_of':'failed'})
                now[0] = 18
                event('planning_request_finished', {'request_id':'success','outcome':'success'})
                pair = {'failed_request_id':'failed','successful_request_id':'success'}
                event('planning_request_retry_succeeded', pair)
                event('planning_request_retry_succeeded', pair)
                snapshot = budget.stage.snapshot()
                self.assertEqual(snapshot['effective_seconds'], 3)
                self.assertEqual(snapshot['planning_seconds'], 3)
                self.assertEqual(snapshot['excluded_infrastructure_seconds'], 10)
                receipts = [r for r in budget.ledger.rows if r['kind']=='time_budget_checkpoint'
                            and r['data']['boundary']=='planning_request_retry_credit']
                self.assertEqual(len(receipts), 1)
                self.assertIn('provider_request_accounting', receipts[0]['data']['state'])
                budget.stage.close()
                restored = original(budget.ledger, seconds=100, include_planning=True,
                                    resume_authorization='test-only recovery', monotonic=lambda:now[0])
                self.assertEqual(restored.snapshot()['effective_seconds'], 3)
                self.assertEqual(restored.snapshot()['planning_seconds'], 3)
                restored.close()
            finally:
                budget.close()

    def test_workspace_timeout_rolls_back_partial_files_and_submission_without_reusing_queries(self):
        from regen.planning import workspace_planner as planner
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); workspace = root/'workspace'; workspace.mkdir()
            (workspace/'NOTES.md').write_text('before request')
            harness = SimpleNamespace(state={'sequence':3,'inspected':[]}, submitted=None,
                lock=threading.RLock(), state_path=root/'control/state.json', active_call=None)
            seen = []
            def call(settings, policy, output, public, files, api, prompt, number):
                seen.append((prompt, number, (files/'NOTES.md').read_text()))
                prefix = root/f'call-{number}'
                if number == 1:
                    (files/'NOTES.md').write_text('partial model response')
                    harness.state.update(sequence=8, inspected=['failed call'])
                    harness.submitted = {'call_number':number,'proposal':'partial'}
                    return {'timed_out':True,'returncode':124}, prefix
                self.assertIsNone(harness.submitted)
                self.assertEqual(harness.state, {'sequence':8,'inspected':[]})
                return {'timed_out':False,'returncode':0}, prefix
            with patch.object(planner, 'call_worker', side_effect=call), \
                    patch.object(planner, 'verify_worker_call'), \
                    patch('regen.infrastructure.planning_retry.time.sleep'), \
                    patch('regen.infrastructure.planning_retry.discard_timeout_output'), redirect_stdout(io.StringIO()):
                _, _, number = planner.call_worker_with_retry({'codex_sha256':'hash'}, {}, root,
                    root/'public', workspace, root/'api', 'same prompt', 0, harness)
            self.assertEqual(number, 2)
            self.assertEqual(seen, [('same prompt',1,'before request'),('same prompt',2,'before request')])


if __name__ == '__main__':
    unittest.main()
