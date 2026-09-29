"""Local durable task executor and bounded, exclusive native resource leases.

Tasks produce results; the theory controller remains responsible for acceptance.
No result is trusted as proof merely because its task completed.
"""
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from copy import deepcopy
from functools import wraps
import json
import fcntl
from pathlib import Path
import subprocess
import threading
import time
from uuid import uuid4

from regen.execution.check_tasks import CURRENT_TASK, Cancellation, CheckCancelled, TERMINAL, check_cancelled
from regen.execution.proof_commits import digest, durable

CURRENT_RESOURCES = ContextVar('regen_native_resources', default=None)
_LEASE = ContextVar('regen_native_lease', default=None)


def _raise_cleanup_errors(errors):
    if errors:
        for error in errors[1:]:
            errors[0].add_note(f'Additional cleanup failure: {type(error).__name__}: {error}')
        raise errors[0]


class NativeResources:
    def __init__(self, slots=1, threads=4, resident_limit=2):
        if any(type(v) is not int or v < 1 for v in (slots, threads, resident_limit)) or resident_limit < slots:
            raise ValueError('positive native slots/threads required; resident_limit must cover slots')
        self.policy = dict(native_slots=slots, threads=threads, resident_limit=resident_limit)
        self.condition = threading.Condition()
        self.queue = deque()
        self.active = set()
        self.residents = OrderedDict()
        self.evicting = set()
        self.closed = False
        self.close_lock = threading.Lock()
        self.metrics = []
        self.peak_active = 0

    @contextmanager
    def lease(self, checker):
        key = id(checker)
        if _LEASE.get() == (id(self), key, threading.get_ident()):
            check_cancelled()
            yield
            return
        ticket = object()
        queued = time.monotonic()
        from regen.infrastructure.progress_events import emit_event
        emit_event('native_queued', checker=str(getattr(checker,'work_dir',key)))
        admitted = False
        victim = None
        try:
            with self.condition:
                self.queue.append(ticket)
                while True:
                    check_cancelled()
                    if self.closed:
                        raise RuntimeError('native pool is closed')
                    if (self.queue[0] is ticket and len(self.active) < self.policy['native_slots']
                            and key not in self.active and key not in self.evicting):
                        if key in self.residents or len(self.residents) < self.policy['resident_limit']:
                            break
                        victim = next((k for k in self.residents
                                       if k not in self.active and k not in self.evicting), None)
                        if victim is not None:
                            break
                    self.condition.wait(.1)
                self.queue.popleft()
                self.active.add(key)
                admitted = True
                self.peak_active = max(self.peak_active,len(self.active))
                if victim is not None:
                    # Keep the victim resident until close succeeds, reserving
                    # its capacity for this replacement without holding the lock.
                    self.evicting.add(victim)
                    old_checker = self.residents[victim]
                else:
                    self.residents[key] = checker
                    self.residents.move_to_end(key)
                self.condition.notify_all()
            if victim is not None:
                old_checker.close_prefix()
                with self.condition:
                    del self.residents[victim]
                    self.evicting.remove(victim)
                    check_cancelled()
                    self.residents[key] = checker
                    self.condition.notify_all()
        except BaseException:
            with self.condition:
                if ticket in self.queue:
                    self.queue.remove(ticket)
                self.evicting.discard(victim)
                if admitted:
                    self.active.discard(key)
                self.condition.notify_all()
            raise
        lease = _LEASE.set((id(self), key, threading.get_ident()))
        started = time.monotonic()
        try:
            from regen.infrastructure.progress_events import emit_event
            emit_event('native_slot_acquired', checker=str(getattr(checker, 'work_dir', key)),
                       queue_seconds=round(started-queued,3))
            yield
            check_cancelled()
        finally:
            _LEASE.reset(lease)
            with self.condition:
                self.active.remove(key)
                self.metrics.append({'checker':str(getattr(checker,'work_dir',key)),
                    'queue_seconds':started-queued,'execution_seconds':time.monotonic()-started})
                self.condition.notify_all()
            emit_event('native_slot_released', elapsed_seconds=round(time.monotonic()-started, 3))

    def close(self):
        with self.close_lock:
            with self.condition:
                if self.active or self.evicting:
                    raise RuntimeError('cannot close native pool while checks are active')
                self.closed = True
                residents = list(self.residents.items())
                self.condition.notify_all()
            errors = []
            for key, checker in residents:
                try:
                    checker.close_prefix()
                except BaseException as error:
                    errors.append(error)
                else:
                    with self.condition:
                        del self.residents[key]
            _raise_cleanup_errors(errors)


