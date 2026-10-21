"""Per-theory effective generation clocks, driven only by trusted harness events.

This clock is independent of token and experiment wall-time accounting. A caller
must select the theory whose proof generation it is executing, and bracket known
non-generation work with exclusions. Model phase labels are never exclusions.
Only first authoritative GREEN observations of registered fixed contracts refresh
inactivity; helper work consumes time but cannot refresh it.
"""
from contextlib import contextmanager
from copy import deepcopy
import math
import threading
import time

from regen.execution.check_tasks import CheckCancelled
from regen.execution.proof_commits import digest


POLICY = 'per-theory-first-green-effective-generation-v1'
EXCLUDED_PHASES = frozenset({
    'planning', 'theory_initialization', 'statement_preflight',
    'prefix_preparation', 'provider_build', 'heap_preparation',
    'seal', 'integration', 'provider_recovery', 'runtime_setup',
    'stream_retry', 'provider_stream_retry',
})
MODEL_OVERLAP_EXCLUSIONS = frozenset({
    'theory_initialization', 'statement_preflight', 'prefix_preparation',
    'provider_build', 'heap_preparation', 'seal', 'integration', 'runtime_setup',
})


class TheoryActivityStop:
    """One selected theory at a time; switching never renews its allowance.

    State is journaled at every boundary and watchdog checkpoint. Restoring starts
    with no active theory: process downtime is not generation. An unclean shutdown
    can leave the interval after the final checkpoint unobserved; its duration is
    explicitly unknown, never inferred from wall time across a server outage.
    """

    def __init__(self, ledger, seconds=3600, *, poll_seconds=1,
                 monotonic=time.monotonic):
        if (type(seconds) not in (int, float) or not math.isfinite(seconds)
                or seconds <= 0):
            raise ValueError('positive finite inactivity duration required')
        if (type(poll_seconds) not in (int, float)
                or not math.isfinite(poll_seconds) or poll_seconds <= 0):
            raise ValueError('positive finite watchdog interval required')
        self.ledger = ledger
        self.seconds = float(seconds)
        self.poll_seconds = poll_seconds
        self.monotonic = monotonic
        self.event = threading.Event()
        self.closed = threading.Event()
        self.lock = threading.RLock()
        self.thread = None
        self.reason = None
        self.expired_theory = None
        self.active_theory = None
        self.theories = {}
        self.exclusions = {}
        self.model_overlap = False
        self._next_exclusion = 0
        self._last_tick = self.monotonic()
        self._last_checkpoint = self._last_tick
        with ledger.lock:
            historical = list(ledger.rows)
        self.seen = {(r['data']['target_id'], r['data']['update_kind'],
                      r['data']['value_sha256']) for r in historical
                     if r['kind'] == 'target_updated'}
        states = [r for r in historical if r['kind'] == 'theory_activity_state']
        prior = states[-1] if states else None
        if prior:
            state = prior['data']['state']
            if state['policy'] != POLICY or state['seconds'] != self.seconds:
                raise ValueError('effective inactivity policy changed on resume')
            for theory, saved in state['theories'].items():
                self.theories[theory] = {
                    'contract_ids': set(saved['contract_ids']),
                    'green_contract_ids': set(saved['green_contract_ids']),
                    'idle_seconds': float(saved['idle_seconds']),
                    'effective_seconds': float(saved['effective_seconds']),
                    'sealed': bool(saved.get('sealed', False)),
                }
        self.stage_id = ledger.append('theory_activity_started', policy=POLICY,
            seconds=self.seconds, resumed_from=prior['event_id'] if prior else None,
            legacy_history_without_effective_clock=bool(not prior and any(
                r['kind'] in ('proof_commit','activity_started','experiment_resumed') for r in historical)),
            unobserved_shutdown_tail=(prior is not None and
                prior['data'].get('boundary') != 'close'),
            recovery_time_accounting='only persisted effective seconds; downtime excluded; unobserved tail is unknown',
        )['event_id']
        self._write_state('started')

    def _complete(self, state):
        return state['contract_ids'] <= state['green_contract_ids']

    def _running(self):
        return (not self.event.is_set() and not self.closed.is_set()
                and self.active_theory is not None
                and (not self.exclusions or (self.model_overlap and
                     set(self.exclusions.values()) <= MODEL_OVERLAP_EXCLUSIONS))
                and not self.theories[self.active_theory]['sealed'])

    def _settle(self):
        now = self.monotonic()
        if not math.isfinite(now) or now < self._last_tick:
            raise ValueError('effective generation monotonic clock moved backwards')
        if self._running():
            state = self.theories[self.active_theory]
            elapsed = now - self._last_tick
            state['idle_seconds'] += elapsed
            state['effective_seconds'] += elapsed
        self._last_tick = now

    def _snapshot_locked(self):
        theories = {}
        for theory, state in self.theories.items():
            theories[theory] = {
                'contract_ids': sorted(state['contract_ids']),
                'green_contract_ids': sorted(state['green_contract_ids']),
                'idle_seconds': state['idle_seconds'],
                'effective_seconds': state['effective_seconds'],
                'completed_contracts': self._complete(state),
                'sealed': state['sealed'],
                'remaining_seconds': max(0., self.seconds - state['idle_seconds']),
            }
        return {'policy': POLICY, 'seconds': self.seconds,
                'active_theory': self.active_theory,
                'exclusions': deepcopy(self.exclusions), 'theories': theories,
                'model_overlap': self.model_overlap,
                'reason': self.reason, 'expired_theory': self.expired_theory,
                'paused': self.event.is_set(),
                'deadline_tie_policy': 'new contract accepted exactly at deadline wins'}

    def _write_state(self, boundary):
        self.ledger.append('theory_activity_state', boundary=boundary,
                           state=self._snapshot_locked())
        self._last_checkpoint = self._last_tick

    def snapshot(self):
        with self.lock:
            self._settle()
            return self._snapshot_locked()

    @property
    def deadline(self):
        """Informational wall estimate; exclusions/switches make it nonbinding."""
        with self.lock:
            self._settle()
            if not self._running():
                return None
            remaining = self.seconds - self.theories[self.active_theory]['idle_seconds']
            return self.ledger.clock() + max(0., remaining)

    def register(self, theories, *, baseline_green=()):
        """Bind the frozen plan once; seed previously proven fixed contracts."""
        allocation = {}
        owners = {}
        for theory, contracts in theories.items():
            if not isinstance(theory, str) or not theory:
                raise ValueError('theory must be a nonempty string')
            ids = set(contracts)
            if any(not isinstance(uid, str) or not uid for uid in ids):
                raise ValueError('fixed contract identifiers must be nonempty strings')
            for uid in ids:
                if uid in owners:
                    raise ValueError('fixed contract assigned to multiple theories')
                owners[uid] = theory
            allocation[theory] = ids
        baseline = set(baseline_green)
        if not baseline <= owners.keys():
            raise ValueError('baseline contains an unregistered fixed contract')
        with self.lock:
            self._settle()
            if self.theories and allocation != {
                    k: v['contract_ids'] for k, v in self.theories.items()}:
                raise ValueError('effective inactivity allocation is frozen')
            for theory, ids in allocation.items():
                if theory not in self.theories:
                    self.theories[theory] = {
                        'contract_ids': ids, 'green_contract_ids': set(),
                        'idle_seconds': 0., 'effective_seconds': 0., 'sealed': False,
                    }
                self.theories[theory]['green_contract_ids'].update(ids & baseline)
            self._write_state('registered')
            self._check_locked()

    def mark_sealed(self, theory):
        """Suspend a theory only on authoritative seal evidence."""
        with self.lock:
            self._settle()
            self._check_locked()
            if theory not in self.theories:
                raise ValueError('cannot seal an unregistered theory')
            state = self.theories[theory]
            if not self._complete(state):
                raise ValueError('cannot seal a theory with unproved fixed contracts')
            if not state['sealed']:
                state['sealed'] = True
                self.ledger.append('theory_activity_sealed', theory=theory)
                self._write_state('sealed')

    def select(self, theory):
        """Called by the harness after resolving an actual work target."""
        with self.lock:
            self._settle()
            self._check_locked()
            if theory is not None and theory not in self.theories:
                raise ValueError('cannot select an unregistered theory')
            if theory == self.active_theory:
                return
            previous = self.active_theory
            self.active_theory = theory
            self.ledger.append('theory_activity_selected', previous_theory=previous,
                               theory=theory)
            self._write_state('selected')

    def set_model_overlap(self, active):
        """Trusted provider callbacks mark model work overlapping native work.

        Native preparation cannot refund concurrent model reasoning. Confirmed
        provider outages remain excluded even if a stale observation
        says model work exists. This flag must not be exposed as a model tool.
        """
        if type(active) is not bool:
            raise ValueError('model overlap must be boolean')
        with self.lock:
            self._settle()
            self._check_locked()
            if active != self.model_overlap:
                self.model_overlap = active
                self.ledger.append('theory_activity_model_overlap', active=active,
                                   theory=self.active_theory)
                self._write_state('model_overlap')

    def begin_exclusion(self, reason):
        if reason not in EXCLUDED_PHASES:
            raise ValueError('unknown harness exclusion phase')
        with self.lock:
            self._settle()
            self._check_locked()
            self._next_exclusion += 1
            handle = str(self._next_exclusion)
            self.exclusions[handle] = reason
            self.ledger.append('theory_activity_exclusion_started', handle=handle,
                               reason=reason, theory=self.active_theory)
            self._write_state('exclusion_started')
            return handle

    def end_exclusion(self, handle):
        with self.lock:
            self._settle()
            if handle not in self.exclusions:
                raise ValueError('unknown or already closed exclusion handle')
            reason = self.exclusions.pop(handle)
            self.ledger.append('theory_activity_exclusion_finished', handle=handle,
                               reason=reason, theory=self.active_theory)
            self._write_state('exclusion_finished')

    @contextmanager
    def excluded(self, reason):
        handle = self.begin_exclusion(reason)
        try:
            yield handle
        finally:
            self.end_exclusion(handle)

    def observe(self, target_id, value, *, kind, theory=None, baseline=False):
        """Validation calls must come from accepted proof journals, never drafts."""
        identity = (target_id, kind, digest(value))
        with self.lock:
            self._settle()
            known = (theory in self.theories and
                     target_id in self.theories[theory]['contract_ids'])
            green = (known and kind == 'validation' and isinstance(value, dict)
                     and value.get('status') in ('green', 'sealed'))
            first_green = green and target_id not in self.theories[theory]['green_contract_ids']
            # The authoritative acceptance is observed before this budget
            # decision. Match A: an acceptance exactly on the boundary wins;
            # a later proof or a previously enacted cancellation cannot revive
            # the run. Other theories' expired clocks still pause globally.
            self._check_locked(deadline_progress=theory if first_green and not baseline else None)
            if first_green:
                state = self.theories[theory]
                state['green_contract_ids'].add(target_id)
                if not baseline:
                    before = state['idle_seconds']
                    state['idle_seconds'] = 0.
                    self.ledger.append('theory_contract_first_green', theory=theory,
                        target_id=target_id, previous_idle_seconds=before,
                        cumulative_effective_seconds=state['effective_seconds'])
            if identity in self.seen:
                if first_green:
                    self._write_state('baseline_green' if baseline else 'first_green')
                return False
            self.seen.add(identity)
            if baseline:
                if first_green:
                    self._write_state('baseline_green')
                return False
            self.ledger.append('target_updated', target_id=target_id, update_kind=kind,
                theory=theory, value_sha256=identity[2],
                refreshes_inactivity=bool(first_green), activity_policy=POLICY)
            self._write_state('first_green' if first_green else 'target_observed')
            return True

    def _cancel_locked(self, reason, theory=None):
        if not self.event.is_set():
            self.reason = reason
            self.expired_theory = theory
            self.event.set()
            self.ledger.append('termination', reason=reason, disposition='pause',
                theory=theory, activity_policy=POLICY, seconds=self.seconds)
            self._write_state('paused')

    def cancel(self, reason='operator_stop'):
        with self.lock:
            self._settle()
            self._cancel_locked(reason)

    def _check_locked(self, *, deadline_progress=None):
        for theory, state in self.theories.items():
            limit_reached = (state['idle_seconds'] > self.seconds if theory == deadline_progress
                             else state['idle_seconds'] >= self.seconds)
            if not state['sealed'] and limit_reached:
                self._cancel_locked('no_new_contract_effective_time', theory)
                break
        if self.event.is_set():
            raise CheckCancelled(self.reason or 'experiment paused')

    def check_publication(self, theory, new_ids):
        """Check an authoritative acceptance before writing, without refreshing.

        The publication guard holds ``lock`` through the write and subsequent
        observation. Only first fixed-contract GREEN may win an exact deadline
        tie; failed writes leave all progress identities and budgets unchanged.
        """
        with self.lock:
            self._settle()
            if theory not in self.theories:
                raise ValueError('publication theory is not registered')
            state = self.theories[theory]
            pending = set(new_ids)
            if not pending <= state['contract_ids']:
                raise ValueError('publication contains unknown fixed contracts')
            first = pending - state['green_contract_ids']
            self._check_locked(deadline_progress=theory if first else None)

    def check(self):
        with self.lock:
            self._settle()
            self._check_locked()
            if self._last_tick - self._last_checkpoint >= self.poll_seconds:
                self._write_state('checkpoint')

    def start(self, *, sample=None, abort=None):
        if self.thread is not None or self.closed.is_set():
            raise RuntimeError('activity monitor can only be started once')

        def watch():
            try:
                while not self.closed.wait(self.poll_seconds):
                    self.check()
                    if sample:
                        sample()
            except Exception as error:
                if not self.event.is_set():
                    self.ledger.append('monitor_error', error_type=type(error).__name__,
                                       message=str(error))
                    self.cancel('activity_monitor_error')
                if abort:
                    abort()
        self.thread = threading.Thread(target=watch, daemon=True)
        self.thread.start()

    def close(self):
        with self.lock:
            if self.closed.is_set():
                return
            self._settle()
            self.closed.set()
            self._write_state('close')
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=10)
