"""Deterministic memory guard checks; never start workers or services."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from regen.tools import parallel_workspace_memory_guard as guard


class MemoryGuardTests(unittest.TestCase):
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
        self.clock = 0

    def sleep(self, duration):
        self.clock += duration

    def run_guard(self, samples=()):
        return guard.monitor(self.root, status=self.status, stop=self.stop,
                             memory=Mock(side_effect=samples),
                             monotonic=lambda: self.clock, sleep=self.sleep)

    def test_terminal_service_exits_without_sampling_or_stop(self):
        self.status.return_value = self.dead
        result = self.run_guard()
        self.assertEqual(result['status'], 'service_finished')
        self.assertEqual(result['samples'], 0)
        self.stop.assert_not_called()

    def test_identity_mismatch_never_stops(self):
        self.status.return_value = {**self.live, 'identity_verified': False}
        with self.assertRaisesRegex(RuntimeError, 'identity unverified'):
            self.run_guard()
        self.stop.assert_not_called()
        record = json.loads((self.root / 'memory-guard.json').read_text())
        self.assertEqual(record['status'], 'guard_error')

    def test_sustained_pressure_stops_once_after_four_seconds(self):
        result = self.run_guard([5 * guard.GiB] * 3)
        self.assertEqual(self.clock, 4)
        self.assertEqual(result['stop_reason'], 'sustained_low_memory')
        self.assertEqual(result['stop_receipt'], self.dead)
        self.stop.assert_called_once_with(self.root, expected_receipt_sha256=self.receipt_sha)

    def test_recovery_resets_pressure_duration(self):
        result = self.run_guard([5, 5, 8, 5, 5, 5][i] * guard.GiB for i in range(6))
        self.assertEqual(self.clock, 10)
        self.assertEqual(result['recoveries'], 1)
        self.assertEqual(result['stop_reason'], 'sustained_low_memory')

    def test_emergency_stops_immediately(self):
        result = self.run_guard([guard.GiB])
        self.assertEqual(self.clock, 0)
        self.assertEqual(result['stop_reason'], 'emergency_low_memory')
        self.stop.assert_called_once()

    def test_sensor_failure_stops_verified_service_and_records_diagnostic(self):
        result = self.run_guard([OSError('sensor unavailable')])
        self.assertEqual(result['stop_reason'], 'memory_sampling_failed')
        self.assertIn('sensor unavailable', result['diagnostic'])
        self.stop.assert_called_once()

    def test_changed_identity_before_stop_is_rejected(self):
        self.status.side_effect = [self.live, {**self.live, 'identity_verified': False}]
        with self.assertRaisesRegex(RuntimeError, 'identity unverified'):
            self.run_guard([guard.GiB])
        self.stop.assert_not_called()

    def test_replaced_receipt_is_rejected(self):
        def changed(_):
            (self.root / 'service-launch.json').write_text(json.dumps({**self.receipt, 'replacement': True}))
            return self.live
        self.status.side_effect = changed
        with self.assertRaisesRegex(RuntimeError, 'receipt changed'):
            self.run_guard([guard.GiB])
        self.stop.assert_not_called()

    def test_stop_failure_persists_diagnostic(self):
        self.stop.side_effect = RuntimeError('stop transport failed')
        with self.assertRaisesRegex(RuntimeError, 'stop transport failed'):
            self.run_guard([guard.GiB])
        record = json.loads((self.root / 'memory-guard.json').read_text())
        self.assertEqual(record['status'], 'guard_error')
        self.assertIn('stop transport failed', record['diagnostic'])

    def test_checkpoint_disk_failure_does_not_prevent_emergency_stop(self):
        real_durable = guard.durable
        def write(path, record):
            if record['status'] in {'stopping', 'stopped_for_memory'}:
                raise OSError('disk full')
            return real_durable(path, record)
        with patch.object(guard, 'durable', side_effect=write), patch.object(guard.sys, 'stderr'):
            result = self.run_guard([guard.GiB])
        self.stop.assert_called_once()
        self.assertEqual(result['status'], 'stopped_for_memory')
        self.assertIn('disk full', result['checkpoint_error'])

    def test_meminfo_uses_global_available_not_free(self):
        with patch.object(Path, 'read_text', return_value='MemFree: 1 kB\nMemAvailable: 7 kB\n'):
            self.assertEqual(guard.available_memory(), 7168)
        with patch.object(Path, 'read_text', return_value='MemFree: 1 kB\n'):
            with self.assertRaisesRegex(RuntimeError, 'MemAvailable'):
                guard.available_memory()

    def test_invalid_policy_rejected(self):
        for options in ({'min_available_bytes': 1}, {'min_available_bytes': True},
                        {'interval_seconds': 0}, {'pressure_seconds': float('nan')}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                guard.monitor(self.root, **options)


if __name__ == '__main__':
    unittest.main()
