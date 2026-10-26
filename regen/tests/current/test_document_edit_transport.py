"""Proof-only editing crosses the real controller and provider error boundary."""
import json
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator

from regen.tests.current import test_theory_document as document_fixture
from regen.tests.current import test_codex_workspace_session as session_fixture


class ProofEditControllerTests(unittest.TestCase):
    def setUp(self):
        self.case = document_fixture.DocumentControllerTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_entry_edit_is_draft_only_until_native_acceptance(self):
        original = self.case.read()['document']
        entry = self.case.call({'action': 'read_entry', 'entry_id': 'target_contract'})
        self.assertEqual(entry['expected_hash'], original['source_sha256'])
        self.assertEqual(entry['proof_body'].strip(), 'oops')
        self.assertNotIn('source', entry)
        saved = self.case.call({'action': 'edit_proof', 'entry_id': 'target_contract',
                               'expected_hash': entry['expected_hash'], 'proof_body': 'by simp'})
        self.assertEqual(saved['status'], 'draft_saved')
        self.assertFalse(saved['checked'])
        self.assertEqual(self.case.controller._state('Target')[1]['proofs'], {})
        current = self.case.call({'action': 'read_entry', 'entry_id': 'target_contract'})
        self.assertEqual(current['proof_body'].strip(), 'by simp')
        self.assertEqual(current['expected_hash'], saved['source_sha256'])
        result = self.case.call({'action': 'check_theory', 'source_sha256': saved['source_sha256']})
        self.assertEqual(result['feedback']['status'], 'accepted')
        self.assertEqual(self.case.controller._state('Target')[1]['proofs'],
                         {'target_contract': 'by simp'})

    def test_proof_edit_keeps_ownership_and_terminal_guards(self):
        from unittest.mock import patch
        entry = self.case.call({'action': 'read_entry', 'entry_id': 'target_contract'})
        proposal = {'action': 'edit_proof', 'entry_id': 'target_contract',
                    'expected_hash': entry['expected_hash'], 'proof_body': 'by simp'}
        with self.assertRaisesRegex(ValueError, 'assigned theory'):
            self.case.controller.handle('proof_action',
                {'theory': 'Other', 'proposal': proposal}, {})
        store, state = self.case.controller._document()
        with patch.object(self.case.controller, '_document',
                          return_value=(store, {**state, 'status': 'sealed'})):
            with self.assertRaisesRegex(ValueError, 'terminal theory'):
                self.case.call(proposal)
        self.assertEqual(self.case.call({'action': 'read_entry',
                                        'entry_id': 'target_contract'})['expected_hash'],
                         entry['expected_hash'])

    def test_shared_generic_tool_accepts_the_new_actions(self):
        from regen.planning.generic_codex_agent import registry
        tools = registry()
        schema = next(t for t in tools if t['name'] == 'proof_action')['inputSchema']
        validator = Draft202012Validator(schema)
        validator.validate({'theory': 'Target', 'proposal':
                            {'action': 'read_entry', 'entry_id': 'target_contract'}})
        validator.validate({'theory': 'Target', 'proposal': {
            'action': 'edit_proof', 'entry_id': 'target_contract',
            'proof_body': 'by simp', 'expected_hash': 'current-document-hash'}})
        errors = list(validator.iter_errors({'theory': 'Target', 'proposal': {
            'action': 'edit_proof', 'entry_id': 'target_contract', 'proof_body': 'by simp'}}))
        self.assertTrue(errors)


class ProofEditTransportTests(unittest.TestCase):
    def test_structured_edit_failure_reaches_model_without_mutation(self):
        case = document_fixture.DocumentControllerTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        draft = case.read()['document']

        def handler(*_):
            return case.call({'action': 'edit_theory', 'base_sha256': draft['source_sha256'],
                              'edits': [{'old': 'not-in-this-document', 'new': 'by simp'}]})

        fixture = session_fixture.CodexWorkspaceSessionTest()
        with tempfile.TemporaryDirectory() as tmp, fixture.session() as session:
            session.run('test', handler, artifact_dir=tmp, model='test', timeout=2)
            events = [json.loads(line) for line in
                      (Path(tmp) / 'session-transcript.jsonl').read_text().splitlines()]
        responses = [row['message']['result'] for row in events
                     if isinstance(row.get('message', {}).get('result'), dict)
                     and row['message']['result'].get('success') is False]
        self.assertEqual(len(responses), 1)
        error = json.loads(responses[0]['contentItems'][0]['text'])
        self.assertEqual(error['error_type'], 'DocumentEditError')
        self.assertTrue(error['code'])
        self.assertEqual(error['details']['edit_index'], 0)
        self.assertEqual(error['details']['match_count'], 0)
        self.assertEqual(error['details']['current_sha256'], draft['source_sha256'])
        self.assertEqual(case.read()['document']['source_sha256'], draft['source_sha256'])


if __name__ == '__main__':
    unittest.main()
