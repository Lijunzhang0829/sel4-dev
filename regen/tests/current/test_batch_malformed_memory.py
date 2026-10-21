"""Malformed actions and legacy derived observations must remain non-authoritative."""
import json
from pathlib import Path
import tempfile
import unittest

from regen.planning.batch_file_planner import (apply_feedback, planner_input, restore_batch_memory,
    query_identity, read_bound_json)
from regen.execution.proof_commits import digest
from regen.tests.current.test_proof_commits import fixture


class BatchMalformedMemoryTest(unittest.TestCase):
    def test_invalid_query_shapes_remain_rejected_observations_without_crashing_next_input(self):
        for request in ([], None, 'not an object', 7):
            with self.subTest(request=request):
                package, state = fixture()
                state = apply_feedback(state, {'action': 'query', 'request': request},
                    {'status': 'rejected_request', 'diagnostic': 'query request must be an object'}, 1)
                view = planner_input(package, state)
                self.assertEqual(view['last_feedback']['status'], 'rejected_request')
                self.assertEqual(view['recent_actions'][0]['status'], 'rejected_request')
                self.assertEqual(state['proofs'], {})

    def test_invalid_proofs_shapes_do_not_crash_rejection_or_create_target_memory(self):
        for proofs in (7, ['invented'], 'not a proof map', True):
            with self.subTest(proofs=proofs):
                package, state = fixture()
                state = apply_feedback(state, {'action': 'submit_batch', 'proofs': proofs},
                    {'status': 'rejected_request', 'diagnostic': 'proofs must be an object'}, 1)
                view = planner_input(package, state)
                self.assertEqual(view['last_feedback']['status'], 'rejected_request')
                self.assertEqual(view['target_memory'], {})
                self.assertEqual(state['proofs'], {})

    def test_legacy_derived_accepted_status_cannot_override_native_red(self):
        _, state = fixture()
        state.update(batch_requests=1,
            batch_history=[{'request_number': 1, 'action': 'submit_batch', 'status': 'accepted'}])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = root/'batch-requests/000001'
            request.mkdir(parents=True)
            (request/'proposal.json').write_text(json.dumps(
                {'action': 'submit_batch', 'proofs': {'helper': 'by simp'}}))
            (request/'check.json').write_text(json.dumps(
                {'check': {'verdict': 'red', 'diagnostic': 'native check failed'}}))
            restored = restore_batch_memory(root, state)
            self.assertEqual(restored['batch_history'][0]['status'], 'rejected_batch')
            from regen.planning.attempt_memory import target_attempts
            self.assertEqual(dict(target_attempts(restored))['helper']['feedback']['status'], 'rejected_batch')
            self.assertEqual(restored['proofs'], {})
            self.assertEqual(restore_batch_memory(root, restored), restored)


    def test_recovery_rejects_modified_bound_feedback_and_proposal(self):
        for changed_name in ('feedback', 'proposal'):
            with self.subTest(artifact=changed_name), tempfile.TemporaryDirectory() as tmp:
                _, state = fixture()
                state['batch_requests'] = 1
                proposal = {'action': 'query', 'request': {'goal_query': True, 'context_node_id': 'contract'}}
                feedback = {'status': 'available', 'kind': 'fact_selection', 'query_identity': 'bound',
                            'records': [{'record_id': 'original'}]}
                state = apply_feedback(state, proposal, feedback, 1)
                root = Path(tmp)
                directory = root/'batch-requests/000001'
                directory.mkdir(parents=True)
                (directory/'proposal.json').write_text(json.dumps(proposal))
                (directory/'feedback.json').write_text(json.dumps(feedback))
                altered = ({**feedback, 'records': [{'record_id': 'changed'}]} if changed_name == 'feedback'
                           else {**proposal, 'request': {'goal_query': True, 'context_node_id': 'helper'}})
                (directory/(changed_name+'.json')).write_text(json.dumps(altered))
                with self.assertRaisesRegex(ValueError, 'artifact integrity mismatch'):
                    restore_batch_memory(root, state)
                self.assertEqual(state['batch_facts'], [{'record_id': 'original'}])

    def test_legacy_query_hash_is_checked_when_general_feedback_hash_absent(self):
        _, state = fixture()
        state['batch_requests'] = 1
        proposal = {'action': 'query', 'request': {}}
        original = {'status': 'available', 'kind': 'fact_selection', 'query_identity': 'bound', 'records': []}
        state = apply_feedback(state, proposal, original, 1)
        del state['batch_history'][0]['feedback_sha256']
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root/'batch-requests/000001'
            directory.mkdir(parents=True)
            (directory/'proposal.json').write_text(json.dumps(proposal))
            (directory/'feedback.json').write_text(json.dumps({**original, 'records': [{'record_id': 'changed'}]}))
            with self.assertRaisesRegex(ValueError, 'artifact integrity mismatch'):
                restore_batch_memory(root, state)

    def test_read_bound_json_rejects_detail_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'feedback.json'
            expected = {'status': 'rejected_batch', 'check': {'diagnostic': 'original failure'}}
            path.write_text(json.dumps(expected))
            self.assertEqual(read_bound_json(path, digest(expected)), expected)
            path.write_text(json.dumps({'status': 'accepted'}))
            with self.assertRaisesRegex(ValueError, 'artifact integrity mismatch'):
                read_bound_json(path, digest(expected))

    def test_vector_index_checksum_and_wal_changes_invalidate_vector_queries(self):
        _, state = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = root/'premises.sqlite'
            database.write_bytes(b'fixed upstream database')
            directory = root/'theory/batch-requests/000001'
            directory.mkdir(parents=True)
            run = {'run_id': 'run', 'premise_database': str(database)}
            request = {'goal_query': True, 'context_node_id': 'contract', 'use_vectors': True}
            for filename in ('premise-vectors.sqlite', 'premise-vectors.sqlite.sha256', 'premise-vectors.sqlite-wal'):
                with self.subTest(file=filename):
                    path = root/filename
                    before = query_identity(run, state, directory, request)
                    path.write_bytes(b'first content')
                    created = query_identity(run, state, directory, request)
                    self.assertNotEqual(created, before)
                    path.write_bytes(b'changed content')
                    modified = query_identity(run, state, directory, request)
                    self.assertNotEqual(modified, created)
                    path.unlink()
                    self.assertNotEqual(query_identity(run, state, directory, request), modified)

    def test_lexical_query_identity_ignores_unused_vector_index(self):
        _, state = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = root/'premises.sqlite'
            database.write_bytes(b'fixed upstream database')
            directory = root/'theory/batch-requests/000001'
            directory.mkdir(parents=True)
            run = {'run_id': 'run', 'premise_database': str(database)}
            request = {'goal_query': True, 'context_node_id': 'contract', 'use_vectors': False}
            before = query_identity(run, state, directory, request)
            (root/'premise-vectors.sqlite').write_bytes(b'new unused vectors')
            self.assertEqual(query_identity(run, state, directory, request), before)


    def test_missing_artifacts_do_not_preserve_legacy_false_acceptance(self):
        _, state = fixture()
        state.update(batch_requests=1,
            batch_history=[{'request_number':1, 'action':'submit_batch', 'status':'accepted', 'proof_node_ids':['helper']}],
            batch_target_memory={'helper':{'request_number':1, 'feedback':{'status':'accepted'}}})
        with tempfile.TemporaryDirectory() as tmp:
            restored = restore_batch_memory(Path(tmp), state)
        self.assertEqual(restored['batch_history'][0]['status'], 'checked_not_committed')
        from regen.planning.attempt_memory import target_attempts
        self.assertEqual(dict(target_attempts(restored))['helper']['feedback']['status'], 'checked_not_committed')
        self.assertEqual(restored['proofs'], {})


if __name__ == '__main__':
    unittest.main()
