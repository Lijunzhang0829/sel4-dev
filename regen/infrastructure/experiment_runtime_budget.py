"""One experiment clock: elapsed live runtime minus evidenced interruptions."""
from copy import deepcopy

from regen.infrastructure.normal_runtime_budget import NormalRuntimeBudget
from regen.infrastructure.effective_time_budget import EffectiveTimeBudget, CHECKPOINT_KIND
from regen.infrastructure.runtime_intervals import RuntimeIntervals, duration

POLICY='experiment-runtime-minus-evidenced-interruptions-v1'
MODE='experiment-runtime'
DEFAULT_SECONDS=172800
PLANNING='@planning'
GLOBAL='@experiment'


def network_failure(failure):
    if not isinstance(failure,dict):return False
    kind,status=failure.get('kind'),failure.get('http_status')
    return (kind in {'transport','http_connection','response_stream','provider_capacity','app_server_eof'}
            or type(status) is int and (status>=500 or status in {408,425,429}))


def native_failure(uid,binding,evidence):
    return (evidence.get('policy')=='native-no-response-after-cleanup-v1'
        and evidence.get('request_id')==uid
        and isinstance(binding.get('worker'),str) and evidence.get('worker')==binding['worker']
        and all(isinstance(binding.get(k),str) and evidence.get(k)==binding[k]
                for k in ('prefix_sha256','request_sha256','imports_sha256'))
        and evidence.get('response_missing') is True and evidence.get('worker_discarded') is True
        and evidence.get('native_response_received') is False)


