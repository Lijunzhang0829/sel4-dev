#!/usr/bin/env python3
"""Task-local durable progress transitions; never periodic liveness writes."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
from uuid import uuid4

from regen.execution.proof_commits import durable

_SINK = ContextVar('regen_progress_sink', default=None)
_STDOUT_LOCK = threading.RLock()
_OBSERVER = ContextVar('regen_progress_observer', default=None)
_PUBLICATION_GUARD = ContextVar('regen_proof_publication_guard', default=None)


@contextmanager
def observe_events(observer, *, publication_guard=None):
    """Harness-owned observer; copied with native/transport task contexts."""
    marker = _OBSERVER.set(observer)
    guard_marker = _PUBLICATION_GUARD.set(publication_guard)
    try:
        yield
    finally:
        _PUBLICATION_GUARD.reset(guard_marker)
        _OBSERVER.reset(marker)


@contextmanager
def proof_publication(record):
    """Linearize the trusted clock with durable accepted-proof publication.

    The journal has already validated the record. The context ends immediately
    after successful authoritative publication, before derived HEAD/cache work.
    Failed writes propagate through the guard and must not report new progress.
    """
    guard = _PUBLICATION_GUARD.get()
    if guard is None:
        yield
    else:
        with guard(record):
            yield


class EventSink:
    def __init__(self, directory, *, run_id, theory=None, task_id=None, pinned=False):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.pinned = pinned
        self.identity = {k:v for k,v in dict(run_id=run_id,theory=theory,task_id=task_id).items() if v is not None}
        self.status = {**self.identity,'schema_version':2,'pid':os.getpid(),'running':True,
                       'started_at':datetime.now(timezone.utc).isoformat(),'last_event':None,'active_event':None}
        self.save()

    def save(self):
        durable(self.directory/'runner-status.json', self.status)

    def emit(self, event, fields):
        with self.lock:
            payload = {'time':datetime.now(timezone.utc).isoformat(),'event':event,
                       **self.identity,**{k:v for k,v in fields.items() if v is not None}}
            with (self.directory/'runner-events.ndjson').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(payload,ensure_ascii=False,default=str)+'\n')
                stream.flush()
            self.status.update(last_event=payload, active_event=payload,
                               last_event_at=payload['time'], last_transition_at=payload['time'])
            self.save()
        return payload

    def close(self, reason):
        with self.lock:
            if self.status['running']:
                self.emit('runner_monitor_stopped', {'reason':reason})
                self.status.update(running=False,stop_reason=reason,stopped_at=datetime.now(timezone.utc).isoformat())
                self.save()


@contextmanager
def event_scope(directory, *, run_id, theory=None, task_id=None):
    sink = EventSink(directory,run_id=run_id,theory=theory,task_id=task_id,pinned=True)
    marker = _SINK.set(sink)
    reason = 'completed'
    try:
        yield sink
    except BaseException:
        reason = 'failed'
        raise
    finally:
        sink.close(reason)
        _SINK.reset(marker)


def configure_event_monitor(run_dir, *, run_id, theory=None, heartbeat_seconds=0.0):
    sink = _SINK.get()
    if sink is not None and sink.pinned:
        return
    if sink is not None and sink.directory == Path(run_dir) and sink.status['running']:
        return
    close_event_monitor('reconfigured')
    _SINK.set(EventSink(run_dir,run_id=run_id,theory=theory))


def close_event_monitor(reason='completed'):
    sink = _SINK.get()
    if sink is not None:
        if sink.pinned:
            return  # Its lexical owner closes it, not a nested batch callback.
        sink.close(reason)
        _SINK.set(None)


def emit_event(event, **fields):
    if event.endswith('_heartbeat'):
        return
    observer = _OBSERVER.get()
    if observer is not None:
        observer(event, fields)
    sink = _SINK.get()
    payload = (sink.emit(event,fields) if sink is not None else
               {'time':datetime.now(timezone.utc).isoformat(),'event':event,**fields})
    try:
        with _STDOUT_LOCK:
            print('[regen-event] '+json.dumps(payload,ensure_ascii=False,default=str),flush=True)
    except (BrokenPipeError, OSError):
        pass


def short_node(node_id: str) -> str:
    return node_id.rsplit(':',1)[-1]


@contextmanager
def generation_exclusion(category, **fields):
    """Bounded infrastructure evidence; consumers union, never sum overlaps."""
    phase_id = uuid4().hex
    emit_event('generation_exclusion_started', phase_id=phase_id,
               category=category, **fields)
    try:
        yield
    finally:
        emit_event('generation_exclusion_finished', phase_id=phase_id,
                   category=category, **fields)
