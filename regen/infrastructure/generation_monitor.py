"""Shared observational lifecycle for planning and proof generation.

These files describe execution; they never authorize proof acceptance, budget
changes, recovery, or repository completion. Those remain decisions of the
existing stage controllers and their validated receipts.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading

from regen.execution.proof_commits import durable


class GenerationMonitor:
    def __init__(self, directory, *, run_id, phase='proof', theory=None,
                 task_id=None, pinned=False):
        if phase not in {'planning', 'proof'}:
            raise ValueError('unknown generation monitoring phase')
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.pinned = pinned
        self.identity = {k: v for k, v in dict(
            run_id=run_id, generation_phase=phase, theory=theory, task_id=task_id).items()
            if v is not None}
        self.status = {**self.identity, 'schema_version': 2, 'pid': os.getpid(),
                       'running': True, 'status_authority': 'observational_only',
                       'started_at': self.now(), 'last_event': None, 'active_event': None}
        self.save()

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    def save(self):
        durable(self.directory/'runner-status.json', self.status)

    def emit(self, event, fields):
        if event.endswith('_heartbeat'):
            return None
        with self.lock:
            # A mirrored child event cannot overwrite its observing scope.
            payload = {**{k: v for k, v in fields.items() if v is not None},
                       **self.identity, 'time': self.now(), 'event': event}
            with (self.directory/'runner-events.ndjson').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(payload, ensure_ascii=False, default=str)+'\n')
                stream.flush()
            self.status.update(last_event=payload, active_event=payload,
                               last_event_at=payload['time'], last_transition_at=payload['time'])
            self.save()
        return payload

    def transition(self, stage, state='running', **details):
        """Record the stage controller's decision, not a model's declaration."""
        if state not in {'running', 'completed', 'failed', 'paused'}:
            raise ValueError('unknown workflow stage state')
        with self.lock:
            self.status.update(workflow_stage=stage, workflow_state=state)
            return self.emit('workflow_stage_changed', {
                **details, 'workflow_stage': stage, 'workflow_state': state})

    def update_workspace(self, rows, *, active=(), failures=None, limits=None, observed_at=None):
        """Project validated workspace rows and scheduler state, only on change.

        In particular, checker activity is not counted as accepted proof work.
        The coordinator supplies counts from its existing journal-backed view.
        """
        keys = ('status', 'selected', 'ready_for_proof_action', 'unsealed_providers', 'green_count',
                'checked_contracts', 'checked_auxiliaries', 'checked_generated_nodes',
                'context_revision', 'seal_id', 'provider_wait')
        summary = {
            'theories': {row['theory']: {key: deepcopy(row[key]) for key in keys if key in row}
                         for row in rows},
            'active_theories': sorted(active),
            'failures': {name: {key: value[key] for key in ('status', 'reason') if key in value}
                         for name, value in (failures or {}).items()},
            'limits': dict(limits or {}),
        }
        with self.lock:
            if self.status.get('workspace') == summary:
                return False
            self.status['workspace'] = summary
            self.status['workspace_observed_at'] = observed_at or self.now()
            self.emit('workflow_workspace_changed', {'workspace': summary,
                'workspace_observed_at': self.status['workspace_observed_at']})
        return True

    def close(self, reason='completed'):
        with self.lock:
            if self.status['running']:
                if self.status.get('workflow_state') == 'running' and reason in {'failed', 'paused'}:
                    self.transition(self.status['workflow_stage'], reason)
                self.emit('runner_monitor_stopped', {'reason': reason})
                self.status.update(running=False, stop_reason=reason, stopped_at=self.now())
                self.save()
