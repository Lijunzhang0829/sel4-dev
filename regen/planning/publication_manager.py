"""Coordinator-owned publication lanes; retain session owners until joined.

A lane releases model-generation capacity, not its checker, native slot, budget
scope or cancellation owner. The session waits for its seal callback and ends
without another model turn. Native checks remain subject to shared resources.
Persisted records are diagnostics, never authority to resume or publish a task.
"""
from threading import RLock
from copy import deepcopy
from regen.execution.proof_commits import durable
from regen.infrastructure.progress_events import emit_event


class PublicationManager:
    def __init__(self, root):
        self.root = root/'publication-tasks'
        self.lock = RLock()
        self.pending = {}
        self.released = set()

    def adopt(self, theory, row):
        with self.lock:
            if theory in self.pending:
                raise ValueError('theory already has a publication owner')
            receipt = {'theory':theory, 'task_id':row['task_id'],
                       'binding':deepcopy(row['binding']),
                       'status':'publication_waiting', 'proof_authority':False}
            durable(self.root/(row['task_id']+'.json'), receipt, immutable=True)
            self.pending[theory] = receipt
        emit_event('publication_task_adopted', **receipt)
        return receipt

    def release_model(self, theory):
        # The caller has exited CodexWorkspaceSession AND clean_codex_home.
        with self.lock:
            receipt = self.pending[theory]
            durable(self.root/(receipt['task_id']+'-model-released.json'),
                    {'theory':theory,'task_id':receipt['task_id'],'provider_closed':True}, immutable=True)
            self.released.add(theory)
        emit_event('publication_model_slot_released', theory=theory, task_id=receipt['task_id'])

    def theories(self):
        with self.lock:
            return set(self.released)

    def generating(self, active):
        return set(active)-self.theories()

    def joined(self, theory):
        # Called only after the entire session future (including cleanup) joins.
        with self.lock:
            receipt = self.pending.pop(theory, None)
            self.released.discard(theory)
        if receipt is not None:
            emit_event('publication_owner_joined', theory=theory, task_id=receipt['task_id'])