class ExperimentRuntimeBudget(NormalRuntimeBudget):
    policy=POLICY
    limit_reason='experiment_runtime_budget_reached'
    experiment_runtime=True

    def __init__(self,ledger,*args,**kwargs):
        self.runtime_intervals=RuntimeIntervals(ledger)
        states=[r for r in ledger.rows if r['kind']==CHECKPOINT_KIND]
        self.runtime_credited=deepcopy(states[-1]['data']['state'].get('runtime_credited',{})) if states else {}
        self.runtime_credited=self.runtime_credited or dict(effective_seconds=0.,worker_seconds=0.,planning_seconds=0.,theory_seconds={})
        super().__init__(ledger,*args,**kwargs)

    def _owner(self,worker):
        return worker if worker is not None else PLANNING if self.planning_active or not self._registered else GLOBAL

    def _settle(self):
        if self.closed.is_set() and not self.finished:
            now=self.monotonic();gap=duration(now-self._last_tick)
            self.normal_wall_seconds+=gap;self.normal_excluded_seconds+=gap
            self._last_tick=now;self._sync_accounting();return
        start=self.normal_wall_seconds;charged=self.effective_seconds
        super()._settle()
        if self.effective_seconds>charged:
            blocked={p['worker_id'] for p in self.exclusions.values() if p.get('confirmed')
                and not (p['reason']=='native_unresponsive' and p['worker_id'] in self._overlap)}
            lanes={w:(t,'worker') for w,t in self.workers.items() if w not in blocked}
            if self.planning_active or not self._registered:lanes[PLANNING]=(None,'planning')
            if not lanes:lanes[GLOBAL]=(None,'global')
            self.runtime_intervals.record(start,self.normal_wall_seconds,lanes,self._overlap)

    def _write_state(self,boundary,**transition):
        self.runtime_intervals.flush()
        return super()._write_state(boundary,**transition)

    def _cancel_locked(self,reason):
        # A provisional cutoff pauses. Late, evidenced correction may restore
        # allowance, but never resumes work without the ordinary authorization.
        return EffectiveTimeBudget._cancel_locked(self,reason)

    def _begin(self,kind,worker,uid,binding):
        with self.lock:
            self._settle();self._check_locked();owner=self._owner(worker)
            theory=self.workers.get(owner)
            self.runtime_intervals.begin(kind,owner,theory,uid,binding,self.normal_wall_seconds)
            self._write_state('experiment_request_started',request_kind=kind,worker_id=owner,request_id=uid)

    def begin_native_request(self,worker_id,request_id,binding):
        if any(not isinstance(binding.get(k),str) or len(binding[k])!=64
               or any(c not in '0123456789abcdef' for c in binding[k])
               for k in ('prefix_sha256','request_sha256')):
            raise ValueError('native timing requires bound request/prefix/import identities')
        return self._begin('native',worker_id,request_id,binding)

    def begin_provider_request(self,worker_id,request_id):
        return self._begin('provider',worker_id,request_id,{})

    def finish_native_request(self,worker_id,request_id,outcome,*,interruption_evidence=None):
        with self.lock:
            self._settle();owner=self._owner(worker_id)
            if outcome not in {'completed','timeout','watchdog','interrupted'}:raise ValueError('invalid native outcome')
            self.runtime_intervals.finish('native',owner,request_id,outcome,self.normal_wall_seconds)
            result=None
            binding=self.runtime_intervals.requests['native:'+request_id]['binding']
            if outcome=='watchdog' and interruption_evidence and native_failure(request_id,binding,interruption_evidence):
                result=self.confirm_interruption('native',request_id,interruption_evidence,
                                                 evidence_id='native:'+request_id)
            self._write_state('experiment_native_finished',request_id=request_id,outcome=outcome)
            return result

    def finish_provider_request(self,worker_id,request_id,*,outcome='success',failure=None,waiting_seconds=None):
        with self.lock:
            self._settle();owner=self._owner(worker_id)
            if outcome not in {'success','failed','retained'}:raise ValueError('invalid provider outcome')
            self.runtime_intervals.finish('provider',owner,request_id,outcome,self.normal_wall_seconds)
            result=None
            if outcome=='failed' and network_failure(failure) and waiting_seconds is not None:
                result=self.confirm_interruption('provider',request_id,
                    {'failure':failure,'waiting_seconds':waiting_seconds},evidence_id='provider:'+request_id)
            self._write_state('experiment_provider_finished',request_id=request_id,outcome=outcome)
            return result

    def confirm_interruption(self,kind,request_id,evidence,*,evidence_id):
        """Trusted harness/audit API, including after detection, pause or close."""
        with self.lock:
            self._settle();key=kind+':'+request_id;row=self.runtime_intervals.requests.get(key)
            if row is None:raise ValueError('unknown request for runtime correction')
            existing=self.runtime_intervals.faults.get(evidence_id)
            if existing is not None:
                if existing['key']!=key or existing['evidence']!=evidence:raise ValueError('interruption evidence changed')
                return dict(evidence_id=evidence_id,request_id=request_id,kind=kind,refund_seconds=0.,
                    total_refunded_seconds=self.runtime_credited['effective_seconds'],interruption=deepcopy(existing))
            if row['end'] is None and self.runtime_intervals.active.get((kind,row['worker']))!=key:
                raise ValueError('unclosed restored request requires crash-tail reconciliation')
            end=row['end'] if row['end'] is not None else self.normal_wall_seconds
            if kind=='native':
                if row['status']!='watchdog' or not native_failure(request_id,row['binding'],evidence):
                    raise ValueError('native correction needs bound missing-response cleanup evidence')
                start=row['start']
            elif kind=='provider':
                if not network_failure(evidence.get('failure')):raise ValueError('correction requires network failure evidence')
                wait=duration(evidence.get('waiting_seconds'))
                start=max(row['start'],end-wait)
            else:raise ValueError('unsupported interruption kind')
            fresh=evidence_id not in self.runtime_intervals.faults
            self.runtime_intervals.confirm(key,start,end,evidence_id,evidence)
            totals=self.runtime_intervals.totals();old=self.runtime_credited
            delta=totals['effective_seconds']-old['effective_seconds']
            self.effective_seconds-=delta;self.normal_excluded_seconds+=delta
            self.worker_seconds-=totals['worker_seconds']-old['worker_seconds']
            self.planning_seconds-=totals['planning_seconds']-old['planning_seconds']
            for theory,value in totals['theory_seconds'].items():
                self._theory_seconds[theory]=self._theory_seconds.get(theory,0.)-(value-old['theory_seconds'].get(theory,0.))
            self.runtime_credited=totals;self._sync_accounting()
            # Preserve exact causal recovery exclusion from the prior worker
            # policy; ordinary startup/switch preparation remains charged.
            if kind=='native' and fresh:
                binding=row['binding']
                self._native_recovery_causes[row['worker']]={'request_id':request_id,
                    'prefix_sha256':binding['prefix_sha256'],'imports_sha256':binding['imports_sha256']}
            receipt=dict(evidence_id=evidence_id,request_id=request_id,kind=kind,
                         refund_seconds=delta,total_refunded_seconds=totals['effective_seconds'],
                         interruption=deepcopy(self.runtime_intervals.faults[evidence_id]))
            self._write_state('experiment_interruption_corrected',correction=receipt)
            return receipt

    def credit_provider_request(self,failed_request_id,successful_request_id):
        # Success is neither needed nor sufficient to label time an outage.
        with self.lock:
            self._settle();self._write_state('experiment_retry_observed',
                failed_request_id=failed_request_id,successful_request_id=successful_request_id)
            return {'refund_seconds':0,'policy':'evidence-required-not-retry-success'}

    def _compact(self):
        value=super()._compact()
        value.update(clock_basis='experiment_runtime',runtime_credited=deepcopy(self.runtime_credited),
            runtime_interval_policy=POLICY,native_timing_policy='normal-work-counts-evidenced-outages-excluded')
        return value

    def snapshot(self):
        value=super().snapshot()
        value.update(clock_basis='experiment_runtime',experiment_clock_policy=POLICY,
            native_timing_policy='normal-work-counts-evidenced-outages-excluded',
            retrospective_refund_seconds=self.runtime_credited['effective_seconds'])
        return value


