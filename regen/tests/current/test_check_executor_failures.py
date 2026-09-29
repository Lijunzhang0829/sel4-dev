"""Fault injection at persistence and native-resource concurrency boundaries."""
from concurrent.futures import ThreadPoolExecutor
import errno
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from regen.execution.check_executor import CheckExecutor, NativeResources
from regen.execution.check_tasks import Cancellation, acceptance_guard
from regen.execution.proof_commits import durable


class ExecutorFailureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.executor = CheckExecutor(self.root / 'tasks', workers=2)
        self.addCleanup(self.executor.close)

    def submit(self, key, execute):
        return self.executor.submit(kind='statement', theory='A', binding={}, request={},
                                    execute=execute, owner='A', task_id=key)

    def partial(self, key, *, token=True):
        row = dict(task_id=key, status='queued', owner=key)
        self.executor.records[key] = row
        self.executor.owners[key] = key
        if token:
            self.executor.tokens[key] = Cancellation()
        durable(self.executor.root / key / 'task.json', row)

    def test_full_disk_during_submission_releases_owner_and_never_dispatches(self):
        executed = []
        with patch('regen.execution.check_executor.durable',
                   side_effect=OSError(errno.ENOSPC, 'disk full')):
            with self.assertRaises(OSError):
                self.submit('lost-write', lambda _: executed.append(True))
            self.assertEqual(self.executor.cancel('lost-write')['status'], 'error')
        self.assertFalse(executed)
        self.assertFalse(self.executor.owners)
        self.assertNotIn('lost-write', self.executor.futures)
        self.assertEqual(self.submit('lost-write', lambda _: executed.append(True))['status'], 'error')
        self.submit('next', lambda _: {'valid': True})
        self.assertEqual(self.executor.wait(['next'], timeout=3)[0]['status'], 'completed')
        self.assertFalse(executed)

    def test_dispatch_failure_is_durable_and_does_not_strand_owner(self):
        with patch.object(self.executor.pool, 'submit', side_effect=RuntimeError('thread creation failed')):
            with self.assertRaisesRegex(RuntimeError, 'thread creation failed'):
                self.submit('lost-dispatch', lambda _: {})
        self.assertFalse(self.executor.owners)
        saved = json.loads((self.executor.root / 'lost-dispatch' / 'task.json').read_text())
        self.assertEqual(saved['status'], 'error')
        self.executor.close()
        with_executor = CheckExecutor(self.executor.root)
        self.addCleanup(with_executor.close)
        self.assertEqual(with_executor.get('lost-dispatch')['status'], 'error')

    def test_thread_start_failure_cancels_work_already_enqueued_without_a_future(self):
        executed = []
        # ThreadPoolExecutor enqueues before starting a thread. submit() can
        # therefore raise without returning the Future for an existing item.
        with patch.object(threading.Thread, 'start', side_effect=RuntimeError('thread start failed')):
            with self.assertRaisesRegex(RuntimeError, 'thread start failed'):
                self.submit('enqueued', lambda _: executed.append('lost'))
        self.assertNotIn('enqueued', self.executor.futures)
        self.submit('next', lambda _: (executed.append('next'), {})[1])
        self.assertEqual(self.executor.wait(['next'], timeout=3)[0]['status'], 'completed')
        self.assertEqual(executed, ['next'])
        self.assertEqual(self.executor.get('enqueued')['status'], 'cancelled')

    def test_partial_record_can_be_cancelled_with_no_future_or_token(self):
        for key, has_token in [('with-token', True), ('without-token', False)]:
            with self.subTest(key=key):
                self.partial(key, token=has_token)
                result = self.executor.cancel(key)
                self.assertEqual(result['status'], 'cancelled')
                self.assertTrue(result['cancellation_requested'])
                self.assertNotIn(key, self.executor.owners)

    def test_close_continues_cancelling_and_releases_ownership_when_writes_fail(self):
        self.partial('first')
        self.partial('second')
        original = durable

        def fail_first(path, value, **kwargs):
            if path.parent.name == 'first':
                raise OSError(errno.ENOSPC, 'first task disk full')
            return original(path, value, **kwargs)

        with patch('regen.execution.check_executor.durable', side_effect=fail_first), \
                patch.object(self.executor.pool, 'shutdown', wraps=self.executor.pool.shutdown) as shutdown:
            with self.assertRaisesRegex(OSError, 'first task disk full'):
                self.executor.close()
            shutdown.assert_called_once_with(wait=True)
        self.assertTrue(self.executor.owner_file.closed)
        self.assertFalse(self.executor.owners)
        self.assertTrue(all(token.event.is_set() for token in self.executor.tokens.values()))
        self.assertEqual(self.executor.get('second')['status'], 'cancelled')
        resumed = CheckExecutor(self.executor.root)
        self.addCleanup(resumed.close)
        self.assertEqual(resumed.get('first')['status'], 'interrupted')
        self.assertEqual(resumed.get('second')['status'], 'cancelled')

    def test_interrupted_shutdown_retains_ownership_until_retry_finishes_writers(self):
        accepted, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def accepted_task(_):
            with acceptance_guard():
                accepted.set()
            if not release.wait(5):
                raise TimeoutError('test accepted task release expired')
            return {'accepted': True}

        self.submit('published', accepted_task)
        self.assertTrue(accepted.wait(3))
        with patch.object(self.executor.pool, 'shutdown', side_effect=KeyboardInterrupt('join interrupted')):
            with self.assertRaisesRegex(KeyboardInterrupt, 'join interrupted'):
                self.executor.close()
        self.assertEqual(self.executor.get('published')['status'], 'running')
        self.assertFalse(self.executor.futures['published'].done())
        self.assertFalse(self.executor.owner_file.closed)
        self.assertFalse(self.executor.shutdown_complete)
        with self.assertRaisesRegex(RuntimeError, 'another executor owns'):
            CheckExecutor(self.executor.root)

        release.set()
        self.executor.close()
        self.assertTrue(self.executor.shutdown_complete)
        self.assertTrue(self.executor.owner_file.closed)
        self.assertEqual(self.executor.get('published')['result'], {'accepted': True})
        resumed = CheckExecutor(self.executor.root)
        self.addCleanup(resumed.close)
        self.assertEqual(resumed.get('published')['status'], 'completed')


