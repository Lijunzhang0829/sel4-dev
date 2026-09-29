"""Recovery shares the journal replay memory gate without changing authority."""
from concurrent.futures import ThreadPoolExecutor
import gc
import json
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import patch
import weakref

from regen.execution import proof_commits
from regen.execution.proof_commits import ProofCommits, durable
from regen.planning import batch_file_planner as planner


class _ObservedGate:
    def __init__(self, on_exit=lambda: None):
        self.lock = threading.RLock()
        self.attempts = queue.Queue()
        self.on_exit = on_exit

    def __enter__(self):
        self.attempts.put(threading.get_ident())
        self.lock.acquire()
        return self

    def __exit__(self, *args):
        try:
            self.on_exit()
        finally:
            self.lock.release()


class BatchRecoveryMemoryTest(unittest.TestCase):
    def accepted(self, number, event='document'):
        return {'event': event, 'state': {'batch_requests': number,
                'batch_feedback': {'status': 'accepted', 'number': number}}}

    def write(self, root, record, number=1):
        path = root/'acceptance/commits'/f'{number:06d}.json'
        durable(path, record)
        return path

    def test_nested_acceptance_results_unchanged_and_history_is_not_authority(self):
        origin = {'event': 'initial', 'evidence': {'continuation': {'commits': [
            self.accepted(3, 'batch'), {'event': 'initial', 'evidence': {
                'continuation': {'commits': [self.accepted(7)]}}}]}}}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write(root, origin)
            self.write(root, {'event': 'planner-feedback', 'state': {
                'batch_requests': 8, 'batch_history': [
                    {'request_number': 8, 'status': 'accepted'}]}}, 2)
            self.write(root, self.accepted(9), 3)
            expected = {number: {'status': 'accepted', 'number': number}
                        for number in (3, 7, 9)}
            self.assertEqual(planner.committed_batch_results(root), expected)

    def test_two_recoveries_and_cold_replay_share_one_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first_path = self.write(root/'first', self.accepted(1))
            second_path = self.write(root/'second', self.accepted(2))
            journal = root/'journal'
            ProofCommits(journal, {}, lambda *args: None).append(
                'initial', {'small': True}, {})
            gate = _ObservedGate()
            entered, release = threading.Event(), threading.Event()
            reads = []
            original = Path.read_text

            def read(path, *args, **kwargs):
                if path in (first_path, second_path) or path.parent == journal/'commits':
                    reads.append(path)
                if path == first_path:
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError('recovery gate was not released')
                return original(path, *args, **kwargs)

            with patch.object(proof_commits, '_COLD_REPLAY_LOCK', gate), \
                    patch.object(Path, 'read_text', read), ThreadPoolExecutor(3) as pool:
                first = pool.submit(planner.committed_batch_results, root/'first')
                try:
                    self.assertTrue(entered.wait(2))
                    gate.attempts.get(timeout=2)
                    second = pool.submit(planner.committed_batch_results, root/'second')
                    replay = pool.submit(ProofCommits(journal, {}, lambda *args: None).replay,
                                         repair=False)
                    gate.attempts.get(timeout=2)
                    gate.attempts.get(timeout=2)
                    self.assertEqual(reads, [first_path])
                finally:
                    release.set()
                self.assertEqual(first.result(3), {1: {'status': 'accepted', 'number': 1}})
                self.assertEqual(second.result(3), {2: {'status': 'accepted', 'number': 2}})
                self.assertEqual(replay.result(3), {'small': True})

    def test_record_is_released_before_gate_exit_including_retained_failure(self):
        class Record(dict):
            pass

        for malformed in (False, True):
            with self.subTest(malformed=malformed), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                record = self.accepted(1)
                if malformed:
                    record['state'] = None
                self.write(root, {'event': 'initial', 'evidence': {
                    'continuation': {'commits': [record]}, 'large': 'x'*10000}})
                references, failures = [], []
                original = json.loads

                def loads(text):
                    decoded = Record(original(text))
                    references.append(weakref.ref(decoded))
                    return decoded

                def released():
                    self.assertEqual(len(references), 1)
                    self.assertIsNone(references[0]())

                with patch.object(proof_commits, '_COLD_REPLAY_LOCK', _ObservedGate(released)), \
                        patch.object(planner.json, 'loads', side_effect=loads):
                    try:
                        result = planner.committed_batch_results(root)
                    except AttributeError as error:
                        failures.append(error)
                gc.collect()
                self.assertIsNone(references[0]())
                if malformed:
                    self.assertEqual(len(failures), 1)
                else:
                    self.assertEqual(result, {1: {'status': 'accepted', 'number': 1}})

    def test_journal_is_locked_before_each_cold_read(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write(root, self.accepted(1))
            self.write(root, self.accepted(2), 2)
            order = []

            class Journal:
                held = False

                def __enter__(self):
                    self.held = True
                    order.append('journal_enter')

                def __exit__(self, *args):
                    order.append('journal_exit')
                    self.held = False

            journal = Journal()
            test = self

            class Cold:
                def __enter__(self):
                    test.assertTrue(journal.held)
                    order.append('cold_enter')

                def __exit__(self, *args):
                    test.assertTrue(journal.held)
                    order.append('cold_exit')

            with patch.object(proof_commits, 'journal_lock', return_value=journal) as lock, \
                    patch.object(proof_commits, '_COLD_REPLAY_LOCK', Cold()):
                self.assertEqual(set(planner.committed_batch_results(root)), {1, 2})
            lock.assert_called_once_with(root/'acceptance')
            self.assertEqual(order, ['journal_enter', 'cold_enter', 'cold_exit',
                                     'cold_enter', 'cold_exit', 'journal_exit'])

    def test_changed_file_during_read_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self.write(root, self.accepted(1))
            original = Path.read_text

            def read(current, *args, **kwargs):
                result = original(current, *args, **kwargs)
                if current == path:
                    current.write_text(result+' ')
                return result

            with patch.object(Path, 'read_text', read), self.assertRaisesRegex(
                    ValueError, 'changed during batch recovery'):
                planner.committed_batch_results(root)

    def test_json_decode_failure_drops_large_document(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self.write(root, self.accepted(1))
            path.write_text('{"large":"'+'x'*10000)
            with self.assertRaises(json.JSONDecodeError) as failure:
                planner.committed_batch_results(root)
            self.assertEqual(failure.exception.doc, '')
            self.assertGreater(failure.exception.pos, 0)


if __name__ == '__main__':
    unittest.main()
