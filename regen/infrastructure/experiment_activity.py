"""Method-neutral inactivity termination and observed usage accounting.

Draft changes are activity, not proof success. Repeated observations and previously
seen versions cannot keep a stalled run alive. Native evidence owns GREEN/seal.
"""
from copy import deepcopy
import math
import threading
import time

from regen.execution.proof_commits import digest
from regen.infrastructure.experiment_ledger import TOKEN_KEYS


class ActivityStop:
    def __init__(self, ledger, seconds=7200, *, poll_seconds=1):
        if seconds <= 0:
            raise ValueError('positive inactivity duration required')
        self.ledger = ledger
        self.seconds = seconds
        self.poll_seconds = poll_seconds
        self.event = threading.Event()
        self.closed = threading.Event()
        self.lock = threading.RLock()
        # Continuation grants a fresh deadline, not fresh content identities.
        self.seen = {(row['data']['target_id'], row['data']['update_kind'],
                      row['data']['value_sha256']) for row in ledger.rows
                     if row['kind'] == 'target_updated'}
        self.deadline = ledger.clock() + seconds
        self.reason = None
        self.stage_id = ledger.append('activity_started', seconds=seconds,
            policy='new-contract-content-or-validation-state-v2')['event_id']
        self.thread = None

    def observe(self, target_id, value, *, kind, theory=None, baseline=False):
        identity = (target_id, kind, digest(value))
        with self.lock:
            self.check()
            if identity in self.seen:
                return False
            self.seen.add(identity)
            if baseline:
                return False
            refresh = not target_id.startswith(('helper:', 'helper-slot:'))
            if refresh:
                self.deadline = self.ledger.clock() + self.seconds
            self.ledger.append('target_updated', target_id=target_id, update_kind=kind,
                theory=theory, value_sha256=identity[2], deadline_unix=self.deadline,
                refreshes_inactivity=refresh)
            return True

    def cancel(self, reason='operator_stop'):
        with self.lock:
            if not self.event.is_set():
                self.reason = reason
                self.event.set()
                self.ledger.append('termination', reason=reason, deadline_unix=self.deadline)

    def check(self):
        from regen.execution.check_tasks import CheckCancelled
        with self.lock:
            if self.ledger.clock() >= self.deadline:
                self.cancel('no_contract_update')
            if self.event.is_set():
                raise CheckCancelled(self.reason or 'experiment stopped')

    def start(self, *, sample=None, abort=None):
        def watch():
            try:
                while not self.closed.wait(self.poll_seconds):
                    self.check()
                    if sample:
                        sample()
            except Exception as error:
                if not self.event.is_set():
                    self.ledger.append('monitor_error', error_type=type(error).__name__, message=str(error))
                    self.cancel('activity_monitor_error')
                if abort:
                    abort()
        self.thread = threading.Thread(target=watch, daemon=True)
        self.thread.start()

    def close(self):
        self.closed.set()
        if self.thread:
            self.thread.join(timeout=10)


GLOBAL_EFFECTIVE_POLICY = 'global-first-contract-green-effective-generation-v1'
GLOBAL_EXCLUDED_PHASES = frozenset({
    'planning', 'theory_initialization', 'statement_preflight',
    'prefix_preparation', 'provider_build', 'heap_preparation',
    'seal', 'integration', 'provider_recovery', 'runtime_setup',
    'stream_retry', 'provider_stream_retry', 'experiment_pause',
})


