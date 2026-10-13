import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from regen.tools.contract_closure_validation import native_exit_gate


class NativeExitInspectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.name = 'regen-isabelle-build-123-' + 'a' * 32
        (self.root / 'service-invocation.json').write_text(json.dumps({'main_pid': 123}))
        (self.root / 'service-runner.log').write_text('[regen-event] ' + json.dumps({
            'event': 'isabelle_build_started', 'container': self.name}) + '\n')

    def inspect_error(self, message):
        return SimpleNamespace(returncode=1, stderr=message, stdout='[]')

    def test_exact_absence_accepts_docker_spelling_variants(self):
        for kind in ('object', 'container'):
            for prefix in ('', 'Error: ', 'Error response from daemon: '):
                for capitalize in (str.lower, str.upper, str.title):
                    message = capitalize(prefix + 'No such ' + kind + ': ') + self.name + '\n'
                    with self.subTest(message=message), patch(
                            'regen.tools.contract_closure_validation.subprocess.run',
                            return_value=self.inspect_error(message)) as run:
                        self.assertEqual(native_exit_gate(self.root, grace_seconds=0), {
                            'owned_native_containers': [self.name], 'all_exited': True})
                        run.assert_called_once_with(['docker', 'inspect', self.name],
                                                    capture_output=True, text=True, timeout=15)

    def test_other_objects_and_partial_names_do_not_prove_exit(self):
        for name in ('another-container', self.name[:-1], self.name + '-extra', self.name.upper()):
            for kind in ('object', 'container'):
                with self.subTest(name=name, kind=kind), patch(
                        'regen.tools.contract_closure_validation.subprocess.run',
                        return_value=self.inspect_error('error: no such ' + kind + ': ' + name)) as run:
                    with self.assertRaisesRegex(RuntimeError, 'native identity inspection failed'):
                        native_exit_gate(self.root, grace_seconds=0)
                    self.assertEqual(run.call_count, 1)

    def test_inspect_errors_never_count_as_absence(self):
        missing = 'error: no such object: ' + self.name
        messages = (
            'Cannot connect to the Docker daemon at unix:///var/run/docker.sock',
            'permission denied while trying to connect to the Docker daemon socket',
            'error during connect: dial unix /var/run/docker.sock: no such file or directory',
            'Error response from daemon: context deadline exceeded',
            'Error response from daemon: permission denied: No such object: ' + self.name,
            missing + '\nCannot connect to the Docker daemon',
            'permission denied\n' + missing,
            '',
        )
        for message in messages:
            with self.subTest(message=message), patch(
                    'regen.tools.contract_closure_validation.subprocess.run',
                    return_value=self.inspect_error(message)) as run:
                with self.assertRaisesRegex(RuntimeError, 'native identity inspection failed'):
                    native_exit_gate(self.root, grace_seconds=0)
                self.assertEqual(run.call_count, 1)

    def test_inspect_timeout_propagates_without_stopping(self):
        with patch('regen.tools.contract_closure_validation.subprocess.run',
                   side_effect=subprocess.TimeoutExpired(['docker', 'inspect', self.name], 15)) as run:
            with self.assertRaises(subprocess.TimeoutExpired):
                native_exit_gate(self.root, grace_seconds=0)
            self.assertEqual(run.call_count, 1)

    def test_container_removed_after_owned_stop_counts_as_exited(self):
        info = {'Name': '/' + self.name,
                'Config': {'Cmd': ['/bin/sh', '/native-owner/owner.sh', '60',
                                   '/native-owner/owner.heartbeat']},
                'State': {'Running': True}}
        live = SimpleNamespace(returncode=0, stderr='', stdout=json.dumps([info]))
        with patch('regen.tools.contract_closure_validation.subprocess.run', side_effect=[
                live, live, SimpleNamespace(returncode=0),
                self.inspect_error('error: no such object: ' + self.name)]) as run:
            self.assertTrue(native_exit_gate(self.root, grace_seconds=0)['all_exited'])
            self.assertEqual(run.call_args_list[2].args[0],
                             ['docker', 'stop', '--timeout', '10', self.name])
            self.assertEqual(run.call_count, 4)

    def test_successful_inspect_still_requires_exact_name_and_owner(self):
        owner = ['/bin/sh', '/native-owner/owner.sh', '60', '/native-owner/owner.heartbeat']
        for name, command in (('/different-container', owner), ('/' + self.name, ['unrelated'])):
            info = {'Name': name, 'Config': {'Cmd': command}, 'State': {'Running': True}}
            with self.subTest(name=name, command=command), patch(
                    'regen.tools.contract_closure_validation.subprocess.run',
                    return_value=SimpleNamespace(returncode=0, stderr='', stdout=json.dumps([info]))) as run:
                with self.assertRaisesRegex(RuntimeError, 'owner protocol differs; refusing cleanup'):
                    native_exit_gate(self.root, grace_seconds=0)
                self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main()
