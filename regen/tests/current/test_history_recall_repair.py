"""Real journal/history paths and protocol projections; no model/native calls."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from regen.execution.proof_commits import digest, durable
from regen.execution.repository_execution import journal
from regen.execution.shared_proof_workspace import SharedProofWorkspace
from regen.infrastructure.codex_workspace_session import ProgressWatch, SessionStalled
from regen.infrastructure.information_progress import information_payload, proof_work
from regen.planning.attempt_views import attempt_view
from regen.planning.batch_file_planner import action_record, compact_feedback, planner_input
from regen.planning.document_interface import public_view
from regen.planning.document_recall import compact_history_feedback, document_observations, retry_summary
from regen.planning.planner_workspace import read_observation
from regen.tests.current.test_shared_proof_workspace import setup_run


class HistoryViewsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.run, self.packages, _, self.state = setup_run(self.root)
        self.workspace = SharedProofWorkspace(self.root)
        self.directory = self.root/'theories/Source'
        self.candidate = {'action': 'submit_batch', 'document_source': 'exact source ' * 5000,
            'proofs': {'source_contract': 'by fail', 'left': 'by simp'},
            'graph': {'generated_nodes': [{'id': 'left', 'context': ['context A begin']}]}}
        self.feedback = {'status': 'rejected_batch', 'check': {'verdict': 'red',
            'diagnostic': 'residual with assumptions ' * 1200, 'source_content': 'source ' * 5000,
            'log_content': 'log ' * 5000, 'native_proof_state_full': {'goals': ['P ==> Q']}},
            'node_results': [{'node_id': 'source_contract', 'status': 'failed'},
                             {'node_id': 'left', 'status': 'completed_uncommitted'}]}
        self.append_attempt(1)

    def append_attempt(self, number):
        row = action_record(self.candidate, self.feedback, number)
        self.state.setdefault('batch_history', []).append(row)
        self.state['batch_requests'] = number
        durable(self.directory/'batch-requests'/f'{number:06d}'/'proposal.json', self.candidate)
        durable(self.directory/'batch-requests'/f'{number:06d}'/'feedback.json', self.feedback)
        journal(self.root, self.run, self.packages[0]).append('planner-feedback', self.state, {})

    def test_default_summary_and_explicit_full_roundtrip(self):
        summary = self.workspace.read_attempt('Source', 1)
        self.assertNotIn('candidate', summary)
        self.assertNotIn('feedback', summary)
        self.assertEqual(summary['summary']['checker_failed_entry_ids'], ['source_contract'])
        self.assertCountEqual(summary['summary']['submitted_entry_ids'], ['source_contract', 'left'])
        self.assertTrue(summary['summary']['diagnostic_excerpt']['incomplete'])
        full = self.workspace.read_attempt('Source', 1, view='full')
        self.assertEqual(full['candidate'], self.candidate)
        self.assertEqual(full['feedback'], self.feedback)
        self.assertEqual(full['content_id'], summary['content_id'])
        self.assertLess(len(json.dumps(summary)), len(json.dumps(full)) / 10)
        # The controller's ordinary receipt and document vocabulary adapters
        # must not silently recompact an explicitly requested full artifact.
        self.assertEqual(public_view(compact_feedback(full)), full)
        from regen.planning.parallel_file_planner import TheoryController
        receipt = TheoryController._public({'task_id': 'receipt', 'request': {},
            'result': {'status': 'available', 'attempts': [full]}})
        self.assertEqual(public_view(receipt)['result']['attempts'][0], full)
        self.assertEqual(digest(full['candidate']), full['bindings']['proposal_sha256'])
        self.assertEqual(digest(full['feedback']), full['bindings']['feedback_sha256'])

    def test_directory_filter_page_and_snapshot_change(self):
        self.append_attempt(2)
        page = self.workspace.list_attempts('Source', entry_id='left', limit=1)
        self.assertEqual(page['total'], 2)
        self.assertEqual(page['records'][0]['request_number'], 2)
        self.assertNotIn('left', page['records'][0]['checker_failed_entry_ids'])
        second = self.workspace.list_attempts('Source', entry_id='left', offset=1,
            snapshot_commit_id=page['snapshot_commit_id'])
        self.assertEqual(second['records'][0]['request_number'], 1)
        self.assertEqual(self.workspace.list_attempts('Source', entry_id='absent')['total'], 0)
        self.append_attempt(3)
        with self.assertRaisesRegex(ValueError, 'snapshot changed'):
            self.workspace.list_attempts('Source', snapshot_commit_id=page['snapshot_commit_id'])

    def test_head_changes_do_not_create_information(self):
        first = self.workspace.read_attempt('Source', 1)
        self.append_attempt(2)
        second = self.workspace.read_attempt('Source', 1)
        self.assertNotEqual(first['source_commit_id'], second['source_commit_id'])
        self.assertEqual(first['content_id'], second['content_id'])
        args = {'theory': 'Source', 'request_number': 1}
        self.assertEqual(information_payload('read_attempt', args, first),
                         information_payload('read_attempt', args, second))

    def test_single_and_plural_share_views_and_identity(self):
        for view in ('summary', 'full'):
            single = self.workspace.read_attempt('Source', 1, view=view)
            plural = read_observation(self.directory, self.state,
                {'action': 'read_attempts', 'request_numbers': [1], 'view': view})['attempts'][0]
            for key in plural:
                self.assertEqual(single[key], plural[key], (view, key))

    def test_every_view_still_rejects_tampered_artifacts(self):
        durable(self.directory/'batch-requests/000001/proposal.json', {'proofs': {'x': 'by simp'}})
        for view in ('summary', 'full'):
            with self.assertRaises(ValueError):
                self.workspace.read_attempt('Source', 1, view=view)
            with self.assertRaises(ValueError):
                read_observation(self.directory, self.state,
                    {'action': 'read_attempts', 'request_numbers': [1], 'view': view})

    def test_malformed_candidate_can_be_summarized_without_inventing_entries(self):
        value = attempt_view(1, {'proofs': 3, 'document_source': None}, {'check': None})
        self.assertEqual(value['summary']['submitted_entry_ids'], [])
        self.assertEqual(value['summary']['failure_localization'], 'unknown')
        for view in ('other', None):
            with self.assertRaises(ValueError):
                self.workspace.read_attempt('Source', 1, view=view)

    def test_restored_document_does_not_reexpand_legacy_working_set(self):
        attempt = {'request_number': 1, 'candidate': self.candidate, 'feedback': self.feedback}
        self.state.update(batch_requested_attempts=[attempt], batch_target_memory={'left': attempt},
            batch_notes='Investigate the last exact residual; no accepted proof changes.')
        before = deepcopy(self.state)
        context = planner_input(self.packages[0], self.state)
        view = document_observations(context, self.state)
        self.assertEqual(view['planner_notes'], self.state['batch_notes'])
        self.assertIn('recalled_fact_index', view)
        self.assertFalse(view['target_memory']['left']['checker_reported_failed'])
        self.assertEqual(view['target_memory']['left']['last_containing_request'], 1)
        self.assertNotIn('candidate', view['requested_attempts'][0])
        self.assertEqual(self.state, before)
        self.assertEqual(view, document_observations(planner_input(self.packages[0], self.state), self.state))
        nested = {'status': 'available', 'observations': [{'request': {'action': 'read_attempts'},
            'result': {'status': 'available', 'attempts': [attempt]}}]}
        compact = compact_history_feedback(nested, self.state)
        self.assertNotIn('candidate', compact['observations'][0]['result']['attempts'][0])

    def test_controller_document_path_keeps_source_and_exposes_navigation(self):
        from regen.planning.parallel_file_planner import TheoryController
        controller = TheoryController.__new__(TheoryController)
        controller.document_mode = True; controller.theory = 'Source'
        store = Mock(); store.read.return_value = {'source': 'exact draft', 'source_sha256': 'sha'}
        controller._document = Mock(return_value=(store, self.state))
        controller.shared = Mock(); controller.shared._package.return_value = self.packages[0]
        value = controller._handle('read_theory', {'theory': 'Source'}, {})
        self.assertEqual(value['document']['source'], 'exact draft')
        self.assertIn('target_memory', value['observations'])
        self.assertIn('recent_actions', value['observations'])

    def test_navigation_keeps_all_addresses_not_repeated_receipt_metadata(self):
        context = planner_input(self.packages[0], self.state)
        context['query_index'] = [{'query_id': str(i), 'context_node_id': 'left',
            'count': 100, 'last_request': 9, 'task_ids': ['receipt']} for i in range(30)]
        view = document_observations(context, self.state)
        self.assertEqual(len(view['query_index']), 30)
        self.assertEqual(view['query_index'][-1], {'query_id': '29', 'context_node_id': 'left'})
        self.assertEqual(view['recent_actions'][0]['submitted_entry_count'], 2)
        self.assertNotIn('submitted_entry_ids', view['recent_actions'][0])
        self.assertEqual(view['recent_actions'][0]['read_action']['request_numbers'], [1])


class InformationProgressTests(unittest.TestCase):
    def test_task_receipt_changes_cannot_refresh_reads(self):
        watch = ProgressWatch(900)
        args = {'theory': 'T', 'proposal': {'action': 'read_attempts', 'request_numbers': [1]}}
        for number in range(8):
            watch.observe('proof_action', args, {'task_id': str(number), 'status': 'available'}, 0)
        self.assertEqual(watch.generation, 1)
        with self.assertRaises(SessionStalled):
            watch.observe('proof_action', args, {'task_id': 'last', 'status': 'available'}, 0)

    def test_new_context_diagnostic_and_page_are_not_hidden(self):
        watch = ProgressWatch(900)
        for number in range(10):
            watch.observe('read_attempt', {'request_number': 1},
                {'source_commit_id': str(number), 'candidate': {'context': [str(number)]}}, 0)
        self.assertEqual(watch.generation, 10)
        for number in range(10):
            watch.observe('proof_action', {'proposal': {'action': 'read_diagnostic', 'offset': number}},
                {'task_id': str(number), 'text': 'same-looking page'}, 0)
        self.assertEqual(watch.generation, 20)

    def test_do_not_strip_mathematical_ids_or_mutate_receipts(self):
        args = {'proposal': {'action': 'query'}}
        value = {'task_id': 'receipt', 'candidate': {'proofs': {'task_id': 'by simp'}}}
        before = deepcopy(value)
        normalized = information_payload('proof_action', args, value)
        self.assertEqual(normalized[2]['candidate']['proofs']['task_id'], 'by simp')
        self.assertEqual(before, value)

    def test_red_check_is_work_cached_red_and_rejected_calls_are_not(self):
        args = {'proposal': {'action': 'check_theory'}}
        self.assertTrue(proof_work('proof_action', args, {'feedback': {'check': {'verdict': 'red'}}}))
        self.assertFalse(proof_work('proof_action', args, {'feedback': {'cached_failure': True}}))
        self.assertFalse(proof_work('proof_action', args, {'status': 'rejected_request'}))
        self.assertFalse(proof_work('proof_action', {'proposal': {'action': 'resolve_symbols'}}, {'status': 'available'}))

    def test_direct_and_wrapped_observation_have_same_information(self):
        read = {'action': 'read_attempts', 'request_numbers': [1]}
        a = information_payload('proof_action', {'theory': 'T', 'proposal': read}, {'task_id': 'a', 'status': 'available'})
        b = information_payload('proof_action', {'theory': 'T', 'proposal': {'action': 'observe', 'reads': [read]}},
                                {'task_id': 'b', 'status': 'available'})
        self.assertEqual(a, b)

    def test_new_session_may_read_required_evidence_again(self):
        args = {'theory': 'T', 'request_number': 1}
        first, second = ProgressWatch(900), ProgressWatch(900)
        first.observe('read_attempt', args, {'candidate': {'proofs': {}}}, 0)
        second.observe('read_attempt', args, {'candidate': {'proofs': {}}}, 0)
        self.assertEqual((first.generation, second.generation), (1, 1))

    def test_async_receipt_nonce_is_not_information_but_result_is(self):
        args = {'theory': 'T', 'proposal': {'action': 'resolve_symbols', 'symbols': ['A']}}
        first = {'theory': 'T', 'status': 'completed', 'task_id': 'one',
                 'submitted_at': 1, 'result': {'facts': ['A']}}
        second = {**first, 'task_id': 'two', 'submitted_at': 2}
        self.assertEqual(information_payload('submit_check', args, first),
                         information_payload('submit_check', args, second))
        second['result'] = {'facts': ['A', 'B']}
        self.assertNotEqual(information_payload('submit_check', args, first),
                            information_payload('submit_check', args, second))

    def test_receipt_reread_uses_evidence_not_task_address(self):
        first = {'status': 'completed', 'theory': 'T', 'result': {'symbols': ['x']}}
        self.assertEqual(information_payload('get_check_result', {'task_id': 'a'}, first),
                         information_payload('get_check_result', {'task_id': 'b'}, first))
        second = {**first, 'result': {'symbols': ['y']}}
        self.assertNotEqual(information_payload('get_check_result', {'task_id': 'a'}, first),
                            information_payload('get_check_result', {'task_id': 'b'}, second))

    def test_retry_receipt_is_bounded_and_theory_specific(self):
        rows = [{'theory': 'Other', 'retry_scheduled': True, 'diagnostic': 'other'},
                {'theory': 'T', 'retry_scheduled': True, 'error_type': 'SessionStalled',
                 'diagnostic': 'x' * 3000}]
        before = deepcopy(rows)
        summary = retry_summary(rows, 'T')
        self.assertEqual(summary['previous_error_type'], 'SessionStalled')
        self.assertTrue(summary['diagnostic']['incomplete'])
        self.assertEqual(len(summary['diagnostic']['text']), 2000)
        self.assertIsNone(retry_summary(rows, 'Absent'))
        self.assertEqual(before, rows)


class HistoricalRegistryTests(unittest.TestCase):
    def test_frozen_document_registry_accepted_but_changed_help_rejected(self):
        from regen.infrastructure.workspace_transcript import approved_registry
        from regen.planning.parallel_file_planner import tools
        current = [{'type': 'function', **tool} for tool in tools(True)]
        self.assertTrue(approved_registry(current))
        legacy = deepcopy([tool for tool in current if tool['name'] != 'list_attempts'])
        for tool in legacy:
            if tool['name'] == 'read_attempt':
                tool['description'] = 'Read a bound failed candidate and feedback in any theory as reference only.'
                tool['inputSchema']['properties'].pop('view', None)
        self.assertTrue(approved_registry(legacy))
        legacy[-1]['description'] = 'unreviewed extension'
        self.assertFalse(approved_registry(legacy))

    def test_frozen_generic_registries_keep_exact_compatibility(self):
        from regen.infrastructure.workspace_transcript import approved_registry
        from regen.planning.generic_codex_agent import registry
        legacy = deepcopy([{'type': 'function', **tool} for tool in registry()
                           if tool['name'] != 'list_attempts'])
        for tool in legacy:
            if tool['name'] == 'read_attempt':
                tool['description'] = 'Read a bound failed candidate and feedback in any theory as reference only.'
                tool['inputSchema']['properties'].pop('view', None)
        self.assertTrue(approved_registry(legacy))
        older = [tool for tool in legacy if tool['name'] != 'proof_tool_help']
        for tool in older:
            if tool['name'] == 'experiment_status':
                tool['description'] = 'Read deadline, accounting, plan and previous revision identities; optional notes persist across resume.'
        self.assertTrue(approved_registry(older))
        older[-1]['description'] = 'unreviewed generic extension'
        self.assertFalse(approved_registry(older))


if __name__ == '__main__':
    unittest.main()
