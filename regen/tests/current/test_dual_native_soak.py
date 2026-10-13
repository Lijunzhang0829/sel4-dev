"""Driver contracts only; no mocked assertion substitutes for native receipts."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from regen.execution.batch_checker import body
from regen.execution.check_executor import CURRENT_RESOURCES, NativeResources
from regen.tools.dual_native_soak import (
    GiB, Recorder, ResourceGuard, SoakChecker, cleanup_registered, native_window,
    output_bytes, overlap_seconds, prune_transactions, run, source,
    unexpected_task_errors, validate_options, workload,
)


class DualNativeSoakTests(unittest.TestCase):
    def test_workload_is_bounded_real_proofs_not_sleep_or_admission(self):
        first, names, tag = workload(1, 2, 8)
        later, later_names, _ = workload(1, 999999, 8)
        self.assertEqual(names, later_names)
        self.assertEqual(first.count('by (induct xs) simp_all'), 8)
        self.assertIn('DUAL_SOAK_START 1_2', first)
        self.assertIn('DUAL_SOAK_END 1_2', first)
        self.assertEqual(tag, '1_2')
        for forbidden in ('sleep', 'sorry', 'oops', 'axiomatization'):
            self.assertNotIn(forbidden, first)
        self.assertLess(len(later)-len(first), 20)

    def test_exact_source_body_preserved(self):
        content = '\nunbundle l4v_word_context\nlemma a: "True" by simp\n'
        self.assertEqual(body(source('Semantic_Base.Semantic_Root', content)), content)

    def test_markers_need_actual_complete_native_window(self):
        self.assertEqual(native_window('DUAL_SOAK_START 1_2 10.125\nDUAL_SOAK_END 1_2 11.75\n', '1_2'),
                         {'start':10.125, 'end':11.75})
        for log in ('', 'DUAL_SOAK_START 1_2 10.125\n',
                    'DUAL_SOAK_START 1_2 10.0\nDUAL_SOAK_END 1_2 9.0\n',
                    'DUAL_SOAK_START 1_2 1.0\nDUAL_SOAK_START 1_2 2.0\nDUAL_SOAK_END 1_2 3.0\n'):
            with self.assertRaises(ValueError):
                native_window(log, '1_2')

    def test_overlap_uses_native_intervals_not_thread_lifetime(self):
        self.assertEqual(overlap_seconds({1:[(1, 4), (8, 11)], 2:[(3, 6), (7, 10)]}), 3)
        self.assertEqual(overlap_seconds({1:[(1, 2)], 2:[(2, 3)]}), 0)
        self.assertEqual(overlap_seconds({1:[], 2:[(2, 3)]}), 0)

    def test_resource_guard_sustained_low_floor_and_recovery(self):
        guard = ResourceGuard(grace_seconds=30)
        low = {'available_memory_bytes':5*GiB, 'free_disk_bytes':9*GiB}
        healthy = {'available_memory_bytes':7*GiB, 'free_disk_bytes':9*GiB}
        guard.check(low, 0)
        guard.check(low, 29)
        guard.check(healthy, 30)
        guard.check(low, 31)
        guard.check(low, 60)
        with self.assertRaisesRegex(RuntimeError, 'sustained'):
            guard.check(low, 61)

    def test_resource_guard_emergency_does_not_wait_for_grace(self):
        for key in ('available_memory_bytes', 'free_disk_bytes'):
            sample = {'available_memory_bytes':10*GiB, 'free_disk_bytes':10*GiB}
            sample[key] = GiB
            with self.assertRaisesRegex(RuntimeError, 'emergency'):
                ResourceGuard().check(sample, 0)

    def test_default_memory_guard_is_four_seconds_not_disk_grace(self):
        guard = ResourceGuard()
        low = dict(available_memory_bytes=5*GiB, free_disk_bytes=7*GiB)
        guard.check(low, 0)
        with self.assertRaisesRegex(RuntimeError, 'available_memory_bytes'):
            guard.check(low, 4)

    def test_real_error_at_deadline_cannot_be_masked_by_normal_cancel(self):
        rows = [{'status':'error', 'error_type':'AssertionError'},
                {'status':'error', 'error_type':'InterruptedError'},
                {'status':'error', 'error_type':'BrokenBarrierError'},
                {'status':'cancelled'}, {'status':'completed'}]
        self.assertEqual(unexpected_task_errors(rows, 'duration_complete'), [rows[0]])
        self.assertEqual(unexpected_task_errors(rows, 'failure'), rows[:3])

    def test_output_counter_ignores_only_concurrently_removed_files(self):
        missing = SimpleNamespace(is_file=lambda:True)
        with patch.object(Path, 'rglob', return_value=[missing]):
            missing.stat = lambda: (_ for _ in ()).throw(FileNotFoundError())
            self.assertEqual(output_bytes(Path('/unused')), 0)
            missing.stat = lambda: (_ for _ in ()).throw(PermissionError())
            with self.assertRaises(PermissionError):
                output_bytes(Path('/unused'))

    def test_options_reject_over_cap_or_over_authorized_duration(self):
        validate_options(7200, 10240, 4, 4, 64)
        for values in ((7201,10240,4,4,64), (29,10240,4,4,64),
                       (7200,16384,4,4,64), (7200,10240,8,4,64),
                       (7200,10240,4,8,64), (7200,10240,4,4,0),
                       (float('nan'),10240,4,4,64)):
            with self.assertRaises(ValueError):
                validate_options(*values)

    def test_limits_validated_before_loading_assets(self):
        with patch('regen.tools.dual_native_soak.load_context') as load:
            with self.assertRaises(ValueError):
                run('/unused', '/unused', memory_mb=16384)
            load.assert_not_called()

    def test_readonly_parent_heap_and_real_production_caps(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checker = SoakChecker(root, root/'worker', generated_dir=root/'generated',
                heaps_dir=root/'heaps', l4v_dir=root/'l4v', semantic_workspace=root/'semantic',
                isabelle_dir=root/'isabelle')
            resources = NativeResources(2, 4, 2, memory_mb=10240)
            token = CURRENT_RESOURCES.set(resources)
            try:
                args = checker._command(root/'test.thy', session='Semantic_Base')
            finally:
                CURRENT_RESOURCES.reset(token)
            self.assertIn(f'type=bind,src={root}/heaps,dst=/root/.isabelle/heaps,readonly', args)
            self.assertEqual(args[args.index('--memory')+1], '10240m')
            self.assertEqual(args[args.index('--memory-swap')+1], '10240m')
            self.assertEqual(args[args.index('--cpus')+1], '4')
            self.assertIn('threads=4', args)
            self.assertIn('ML_OPTIONS=-H 1024 --maxheap 7680 --stackspace 64 --gcthreads 4', args)
            executable='/workspace/verification/isabelle/bin/isabelle'
            at=args.index(executable)
            self.assertEqual(args[at+1], 'env')
            self.assertEqual(args[at+3:at+5], [executable, 'process'])
            self.assertEqual(args[args.index('--network')+1], 'none')

    def test_pruning_only_completed_owned_numbered_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ('prefix', 'worker.log', '1.request', '1.audit.xml', '2.request', '3.request', '11.request'):
                (root/name).write_text('data')
            (root/'1.symlink').symlink_to(root/'prefix')
            worker = SimpleNamespace(root=root, index=2)
            self.assertEqual(prune_transactions(worker, 1, 2), 12)
            for name in ('prefix', 'worker.log', '3.request', '11.request', '1.symlink'):
                self.assertTrue((root/name).exists())
            with self.assertRaises(ValueError):
                prune_transactions(worker, 2, 3)

    def test_kill_rejects_unregistered_names_before_subprocess(self):
        with tempfile.TemporaryDirectory() as temporary:
            recorder = Recorder(Path(temporary), 60, {})
            with patch('regen.tools.dual_native_soak.subprocess.run') as command:
                with self.assertRaises(ValueError):
                    recorder.kill_owned('user-important-container')
                command.assert_not_called()
            with self.assertRaises(ValueError):
                recorder.register(1, 'user-important-container')

    def test_cleanup_only_registered_names_and_requires_absence_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            recorder = Recorder(Path(temporary), 60, {})
            name = 'regen-batch-'+'a'*16
            recorder.register(1, name)
            success = SimpleNamespace(returncode=0, stdout='', stderr='')
            absent = SimpleNamespace(returncode=1, stdout='', stderr='Error: No such object: '+name)
            with patch('regen.tools.dual_native_soak.subprocess.run', side_effect=[success, absent]) as command:
                result = cleanup_registered(recorder)
            self.assertTrue(result[0]['confirmed_absent'])
            self.assertEqual(command.call_args_list[0].args[0], ['docker','rm','-f',name])
            failed = SimpleNamespace(returncode=1, stdout='', stderr='Cannot connect to Docker daemon')
            with patch('regen.tools.dual_native_soak.subprocess.run', return_value=failed):
                self.assertFalse(cleanup_registered(recorder)[0]['confirmed_absent'])

    def test_status_records_wall_deadline_and_does_not_claim_effective_time(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recorder = Recorder(root, 60, {})
            recorder.checkpoint(SimpleNamespace(peak_active=2), {'free_disk_bytes':10*GiB})
            status = json.loads((root/'status.json').read_text())
            self.assertIn('expected_workload_end_at', status)
            self.assertEqual(status['native_peak_active'], 2)
            self.assertNotIn('effective_seconds', status)
            self.assertEqual(len((root/'events.jsonl').read_text().splitlines()), 1)

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch('regen.tools.dual_native_soak.load_context', return_value={}):
                with self.assertRaises(FileExistsError):
                    run('/fixture', temporary)

    def test_startup_refuses_insufficient_headroom_without_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)/'new-run'
            ctx = dict(checker_context_id='frozen-context', image='frozen-image', isabelle_sha256='sha')
            sample = dict(available_memory_bytes=25*GiB, free_disk_bytes=20*GiB)
            with patch('regen.tools.dual_native_soak.load_context', return_value=ctx), \
                 patch('regen.tools.dual_native_soak.implementation_hashes', return_value={}), \
                 patch('regen.tools.dual_native_soak.host_resources', return_value=sample), \
                 patch('regen.tools.dual_native_soak.CheckExecutor') as executor:
                report = run('/fixture', output, duration_seconds=60)
            executor.assert_not_called()
            self.assertFalse(report['passed'])
            self.assertIn('headroom', report['error']['message'])
            manifest = json.loads((output/'manifest.json').read_text())
            self.assertEqual(manifest['model_calls'], 0)
            self.assertIn('effective-generation clock', manifest['not_covered'])


if __name__ == '__main__':
    unittest.main()
