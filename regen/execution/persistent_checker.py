"""Speculative fixed-prefix worker; every GREEN is independently cold-certified."""
import atexit
from regen.execution.check_tasks import check_cancelled, CheckCancelled
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path


def _renew_owner_lease(heartbeat, stopped, interval):
    """Resource lease only: no periodic experiment/progress events are emitted."""
    while not stopped.wait(interval):
        try:
            os.utime(heartbeat, None)
        except OSError:
            # Fail closed: the in-container guardian will expire this lease.
            return


class PrefixWorker:
    def __init__(self, checker, source, node_id, fact_name):
        self.checker = checker
        self.closed = False
        self.index = 0
        self.name = 'regen-prefix-' + uuid.uuid4().hex[:16]
        self.root = checker.work_dir / self.name
        self.root.mkdir()
        label = re.search(r'^theory (\S+)', source)[1]
        marker = 'ML \\<open>writeln ("REGEN_TIMING_START ' + node_id + ' '
        prefix = source[:source.index(marker)].split('\nbegin\n', 1)[1]
        self.identity = hashlib.sha256((prefix + json.dumps(checker.imports()) + label).encode()).hexdigest()
        for name, value in [('command_timeout',str(getattr(checker, 'persistent_command_timeout', 60))),('prefix',prefix),('imports','\n'.join(checker.imports())),('theory_name',label),('fact_name',fact_name),('line_number',str(source[:source.index(marker)].count('\n')+2))]:
            (self.root/name).write_text(value)
        (self.root/'server.ML').write_bytes(Path(__file__).with_suffix('.ML').read_bytes())
        command = checker._command(checker.work_dir/(label+'.thy'), session='Generated_Refine' if checker.previous_theories else checker.base_session,
                                   writable_mounts=[(self.root,'/worker')])
        command[2:2] = ['--name',self.name]
        command = command[:-2] + ['-e','ML_file "/worker/server.ML"']
        self._launch(command)

    def _launch(self, command):
        # The guardian is PID 1, outside Isabelle, so losing the coordinator also
        # terminates a worker busy loading a heap or executing a candidate.
        lease_seconds = int(getattr(self.checker, 'owner_lease_seconds', 60))
        if lease_seconds < 1:
            raise ValueError('owner_lease_seconds must be positive')
        heartbeat = self.root/'owner.heartbeat'
        heartbeat.touch()
        (self.root/'owner_watchdog.sh').write_bytes(
            Path(__file__).with_name('worker_owner.sh').read_bytes())
        insertion = command.index('/workspace/verification/isabelle/bin/isabelle')
        command[insertion:insertion] = [
            '/bin/sh', '/worker/owner_watchdog.sh', str(lease_seconds), '/worker/owner.heartbeat']
        self._owner_stopped = threading.Event()
        self._owner_thread = threading.Thread(
            target=_renew_owner_lease,
            args=(heartbeat, self._owner_stopped, min(5, lease_seconds/3)),
            name=self.name+'-owner-lease', daemon=True)
        self.log = open(self.root/'worker.log', 'w')
        try:
            self._owner_thread.start()
            self.process = subprocess.Popen(
                command, stdout=self.log, stderr=subprocess.STDOUT, text=True)
        except BaseException:
            self._owner_stopped.set()
            self.log.close()
            raise
        atexit.register(self.close)
        try:
            self.wait(self.root/'ready')
        except Exception:
            self.close()
            raise

    def wait(self, path):
        start = time.monotonic()
        while not path.exists():
            try:
                check_cancelled()
            except CheckCancelled:
                self.close()
                raise
            if self.process.poll() is not None:
                raise RuntimeError((self.root/'worker.log').read_text()[-6000:])
            if time.monotonic()-start > self.checker.timeout:
                self.close()
                raise TimeoutError('persistent checker timeout')
            time.sleep(.05)

    def check(self, body):
        self.index += 1
        start = time.monotonic()
        stem = str(self.index)
        log_offset = (self.root/'worker.log').stat().st_size
        temp = self.root/(stem+'.tmp')
        temp.write_text(body)
        temp.rename(self.root/(stem+'.request'))
        response = self.root/(stem+'.response')
        self.wait(response)
        text = response.read_text()
        with open(self.root/'worker.log','rb') as log:
            log.seek(log_offset)
            output = log.read().decode(errors='replace')
        (self.root/(stem+'.commands.log')).write_text(output)
        return {'verdict':'green' if text=='GREEN' else 'red','returncode':0 if text=='GREEN' else 1,
                'diagnostic':text,'wall_ms':round((time.monotonic()-start)*1000),
                'source':str(self.root/(stem+'.request')),'log':str(response),
                'node_wall_ms':{},'persistent_prefix':self.identity,'speculative':True,
                'command_timings': [{'command':cmd,'wall_ms':int(ms)} for cmd,ms in re.findall(r'^REGEN_COMMAND (\S+) (\d+)$',output,re.M)],
                'command_log':str(self.root/(stem+'.commands.log'))}

    def close(self):
        if self.closed:
            return
        if hasattr(self, '_owner_stopped'):
            self._owner_stopped.set()
        # A dead Docker client says nothing about its container. Always remove
        # the exact owned name; a daemon/transport error must remain retryable.
        try:
            (self.root/'stop').touch()
        except OSError:
            pass  # Disk failure must not prevent owned-container cleanup.
        if self.process.poll() is None:
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        self._remove_container()
        if self.process.poll() is None:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
                # Cover a Docker client that was still creating its container
                # during the first removal attempt.
                self._remove_container()
        try:
            self.log.close()
        finally:
            self.closed = True
            atexit.unregister(self.close)

    def _remove_container(self):
        result = subprocess.run(
            ['docker', 'rm', '-f', self.name], capture_output=True,
            text=True, timeout=30)
        if result.returncode:
            diagnostic = (result.stderr or '') + (result.stdout or '')
            # --rm containers normally disappear on graceful exit. Do not
            # mistake a daemon connection failure for confirmation of absence.
            normalized = diagnostic.casefold()
            if self.name.casefold() in normalized and 'is already in progress' in normalized:
                from regen.execution.native_container import wait_for_owned_removal
                wait_for_owned_removal(self.name)
                return
            absent = (f'no such container: {self.name}'.casefold() in normalized or
                      f'no such object: {self.name}'.casefold() in normalized)
            if not absent:
                raise RuntimeError('failed to remove owned worker ' + self.name + ': ' + diagnostic.strip())