def native_operation(function):
    @wraps(function)
    def call(checker, *args, **kwargs):
        resources = CURRENT_RESOURCES.get()
        check_cancelled()
        if resources is None:
            return function(checker, *args, **kwargs)
        with resources.lease(checker):
            return function(checker, *args, **kwargs)
    return call


def native_run(command, *, timeout, **kwargs):
    """Only task-owned subprocesses are cancelled; old synchronous callers work unchanged."""
    token = CURRENT_TASK.get()
    if token is None:
        return subprocess.run(command, timeout=timeout, **kwargs)
    token.check()
    # Existing callers use capture_output/text only; retain CompletedProcess API.
    options = dict(kwargs)
    if options.pop('capture_output', False):
        options.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with token.lock:
        token.check()
        process = subprocess.Popen(command, **options)
    started = time.monotonic()
    try:
        while True:
            token.check()
            remaining = timeout-(time.monotonic()-started)
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                stdout, stderr = process.communicate(timeout=min(.2, remaining))
                token.check()
                return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                if time.monotonic()-started >= timeout:
                    raise
    except BaseException:
        if '--name' in command:
            try:
                subprocess.run(['docker','rm','-f',command[command.index('--name')+1]],
                               capture_output=True, timeout=30)
            except (OSError, subprocess.SubprocessError):
                pass
        if process.poll() is None:
            process.kill()
        process.communicate()
        raise


