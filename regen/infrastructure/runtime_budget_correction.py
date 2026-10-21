"""Audited infrastructure-time correction in a NEW, not-yet-launched continuation.

Original events and costs remain immutable. One atomic checkpoint contains both
the correction receipt and adjusted state, so a crash cannot apply half a refund.
Only time known to be blocked is excluded; tokens and accepted proofs are kept.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from regen.execution.proof_commits import digest
from regen.infrastructure.effective_time_budget import CHECKPOINT_KIND, POLICY
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.incremental_log import read_artifact

CORRECTION_BOUNDARY = 'audited_runtime_infrastructure_correction'
ERROR_TEXT = 'workspace launch does not match the trusted Codex executable'


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def interval_correction(rows, intervals):
    """Integrate charged deltas, excluding a lane only within its reviewed fault.

Every gate transition has a checkpoint. Within one constant-rate bracket, split
at fault boundaries and interpolate monotonic charged counters using wall receipt
timestamps. Reject clock drift/ambiguous brackets rather than invent precision.
Global refund occurs only when ALL charged lanes in a subinterval are faulty.
"""
    checkpoints = [r for r in rows if r['kind'] == CHECKPOINT_KIND]
    if len(checkpoints) < 2 or not intervals:
        raise ValueError('checkpoint history and fault intervals required')
    allocation = next((r['data'] for r in rows if r['kind'] == 'time_budget_registered'), None)
    if allocation is None:
        raise ValueError('missing registered theory allocation')
    names = set(allocation.get('theory_ids', allocation['contract_owners'].values()))
    for item in intervals:
        if (item.get('theory') not in names or not _finite(item.get('start_unix'))
                or not _finite(item.get('end_unix')) or item['end_unix'] <= item['start_unix']
                or item['start_unix'] < checkpoints[0]['time_unix']
                or item['end_unix'] > checkpoints[-1]['time_unix']):
            raise ValueError('invalid or unbracketed fault interval')
    earliest = min(v['start_unix'] for v in intervals)
    latest = max(v['end_unix'] for v in intervals)
    if any(r['kind'] == 'generation_clock_untrusted' and earliest <= r['time_unix'] <= latest
           for r in rows):
        raise ValueError('fault interval contains untrusted clock evidence')
    if any(r['kind'] == 'time_budget_green' and any(
            v['theory'] == r['data']['theory'] and v['start_unix'] <= r['time_unix'] <= v['end_unix']
            for v in intervals) for r in rows):
        raise ValueError('accepted contract inside claimed unavailable interval')
    refund = {t: 0. for t in sorted({v['theory'] for v in intervals})}
    union = 0.; brackets = []; max_residual = 0.
    for left, right in zip(checkpoints, checkpoints[1:]):
        begin, end = left['time_unix'], right['time_unix']
        if end <= earliest or begin >= latest:
            continue
        if not end > begin:
            raise ValueError('nonmonotonic correction bracket')
        a, b = left['data']['state'], right['data']['state']
        span = end-begin
        theories = set(a.get('theory_seconds', {})) | set(b.get('theory_seconds', {}))
        delta = {t: b['theory_seconds'].get(t, 0.)-a['theory_seconds'].get(t, 0.) for t in theories}
        du, dw = b['effective_seconds']-a['effective_seconds'], b['worker_seconds']-a['worker_seconds']
        if any(v < -1e-6 for v in [du, dw, *delta.values()]):
            raise ValueError('correction overlaps an earlier adjustment or decreasing clock')
        if abs(dw-sum(delta.values())) > .001:
            raise ValueError('worker/theory accounting mismatch')
        if du == 0 and dw == 0:
            continue  # Offline/excluded time was never charged, never refund it.
        if span > 120:
            raise ValueError('charged correction bracket exceeds 120 seconds')
        rates = {t: round(v/span) for t, v in delta.items()}
        residual = max([abs(du-round(du/span)*span),
                        *[abs(delta[t]-rates[t]*span) for t in delta]])
        max_residual = max(max_residual, residual)
        if (residual > .1 or any(v not in (0, 1) for v in rates.values())
                or round(du/span) != int(any(rates.values()))):
            raise ValueError('inconsistent constant-gate rates or wall/monotonic drift')
        charged = {t for t, rate in rates.items() if rate}
        cuts = sorted({begin, end, *[max(begin, min(end, v[k]))
            for v in intervals for k in ('start_unix', 'end_unix')]})
        bracket_refund = 0.
        for lo, hi in zip(cuts, cuts[1:]):
            middle = (lo+hi)/2
            failed = {v['theory'] for v in intervals if v['start_unix'] <= middle < v['end_unix']}
            ratio = (hi-lo)/span
            for theory in charged & failed:
                refund[theory] += delta[theory]*ratio
            if charged and charged <= failed:
                amount = du*ratio; union += amount; bracket_refund += amount
        if bracket_refund or any(t in refund and delta[t] for t in charged):
            brackets.append({'left_event_id': left['event_id'], 'right_event_id': right['event_id'],
                             'union_refund_seconds': bracket_refund})
    return {'effective_seconds': union, 'worker_seconds': sum(refund.values()),
            'theory_seconds': refund, 'brackets': brackets,
            'method': 'constant-gate interpolation of monotonic deltas; per-lane exclusion and union recomputation',
            'max_wall_monotonic_residual_seconds': max_residual, 'not_subsecond_exact': True}


def _failure_evidence(source):
    """Only trusted local callback errors, never a model merely quoting an error."""
    first = {}; successes = []; productive_actions = []
    for path in sorted((source/'parallel-sessions').glob('**/session-transcript.jsonl')):
        relative = path.relative_to(source)
        theory = relative.parts[2]
        calls = {}; starts = {}; sha = hashlib.sha256(); failures = []
        with path.open('rb') as handle:
            for line_number, line in enumerate(handle, 1):
                sha.update(line)
                if not line.endswith(b'\n'):
                    raise ValueError('incomplete transcript; source must be stopped')
                row = json.loads(line); message = row.get('message', {})
                raw = message.get('raw_json')
                if row.get('direction') == 'in' and raw and 'item/tool/call' in raw:
                    request = json.loads(raw)
                    if request.get('method') == 'item/tool/call':
                        p = request['params']; calls[p['callId']] = p
                if row.get('direction') == 'local' and message.get('event') == 'tool_started':
                    starts[message['call_id']] = row['time_unix']
                if row.get('direction') != 'local' or message.get('event') != 'tool_finished':
                    continue
                call = calls.get(message.get('call_id'))
                response = message.get('response', {})
                if not call:
                    continue
                action = (call.get('arguments') or {}).get('proposal', {}).get('action')
                if call.get('tool') == 'proof_action' and (call.get('arguments') or {}).get('theory') != theory:
                    raise ValueError('proof callback ownership differs from transcript lane')
                errors = []
                for content in response.get('contentItems', []):
                    try:
                        error = json.loads(content.get('text', ''))
                    except (ValueError, TypeError):
                        continue
                    if isinstance(error, dict):
                        errors.append(error)
                    if (call.get('tool') == 'proof_action' and isinstance(error, dict)
                            and error.get('error_type') in ('ValueError', 'CodexRuntimeError')
                            and error.get('message') == ERROR_TEXT):
                        failures.append({'theory': theory, 'start_unix': row['time_unix'],
                            'transcript': str(relative), 'line_number': line_number,
                            'call_id': message['call_id'], 'receipt_sha256': digest(row),
                            'error_type': error['error_type'], 'reason': ERROR_TEXT})
                if response.get('success') is True:
                    business_fault = any(e.get('status') == 'error' and e.get('diagnostic') == ERROR_TEXT for e in errors)
                    if business_fault:
                        continue  # RPC success is not successful proof checking.
                    if call.get('tool') == 'proof_action' and action != 'query':
                        productive_actions.append((theory, row['time_unix']))
                    else:
                        if message['call_id'] not in starts:
                            raise ValueError('successful callback lacks trusted start receipt')
                        successes.append({'theory': theory, 'start_unix': starts[message['call_id']],
                            'end_unix': row['time_unix'], 'call_id': message['call_id'],
                            'tool': call.get('tool'), 'action': action,
                            'transcript': str(relative), 'line_number': line_number,
                            'receipt_sha256': digest(row)})
        if failures:
            earliest = min(failures, key=lambda r: r['start_unix'])
            earliest.update(transcript_sha256=sha.hexdigest(), observed_error_count=len(failures))
            if theory not in first or earliest['start_unix'] < first[theory]['start_unix']:
                first[theory] = earliest
    if not first:
        raise ValueError('no trusted executable-integrity failure receipts')
    if any(t in first and when >= first[t]['start_unix'] for t, when in productive_actions):
        raise ValueError('successful proof action after fault; cannot assume a terminal outage')
    retained = [v for v in successes if v['theory'] in first and v['end_unix'] > first[v['theory']]['start_unix']]
    return [first[t] for t in sorted(first)], retained


def plan_terminal_runtime_correction(source_dir):
    """Read-only proposal. Caller establishes stopped liveness and owns source lock."""
    source = Path(source_dir).resolve(); run = read_artifact(source/'run.json')
    ledger = Ledger(source/'effective-generation-budget', run['run_id'])
    checkpoints = [r for r in ledger.rows if r['kind'] == CHECKPOINT_KIND]
    if not checkpoints:
        raise ValueError('source has no clock checkpoint')
    last = checkpoints[-1]; state = last['data']['state']
    if (state['policy'] != POLICY or not state['paused'] or state['gate_open']
            or state['finished'] or state.get('unclean_recoveries')):
        raise ValueError('source requires clean paused clock')
    failures, retained = _failure_evidence(source)
    intervals = []
    for failure in failures:
        start = failure['start_unix']; end = last['time_unix']
        for good in sorted((v for v in retained if v['theory'] == failure['theory']), key=lambda v: v['start_unix']):
            if good['start_unix'] > start:
                intervals.append({'theory': failure['theory'], 'start_unix': start, 'end_unix': min(end, good['start_unix'])})
            start = max(start, good['end_unix'])
        if start < end:
            intervals.append({'theory': failure['theory'], 'start_unix': start, 'end_unix': end})
    # Reject even a differently named second refund of the same fault interval.
    previous = [r['data'].get('correction') for r in checkpoints
                if r['data'].get('boundary') == CORRECTION_BOUNDARY]
    if any(p and any(a['theory'] == b['theory'] and
            max(a['start_unix'], b['start_unix']) < min(a['end_unix'], b['end_unix'])
            for a in p['intervals'] for b in intervals) for p in previous):
        raise ValueError('fault interval already corrected in ancestor history')
    adjustment = interval_correction(ledger.rows, intervals)
    corrected = deepcopy(state)
    for key in ('effective_seconds', 'worker_seconds'):
        corrected[key] -= adjustment[key]
    for theory, amount in adjustment['theory_seconds'].items():
        corrected['theory_seconds'][theory] -= amount
    if any(v < 0 for v in [corrected['effective_seconds'], corrected['worker_seconds'],
                          *corrected['theory_seconds'].values()]):
        raise ValueError('correction exceeds charged history')
    proposal = {'policy': 'audited-runtime-failure-correction-v1', 'source_run': str(source),
        'source_run_sha256': digest(run), 'source_tail_event_id': ledger.rows[-1]['event_id'],
        'source_checkpoint_event_id': last['event_id'], 'intervals': intervals,
        'first_failure_receipts': failures, 'retained_successful_tool_intervals': retained,
        'adjustment': adjustment, 'original_state': state, 'corrected_state': corrected,
        'preserve_tokens_and_accepted_proofs': True,
        'boundary_policy': 'first trusted failure receipt, not tool start or guessed executable deletion time'}
    proposal['correction_id'] = digest(proposal)
    return proposal


def apply_runtime_correction(source_dir, destination, *, authorization, proposal):
    """Append once after carry_parallel_budget, before target service creation.

