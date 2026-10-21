"""Resource-guard fault injection; no services, models or native workers."""
import errno
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from regen.tools import parallel_workspace_memory_guard as guard


class ResourceGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.receipt = {'run_dir': str(self.root), 'unit': guard.service._unit(self.root)}
        (self.root / 'service-launch.json').write_text(json.dumps(self.receipt))
        self.receipt_sha = hashlib.sha256((self.root / 'service-launch.json').read_bytes()).hexdigest()
        self.live = {'unit': self.receipt['unit'], 'running': True,
                     'identity_verified': True, 'status': 'running'}
        self.dead = {**self.live, 'running': False, 'status': 'stopped'}
        self.status = Mock(return_value=self.live)
        self.stop = Mock(return_value=self.dead)
        self.memory = Mock(return_value=16 * guard.GiB)
        self.disk = Mock(return_value=16 * guard.GiB)
        self.clock = 0

    def sleep(self, duration):
        self.clock += duration

    def run_guard(self, **options):
        settings = dict(status=self.status, stop=self.stop, memory=self.memory,
                        disk=self.disk, min_free_disk_bytes=8 * guard.GiB,
                        monotonic=lambda: self.clock, sleep=self.sleep)
        settings.update(options)
        return guard.monitor(self.root, **settings)

    def test_disk_sustained_pressure_stops_after_thirty_seconds(self):
        self.disk.return_value = 7 * guard.GiB
        result = self.run_guard()
        self.assertEqual(self.clock, 30)
        self.assertEqual(result['samples'], 16)
        self.assertEqual(result['stop_reason'], 'sustained_low_disk')
        self.assertEqual(result['status'], 'stopped_for_disk')
        self.assertEqual(result['free_disk_bytes'], 7 * guard.GiB)
        self.disk.assert_called_with(self.root)
        self.stop.assert_called_once_with(self.root, expected_receipt_sha256=self.receipt_sha)

    def test_disk_emergency_stops_on_first_sample(self):
        self.disk.return_value = guard.GiB
        result = self.run_guard()
        self.assertEqual(self.clock, 0)
        self.assertEqual(result['stop_reason'], 'emergency_low_disk')
        self.stop.assert_called_once_with(self.root, expected_receipt_sha256=self.receipt_sha)

    def test_disk_floor_is_strict_and_recovery_resets_timer(self):
        self.disk.side_effect = [v * guard.GiB for v in (7, 7, 8, 7, 7, 7)]
        result = self.run_guard(disk_pressure_seconds=4)
        self.assertEqual(self.clock, 10)
        self.assertEqual(result['disk_recoveries'], 1)
        self.assertEqual(result['stop_reason'], 'sustained_low_disk')

    def test_emergency_floor_is_strict(self):
        self.disk.return_value = 2 * guard.GiB
        result = self.run_guard(disk_pressure_seconds=4)
        self.assertEqual(self.clock, 4)
        self.assertEqual(result['stop_reason'], 'sustained_low_disk')

    def test_disk_and_memory_pressure_timers_are_independent(self):
        self.memory.side_effect = [v * guard.GiB for v in (5, 8, 8, 8)]
        self.disk.side_effect = [v * guard.GiB for v in (16, 7, 7, 7)]
        result = self.run_guard(disk_pressure_seconds=4)
        self.assertEqual(self.clock, 6)
        self.assertEqual(result['recoveries'], 1)
        self.assertEqual(result['stop_reason'], 'sustained_low_disk')

    def test_memory_emergency_does_not_wait_for_disk_sample(self):
        self.memory.return_value = guard.GiB
        result = self.run_guard()
        self.assertEqual(result['stop_reason'], 'emergency_low_memory')
        self.disk.assert_not_called()

    def test_api_can_keep_memory_only_behavior(self):
        self.status.side_effect = [self.live, self.dead]
        result = self.run_guard(min_free_disk_bytes=None)
        self.assertEqual(result['status'], 'service_finished')
        self.disk.assert_not_called()
        self.stop.assert_not_called()

    def test_disk_sensor_failure_fails_closed(self):
        self.disk.side_effect = OSError('statvfs unavailable')
        result = self.run_guard()
        self.assertEqual(result['stop_reason'], 'disk_sampling_failed')
        self.assertIn('statvfs unavailable', result['diagnostic'])
        self.stop.assert_called_once_with(self.root, expected_receipt_sha256=self.receipt_sha)

    def test_invalid_disk_sensor_samples_fail_closed(self):
        for sample in (True, -1, float('nan'), '1', None):
            with self.subTest(sample=sample):
                self.disk.return_value = sample
                self.stop.reset_mock()
                result = self.run_guard()
                self.assertEqual(result['stop_reason'], 'disk_sampling_failed')
                self.stop.assert_called_once_with(self.root, expected_receipt_sha256=self.receipt_sha)

    def test_startup_checkpoint_enospc_still_stops_verified_task(self):
        with patch.object(guard, 'durable', side_effect=OSError(errno.ENOSPC, 'disk full')), \
                patch.object(guard.sys, 'stderr'):
            result = self.run_guard()
        self.assertEqual(result['stop_reason'], 'checkpoint_failed')
        self.assertEqual(result['status'], 'stopped_for_guard')
        self.assertEqual(result['samples'], 0)
        self.memory.assert_not_called()
        self.stop.assert_called_once_with(self.root, expected_receipt_sha256=self.receipt_sha)

    def test_startup_checkpoint_failure_never_stops_unverified_task(self):
        self.status.return_value = {**self.live, 'identity_verified': False}
        with patch.object(guard, 'durable', side_effect=OSError(errno.ENOSPC, 'disk full')), \
                patch.object(guard.sys, 'stderr'):
            with self.assertRaisesRegex(RuntimeError, 'identity unverified'):
                self.run_guard()
        self.stop.assert_not_called()

    def test_checkpoint_serialization_failure_also_fails_closed(self):
        with patch.object(guard, 'durable', side_effect=ValueError('serialization failed')), \
                patch.object(guard.sys, 'stderr'):
            result = self.run_guard()
        self.assertEqual(result['stop_reason'], 'checkpoint_failed')
        self.stop.assert_called_once_with(self.root, expected_receipt_sha256=self.receipt_sha)

    def test_checkpoint_failure_with_failed_stop_preserves_diagnostic(self):
        real_durable = guard.durable
        writes = 0

        def write(path, record):
            nonlocal writes
            writes += 1
            if writes == 1:
                raise OSError('startup checkpoint failed')
            return real_durable(path, record)

        self.stop.side_effect = RuntimeError('stop transport failed')
        with patch.object(guard, 'durable', side_effect=write):
            with self.assertRaisesRegex(RuntimeError, 'stop transport failed'):
                self.run_guard()
        record = json.loads((self.root / 'memory-guard.json').read_text())
        self.assertEqual(record['status'], 'guard_error')
        self.assertIn('stop transport failed', record['diagnostic'])
        self.stop.assert_called_once_with(self.root, expected_receipt_sha256=self.receipt_sha)

    def test_regular_checkpoint_failure_stops_while_resources_are_healthy(self):
        real_durable = guard.durable

        def write(path, record):
            if record['samples'] > 0:
                raise OSError(errno.ENOSPC, 'disk full')
            return real_durable(path, record)

        with patch.object(guard, 'durable', side_effect=write), \
                patch.object(guard.sys, 'stderr'):
            result = self.run_guard(checkpoint_seconds=4)
        self.assertEqual(self.clock, 4)
        self.assertEqual(result['stop_reason'], 'checkpoint_failed')
        self.stop.assert_called_once_with(self.root, expected_receipt_sha256=self.receipt_sha)

    def test_disk_emergency_checkpoint_failure_does_not_prevent_stop(self):
        self.disk.return_value = guard.GiB
        real_durable = guard.durable

        def write(path, record):
            if record['status'] in {'stopping', 'stopped_for_disk'}:
                raise OSError(errno.ENOSPC, 'disk full')
            return real_durable(path, record)

        with patch.object(guard, 'durable', side_effect=write), \
                patch.object(guard.sys, 'stderr'):
            result = self.run_guard()
        self.assertEqual(result['status'], 'stopped_for_disk')
        self.assertIn('disk full', result['checkpoint_error'])
        self.stop.assert_called_once_with(self.root, expected_receipt_sha256=self.receipt_sha)

    def test_service_stop_checkpoint_enospc_rechecks_without_stopping_twice(self):
        self.disk.return_value = guard.GiB
        self.status.side_effect = [self.live, self.live, self.dead]
        self.stop.side_effect = OSError(errno.ENOSPC, 'service status write failed')
        result = self.run_guard()
        self.assertEqual(result['stop_receipt'], self.dead)
        self.assertIn('service status write failed', result['stop_checkpoint_error'])
        self.stop.assert_called_once_with(self.root, expected_receipt_sha256=self.receipt_sha)

    def test_stop_oserror_is_not_success_when_task_is_still_running(self):
        self.disk.return_value = guard.GiB
        self.stop.side_effect = OSError('cannot reach service manager')
        with self.assertRaisesRegex(RuntimeError, 'could not confirm'):
            self.run_guard()
        self.stop.assert_called_once_with(self.root, expected_receipt_sha256=self.receipt_sha)

    def test_unit_disappearing_during_verified_stop_is_terminal(self):
        self.disk.return_value = guard.GiB
        self.stop.return_value = {**self.dead, 'identity_verified': False, 'status': 'not_loaded'}
        result = self.run_guard()
        self.assertEqual(result['status'], 'stopped_for_disk')
        self.assertFalse(result['stop_receipt']['running'])
        self.stop.assert_called_once_with(self.root, expected_receipt_sha256=self.receipt_sha)

    def test_checkpoint_failure_after_service_finished_never_stops_again(self):
        self.status.return_value = self.dead
        with patch.object(guard, 'durable', side_effect=OSError(errno.ENOSPC, 'disk full')), \
                patch.object(guard.sys, 'stderr'):
            result = self.run_guard()
        self.assertEqual(result['status'], 'service_finished')
        self.stop.assert_not_called()

    def test_disk_low_identity_change_before_stop_is_rejected(self):
        self.disk.return_value = guard.GiB
        self.status.side_effect = [self.live, {**self.live, 'identity_verified': False}]
        with self.assertRaisesRegex(RuntimeError, 'identity unverified'):
            self.run_guard()
        self.stop.assert_not_called()

    def test_receipt_replacement_during_final_status_check_never_stops(self):
        self.disk.return_value = guard.GiB

        def replaced(_):
            (self.root / 'service-launch.json').write_text(
                json.dumps({**self.receipt, 'replacement': True}))
            return self.live

        calls = iter((lambda _: self.live, replaced))
        self.status.side_effect = lambda root: next(calls)(root)
        with self.assertRaisesRegex(RuntimeError, 'receipt changed'):
            self.run_guard()
        self.stop.assert_not_called()

    def test_wrong_unit_from_stop_is_rejected(self):
        self.disk.return_value = guard.GiB
        self.stop.return_value = {**self.dead, 'unit': 'unrelated.service'}
        with self.assertRaisesRegex(RuntimeError, 'could not confirm'):
            self.run_guard()

    def test_transient_status_query_failure_revalidates_then_stops_once(self):
        self.status.side_effect = [self.live, RuntimeError('transient systemd query failure'),
                                   self.live, self.live]
        result = self.run_guard()
        self.assertEqual(result['status'], 'stopped_for_guard')
        self.assertEqual(result['stop_reason'], 'service_status_query_failed')
        self.assertEqual(result['status_query_failures'], 1)
        self.assertTrue(result['stop_confirmed'])
        self.assertEqual(self.clock, 4)
        self.assertEqual(result['samples'], 1)
        self.stop.assert_called_once_with(self.root, expected_receipt_sha256=self.receipt_sha)

    def test_persistent_query_failure_is_bounded_and_never_claims_safe_stop(self):
        self.status.side_effect = [self.live] + [RuntimeError('bus unavailable')] * 3
        with self.assertRaisesRegex(RuntimeError, 'safe stop cannot be confirmed'):
            self.run_guard()
        self.assertEqual(self.status.call_count, 4)
        self.assertEqual(self.clock, 6)
        self.stop.assert_not_called()
        record = json.loads((self.root / 'memory-guard.json').read_text())
        self.assertEqual(record['status'], 'guard_error')
        self.assertEqual(record['status_query_failures'], 3)
        self.assertFalse(record['stop_confirmed'])
        self.assertIn('bus unavailable', record['status_query_error'])
        self.assertIn('cannot be confirmed', record['diagnostic'])

    def test_recovered_query_with_new_identity_still_never_stops(self):
        self.status.side_effect = [RuntimeError('bus unavailable'),
                                   {**self.live, 'identity_verified': False}]
        with self.assertRaisesRegex(RuntimeError, 'identity unverified'):
            self.run_guard()
        self.stop.assert_not_called()

    def test_receipt_replacement_during_stopping_checkpoint_is_bound_in_service(self):
        self.disk.return_value = guard.GiB
        real_durable = guard.durable

        def replaced(path, record):
            if record['status'] == 'stopping':
                (self.root / 'service-launch.json').write_text(
                    json.dumps({**self.receipt, 'replacement': True}))
            return real_durable(path, record)

        with patch.object(guard, 'durable', side_effect=replaced), \
                patch.object(guard.service, 'status') as service_status, \
                patch.object(guard.service.subprocess, 'run') as external_stop:
            with self.assertRaisesRegex(RuntimeError, 'refusing to stop replacement owner'):
                self.run_guard(stop=guard.service.stop)
        service_status.assert_not_called()
        external_stop.assert_not_called()
        record = json.loads((self.root / 'memory-guard.json').read_text())
        self.assertEqual(record['status'], 'guard_error')
        self.assertFalse(record['stop_confirmed'])

    def test_checkpoints_are_periodic_not_every_sample(self):
        self.status.side_effect = [self.live] * 20 + [self.dead]
        with patch.object(guard, 'durable', wraps=guard.durable) as writer:
            result = self.run_guard()
        self.assertEqual(result['samples'], 20)
        self.assertEqual(self.clock, 40)
        self.assertEqual(writer.call_count, 3)  # startup, 30 seconds, terminal

    def test_event_history_is_bounded(self):
        self.disk.side_effect = [v * guard.GiB for v in ([7, 9] * 40 + [1])]
        result = self.run_guard()
        self.assertEqual(result['disk_recoveries'], 40)
        self.assertEqual(len(result['events']), 32)
        self.assertLess((self.root / 'memory-guard.json').stat().st_size, 8192)
        self.assertEqual(len(list(self.root.glob('*guard*'))), 1)

    def test_free_disk_sensor_uses_run_filesystem(self):
        with patch.object(guard.shutil, 'disk_usage', return_value=Mock(free=123)) as usage:
            self.assertEqual(guard.free_disk_bytes(self.root), 123)
        usage.assert_called_once_with(self.root)

    def test_invalid_disk_or_checkpoint_policies_are_rejected(self):
        policies = ({'min_free_disk_bytes': True}, {'min_free_disk_bytes': 1},
                    {'emergency_free_disk_bytes': 0}, {'emergency_free_disk_bytes': True},
                    {'emergency_free_disk_bytes': 9 * guard.GiB},
                    {'disk_pressure_seconds': -1}, {'disk_pressure_seconds': float('nan')},
                    {'checkpoint_seconds': 0}, {'checkpoint_seconds': float('inf')},
                    {'status_query_attempts': 0}, {'status_query_attempts': True},
                    {'status_query_attempts': 11}, {'status_retry_seconds': -1},
                    {'status_retry_seconds': float('nan')})
        for options in policies:
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.run_guard(**options)
        self.stop.assert_not_called()

    def test_cli_enables_disk_protection_by_default(self):
        with patch.object(guard, 'monitor', return_value={}) as monitor, \
                patch.object(guard.sys, 'argv', ['guard', '--run-dir', str(self.root)]), \
                patch('builtins.print'):
            self.assertEqual(guard.main(), 0)
        self.assertEqual(monitor.call_args.kwargs['min_free_disk_bytes'], 8 * guard.GiB)
        self.assertEqual(monitor.call_args.kwargs['emergency_free_disk_bytes'], 2 * guard.GiB)
        self.assertEqual(monitor.call_args.kwargs['disk_pressure_seconds'], 30)
        self.assertEqual(monitor.call_args.kwargs['checkpoint_seconds'], 30)


if __name__ == '__main__':
    unittest.main()
