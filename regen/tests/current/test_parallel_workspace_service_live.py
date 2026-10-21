"""Opt-in user-systemd checks on synthetic programs, never real Planner calls."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import pty
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from uuid import uuid4

from regen.tools.parallel_workspace_service import _boot_id, _show, _start_ticks


@unittest.skipUnless(os.environ.get('REGEN_SERVICE_LIVE_TESTS') == '1',
                     'set REGEN_SERVICE_LIVE_TESTS=1 for bounded user-systemd checks')
class LiveUserServiceTests(unittest.TestCase):
    def test_client_pty_loss_exit_identity_and_bounded_stop(self):
        output = os.environ.get('REGEN_SERVICE_TEST_OUTPUT')
        if output:
            Path(output).mkdir(parents=True, exist_ok=True)
            root = Path(tempfile.mkdtemp(prefix='user-systemd-', dir=output))
        else:
            temporary = tempfile.TemporaryDirectory(prefix='regen-systemd-')
            self.addCleanup(temporary.cleanup)
            root = Path(temporary.name)
        observations = {'started_at':datetime.now(timezone.utc).isoformat(),
                        'scope':'synthetic-user-systemd-supervision', 'boot_id':_boot_id(),
                        'cases':{}, 'cleanup':{}}
        units = []

        def launch(label, program):
            unit = 'regen-supervision-test-'+uuid4().hex[:16]+'.service'
            units.append(unit)
            description = 'regen synthetic supervision '+uuid4().hex
            log = root/(label+'.log')
            command = ['systemd-run', '--user', '--no-ask-password', '--quiet',
                '--unit='+unit, '--description='+description,
                '--service-type=exec', '--expand-environment=no', '--remain-after-exit',
                '--working-directory='+str(Path.cwd()), '--property=Restart=no',
                '--property=KillMode=mixed', '--property=KillSignal=SIGTERM',
                '--property=SendSIGKILL=yes', '--property=TimeoutStopSec=2',
                '--property=StandardInput=null', '--property=StandardOutput=append:'+str(log),
                '--property=StandardError=append:'+str(log), '--property=UMask=0077',
                '--', sys.executable, '-u', '-c', program]
            master, slave = pty.openpty()
            try:
                client = subprocess.Popen(command, stdin=slave, stdout=slave,
                    stderr=slave, start_new_session=True)
                client_status = client.wait(timeout=10)
                self.assertEqual(client_status, 0)
            finally:
                os.close(slave)
                os.close(master)
            item = {'unit':unit, 'description':description, 'launch_command':command,
                    'launch_client_exited':True, 'launch_client_exitcode':client_status,
                    'launch_pty_closed':True}
            observations['cases'][label] = item
            return unit, description, log, item

        def until(unit, predicate):
            deadline = time.monotonic()+10
            while time.monotonic() < deadline:
                value = _show(unit)
                if predicate(value):
                    return value
                time.sleep(.1)
            self.fail('service state deadline: '+json.dumps(value))

        try:
            unit, description, log, item = launch('survive-client-and-pty',
                'import os,signal,sys,time; '
                'signal.signal(signal.SIGTERM, lambda *_: sys.exit(0)); '
                'print("ready", os.getpid(), flush=True); time.sleep(30)')
            first = until(unit, lambda value: value.get('SubState') == 'running')
            pid = int(first['MainPID'])
            ticks = _start_ticks(pid)
            time.sleep(.5)
            after = _show(unit)
            self.assertEqual(after['Description'], description)
            self.assertEqual(after['InvocationID'], first['InvocationID'])
            self.assertEqual(int(after['MainPID']), pid)
            self.assertIsNotNone(ticks)
            self.assertEqual(_start_ticks(pid), ticks)
            self.assertEqual(after['SubState'], 'running')
            item.update(first=first, after_client_and_pty_exit=after, start_ticks=ticks)
            subprocess.run(['systemctl', '--user', 'stop', unit], check=True,
                           capture_output=True, text=True, timeout=10)
            stopped = _show(unit)
            self.assertEqual(stopped.get('MainPID', '0'), '0')
            self.assertNotEqual(_start_ticks(pid), ticks)
            item['after_stop'] = stopped

            unit, description, log, item = launch('failure-retained',
                'import sys,time; time.sleep(.2); sys.exit(23)')
            failed = until(unit, lambda value: value.get('ActiveState') == 'failed')
            self.assertEqual(failed['Description'], description)
            self.assertEqual(failed['ExecMainStatus'], '23')
            self.assertEqual(failed['Result'], 'exit-code')
            self.assertTrue(failed['InvocationID'])
            item['terminal'] = failed

            unit, description, log, item = launch('success-retained',
                'import time; time.sleep(.2); print("synthetic complete")')
            completed = until(unit, lambda value: value.get('SubState') == 'exited')
            self.assertEqual(completed['Description'], description)
            self.assertEqual(completed['ExecMainStatus'], '0')
            self.assertEqual(completed['Result'], 'success')
            item['terminal'] = completed

            unit, description, log, item = launch('bounded-mixed-stop',
                'import os,signal,subprocess,time; '
                'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
                'child=subprocess.Popen(["sleep", "30"]); '
                'print(os.getpid(),child.pid,flush=True); time.sleep(30)')
            running = until(unit, lambda value: value.get('SubState') == 'running')
            deadline = time.monotonic()+5
            while (not log.exists() or not log.read_text().strip()) and time.monotonic()<deadline:
                time.sleep(.05)
            parent_pid, child_pid = map(int, log.read_text().split())
            identities = {pid:_start_ticks(pid) for pid in (parent_pid, child_pid)}
            started = time.monotonic()
            subprocess.run(['systemctl', '--user', 'stop', unit], check=True,
                           capture_output=True, text=True, timeout=10)
            terminal = _show(unit)
            self.assertEqual(terminal['MainPID'], '0')
            self.assertEqual(terminal['Result'], 'timeout')
            self.assertEqual(terminal['ExecMainStatus'], '9')
            for pid, identity in identities.items():
                self.assertNotEqual(_start_ticks(pid), identity)
            item.update(before_stop=running, terminal=terminal,
                        stop_elapsed_seconds=time.monotonic()-started,
                        parent_and_child_terminated=True)

            unit, description, log, item = launch('hard-killed-main-cleans-child',
                'import os,subprocess,time; '
                'child=subprocess.Popen(["sleep", "30"]); '
                'print(os.getpid(),child.pid,flush=True); time.sleep(30)')
            running = until(unit, lambda value: value.get('SubState') == 'running')
            deadline = time.monotonic()+5
            while (not log.exists() or not log.read_text().strip()) and time.monotonic()<deadline:
                time.sleep(.05)
            parent_pid, child_pid = map(int, log.read_text().split())
            identities = {pid:_start_ticks(pid) for pid in (parent_pid, child_pid)}
            self.assertEqual(str(parent_pid), running['MainPID'])
            self.assertIsNotNone(identities[parent_pid])
            self.assertIsNotNone(identities[child_pid])
            started = time.monotonic()
            os.kill(parent_pid, signal.SIGKILL)
            terminal = until(unit, lambda value: value.get('ActiveState') == 'failed')
            self.assertEqual(terminal['MainPID'], '0')
            self.assertEqual(terminal['ExecMainStatus'], '9')
            for pid, identity in identities.items():
                self.assertNotEqual(_start_ticks(pid), identity)
            item.update(before_kill=running, terminal=terminal,
                        cleanup_elapsed_seconds=time.monotonic()-started,
                        parent_and_child_terminated=True)
            observations['status'] = 'passed'
        except BaseException as error:
            observations.update(status='failed', error=repr(error))
            raise
        finally:
            for unit in units:
                stopped = subprocess.run(['systemctl', '--user', 'stop', unit],
                    capture_output=True, text=True, timeout=10)
                reset = subprocess.run(['systemctl', '--user', 'reset-failed', unit],
                    capture_output=True, text=True, timeout=10)
                observations['cleanup'][unit] = {
                    'stop_returncode':stopped.returncode, 'reset_returncode':reset.returncode,
                    'final':_show(unit)}
            observations['finished_at'] = datetime.now(timezone.utc).isoformat()
            result = root/'result.json'
            result.write_text(json.dumps(observations, indent=2)+'\n')
            if output:
                print('Live supervision evidence:', result)


if __name__ == '__main__':
    unittest.main()
