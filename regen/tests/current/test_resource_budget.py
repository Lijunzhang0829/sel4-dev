"""Heap publication, disk pressure and real thread/process copy exclusion."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import multiprocessing
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from regen.infrastructure import resource_budget as budget


def _process_copy(source, destination, entered, release, outcomes):
    original = budget.shutil.copytree

    def controlled_copy(*args, **kwargs):
        entered.set()
        if release is not None and not release.wait(5):
            raise TimeoutError('test copy gate expired')
        return original(*args, **kwargs)

    try:
        with patch.object(budget.shutil, 'copytree', side_effect=controlled_copy):
            result = budget.clone_heaps(source, destination, reserve_bytes=0)
        outcomes.put(result['status'])
    except BaseException as error:
        outcomes.put(f'{type(error).__name__}: {error}')


class ResourceBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'Semantic_Base').write_bytes(b'heap' * 1024)
        self.destination = self.root / 'heaps'

    def test_insufficient_budget_rejects_before_copy_or_staging(self):
        with patch.object(budget.shutil, 'disk_usage', return_value=SimpleNamespace(free=1)), \
                patch.object(budget.shutil, 'copytree') as copy:
            with self.assertRaisesRegex(budget.ResourceBudgetError, 'disk budget insufficient'):
                budget.clone_heaps(self.source, self.destination)
        copy.assert_not_called()
        self.assertFalse(self.destination.exists())
        self.assertFalse(list(self.root.glob('.heaps-copy-*')))

    def test_success_publishes_exact_heap_bytes_and_records_reserve(self):
        result = budget.clone_heaps(self.source, self.destination, reserve_bytes=0)
        self.assertEqual(result['status'], 'copied')
        self.assertEqual(result['source_bytes'], 4096)
        self.assertEqual(result['file_count'], 1)
        self.assertEqual(result['policy']['reserve_bytes'], 0)
        self.assertEqual((self.destination / 'Semantic_Base').read_bytes(), b'heap' * 1024)
        self.assertFalse(list(self.root.glob('.heaps-copy-*')))

    def test_failed_copy_removes_only_its_own_temporary_tree(self):
        untouched = self.root / 'other-heaps'
        untouched.mkdir()
        (untouched / 'original').write_text('preserve')

        def fail_copy(source, target):
            target.mkdir()
            (target / 'partial-heap').write_text('partial')
            raise OSError('injected copy write failure')

        with patch.object(budget.shutil, 'copytree', side_effect=fail_copy):
            with self.assertRaisesRegex(OSError, 'copy write failure'):
                budget.clone_heaps(self.source, self.destination, reserve_bytes=0)
        self.assertFalse(self.destination.exists())
        self.assertFalse(list(self.root.glob('.heaps-copy-*')))
        self.assertEqual((untouched / 'original').read_text(), 'preserve')

    def test_external_disk_growth_during_copy_prevents_publication(self):
        with patch.object(budget.shutil, 'disk_usage', side_effect=[
                SimpleNamespace(free=100000), SimpleNamespace(free=10)]):
            with self.assertRaisesRegex(budget.ResourceBudgetError, 'before publication'):
                budget.clone_heaps(self.source, self.destination, reserve_bytes=1000)
        self.assertFalse(self.destination.exists())
        self.assertFalse(list(self.root.glob('.heaps-copy-*')))

    def test_existing_destination_is_not_modified(self):
        self.destination.mkdir()
        (self.destination / 'original').write_text('existing')
        with patch.object(budget.shutil, 'copytree') as copy:
            self.assertEqual(budget.clone_heaps(self.source, self.destination)['status'], 'existing')
        copy.assert_not_called()
        self.assertEqual(list(self.destination.iterdir()), [self.destination / 'original'])

    def test_destination_created_at_publication_is_not_overwritten(self):
        original = budget._publish_directory
        identity = []

        def external_writer(source, destination):
            destination.mkdir()
            identity.append(destination.stat().st_ino)
            return original(source, destination)

        with patch.object(budget, '_publish_directory', side_effect=external_writer):
            with self.assertRaises(FileExistsError):
                budget.clone_heaps(self.source, self.destination, reserve_bytes=0)
        self.assertEqual(self.destination.stat().st_ino, identity[0])
        self.assertFalse(list(self.destination.iterdir()))
        self.assertFalse(list(self.root.glob('.heaps-copy-*')))

    def test_two_threads_cannot_spend_the_same_free_bytes(self):
        entered, release = threading.Event(), threading.Event()
        original = budget.shutil.copytree
        free = [12288]
        copy_calls = []

        def controlled_copy(source, target):
            copy_calls.append(str(target))
            entered.set()
            if not release.wait(5):
                raise TimeoutError('test copy gate expired')
            result = original(source, target)
            free[0] -= 8192
            return result

        with patch.object(budget.shutil, 'disk_usage', side_effect=lambda _: SimpleNamespace(free=free[0])), \
                patch.object(budget.shutil, 'copytree', side_effect=controlled_copy), \
                ThreadPoolExecutor(2) as callers:
            first = callers.submit(budget.clone_heaps, self.source, self.destination, reserve_bytes=1024)
            try:
                self.assertTrue(entered.wait(3))
                second = callers.submit(budget.clone_heaps, self.source, self.root / 'second', reserve_bytes=1024)
            finally:
                release.set()
            self.assertEqual(first.result(3)['status'], 'copied')
            with self.assertRaisesRegex(budget.ResourceBudgetError, 'disk budget insufficient'):
                second.result(3)
        self.assertEqual(len(copy_calls), 1)
        self.assertFalse((self.root / 'second').exists())

    def test_filesystem_lock_serializes_distinct_processes(self):
        context = multiprocessing.get_context('fork')
        first_entered, second_entered, release = context.Event(), context.Event(), context.Event()
        outcomes = context.Queue()
        first = context.Process(target=_process_copy,
            args=(self.source, self.destination, first_entered, release, outcomes))
        second = context.Process(target=_process_copy,
            args=(self.source, self.root / 'second', second_entered, None, outcomes))
        first.start()
        try:
            self.assertTrue(first_entered.wait(3))
            second.start()
            self.assertFalse(second_entered.wait(.2))
        finally:
            release.set()
            first.join(6)
            if second.pid is not None:
                second.join(6)
            for process in (first, second):
                if process.pid is not None and process.is_alive():
                    process.terminate()
                    process.join(3)
        self.assertEqual(first.exitcode, 0)
        self.assertEqual(second.exitcode, 0)
        self.assertTrue(second_entered.is_set())
        self.assertEqual([outcomes.get(timeout=1), outcomes.get(timeout=1)], ['copied', 'copied'])
        outcomes.close()

    def test_symlink_destination_is_never_treated_as_an_existing_clone(self):
        self.destination.symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'never a symlink'):
            budget.clone_heaps(self.source, self.destination, reserve_bytes=0)
        self.assertEqual((self.source / 'Semantic_Base').read_bytes(), b'heap' * 1024)

    def test_repository_init_binds_shell_worker_and_records_disk_policy(self):
        from regen.execution import repository_execution as execution
        from regen.tests.current.test_repository_planner import RepositoryPlannerTest
        fixture = RepositoryPlannerTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.call([fixture.proposal], allow=True)
        run_dir = fixture.root / 'budget-run'
        run = execution.init_run(run_dir, fixture.root, 'budget-run',
                                 fixture.output / 'file-generation-list.json')
        shell = Path(execution.__file__).parent / 'worker_owner.sh'
        self.assertEqual(run['implementation']['regen/execution/worker_owner.sh'],
                         hashlib.sha256(shell.read_bytes()).hexdigest())
        self.assertEqual(run['infrastructure_policy']['heap_copy'], budget.HEAP_COPY_POLICY)
        receipt = json.loads((run_dir / 'heap-copy-budget.json').read_text())
        self.assertEqual(receipt['policy']['reserve_bytes'], 2 * 1024 ** 3)
        self.assertEqual(receipt['status'], 'copied')


if __name__ == '__main__':
    unittest.main()
