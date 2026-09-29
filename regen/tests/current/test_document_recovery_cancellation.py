"""Real journal/cancellation boundaries; no native proof success is inferred."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import os
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch

from regen.execution.check_tasks import CURRENT_TASK, Cancellation, CheckCancelled
from regen.execution.proof_commits import ProofCommits, durable
from regen.planning.batch_file_planner import committed_batch_results, restore_batch_memory
from regen.tests.current.test_proof_commits import fixture


class DocumentAcceptanceCancellationTests(unittest.TestCase):
    def test_published_document_is_protected_when_directory_fsync_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = ProofCommits(Path(tmp), {}, lambda *args: None)
            token = Cancellation()
            marker = CURRENT_TASK.set(token)
            fsync = os.fsync
            def sync(descriptor):
                if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                    raise OSError('injected directory fsync failure')
                return fsync(descriptor)
            try:
                with patch('regen.execution.proof_commits.os.fsync', side_effect=sync):
                    with self.assertRaisesRegex(OSError, 'directory fsync failure'):
                        journal.append('document', {'proofs': {'a': 'by simp'}}, {})
                self.assertTrue(token.published)
                self.assertFalse(token.cancel())
                self.assertEqual(journal.replay(repair=False)['proofs'], {'a': 'by simp'})
            finally:
                CURRENT_TASK.reset(marker)

    def test_failed_unpublished_document_remains_cancellable(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = ProofCommits(Path(tmp), {}, lambda *args: None)
            token = Cancellation()
            marker = CURRENT_TASK.set(token)
            try:
                with patch('regen.execution.proof_commits.durable', side_effect=OSError('no publication')):
                    with self.assertRaises(OSError):
                        journal.append('document', {'proofs': {'a': 'by simp'}}, {})
                self.assertFalse(token.published)
                self.assertTrue(token.cancel())
                self.assertIsNone(journal.replay())
            finally:
                CURRENT_TASK.reset(marker)

    def test_pointer_failure_after_document_commit_cannot_enable_cancellation(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = ProofCommits(Path(tmp), {}, lambda *args: None)
            token = Cancellation()
            marker = CURRENT_TASK.set(token)
            def persist(path, *args, **kwargs):
                if path.name == 'HEAD.json':
                    raise OSError('injected pointer write failure')
                return durable(path, *args, **kwargs)
            try:
                with patch('regen.execution.proof_commits.durable', side_effect=persist):
                    with self.assertRaisesRegex(OSError, 'pointer write failure'):
                        journal.append('document', {'proofs': {'a': 'by simp'}}, {})
                self.assertTrue(token.published)
                self.assertFalse(token.cancel())
                self.assertEqual(journal.replay()['proofs'], {'a': 'by simp'})
            finally:
                CURRENT_TASK.reset(marker)

    def test_document_commit_prevents_later_cancellation(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = ProofCommits(Path(tmp), {}, lambda *args: None)
            token = Cancellation()
            marker = CURRENT_TASK.set(token)
            try:
                journal.append('document', {'proofs': {'a': 'by simp'}}, {})
                self.assertTrue(token.published)
                self.assertFalse(token.cancel())
                token.check()
                self.assertEqual(journal.replay()['proofs'], {'a': 'by simp'})
            finally:
                CURRENT_TASK.reset(marker)

    def test_cancelled_document_never_publishes(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = ProofCommits(Path(tmp), {}, lambda *args: None)
            token = Cancellation()
            token.cancel()
            marker = CURRENT_TASK.set(token)
            try:
                with self.assertRaises(CheckCancelled):
                    journal.append('document', {'proofs': {'a': 'by simp'}}, {})
                self.assertIsNone(journal.replay())
            finally:
                CURRENT_TASK.reset(marker)

    def test_cancel_waits_for_in_flight_document_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = ProofCommits(Path(tmp), {}, lambda *args: None)
            token = Cancellation()
            writing, release, cancelling = (threading.Event() for _ in range(3))

            def persist(path, *args, **kwargs):
                if path.parent.name == 'commits':
                    writing.set()
                    if not release.wait(5):
                        raise TimeoutError('test did not release journal write')
                return durable(path, *args, **kwargs)

            def append():
                marker = CURRENT_TASK.set(token)
                try:
                    return journal.append('document', {'proofs': {'a': 'by simp'}}, {})
                finally:
                    CURRENT_TASK.reset(marker)

            def cancel():
                cancelling.set()
                return token.cancel()

            with patch('regen.execution.proof_commits.durable', side_effect=persist), ThreadPoolExecutor(2) as pool:
                writer = pool.submit(append)
                try:
                    self.assertTrue(writing.wait(3))
                    cancellation = pool.submit(cancel)
                    self.assertTrue(cancelling.wait(3))
                    self.assertFalse(cancellation.done())
                finally:
                    release.set()
                self.assertEqual(writer.result(5)['proofs'], {'a': 'by simp'})
                self.assertFalse(cancellation.result(5))
            self.assertTrue(token.published)


class DocumentRecoveryTests(unittest.TestCase):
    def test_current_and_nested_origin_document_acceptance_survives_recovery(self):
        _, state = fixture()
        state.update(batch_requests=75, proofs={'helper': 'by simp'}, accepted_order=['helper'],
                     batch_history=[{'request_number': n, 'action': 'check_theory',
                                     'status': 'accepted'} for n in (6, 56)])
        accepted = lambda n: {'event': 'document', 'state': {
            'batch_requests': n, 'batch_feedback': {'status': 'accepted', 'request': n}}}
        origin = {'event': 'initial', 'evidence': {'continuation': {'commits': [accepted(6)]}}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            durable(root/'acceptance/commits/000001.json', {
                'event': 'initial', 'evidence': {'continuation': {'commits': [origin]}}})
            durable(root/'acceptance/commits/000002.json', accepted(56))
            durable(root/'batch-requests/000057/check.json', {'check': {'verdict': 'green'}})
            durable(root/'batch-requests/000075/proposal.json', {'action': 'check_theory'})
            original = deepcopy(state)
            self.assertEqual(set(committed_batch_results(root)), {6, 56})
            restored = restore_batch_memory(root, state)
            history = {item['request_number']: item for item in restored['batch_history']}
            self.assertEqual(history[6]['status'], 'accepted')
            self.assertEqual(history[56]['status'], 'accepted')
            self.assertEqual(history[57]['status'], 'checked_not_committed')
            self.assertEqual(history[75]['status'], 'interrupted_action')
            for key in ('batch_requests', 'proofs', 'accepted_order', 'graph'):
                self.assertEqual(restored[key], original[key])
            self.assertEqual(state, original)
            self.assertEqual(restore_batch_memory(root, restored), restored)
