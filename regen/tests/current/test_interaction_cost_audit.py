import json
import unittest

from regen.tools.audit_interaction_cost import audit_bytes, audit_baseline_export


def row(time, message, direction='in'):
    return {'time_unix': time, 'direction': direction, 'message': message}


def audit(rows):
    return audit_bytes((''.join(json.dumps(x)+'\n' for x in rows)).encode())


def request(n=1):
    return {'id': n, 'method': 'item/tool/call', 'params': {
        'threadId': 't', 'turnId': 'u', 'callId': str(n), 'tool': 'wait_checks',
        'arguments': {'task_ids': ['check'], 'timeout': 60}}}


def response(text='{"tasks":[{"status":"running"}]}', success=True):
    return {'success': success, 'contentItems': [{'type': 'inputText', 'text': text}]}


def completion(n=1, status='completed', reply=None):
    return {'method': 'item/completed', 'params': {'threadId': 't', 'turnId': 'u',
        'item': {'type': 'dynamicToolCall', 'id': str(n), 'tool': 'wait_checks',
                 'namespace': None, 'status': status, **(reply or response())}}}


def usage(total):
    return {'method': 'thread/tokenUsage/updated', 'params': {'threadId': 't',
        'tokenUsage': {'total': {'totalTokens': total, 'inputTokens': total-1,
                               'outputTokens': 1}}}}


class InteractionCostAuditTests(unittest.TestCase):
    def test_official_export_preserves_unknown_and_incomplete_fields(self):
        raw = json.dumps({'runs': {'baseline': {
            'usage_summary': {'usage': {'usage_complete': False,
                                      'reported_responses': 3, 'usage': {'total_tokens': 90}}},
            'tool_names': {'exec': 3, 'exec_command': 3}}}}).encode()
        result = audit_baseline_export(raw)['sessions'][0]
        self.assertIsNone(result['payload_sizes'])
        self.assertIsNone(result['unchanged_wait_replies'])
        self.assertFalse(result['usage_complete'])
        self.assertEqual(result['response_usage_records'], 3)

    def test_duplicate_usage_is_not_an_extra_request_or_extra_tokens(self):
        result = audit([row(1, usage(10)), row(2, usage(10)), row(3, usage(30))])
        self.assertEqual(result['counts']['unique_usage_updates'], 2)
        self.assertEqual(result['counts']['duplicate_usage_updates'], 1)
        self.assertEqual(result['recorded_usage']['totalTokens'], 30)

    def test_nonmonotonic_usage_is_flagged_not_silently_added(self):
        result = audit([row(1, usage(30)), row(2, usage(10))])
        self.assertEqual(result['usage_excluded_threads'], ['t'])
        self.assertEqual(result['recorded_usage'], {})

    def test_repeated_waits_and_interior_usage_are_separate(self):
        rows = [row(0, usage(10))]
        for n, start in [(1, 10), (2, 100)]:
            rows.extend([row(start, request(n)),
                row(start+10, usage(n*100)),
                row(start+60, {'event': 'tool_finished', 'call_id': str(n),
                              'response': response()}, 'local')])
        result = audit(rows)
        self.assertEqual(result['counts']['identical_wait_replies'], 1)
        self.assertEqual(result['counts']['wait_interior_usage_updates'], 2)
        self.assertEqual(result['wait_interior_recorded_usage']['totalTokens'], 190)

    def test_failed_notification_precedes_sent_response_real_failure_shape(self):
        result = audit([row(0, request()), row(18, completion(status='failed',
                       reply={'success': False, 'contentItems': []})),
                       row(60, {'id': 1, 'result': response()}, 'out')])
        self.assertEqual(result['confirmation_problems'][0]['classification'],
                         'failed_before_response_sent')
        self.assertEqual(result['confirmation_problems'][0]['sent_items'], 1)

    def test_early_matching_receipt_is_still_valid(self):
        result = audit([row(0, request()), row(1, completion()),
                       row(2, {'id': 1, 'result': response()}, 'out')])
        self.assertEqual(result['confirmation_problems'], [])
        self.assertEqual(result['counts']['matching_completion_records'], 1)

    def test_payload_or_success_change_is_rejected(self):
        for reply in [response('different'), response(success=False), response('{ "tasks": [] }')]:
            result = audit([row(0, request()), row(1, {'id': 1, 'result': response()}, 'out'),
                            row(2, completion(reply=reply))])
            self.assertEqual(len(result['confirmation_problems']), 1)

    def test_identical_error_response_is_confirmed(self):
        reply = response('error', False)
        result = audit([row(0, request()), row(1, {'id': 1, 'result': reply}, 'out'),
                        row(2, completion(reply=reply))])
        self.assertEqual(result['counts']['matching_completion_records'], 1)

    def test_partial_tail_is_explicit_corrupt_complete_line_rejected(self):
        self.assertEqual(audit_bytes(b'{')['counts']['partial_tail_lines'], 1)
        with self.assertRaises(ValueError):
            audit_bytes(b'{\n')

    def test_missing_receipt_is_not_success(self):
        result = audit([row(0, request()), row(1, {'id': 1, 'result': response()}, 'out')])
        self.assertEqual(result['counts']['sent_without_completion'], 1)
        self.assertNotIn('matching_completion_records', result['counts'])

    def test_unicode_payload_bytes_are_not_characters(self):
        result = audit([row(0, request()), row(1, {'event': 'tool_finished',
                       'call_id': '1', 'response': response('中文')}, 'local')])
        self.assertEqual(result['payloads']['wait_checks']['text_characters'], 2)
        self.assertGreater(result['payloads']['wait_checks']['bytes'], 6)


if __name__ == '__main__':
    unittest.main()
