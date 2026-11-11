"""Durable check identities and cooperative cancellation, separate from proof verdicts."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import threading

CURRENT_TASK = ContextVar('regen_check_task', default=None)
TERMINAL = frozenset({'completed', 'cancelled', 'error', 'interrupted'})


class CheckCancelled(Exception):
    """Cancellation must never enter the native cold-retry path."""


class NativePreparationError(RuntimeError):
    """The trusted prefix could not be prepared; this is not a candidate RED."""


class ImplementationChangedError(NativePreparationError):
    """Frozen execution code drift is terminal, never model-repairable feedback."""


class NativeWorkerUnavailable(RuntimeError):
    """A launched worker exited before publishing the requested response."""

    def __init__(self, message, *, worker, waiting_for):
        super().__init__(message)
        self.native_worker = worker
        self.waiting_for = waiting_for


class NativeCleanupError(NativePreparationError):
    """Worker disposal was not confirmed; no replacement may be launched."""


class RecoverableNativePreparationError(NativePreparationError):
    """Cleanup-confirmed native incident, backed by an immutable local receipt.

    This authorizes restarting the session, never replaying a candidate or
    changing its verdict/budget. Generic preparation errors remain terminal.
    """

    policy = 'native-worker-session-requeue-v1'

    def __init__(self, message, *, receipt_path, receipt_sha256):
        super().__init__(message)
        self.receipt_path = str(receipt_path)
        self.receipt_sha256 = receipt_sha256
        self.recovery = self.read_receipt()

    def read_receipt(self, root=None):
        import hashlib
        import json
        import re
        from pathlib import Path
        path = Path(self.receipt_path).resolve()
        if root is not None and not path.is_relative_to(Path(root).resolve()):
            raise NativePreparationError('native recovery receipt is outside this run')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != self.receipt_sha256:
            raise NativePreparationError('native recovery receipt changed')
        value = json.loads(raw)
        if (value.get('policy') != self.policy
                or value.get('kind') not in {'worker_exit', 'prefix_timeout'}
                or value.get('worker_discarded') is not True
                or value.get('pending_discarded') is not True
                or value.get('native_response_received') is not False
                or not re.fullmatch(r'[0-9a-f]{32}', value.get('incident_id', ''))
                or not re.fullmatch(r'[0-9a-f]{64}', value.get('prefix_sha256', ''))):
            raise NativePreparationError('invalid native recovery receipt')
        prefix = Path(value['prefix_source_path']).resolve()
        if prefix.parent != path.parent:
            raise NativePreparationError('native recovery prefix is outside its receipt directory')
        if hashlib.sha256(prefix.read_bytes()).hexdigest() != value['prefix_source_sha256']:
            raise NativePreparationError('native recovery prefix changed')
        from regen.execution.batch_checker import body
        if hashlib.sha256(body(prefix.read_text()).encode()).hexdigest() != value['prefix_sha256']:
            raise NativePreparationError('native recovery prefix binding mismatch')
        return value

    def record(self):
        return {'receipt_path': self.receipt_path, 'receipt_sha256': self.receipt_sha256}


@dataclass
class Cancellation:
    event: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)
    published: bool = False

    def check(self):
        if self.event.is_set():
            raise CheckCancelled('check task cancelled; no uncommitted result may be accepted')

    def cancel(self):
        with self.lock:
            if self.published:
                return False
            self.event.set()
            return True

    @contextmanager
    def acceptance(self):
        # Linearize cancellation against durable proof acceptance.
        with self.lock:
            self.check()
            yield
            self.published = True


def check_cancelled():
    token = CURRENT_TASK.get()
    if token is not None:
        token.check()


@contextmanager
def acceptance_guard():
    token = CURRENT_TASK.get()
    if token is None:
        yield
    else:
        with token.acceptance():
            yield
