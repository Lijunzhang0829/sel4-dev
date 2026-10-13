"""Versioned sleep auditing without providers, subprocesses or native workers."""
from copy import deepcopy
import json
import unittest

from regen.infrastructure.workspace_transcript import _audit


ABSENT = object()


def event(direction, message):
    return {'time_unix': 0, 'direction': direction,
            'message': {'raw_json': json.dumps(message), 'parsed': message}
            if direction == 'in' else message}


def fixture(version=ABSENT, count=4):
    launch = {'event': 'launch', 'provider': 'codex-app-server', 'synthetic': True}
    if version is not ABSENT:
        launch['sleep_policy_version'] = version
    events = [event('local', launch),
        event('out', {'id': 1, 'method': 'thread/start', 'params': {
            'sandbox': 'read-only', 'approvalPolicy': 'never', 'dynamicTools': [
                {'type': 'function', 'name': 'proof_action', 'description': 'fixture',
                 'inputSchema': {'type': 'object'}}]}}),
        event('in', {'id': 1, 'result': {'thread': {'id': 'thread'}}}),
        event('out', {'id': 2, 'method': 'turn/start', 'params': {'threadId': 'thread'}}),
        event('in', {'id': 2, 'result': {'turn': {'id': 'turn'}}})]
    for number in range(count):
        params = {'threadId': 'thread', 'turnId': 'turn',
                  'item': {'type': 'sleep', 'id': f'sleep-{number}', 'durationMs': 40000}}
        events += [event('in', {'method': method, 'params': deepcopy(params)})
                   for method in ('item/started', 'item/completed')]
    events.append(event('in', {'method': 'turn/completed', 'params': {
        'threadId': 'thread', 'turn': {'id': 'turn', 'status': 'completed'}}}))
    return events


def audit(events):
    raw = ''.join(json.dumps(value)+'\n' for value in events).encode()
    return _audit(raw, require_terminal=True, require_real=False)


def replace_incoming(events, index, mutate):
    message = deepcopy(events[index]['message']['parsed'])
    mutate(message)
    events[index] = event('in', message)


def tool_request(rpc_id=50, turn='turn'):
    return event('in', {'id': rpc_id, 'method': 'item/tool/call', 'params': {
        'threadId': 'thread', 'turnId': turn, 'callId': f'call-{rpc_id}',
        'namespace': None, 'tool': 'proof_action', 'arguments': {}}})


def tool_result(rpc_id=50):
    return event('out', {'id': rpc_id, 'result': {'success': True, 'contentItems': []}})


def start_turn(turn, rpc_id=3):
    return [event('out', {'id': rpc_id, 'method': 'turn/start', 'params': {'threadId': 'thread'}}),
            event('in', {'id': rpc_id, 'result': {'turn': {'id': turn}}})]


def finish_turn(turn):
    return event('in', {'method': 'turn/completed', 'params': {
        'threadId': 'thread', 'turn': {'id': turn, 'status': 'completed'}}})


