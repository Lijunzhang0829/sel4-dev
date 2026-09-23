"""Exact-ID read failures should tell the planner how to make a valid retry."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from regen.execution.proof_commits import ProofCommits
from regen.execution.repository_execution import validator
from regen.planning.batch_file_planner import ReadIdError, validate_read_ids, _run_batch_file
from regen.tests.current.test_proof_commits import fixture, check


class ReadIdsTest(unittest.TestCase):
    exact = 'premise:3f3507e9713eab2dd48edfab'

    def validate(self, ids, known=None, **options):
        return validate_read_ids(ids, [self.exact] if known is None else known,
            action='read_facts', field='record_ids', maximum=24, index='recalled_fact_index', **options)

    def test_missing_final_character_is_rejected_with_exact_suggestion(self):
        missing = self.exact[:-1]
        with self.assertRaises(ReadIdError) as caught:
            self.validate([missing])
        self.assertEqual(caught.exception.details['invalid_ids'], [missing])
        self.assertEqual(caught.exception.details['suggested_exact_ids'], {missing: [self.exact]})
        self.assertIn(missing, str(caught.exception))
        self.assertIn('explicit new request', str(caught.exception))

    def test_multiple_invalid_ids_are_all_reported_without_returning_partial_records(self):
        missing = self.exact[:-1]
        with self.assertRaises(ReadIdError) as caught:
            self.validate([self.exact, missing, 'totally_unknown'])
        self.assertEqual(caught.exception.details['invalid_ids'], [missing, 'totally_unknown'])
        self.assertEqual(caught.exception.details['suggested_exact_ids'], {missing: [self.exact]})

    def test_one_character_edit_suggests_only_unambiguous_high_similarity(self):
        typo = self.exact.replace('9713', '9714')
        with self.assertRaises(ReadIdError) as caught:
            self.validate([typo])
        self.assertEqual(caught.exception.details['suggested_exact_ids'], {typo: [self.exact]})
        common = 'premise:11111111111111111111111'
        with self.assertRaises(ReadIdError) as caught:
            self.validate([common+'x'], [common+'a', common+'b'])
        self.assertEqual(caught.exception.details['suggested_exact_ids'], {})

    def test_ambiguous_or_short_prefix_does_not_guess(self):
        common = self.exact[:-1]
        for value, known in ((common, [common+'a', common+'b']), ('premise', [self.exact])):
            with self.subTest(value=value), self.assertRaises(ReadIdError) as caught:
                self.validate([value], known)
            self.assertEqual(caught.exception.details['suggested_exact_ids'], {})

    def test_type_and_count_errors_explain_which_constraint_failed(self):
        for value in (None, self.exact, {}):
            with self.subTest(value=value), self.assertRaisesRegex(ReadIdError, 'must be a list'):
                self.validate(value)
        for value in ([], [self.exact]*25):
            with self.subTest(length=len(value)), self.assertRaises(ReadIdError) as caught:
                self.validate(value)
            self.assertEqual(caught.exception.details['received_count'], len(value))
        with self.assertRaises(ReadIdError) as caught:
            self.validate([self.exact, None, 2, []])
        self.assertEqual(caught.exception.details['invalid_id_items'],
                         [{'index':1, 'value':None}, {'index':2, 'value':2}, {'index':3, 'value':[]}])

    def test_query_and_proof_reads_share_exact_id_diagnostics(self):
        for action, field, index, exact in (
            ('read_queries', 'query_ids', 'query_index', 'legacy:123456789abcdef'),
            ('read_proofs', 'node_ids', 'green_node_ids', 'helper:long_proven_helper')):
            with self.subTest(action=action), self.assertRaises(ReadIdError) as caught:
                validate_read_ids([exact[:-1]], [exact], action=action, field=field, maximum=4, index=index)
            self.assertIn(index, str(caught.exception))
            self.assertEqual(caught.exception.details['suggested_exact_ids'], {exact[:-1]: [exact]})

    def test_controller_rejects_typo_then_explicit_retry_returns_entire_fact(self):
        package, initial = fixture()
        fact = {'record_id':self.exact, 'canonical_name':'Trusted.long_lemma',
                'exact_statement':'lemma long_lemma: "P ==> Q"', 'context':['context locale_name'],
                'attributes':['simp'], 'source_class':'frozen_parent', 'extra_complete_field':'retained'}
        initial['batch_facts'] = [fact]
        missing = self.exact[:-1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); directory = root/'theories/Demo'; directory.mkdir(parents=True)
            journal = ProofCommits(directory/'acceptance', {}, validator(package))
            journal.append('initial', initial, {'check':check()})
            setup = ({'run_id':'test-run','execution_models':{'File Planner':'test'}}, package, directory, journal, Mock(), [])
            prompts = []
            def call(*args, **kwargs):
                prompts.append(json.loads(args[1].split('\nINPUT:\n',1)[1]))
                return json.dumps({'action':'read_facts', 'record_ids':[missing if len(prompts)==1 else self.exact]})
            with patch('regen.execution.repository_execution.setup', return_value=setup), \
                 patch('regen.execution.repository_execution.progress', return_value={}), \
                 patch('regen.execution.proof_session_runner.frozen_role_model', return_value='test'), \
                 patch('regen.planning.batch_file_planner.call_llm', side_effect=call), \
                 patch('regen.planning.batch_file_planner.validate_call_artifact', return_value={'sealable_generation':True}):
                result = _run_batch_file(root, 'Demo', max_requests=2)
            self.assertEqual(result['status'], 'checkpoint')
            first = json.loads((directory/'batch-requests/000001/feedback.json').read_text())
            self.assertEqual(first['status'], 'rejected_request')
            self.assertEqual(first['invalid_ids'], [missing])
            self.assertEqual(first['suggested_exact_ids'], {missing:[self.exact]})
            self.assertNotIn('records', first)
            self.assertEqual(prompts[1]['last_feedback'], first)
            second = json.loads((directory/'batch-requests/000002/feedback.json').read_text())
            self.assertEqual(second, {'status':'available', 'records':[fact]})
            state = journal.replay()
            self.assertEqual(state['batch_requested_fact_ids'], [self.exact])
            self.assertEqual(state['batch_requests'], 2)
            self.assertEqual(state['proofs'], {})
            self.assertEqual(state['graph'], initial['graph'])


if __name__ == '__main__':
    unittest.main()