class CheckExecutor:
    """One owner per directory; completed results survive process restarts.

    Interrupted jobs are not silently dispatched again. The caller must inspect
    accepted journals and explicitly submit a new task if work remains.
    """
    def __init__(self, directory, *, workers=4):
        self.worker_capacity = workers
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)
        self.owner_file = (self.root/'executor.lock').open('a+')
        try:
            fcntl.flock(self.owner_file.fileno(),fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.owner_file.close()
            raise RuntimeError('another executor owns this task directory')
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.close_lock = threading.Lock()
        self.shutdown_complete = False
        try:
            self.records, self.tokens, self.futures, self.owners = {}, {}, {}, {}
            self.closed = False
            for path in self.root.glob('*/task.json'):
                record = json.loads(path.read_text())
                if record['status'] not in TERMINAL:
                    receipt_path = path.parent/'completion.json'
                    if receipt_path.is_file():
                        receipt = json.loads(receipt_path.read_text())
                        result = json.loads((path.parent/'result.json').read_text())
                        if receipt.get('identity_hash')!=record.get('identity_hash') or receipt.get('result_sha256')!=digest(result):
                            raise ValueError('recovered result binding mismatch')
                        record.update(status='completed',result_sha256=receipt['result_sha256'],finished_at=receipt['finished_at'])
                    else:
                        record.update(status='interrupted', diagnostic='executor restarted; inspect journal before retry')
                    durable(path, record)
                self.records[record['task_id']] = record
            self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='check-task')
        except BaseException:
            self.owner_file.close()
            raise

    def _save(self, record):
        owner = record.get('owner')
        if record['status'] in TERMINAL and owner is not None and self.owners.get(owner)==record['task_id']:
            del self.owners[owner]
        try:
            durable(self.root/record['task_id']/'task.json', record)
        finally:
            self.condition.notify_all()

    def submit(self, *, kind, theory, binding, request, execute, owner=None, task_id=None):
        if kind not in {'proof_batch','statement','symbol','seal','action','preflight','integration'}:
            raise ValueError('unknown check task kind')
        task_id = task_id or uuid4().hex
        if not isinstance(task_id, str) or not task_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in task_id):
            raise ValueError('invalid task identity')
        identity = dict(kind=kind, theory=theory, binding=deepcopy(binding), request=deepcopy(request))
        with self.lock:
            if self.closed:
                raise RuntimeError('check executor is closed')
            if task_id in self.records:
                if self.records[task_id]['identity_hash'] != digest(identity):
                    raise ValueError('task identity reused with different input')
                return self.get(task_id)
            if owner is not None and owner in self.owners:
                raise ValueError('a proof action is already in flight for this theory')
            sequence = max((row.get('submission_sequence',0) for row in self.records.values()),default=0)+1
            record = dict(task_id=task_id, submission_sequence=sequence, **identity, identity_hash=digest(identity), status='queued',
                          submitted_at=time.time(), owner=owner)
            self.records[task_id] = record
            self.tokens[task_id] = Cancellation()
            if owner is not None:
                self.owners[owner] = task_id
            try:
                self._save(record)
                context = copy_context()
                self.futures[task_id] = self.pool.submit(context.run, self._run, task_id, execute)
            except BaseException as error:
                # A failed write or dispatch must not strand the theory owner.
                # Retain the terminal identity so retrying this ID cannot run it.
                self.tokens[task_id].cancel()
                record.update(status='error', finished_at=time.time(),
                              error_type=type(error).__name__, diagnostic=f'submission failed: {error}')
                try:
                    self._save(record)
                except BaseException as save_error:
                    error.add_note(f'Could not persist submission failure: {save_error}')
                raise
            return self.get(task_id)

    def _run(self, task_id, execute):
        token = self.tokens[task_id]
        marker = CURRENT_TASK.set(token)
        try:
            with self.lock:
                record = self.records[task_id]
                token.check()
                record.update(status='running', started_at=time.time())
                self._save(record)
            result = execute(self.root/task_id)
            with token.lock:
                token.check()
                durable(self.root/task_id/'result.json', result, immutable=True)
                durable(self.root/task_id/'completion.json',{'identity_hash':record['identity_hash'],
                    'result_sha256':digest(result),'finished_at':time.time()},immutable=True)
                with self.lock:
                    record.update(status='completed', finished_at=time.time(), result_sha256=digest(result))
                    self._save(record)
        except CheckCancelled as error:
            with self.lock:
                self.records[task_id].update(status='cancelled', finished_at=time.time(), diagnostic=str(error))
                self._save(self.records[task_id])
        except BaseException as error:
            with self.lock:
                self.records[task_id].update(status='error', finished_at=time.time(),
                    error_type=type(error).__name__, diagnostic=str(error))
                self._save(self.records[task_id])
        finally:
            CURRENT_TASK.reset(marker)
            with self.lock:
                owner = self.records[task_id].get('owner')
                if owner is not None and self.owners.get(owner) == task_id:
                    del self.owners[owner]
                self.condition.notify_all()

    def get(self, task_id):
        with self.lock:
            record = deepcopy(self.records[task_id])
            if record['status'] == 'completed':
                result = json.loads((self.root/task_id/'result.json').read_text())
                if digest(result) != record['result_sha256']:
                    raise ValueError('check result content changed')
                record['result'] = result
            return record

    def wait(self, task_ids, *, timeout=30):
        if not task_ids or len(set(task_ids)) != len(task_ids) or not 0 <= timeout <= 60:
            raise ValueError('distinct task IDs and wait timeout between 0 and 60 required')
        deadline = time.monotonic()+timeout
        with self.condition:
            while True:
                results = [self.get(key) for key in task_ids]
                if any(row['status'] in TERMINAL for row in results) or time.monotonic() >= deadline:
                    return results
                self.condition.wait(deadline-time.monotonic())

    def cancel(self, task_id):
        # Do not hold executor lock while waiting for the commit guard.
        with self.lock:
            record = self.get(task_id)
            if record['status'] in TERMINAL:
                return record
            token = self.tokens.get(task_id)
        allowed = token.cancel() if token is not None else True
        with self.lock:
            future = self.futures.get(task_id)
            row = self.records[task_id]
            if allowed and row['status'] not in TERMINAL and (future is None or future.cancel()):
                row.update(status='cancelled',finished_at=time.time(),diagnostic='cancelled before dispatch')
                owner = row.get('owner')
                if owner is not None and self.owners.get(owner)==task_id:
                    del self.owners[owner]
                self._save(row)
        result = self.get(task_id)
        result['cancellation_requested'] = allowed
        return result

    def close(self):
        with self.close_lock:
            if self.shutdown_complete:
                return
            errors = []
            with self.lock:
                self.closed = True
                pending = [key for key,row in self.records.items() if row['status'] not in TERMINAL]
            try:
                for key in pending:
                    try:
                        self.cancel(key)
                    except BaseException as error:
                        errors.append(error)
            finally:
                try:
                    self.pool.shutdown(wait=True)
                except BaseException as error:
                    errors.append(error)
                else:
                    # A failed join does not prove the writers have stopped.
                    # Retain directory ownership and allow close() to retry.
                    self.owner_file.close()
                    self.shutdown_complete = True
            _raise_cleanup_errors(errors)