Caller holds mutation locks and proves source stopped. Recomputes the proposal
from source evidence. Source is NEVER modified. Exact repeats are idempotent.
"""
    if not isinstance(authorization, str) or not authorization.strip():
        raise ValueError('explicit accounting correction authorization required')
    source, target = Path(source_dir).resolve(), Path(destination).resolve()
    if source == target:
        raise ValueError('correction requires a distinct unlaunched continuation')
    if (target/'service-launch.json').exists():
        raise ValueError('target has already been launched')
    current = plan_terminal_runtime_correction(source)
    if current != proposal:
        raise ValueError('correction proposal or source evidence changed')
    target_run = read_artifact(target/'run.json')
    origin = read_artifact(target/'workspace-continuation.json')
    if Path(origin['source_run']).resolve() != source or (target/'checkpoint.json').exists():
        raise ValueError('correction requires latest full-workspace continuation')
    out = Ledger(target/'effective-generation-budget', target_run['run_id'])
    prior = [r for r in out.rows if r['kind'] == CHECKPOINT_KIND
             and r['data'].get('boundary') == CORRECTION_BOUNDARY
             and r['data'].get('correction', {}).get('correction_id') == proposal['correction_id']]
    if prior:
        if len(prior) != 1 or prior[0]['data']['correction'] != proposal:
            raise ValueError('conflicting correction receipt')
        return prior[0]
    if not out.rows or out.rows[-1]['kind'] != 'parallel_budget_continuation_completed':
        raise ValueError('apply only immediately after a completed budget import')
    receipt = out.rows[-1]['data']
    if (receipt['source_run'] != str(source)
            or receipt['source_tail_event_id'] != proposal['source_tail_event_id']):
        raise ValueError('target import does not bind reviewed source')
    checkpoints = [r for r in out.rows if r['kind'] == CHECKPOINT_KIND]
    if not checkpoints or checkpoints[-1]['data']['state'] != proposal['original_state']:
        raise ValueError('target clock differs from reviewed source clock')
    return out.append(CHECKPOINT_KIND, boundary=CORRECTION_BOUNDARY,
        state=proposal['corrected_state'], correction=proposal, authorization=authorization)
