"""Bounded HTTP recovery: actual scheduler/clock/journal, no external requests."""
import json
import unittest

from regen.infrastructure.codex_workspace_session import ProviderHTTPError, SessionStalled
from regen.infrastructure.progress_events import emit_event
from regen.planning.parallel_file_planner import _execution_policy
from regen.tests.current import test_parallel_provider_cooldown as fixtures


class HTTPRetryTests(unittest.TestCase):
    setUp = fixtures.ProviderCooldownTests.setUp
    seal = fixtures.ProviderCooldownTests.seal
    exercise = fixtures.ProviderCooldownTests.exercise

    def failure(self):
        emit_event('generation_provider_http_failure', failure={'kind':'http_status','http_status':503})
        return ProviderHTTPError({'kind':'http_status','http_status':503})

    def test_first_fault_retries_once_in_new_directory_preserving_charged_time(self):
        def session(theory):
            emit_event('theory_generation_started')
            if self.attempts[theory] == 1:
                self.now += 13
                error = self.failure()
                self.now += 20  # cleanup after the observed fault is not generation
                raise error
            self.now += 11
            return self.seal(theory)
        result = self.exercise(session, http_retries=1, workers=1)
        self.assertEqual(self.attempts, {'A':2})
        self.assertEqual(result['generation_budget']['effective_seconds'], 24)
        self.assertEqual(sum(self.waits), 30)
        self.assertNotEqual(*self.paths['A'])
        receipts = [r for r in self.budget.ledger.rows if r['kind']=='provider_http_retry_scheduled']
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]['data']['retry_number'], 1)
        self.assertTrue(json.loads((self.paths['A'][0]/'failure.json').read_text())['retry_scheduled'])

    def test_repeated_transient_faults_backoff_and_automatically_recover(self):
        def session(theory):
            if self.attempts[theory] <= 6:
                raise self.failure()
            return self.seal(theory)
        result = self.exercise(session, http_retries=1)
        self.assertEqual(self.attempts, {'A':7})
        self.assertEqual(sum(self.waits), 30+60+120+240+300+300)
        self.assertFalse(result['failures'])
        self.assertEqual(result['generation_budget']['effective_seconds'], 0)

    def test_restarting_coordinator_preserves_backoff_not_lifetime_allowance(self):
        def callback(theory):
            if self.attempts[theory] == 1:
                raise self.failure()
            raise ProviderHTTPError({'kind':'http_status','http_status':401})
        with self.assertRaises(ProviderHTTPError):
            self.exercise(callback, http_retries=1)
        def recovered(theory):
            if self.attempts[theory] == 4:
                raise self.failure()
            return self.seal(theory)
        self.exercise(recovered, http_retries=1, resume_authorization='test-resume')
        self.assertEqual(self.attempts, {'A':5})
        self.assertEqual(len([r for r in self.budget.ledger.rows
                              if r['kind']=='provider_http_retry_scheduled']), 3)

    def test_successful_exchange_resets_next_incident_backoff(self):
        def session(theory):
            if self.attempts[theory] < 4:
                if self.attempts[theory] == 3:
                    emit_event('provider_transport_progress', generation_id='new')
                raise self.failure()
            return self.seal(theory)
        self.exercise(session, http_retries=1)
        receipts = [r['data'] for r in self.budget.ledger.rows if r['kind']=='provider_http_retry_scheduled']
        self.assertEqual([r['delay_seconds'] for r in receipts], [30,60,30])

    def test_session_stall_does_not_consume_http_allowance(self):
        def session(theory):
            if self.attempts[theory] == 1:
                raise SessionStalled('fixture stalled')
            if self.attempts[theory] == 2:
                raise self.failure()
            return self.seal(theory)
        result = self.exercise(session, http_retries=1)
        self.assertEqual(self.attempts, {'A':3})
        self.assertFalse(result['failures'])

    def test_cleanup_failure_pauses_without_retry_or_overlapping_owner(self):
        def session(theory):
            error = self.failure(); error.add_note('owner cleanup failed')
            raise error
        with self.assertRaises(ProviderHTTPError) as caught:
            self.exercise(session, http_retries=1)
        self.assertEqual(self.attempts, {'A':1})
        self.assertEqual(caught.exception.regen_pause_result['reason'], 'provider_http_cleanup_failed')
        self.assertFalse(self.waits)

    def test_http_cooldown_does_not_block_ready_peer(self):
        def session(theory):
            if theory == 'A' and self.attempts[theory] == 1:
                raise self.failure()
            if theory == 'B':
                self.assertEqual(self.now, 0)
                emit_event('theory_generation_started'); self.now += 7
            return self.seal(theory)
        result = self.exercise(session, ('A','B'), http_retries=1, workers=1)
        self.assertEqual(result['generation_budget']['effective_seconds'], 7)
        self.assertEqual(self.attempts, {'A':2, 'B':1})
        self.assertEqual(sum(self.waits), 23)

    def test_policy_is_opt_in_and_bounded(self):
        self.assertNotIn('provider_http_retries', _execution_policy({}, ['A']))
        self.assertEqual(_execution_policy({'provider_http_retries':1}, ['A'])['provider_http_retries'], 1)
        for value in (-1, 2, True, '1'):
            with self.assertRaises(ValueError):
                _execution_policy({'provider_http_retries':value}, ['A'])


if __name__ == '__main__':
    unittest.main()
