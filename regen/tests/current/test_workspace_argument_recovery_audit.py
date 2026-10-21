"""Argument rejection provenance: no providers or native processes."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator, ValidationError

from regen.infrastructure.workspace_transcript import (
    _audit, record_workspace_action, verify_workspace_action)


def event(direction, message):
    return {'direction': direction, 'message':
            {'raw_json': json.dumps(message), 'parsed': message}
            if direction == 'in' else message}


def raw(events):
    return ''.join(json.dumps(e)+'\n' for e in events).encode()


def fixture():
    schema = {'type': 'object', 'required': ['theory', 'proposal'],
              'properties': {'theory': {'const': 'Demo'},
                             'proposal': {'type': 'object', 'required': ['action']}}}
    proposal = {'action': 'submit_batch', 'proofs': {'helper': 'by simp'}}
    bad = {'id': 50, 'method': 'item/tool/call', 'params': {
        'threadId': 'thread', 'turnId': 'turn', 'callId': 'bad',
        'tool': 'proof_action', 'arguments': {'theory': 'Demo', 'proposal': {}}}}
    error = next(Draft202012Validator(schema).iter_errors(bad['params']['arguments']))
    rejection = {'error_type': 'ToolArgumentValidationError', 'code': 'invalid_tool_arguments',
                 'message': error.message, 'path': list(error.absolute_path),
                 'schema_path': list(error.absolute_schema_path)}
    good = deepcopy(bad)
    good['id'] = 51
    good['params'].update(callId='good', arguments={'theory': 'Demo', 'proposal': proposal})
    events = [event('local', {'event': 'launch', 'synthetic': True, 'model': 'test',
                             'tool_argument_policy_version': 2}),
        event('out', {'id': 1, 'method': 'thread/start', 'params': {
            'sandbox': 'read-only', 'approvalPolicy': 'never', 'dynamicTools': [
                {'type': 'function', 'name': 'proof_action', 'inputSchema': schema}]}}),
        event('in', {'id': 1, 'result': {'thread': {'id': 'thread'}}}),
        event('out', {'id': 2, 'method': 'turn/start', 'params': {'threadId': 'thread', 'model': 'test'}}),
        event('in', {'id': 2, 'result': {'turn': {'id': 'turn'}}}), event('in', bad),
        event('local', {'event': 'tool_arguments_rejected', 'thread_id': 'thread',
                       'turn_id': 'turn', 'call_id': 'bad', 'tool': 'proof_action', 'error': rejection}),
        event('out', {'id': 50, 'result': {'success': False, 'contentItems': [
            {'type': 'inputText', 'text': json.dumps(rejection)}]}}), event('in', good)]
    return events, good, proposal


class WorkspaceArgumentRecoveryAuditTest(unittest.TestCase):
    def audit(self, events, terminal=False):
        return _audit(raw(events), require_terminal=terminal, require_real=False)

    def test_corrected_action_can_record_and_verify(self):
        events, request, proposal = fixture()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'session.jsonl'
            path.write_bytes(raw(events))
            metadata = {'thread_id': 'thread', 'turn_id': 'turn', 'call_id': 'good',
                        'raw_request': request, 'transcript_path': str(path),
                        'transcript_offset': path.stat().st_size}
            prefix = Path(directory)/'action'
            record_workspace_action(prefix, 'fixture', proposal, metadata, theory='Demo', model='test')
            self.assertFalse(verify_workspace_action(prefix, require_real=False)['sealable_generation'])

    def test_pending_invalid_is_not_bindable(self):
        events, _, _ = fixture()
        report = self.audit(events[:6])
        self.assertEqual(report['tool_requests'], [])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'session.jsonl'
            path.write_bytes(raw(events[:6]))
            metadata = {'thread_id': 'thread', 'turn_id': 'turn', 'call_id': 'bad',
                        'raw_request': events[5]['message']['parsed'], 'transcript_path': str(path),
                        'transcript_offset': path.stat().st_size}
            with self.assertRaisesRegex(ValueError, 'exact tool request'):
                record_workspace_action(Path(directory)/'bad', 'fixture', {}, metadata,
                                        theory='Demo', model='test')

    def test_other_action_can_bind_with_pending_invalid(self):
        events, _, _ = fixture()
        self.assertEqual(len(self.audit(events[:6]+events[8:])['tool_requests']), 1)

    def test_invalid_submit_check_cannot_authorize_proof(self):
        events, _, _ = fixture()
        events[1]['message']['params']['dynamicTools'][0]['name'] = 'submit_check'
        request = deepcopy(events[5]['message']['parsed'])
        request['params']['tool'] = 'submit_check'
        events[5] = event('in', request)
        self.assertEqual(self.audit(events[:6])['tool_requests'], [])

    def test_notification_ownership_requires_confirmed_start(self):
        events, _, _ = fixture()
        notification = event('in', {'method': 'item/started', 'params': {
            'threadId': 'thread', 'turnId': 'turn',
            'item': {'type': 'dynamicToolCall', 'tool': 'proof_action', 'arguments': {}}}})
        # Valid early notification binds only through the durable response.
        self.audit(events[:4]+[notification]+events[4:])
        with self.assertRaisesRegex(ValueError, 'unknown or finished owner'):
            self.audit(events[:4]+[notification])
        foreign = deepcopy(notification['message']['parsed'])
        foreign['params']['turnId'] = 'foreign'
        with self.assertRaisesRegex(ValueError, 'unknown or finished owner'):
            self.audit(events[:5]+[event('in', foreign)]+events[5:])

    def test_missing_rejection_and_forged_success_are_rejected(self):
        for mode in ('missing', 'success', 'mismatch', 'duplicate'):
            events, _, _ = fixture()
            if mode == 'missing':
                events.pop(6)
            elif mode == 'success':
                events[7]['message']['result']['success'] = True
            elif mode == 'mismatch':
                events[6]['message']['error']['message'] = 'forged'
            else:
                events.insert(7, deepcopy(events[6]))
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.audit(events)

    def test_old_policy_does_not_reinterpret_rejection(self):
        for version in (None, 1):
            events, _, _ = fixture()
            if version is None:
                del events[0]['message']['tool_argument_policy_version']
            else:
                events[0]['message']['tool_argument_policy_version'] = version
            with self.assertRaises(ValidationError):
                self.audit(events)

    def test_unknown_policy_is_rejected(self):
        for version in (True, 3, '2', None):
            events, _, _ = fixture()
            events[0]['message']['tool_argument_policy_version'] = version
            with self.assertRaisesRegex(ValueError, 'argument policy'):
                self.audit(events)

    def test_terminal_requires_invalid_failure_response(self):
        events, _, _ = fixture()
        events[0]['message']['pending_terminal_policy_version'] = 2
        terminal = event('in', {'method': 'turn/completed', 'params': {
            'threadId': 'thread', 'turn': {'id': 'turn', 'status': 'completed'}}})
        with self.assertRaisesRegex(ValueError, 'completed terminal'):
            self.audit(events[:7]+[terminal], terminal=True)
        self.assertTrue(self.audit(events[:8]+[terminal], terminal=True)['terminal'])

    def test_deferred_terminal_allows_late_display_completion_not_rpc(self):
        events, _, _ = fixture()
        events[0]['message']['pending_terminal_policy_version'] = 2
        terminal = event('in', {'method': 'turn/completed', 'params': {
            'threadId': 'thread', 'turn': {'id': 'turn', 'status': 'completed'}}})
        completion = event('in', {'method': 'item/completed', 'params': {
            'threadId': 'thread', 'turnId': 'turn',
            'item': {'type': 'dynamicToolCall', 'tool': 'proof_action', 'arguments': {}}}})
        sequence = events[:6]+[terminal]+events[6:8]+[completion]
        self.assertTrue(self.audit(sequence, terminal=True)['terminal'])
        with self.assertRaisesRegex(ValueError, 'finished turn'):
            self.audit(sequence+events[8:], terminal=True)


if __name__ == '__main__':
    unittest.main()
