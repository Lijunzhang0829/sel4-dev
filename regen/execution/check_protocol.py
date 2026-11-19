"""Opt-in, source-sealed native checking protocol shared with the baselines.

Missing descriptors retain the historical checker. New comparison inputs bind
this descriptor in semantic_files_sha256; changing it requires a new context/run.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import json
import math
import time
from uuid import uuid4

NAME = 'fresh-process-total-v1'
FILENAME = 'native-check-protocol.json'
SUCCESS_STATUSES = frozenset({'ok', 'green', 'proved', 'disproved', 'refuted', 'context_refuted'})
TIMEOUT_STATUSES = frozenset({'timeout'})
INFRASTRUCTURE_STATUSES = frozenset({'infrastructure_error', 'stack_exhausted', 'resource_exhausted', 'native_interrupt'})
BLOCKED_STATUSES = frozenset({'blocked_dependency'})


def classify_status(status):
    """Canonical native-attempt class shared with the baselines."""
    if status in SUCCESS_STATUSES:
        return 'success'
    if status in TIMEOUT_STATUSES:
        return 'timeout'
    if status in INFRASTRUCTURE_STATUSES:
        return 'infrastructure'
    if status in BLOCKED_STATUSES:
        return 'blocked'
    return 'proof_failure'


def charged_attempt(status):
    """Only proof failures and timeouts consume an attempt."""
    return classify_status(status) in {'timeout', 'proof_failure'}


PROTOCOL = {
    'schema_version': 1, 'name': NAME, 'backend': 'fresh-process-v1',
    'total_timeout_seconds': 300,
    'includes': ['process_start', 'dependency_loading', 'accepted_prefix',
                 'candidate_execution', 'native_audit'],
    'proof_worker_reuse': False,
    'timeout_accounting': 'charged',
    'timeout_result': 'undetermined',
    'cleanup': 'discard_unaccepted_state_before_next_explicit_check',
}
_DEADLINE = ContextVar('native_total_check_deadline', default=None)


class CheckDeadlineExceeded(TimeoutError):
    """A bound check exhausted its time; never a mathematical disproof."""


def descriptor():
    return deepcopy(PROTOCOL)


def configure(checker):
    path = checker.semantic_workspace / FILENAME
    checker.native_check_protocol = None
    if not path.exists():
        return
    value = json.loads(path.read_text())
    if value != PROTOCOL:
        raise ValueError('unsupported native check protocol')
    checker.native_check_protocol = value
    checker.batch_incremental = False
    checker.persistent = False
    checker.timeout = limit(checker, checker.timeout)


def enabled(checker):
    return getattr(checker, 'native_check_protocol', None) == PROTOCOL


def limit(checker, requested=None):
    value = checker.timeout if requested is None else requested
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError('native check deadline must be positive and finite')
    return min(value, PROTOCOL['total_timeout_seconds']) if enabled(checker) else value


@contextmanager
def total_call(checker, requested=None):
    """One deadline includes transient sketch startup and its native audit."""
    if not enabled(checker):
        yield
        return
    end = time.monotonic() + limit(checker, requested)
    previous = _DEADLINE.get()
    token = _DEADLINE.set(min(previous, end) if previous is not None else end)
    try:
        yield
    finally:
        _DEADLINE.reset(token)


def remaining(requested=None):
    end = _DEADLINE.get()
    if end is None:
        return requested
    left = end - time.monotonic()
    if left <= 0:
        raise CheckDeadlineExceeded('native total check deadline reached')
    return min(left, requested) if requested is not None else left


def baseline_config():
    """Trusted deployment metadata; the baseline already enforces this cap."""
    return dict(backend=PROTOCOL['backend'], timeout_seconds=300,
                total_timeout_seconds=300, native_check_protocol=descriptor())


def queued_timeout(checker, source, seconds, *, before_admission=True):
    """No process ran, so publish only undetermined feedback, never evidence."""
    from pathlib import Path
    root=Path(checker.work_dir)
    root.mkdir(parents=True,exist_ok=True)
    path=root/('Queue_Timeout_'+uuid4().hex+'.thy')
    path.write_text(source if isinstance(source,str) else '(* Native check did not execute. *)\n')
    log=path.with_suffix('.log')
    message=('Shared total check deadline reached while waiting for native admission.'
             if before_admission else 'Shared total check deadline reached before native completion.')
    log.write_text(message+'\n')
    return dict(verdict='red',returncode=124,timed_out=True,diagnostic=message,
        source=str(path),log=str(log),timeout_scope='total-check',timeout_seconds=seconds,
        total_timeout_seconds=seconds,native_check_protocol=descriptor(),
        failure_scope='candidate',check_scope=('not-executed-admission-timeout' if before_admission else 'total-check'),
        native_execution_started=False if before_admission else None,publishable=False,incremental=False)
