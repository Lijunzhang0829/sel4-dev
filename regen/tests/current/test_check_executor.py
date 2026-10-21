"""Real task/thread coordination; no model or native process is mocked as proof success."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import json
from pathlib import Path
import tempfile
import threading
import unittest

from regen.execution.check_executor import CheckExecutor, NativeResources, CURRENT_RESOURCES, native_operation
from regen.execution.check_tasks import CURRENT_TASK, Cancellation, CheckCancelled, acceptance_guard, check_cancelled
from regen.execution.proof_commits import commit_paths, durable
from regen.infrastructure.progress_events import event_scope, emit_event


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.executor = CheckExecutor(self.root/'tasks',workers=2)
        self.addCleanup(self.executor.close)

    def submit(self, callback, **kwargs):
        return self.executor.submit(kind='statement',theory='A',binding={'context':'frozen'},
                                    request={'node':'helper'},execute=callback,**kwargs)['task_id']

    def terminal(self, key):
        for _ in range(10):
            result = self.executor.wait([key],timeout=1)[0]
            if result['status'] in {'completed','cancelled','error','interrupted'}:
                return result
        self.fail('task failed to finish')

    def test_submission_order_survives_restart(self):
        a=self.submit(lambda _: {})
        self.terminal(a)
        sequence=self.executor.get(a)['submission_sequence']
        self.executor.close()
        self.executor=CheckExecutor(self.root/'tasks',workers=2)
        self.addCleanup(self.executor.close)
        b=self.submit(lambda _: {})
        self.terminal(b)
        self.assertGreater(self.executor.get(b)['submission_sequence'],sequence)

    def test_independent_tasks_overlap_and_results_are_durable(self):
        barrier = threading.Barrier(2)
        def run(path):
            barrier.wait(timeout=3)
            return {'proofs_checked':False,'verdict':'green','path':str(path)}
        a,b = self.submit(run),self.submit(run)
        self.assertEqual(self.terminal(a)['status'],'completed')
        self.assertEqual(self.terminal(b)['status'],'completed')
        self.assertNotEqual(self.executor.get(a)['result']['path'],self.executor.get(b)['result']['path'])
        self.executor.close()
        resumed = CheckExecutor(self.root/'tasks'); self.addCleanup(resumed.close)
        self.assertFalse(resumed.get(a)['result']['proofs_checked'])

    def test_mutating_owner_is_exclusive_but_readers_are_allowed(self):
        release = threading.Event(); self.addCleanup(release.set)
        a=self.submit(lambda _: (release.wait(3),{})[1],owner='A')
        with self.assertRaisesRegex(ValueError,'already in flight'):
            self.submit(lambda _: {},owner='A')
        b=self.submit(lambda _: {'observation':True})
        self.assertEqual(self.terminal(b)['status'],'completed')
        release.set(); self.terminal(a)

    def test_cancel_running_does_not_accept_late_success(self):
        entered=threading.Event(); release=threading.Event(); self.addCleanup(release.set)
        def run(_):
            entered.set();release.wait(3)
            return {'verdict':'green'}
        key=self.submit(run);self.assertTrue(entered.wait(3))
        self.assertTrue(self.executor.cancel(key)['cancellation_requested'])
        release.set()
        self.assertEqual(self.terminal(key)['status'],'cancelled')
        self.assertFalse((self.root/'tasks'/key/'result.json').exists())

    def test_acceptance_linearizes_before_cancellation(self):
        accepted=threading.Event(); release=threading.Event();self.addCleanup(release.set)
        def run(_):
            with acceptance_guard():
                accepted.set()
            release.wait(3)
            return {'accepted':True}
        key=self.submit(run); self.assertTrue(accepted.wait(3))
        self.assertFalse(self.executor.cancel(key)['cancellation_requested'])
        release.set();self.assertTrue(self.terminal(key)['result']['accepted'])

    def test_repeated_identity_does_not_repeat_work(self):
        count=[]
        key=self.submit(lambda _: (count.append(1),{})[1],task_id='same')
        self.terminal(key)
        self.submit(lambda _: count.append(2),task_id='same')
        self.assertEqual(count,[1])
        with self.assertRaisesRegex(ValueError,'different input'):
            self.executor.submit(kind='symbol',theory='A',binding={},request={},execute=lambda _: {},task_id='same')

    def test_corrupt_result_is_not_returned(self):
        key=self.submit(lambda _: {'value':1});self.terminal(key)
        (self.root/'tasks'/key/'result.json').write_text('{"value":2}')
        with self.assertRaisesRegex(ValueError,'changed'):
            self.executor.get(key)

    def test_second_executor_cannot_own_the_same_directory(self):
        with self.assertRaisesRegex(RuntimeError,'another executor'):
            CheckExecutor(self.root/'tasks')

    def test_receipt_recovers_result_after_status_write_was_lost(self):
        key=self.submit(lambda _: {'checked':True});self.terminal(key)
        self.executor.close()
        path=self.root/'tasks'/key/'task.json'
        record=json.loads(path.read_text());record['status']='running';durable(path,record)
        resumed=CheckExecutor(self.root/'tasks');self.addCleanup(resumed.close)
        self.assertTrue(resumed.get(key)['result']['checked'])
        self.assertEqual(resumed.get(key)['status'],'completed')

    def test_corrupt_restore_releases_directory_ownership(self):
        path=self.root/'broken'/'job'/'task.json'
        path.parent.mkdir(parents=True);path.write_text('invalid json')
        with self.assertRaises(ValueError):CheckExecutor(self.root/'broken')
        path.unlink()
        repaired=CheckExecutor(self.root/'broken');repaired.close()

    def test_restart_marks_pending_interrupted_without_dispatch(self):
        durable(self.root/'restart'/'lost'/'task.json',{'task_id':'lost','status':'running'})
        executor=CheckExecutor(self.root/'restart');self.addCleanup(executor.close)
        self.assertEqual(executor.get('lost')['status'],'interrupted')

    def test_cancelled_exception_does_not_fall_back_to_cold_checker(self):
        from regen.execution.proof_batches import check_batch
        # Cancellation is deliberately not RuntimeError/OSError/ValueError.
        self.assertFalse(issubclass(CheckCancelled,(RuntimeError,OSError,ValueError)))
        token=Cancellation();token.cancel()
        marker=CURRENT_TASK.set(token)
        try:
            with self.assertRaises(CheckCancelled):
                check_batch(None,{}, {})
        finally:
            CURRENT_TASK.reset(marker)

    def test_event_sinks_are_isolated_and_have_no_heartbeat_writes(self):
        barrier=threading.Barrier(2)
        def run(name):
            directory=self.root/name
            with event_scope(directory,run_id='run',theory=name):
                barrier.wait(3)
                emit_event('isabelle_started')
                before=(directory/'runner-status.json').read_bytes()
                emit_event('llm_heartbeat')
                self.assertEqual(before,(directory/'runner-status.json').read_bytes())
            return [json.loads(line) for line in (directory/'runner-events.ndjson').read_text().splitlines()]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(run,name) for name in ('A','B')]
            for name,future in zip(('A','B'),futures):
                self.assertTrue(all(row['theory']==name for row in future.result()))

    def test_head_is_read_before_paths(self):
        # A commit appended after the captured HEAD is a complete valid prefix;
        # a newer HEAD read after an old list used to produce a false corruption error.
        directory=self.root/'journal'
        durable(directory/'HEAD.json',{'sequence':1})
        durable(directory/'commits'/'000001.json',{})
        from unittest.mock import patch
        original=Path.glob
        def append_after_listing(path,pattern):
            paths=list(original(path,pattern))
            if path==directory/'commits':
                durable(directory/'commits'/'000002.json',{})
                durable(directory/'HEAD.json',{'sequence':2})
            return iter(paths)
        with patch.object(Path,'glob',append_after_listing):
            paths,head=commit_paths(directory)
        self.assertEqual((len(paths),head['sequence']),(1,1))


class ResourceTests(unittest.TestCase):
    def test_failed_eviction_retains_residency_until_cleanup_succeeds(self):
        class Checker:
            def __init__(self):self.live=False;self.fail=False
            def close_prefix(self):
                if self.fail:raise TimeoutError('worker still live')
                self.live=False
        a,b=Checker(),Checker()
        pool=NativeResources(1,1,1)
        self.addCleanup(pool.close)
        with pool.lease(a):a.live=True
        a.fail=True
        try:
            for _ in range(2):
                with self.assertRaises(TimeoutError):
                    with pool.lease(b):b.live=True
                self.assertEqual(list(pool.residents),[id(a)])
                self.assertTrue(a.live);self.assertFalse(b.live)
                self.assertFalse(pool.active);self.assertFalse(pool.queue)
        finally:a.fail=False
        with pool.lease(b):b.live=True
        self.assertFalse(a.live)
        self.assertEqual(list(pool.residents),[id(b)])

    def test_two_native_leases_overlap_with_independent_owners(self):
        resources=NativeResources(slots=2,threads=1,resident_limit=2)
        barrier=threading.Barrier(2)
        class Checker:
            def close_prefix(self):pass
            @native_operation
            def check(self):barrier.wait(timeout=3);return True
        def run(checker):
            marker=CURRENT_RESOURCES.set(resources)
            try:return checker.check()
            finally:CURRENT_RESOURCES.reset(marker)
        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks=[pool.submit(run,Checker()) for _ in range(2)]
            self.assertTrue(all(task.result() for task in tasks))
        resources.close()

    def test_nested_lease_is_not_a_second_slot_and_idle_prefix_is_evicted(self):
        resources=NativeResources(slots=1,threads=1,resident_limit=1)
        class Checker:
            closed=0
            def close_prefix(self):self.closed+=1
        a,b=Checker(),Checker()
        with resources.lease(a):
            with resources.lease(a):self.assertEqual(len(resources.active),1)
        with resources.lease(b):self.assertEqual(a.closed,1)
        resources.close()

    def test_two_residents_retain_prefixes_with_one_execution_slot(self):
        resources=NativeResources(slots=1,threads=1,resident_limit=2)
        class Checker:
            closed=0
            def close_prefix(self):self.closed+=1
        a,b=Checker(),Checker()
        for checker in (a,b,a,b):
            with resources.lease(checker):
                self.assertEqual(len(resources.active),1)
        self.assertEqual((a.closed,b.closed),(0,0))
        resources.close()
        self.assertEqual((a.closed,b.closed),(1,1))
