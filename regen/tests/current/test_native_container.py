"""Owner lifecycle tests; optional live Docker tests use synthetic workloads."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from regen.execution.native_container import docker_limits, owner_command, remove_owned
from regen.execution.check_executor import CURRENT_RESOURCES, NativeResources, native_run

ISABELLE = '/workspace/verification/isabelle/bin/isabelle'


class NativeContainerTests(unittest.TestCase):
    def test_limits_apply_without_pool_and_validate_configuration(self):
        self.assertIn('--memory', docker_limits())
        pool = NativeResources(1, 2, 1, cpus=1.5, memory_mb=512, pids_limit=64)
        token = CURRENT_RESOURCES.set(pool)
        try:
            self.assertEqual(docker_limits(), ['--cpus','1.5','--memory','512m',
                '--memory-swap','512m','--pids-limit','64'])
        finally:
            CURRENT_RESOURCES.reset(token)
            pool.close()
        for config in ({'cpus':float('nan')},{'cpus':True},{'memory_mb':0},{'pids_limit':False}):
            with self.assertRaises(ValueError):
                NativeResources(**config)

    def test_cold_and_build_have_independent_lease_and_exact_cleanup(self):
        for operation in ('process','build'):
            with self.subTest(operation=operation), patch(
                    'regen.execution.native_container.remove_owned') as remove:
                command = ['docker','run','--name','owned-test','image',ISABELLE,operation]
                with owner_command(command) as guarded:
                    self.assertIn('/native-owner/owner.sh', guarded)
                    mount = next(x for x in guarded if x.startswith('type=bind,src='))
                    root = Path(mount.split('src=',1)[1].split(',dst=')[0])
                    self.assertTrue((root/'owner.heartbeat').is_file())
                remove.assert_called_once_with('owned-test')
                self.assertFalse(root.exists())

    def test_cleanup_failure_is_not_reported_as_success(self):
        with patch('regen.execution.native_container.subprocess.run',
                   return_value=subprocess.CompletedProcess([],1,'','daemon unavailable')):
            with self.assertRaisesRegex(RuntimeError,'cleanup failed'):
                remove_owned('owned-test')


@unittest.skipUnless(os.environ.get('REGEN_WORKER_DOCKER_TESTS') == '1', 'opt-in live Docker')
class NativeContainerDockerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # This is a synthetic workload, not an Isabelle proof.
        self.script = self.root/'native'
        self.script.write_text('#!/bin/sh\ncase "$1" in\n'
            'oom) exec python3 -c "x=bytearray(256*1024*1024)" ;;\n'
            'ok) exit 0 ;;\n*) exec sleep 120 ;;\nesac\n')
        self.script.chmod(0o755)
        self.name = 'regen-owner-test-' + uuid.uuid4().hex
        self.addCleanup(remove_owned, self.name)

    def command(self, operation):
        return ['docker','run','--rm','--network','none','--name',self.name,
            '--cpus','0.5','--memory','64m','--memory-swap','64m','--pids-limit','64',
            '--mount',f'type=bind,src={self.script},dst={ISABELLE},readonly',
            'python:3.11-slim',ISABELLE,operation]

    def absent(self):
        result = subprocess.run(['docker','inspect',self.name],capture_output=True,text=True,timeout=10)
        return result.returncode != 0 and 'no such object:' in result.stderr.lower()

    def test_normal_timeout_and_oom_remove_containers(self):
        result = native_run(self.command('ok'),timeout=15,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(self.absent())
        with self.assertRaises(subprocess.TimeoutExpired):
            native_run(self.command('process'),timeout=2,capture_output=True,text=True)
        self.assertTrue(self.absent())
        result = native_run(self.command('oom'),timeout=20,capture_output=True,text=True)
        self.assertEqual(result.returncode,137,result.stderr)
        self.assertTrue(self.absent())

    def test_owner_sigkill_reaps_process_and_build(self):
        for operation in ('process','build'):
            with self.subTest(operation=operation):
                code = ('import json,subprocess,sys\n'
                    'from regen.execution.native_container import owner_command\n'
                    'with owner_command(json.loads(sys.argv[1]),lease_seconds=2) as cmd:\n'
                    ' subprocess.run(cmd,check=True)\n')
                owner = subprocess.Popen([sys.executable,'-c',code,json.dumps(self.command(operation))],
                                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                try:
                    deadline = time.monotonic()+15
                    running = False
                    while time.monotonic()<deadline:
                        result = subprocess.run(['docker','inspect','--format','{{.State.Running}}',self.name],
                                                capture_output=True,text=True,timeout=5)
                        if result.returncode==0 and result.stdout.strip()=='true':
                            running = True
                            break
                        time.sleep(.1)
                    self.assertTrue(running,'container never became ready')
                    owner.kill()
                    owner.wait(timeout=5)
                    deadline = time.monotonic()+10
                    while time.monotonic()<deadline and not self.absent():
                        time.sleep(.2)
                    self.assertTrue(self.absent(),'orphan survived owner lease')
                finally:
                    if owner.poll() is None:
                        owner.kill()
                    owner.wait(timeout=5)
                    remove_owned(self.name)

    def test_cancellation_reclaims_actual_container(self):
        from regen.execution.check_tasks import Cancellation, CURRENT_TASK, CheckCancelled
        cancellation=Cancellation()
        marker=CURRENT_TASK.set(cancellation)
        timer=threading.Timer(2,cancellation.cancel)
        timer.start()
        try:
            with self.assertRaises(CheckCancelled):
                native_run(self.command('process'),timeout=20,capture_output=True,text=True)
            self.assertTrue(self.absent())
        finally:
            timer.cancel()
            timer.join()
            CURRENT_TASK.reset(marker)