def budget_instructions(seconds):
    return (f'The experiment allowance is {seconds:g} seconds. All normal work from experiment '
        'start to completion counts, including planning, model reasoning, retrieval, native '
        'preparation, proof failures, ordinary proof timeouts, seals and integration. '
        'Recorded pauses/offline gaps and evidenced unresponsive-worker/network waits do not '
        'count. Confirmed waits may be refunded retrospectively; overlaps are excluded once '
        'and healthy parallel work remains charged. Only trusted harness evidence authorizes '
        'a correction. Budget exhaustion pauses work; a later correction never auto-resumes it. '
        'Read experiment_status for the remaining allowance; do not infer time from GREEN counts.')


def correct_stopped_budget(ledger,kind,request_id,evidence,*,evidence_id):
    """Audit API for a stopped writer; caller must hold its mutation lock.

    No clock or generation is started. This also works after a provisional
    budget cutoff, when an ordinary budget constructor must reject continuation.
    """
    rows=[r for r in ledger.rows if r['kind']==CHECKPOINT_KIND]
    if not rows:raise ValueError('runtime correction requires an existing clock')
    previous=rows[-1];state=deepcopy(previous['data']['state'])
    if state['policy']!=POLICY or state['gate_open'] or state.get('unclean_recoveries'):
        raise ValueError('runtime correction requires a clean stopped unified clock')
    book=RuntimeIntervals(ledger);key=kind+':'+request_id;row=book.requests.get(key)
    if row is None:raise ValueError('unknown request for runtime correction')
    existing=book.faults.get(evidence_id)
    if existing is not None:
        if existing['key']!=key or existing['evidence']!=evidence:raise ValueError('interruption evidence changed')
        return dict(evidence_id=evidence_id,request_id=request_id,kind=kind,refund_seconds=0.,
                    total_refunded_seconds=state['runtime_credited']['effective_seconds'],interruption=deepcopy(existing))
    if row['end'] is None:raise ValueError('unclosed request requires crash-tail reconciliation')
    end=row['end'] if row['end'] is not None else state['normal_wall_seconds']
    if kind=='native':
        if row['status']!='watchdog' or not native_failure(request_id,row['binding'],evidence):
            raise ValueError('native correction needs bound missing-response cleanup evidence')
        start=row['start']
    elif kind=='provider':
        if not network_failure(evidence.get('failure')):raise ValueError('correction requires network failure evidence')
        start=max(row['start'],end-duration(evidence.get('waiting_seconds')))
    else:raise ValueError('unsupported interruption kind')
    book.confirm(key,start,end,evidence_id,evidence)
    totals=book.totals();old=state['runtime_credited'];delta=totals['effective_seconds']-old['effective_seconds']
    state['effective_seconds']-=delta;state['normal_excluded_seconds']+=delta
    state['worker_seconds']-=totals['worker_seconds']-old['worker_seconds']
    state['planning_seconds']-=totals['planning_seconds']-old['planning_seconds']
    for theory,value in totals['theory_seconds'].items():
        state['theory_seconds'][theory]=state['theory_seconds'].get(theory,0.)-(value-old['theory_seconds'].get(theory,0.))
    state['runtime_credited']=totals
    if kind=='native':
        binding=row['binding']
        state['normal_native_recovery_causes'][row['worker']]={'request_id':request_id,
            'prefix_sha256':binding['prefix_sha256'],'imports_sha256':binding['imports_sha256']}
    if not state['finished']:
        gap=duration(ledger.clock()-previous['time_unix'])
        state['normal_wall_seconds']+=gap;state['normal_excluded_seconds']+=gap
    correction=dict(evidence_id=evidence_id,request_id=request_id,kind=kind,refund_seconds=delta,
        total_refunded_seconds=totals['effective_seconds'],interruption=deepcopy(book.faults[evidence_id]))
    ledger.append(CHECKPOINT_KIND,boundary='experiment_interruption_corrected',state=state,correction=correction)
    return correction


def correct_stopped_experiment(run_dir,kind,request_id,evidence,*,evidence_id):
    """Apply a later audit to an offline run through its normal writer lock."""
    from pathlib import Path
    from regen.execution.proof_session_runner import acquire_run_mutation_lock, release_run_mutation_lock
    from regen.infrastructure.incremental_log import read_artifact
    from regen.infrastructure.experiment_ledger import Ledger
    from regen.tools.parallel_workspace_service import status
    root=Path(run_dir).resolve()
    handle=acquire_run_mutation_lock(root,operation='experiment-runtime-correction')
    try:
        observed=status(root)
        if observed['running']:raise ValueError('stop the experiment service before auditing time')
        if not observed['identity_verified'] and observed['status'] not in {'previous_boot','not_loaded'}:
            raise ValueError('cannot verify stopped experiment identity')
        run=read_artifact(root/'run.json')
        return correct_stopped_budget(Ledger(root/'effective-generation-budget',run['run_id']),
            kind,request_id,evidence,evidence_id=evidence_id)
    finally:
        release_run_mutation_lock(handle)
