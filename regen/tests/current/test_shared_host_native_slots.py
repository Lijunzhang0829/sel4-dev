"""Opt-in native capacity shared by independent experiment processes."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from regen.execution.check_executor import NativeResources
from regen.execution.check_tasks import CURRENT_TASK, Cancellation, CheckCancelled


class Checker:
    def close_prefix(self):
        pass


class SharedHostNativeSlotTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.lock_dir = Path(directory.name) / 'host-slots'

    def resources(self, **kwargs):
        value = NativeResources(1, 1, 1, shared_host_lock_dir=self.lock_dir, **kwargs)
        self.addCleanup(value.close)
        return value

    def test_opt_in_policy_is_durable_and_mismatch_is_rejected(self):
        self.assertNotIn('shared_host_slots', NativeResources().policy)
        with self.assertRaisesRegex(ValueError, 'absolute'):
            NativeResources(shared_host_lock_dir='relative-lock-directory')
        first = self.resources(shared_host_slots=1)
        self.assertEqual(first.policy['shared_host_lock_dir'], str(self.lock_dir.resolve()))
        self.resources(shared_host_slots=1)
        with self.assertRaisesRegex(ValueError, 'policy mismatch'):
            NativeResources(shared_host_lock_dir=self.lock_dir, shared_host_slots=2)

    def test_unsafe_lock_directory_and_symlinked_policy_are_rejected(self):
        self.lock_dir.mkdir(mode=0o700)
        os.chmod(self.lock_dir, 0o777)
        try:
            with self.assertRaisesRegex(ValueError, 'not group/world writable'):
                self.resources()
        finally:
            os.chmod(self.lock_dir, 0o700)
        (self.lock_dir / 'policy.lock').symlink_to(self.lock_dir / 'elsewhere')
        with self.assertRaisesRegex(ValueError, 'unsafe shared host lock file'):
            self.resources()
        (self.lock_dir / 'policy.lock').unlink()
        resources = self.resources()
        (self.lock_dir / 'slot-0000.lock').symlink_to(self.lock_dir / 'elsewhere')
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, 'unsafe shared host lock file'):
            with resources.lease(Checker()):
                self.fail('unsafe slot file was accepted')

    def test_owned_cleanup_does_not_wait_for_other_process_slot_and_retries_failure(self):
        class FlakyChecker:
            attempts = 0

            def close_prefix(self):
                self.attempts += 1
                if self.attempts == 1:
                    raise OSError('owned worker cleanup failed')

        second = self.resources(shared_host_cleanup_timeout=.2)
        checker = FlakyChecker()
        with redirect_stdout(io.StringIO()), second.lease(checker):
            pass
        code = """
import sys, time
from regen.execution.check_executor import NativeResources
class Checker:
    def close_prefix(self): pass
pool = NativeResources(1, 1, 1, shared_host_lock_dir=sys.argv[1])
with pool.lease(Checker()):
    print('READY', file=sys.stderr, flush=True)
    time.sleep(20)
"""
        child = subprocess.Popen([sys.executable, '-c', code, str(self.lock_dir)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            ready, _, _ = select.select([child.stderr], [], [], 5)
            self.assertTrue(ready, 'other process did not acquire the native slot')
            self.assertEqual(child.stderr.readline().strip(), 'READY')
            started = time.monotonic()
            with self.assertRaisesRegex(OSError, 'owned worker cleanup failed'):
                second.close()
            self.assertLess(time.monotonic() - started, 1.)
            self.assertEqual(list(second.residents), [id(checker)])
            second.close()
            self.assertLess(time.monotonic() - started, 1.)
            self.assertFalse(second.residents)
            self.assertEqual(checker.attempts, 2)
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
            child.stdout.close()
            child.stderr.close()

    def test_two_pools_share_one_slot_and_report_host_wait(self):
        first, second = self.resources(), self.resources()
        entered, release = threading.Event(), threading.Event()
        second_entered = threading.Event()

        def hold():
            with first.lease(Checker()):
                entered.set()
                self.assertTrue(release.wait(5))

        def contend():
            with second.lease(Checker()):
                second_entered.set()

        with redirect_stdout(io.StringIO()), ThreadPoolExecutor(2) as workers:
            owner = workers.submit(hold)
            try:
                self.assertTrue(entered.wait(3))
                contender = workers.submit(contend)
                time.sleep(.2)
                self.assertFalse(second_entered.is_set())
            finally:
                release.set()
            owner.result(timeout=3)
            contender.result(timeout=3)
        self.assertTrue(second_entered.is_set())
        self.assertGreaterEqual(second.metrics[0]['host_queue_seconds'], .1)
        self.assertEqual(second.metrics[0]['host_slot'], 0)

    def test_cancellation_during_host_wait_releases_local_reservation(self):
        first, second = self.resources(), self.resources()
        cancel = Cancellation()
        attempted = threading.Event()
        checker = Checker()

        def contend():
            marker = CURRENT_TASK.set(cancel)
            try:
                attempted.set()
                with second.lease(checker):
                    self.fail('cancelled waiter entered native body')
            finally:
                CURRENT_TASK.reset(marker)

        with redirect_stdout(io.StringIO()), first.lease(Checker()):
            with ThreadPoolExecutor(1) as workers:
                future = workers.submit(contend)
                self.assertTrue(attempted.wait(3))
                deadline = time.monotonic() + 3
                while not second.active and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(second.active)
                cancel.cancel()
                with self.assertRaises(CheckCancelled):
                    future.result(timeout=3)
        self.assertFalse(second.active)
        self.assertFalse(second.queue)
        self.assertFalse(second.residents)

    def test_process_crash_releases_host_slot(self):
        code = """
import sys, time
from regen.execution.check_executor import NativeResources
class Checker:
    def close_prefix(self): pass
pool = NativeResources(1, 1, 1, shared_host_lock_dir=sys.argv[1])
with pool.lease(Checker()):
    print('READY', file=sys.stderr, flush=True)
    time.sleep(60)
"""
        child = subprocess.Popen([sys.executable, '-c', code, str(self.lock_dir)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def cleanup_child():
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
            child.stdout.close()
            child.stderr.close()
        self.addCleanup(cleanup_child)
        ready, _, _ = select.select([child.stderr], [], [], 5)
        self.assertTrue(ready, 'child did not acquire the native slot')
        self.assertEqual(child.stderr.readline().strip(), 'READY')

        contender = self.resources()
        entered = threading.Event()
        with redirect_stdout(io.StringIO()), ThreadPoolExecutor(1) as workers:
            future = workers.submit(lambda: self._enter(contender, entered))
            time.sleep(.2)
            self.assertFalse(entered.is_set())
            child.kill()
            child.wait(timeout=5)
            future.result(timeout=5)
        self.assertTrue(entered.is_set())
        self.assertGreaterEqual(contender.metrics[0]['host_queue_seconds'], .1)

    @staticmethod
    def _enter(resources, entered):
        with resources.lease(Checker()):
            entered.set()


if __name__ == '__main__':
    unittest.main()
