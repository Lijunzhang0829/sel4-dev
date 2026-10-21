"""Explicit historical branch accounting; original work is retained for audit.

This is not a refund API for ordinary retries. The supervisor must restore both
workers to a reviewed historical proof checkpoint and exclude ALL later results.
"""
from copy import deepcopy
import math
from pathlib import Path
import time

from regen.execution.proof_commits import digest
from regen.infrastructure.incremental_log import read_artifact
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.effective_time_budget import CHECKPOINT_KIND, POLICY


def state_at_cutoff(rows, cutoff):
    if type(cutoff) not in (int, float) or not math.isfinite(cutoff):
        raise ValueError('finite historical cutoff required')
    checkpoints = [r for r in rows if r['kind'] == CHECKPOINT_KIND]
    before = [r for r in checkpoints if r['time_unix'] <= cutoff]
    after = [r for r in checkpoints if r['time_unix'] > cutoff]
    if not before or not after:
        raise ValueError('cutoff must be bracketed by durable clock checkpoints')
    left, right = before[-1], after[0]
    a, b = left['data']['state'], right['data']['state']
    if a.get('unclean_recoveries') or a['finished'] or a['paused']:
        raise ValueError('cutoff is not in a clean running interval')
    interval = right['time_unix'] - left['time_unix']
    if not 0 < interval <= 120:
        raise ValueError('clock bracket is too wide or nonmonotonic')
    # Every trusted gate transition writes a checkpoint. A lifecycle/termination
    # event between these two snapshots would break constant-rate interpolation.
    between = rows[left['sequence']:right['sequence']-1]
    if any(r['kind'] in {'termination', 'time_budget_started', 'time_budget_extended',
                         'time_budget_finished', 'generation_clock_untrusted'} for r in between):
        raise ValueError('ambiguous clock transition inside cutoff bracket')
    fraction = (cutoff-left['time_unix'])/interval
    result = deepcopy(a)
    deltas = {k:b[k]-a[k] for k in ('effective_seconds','worker_seconds')}
    theories = set(a['theory_seconds']) | set(b['theory_seconds'])
    td = {t:b['theory_seconds'].get(t,0)-a['theory_seconds'].get(t,0) for t in theories}
    rates = [deltas['effective_seconds']/interval, *[v/interval for v in td.values()]]
    # Wall timestamps bracket monotonic counters; reject jumps/drift rather than
    # pretending wall and monotonic samples are precisely simultaneous.
    residual = max(abs(rate-round(rate))*interval for rate in rates)
    if (any(not 0 <= round(rate) <= 1 for rate in rates) or residual > .1
            or abs(sum(td.values())-deltas['worker_seconds']) > .001
            or round(rates[0]) != int(any(round(v/interval) for v in td.values()))):
        raise ValueError('cutoff clocks have inconsistent rates or wall-clock drift')
    for key, delta in deltas.items():
        result[key] += fraction*delta
    result['theory_seconds'] = {t:a['theory_seconds'].get(t,0)+fraction*td[t] for t in theories}
    result.update(paused=True, gate_open=False, reason='authorized_historical_branch', finished=False)
    return result, {'left_event_id':left['event_id'], 'right_event_id':right['event_id'],
        'cutoff_unix':cutoff, 'bracket_seconds':interval,
        'method':'constant-gate interpolation of monotonic totals between durable wall samples',
        'wall_monotonic_bracket_residual_seconds':residual,
        'not_subsecond_exact':True}


