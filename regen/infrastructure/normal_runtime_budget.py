"""Normal experiment runtime, minus evidenced all-worker interruption intervals.

A versioned new policy; legacy clocks and historical ledger identities stay intact.
Proof failures, native timeouts and successful retries never grant time credits.
"""
from copy import deepcopy
import math

from regen.infrastructure.active_wall_budget import ActiveWallBudget
from regen.infrastructure.effective_time_budget import CHECKPOINT_KIND
from regen.infrastructure.theory_activity import EXCLUDED_PHASES

POLICY='normal-wall-minus-confirmed-interruptions-v1'
LIMIT_REASON='normal_runtime_budget_reached'
INTERRUPTIONS=frozenset({'provider_recovery','provider_stream_retry','stream_retry',
    'experiment_pause','native_unresponsive','provider_interruption','offline'})


class NormalRuntimeBudget(ActiveWallBudget):
    policy=POLICY
    limit_reason=LIMIT_REASON
    normal_runtime=True

    def __init__(self,ledger,*args,handoff=False,**kwargs):
        states=[r for r in ledger.rows if r['kind']==CHECKPOINT_KIND]
        prior=states[-1] if states else None
        old=prior['data']['state'] if prior else {}
        self.normal_wall_seconds=old.get('normal_wall_seconds',old.get('effective_seconds',0.))
        self.normal_excluded_seconds=old.get('normal_excluded_seconds',0.)
        self._provider_handles={};self._provider_pending={};self._normal_credits={}
        kwargs['include_planning']=True
        if prior and old.get('policy')==POLICY and not old.get('gate_open') and not old.get('finished'):
            gap=ledger.clock()-prior['time_unix']
            if not math.isfinite(gap) or gap<0:raise ValueError('invalid recovery wall interval')
            if not handoff:
                self.normal_wall_seconds+=gap;self.normal_excluded_seconds+=gap
        else:gap=0.
        super().__init__(ledger,*args,**kwargs)
        if handoff and gap:
            self.normal_wall_seconds+=gap
            self.effective_seconds+=gap
            self.planning_seconds+=gap
            self._sync_accounting()
            self._write_state('normal_planning_handoff',seconds=gap,refund_seconds=0)
        if self.unclean_recoveries:
            self.cancel('normal_runtime_unobserved_tail')

    def _open_workers(self):
        if self.event.is_set() or self.closed.is_set() or self.finished or getattr(self,'unclean_recoveries',0):return []
        blocked=[p for p in self.exclusions.values() if p.get('confirmed')]
        if any(p['worker_id'] is None for p in blocked):return []
        lanes=set(self.workers)|{p['worker_id'] for p in blocked}
        unavailable={p['worker_id'] for p in blocked}
        if lanes and lanes<=unavailable:return []
        return ['wall']

    def _gate_open(self):return bool(self._open_workers())

    def _sync_accounting(self):
        self.provider_requests.raw_effective_seconds=self.effective_seconds
        self.provider_requests.raw_worker_seconds=self.worker_seconds
        self.provider_requests.raw_theory_seconds=dict(self._theory_seconds)

    def _settle(self):
        now=self.monotonic()
        if not math.isfinite(now) or now<self._last_tick:raise ValueError('normal runtime monotonic clock moved backwards')
        delta=now-self._last_tick
        if not self.closed.is_set() and not self.finished and not getattr(self,'unclean_recoveries',0):
            self.normal_wall_seconds+=delta
            if self.event.is_set():
                self.normal_excluded_seconds+=delta
            else:
                if self._open_workers():
                    self.effective_seconds+=delta
                    blocked={p['worker_id'] for p in self.exclusions.values() if p.get('confirmed')}
                    healthy={w:t for w,t in self.workers.items() if w not in blocked}
                    self.worker_seconds+=delta*len(healthy)
                    if self.planning_active or not self._registered:self.planning_seconds+=delta
                    for theory in set(healthy.values()):self._theory_seconds[theory]=self._theory_seconds.get(theory,0.)+delta
                else:self.normal_excluded_seconds+=delta
        self._last_tick=now
        self._sync_accounting()

    def begin_exclusion(self,reason,*,worker_id=None,confirmed=False,evidence_id=None):
        if reason not in EXCLUDED_PHASES|INTERRUPTIONS:raise ValueError('unknown harness phase')
        if confirmed and (reason not in INTERRUPTIONS or not isinstance(evidence_id,str) or not evidence_id):
            raise ValueError('confirmed interruption requires trusted evidence')
        with self.lock:
            self._settle();self._check_locked();self._next_exclusion+=1
            handle=str(self._next_exclusion)
            self.exclusions[handle]=dict(reason=reason,worker_id=worker_id,
                confirmed=bool(confirmed),evidence_id=evidence_id)
            self._write_state('exclusion_started',handle=handle,**self.exclusions[handle])
            return handle

    def confirm_provider_failure(self,worker_id,evidence_id,*,blocked=True):
        with self.lock:
            self._provider_pending[worker_id]=evidence_id
            if not blocked:return
            if worker_id not in self._provider_handles:
                self._provider_handles[worker_id]=self.begin_exclusion('provider_interruption',
                    worker_id=worker_id,confirmed=True,evidence_id=evidence_id)

    def provider_stopped(self,worker_id):
        if worker_id in self._provider_pending:
            self.confirm_provider_failure(worker_id,self._provider_pending[worker_id])

    def provider_recovered(self,worker_id):
        with self.lock:
            self._provider_pending.pop(worker_id,None)
            handle=self._provider_handles.pop(worker_id,None)
            if handle is not None:self.end_exclusion(handle)

    def begin_native_request(self,worker_id,request_id,binding):
        return self._request('native',worker_id,request_id,binding=binding)

    def finish_native_request(self,worker_id,request_id,outcome):
        return self._request('native',worker_id,request_id,outcome=outcome)

    def begin_provider_request(self,worker_id,request_id):
        return self._request('provider',worker_id,request_id)

    def finish_provider_request(self,worker_id,request_id,*,outcome,failure=None):
        return self._request('provider',worker_id,request_id,outcome=outcome,failure=failure)

    def _request(self,kind,worker_id,request_id,**fields):
        with self.lock:
            self._settle()
            self._write_state('normal_'+kind+'_request',worker_id=worker_id,request_id=request_id,
                fields=fields,refund_seconds=0)
            return {'effective_seconds':0.,'worker_seconds':0.,'theory_seconds':{},'policy':'no-retrospective-time-refund'}

    def credit_provider_request(self,failed_request_id,successful_request_id):
        with self.lock:
            self._settle();self._check_locked()
            key=(failed_request_id,successful_request_id)
            if key not in self._normal_credits:
                self._normal_credits[key]={'failed_request_id':failed_request_id,
                    'successful_request_id':successful_request_id,'effective_seconds':0.,
                    'worker_seconds':0.,'theory_seconds':{},'refund_seconds':0,
                    'policy':'no-retrospective-time-refund'}
                self._write_state('normal_retry_no_refund',receipt=self._normal_credits[key])
            return deepcopy(self._normal_credits[key])

    def bind_wall_limit(self,seconds,**kwargs):
        if seconds is not None:raise ValueError('normal runtime has no fixed calendar cutoff')

    def _compact(self):
        value=super()._compact()
        value.update(clock_basis='normal_runtime',normal_wall_seconds=self.normal_wall_seconds,
            normal_excluded_seconds=self.normal_excluded_seconds,
            native_timing_policy='ordinary-native-timeouts-count; no automatic refunds',
            include_planning=True)
        return value

    def snapshot(self):
        value=super().snapshot()
        known=value['accounting_complete']
        value.update(clock_basis='normal_runtime',experiment_clock_policy=POLICY,
            effective_experiment_seconds=value['effective_seconds'] if known else None,
            observed_effective_seconds_lower_bound=value['effective_seconds'],
            experiment_wall_seconds=self.normal_wall_seconds if known else None,
            excluded_experiment_seconds=self.normal_excluded_seconds if known else None,
            remaining_seconds=value['remaining_seconds'] if known else None,
            native_timing_policy='ordinary-native-timeouts-count; no automatic refunds')
        return value

    def reconcile_unknown_tail(self,*,active_seconds,wall_seconds,evidence_id):
        if (not isinstance(evidence_id,str) or not evidence_id or self.reason!='normal_runtime_unobserved_tail'
                or any(type(v) not in (float,int) or not math.isfinite(v) or v<0 for v in (active_seconds,wall_seconds))
                or active_seconds>wall_seconds):raise ValueError('unknown tail requires trusted reconciliation')
        with self.lock:
            self.effective_seconds+=active_seconds
            self.normal_wall_seconds+=wall_seconds
            self.normal_excluded_seconds+=wall_seconds-active_seconds
            self.unclean_recoveries=0;self.unobserved_shutdown_tail=False
            self.reason=None;self.event.clear();self._last_tick=self.monotonic();self._sync_accounting()
            self._write_state('normal_crash_tail_reconciled',active_seconds=active_seconds,
                wall_seconds=wall_seconds,evidence_id=evidence_id)
            self._check_locked()


def budget_instructions(seconds):
    return (f'The allocated budget is {seconds:g} seconds of normal experiment runtime. '
        'Planning, reasoning, helpers, retrieval, tool waits, ordinary native timeouts, '
        'builds, seals and integration all count. Only trusted evidence of a recorded '
        'pause, confirmed infrastructure interruption with every worker blocked, or '
        'offline recovery can exclude time; overlapping exclusions count once. '
        'Proof failure, no new GREEN result and successful retries do not refund time. '
        'Read experiment_status.generation_clock for remaining effective time; no fixed '
        'calendar deadline applies. An unknown crash tail blocks continuation until '
        'evidence-backed reconciliation. Do not change the clock or reset the allowance. '
        'Keep working on unresolved goals while effective time remains, unless a genuine '
        'infrastructure blocker requires preserving work and reporting the blocker.')