class WorkspaceSleepPolicyAuditTests(unittest.TestCase):
    def test_unversioned_legacy_over_budget_still_rejected(self):
        with self.assertRaisesRegex(ValueError, 'per-turn sleep budget'):
            audit(fixture())

    def test_explicit_legacy_over_budget_still_rejected(self):
        with self.assertRaisesRegex(ValueError, 'per-turn sleep budget'):
            audit(fixture(1))

    def test_legacy_valid_transcript_still_accepted(self):
        self.assertTrue(audit(fixture(count=1))['terminal'])

    def test_new_policy_allows_four_distinct_legal_sleeps(self):
        self.assertTrue(audit(fixture(2))['terminal'])

    def test_unknown_or_noninteger_policy_fails_even_without_sleep(self):
        for version in (None, True, False, '2', 2.0, 0, 3, {}, []):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, 'sleep policy version'):
                audit(fixture(version, count=0))

    def test_new_policy_wrong_owner_remains_rejected(self):
        events = fixture(2)
        replace_incoming(events, 5, lambda message: message['params'].update(threadId='wrong'))
        with self.assertRaisesRegex(ValueError, 'unbound sleep'):
            audit(events)

    def test_new_policy_duplicate_id_remains_rejected(self):
        events = fixture(2)
        replace_incoming(events, 7, lambda message: message['params']['item'].update(id='sleep-0'))
        with self.assertRaisesRegex(ValueError, 'duplicate sleep'):
            audit(events)

    def test_new_policy_changed_completion_remains_rejected(self):
        events = fixture(2)
        replace_incoming(events, 6, lambda message: message['params']['item'].update(durationMs=30000))
        with self.assertRaisesRegex(ValueError, 'changed sleep completion'):
            audit(events)

    def test_new_policy_single_sleep_limit_is_not_removed(self):
        for duration in (60001, -1, True, 40000.0):
            events = fixture(2)
            replace_incoming(events, 5, lambda message: message['params']['item'].update(durationMs=duration))
            with self.subTest(duration=duration), self.assertRaisesRegex(ValueError, 'maximum 60000'):
                audit(events)

    def test_new_policy_unfinished_sleep_at_turn_completion_rejected(self):
        events = fixture(2, count=1)
        events.pop(6)
        with self.assertRaisesRegex(ValueError, 'unfinished sleep'):
            audit(events)

    def test_new_policy_sleep_after_terminal_turn_rejected(self):
        events = fixture(2, count=1)
        # Both lifecycle events belong to a turn that has already completed.
        events[5:8] = [events[7], events[5], events[6]]
        with self.assertRaisesRegex(ValueError, 'unbound sleep'):
            audit(events)

    def test_failed_session_cannot_become_sealable_under_new_policy(self):
        for version in (ABSENT, 1, 2):
            events = fixture(version, count=1)
            events.insert(-1, event('local', {'event': 'session_failed', 'nonsealable': True,
                'exception_type': 'SessionProtocolError',
                'message': 'duplicate sleep item or per-turn sleep budget exceeded'}))
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, 'failed or unsafe'):
                audit(events)

    def test_failure_marker_after_four_sleeps_remains_rejected_after_relabel(self):
        events = fixture(2)
        events.insert(-1, event('local', {'event': 'session_failed', 'nonsealable': True}))
        with self.assertRaisesRegex(ValueError, 'failed or unsafe'):
            audit(events)

    def test_new_policy_keeps_unknown_builtin_rejection(self):
        events = fixture(2, count=0)
        events.insert(-1, event('in', {'method': 'item/started', 'params': {
            'threadId': 'thread', 'turnId': 'turn', 'item': {'type': 'commandExecution'}}}))
        with self.assertRaisesRegex(ValueError, 'built-in tool'):
            audit(events)

    def test_terminal_before_result_rejected_even_if_result_later_arrives(self):
        for version in (ABSENT, 1, 2):
            events = fixture(version, count=0)
            events.insert(-1, tool_request())
            events.append(tool_result())
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, 'before pending tool results'):
                audit(events)

    def test_finished_turn_rejects_new_tool_even_if_later_replied(self):
        for version in (ABSENT, 1, 2):
            events = fixture(version, count=0) + [tool_request(), tool_result()]
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, 'finished turn'):
                audit(events)

    def test_result_before_terminal_is_accepted_for_old_and_new_policies(self):
        for version in (ABSENT, 1, 2):
            events = fixture(version, count=0)
            events[-1:-1] = [tool_request(), tool_result()]
            with self.subTest(version=version):
                self.assertTrue(audit(events)['terminal'])

    def test_finished_first_turn_does_not_reject_second_turn_tool(self):
        events = fixture(2, count=0)
        events += start_turn('second-turn')
        events += [tool_request(51, 'second-turn'), tool_result(51), finish_turn('second-turn')]
        self.assertTrue(audit(events)['terminal'])

    def test_pending_tool_check_uses_the_completed_turn_owner(self):
        # Ownership isolation: finishing the first turn after its own reply
        # must not be rejected merely because another known turn has a request.
        events = fixture(2, count=0)[:-1] + [tool_request(50)]
        events += start_turn('second-turn')
        events += [tool_request(51, 'second-turn'), tool_result(50), finish_turn('turn'),
                   tool_result(51), finish_turn('second-turn')]
        self.assertTrue(audit(events)['terminal'])


if __name__ == '__main__':
    unittest.main()
