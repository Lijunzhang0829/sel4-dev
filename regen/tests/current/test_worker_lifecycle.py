"""Resident ownership/cleanup failure injection; no provider is contacted."""
import atexit
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import Mock, patch

from regen.execution.batch_checker import BatchWorker
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.persistent_checker import PrefixWorker, _renew_owner_lease


GUARDIAN = Path(__file__).parents[2]/'execution/worker_owner.sh'


class WorkerLifecycleTests(unittest.TestCase):
    def test_checker_cleanup_attempts_both_workers_and_retries_only_failure(self):
        checker = object.__new__(IsabelleChecker)
        batch, prefix = Mock(), Mock()
        checker._batch_worker, checker._prefix_worker = batch, prefix
        checker._prefix_key = 'prefix-key'
        batch.close.side_effect = [RuntimeError('batch cleanup failed'), None]
        with self.assertRaisesRegex(RuntimeError, 'batch cleanup failed'):
            checker.close_prefix()
        self.assertIs(checker._batch_worker, batch)
        self.assertIsNone(checker._prefix_worker)
        self.assertIsNone(checker._prefix_key)
        prefix.close.assert_called_once()
        checker.close_prefix()
        self.assertIsNone(checker._batch_worker)
        self.assertEqual(batch.close.call_count, 2)
        prefix.close.assert_called_once()

    def test_checker_cleanup_preserves_failed_prefix_identity(self):
        checker = object.__new__(IsabelleChecker)
        batch, prefix = Mock(), Mock()
        checker._batch_worker, checker._prefix_worker = batch, prefix
        checker._prefix_key = 'prefix-key'
        batch.close.side_effect = RuntimeError('batch unavailable')
        prefix.close.side_effect = OSError('prefix unavailable')
        with self.assertRaisesRegex(RuntimeError, 'batch unavailable') as caught:
            checker.close_prefix()
        self.assertIs(checker._batch_worker, batch)
        self.assertIs(checker._prefix_worker, prefix)
        self.assertEqual(checker._prefix_key, 'prefix-key')
        self.assertTrue(any('prefix unavailable' in note for note in caught.exception.__notes__))
        prefix.close.side_effect = None
        batch.close.side_effect = None
        checker.close_prefix()
        self.assertIsNone(checker._prefix_key)
        self.assertIsNone(checker._prefix_worker)
        self.assertIsNone(checker._batch_worker)

    def worker(self, root, kind=PrefixWorker):
        worker = object.__new__(kind)
        worker.root, worker.closed, worker.name = root, False, 'regen-test-owned'
        worker.process, worker.log = Mock(), Mock()
        worker.process.poll.return_value = 1
        return worker

    def test_dead_docker_client_still_removes_exact_owned_container(self):
        for kind in (PrefixWorker, BatchWorker):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                worker = self.worker(Path(tmp), kind)
                with patch('regen.execution.persistent_checker.subprocess.run',
                           return_value=subprocess.CompletedProcess([], 0)) as remove:
                    worker.close()
                    worker.close()
                remove.assert_called_once_with(
                    ['docker', 'rm', '-f', worker.name], capture_output=True,
                    text=True, timeout=30)
                self.assertTrue(worker.closed)
                worker.log.close.assert_called_once()

    def test_daemon_error_with_dead_client_keeps_cleanup_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = self.worker(Path(tmp))
            responses = [subprocess.CompletedProcess([], 1, '', 'Cannot connect to daemon'),
                         subprocess.CompletedProcess([], 0, '', '')]
            with patch('regen.execution.persistent_checker.subprocess.run', side_effect=responses), \
                 patch.object(atexit, 'unregister') as unregister:
                with self.assertRaisesRegex(RuntimeError, 'Cannot connect'):
                    worker.close()
                self.assertFalse(worker.closed)
                worker.log.close.assert_not_called()
                unregister.assert_not_called()
                worker.close()
                self.assertTrue(worker.closed)
                unregister.assert_called_once()

    def test_confirmed_auto_removed_container_is_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = self.worker(Path(tmp))
            response = subprocess.CompletedProcess(
                [], 1, '', 'Error response from daemon: No such container: ' + worker.name)
            with patch('regen.execution.persistent_checker.subprocess.run', return_value=response):
                worker.close()
            self.assertTrue(worker.closed)

    def test_both_worker_types_launch_with_independent_owner_lease(self):
        for kind in (PrefixWorker, BatchWorker):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                worker = self.worker(Path(tmp), kind)
                worker.checker = SimpleNamespace(timeout=60)
                worker.wait = Mock()
                command = ['docker', 'run', '--rm', 'image',
                           '/workspace/verification/isabelle/bin/isabelle', 'process',
                           '-e', 'ML_file "/worker/server.ML"']
                process = Mock()
                process.poll.return_value = 0
                with patch('regen.execution.persistent_checker.subprocess.Popen', return_value=process) as start, \
                     patch('regen.execution.persistent_checker.subprocess.run',
                           return_value=subprocess.CompletedProcess([], 0)):
                    worker._launch(command)
                    actual = start.call_args.args[0]
                    self.assertEqual(actual[4:8], [
                        '/bin/sh', '/worker/owner_watchdog.sh', '60', '/worker/owner.heartbeat'])
                    self.assertTrue((worker.root/'owner.heartbeat').is_file())
                    worker.wait.assert_called_once_with(worker.root/'ready')
                    worker.close()
                worker._owner_thread.join(timeout=1)
                self.assertFalse(worker._owner_thread.is_alive())

    def test_lease_write_failure_stops_renewal(self):
        stopped = Mock()
        stopped.wait.side_effect = [False, False, True]
        with patch('regen.execution.persistent_checker.os.utime', side_effect=OSError('disk unavailable')) as touch:
            _renew_owner_lease(Path('/unused/heartbeat'), stopped, 5)
        touch.assert_called_once()
        stopped.wait.assert_called_once_with(5)

    def test_guardian_expires_while_worker_is_busy(self):
        with tempfile.TemporaryDirectory() as tmp:
            heartbeat = Path(tmp)/'heartbeat'
            heartbeat.touch()
            result = subprocess.run(
                ['/bin/sh', str(GUARDIAN), '2', str(heartbeat), 'sleep', '30'],
                capture_output=True, text=True, timeout=8)
            self.assertEqual(result.returncode, 125, result.stderr)
            self.assertIn('REGEN_WORKER_OWNER_LEASE_EXPIRED', result.stderr)

    def test_guardian_does_not_replace_candidate_deadlines(self):
        with tempfile.TemporaryDirectory() as tmp:
            heartbeat = Path(tmp)/'heartbeat'
            heartbeat.touch()
            stopped = threading.Event()
            owner = threading.Thread(target=_renew_owner_lease,
                args=(heartbeat, stopped, .1), daemon=True)
            owner.start()
            try:
                # This operation lasts longer than the lease but keeps its own
                # native exit status while the live owner renews the lease.
                result = subprocess.run(
                    ['/bin/sh', str(GUARDIAN), '2', str(heartbeat),
                     '/bin/sh', '-c', 'sleep 3; exit 17'],
                    capture_output=True, text=True, timeout=8)
            finally:
                stopped.set()
                owner.join(timeout=1)
            self.assertEqual(result.returncode, 17, result.stderr)
            self.assertNotIn('REGEN_WORKER_OWNER_LEASE_EXPIRED', result.stderr)


