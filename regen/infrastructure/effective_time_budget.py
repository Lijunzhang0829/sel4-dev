"""Recoverable, time-only generation budget with trusted per-worker gates.

The clock charges the UNION of open worker intervals, not their sum. A separate
worker-seconds counter exposes concurrency. Tokens remain in the existing usage
ledger and never control this clock. No proof text or model history is copied.
"""
from contextlib import contextmanager
from copy import deepcopy
import math
import threading
import time

from regen.execution.check_tasks import CheckCancelled
from regen.execution.proof_commits import digest
from regen.infrastructure.theory_activity import EXCLUDED_PHASES, MODEL_OVERLAP_EXCLUSIONS

POLICY = 'global-effective-generation-budget-v1'
EFFECTIVE_TIME_BUDGET_POLICY = POLICY
CHECKPOINT_KIND = 'time_budget_checkpoint'
DEFAULT_SECONDS = 172800
LIMIT_REASON = 'effective_time_budget_reached'


def _positive(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError('positive finite '+name+' required')
    return float(value)


def _nonnegative(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('nonnegative finite '+name+' required')
    return float(value)


def budget_journal_summary(ledger):
    """Read-only durable observation; never extrapolate through an offline gap."""
    with ledger.lock:
        rows = list(ledger.rows)
    checkpoints = [r for r in rows if r['kind'] == CHECKPOINT_KIND]
    if not checkpoints:
        return None
    last = checkpoints[-1]
    state = deepcopy(last['data']['state'])
    for row in rows[last['sequence']:]:
        data = row['data']
        if row['kind'] == 'termination' and data.get('activity_policy') == state['policy']:
            for key in ('effective_seconds', 'worker_seconds'):
                state[key] = max(state.get(key, 0.), data.get(key, 0.))
            state.update(paused=True, gate_open=False, reason=data.get('reason'))
            last = row
        elif row['kind'] == 'time_budget_extended':
            state['seconds'] = data['seconds']
            last = row
        elif row['kind'] == 'time_budget_finished':
            state.update(finished=True, gate_open=False, finish_reason=data['reason'])
            last = row
    state.update(remaining_seconds=max(0., state['seconds']-state['effective_seconds']),
        token_limit=None, observed_event_id=last['event_id'],
        observed_at_unix=last.get('time_unix'),
        observation='last durable observation, not a live process or gate liveness claim',
        accounting_complete=not bool(state.get('unclean_recoveries')),
        unobserved_tail_seconds=None if state.get('unclean_recoveries') else 0)
    return state


class EffectiveTimeBudget:
    policy = POLICY
    limit_reason = LIMIT_REASON
    monitor_error_reason = 'effective_time_monitor_error'
    persistence_error_reason = 'effective_time_budget_persistence_error'
    """One experiment clock, independent of GREEN updates and token availability.

    Use select/deactivate and balanced exclusion handles from trusted harness
    callbacks only. On recovery every worker starts CLOSED. A lost crash tail is
    explicitly unknown; checkpoint cadence is NOT a guarantee against a hung OS.
    Caller must hold its experiment single-writer lock for the object's lifetime.
    """
    def __init__(self, ledger, seconds=DEFAULT_SECONDS, *, poll_seconds=1,
                 checkpoint_seconds=30, resume_authorization=None,
                 extension_seconds=None, monotonic=time.monotonic):
        self.base_seconds = _positive(seconds, 'effective time budget')
        self.seconds = self.base_seconds
        self.poll_seconds = _positive(poll_seconds, 'watchdog interval')
        self.checkpoint_seconds = _positive(checkpoint_seconds, 'checkpoint interval')
        if resume_authorization is not None and (
                not isinstance(resume_authorization, str) or not resume_authorization.strip()):
            raise ValueError('nonempty explicit resume authorization required')
        if extension_seconds is not None:
            _positive(extension_seconds, 'extension')
            if resume_authorization is None:
                raise ValueError('extension requires explicit resume authorization')
        self.ledger = ledger
        self.monotonic = monotonic
        self.lock = threading.RLock()
        self.event = threading.Event()
        self.closed = threading.Event()
        self.thread = None
        self.reason = None
        self.finished = False
        self.finish_reason = None
        self.effective_seconds = 0.
        self.worker_seconds = 0.
        self.theories = {}
        self.contract_owners = {}
        self.green_contract_ids = set()
        self.workers = {}
        self.exclusions = {}
        self._overlap = set()
        self._next_exclusion = 0
        self._theory_seconds = {}
        self._last_tick = self.monotonic()
        self._last_checkpoint = self._last_tick
        self.unobserved_shutdown_tail = False
        self.unclean_recoveries = 0
        with ledger.lock:
            history = list(ledger.rows)
        checkpoints = [r for r in history if r['kind'] == CHECKPOINT_KIND]
        registrations = [r for r in history if r['kind'] == 'time_budget_registered']
        self._registered = bool(registrations)
        prior = checkpoints[-1] if checkpoints else None
        if registrations:
            registration = registrations[-1]['data']
            if digest(registration['contract_owners']) != registration['allocation_sha256']:
                raise ValueError('time budget allocation digest mismatch')
            self.contract_owners = dict(registration['contract_owners'])
            names = registration.get('theory_ids', sorted(set(self.contract_owners.values())))
            if (not isinstance(names, list) or len(names) != len(set(names))
                    or any(not isinstance(name, str) or not name for name in names)
                    or not set(self.contract_owners.values()) <= set(names)):
                raise ValueError('invalid time budget theory allocation')
            self.theories = {name: self._new_theory() for name in names}
            for uid, theory in self.contract_owners.items():
                self.theories.setdefault(theory, self._new_theory())['contract_ids'].add(uid)
            self.green_contract_ids.update(registration.get('baseline_green', []))
        for row in history:
            if row['kind'] == 'time_budget_green':
                self.green_contract_ids.add(row['data']['contract_id'])
            elif row['kind'] == 'time_budget_sealed':
                self.theories[row['data']['theory']]['sealed'] = True
            elif row['kind'] == 'seal_invalidated':
                self.theories[row['data']['theory']]['sealed'] = False
        if not self.green_contract_ids <= self.contract_owners.keys():
            raise ValueError('unknown accepted contract in time budget journal')
        for uid in self.green_contract_ids:
            self.theories[self.contract_owners[uid]]['green_contract_ids'].add(uid)
        if any(r['kind'] == 'time_budget_finished' for r in history):
            raise ValueError('completed experiment cannot be resumed')
        if prior:
            state = prior['data']['state']
            if state['policy'] != self.policy or state['base_seconds'] != self.base_seconds:
                raise ValueError('time budget binding changed; use an explicit audited migration')
            if state['finished']:
                raise ValueError('completed experiment cannot be resumed')
            if resume_authorization is None:
                raise ValueError('existing time budget requires explicit resume authorization')
            self.seconds = _positive(state['seconds'], 'persisted budget')
            self.effective_seconds = _nonnegative(state['effective_seconds'], 'persisted time')
            self.worker_seconds = _nonnegative(state['worker_seconds'], 'persisted worker time')
            self._theory_seconds = {key: _nonnegative(value, 'persisted theory time')
                for key, value in state.get('theory_seconds', {}).items()}
            self._next_exclusion = state.get('next_exclusion', 0)
            self.unclean_recoveries = state.get('unclean_recoveries', 0)
            # Either event may be durable before its checkpoint if the process died.
            for row in history[prior['sequence']:]:
                if row['kind'] == 'time_budget_extended':
                    data = row['data']
                    if data['previous_seconds'] != self.seconds:
                        raise ValueError('time budget extension chain mismatch')
                    self.seconds = _positive(data['seconds'], 'persisted extension')
                elif row['kind'] == 'termination' and row['data'].get('activity_policy') == self.policy:
                    data = row['data']
                    self.effective_seconds = max(self.effective_seconds,
                        _nonnegative(data['effective_seconds'], 'persisted cutoff time'))
                    self.worker_seconds = max(self.worker_seconds,
                        _nonnegative(data.get('worker_seconds', self.worker_seconds), 'persisted cutoff worker time'))
                    for key, value in data.get('theory_seconds', {}).items():
                        self._theory_seconds[key] = max(self._theory_seconds.get(key, 0.),
                            _nonnegative(value, 'persisted cutoff theory time'))
            self.unobserved_shutdown_tail = bool(state.get('gate_open'))
            self.unclean_recoveries += int(self.unobserved_shutdown_tail)
            if extension_seconds is None and self.effective_seconds >= self.seconds:
                raise ValueError('effective time budget exhausted; explicit extension required')
            if extension_seconds is not None:
                old = self.seconds
                if self.effective_seconds >= old+extension_seconds:
                    raise ValueError('extension must exceed already consumed effective time')
                self.seconds += extension_seconds
                self._append('time_budget_extended', previous_seconds=old, seconds=self.seconds,
                    additional_seconds=extension_seconds, authorization=resume_authorization,
                    baseline_checkpoint=prior['event_id'], effective_seconds=self.effective_seconds)
        else:
            if resume_authorization is not None or extension_seconds is not None:
                raise ValueError('cannot resume without a persisted time budget checkpoint')
            if registrations or any(r['kind'] in {
                    'theory_activity_state', 'global_activity_state',
                    'global_exploration_state', 'proof_commit'} for r in history):
                raise ValueError('legacy history lacks time budget; audited migration required')
        self.stage_id = self._append('time_budget_started', policy=self.policy,
            base_seconds=self.base_seconds, seconds=self.seconds,
            resumed_from=prior['event_id'] if prior else None,
            authorization=resume_authorization, recovered_gate='CLOSED',
            unobserved_shutdown_tail=self.unobserved_shutdown_tail,
            unobserved_tail_seconds=None if self.unclean_recoveries else 0)['event_id']
        self._write_state('started')

    @staticmethod
    def _new_theory():
        return {'contract_ids': set(), 'green_contract_ids': set(), 'sealed': False}

    @property
    def active_theory(self):
        return self.workers.get('default')

    @property
    def effective_seconds_limit(self):
        return self.seconds

    def _append(self, kind, **fields):
        try:
            return self.ledger.append(kind, **fields)
        except BaseException:
            self.reason = self.persistence_error_reason
            self.event.set()
            raise

    def _open_workers(self):
        if self.event.is_set() or self.closed.is_set() or self.finished:
            return []
        result = []
        for worker, theory in self.workers.items():
            if self.theories[theory]['sealed']:
                continue
            reasons = {p['reason'] for p in self.exclusions.values()
                       if p['worker_id'] in (None, worker)}
            if not reasons or (worker in self._overlap and reasons <= MODEL_OVERLAP_EXCLUSIONS):
                result.append(worker)
        return result

    def _settle(self):
        now = self.monotonic()
        if not math.isfinite(now) or now < self._last_tick:
            self.event.set(); self.reason = 'effective_time_clock_error'
            raise ValueError('effective generation monotonic clock moved backwards')
        workers = self._open_workers()
        elapsed = now-self._last_tick
        if workers:
            self.effective_seconds += elapsed
            self.worker_seconds += elapsed*len(workers)
            for theory in {self.workers[w] for w in workers}:
                self._theory_seconds[theory] = self._theory_seconds.get(theory, 0.)+elapsed
        self._last_tick = now

    def _compact(self):
        return dict(policy=self.policy, base_seconds=self.base_seconds, seconds=self.seconds,
            effective_seconds=self.effective_seconds, worker_seconds=self.worker_seconds,
            theory_seconds=dict(self._theory_seconds), next_exclusion=self._next_exclusion,
            reason=self.reason, paused=self.event.is_set() and not self.finished, finished=self.finished,
            finish_reason=self.finish_reason, gate_open=bool(self._open_workers()),
            checkpoint_seconds=self.checkpoint_seconds, unclean_recoveries=self.unclean_recoveries)

    def _write_state(self, boundary, **transition):
        self._append(CHECKPOINT_KIND, boundary=boundary, state=self._compact(), **transition)
        self._last_checkpoint = self._last_tick

    def snapshot(self):
        with self.lock:
            self._settle()
            value = self._compact()
            value.update(effective_seconds_limit=self.seconds, token_limit=None,
                remaining_seconds=max(0., self.seconds-self.effective_seconds),
                gate_state='OPEN' if self._open_workers() else 'CLOSED',
                active_theory=self.active_theory, active_workers=dict(self.workers),
                exclusions=deepcopy(self.exclusions), terminal_reason=self.reason,
                closed=self.closed.is_set(), green_contract_ids=sorted(self.green_contract_ids),
                contract_owners=dict(self.contract_owners),
                unobserved_shutdown_tail=self.unobserved_shutdown_tail,
                unobserved_tail_seconds=None if self.unclean_recoveries else 0,
                accounting_complete=not bool(self.unclean_recoveries),
                budget_overrun_seconds=max(0., self.effective_seconds-self.seconds),
                theories={t:dict(contract_ids=sorted(s['contract_ids']),
                    green_contract_ids=sorted(s['green_contract_ids']), sealed=s['sealed'],
                    effective_seconds=self._theory_seconds.get(t, 0.))
                    for t,s in self.theories.items()})
            return value

    @property
    def deadline(self):
        with self.lock:
            self._settle()
            return (self.ledger.clock()+max(0., self.seconds-self.effective_seconds)
                    if self._open_workers() else None)

    def register(self, theories, *, baseline_green=()):
        owners = {}
        for theory, ids in theories.items():
            if not isinstance(theory, str) or not theory:
                raise ValueError('nonempty theory required')
            for uid in ids:
                if not isinstance(uid, str) or not uid or uid in owners:
                    raise ValueError('invalid or duplicate fixed contract')
                owners[uid] = theory
        baseline = set(baseline_green)
        if not baseline <= owners.keys():
            raise ValueError('unknown baseline contract')
        with self.lock:
            self._settle(); self._check_locked()
            if self._registered and (owners != self.contract_owners or set(theories) != set(self.theories)):
                raise ValueError('time budget contract allocation is frozen')
            if not self._registered:
                self.contract_owners = owners
                for theory in theories:
                    self.theories[theory] = self._new_theory()
                for uid, theory in owners.items():
                    self.theories[theory]['contract_ids'].add(uid)
                self._append('time_budget_registered', contract_owners=owners,
                    allocation_sha256=digest(owners), theory_ids=sorted(theories),
                    baseline_green=sorted(baseline))
                self._registered = True
            for uid in baseline-self.green_contract_ids:
                self._green(uid, baseline=True)
            self._write_state('registered')

    def select(self, theory, *, worker_id='default'):
        if not isinstance(worker_id, str) or not worker_id:
            raise ValueError('nonempty worker identity required')
        with self.lock:
            self._settle(); self._check_locked()
            if theory is not None and theory not in self.theories:
                raise ValueError('cannot select an unregistered theory')
            if self.workers.get(worker_id) == theory:
                return
            previous = self.workers.get(worker_id)
            if theory is None:
                self.workers.pop(worker_id, None); self._overlap.discard(worker_id)
            else:
                self.workers[worker_id] = theory
            self._write_state('worker_selected', worker_id=worker_id, previous=previous, theory=theory)

    def deactivate(self, worker_id='default'):
        # Teardown must work even after cancellation, without masking the cause.
        with self.lock:
            self._settle()
            if worker_id in self.workers:
                theory = self.workers.pop(worker_id); self._overlap.discard(worker_id)
                self._write_state('worker_stopped', worker_id=worker_id, theory=theory)

    def begin_exclusion(self, reason, *, worker_id=None):
        if reason not in EXCLUDED_PHASES:
            raise ValueError('unknown trusted harness exclusion')
        with self.lock:
            self._settle(); self._check_locked()
            if worker_id is not None and (not isinstance(worker_id, str) or not worker_id):
                raise ValueError('nonempty worker identity required')
            self._next_exclusion += 1
            handle = str(self._next_exclusion)
            self.exclusions[handle] = dict(reason=reason, worker_id=worker_id)
            self._write_state('exclusion_started', handle=handle, reason=reason, worker_id=worker_id)
            return handle

    def end_exclusion(self, handle):
        with self.lock:
            self._settle()
            if handle not in self.exclusions:
                raise ValueError('unmatched exclusion end')
            self.exclusions.pop(handle)
            self._write_state('exclusion_finished', handle=handle)

    @contextmanager
    def exclude(self, reason, *, worker_id=None):
        handle = self.begin_exclusion(reason, worker_id=worker_id)
        try:
            yield
        finally:
            self.end_exclusion(handle)

    # Existing generic controllers use this spelling for seal and integration.
    excluded = exclude

    def set_model_overlap(self, active, *, worker_id='default'):
        if type(active) is not bool:
            raise ValueError('model overlap must be boolean')
        with self.lock:
            self._settle()
            if active:
                self._check_locked()
            if active == (worker_id in self._overlap):
                return
            if active:self._overlap.add(worker_id)
            else:self._overlap.discard(worker_id)
            self._write_state('model_overlap', worker_id=worker_id, active=active)

    def _green(self, uid, *, baseline=False):
        theory = self.contract_owners[uid]
        self._append('time_budget_green', contract_id=uid, theory=theory, baseline=baseline)
        self.green_contract_ids.add(uid)
        self.theories[theory]['green_contract_ids'].add(uid)

    def observe(self, target_id, value, *, kind='validation', theory=None, baseline=False):
        # Called for already committed authority; never grants more generation time.
        with self.lock:
            self._settle()
            if target_id not in self.contract_owners:
                return False
            if theory is not None and self.contract_owners[target_id] != theory:
                raise ValueError('contract belongs to a different theory')
            green = kind in {'validation', 'proof_commit'} and isinstance(value, dict) and value.get('status') in {'green','sealed'}
            if not green or target_id in self.green_contract_ids:
                return False
            self._green(target_id, baseline=baseline); self._write_state('green_observed')
            return not baseline

    def check_publication(self, theory, newly_green):
        with self.lock:
            self._settle(); self._check_locked()
            if theory not in self.theories or not set(newly_green) <= self.theories[theory]['contract_ids']:
                raise ValueError('publication outside frozen contract allocation')

    def mark_sealed(self, theory):
        with self.lock:
            self._settle()
            state = self.theories[theory]
            if not state['contract_ids'] <= state['green_contract_ids']:
                raise ValueError('cannot seal unproved contracts')
            if not state['sealed']:
                self._append('time_budget_sealed', theory=theory)
                state['sealed'] = True; self._write_state('sealed')

    def invalidate_seal(self, theory, *, old_source_identity,
                        current_source_identity, reason, changed_sources=(),
                        missing_imports=()):
        """Supersede a seal without erasing its receipt or first-GREEN costs."""
        with self.lock:
            self._settle()
            if theory not in self.theories or not self.theories[theory]['sealed']:
                raise ValueError('cannot invalidate an unsealed theory')
            self._append('seal_invalidated', theory=theory,
                old_source_identity=old_source_identity,
                current_source_identity=current_source_identity,
                reason=reason, changed_sources=list(changed_sources),
                missing_imports=list(missing_imports))
            self.theories[theory]['sealed'] = False
            self._write_state('seal_invalidated', theory=theory)

    def _cancel_locked(self, reason):
        if self.event.is_set():return
        self.reason = reason; self.event.set()
        self._append('termination', reason=reason, disposition='pause', activity_policy=self.policy,
            effective_seconds=self.effective_seconds, worker_seconds=self.worker_seconds,
            theory_seconds=dict(self._theory_seconds), seconds=self.seconds, token_limit=None)
        self._write_state('paused')

    def cancel(self, reason='operator_pause'):
        with self.lock:
            self._settle(); self._cancel_locked(reason)

    def _check_locked(self):
        if self.effective_seconds >= self.seconds:
            self._cancel_locked(self.limit_reason)
        if self.event.is_set() or self.closed.is_set():
            raise CheckCancelled(self.reason or 'time budget closed')

    def check(self):
        with self.lock:
            self._settle(); self._check_locked()
            if self._last_tick-self._last_checkpoint >= self.checkpoint_seconds:
                self._write_state('checkpoint')

    def usage_updated(self):
        self.check()  # Token absence, size or provider failure never changes time policy.
        return False

    def start(self, *, sample=None, abort=None):
        if self.thread is not None or self.closed.is_set():
            raise RuntimeError('time budget monitor already used')
        def watch():
            try:
                while not self.closed.wait(self.poll_seconds):
                    self.check()
                    if sample:sample()
            except Exception:
                try:
                    if not self.event.is_set():
                        self.cancel(self.monitor_error_reason)
                finally:
                    if abort:abort()
        self.thread = threading.Thread(target=watch, daemon=True)
        self.thread.start()

    def close(self):
        with self.lock:
            if self.closed.is_set():return
            self._settle(); self.closed.set(); self._write_state('close')
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=10)

    def finish(self, reason='completed'):
        if not isinstance(reason, str) or not reason:
            raise ValueError('finish reason required')
        with self.lock:
            self._settle(); self._check_locked()
            self._append('time_budget_finished', reason=reason, effective_seconds=self.effective_seconds)
            self.finished = True; self.finish_reason = reason
            self.event.set(); self.closed.set(); self._write_state('finished')
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=10)