def carry_checkpoint_budget(source, target, *, authorization, review):
    """Caller holds the stopped source's mutation lock and validates proof fork."""
    source, target = Path(source).resolve(), Path(target).resolve()
    if not isinstance(authorization,str) or not authorization.strip():
        raise ValueError('explicit rollback authorization required')
    checkpoint = read_artifact(target/'checkpoint.json')
    origin = read_artifact(target/'workspace-continuation.json')
    if (checkpoint['source_run'] != str(source) or origin['source_run'] != str(source)
            or review.get('checkpoint_sha256') != digest(checkpoint)
            or review.get('disposition') != 'exclude_entire_post_fault_branch_and_results'):
        raise ValueError('review must bind the exact proof rollback checkpoint')
    old_run, new_run = [read_artifact(p/'run.json') for p in (source,target)]
    if old_run['run_id'] == new_run['run_id']:
        raise ValueError('rollback requires a new execution identity')
    ledger = Ledger(source/'effective-generation-budget',old_run['run_id'])
    if review.get('source_tail_event_id') != ledger.rows[-1]['event_id']:
        raise ValueError('source accounting changed since review')
    bindings = [r for r in ledger.rows if r['kind']=='parallel_budget_binding']
    if (len(bindings)!=1 or bindings[0]['data']['run_sha256']!=digest(old_run)
            or bindings[0]['data']['policy']!=POLICY
            or bindings[0]['data']['configuration_sha256'] != digest(
                read_artifact(source/'parallel-workspace-policy.json'))):
        raise ValueError('source clock binding mismatch')
    last = [r for r in ledger.rows if r['kind']==CHECKPOINT_KIND][-1]['data']['state']
    if not last['paused'] or last['gate_open'] or last['finished'] or last.get('unclean_recoveries'):
        raise ValueError('source must have a clean paused checkpoint')
    cutoff = review['fault_start_unix']
    if review['proof_cutoff_unix'] > cutoff:
        raise ValueError('proof checkpoint is after the fault boundary')
    state, interpolation = state_at_cutoff(ledger.rows,cutoff)
    prefix = [r for r in ledger.rows if r['time_unix'] <= cutoff]
    retained_uncertainty = [r['event_id'] for r in prefix if r['kind']=='generation_clock_untrusted']
    if review.get('reviewed_retained_uncertainty_ids') != retained_uncertainty:
        raise ValueError('retained historical clock uncertainty must be explicitly reviewed')
    # The plan allocation and GREEN set must match the actual verified fork;
    # subsequent baseline registration cannot silently import excluded results.
    allocation = [r for r in prefix if r['kind']=='time_budget_registered'][-1]['data']
    green = set(allocation.get('baseline_green',[])) | {
        r['data']['contract_id'] for r in prefix if r['kind']=='time_budget_green'}
    selected_green = set()
    for theory,selection in checkpoint['theories'].items():
        paths = sorted((source/'theories'/theory/'acceptance/commits').glob('*.json'))
        selected = next((read_artifact(p) for p in paths
                         if read_artifact(p)['commit_id']==selection['commit_id']),None)
        if selected is None:
            raise ValueError('selected proof commit absent')
        selected_green.update(set(selected['state']['proofs']) & set(allocation['contract_owners']))
    if selected_green != green:
        raise ValueError('historical proof GREEN set differs from cutoff clock')
    out = Ledger(target/'effective-generation-budget',new_run['run_id'])
    if out.rows:
        raise ValueError('target budget already exists')
    out.append('parallel_budget_binding',**{**bindings[0]['data'],'run_sha256':digest(new_run)})
    out.append('parallel_budget_continuation_started',source_run=str(source),
        source_tail_event_id=ledger.rows[-1]['event_id'],authorization=authorization,
        review=review,kind_of_continuation='historical_branch')
    try:
        for row in prefix:
            if row['kind']=='parallel_budget_binding' or row['kind'].startswith('parallel_budget_continuation_'):
                continue
            out.clock=lambda row=row:row['time_unix']
            out.append(row['kind'],**row['data'])
        out.clock=lambda:cutoff
        out.append(CHECKPOINT_KIND,boundary='authorized_historical_branch',state=state)
    finally:
        out.clock=time.time
    receipt={'source_run':str(source),'source_tail_event_id':ledger.rows[-1]['event_id'],
        'authorization':authorization,'review':review,'interpolation':interpolation,
        'carried_effective_seconds':state['effective_seconds'],
        'carried_worker_seconds':state['worker_seconds'], 'total_limit_seconds':state['seconds'],
        'excluded_branch_effective_seconds':last['effective_seconds']-state['effective_seconds'],
        'original_observed_effective_seconds':last['effective_seconds'],
        'original_accounting_unchanged':True,'green_contract_ids':sorted(green),
        'offline_until_generation_restart':'closed gate; no model context or later results inherited'}
    out.append('parallel_budget_continuation_completed',**receipt)
    return receipt