@unittest.skipUnless(os.environ.get('REGEN_WORKER_DOCKER_TESTS') == '1',
                     'set REGEN_WORKER_DOCKER_TESTS=1 for bounded real Docker fault tests')
class WorkerDockerLifecycleTests(unittest.TestCase):
    def docker(self, *arguments):
        return subprocess.run(['docker', *arguments], capture_output=True,
                              text=True, timeout=10)

    def command(self, root, name):
        return ['docker', 'run', '--name', name, '--network', 'none',
                '--mount', f'type=bind,src={root},dst=/worker',
                '--mount', f'type=bind,src={GUARDIAN.resolve()},dst=/owner.sh,readonly',
                '--entrypoint', '/bin/sh', 'ubuntu:20.04',
                '/owner.sh', '2', '/worker/heartbeat', 'sleep', '30']

    def state_until(self, name, expected):
        deadline = time.monotonic()+10
        while time.monotonic() < deadline:
            result = self.docker('inspect', '--format', '{{.State.Status}} {{.State.ExitCode}}', name)
            if result.returncode == 0 and result.stdout.strip() == expected:
                return
            time.sleep(.1)
        self.fail(f'{name} did not reach {expected}: {result.stdout} {result.stderr}')

    def test_sigkill_owner_expires_busy_container(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'heartbeat').touch()
            name = 'regen-owner-test-'+uuid.uuid4().hex[:12]
            command = self.command(root, name)
            code = (
                'import subprocess, threading, time; '
                'from pathlib import Path; '
                'from regen.execution.persistent_checker import _renew_owner_lease; '
                f'threading.Thread(target=_renew_owner_lease, args=(Path({str(root/"heartbeat")!r}), '
                'threading.Event(), .1), daemon=True).start(); '
                f'subprocess.Popen({command!r}, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT); '
                'time.sleep(30)')
            owner = subprocess.Popen([sys.executable, '-c', code])
            try:
                self.state_until(name, 'running 0')
                time.sleep(3)
                self.state_until(name, 'running 0')
                owner.kill()
                owner.wait(timeout=5)
                self.state_until(name, 'exited 125')
                self.assertIn('REGEN_WORKER_OWNER_LEASE_EXPIRED',
                              self.docker('logs', name).stderr)
            finally:
                if owner.poll() is None:
                    owner.kill()
                    owner.wait(timeout=5)
                self.docker('rm', '-f', name)

    def test_dead_cli_close_reclaims_live_container(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'heartbeat').touch()
            name = 'regen-cli-test-'+uuid.uuid4().hex[:12]
            stopped = threading.Event()
            lease = threading.Thread(target=_renew_owner_lease,
                args=(root/'heartbeat', stopped, .1), daemon=True)
            lease.start()
            process = subprocess.Popen(self.command(root, name),
                stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
            worker = object.__new__(PrefixWorker)
            worker.root, worker.name, worker.closed = root, name, False
            worker.process, worker.log, worker._owner_stopped = process, Mock(), stopped
            try:
                self.state_until(name, 'running 0')
                process.kill()
                process.wait(timeout=5)
                self.state_until(name, 'running 0')
                worker.close()
                self.assertTrue(worker.closed)
                result = self.docker('inspect', name)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('no such object', result.stderr.casefold())
            finally:
                stopped.set()
                lease.join(timeout=1)
                self.docker('rm', '-f', name)
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)


