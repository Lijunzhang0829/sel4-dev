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
