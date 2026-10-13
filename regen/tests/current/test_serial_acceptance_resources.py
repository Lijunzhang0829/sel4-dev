"""Resource validation for real serial acceptance; no simulated native success."""
import unittest
from unittest.mock import patch

from regen.tools import serial_application_acceptance as acceptance


class SerialAcceptanceResourcesTests(unittest.TestCase):
    def test_invalid_limits_fail_before_creating_output_or_loading_context(self):
        for limits in ({'native_memory_mb': 0}, {'native_threads': 0},
                       {'native_cpus': float('nan')}):
            with self.subTest(limits=limits), \
                    patch.object(acceptance.Path, 'mkdir') as mkdir, \
                    patch.object(acceptance, 'load_context') as load:
                with self.assertRaises(ValueError):
                    acceptance.run('/unused', '/unused-output', **limits)
                mkdir.assert_not_called()
                load.assert_not_called()

    def test_requested_caps_are_forwarded_without_changing_single_slot_scope(self):
        with patch.object(acceptance, 'NativeResources') as resources, \
                patch.object(acceptance.Path, 'mkdir'), \
                patch.object(acceptance, 'load_context', side_effect=RuntimeError('stop before native')):
            with self.assertRaisesRegex(RuntimeError, 'stop before native'):
                acceptance.run('/unused', '/unused-output', native_memory_mb=10240,
                               native_threads=4, native_cpus=4)
            resources.assert_called_once_with(1, 4, 1, memory_mb=10240, cpus=4)


if __name__ == '__main__':
    unittest.main()
