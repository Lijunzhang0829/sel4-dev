"""The supervised run identity must match the runner's actual argv target."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from regen.tools import parallel_workspace_service as service


class SupervisorArgvTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.prefix = [sys.executable, '-u', '-m',
                       'regen.execution.proof_session_runner', 'parallel-workspace']
        self.arguments = ['--run-dir', str(self.root), '--allow-external-call']

    def manifest(self, arguments=None, prefix=None):
        command = (self.prefix if prefix is None else prefix) + (
            self.arguments if arguments is None else arguments)
        (self.root/'experiment.json').write_text(json.dumps({
            'command':command, 'cwd':str(self.root)}))
        return service._manifest(self.root)[0]

    def test_valid_exact_command_is_preserved(self):
        self.assertEqual(self.manifest()['command'], self.prefix+self.arguments)

    def test_unbuffered_python_is_optional(self):
        prefix = [item for item in self.prefix if item != '-u']
        self.assertEqual(self.manifest(prefix=prefix)['command'], prefix+self.arguments)

    def test_equals_duplicate_cannot_override_bound_run(self):
        with self.assertRaisesRegex(ValueError, 'ambiguous runner option'):
            self.manifest(self.arguments+['--run-dir=/another-run'])

    def test_abbreviations_and_unknown_options_are_rejected(self):
        for flag in ('--run-di', '--run', '--native-s', '--unknown', '--'):
            with self.subTest(flag=flag), self.assertRaisesRegex(ValueError, 'ambiguous runner option'):
                self.manifest(self.arguments+[flag, '/another-run'])

    def test_duplicate_options_are_rejected_except_theory(self):
        for option, value in (('--run-dir', str(self.root)), ('--native-slots', '2')):
            args = self.arguments+[option, value]
            if option != '--run-dir':
                args += [option, value]
            with self.subTest(option=option), self.assertRaisesRegex(ValueError, 'duplicate runner option'):
                self.manifest(args)
        with self.assertRaisesRegex(ValueError, 'duplicate runner option'):
            self.manifest(self.arguments+['--allow-external-call'])
        self.manifest(self.arguments+['--theory', 'A', '--theory', 'B'])

    def test_run_value_cannot_be_an_option(self):
        for arguments in (['--run-dir'], ['--run-dir', '--allow-external-call']):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(ValueError, 'missing runner option value'):
                self.manifest(arguments)

    def test_missing_or_different_target_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'exactly one --run-dir'):
            self.manifest(['--allow-external-call'])
        with self.assertRaisesRegex(ValueError, 'another run directory'):
            self.manifest(['--run-dir', '/another-run'])

    def test_start_cli_reports_immediate_service_failure(self):
        for status, expected in (('failed', 1), ('stopped', 1),
                                 ('running', 0), ('process_completed', 0)):
            with self.subTest(status=status), \
                    patch.object(sys, 'argv', ['service', 'start', '--run-dir', str(self.root)]), \
                    patch.object(service, 'start', return_value={'status':status}), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(service.main(), expected)


if __name__ == '__main__':
    unittest.main()