class GlobalEffectiveActivityStop:
    """Global effective-generation inactivity clock.

    The frozen contract allocation authenticates progress and attributes it to a
    theory. There is one inactivity allowance for the whole experiment:
    switching theories never freezes or renews it. Only the first authoritative
    GREEN observation of a frozen contract renews the allowance.

    Known non-generation phases are explicit exclusions. Process downtime is
    excluded by construction: recovery starts with no selected theory and from
    the last durable effective-time checkpoint, never from elapsed wall time.
    """

    def __init__(self, ledger, seconds=3600, *, poll_seconds=1,
                 checkpoint_seconds=5, monotonic=time.monotonic,
                 resume_authorization=None):
        if (type(seconds) not in (int, float) or not math.isfinite(seconds)
                or seconds <= 0):
            raise ValueError('positive finite inactivity duration required')
        if (type(poll_seconds) not in (int, float)
                or not math.isfinite(poll_seconds) or poll_seconds <= 0):
            raise ValueError('positive finite watchdog interval required')
        if (type(checkpoint_seconds) not in (int, float)
                or not math.isfinite(checkpoint_seconds) or checkpoint_seconds <= 0):
            raise ValueError('positive finite checkpoint interval required')
        if resume_authorization is not None and (not isinstance(resume_authorization, str)
                or not resume_authorization.strip() or len(resume_authorization) > 512):
            raise ValueError('resume authorization must be a nonempty bounded string')
        self.ledger = ledger
        self.seconds = float(seconds)
        self.poll_seconds = poll_seconds
        self.checkpoint_seconds = float(checkpoint_seconds)
        self.monotonic = monotonic
        self.event = threading.Event()
        self.closed = threading.Event()
        self.lock = threading.RLock()
        self.thread = None
        self.reason = None
        self.finished = False
        self.finish_reason = None
        self.window_number = 1
        self.active_theory = None
        self.contract_owners = {}
        self.green_contract_ids = set()
        self.idle_seconds = 0.
        self.effective_seconds = 0.
        self.exclusions = {}
        self._next_exclusion = 0
        self._last_tick = self.monotonic()
        self._last_checkpoint = self._last_tick
        with ledger.lock:
            historical = list(ledger.rows)
        self.seen = {(r['data']['target_id'], r['data']['update_kind'],
                      r['data']['value_sha256']) for r in historical
                     if r['kind'] == 'target_updated'}
        states = [r for r in historical if r['kind'] == 'global_activity_state']
        prior = states[-1] if states else None
        registrations = [r for r in historical if r['kind'] == 'global_activity_registered']
        registration = registrations[-1] if registrations else None
        finished_events = [r for r in historical if r['kind'] == 'global_activity_finished']
        self._registration_persisted = registration is not None
        prior_checkpoint_seconds = None
        if registration:
            registered_owners = registration['data']['contract_owners']
            if registration['data']['allocation_sha256'] != digest(registered_owners):
                raise ValueError('global activity registration digest mismatch')
            if any(r['data']['allocation_sha256'] != registration['data']['allocation_sha256']
                   for r in registrations):
                raise ValueError('conflicting global activity registrations')
        if prior:
            state = prior['data']['state']
            prior_checkpoint_seconds = float(state.get('checkpoint_seconds',
                                                       self.checkpoint_seconds))
            if state['policy'] != GLOBAL_EFFECTIVE_POLICY or state['seconds'] != self.seconds:
                raise ValueError('global effective inactivity policy changed on resume')
            if state.get('finished', False) or finished_events:
                raise ValueError('global effective inactivity clock is durably finished')
            if resume_authorization is None:
                raise ValueError('explicit resume authorization required for existing activity state')
            self.contract_owners = dict(registration['data']['contract_owners']
                if registration else state.get('contract_owners', {}))
            self.green_contract_ids = set(state['green_contract_ids'])
            if not self.green_contract_ids <= self.contract_owners.keys():
                raise ValueError('persisted GREEN identity is absent from frozen allocation')
            self.idle_seconds = float(state['idle_seconds'])
            self.effective_seconds = float(state['effective_seconds'])
            prior_reason = state.get('terminal_reason')
            tail = [r for r in historical if r['sequence'] > prior['sequence']]
            tail_green = [r for r in tail if r['kind'] == 'global_contract_first_green']
            if tail_green:
                self.green_contract_ids.update(r['data']['target_id'] for r in tail_green)
                self.idle_seconds = 0.
                self.effective_seconds = max(self.effective_seconds,
                    max(float(r['data']['cumulative_effective_seconds']) for r in tail_green))
            tail_pauses = [r for r in tail if r['kind'] == 'termination'
                           and r['data'].get('disposition') == 'pause']
            if tail_pauses:
                pause = tail_pauses[-1]['data']
                prior_reason = pause.get('reason')
                self.idle_seconds = max(self.idle_seconds,
                    float(pause.get('idle_seconds', self.idle_seconds)))
                self.effective_seconds = max(self.effective_seconds,
                    float(pause.get('cumulative_effective_seconds', self.effective_seconds)))
            self.window_number = int(state.get('window_number', 1))
            self._next_exclusion = int(state.get('next_exclusion', 0))
            new_window = (prior_reason == 'no_new_contract_global_effective_time'
                          and self.idle_seconds >= self.seconds)
            previous_idle = self.idle_seconds
            if new_window:
                self.idle_seconds = 0.
                self.window_number += 1
            ledger.append('global_activity_resume_authorized',
                authorization=resume_authorization, resumed_from=prior['event_id'],
                previous_boundary=prior['data'].get('boundary'),
                previous_terminal_reason=prior_reason,
                previous_idle_seconds=previous_idle,
                cumulative_effective_seconds=self.effective_seconds,
                green_contract_ids=sorted(self.green_contract_ids),
                new_inactivity_window=new_window,
                window_number=self.window_number)
            if self.contract_owners and not self._registration_persisted:
                ledger.append('global_activity_registered',
                    contract_owners=dict(sorted(self.contract_owners.items())),
                    allocation_sha256=digest(dict(sorted(self.contract_owners.items()))),
                    contract_count=len(self.contract_owners),
                    migrated_from_state=prior['event_id'])
                self._registration_persisted = True
        elif resume_authorization is not None:
            raise ValueError('resume authorization supplied without existing activity state')
        self.stage_id = ledger.append('global_activity_started',
            policy=GLOBAL_EFFECTIVE_POLICY, seconds=self.seconds,
            poll_seconds=self.poll_seconds, checkpoint_seconds=self.checkpoint_seconds,
            resumed_from=prior['event_id'] if prior else None,
            unobserved_shutdown_tail=(prior is not None and
                prior['data'].get('boundary') != 'close'),
            recovered_gate='CLOSED',
            unobserved_tail_upper_bound_seconds=(prior_checkpoint_seconds
                if prior is not None and prior['data'].get('boundary') != 'close' else 0),
            recovery_time_accounting=('only persisted effective seconds; offline time excluded; '
                                      'unobserved shutdown tail is unknown'))['event_id']
        self._write_state('started')

    def _running(self):
        return (not self.event.is_set() and not self.closed.is_set()
                and self.active_theory is not None and not self.exclusions)

    def _settle(self):
        now = self.monotonic()
        if not math.isfinite(now) or now < self._last_tick:
            raise ValueError('effective generation monotonic clock moved backwards')
        if self._running():
            elapsed = now - self._last_tick
            self.idle_seconds += elapsed
            self.effective_seconds += elapsed
        self._last_tick = now

    def _snapshot_locked(self):
        return {
            'policy': GLOBAL_EFFECTIVE_POLICY,
            'seconds': self.seconds,
            'poll_seconds': self.poll_seconds,
            'checkpoint_seconds': self.checkpoint_seconds,
            'green_contract_ids': sorted(self.green_contract_ids),
            'idle_seconds': self.idle_seconds,
            'effective_seconds': self.effective_seconds,
            'remaining_seconds': max(0., self.seconds - self.idle_seconds),
            'active_theory': self.active_theory,
            'exclusions': deepcopy(self.exclusions),
            'next_exclusion': self._next_exclusion,
            'window_number': self.window_number,
            'terminal_reason': self.reason,
            'finished': self.finished,
            'finish_reason': self.finish_reason,
            'paused': self.event.is_set(),
            'closed': self.closed.is_set(),
            'gate_state': 'OPEN' if self._running() else 'CLOSED',
            'deadline_tie_policy': 'first contract GREEN accepted exactly at deadline wins',
        }

    def _write_state(self, boundary):
        self.ledger.append('global_activity_state', boundary=boundary,
                           state=self._snapshot_locked())
        self._last_checkpoint = self._last_tick

    def snapshot(self):
        with self.lock:
            self._settle()
            return self._snapshot_locked()

    @property
    def deadline(self):
        """Informational wall estimate while effective generation is running."""
        with self.lock:
            self._settle()
            if not self._running():
                return None
            return self.ledger.clock() + max(0., self.seconds - self.idle_seconds)

    def register(self, theories, *, baseline_green=()):
        """Freeze a unique contract-to-theory allocation exactly once."""
        owners = {}
        for theory, contracts in theories.items():
            if not isinstance(theory, str) or not theory:
                raise ValueError('theory must be a nonempty string')
            for uid in contracts:
                if not isinstance(uid, str) or not uid:
                    raise ValueError('fixed contract identifiers must be nonempty strings')
                if uid in owners:
                    raise ValueError('fixed contract assigned to multiple theories')
                owners[uid] = theory
        baseline = set(baseline_green)
        if not baseline <= owners.keys():
            raise ValueError('baseline contains an unregistered fixed contract')
        with self.lock:
            self._settle()
            if self.contract_owners and owners != self.contract_owners:
                raise ValueError('global effective inactivity allocation is frozen')
            if not self.contract_owners:
                self.contract_owners = owners
            if not self._registration_persisted:
                self.ledger.append('global_activity_registered',
                    contract_owners=dict(sorted(owners.items())),
                    allocation_sha256=digest(dict(sorted(owners.items()))),
                    contract_count=len(owners))
                self._registration_persisted = True
            self.green_contract_ids.update(baseline)
            self._write_state('registered')
            self._check_locked()

    def select(self, theory):
        """Select current generation work; switching theory does not reset time."""
        with self.lock:
            self._settle()
            self._check_locked()
            if theory is not None and theory not in set(self.contract_owners.values()):
                raise ValueError('cannot select an unregistered theory')
            if theory == self.active_theory:
                return
            previous = self.active_theory
            self.active_theory = theory
            self.ledger.append('global_activity_selected', previous_theory=previous,
                               theory=theory)
            self._write_state('selected')

    def begin_exclusion(self, reason):
        if reason not in GLOBAL_EXCLUDED_PHASES:
            raise ValueError('unknown global effective clock exclusion')
        with self.lock:
            self._settle()
            self._check_locked()
            self._next_exclusion += 1
            handle = str(self._next_exclusion)
            self.exclusions[handle] = reason
            self.ledger.append('global_activity_exclusion_started', handle=handle,
                               reason=reason, theory=self.active_theory)
            self._write_state('exclusion_started')
            return handle

    def end_exclusion(self, handle):
        with self.lock:
            self._settle()
            if handle not in self.exclusions:
                raise ValueError('unknown or already closed exclusion handle')
            reason = self.exclusions.pop(handle)
            self.ledger.append('global_activity_exclusion_finished', handle=handle,
                               reason=reason, theory=self.active_theory)
            self._write_state('exclusion_finished')

    def observe(self, target_id, value, *, kind, theory=None, baseline=False):
        """Observe trusted validation; drafts and helpers never renew the clock."""
        identity = (target_id, kind, digest(value))
        with self.lock:
            self._settle()
            owner = self.contract_owners.get(target_id)
            green = (owner is not None and theory == owner and kind == 'validation'
                     and isinstance(value, dict)
                     and value.get('status') in ('green', 'sealed'))
            first_green = green and target_id not in self.green_contract_ids
            # An authoritative first GREEN exactly on the boundary wins. Any
            # other observation at the boundary enacts the pause first.
            self._check_locked(deadline_progress=first_green and not baseline)
            if identity in self.seen:
                return False
            self.seen.add(identity)
            if first_green:
                self.green_contract_ids.add(target_id)
                if not baseline:
                    before = self.idle_seconds
                    self.idle_seconds = 0.
                    self.ledger.append('global_contract_first_green', theory=theory,
                        target_id=target_id, previous_idle_seconds=before,
                        cumulative_effective_seconds=self.effective_seconds)
            self.ledger.append('target_updated', target_id=target_id,
                update_kind=kind, theory=theory, value_sha256=identity[2],
                refreshes_inactivity=bool(first_green and not baseline),
                activity_policy=GLOBAL_EFFECTIVE_POLICY)
            self._write_state('baseline_green' if baseline and first_green else
                              'first_green' if first_green else 'target_observed')
            return bool(first_green and not baseline)

    def _cancel_locked(self, reason):
        if not self.event.is_set():
            self.reason = reason
            self.event.set()
            self.ledger.append('termination', reason=reason, disposition='pause',
                activity_policy=GLOBAL_EFFECTIVE_POLICY, seconds=self.seconds,
                idle_seconds=self.idle_seconds,
                cumulative_effective_seconds=self.effective_seconds,
                window_number=self.window_number)
            self._write_state('paused')

    def cancel(self, reason='operator_stop'):
        with self.lock:
            self._settle()
            self._cancel_locked(reason)

    def _check_locked(self, *, deadline_progress=False):
        limit_reached = (self.idle_seconds > self.seconds if deadline_progress
                         else self.idle_seconds >= self.seconds)
        if limit_reached:
            self._cancel_locked('no_new_contract_global_effective_time')
        if self.event.is_set():
            from regen.execution.check_tasks import CheckCancelled
            raise CheckCancelled(self.reason or 'experiment paused')

    def check(self):
        with self.lock:
            self._settle()
            self._check_locked()
            if self._last_tick - self._last_checkpoint >= self.checkpoint_seconds:
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

    def finish(self, reason='completed'):
        """Persist an intentionally non-resumable terminal experiment state."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('finish reason must be a nonempty string')
        with self.lock:
            if self.finished:
                return
            self._settle()
            self.finished = True
            self.finish_reason = reason
            self.closed.set()
            self.event.set()
            self.ledger.append('global_activity_finished', reason=reason,
                cumulative_effective_seconds=self.effective_seconds,
                idle_seconds=self.idle_seconds, window_number=self.window_number)
            self._write_state('finished')
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=10)


GLOBAL_EXPLORATION_POLICY = 'global-authoritative-tokens-and-effective-generation-v1'


class GlobalExplorationStop:
    """One global exploration gate with a conjunctive token/time ceiling.

    Usage is incrementally replayed from the append-only ledger.  A thread's
    latest cumulative ``usage_snapshot.totalTokens`` supersedes response-based
    fallback for that thread; cached and reasoning counters are never added to
    ``totalTokens``.  Any unknown current thread total keeps the aggregate
    unknown and therefore cannot satisfy the token half of the stopping rule.
    """

    def __init__(self, ledger, token_limit=1_000_000_000,
                 effective_seconds_limit=172800, *, poll_seconds=1,
                 checkpoint_seconds=5, monotonic=time.monotonic,
                 resume_authorization=None):
        for value, label in ((token_limit, 'token'),
                             (effective_seconds_limit, 'effective time'),
                             (poll_seconds, 'watchdog'),
                             (checkpoint_seconds, 'checkpoint')):
            if (type(value) not in (int, float) or not math.isfinite(value)
                    or value <= 0):
                raise ValueError('positive finite '+label+' limit required')
        if type(token_limit) is not int:
            raise ValueError('token limit must be an integer')
        if resume_authorization is not None and (not isinstance(resume_authorization, str)
                or not resume_authorization.strip() or len(resume_authorization)>512):
            raise ValueError('resume authorization must be a nonempty bounded string')
        self.ledger=ledger;self.token_limit=token_limit
        self.effective_seconds_limit=float(effective_seconds_limit)
        self.poll_seconds=poll_seconds;self.checkpoint_seconds=float(checkpoint_seconds)
        self.monotonic=monotonic;self.event=threading.Event();self.closed=threading.Event()
        self.lock=threading.RLock();self.thread=None;self.reason=None
        self.finished=False;self.finish_reason=None;self.active_theory=None
        self.contract_owners={};self.green_contract_ids=set();self.exclusions={}
        self.effective_seconds=0.;self._next_exclusion=0
        self._last_tick=self.monotonic();self._last_checkpoint=self._last_tick
        self._usage_cursor=0;self._snapshot_totals={};self._response_totals={}
        self._response_unknown=set();self._response_ids=set()
        self._seen_progress=set()
        with ledger.lock: historical=list(ledger.rows)
        states=[r for r in historical if r['kind']=='global_exploration_state']
        prior=states[-1] if states else None
        registrations=[r for r in historical if r['kind']=='global_exploration_registered']
        registration=registrations[-1] if registrations else None
        self._registration_persisted=registration is not None
        if registration:
            owners=registration['data']['contract_owners']
            if registration['data']['allocation_sha256']!=digest(owners):
                raise ValueError('global exploration registration digest mismatch')
            self.contract_owners=dict(owners)
        self._replay_usage(historical)
        for row in historical:
            if row['kind']=='global_exploration_progress':
                data=row['data'];identity=(data['target_id'],data['update_kind'],data['value_sha256'])
                self._seen_progress.add(identity)
                if data.get('first_authoritative_green'):
                    self.green_contract_ids.add(data['target_id'])
        terminal_events=[r for r in historical if r['kind']=='termination'
            and r['data'].get('activity_policy')==GLOBAL_EXPLORATION_POLICY]
        joint_event=next((r for r in reversed(terminal_events)
            if r['data'].get('reason')=='global_exploration_joint_limit_reached'),None)
        finish_event=next((r for r in reversed(historical)
            if r['kind']=='global_exploration_finished'),None)
        if joint_event or finish_event:
            raise ValueError('global exploration is durably finished')
        previous_checkpoint_seconds=None
        if prior:
            state=prior['data']['state']
            if (state['policy']!=GLOBAL_EXPLORATION_POLICY
                    or state['token_limit']!=self.token_limit
                    or state['effective_seconds_limit']!=self.effective_seconds_limit):
                raise ValueError('global exploration policy or limits changed on resume')
            if state.get('finished') or state.get('terminal_reason')=='global_exploration_joint_limit_reached':
                raise ValueError('global exploration is durably finished')
            if resume_authorization is None:
                raise ValueError('explicit resume authorization required for existing exploration state')
            if not self.contract_owners:
                self.contract_owners=dict(state.get('contract_owners',{}))
            self.green_contract_ids=set(state.get('green_contract_ids',[]))
            for row in historical[prior['sequence']:]:
                if row['kind']=='global_exploration_progress' and row['data'].get('first_authoritative_green'):
                    self.green_contract_ids.add(row['data']['target_id'])
            self.effective_seconds=float(state['effective_seconds'])
            later_terminal=next((r for r in reversed(terminal_events)
                if r['sequence']>prior['sequence']),None)
            if later_terminal:
                self.effective_seconds=max(self.effective_seconds,
                    float(later_terminal['data'].get('effective_seconds',0)))
            self._next_exclusion=int(state.get('next_exclusion',0))
            previous_checkpoint_seconds=float(state.get('checkpoint_seconds',self.checkpoint_seconds))
            ledger.append('global_exploration_resume_authorized',
                authorization=resume_authorization,resumed_from=prior['event_id'],
                previous_boundary=prior['data'].get('boundary'),
                previous_terminal_reason=(later_terminal['data'].get('reason')
                    if later_terminal else state.get('terminal_reason')),
                cumulative_effective_seconds=self.effective_seconds,
                authoritative_total_tokens=self._authoritative_total(),
                green_contract_ids=sorted(self.green_contract_ids))
            if self.contract_owners and not self._registration_persisted:
                self._write_registration(migrated_from_state=prior['event_id'])
        elif resume_authorization is not None:
            raise ValueError('resume authorization supplied without existing exploration state')
        self.stage_id=ledger.append('global_exploration_started',
            policy=GLOBAL_EXPLORATION_POLICY,token_limit=self.token_limit,
            effective_seconds_limit=self.effective_seconds_limit,
            poll_seconds=self.poll_seconds,checkpoint_seconds=self.checkpoint_seconds,
            resumed_from=prior['event_id'] if prior else None,recovered_gate='CLOSED',
            unobserved_shutdown_tail=bool(prior and prior['data'].get('boundary')!='close'),
            unobserved_tail_upper_bound_seconds=(previous_checkpoint_seconds
                if prior and prior['data'].get('boundary')!='close' else 0))['event_id']
        self._write_state('started')

    def _running(self):
        return (not self.event.is_set() and not self.closed.is_set()
                and self.active_theory is not None and not self.exclusions)

    def _settle(self):
        now=self.monotonic()
        if not math.isfinite(now) or now<self._last_tick:
            raise ValueError('effective generation monotonic clock moved backwards')
        if self._running():self.effective_seconds+=now-self._last_tick
        self._last_tick=now

    def _consume_usage_row(self,row):
        data=row['data'];thread=data.get('thread_id')
        if not isinstance(thread,str) or not thread:return False
        if row['kind']=='usage_snapshot':
            value=(data.get('total') or {}).get('totalTokens')
            self._snapshot_totals[thread]=value if type(value) is int and value>=0 else None
            return True
        if row['kind']=='response':
            rid=row.get('response_id') or data.get('response_id')
            if not isinstance(rid,str) or not rid or rid in self._response_ids:return False
            self._response_ids.add(rid);usage=data.get('usage')
            value=None if not isinstance(usage,dict) else usage.get('totalTokens')
            if type(value) is int and value>=0:
                self._response_totals[thread]=self._response_totals.get(thread,0)+value
            else:self._response_unknown.add(thread)
            return True
        return False

    def _replay_usage(self,rows):
        changed=False
        for row in rows[self._usage_cursor:]:
            if row['kind'] in ('usage_snapshot','response'):
                changed=self._consume_usage_row(row) or changed
        self._usage_cursor=len(rows)
        return changed

    def _refresh_usage(self):
        with self.ledger.lock:
            end=len(self.ledger.rows);tail=list(self.ledger.rows[self._usage_cursor:end])
        changed=False
        for row in tail:
            if row['kind'] in ('usage_snapshot','response'):
                changed=self._consume_usage_row(row) or changed
        self._usage_cursor=end
        return changed

    def _token_view(self):
        threads=set(self._snapshot_totals)|set(self._response_totals)|self._response_unknown
        if not threads:return None,[],[], 'unknown_no_usage'
        unknown=[];fallback=[];values=[]
        for thread in sorted(threads):
            if thread in self._snapshot_totals:
                value=self._snapshot_totals[thread]
            elif thread in self._response_unknown:
                value=None
            else:
                value=self._response_totals.get(thread)
                fallback.append(thread)
            if value is None:unknown.append(thread)
            else:values.append(value)
        return (None if unknown else sum(values),unknown,fallback,
                'unknown' if unknown else 'authoritative')

    def _authoritative_total(self):return self._token_view()[0]

    def _snapshot_locked(self):
        total,unknown,fallback,status=self._token_view()
        return {'policy':GLOBAL_EXPLORATION_POLICY,'token_limit':self.token_limit,
            'effective_seconds_limit':self.effective_seconds_limit,
            'authoritative_total_tokens':total,'token_status':status,
            'unknown_token_thread_ids':unknown,'response_fallback_thread_ids':fallback,
            'effective_seconds':self.effective_seconds,
            'tokens_reached':total is not None and total>=self.token_limit,
            'effective_time_reached':self.effective_seconds>=self.effective_seconds_limit,
            'green_contract_ids':sorted(self.green_contract_ids),
            'active_theory':self.active_theory,'exclusions':deepcopy(self.exclusions),
            'next_exclusion':self._next_exclusion,'poll_seconds':self.poll_seconds,
            'checkpoint_seconds':self.checkpoint_seconds,'terminal_reason':self.reason,
            'finished':self.finished,'finish_reason':self.finish_reason,
            'closed':self.closed.is_set(),'gate_state':'OPEN' if self._running() else 'CLOSED',
            'usage_cursor_sequence':self._usage_cursor}

    def _write_state(self,boundary):
        self.ledger.append('global_exploration_state',boundary=boundary,state=self._snapshot_locked())
        self._last_checkpoint=self._last_tick

    def snapshot(self):
        with self.lock:self._settle();self._refresh_usage();return self._snapshot_locked()

    def _write_registration(self,**extra):
        owners=dict(sorted(self.contract_owners.items()))
        self.ledger.append('global_exploration_registered',contract_owners=owners,
            allocation_sha256=digest(owners),contract_count=len(owners),**extra)
        self._registration_persisted=True

    def register(self,theories,*,baseline_green=()):
        owners={}
        for theory,contracts in theories.items():
            if not isinstance(theory,str) or not theory:raise ValueError('theory must be nonempty')
            for uid in contracts:
                if not isinstance(uid,str) or not uid or uid in owners:
                    raise ValueError('invalid or duplicate frozen contract')
                owners[uid]=theory
        baseline=set(baseline_green)
        if not baseline<=owners.keys():raise ValueError('unknown baseline contract')
        with self.lock:
            self._settle();self._refresh_usage();self._check_locked()
            if self.contract_owners and owners!=self.contract_owners:
                raise ValueError('global exploration allocation is frozen')
            if not self.contract_owners:self.contract_owners=owners
            if not self._registration_persisted:self._write_registration()
            self.green_contract_ids.update(baseline);self._write_state('registered')

    def select(self,theory):
        with self.lock:
            self._settle();self._refresh_usage();self._check_locked()
            if theory is not None and theory not in set(self.contract_owners.values()):
                raise ValueError('cannot select unregistered theory')
            if theory==self.active_theory:return
            previous=self.active_theory;self.active_theory=theory
            self.ledger.append('global_exploration_selected',previous_theory=previous,theory=theory)
            self._write_state('selected')

    def begin_exclusion(self,reason):
        if reason not in GLOBAL_EXCLUDED_PHASES:raise ValueError('unknown exploration exclusion')
        with self.lock:
            self._settle();self._refresh_usage();self._check_locked();self._next_exclusion+=1
            handle=str(self._next_exclusion);self.exclusions[handle]=reason
            self.ledger.append('global_exploration_exclusion_started',handle=handle,reason=reason)
            self._write_state('exclusion_started');return handle

    def end_exclusion(self,handle):
        with self.lock:
            self._settle()
            if handle not in self.exclusions:raise ValueError('unknown exclusion handle')
            reason=self.exclusions.pop(handle)
            self.ledger.append('global_exploration_exclusion_finished',handle=handle,reason=reason)
            self._write_state('exclusion_finished');self._refresh_usage();self._check_locked()

    def observe(self,target_id,value,*,kind,theory=None,baseline=False):
        with self.lock:
            self._settle();self._refresh_usage();self._check_locked()
            identity=(target_id,kind,digest(value))
            if identity in self._seen_progress:return False
            owner=self.contract_owners.get(target_id)
            green=(owner is not None and owner==theory and kind=='validation'
                and isinstance(value,dict) and value.get('status') in ('green','sealed'))
            first=green and target_id not in self.green_contract_ids
            if first:self.green_contract_ids.add(target_id)
            self._seen_progress.add(identity)
            self.ledger.append('global_exploration_progress',target_id=target_id,theory=theory,
                update_kind=kind,value_sha256=identity[2],first_authoritative_green=first,
                affects_limit=False,baseline=baseline)
            self._write_state('first_green' if first else 'progress_observed')
            return bool(first and not baseline)

    def _cancel_locked(self,reason):
        if not self.event.is_set():
            self.reason=reason;self.event.set()
            joint=reason=='global_exploration_joint_limit_reached'
            if joint:
                self.finished=True;self.finish_reason=reason;self.closed.set()
            total,unknown,_,status=self._token_view()
            self.ledger.append('termination',reason=reason,
                disposition='stop' if joint else 'pause',
                activity_policy=GLOBAL_EXPLORATION_POLICY,
                authoritative_total_tokens=total,unknown_token_thread_ids=unknown,
                token_status=status,effective_seconds=self.effective_seconds,
                token_limit=self.token_limit,effective_seconds_limit=self.effective_seconds_limit)
            self._write_state('finished' if joint else 'paused')

    def cancel(self,reason='operator_pause'):
        with self.lock:self._settle();self._refresh_usage();self._cancel_locked(reason)

    def _check_locked(self):
        total=self._authoritative_total()
        if (total is not None and total>=self.token_limit
                and self.effective_seconds>=self.effective_seconds_limit):
            self._cancel_locked('global_exploration_joint_limit_reached')
        if self.event.is_set():
            from regen.execution.check_tasks import CheckCancelled
            raise CheckCancelled(self.reason or 'global exploration stopped')

    def usage_updated(self):
        with self.lock:
            self._settle();changed=self._refresh_usage();self._check_locked()
            if changed:self._write_state('usage_updated')
            return changed

    def check(self):
        with self.lock:
            self._settle();changed=self._refresh_usage();self._check_locked()
            if changed:self._write_state('usage_updated')
            elif self._last_tick-self._last_checkpoint>=self.checkpoint_seconds:
                self._write_state('checkpoint')

    def start(self,*,sample=None,abort=None):
        if self.thread is not None or self.closed.is_set():raise RuntimeError('monitor already used')
        def watch():
            try:
                while not self.closed.wait(self.poll_seconds):
                    self.check()
                    if sample:sample()
            except Exception as error:
                if not self.event.is_set():
                    self.ledger.append('monitor_error',error_type=type(error).__name__,message=str(error))
                    self.cancel('exploration_monitor_error')
                if abort:abort()
        self.thread=threading.Thread(target=watch,daemon=True);self.thread.start()

    def close(self):
        with self.lock:
            if self.closed.is_set():return
            self._settle();self._refresh_usage();self.closed.set();self._write_state('close')
        if self.thread and self.thread is not threading.current_thread():self.thread.join(timeout=10)

    def finish(self,reason='completed'):
        if not isinstance(reason,str) or not reason.strip():raise ValueError('finish reason required')
        with self.lock:
            if self.finished:return
            self._settle();self._refresh_usage();self.finished=True;self.finish_reason=reason
            self.closed.set();self.event.set()
            self.ledger.append('global_exploration_finished',reason=reason,
                authoritative_total_tokens=self._authoritative_total(),
                cumulative_effective_seconds=self.effective_seconds)
            self._write_state('finished')
        if self.thread and self.thread is not threading.current_thread():self.thread.join(timeout=10)


class UsageSnapshots:
    """Persist cumulative provider snapshots and deltas, never sum snapshots.

