"""Continuation bindings include the frozen complete-context policy."""
import unittest
from unittest.mock import patch
from regen.execution.repository_execution import continuation_state
from regen.execution.proof_commits import digest

class ContinuationPolicyTests(unittest.TestCase):
    def test_arch_policy_is_verified_not_dropped(self):
        run = {'session_schedule': {}, 'run_id': 'source',
               'implementation': {}, 'complete_context_policy': 'arch-reuse-v1'}
        package = {'package_payload_sha256': 'package'}
        binding = {'run_id': 'source', 'implementation': digest({}),
                   'package_sha256': 'package', 'imports': [],
                   'complete_context_policy': 'arch-reuse-v1'}
        record = {'sequence': 1, 'parent': None, 'binding': binding,
                  'event': 'initial', 'state': {'status': 'generating'},
                  'evidence': {}}
        record['commit_id'] = digest(record)
        origin = {'run': run, 'imports': [], 'commits': [record]}
        with patch('regen.execution.repository_execution.validator', return_value=lambda *a: None):
            self.assertEqual(continuation_state(package, origin)['status'], 'generating')
            run['complete_context_policy'] = 'isolated-v1'
            with self.assertRaisesRegex(ValueError, 'invalid continuation source chain'):
                continuation_state(package, origin)