@unittest.skipUnless(os.environ.get('REGEN_WORKER_ISABELLE_TESTS') == '1',
                     'set REGEN_WORKER_ISABELLE_TESTS=1 for synthetic native HOL smoke')
class WorkerNativeLifecycleTests(unittest.TestCase):
    def test_parallel_prefix_and_audited_batch(self):
        repo = Path(__file__).resolve().parents[3]
        isabelle = Path(os.environ.get('REGEN_WORKER_ISABELLE_DIR', repo/'verification/isabelle'))
        heaps = Path(os.environ.get('REGEN_WORKER_ISABELLE_HEAPS',
            repo/'reports/regen/live-environment-002/Semantic-Base/heaps'))
        image = os.environ.get('REGEN_WORKER_ISABELLE_IMAGE', 'sel4-public:tuned')
        output = os.environ.get('REGEN_WORKER_SMOKE_OUTPUT')
        if output:
            Path(output).mkdir(parents=True, exist_ok=True)
            directory = Path(tempfile.mkdtemp(prefix='native-workers-', dir=output))
        else:
            temporary = tempfile.TemporaryDirectory(prefix='regen-native-workers-')
            self.addCleanup(temporary.cleanup)
            directory = Path(temporary.name)

        def check(kind):
            root = directory/kind
            root.mkdir()

            def command(source, *, session, writable_mounts):
                args = ['docker', 'run', '--rm', '--network', 'none',
                        '--mount', f'type=bind,src={isabelle},dst=/workspace/verification/isabelle,readonly',
                        '--mount', f'type=bind,src={heaps},dst=/root/.isabelle/heaps,readonly']
                for host, container in writable_mounts:
                    args += ['--mount', f'type=bind,src={host},dst={container}']
                return args + [image, '/workspace/verification/isabelle/bin/isabelle',
                               'process', '-l', 'HOL', '-o', 'threads=1', '-T', '/unused']

            checker = SimpleNamespace(work_dir=root, timeout=90,
                persistent_command_timeout=10, batch_candidate_timeout=10,
                owner_lease_seconds=10, previous_theories=[], base_session='HOL',
                imports=lambda: ['Main'], _command=command)
            if kind == 'batch':
                source = 'theory Owned_Batch\nimports Main\nbegin\n\nend\n'
                worker = BatchWorker(checker, source, [], ['Main'])
                try:
                    result = worker.check_batch(
                        'theory Owned_Batch\nimports Main\nbegin\n'
                        'lemma owned_true: "True" by simp\n\nend\n', ['owned_true'])
                    self.assertEqual(result['verdict'], 'green', result)
                    worker.align(worker.pending)
                    return {'kind':kind, 'check':result, 'commit':'COMMITTED'}
                finally:
                    worker.close()
            source = ('theory Owned_Prefix\nimports Main\nbegin\n'
                      'ML \\<open>writeln ("REGEN_TIMING_START n ");\\<close>\nend\n')
            worker = PrefixWorker(checker, source, 'n', 'owned_true')
            try:
                bad = worker.check('lemma owned_true: "False" by simp\n')
                good = worker.check('lemma owned_true: "True" by simp\n')
                self.assertEqual(bad['verdict'], 'red', bad)
                self.assertEqual(good['verdict'], 'green', good)
                self.assertTrue(good['speculative'])
                return {'kind':kind, 'negative':bad, 'positive':good}
            finally:
                worker.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(check, ['prefix', 'batch']))
        result_path = directory/'result.json'
        result_path.write_text(json.dumps({
            'status':'passed', 'scope':'synthetic-HOL-worker-lifecycle',
            'image':image, 'isabelle_dir':str(isabelle), 'heaps_dir':str(heaps),
            'parallel_workers':2, 'results':results}, indent=2)+'\n')
        if output:
            print('Native worker lifecycle evidence:', result_path)


if __name__ == '__main__':
    unittest.main()
