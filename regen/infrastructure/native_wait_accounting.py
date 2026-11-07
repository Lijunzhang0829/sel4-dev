"""Exclude only recorded, non-model native timeout contributors from a union clock.

Successful native work remains charged. Preparation is already excluded by its
phase, so it cannot be credited twice. An interrupted request is retained on
recovery; a partial log never certifies a timeout or authorizes a refund.
"""
from copy import deepcopy
import math

POLICY = 'native-timeout-contributors-v1'


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('invalid native accounting duration')
    return float(value)


class NativeWaitAccounting:
    def __init__(self):
        self.active = {}
        self.requests = {}
        self.segments = {}
        self.credits = {}

    def begin(self, worker, theory, uid, binding):
        if (any(not isinstance(x, str) or not x for x in (worker, theory, uid))
                or worker in self.active or uid in self.requests or uid in self.credits):
            raise ValueError('duplicate or invalid native request')
        if (not isinstance(binding, dict)
                or any(not isinstance(binding.get(k), str) or len(binding[k]) != 64
                       or any(c not in '0123456789abcdef' for c in binding[k])
                       for k in ('prefix_sha256', 'request_sha256'))):
            raise ValueError('native wait requires exact prefix and request digests')
        self.active[worker] = uid
        self.requests[uid] = dict(worker=worker, theory=theory, binding=deepcopy(binding))

    def record(self, seconds, workers, planning, model_workers):
        if not seconds or not self.active:
            return
        contributors = tuple(sorted((worker, theory,
            self.active.get(worker) if worker not in model_workers else None)
            for worker, theory in workers.items()))
        if any(uid is not None for _, _, uid in contributors):
            key = (bool(planning), contributors)
            self.segments[key] = self.segments.get(key, 0.) + seconds

    def finish(self, worker, uid, *, timed_out):
        if uid in self.credits:
            if not timed_out or self.credits[uid]['worker_id'] != worker:
                raise ValueError('native timeout receipt differs')
            return deepcopy(self.credits[uid])
        if self.active.get(worker) != uid or uid not in self.requests:
            raise ValueError('unmatched native wait completion')
        request = self.requests.pop(uid)
        self.active.pop(worker)
        union = worker_seconds = 0.; theories = {}
        updated = {}
        for (planning, contributors), seconds in self.segments.items():
            removed = [(w, t, r) for w, t, r in contributors if r == uid]
            kept = tuple((w, t, r) for w, t, r in contributors if r != uid)
            if timed_out and removed:
                worker_seconds += seconds * len(removed)
                for theory in {t for _, t, _ in removed} - {t for _, t, _ in kept}:
                    theories[theory] = theories.get(theory, 0.) + seconds
                if not planning and not kept:
                    union += seconds
            # Successful or interrupted work is permanent and must keep other
            # lanes' union cost, even if another request later times out.
            if not timed_out:
                kept = tuple((w, t, None if r == uid else r) for w, t, r in contributors)
            if any(r is not None for _, _, r in kept):
                key = (planning, kept)
                updated[key] = updated.get(key, 0.) + seconds
        self.segments = updated
        receipt = dict(policy=POLICY, request_id=uid, worker_id=worker,
            theory=request['theory'], binding=request['binding'],
            effective_seconds=union, worker_seconds=worker_seconds,
            theory_seconds=theories, model_work_retained=True,
            original_consumption_preserved=True)
        if timed_out:
            self.credits[uid] = deepcopy(receipt)
        return receipt

    def state(self):
        return dict(policy=POLICY, active=dict(self.active), requests=deepcopy(self.requests),
            credits=deepcopy(self.credits), segments=[dict(planning=p, seconds=s,
                contributors=[dict(worker=w, theory=t, request_id=r) for w, t, r in c])
                for (p, c), s in self.segments.items()])

    @classmethod
    def restore(cls, state):
        result = cls()
        if state is None:
            return result
        if state.get('policy') != POLICY:
            raise ValueError('native wait accounting policy changed')
        for uid, receipt in state.get('credits', {}).items():
            if receipt.get('policy') != POLICY or receipt.get('request_id') != uid:
                raise ValueError('invalid native timeout credit identity')
            for field in ('effective_seconds', 'worker_seconds'):
                number(receipt.get(field))
            for value in receipt.get('theory_seconds', {}).values():
                number(value)
            result.credits[uid] = deepcopy(receipt)
        # No failure receipt exists for an open request at shutdown. Retain its
        # already charged cost; never turn a crash into a timeout refund.
        return result
