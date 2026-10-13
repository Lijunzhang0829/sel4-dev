"""Pure error-classifier tests; no provider, network or Isabelle processes."""
from copy import deepcopy
import unittest

from regen.infrastructure.provider_http_failure import classify_provider_http_failure


class ProviderHTTPFailureTests(unittest.TestCase):
    def test_structured_connection_status(self):
        self.assertEqual(classify_provider_http_failure({
            'codexErrorInfo': {'httpConnectionFailed': {'httpStatusCode': 503}},
            'message': 'private endpoint details',
        }), {'kind': 'http_connection', 'http_status': 503})

    def test_actual_capability_space_timeout_with_null_http_status(self):
        error = {'message': 'Reconnecting... 2/5',
            'codexErrorInfo': {'responseStreamDisconnected': {'httpStatusCode': None}},
            'additionalDetails': 'request timed out', 'misalignment': None}
        self.assertEqual(classify_provider_http_failure(error),
            {'kind': 'response_stream', 'http_status': None})

    def test_stream_string_and_object_variants(self):
        for variant in ('responseStreamDisconnected', 'responseStreamConnectionFailed'):
            for info in (variant, {variant: None}, {variant: {}}):
                with self.subTest(info=info):
                    self.assertEqual(classify_provider_http_failure({'codexErrorInfo': info}),
                        {'kind': 'response_stream', 'http_status': None})

    def test_all_failure_statuses_are_classified_not_retry_authorized(self):
        for status in (400, 401, 403, 404, 408, 429, 500, 502, 503, 504, 599):
            with self.subTest(status=status):
                self.assertEqual(classify_provider_http_failure({'httpStatusCode': status}),
                    {'kind': 'http_status', 'http_status': status})

    def test_nested_status_in_other_structured_variant(self):
        self.assertEqual(classify_provider_http_failure({
            'codexErrorInfo': {'serverOverloaded': {'httpStatusCode': 503}}}),
            {'kind': 'provider_capacity', 'http_status': 503})

    def test_malformed_status_not_coerced(self):
        for status in (True, False, '503', None, 0, 200, 302, 600, [], {}):
            with self.subTest(status=status):
                self.assertIsNone(classify_provider_http_failure({'httpStatusCode': status}))

    def test_anchored_http_diagnostics(self):
        for text in ('HTTP 503 Service Unavailable', 'HTTP/1.1 502 Bad Gateway',
                     'HTTP status code: 429', 'HTTP error 401',
                     'unexpected status 503', 'unexpected status code: 504',
                     'request failed with HTTP 503', 'response failed: HTTP 503'):
            with self.subTest(text=text):
                # The colon after "failed" is not an accepted textual prefix;
                # a genuine structured notification still classifies above.
                if text == 'response failed: HTTP 503':
                    self.assertIsNone(classify_provider_http_failure(text))
                else:
                    self.assertEqual(classify_provider_http_failure(text)['kind'], 'http_status')

    def test_existing_transport_phrases(self):
        for text in ('TLS handshake EOF', 'stream disconnected before completion',
                     'error sending request for url (https://private.invalid)',
                     'transport error: network error', 'waiting for network'):
            with self.subTest(text=text):
                self.assertEqual(classify_provider_http_failure({'additionalDetails': text}),
                    {'kind': 'transport', 'http_status': None})

    def test_unrelated_proof_protocol_and_overload_errors(self):
        for error in (None, 503, [],
                      {'message': 'request timed out'},
                      {'message': 'Proof references https://example.invalid/HTTP/503'},
                      {'message': 'Failed to prove lemma about HTTP 503'},
                      {'proof': 'error sending request'},
                      {'codexErrorInfo': {'other': {'proof': {'httpStatusCode': 503}}}}):
            with self.subTest(error=error):
                self.assertIsNone(classify_provider_http_failure(error))

    def test_metadata_has_no_diagnostics_or_mutation(self):
        error = {'codexErrorInfo': {'httpConnectionFailed': {'httpStatusCode': 401}},
            'message': 'token=SECRET https://user:password@private.invalid'}
        original = deepcopy(error)
        self.assertEqual(classify_provider_http_failure(error),
            {'kind': 'http_connection', 'http_status': 401})
        self.assertEqual(error, original)


if __name__ == '__main__':
    unittest.main()