Intervals may span several responses: attribution is declared work scope, not a
claim of exact causal per-contract cost. Unknown counters stay unknown.
"""
    def __init__(self, ledger):
        self.ledger = ledger
        self.previous = {}
        for row in ledger.rows:
            if row['kind'] == 'usage_snapshot':
                self.previous[row['data']['thread_id']] = row['data']['total']
        self.lock = threading.RLock()

    def record(self, thread_id, total, *, scope, source, turn_id=None, evidence=None):
        if not thread_id or not isinstance(total, dict):
            return
        total = {k:total.get(k) for k in TOKEN_KEYS}
        if any(v is not None and (type(v) is not int or v < 0) for v in total.values()):
            raise ValueError('invalid usage snapshot')
        with self.lock:
            prior = self.previous.get(thread_id)
            if total == prior:
                return
            delta = {}
            for k, v in total.items():
                old = 0 if prior is None else prior[k]
                delta[k] = None if v is None or old is None else v-old
                if delta[k] is not None and delta[k] < 0:
                    raise ValueError('provider cumulative usage decreased')
            self.ledger.append('usage_snapshot', thread_id=thread_id, turn_id=turn_id,
                total=total, delta=delta, scope=deepcopy(scope), source=source, evidence=evidence,
                attribution='observed interval; declared scope is not causal attribution')
            self.previous[thread_id] = total


def experiment_summary(ledger):
    """Comparable time/token curve, with helpers kept separate from contracts."""
    totals = {}; green = set(); helpers = set(); curve = []; owners = {}; costs=[]; targets={}; checks=[]; seals=[]
    start = ledger.rows[0]['time_unix'] if ledger.rows else ledger.clock()
    segment_start = start
    running_seconds = 0
    stopped_at = None
    for row in ledger.rows:
        data = row['data']
        if row['kind'] == 'experiment_resumed' and segment_start is None:
            segment_start = row['time_unix']
            stopped_at = None
        if row['kind'] in ('experiment_finished', 'experiment_paused') and segment_start is not None:
            running_seconds += max(0, row['time_unix'] - segment_start)
            segment_start = None
            stopped_at = row['time_unix']
        if (row['kind'] == 'termination' and data.get('disposition') == 'pause'
                and segment_start is not None):
            running_seconds += max(0, row['time_unix'] - segment_start)
            segment_start = None
            stopped_at = row['time_unix']
        elapsed = running_seconds + (max(0, row['time_unix'] - segment_start)
                                     if segment_start is not None else 0)
        if row['kind'] == 'usage_snapshot':
            totals[data['thread_id']] = data['total']
            scope=data['scope']; scoped_targets=scope.get('target_ids',[])
            scoped_theories=scope.get('theories')
            if scoped_theories is None:
                scoped_theories=[scope['theory']] if scope.get('theory') else []
            shared=len(scoped_targets)>1 or len(scoped_theories)>1
            costs.append({'event_id':row['event_id'],'time_unix':row['time_unix'],
                'thread_id':data['thread_id'],'scope':scope,'usage':data['delta'],
                'allocation':'shared' if shared else
                    'declared-single-target' if len(scoped_targets)==1 else 'unattributed'})
        if row['kind'] == 'proof_commit':
            theory=data.get('theory')
            if theory is not None:
                prior=owners.get(theory,set());green.difference_update(prior);helpers.difference_update(prior)
                owners[theory]={node['uid'] for node in data['nodes']}
                for uid in prior - owners[theory]:
                    if uid in targets:
                        targets[uid].update(status='removed',event_id=row['event_id'],
                            observed_at_unix=row['time_unix'],journal_artifact=data.get('journal_artifact'),
                            commit_id=data.get('commit_id'))
            for node in data['nodes']:
                targets[node['uid']]={**node,'event_id':row['event_id'],'observed_at_unix':row['time_unix'],
                    'journal_artifact':data.get('journal_artifact'),'commit_id':data.get('commit_id')}
                group = green if node['kind'] == 'contract' else helpers
                if node['status'] in ('green', 'sealed'):
                    group.add(node['uid'])
                else:
                    group.discard(node['uid'])
        if row['kind'] in ('plan_accepted','plan_restored'):
            green.clear(); helpers.clear(); owners.clear()
            for n in targets.values():n['status']='invalidated_by_plan_revision'
        if row['kind']=='check_completed':checks.append({'event_id':row['event_id'],'time_unix':row['time_unix'],**data})
        if row['kind'] in ('seal_attempt','seal_result','sealed','seal_failed'):seals.append({'event_id':row['event_id'],'kind':row['kind'],'time_unix':row['time_unix'],**data})
        if row['kind'] in ('proof_commit', 'usage_snapshot', 'integrated', 'termination'):
            known = [v.get('totalTokens') for v in totals.values()]
            curve.append(dict(event_id=row['event_id'], elapsed_seconds=elapsed,
                wall_elapsed_seconds=row['time_unix']-start,
                total_tokens=sum(known) if known and all(v is not None for v in known) else None,
                contracts_green=len(green), helpers_green=len(helpers)))
    end = ledger.clock() if segment_start is not None else stopped_at
    elapsed = running_seconds + (max(0, end - segment_start)
                                 if segment_start is not None else 0)
    # A live report must include time since its most recent durable event.  A
    # stopped report remains pinned to the latest recorded boundary, so merely
    # reading it later cannot turn pause/offline time into observed run time.
    wall_end=end if segment_start is not None else ledger.rows[-1]['time_unix'] if ledger.rows else end
    from regen.infrastructure.effective_time_budget import budget_journal_summary
    return {'curve':curve, 'elapsed_seconds':elapsed, 'wall_elapsed_seconds':wall_end-start,
        'generation_clock':budget_journal_summary(ledger),
        'time_accounting':'sum of running segments; finished/paused to resumed gaps excluded; wall includes gaps',
        'usage_by_thread':totals, 'usage_accounting':observed_usage(ledger),
        'targets':targets,'checks':checks,'seal_events':seals, 'accounting':'cumulative snapshots; cached input is a subset of input',
        'target_cost_policy':'shared/unattributed intervals retained; no fabricated exclusive allocation',
        'cost_intervals':costs,
        'milestones':{str(n):next((r for r in curve if r['contracts_green']>=n),None) for n in (10,30,50,75,100)},
        'token_curve_precision':'observed cumulative usage at event; delayed/missing provider usage is not zero; no interpolation'}


def observed_usage(ledger):
    """Latest cumulative snapshot per thread; response receipts only as fallback.

    Never add snapshots to response receipts for the same thread. Missing usage
    remains unknown; this is observed accounting, not a claim about unreported work.
    """
    with ledger.lock:
        rows=list(ledger.rows)
    snapshots={};responses={}
    for row in rows:
        data=row['data']
        if row['kind']=='usage_snapshot':snapshots[data['thread_id']]=data['total']
        elif row['kind']=='response':
            responses.setdefault(data['thread_id'],{})[data['response_id']]=data.get('usage')
    threads={}
    for thread in snapshots.keys() | responses.keys():
        if thread in snapshots:
            total=snapshots[thread].get('totalTokens');basis='latest_thread_cumulative_snapshot'
        else:
            values=[None if u is None else u.get('totalTokens') for u in responses[thread].values()]
            total=sum(values) if values and all(v is not None for v in values) else None
            basis='deduplicated_response_receipts_only'
        threads[thread]={'total_tokens':total,'basis':basis}
    values=[v['total_tokens'] for v in threads.values()]
    return {'total_tokens':sum(values) if values and all(v is not None for v in values) else None,
        'threads':threads,'coverage':'observed; unreported usage is not zero',
        'cache_policy':'cached input is included in input; reasoning is included in output'}
