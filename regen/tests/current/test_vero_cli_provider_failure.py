"""VERO CLI failure classification is pure; no model or native checker needed."""
import unittest

from regen.infrastructure.vero_cli_provider_failure import (
    RECOVERY_WINDOW_SECONDS, classify_vero_cli_failure,
    vero_cli_recovery_decision,
)


class VeroCLIProviderFailureTests(unittest.TestCase):
    def classify(self, message, *, kind='error'):
        event = ({'type':'turn.failed', 'error':{'message':message}} if kind == 'turn.failed'
                 else {'type':'error', 'message':message})
        return classify_vero_cli_failure([event])

    def test_auth_status_requires_intervention_without_replay(self):
        for message in ('HTTP 401 Unauthorized', 'Error: HTTP error 403',
                        '401 Unauthorized', '403 Forbidden'):
            with self.subTest(message=message):
                classified=self.classify(message)
                self.assertEqual(classified['category'],'auth')
                self.assertEqual(vero_cli_recovery_decision(classified,0,0,
                    checkpoint_verified=True),
                    {'retry':False,'reason':'provider_authentication_required'})

    def test_structured_auth_and_transient(self):
        cases=[
            ({'codex_error_info':{'httpConnectionFailed':{'httpStatusCode':401}}},'auth'),
            ({'codexErrorInfo':{'serverOverloaded':{'httpStatusCode':503}}},'transient'),
            ({'codexErrorInfo':'responseStreamDisconnected'},'transient'),
        ]
        for payload,category in cases:
            with self.subTest(payload=payload):
                classified=classify_vero_cli_failure([{'type':'turn.failed','error':payload}])
                self.assertEqual(classified['category'],category)
                self.assertEqual(classified['evidence'],'turn.failed')

    def test_transient_diagnostics_get_bounded_new_invocation_delay(self):
        for message in ('HTTP 503 Service Unavailable', 'The server is overloaded',
                        'stream disconnected before completion',
                        'TLS handshake EOF', 'connection failed'):
            with self.subTest(message=message):
                classified=self.classify(message)
                self.assertEqual(classified['category'],'transient')
                decision=vero_cli_recovery_decision(classified,0,0,
                    checkpoint_verified=True)
                self.assertTrue(decision['retry'])
                self.assertEqual(decision['delay_seconds'],30)
                self.assertEqual(vero_cli_recovery_decision(classified,1,
                    RECOVERY_WINDOW_SECONDS-60,checkpoint_verified=True)['reason'],
                    'provider_recovery_window_exhausted')

    def test_unknown_or_untrusted_checkpoint_cannot_retry(self):
        unknown=self.classify('invalid configuration')
        self.assertEqual(unknown['category'],'unknown')
        self.assertEqual(vero_cli_recovery_decision(unknown,0,0,
            checkpoint_verified=True)['reason'],'provider_failure_unclassified')
        transient=self.classify('HTTP 503')
        self.assertEqual(vero_cli_recovery_decision(transient,0,0,
            checkpoint_verified=False)['reason'],'provider_checkpoint_unverified')
        self.assertEqual(vero_cli_recovery_decision(transient,0,0,
            checkpoint_verified=True,owner_cleaned=False)['reason'],
            'provider_owner_cleanup_failed')

    def test_proof_output_and_recovered_notice_cannot_spoof_terminal_failure(self):
        proof={'type':'item.completed', 'item':{'type':'command_execution',
            'aggregated_output':'HTTP 503; stream disconnected'}}
        self.assertEqual(classify_vero_cli_failure([proof])['category'],'unknown')
        recovered={'type':'error','message':'HTTP 503'}
        final={'type':'turn.failed','error':{'message':'invalid configuration'}}
        self.assertEqual(classify_vero_cli_failure([recovered,final])['category'],'unknown')
        self.assertEqual(classify_vero_cli_failure([recovered,{'type':'turn.failed'}])['category'],
                         'unknown')

    def test_stderr_fallback_is_anchored_and_output_has_no_raw_diagnostic(self):
        raw='secret=https://person:password@example.invalid\nError: HTTP 503 Service Unavailable'
        classified=classify_vero_cli_failure([],raw)
        self.assertEqual(classified,{'category':'transient',
            'failure':{'kind':'http_status','http_status':503},'evidence':'stderr'})
        self.assertNotIn('password',repr(classified))
        self.assertEqual(classify_vero_cli_failure([],
            'proof says HTTP 503 Service Unavailable')['category'],'unknown')


if __name__=='__main__':
    unittest.main()
