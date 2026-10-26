"""Once-per-incident replacement sessions: real scheduler, no provider/checker."""
import json
import unittest

from regen.execution.check_tasks import CheckCancelled
from regen.infrastructure.codex_workspace_session import ProviderFeedbackError
from regen.infrastructure.progress_events import emit_event
from regen.planning.parallel_file_planner import (
    FEEDBACK_RETRY_POLICY, _execution_policy, _feedback_recovery_state,
)
from regen.tests.current import test_parallel_provider_cooldown as fixtures


class FeedbackRetryTests(unittest.TestCase):
    setUp = fixtures.ProviderCooldownTests.setUp
    seal = fixtures.ProviderCooldownTests.seal
    exercise = fixtures.ProviderCooldownTests.exercise

    def failure(self, request_id):
        return ProviderFeedbackError({'kind':'no_feedback', 'http_status':None}, request_id)

    def succeeded(self, theory, request_id):
        # The session/bridge tests separately validate and credit the completed
        # request. Here this is the trusted ledger receipt consumed by scheduling.
        self.budget.ledger.append('provider_feedback_retry_succeeded', theory=theory,
            failed_request_id=request_id, successful_request_id=request_id+'-replacement')

    def test_default_once_replacement_preserves_work_and_passes_request_identity(self):
        def session(theory):
            options = self.session_options[theory][-1]
            self.assertTrue(options['feedback_retry_mode'])
            if self.attempts[theory] == 1:
                self.assertIsNone(options['retry_failed_request_id'])
                emit_event('theory_generation_started')
                self.now += 30
                raise self.failure('failed-1')
            self.assertEqual(options['retry_failed_request_id'], 'failed-1')
            self.succeeded(theory, 'failed-1')
            return self.seal(theory)
        result = self.exercise(session, retries=0)
        self.assertEqual(self.attempts, {'A':2})
        self.assertFalse(result['failures'])
        self.assertNotEqual(*self.paths['A'])
        self.assertEqual(result['generation_budget']['effective_seconds'], 30)
        receipt = json.loads((self.paths['A'][0]/'failure.json').read_text())
        self.assertTrue(receipt['retry_scheduled'])
        self.assertEqual(receipt['failed_request_id'], 'failed-1')
        state = _feedback_recovery_state(self.budget.ledger.rows, 'A')
        self.assertEqual(state['retries'], 0)
        self.assertIsNone(state['failed_request_id'])

    def test_second_failure_pauses_and_resume_cannot_spend_another_retry(self):
        def session(theory):
            raise self.failure('failed-'+str(self.attempts[theory]))
        with self.assertRaises(ProviderFeedbackError) as caught:
            self.exercise(session, retries=3, http_retries=1)
        self.assertEqual(self.attempts, {'A':2})
        self.assertEqual(caught.exception.regen_pause_result['reason'],
                         'provider_feedback_retry_exhausted')
        with self.assertRaises(CheckCancelled) as resumed:
            self.exercise(session, retries=3, http_retries=1,
                          resume_authorization='offline fixture resume')
        self.assertEqual(self.attempts, {'A':2})
        self.assertEqual(resumed.exception.regen_pause_result['reason'],
                         'provider_feedback_retry_exhausted')

    def test_bound_success_resets_allowance_for_later_independent_incident(self):
        def session(theory):
            attempt = self.attempts[theory]
            if attempt == 1:
                raise self.failure('first')
            if attempt == 2:
                self.succeeded(theory, 'first')
                raise self.failure('second')
            self.assertEqual(self.session_options[theory][-1]['retry_failed_request_id'], 'second')
            self.succeeded(theory, 'second')
            return self.seal(theory)
        self.exercise(session, retries=0)
        self.assertEqual(self.attempts, {'A':3})

    def test_atomic_credit_resets_incident_even_if_derived_success_event_is_missing(self):
        def session(theory):
            emit_event('theory_generation_started')
            attempt = self.attempts[theory]
            if attempt == 1:
                self.budget.begin_provider_request(theory, 'first')
                self.now += 10
                self.budget.finish_provider_request(theory, 'first', outcome='failed')
                raise self.failure('first')
            if attempt == 2:
                self.budget.begin_provider_request(theory, 'first-replacement')
                self.budget.finish_provider_request(theory, 'first-replacement')
                self.budget.credit_provider_request('first', 'first-replacement')
                # Simulate losing the derived provider_feedback_retry_succeeded
                # event: the successful credit checkpoint is sufficient.
                self.assertFalse(any(r['kind']=='provider_feedback_retry_succeeded'
                                     for r in self.budget.ledger.rows))
                raise self.failure('second')
            self.assertEqual(self.session_options[theory][-1]['retry_failed_request_id'], 'second')
            self.succeeded(theory, 'second')
            return self.seal(theory)
        result = self.exercise(session, retries=0)
        self.assertEqual(self.attempts, {'A':3})
        self.assertEqual(result['generation_budget']['effective_seconds'], 0)

    def test_interrupted_replacement_is_not_reissued_after_coordinator_resume(self):
        def session(theory):
            if self.attempts[theory] == 1:
                raise self.failure('first')
            raise RuntimeError('client stopped without a complete response receipt')
        result = self.exercise(session)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(self.attempts, {'A':2})
        with self.assertRaises(CheckCancelled) as resumed:
            self.exercise(session, resume_authorization='offline fixture resume')
        self.assertEqual(self.attempts, {'A':2})
        self.assertEqual(resumed.exception.regen_pause_result['reason'],
                         'provider_feedback_retry_interrupted')

    def test_foreign_success_receipt_cannot_grant_extra_attempt(self):
        def session(theory):
            if self.attempts[theory] == 1:
                raise self.failure('first')
            self.succeeded(theory, 'unrelated')
            self.succeeded('B', 'first')
            raise self.failure('second')
        with self.assertRaises(ProviderFeedbackError):
            self.exercise(session)
        self.assertEqual(self.attempts, {'A':2})

    def test_cleanup_failure_never_starts_replacement_owner(self):
        def session(theory):
            error = self.failure('first')
            error.add_note('owned callback could not be joined')
            raise error
        with self.assertRaises(ProviderFeedbackError) as caught:
            self.exercise(session)
        self.assertEqual(self.attempts, {'A':1})
        self.assertEqual(caught.exception.regen_pause_result['reason'],
                         'provider_feedback_cleanup_failed')
        self.assertFalse(any(r['kind']=='provider_feedback_retry_scheduled'
                             for r in self.budget.ledger.rows))

    def test_explicit_legacy_disable_does_not_invent_retry(self):
        def session(theory):
            self.assertFalse(self.session_options[theory][-1]['feedback_retry_mode'])
            raise self.failure('first')
        with self.assertRaises(ProviderFeedbackError) as caught:
            self.exercise(session, feedback_retries=0)
        self.assertEqual(self.attempts, {'A':1})
        self.assertEqual(caught.exception.regen_pause_result['reason'],
                         'provider_feedback_retry_disabled')

    def test_policy_has_default_one_and_rejects_other_allowances(self):
        self.assertEqual(_execution_policy({}, ['A'])['provider_feedback_retries'], 1)
        self.assertEqual(_execution_policy({'provider_feedback_retries':0}, ['A'])[
            'provider_feedback_retries'], 0)
        for value in (-1, 2, True, '1'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _execution_policy({'provider_feedback_retries':value}, ['A'])

    def test_durable_start_distinguishes_unstarted_reservation_from_interrupted_retry(self):
        def row(kind, **data):
            return {'kind':kind, 'data':{'theory':'A', 'retry_policy':FEEDBACK_RETRY_POLICY,
                                        'failed_request_id':'failed', **data}}
        rows = [row('provider_feedback_retry_scheduled')]
        self.assertFalse(_feedback_recovery_state(rows, 'A')['started'])
        rows.append(row('provider_feedback_retry_started'))
        state = _feedback_recovery_state(rows, 'A')
        self.assertTrue(state['started'])
        self.assertEqual(state['retries'], 1)
        rows.append(row('provider_feedback_retry_succeeded'))
        self.assertEqual(_feedback_recovery_state(rows, 'A')['retries'], 0)


if __name__ == '__main__':
    unittest.main()
