"""Exact request-level corrections to an already charged parallel union clock.

Only trusted response boundaries label time. A received complete response makes
its work permanent, irrespective of proof success. Failed requests stay charged
until a later successful retry is explicitly paired with them. Pending segments
retain every contributor, so removing one lane never removes a healthy peer.
"""
from copy import deepcopy
import math


POLICY = 'successful-retry-request-exclusion-v1'


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('invalid provider request accounting time')
    return float(value)


def _identity(value):
    if not isinstance(value, str) or not value:
        raise ValueError('nonempty provider request identity required')
    return value


class ProviderRequestAccounting:
    def __init__(self, effective_seconds=0., worker_seconds=0., theory_seconds=None):
        self.raw_effective_seconds = _number(effective_seconds)
        self.raw_worker_seconds = _number(worker_seconds)
        self.raw_theory_seconds = dict(theory_seconds or {})
        self.excluded_effective_seconds = 0.
        self.excluded_worker_seconds = 0.
        self.excluded_theory_seconds = {}
        self.other_excluded_effective_seconds = 0.
        self.other_excluded_worker_seconds = 0.
        self.other_excluded_theory_seconds = {}
        self.sequence = 0
        self.requests = {}
        self.active = {}
        self.credits = {}
        self.segments = {}

    @staticmethod
    def _key(planning, contributors):
        return bool(planning), tuple(sorted(contributors))

    def _add_segment(self, seconds, planning, contributors):
        # None marks permanent, nonrefundable work. Segments with no pending
        # request cannot be affected by any future correction and need no storage.
        if seconds and any(request is not None for _, _, request in contributors):
            key = self._key(planning, contributors)
            self.segments[key] = self.segments.get(key, 0.) + seconds

    def record(self, seconds, workers, planning=False):
        if not seconds or not (workers or planning):
            return
        self.raw_effective_seconds += seconds
        self.raw_worker_seconds += seconds * len(workers)
        for theory in set(workers.values()):
            self.raw_theory_seconds[theory] = self.raw_theory_seconds.get(theory, 0.) + seconds
        self._add_segment(seconds, planning,
            [(worker, theory, self.active.get(worker)) for worker, theory in workers.items()])

    def _next(self):
        self.sequence += 1
        return self.sequence

    def begin(self, worker_id, theory, request_id):
        for value in (worker_id, theory, request_id):
            _identity(value)
        if request_id in self.requests or worker_id in self.active:
            raise ValueError('duplicate or overlapping provider request')
        # The coordinator keeps the full ID inventory in existing start events,
        # not repeated inside every checkpoint. A lane's next request makes its
        # previous ordinary response unnecessary for an immediate retry credit.
        referenced = {r['successful_request_id'] for r in self.credits.values()}
        self.requests = {uid:r for uid, r in self.requests.items()
            if r['worker_id'] != worker_id or r['status'] not in ('success', 'retained', 'unresolved')
            or uid in referenced}
        self.requests[request_id] = dict(worker_id=worker_id, theory=theory,
            status='open', started_sequence=self._next())
        self.active[worker_id] = request_id

    def finish(self, worker_id, request_id, outcome, failure=None):
        if outcome not in ('success', 'failed', 'retained'):
            raise ValueError('invalid provider request outcome')
        request = self.requests.get(request_id)
        if (request is None or request['worker_id'] != worker_id
                or self.active.get(worker_id) != request_id or request['status'] != 'open'):
            raise ValueError('unmatched provider request completion')
        if failure is not None and (outcome != 'failed' or not isinstance(failure, dict)):
            raise ValueError('failure receipt requires a failed provider request')
        request.update(status=outcome, finished_sequence=self._next())
        if failure is not None:
            request['failure'] = deepcopy(failure)
        self.active.pop(worker_id)
        if outcome in ('success', 'retained'):
            self._make_permanent({request_id})

    def _make_permanent(self, request_ids):
        old, self.segments = self.segments, {}
        for (planning, contributors), seconds in old.items():
            self._add_segment(seconds, planning,
                [(w, t, None if r in request_ids else r) for w, t, r in contributors])

    def existing_credit(self, failed_request_id, successful_request_id):
        receipt = self.credits.get(failed_request_id)
        if receipt is not None and receipt['successful_request_id'] != successful_request_id:
            raise ValueError('failed provider request already credited to another retry')
        return deepcopy(receipt)

    def credit(self, failed_request_id, successful_request_id):
        previous = self.existing_credit(failed_request_id, successful_request_id)
        if previous is not None:
            return previous
        failed = self.requests.get(failed_request_id)
        success = self.requests.get(successful_request_id)
        if (failed is None or success is None or failed['status'] != 'failed'
                or success['status'] != 'success' or failed['worker_id'] != success['worker_id']
                or failed['theory'] != success['theory']
                or failed['finished_sequence'] >= success['started_sequence']):
            raise ValueError('credit requires a failed request and its later successful retry in the same lane')
        if any(r['successful_request_id'] == successful_request_id for r in self.credits.values()):
            raise ValueError('successful provider retry already used for another request')
        union = worker = 0.; theories = {}
        old, self.segments = self.segments, {}
        for (planning, contributors), seconds in old.items():
            removed = [(w, t, r) for w, t, r in contributors if r == failed_request_id]
            kept = [(w, t, r) for w, t, r in contributors if r != failed_request_id]
            if removed:
                worker += seconds * len(removed)
                for theory in {t for _, t, _ in removed} - {t for _, t, _ in kept}:
                    theories[theory] = theories.get(theory, 0.) + seconds
                if not kept and not planning:
                    union += seconds
            self._add_segment(seconds, planning, kept)
        self.excluded_effective_seconds += union
        self.excluded_worker_seconds += worker
        for theory, seconds in theories.items():
            self.excluded_theory_seconds[theory] = self.excluded_theory_seconds.get(theory, 0.) + seconds
        receipt = dict(policy=POLICY, failed_request_id=failed_request_id,
            successful_request_id=successful_request_id, worker_id=failed['worker_id'],
            theory=failed['theory'], effective_seconds=union, worker_seconds=worker,
            theory_seconds=theories, original_consumption_preserved=True)
        self.credits[failed_request_id] = deepcopy(receipt)
        failed['status'] = 'credited'
        return receipt

    def restore_unfinished(self):
        """An old process's open request is not evidence of a network failure."""
        abandoned = set(self.active.values())
        for request_id in abandoned:
            self.requests[request_id].update(status='unresolved', finished_sequence=self._next())
        self.active.clear()
        self._make_permanent(abandoned)
        return sorted(abandoned)

    def import_failed_requests(self, requests, segments):
        """Register reviewed old intervals without changing any raw or net cost.

        The migration caller must bind exact immutable source evidence. These
        segments are subsets of costs already present in this accounting base;
        this method does not infer missing intervals or timestamps.
        """
        if self.requests or self.segments or self.credits:
            raise ValueError('historical requests require fresh accounting metadata')
        trial = ProviderRequestAccounting(self.raw_effective_seconds,
            self.raw_worker_seconds, self.raw_theory_seconds)
        for request in requests:
            trial.begin(request['worker_id'], request['theory'], request['request_id'])
            trial.finish(request['worker_id'], request['request_id'], 'failed', request.get('failure'))
        union = worker = 0.; theories = {}
        for segment in segments:
            seconds = _number(segment['seconds'])
            if type(segment['planning']) is not bool:
                raise ValueError('invalid imported planning contribution')
            contributors = []
            for c in segment['contributors']:
                w, t, uid = c['worker_id'], c['theory'], c['request_id']
                _identity(w); _identity(t)
                if uid is not None:
                    request = trial.requests.get(uid)
                    if request is None or (request['worker_id'], request['theory']) != (w, t):
                        raise ValueError('unknown imported failed request contribution')
                contributors.append((w, t, uid))
            if (len({w for w, _, _ in contributors}) != len(contributors)
                    or not any(uid is not None for _, _, uid in contributors)):
                raise ValueError('invalid imported request contributors')
            union += seconds
            worker += seconds * len(contributors)
            for theory in {t for _, t, _ in contributors}:
                theories[theory] = theories.get(theory, 0.) + seconds
            trial._add_segment(seconds, segment['planning'], contributors)
        if (union > self.raw_effective_seconds + 1e-7 or worker > self.raw_worker_seconds + 1e-7
                or any(v > self.raw_theory_seconds.get(t, 0.) + 1e-7 for t, v in theories.items())):
            raise ValueError('imported request intervals exceed already charged history')
        self.requests, self.sequence, self.segments = trial.requests, trial.sequence, trial.segments

    def summary(self):
        return dict(raw_effective_seconds=self.raw_effective_seconds,
            raw_worker_seconds=self.raw_worker_seconds,
            raw_theory_seconds=dict(self.raw_theory_seconds),
            excluded_failed_request_seconds=self.excluded_effective_seconds,
            excluded_failed_request_worker_seconds=self.excluded_worker_seconds,
            excluded_failed_request_theory_seconds=dict(self.excluded_theory_seconds),
            excluded_infrastructure_seconds=self.other_excluded_effective_seconds,
            excluded_infrastructure_worker_seconds=self.other_excluded_worker_seconds,
            excluded_infrastructure_theory_seconds=dict(self.other_excluded_theory_seconds))

    def state(self):
        return dict(policy=POLICY, sequence=self.sequence, **self.summary(),
            requests=deepcopy(self.requests), active=dict(self.active), credits=deepcopy(self.credits),
            segments=[dict(seconds=seconds, planning=planning,
                contributors=[dict(worker_id=w, theory=t, request_id=r) for w, t, r in contributors])
                for (planning, contributors), seconds in self.segments.items()])

    @classmethod
    def restore(cls, value, *, effective_seconds, worker_seconds, theory_seconds,
                infrastructure_correction=None):
        if not isinstance(value, dict) or value.get('policy') != POLICY:
            raise ValueError('provider request accounting binding changed')
        result = cls(value['raw_effective_seconds'], value['raw_worker_seconds'],
                     value['raw_theory_seconds'])
        result.excluded_effective_seconds = _number(value['excluded_failed_request_seconds'])
        result.excluded_worker_seconds = _number(value['excluded_failed_request_worker_seconds'])
        result.excluded_theory_seconds = deepcopy(value['excluded_failed_request_theory_seconds'])
        result.other_excluded_effective_seconds = _number(value.get('excluded_infrastructure_seconds', 0.))
        result.other_excluded_worker_seconds = _number(value.get('excluded_infrastructure_worker_seconds', 0.))
        result.other_excluded_theory_seconds = deepcopy(value.get('excluded_infrastructure_theory_seconds', {}))
        if infrastructure_correction is not None:
            result.other_excluded_effective_seconds += _number(infrastructure_correction['effective_seconds'])
            result.other_excluded_worker_seconds += _number(infrastructure_correction['worker_seconds'])
            for theory, seconds in infrastructure_correction['theory_seconds'].items():
                result.other_excluded_theory_seconds[theory] = (
                    result.other_excluded_theory_seconds.get(theory, 0.) + _number(seconds))
        result.sequence = value['sequence']
        if type(result.sequence) is not int or result.sequence < 0:
            raise ValueError('invalid provider request sequence')
        result.requests = deepcopy(value['requests'])
        result.active = deepcopy(value['active'])
        result.credits = deepcopy(value['credits'])
        for request_id, request in result.requests.items():
            for identity in (request_id, request['worker_id'], request['theory']):
                _identity(identity)
            if request['status'] not in ('open', 'failed', 'success', 'credited', 'unresolved', 'retained'):
                raise ValueError('invalid persisted provider request status')
            if (type(request['started_sequence']) is not int
                    or not 0 < request['started_sequence'] <= result.sequence):
                raise ValueError('invalid provider request start sequence')
            if request['status'] != 'open' and (type(request.get('finished_sequence')) is not int
                    or not request['started_sequence'] < request['finished_sequence'] <= result.sequence):
                raise ValueError('invalid provider request finish sequence')
        open_requests = {uid:r for uid, r in result.requests.items() if r['status'] == 'open'}
        if (len(open_requests) != len(result.active)
                or result.active != {r['worker_id']: uid for uid, r in open_requests.items()}):
            raise ValueError('provider request active lane mismatch')
        for segment in value['segments']:
            seconds = _number(segment['seconds'])
            if type(segment['planning']) is not bool:
                raise ValueError('invalid request accounting planning contribution')
            contributors = []
            for c in segment['contributors']:
                worker, theory, uid = c['worker_id'], c['theory'], c['request_id']
                _identity(worker); _identity(theory)
                if uid is not None:
                    request = result.requests.get(uid)
                    if (request is None or request['worker_id'] != worker or request['theory'] != theory
                            or request['status'] not in ('open', 'failed')):
                        raise ValueError('invalid pending request accounting contribution')
                contributors.append((worker, theory, uid))
            if len({w for w, _, _ in contributors}) != len(contributors):
                raise ValueError('duplicate request accounting worker contribution')
            result._add_segment(seconds, segment['planning'], contributors)
        for uid, credit in result.credits.items():
            failed = result.requests.get(uid)
            success = result.requests.get(credit['successful_request_id'])
            if (credit.get('policy') != POLICY or credit.get('failed_request_id') != uid
                    or failed is None or failed['status'] != 'credited'
                    or success is None or success['status'] != 'success'
                    or failed['worker_id'] != success['worker_id'] or failed['theory'] != success['theory']
                    or failed['finished_sequence'] >= success['started_sequence']):
                raise ValueError('invalid persisted provider retry credit')
        if (set(result.credits) != {uid for uid, r in result.requests.items() if r['status'] == 'credited'}
                or len({r['successful_request_id'] for r in result.credits.values()}) != len(result.credits)):
            raise ValueError('duplicate or missing provider retry credit receipt')
        amounts = [(result.excluded_effective_seconds, 'effective_seconds'),
                   (result.excluded_worker_seconds, 'worker_seconds')]
        for total, key in amounts:
            if not math.isclose(total, sum(_number(r[key]) for r in result.credits.values()),
                                rel_tol=1e-10, abs_tol=1e-7):
                raise ValueError('provider retry credit total differs from receipts')
        for theory in set(result.excluded_theory_seconds) | {
                t for r in result.credits.values() for t in r['theory_seconds']}:
            if not math.isclose(_number(result.excluded_theory_seconds.get(theory, 0.)),
                    sum(_number(r['theory_seconds'].get(theory, 0.)) for r in result.credits.values()),
                    rel_tol=1e-10, abs_tol=1e-7):
                raise ValueError('provider retry theory credit differs from receipts')
        result.validate(effective_seconds, worker_seconds, theory_seconds)
        return result

    def validate(self, effective_seconds, worker_seconds, theory_seconds):
        pairs = [(self.raw_effective_seconds, self.excluded_effective_seconds + self.other_excluded_effective_seconds, effective_seconds),
                 (self.raw_worker_seconds, self.excluded_worker_seconds + self.other_excluded_worker_seconds, worker_seconds)]
        for theory in (set(self.raw_theory_seconds) | set(self.excluded_theory_seconds)
                       | set(self.other_excluded_theory_seconds) | set(theory_seconds)):
            pairs.append((self.raw_theory_seconds.get(theory, 0.),
                          self.excluded_theory_seconds.get(theory, 0.) + self.other_excluded_theory_seconds.get(theory, 0.),
                          theory_seconds.get(theory, 0.)))
        for raw, excluded, net in pairs:
            raw, excluded, net = _number(raw), _number(excluded), _number(net)
            if not math.isclose(raw - excluded, net, rel_tol=1e-10, abs_tol=1e-7):
                raise ValueError('provider request raw and net accounting mismatch')