class NativeFailureTests(unittest.TestCase):
    def test_one_close_failure_does_not_skip_others_and_can_be_retried(self):
        class Checker:
            def __init__(self, failing=False):
                self.failing = failing
                self.closed = False
                self.calls = 0

            def close_prefix(self):
                self.calls += 1
                if self.failing:
                    raise TimeoutError('worker remains live')
                self.closed = True

        resources = NativeResources(slots=1, threads=1, resident_limit=2)
        first, second = Checker(True), Checker()
        for checker in (first, second):
            with resources.lease(checker):
                pass
        with self.assertRaisesRegex(TimeoutError, 'remains live'):
            resources.close()
        self.assertFalse(first.closed)
        self.assertTrue(second.closed)
        self.assertEqual(list(resources.residents), [id(first)])
        with self.assertRaisesRegex(RuntimeError, 'pool is closed'):
            with resources.lease(second):
                self.fail('closed pool admitted new work')
        first.failing = False
        resources.close()
        self.assertTrue(first.closed)
        self.assertFalse(resources.residents)
        self.assertEqual(second.calls, 1)

    def test_slow_eviction_does_not_block_another_active_lease_from_releasing(self):
        release_active, release_eviction = threading.Event(), threading.Event()
        active_entered, eviction_entered = threading.Event(), threading.Event()

        class Checker:
            def __init__(self, slow=False):
                self.slow = slow

            def close_prefix(self):
                if self.slow:
                    eviction_entered.set()
                    if not release_eviction.wait(5):
                        raise TimeoutError('test eviction stalled')

        resources = NativeResources(slots=2, threads=1, resident_limit=2)
        self.addCleanup(resources.close)
        active, victim, replacement = Checker(), Checker(True), Checker()
        for checker in (active, victim):
            with resources.lease(checker):
                pass

        def hold_active():
            with resources.lease(active):
                active_entered.set()
                if not release_active.wait(5):
                    raise TimeoutError('test active lease stalled')

        def replace():
            with resources.lease(replacement):
                return True

        with ThreadPoolExecutor(2) as callers:
            first = callers.submit(hold_active)
            try:
                self.assertTrue(active_entered.wait(3))
                second = callers.submit(replace)
                self.assertTrue(eviction_entered.wait(3))
                release_active.set()
                # Completion requires acquiring the resource condition lock.
                first.result(timeout=1)
                with resources.condition:
                    self.assertEqual(len(resources.residents), 2)
                    self.assertEqual(resources.evicting, {id(victim)})
                    self.assertEqual(resources.active, {id(replacement)})
                self.assertFalse(second.done())
            finally:
                release_active.set()
                release_eviction.set()
            self.assertTrue(second.result(timeout=3))

    def test_concurrent_evictions_reserve_capacity_until_both_workers_close(self):
        live, peak = set(), []
        live_lock = threading.Lock()
        entered = [threading.Event(), threading.Event()]
        release = [threading.Event(), threading.Event()]

        class Checker:
            def __init__(self, index=None):
                self.index = index

            def start(self):
                with live_lock:
                    live.add(id(self))
                    peak.append(len(live))

            def close_prefix(self):
                if self.index is not None:
                    entered[self.index].set()
                    if not release[self.index].wait(5):
                        raise TimeoutError('test eviction stalled')
                with live_lock:
                    live.discard(id(self))

        resources = NativeResources(slots=2, threads=1, resident_limit=2)
        self.addCleanup(resources.close)
        originals = [Checker(0), Checker(1)]
        replacements = [Checker(), Checker()]
        for checker in originals:
            with resources.lease(checker):
                checker.start()

        def replace(checker):
            with resources.lease(checker):
                checker.start()

        with ThreadPoolExecutor(2) as callers:
            futures = [callers.submit(replace, checker) for checker in replacements]
            try:
                self.assertTrue(all(event.wait(3) for event in entered))
                with resources.condition:
                    self.assertEqual(len(resources.residents), 2)
                    self.assertEqual(len(resources.evicting), 2)
                self.assertFalse(any(future.done() for future in futures))
            finally:
                for event in release:
                    event.set()
            for future in futures:
                future.result(timeout=3)
        self.assertEqual(max(peak), 2)
        self.assertEqual(live, {id(checker) for checker in replacements})
        self.assertFalse(resources.evicting)


if __name__ == '__main__':
    unittest.main()
