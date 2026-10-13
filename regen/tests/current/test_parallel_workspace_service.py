"""Supervisor boundary tests; no service or provider is actually launched."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from regen.tools import parallel_workspace_service as service


class ParallelWorkspaceServiceTests(unittest.TestCase):
    def test_focused_contract_is_bound_without_ambiguous_duplicates(self):
        self.command.extend(['--focus-contract-id','contract:Test:target:123'])
        self.manifest()
        manifest, _ = service._manifest(self.root)
        self.assertEqual(manifest['command'], self.command)
        self.command.extend(['--focus-contract-id','contract:Test:other:456'])
        self.manifest()
        with self.assertRaisesRegex(ValueError, 'duplicate runner option'):
            service._manifest(self.root)

    def test_explicit_generation_strategy_is_bound_in_frozen_command(self):
        self.command.extend(['--generation-strategy','helper-closure-v1'])
        self.manifest()
        manifest, _ = service._manifest(self.root)
        self.assertEqual(manifest['command'],self.command)

    def test_explicit_provider_build_policy_is_allowed(self):
        self.command.extend(['--provider-build-timeout', '1800', '--build-ml-heap-mb', '12288'])
        self.manifest()
        manifest, _ = service._manifest(self.root)
        self.assertEqual(manifest['command'], self.command)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.command = [sys.executable, '-u', '-m',
            'regen.execution.proof_session_runner', 'parallel-workspace',
            '--run-dir', str(self.root), '--allow-external-call']
        self.manifest()

    def manifest(self, requirements=None):
        value = {'command':self.command, 'cwd':str(self.root)}
        if requirements is not None:
            value['resource_requirements'] = requirements
        (self.root/'experiment.json').write_text(json.dumps(value))

    def receipt(self, *, boot='boot-current', with_invocation=True):
        value = {'run_dir':str(self.root), 'unit':service._unit(self.root),
                 'description':'unique-test-description', 'boot_id':boot,
                 'stop_timeout_seconds':7}
        (self.root/'service-launch.json').write_text(json.dumps(value))
        if with_invocation:
            (self.root/'service-invocation.json').write_text(json.dumps({
                'invocation_id':'invocation-1', 'main_pid':123, 'start_ticks':'900'}))
        return value

    def running(self, **changes):
        receipt = json.loads((self.root/'service-launch.json').read_text())
        return {'LoadState':'loaded', 'ActiveState':'active', 'SubState':'running',
                'Description':receipt['description'], 'InvocationID':'invocation-1',
                'MainPID':'123', 'ExecMainPID':'123', 'ExecMainCode':'0',
                'ExecMainStatus':'0', 'Result':'success', **changes}

    def start_show(self, unit):
        self.assertEqual(unit, service._unit(self.root))
        if not (self.root/'service-launch.json').exists():
            return {'LoadState':'not-found'}
        return self.running()

    def test_resource_shortage_does_not_write_launch_receipt_or_start(self):
        self.manifest({'min_free_disk_bytes':100})
        with patch.object(service.shutil, 'disk_usage', return_value=SimpleNamespace(free=99)), \
             patch.object(service, '_show') as show, \
             patch.object(service.subprocess, 'run') as launch:
            with self.assertRaisesRegex(RuntimeError, 'insufficient resources'):
                service.start(self.root)
        show.assert_not_called()
        launch.assert_not_called()
        self.assertFalse((self.root/'service-launch.json').exists())

    def test_resource_admission_checks_disk_and_memory_in_bytes(self):
        requirements = {'min_free_disk_bytes':100, 'min_available_memory_bytes':2048}
        with patch.object(service.shutil, 'disk_usage', return_value=SimpleNamespace(free=100)), \
             patch.object(Path, 'read_text', return_value='MemTotal: 8 kB\nMemAvailable: 2 kB\n'):
            result = service._resource_admission(self.root, requirements)
            self.assertEqual(result['available_memory_bytes'], 2048)
            self.assertEqual(result['free_disk_bytes'], 100)
            with self.assertRaisesRegex(RuntimeError, 'min_available_memory_bytes'):
                service._resource_admission(self.root,
                    {**requirements, 'min_available_memory_bytes':2049})

    def test_invalid_resource_requirements_cannot_disable_checks(self):
        for requirements in ([], {'unknown':1}, {'min_free_disk_bytes':-1},
                             {'min_free_disk_bytes':True}, {'min_available_memory_bytes':'1'}):
            with self.subTest(requirements=requirements), \
                 self.assertRaisesRegex(ValueError, 'invalid experiment resource_requirements'):
                service._resource_admission(self.root, requirements)

    def test_launch_properties_ownership_and_secret_environment_boundary(self):
        def launched(command, **kwargs):
            self.assertTrue((self.root/'service-launch.json').exists())
            self.assertFalse((self.root/'service-invocation.json').exists())
            self.assertEqual(kwargs['timeout'], 30)
            import base64
            actual = command[command.index('--')+1:]
            self.assertEqual(json.loads(base64.b64decode(actual[-1])), self.command)
            self.assertNotIn('--expand-environment=no', command)
            for option in ('--user', '--no-ask-password', '--quiet', '--service-type=exec',
                           '--remain-after-exit',
                           '--property=Restart=no', '--property=KillMode=mixed',
                           '--property=KillSignal=SIGTERM', '--property=SendSIGKILL=yes',
                           '--property=TimeoutStopSec=7', '--property=StandardInput=null',
                           '--property=UMask=0077', '--setenv=REGEN_CODEX_BIN=/test/codex'):
                self.assertIn(option, command)
            self.assertIn('--property=StandardOutput=append:'+str(self.root/'service-runner.log'), command)
            self.assertIn('--property=StandardError=append:'+str(self.root/'service-runner.log'), command)
            self.assertNotIn('test-secret-sentinel', ' '.join(command))
            return subprocess.CompletedProcess(command, 0, '', '')
        with patch.object(service, '_boot_id', return_value='boot-current'), \
             patch.object(service, '_start_ticks', return_value='900'), \
             patch.object(service, '_resource_admission', return_value={'requirements':{}}), \
             patch.object(service, '_show', side_effect=self.start_show), \
             patch.object(service.subprocess, 'run', side_effect=launched) as launch, \
             patch.dict(service.os.environ, {'REGEN_CODEX_BIN':'/test/codex',
                                            'REGEN_TEST_SECRET':'test-secret-sentinel'}, clear=True):
            result = service.start(self.root, stop_timeout=7)
        launch.assert_called_once()
        self.assertEqual(result['status'], 'running')
        self.assertTrue(result['identity_verified'])
        receipt = json.loads((self.root/'service-launch.json').read_text())
        self.assertEqual(receipt['restart_policy'], 'never')
        self.assertNotIn('test-secret-sentinel', json.dumps(receipt))
        invocation = json.loads((self.root/'service-invocation.json').read_text())
        self.assertEqual(invocation, {'invocation_id':'invocation-1', 'main_pid':123, 'start_ticks':'900'})

    def test_launch_timeout_keeps_receipt_and_prevents_replay(self):
        with patch.object(service, '_boot_id', return_value='boot-current'), \
             patch.object(service, '_resource_admission', return_value={'requirements':{}}), \
             patch.object(service, '_show', return_value={'LoadState':'not-found'}), \
             patch.object(service.subprocess, 'run', side_effect=subprocess.TimeoutExpired('systemd-run', 30)) as launch:
            with self.assertRaises(subprocess.TimeoutExpired):
                service.start(self.root)
            self.assertTrue((self.root/'service-launch.json').exists())
            with self.assertRaisesRegex(RuntimeError, 'already has a service launch receipt'):
                service.start(self.root)
        launch.assert_called_once()

    def test_nonzero_launch_keeps_receipt_and_prevents_replay(self):
        with patch.object(service, '_boot_id', return_value='boot-current'), \
             patch.object(service, '_resource_admission', return_value={'requirements':{}}), \
             patch.object(service, '_show', return_value={'LoadState':'not-found'}), \
             patch.object(service.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'launch failed')) as launch:
            with self.assertRaisesRegex(RuntimeError, 'service launch failed'):
                service.start(self.root)
            with self.assertRaisesRegex(RuntimeError, 'already has a service launch receipt'):
                service.start(self.root)
        launch.assert_called_once()

    def test_existing_service_prevents_launch_without_receipt(self):
        with patch.object(service, '_resource_admission', return_value={'requirements':{}}), \
             patch.object(service, '_show', return_value={'LoadState':'loaded'}), \
             patch.object(service.subprocess, 'run') as launch:
            with self.assertRaisesRegex(RuntimeError, 'already owns this run identity'):
                service.start(self.root)
        launch.assert_not_called()
        self.assertFalse((self.root/'service-launch.json').exists())

    def test_control_lock_excludes_simultaneous_launch(self):
        with service._control_lock(self.root), patch.object(service.subprocess, 'run') as launch:
            with self.assertRaises(BlockingIOError):
                service.start(self.root)
        launch.assert_not_called()

    def test_mismatched_identity_and_reused_pid_never_stop(self):
        self.receipt()
        cases = [({'Description':'another service'}, '900'),
                 ({'InvocationID':'another invocation'}, '900'),
                 ({'InvocationID':''}, '900'),
                 ({'MainPID':'124'}, '900'),
                 ({}, '901'), ({}, None)]
        for changes, ticks in cases:
            with self.subTest(changes=changes, ticks=ticks), \
                 patch.object(service, '_boot_id', return_value='boot-current'), \
                 patch.object(service, '_show', return_value=self.running(**changes)), \
                 patch.object(service, '_start_ticks', return_value=ticks), \
                 patch.object(service.subprocess, 'run') as stop:
                self.assertFalse(service.status(self.root)['identity_verified'])
                with self.assertRaisesRegex(RuntimeError, 'refusing to stop'):
                    service.stop(self.root)
                stop.assert_not_called()

    def test_previous_boot_never_queries_or_stops_current_service(self):
        self.receipt(boot='previous-boot')
        with patch.object(service, '_boot_id', return_value='boot-current'), \
             patch.object(service, '_show') as show, \
             patch.object(service.subprocess, 'run') as stop:
            self.assertEqual(service.stop(self.root)['status'], 'previous_boot')
        show.assert_not_called()
        stop.assert_not_called()

    def test_absent_service_is_already_stopped(self):
        self.receipt()
        with patch.object(service, '_boot_id', return_value='boot-current'), \
             patch.object(service, '_show', return_value={'LoadState':'not-found'}), \
             patch.object(service.subprocess, 'run') as stop:
            self.assertEqual(service.stop(self.root)['status'], 'not_loaded')
        stop.assert_not_called()

    def test_verified_stop_targets_only_owned_unit_and_is_bounded(self):
        receipt = self.receipt()
        with patch.object(service, '_boot_id', return_value='boot-current'), \
             patch.object(service, '_start_ticks', return_value='900'), \
             patch.object(service, '_show', side_effect=[self.running(), {'LoadState':'not-found'}]), \
             patch.object(service.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as stop:
            self.assertEqual(service.stop(self.root)['status'], 'not_loaded')
        stop.assert_called_once_with(['systemctl', '--user', 'stop', receipt['unit']],
                                    capture_output=True, text=True, timeout=22)

    def test_expected_receipt_digest_allows_only_matching_owner(self):
        receipt = self.receipt()
        expected = hashlib.sha256((self.root/'service-launch.json').read_bytes()).hexdigest()
        with patch.object(service, '_boot_id', return_value='boot-current'), \
             patch.object(service, '_start_ticks', return_value='900'), \
             patch.object(service, '_show', side_effect=[self.running(), {'LoadState':'not-found'}]), \
             patch.object(service.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as stop:
            self.assertEqual(service.stop(self.root, expected_receipt_sha256=expected)['status'], 'not_loaded')
        stop.assert_called_once_with(['systemctl', '--user', 'stop', receipt['unit']],
                                    capture_output=True, text=True, timeout=22)

    def test_replaced_receipt_is_rejected_before_status_or_external_stop(self):
        receipt = self.receipt()
        expected = hashlib.sha256((self.root/'service-launch.json').read_bytes()).hexdigest()
        (self.root/'service-launch.json').write_text(json.dumps({**receipt, 'replacement':True}))
        with patch.object(service, 'status') as status, \
             patch.object(service.subprocess, 'run') as stop:
            with self.assertRaisesRegex(RuntimeError, 'refusing to stop replacement owner'):
                service.stop(self.root, expected_receipt_sha256=expected)
        status.assert_not_called()
        stop.assert_not_called()

    def test_receipt_replacement_during_status_query_is_rejected(self):
        receipt = self.receipt()
        expected = hashlib.sha256((self.root/'service-launch.json').read_bytes()).hexdigest()

        def replace(_):
            (self.root/'service-launch.json').write_text(json.dumps({**receipt, 'replacement':True}))
            return {'identity_verified':True, 'running':True, 'status':'running'}

        with patch.object(service, 'status', side_effect=replace), \
             patch.object(service.subprocess, 'run') as stop:
            with self.assertRaisesRegex(RuntimeError, 'refusing to stop replacement owner'):
                service.stop(self.root, expected_receipt_sha256=expected)
        stop.assert_not_called()

    def test_bound_stop_uses_same_control_lock_as_start(self):
        self.receipt()
        expected = hashlib.sha256((self.root/'service-launch.json').read_bytes()).hexdigest()
        with service._control_lock(self.root), patch.object(service, 'status') as status:
            with self.assertRaises(BlockingIOError):
                service.stop(self.root, expected_receipt_sha256=expected)
        status.assert_not_called()

    def test_replacement_cannot_be_accepted_as_already_terminal(self):
        receipt = self.receipt()
        expected = hashlib.sha256((self.root/'service-launch.json').read_bytes()).hexdigest()

        def replace(_):
            (self.root/'service-launch.json').write_text(json.dumps({**receipt, 'replacement':True}))
            return {'identity_verified':False, 'running':False, 'status':'not_loaded'}

        with patch.object(service, 'status', side_effect=replace), \
             patch.object(service.subprocess, 'run') as stop:
            with self.assertRaisesRegex(RuntimeError, 'refusing to stop replacement owner'):
                service.stop(self.root, expected_receipt_sha256=expected)
        stop.assert_not_called()

    def test_invalid_expected_receipt_digest_never_stops(self):
        self.receipt()
        with patch.object(service, 'status') as status:
            for expected in (True, '', '0'*63, 'z'*64, 'A'*64):
                with self.subTest(expected=expected), self.assertRaises(ValueError):
                    service.stop(self.root, expected_receipt_sha256=expected)
        status.assert_not_called()

    def test_instant_success_and_failure_record_invocation_without_live_pid(self):
        for active, substate, code, expected in (
                ('active', 'exited', '0', 'process_completed'),
                ('failed', 'failed', '23', 'failed')):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as tmp:
                original_root = self.root
                original_command = self.command
                self.root = Path(tmp).resolve()
                self.command = [*original_command[:-3], '--run-dir', str(self.root), '--allow-external-call']
                self.manifest()
                def show(unit):
                    if not (self.root/'service-launch.json').exists():
                        return {'LoadState':'not-found'}
                    return self.running(MainPID='0', ActiveState=active,
                                        SubState=substate, ExecMainStatus=code)
                try:
                    with patch.object(service, '_boot_id', return_value='boot-current'), \
                         patch.object(service, '_resource_admission', return_value={'requirements':{}}), \
                         patch.object(service, '_show', side_effect=show), \
                         patch.object(service, '_start_ticks') as ticks, \
                         patch.object(service.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')):
                        result = service.start(self.root)
                    self.assertEqual(result['status'], expected)
                    self.assertFalse(result['running'])
                    self.assertTrue(result['identity_verified'])
                    ticks.assert_not_called()
                    invocation = json.loads((self.root/'service-invocation.json').read_text())
                    self.assertEqual(invocation['invocation_id'], 'invocation-1')
                    self.assertEqual(invocation['main_pid'], 0)
                finally:
                    self.root, self.command = original_root, original_command

    def test_daemon_query_failure_is_not_misreported_as_absent(self):
        with patch.object(service.subprocess, 'run',
                          return_value=subprocess.CompletedProcess([], 1, '', 'bus unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'cannot query user service manager'):
                service._show('unique.service')

    def test_not_found_response_is_accepted_even_with_nonzero_show(self):
        with patch.object(service.subprocess, 'run',
                          return_value=subprocess.CompletedProcess([], 1, 'LoadState=not-found\n', 'not found')):
            self.assertEqual(service._show('unique.service'), {'LoadState':'not-found'})


if __name__ == '__main__':
    unittest.main()
