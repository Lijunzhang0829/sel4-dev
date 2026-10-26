"""Review a legacy silent tail and reserve one retry in a fresh continuation.

The old runner did not label provider requests. Only complete, already charged
checkpoint brackets inside a verified terminal silent interval are imported.
No cost changes at import: the ordinary request clock can exclude these segments
only after a later complete response from the bound replacement request.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from regen.execution.proof_commits import digest, durable
from regen.infrastructure.effective_time_budget import CHECKPOINT_KIND, POLICY
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.incremental_log import read_artifact
from regen.infrastructure.provider_request_accounting import ProviderRequestAccounting


REVIEW_POLICY = 'legacy-terminal-silent-request-review-v1'
IMPORT_BOUNDARY = 'legacy_provider_feedback_pending_import'
RETRY_POLICY = 'provider-feedback-retry-v1'
_METADATA = {'thread/tokenUsage/updated', 'account/rateLimits/updated',
             'turn/moderationMetadata'}


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _terminal_request(source, path):
    relative = path.relative_to(source)
    if len(relative.parts) != 4 or relative.parts[0] != 'parallel-sessions':
        raise ValueError('legacy import requires one original attempt per theory')
    theory = relative.parts[2]
    attempt = read_artifact(path.parent/'attempt.json')
    monitor = read_artifact(path.parent/'runner-status.json')
    if (attempt.get('attempt') != 1 or attempt.get('theory') != theory
            or monitor.get('running') is not False or not monitor.get('stopped_at')
            or monitor.get('theory') != theory):
        raise ValueError('legacy session lacks verified stopped first-attempt receipts')
    calls = {}; finished = {}; tail = []; candidate = None; failure = None; closed = None
    digestor = hashlib.sha256(); previous = -math.inf
    with path.open('rb') as stream:
        for number, line in enumerate(stream, 1):
            if not line.endswith(b'\n'):
                raise ValueError('incomplete legacy provider transcript')
            digestor.update(line)
            row = json.loads(line); timestamp = row.get('time_unix')
            if not _finite(timestamp) or timestamp < previous:
                raise ValueError('legacy provider transcript has nonmonotonic timestamps')
            previous = timestamp
            message = row.get('message', {}); direction = row.get('direction')
            packet = json.loads(message['raw_json']) if direction == 'in' and 'raw_json' in message else message
            if direction == 'in' and packet.get('method') == 'item/tool/call':
                calls[packet['id']] = packet['params']
            if direction == 'local' and message.get('event') == 'tool_finished':
                finished[message['call_id']] = message['response']
            if direction == 'out' and 'result' in message and message.get('id') in calls:
                call = calls[message['id']]
                candidate = dict(theory=theory, start_unix=timestamp,
                    transcript=str(relative), start_line=number, start_receipt_sha256=digest(row),
                    call_id=call['callId'], thread_id=call['threadId'], turn_id=call['turnId'],
                    _valid=(finished.get(call['callId']) == message['result']
                        and call.get('tool') == 'proof_action'
                        and call.get('arguments', {}).get('theory') == theory))
                tail = []
            elif candidate is not None:
                tail.append((number, row, packet))
            if direction == 'local' and message.get('event') == 'session_failed':
                if failure is not None:
                    raise ValueError('multiple failures require a separate legacy review')
                failure = (number, row)
            if direction == 'local' and message.get('event') == 'session_closed':
                closed = (number, row)
    if candidate is None or failure is None or closed is None:
        raise ValueError('missing terminal request, failure or close receipt')
    if not candidate.pop('_valid'):
        raise ValueError('terminal feedback does not match its trusted tool completion')
    number, row = failure
    message = row['message']; kind = message.get('exception_type')
    if (kind not in {'SessionStalled', 'SessionCancelled'} or not message.get('nonsealable')
            or closed[0] <= number or closed[1]['message'].get('nonsealable') is not True
            or candidate['start_line'] >= number or row['time_unix']-candidate['start_unix'] < 60):
        raise ValueError('legacy request is not a terminal stalled/cancelled silent interval')
    expected = ('no new tool information or proof action within progress deadline'
                if kind == 'SessionStalled' else 'workspace coordinator cancelled')
    if message.get('message') != expected:
        raise ValueError('unrecognized legacy session failure')
    for line_number, after, packet in tail:
        if line_number >= number:
            if (after.get('direction') != 'local'
                    or after['message'].get('event') not in {'session_failed', 'session_closed'}):
                raise ValueError('provider activity continued after legacy failure')
            continue
        method = packet.get('method')
        acknowledgment = (method == 'item/completed'
            and packet.get('params', {}).get('item', {}).get('type') == 'dynamicToolCall'
            and packet['params']['item'].get('id') == candidate['call_id'])
        if after.get('direction') != 'in' or not (method in _METADATA or acknowledgment):
            raise ValueError('model or tool feedback exists inside claimed silent interval')
        params = packet.get('params', {})
        for key, expected_id in [('threadId', candidate['thread_id']), ('turnId', candidate['turn_id'])]:
            if key in params and params[key] != expected_id:
                raise ValueError('legacy terminal feedback changed thread identity')
    candidate.update(end_unix=row['time_unix'], failure_line=number,
        failure_receipt_sha256=digest(row), exception_type=kind, failure_message=expected,
        close_line=closed[0], close_receipt_sha256=digest(closed[1]),
        transcript_sha256=digestor.hexdigest(),
        attempt_sha256=_sha(path.parent/'attempt.json'), monitor_sha256=_sha(path.parent/'runner-status.json'))
    candidate['request_id'] = 'legacy-request:'+digest(candidate)
    return candidate


def _segments(rows, failures):
    checkpoints = [r for r in rows if r['kind'] == CHECKPOINT_KIND]
    requests = {r['theory']: r for r in failures}
    untrusted = [r['time_unix'] for r in rows if r['kind'] == 'generation_clock_untrusted']
    segments = []; brackets = []; maximum_residual = 0.
    for left, right in zip(checkpoints, checkpoints[1:]):
        lo, hi = left['time_unix'], right['time_unix']
        selected = {t: r for t, r in requests.items() if r['start_unix'] <= lo < hi <= r['end_unix']}
        if not selected:
            continue
        # Shutdown uncertainty is reviewed to admit a continuation; it never
        # supplies refundable time or justifies extrapolating past clean evidence.
        if any(lo <= timestamp <= hi for timestamp in untrusted):
            continue
        a, b = left['data']['state'], right['data']['state']
        names = set(a.get('theory_seconds', {})) | set(b.get('theory_seconds', {}))
        delta = {t: b['theory_seconds'].get(t, 0.)-a['theory_seconds'].get(t, 0.) for t in names}
        union = b['effective_seconds']-a['effective_seconds']
        worker = b['worker_seconds']-a['worker_seconds']
        if any(v < -1e-7 for v in [union, worker, *delta.values()]):
            raise ValueError('legacy import overlaps corrected or decreasing accounting')
        if abs(worker-sum(delta.values())) > 1e-5:
            raise ValueError('legacy worker and theory counters disagree')
        charged = {t: v for t, v in delta.items() if v > 1e-9}
        if not charged or not set(charged) & set(selected):
            continue
        if (hi-lo > 120 or a.get('planning_active') or b.get('planning_active')
                or a.get('planning_seconds', 0) != b.get('planning_seconds', 0)):
            raise ValueError('ambiguous charged legacy checkpoint bracket')
        residual = max(abs(union-(hi-lo)), *(abs(v-(hi-lo)) for v in charged.values()))
        maximum_residual = max(maximum_residual, residual)
        if residual > .1:
            raise ValueError('legacy charged bracket has ambiguous wall/monotonic drift')
        if any(r['kind'] == 'time_budget_green' and r['data'].get('theory') in selected
                and lo <= r['time_unix'] <= hi for r in rows):
            raise ValueError('accepted work exists inside claimed silent interval')
        seconds = min(union, *charged.values())
        if seconds <= 0:
            continue
        contributors = [dict(worker_id=t, theory=t,
            request_id=selected[t]['request_id'] if t in selected else None) for t in sorted(charged)]
        segments.append(dict(seconds=seconds, planning=False, contributors=contributors))
        brackets.append(dict(left_event_id=left['event_id'], right_event_id=right['event_id'],
            start_unix=lo, end_unix=hi, seconds=seconds,
            pending_theories=sorted(set(charged) & set(selected))))
    if not segments:
        raise ValueError('no complete charged checkpoint bracket inside reviewed silent intervals')
    interval_summary = {r['theory']: {
        'silent_wall_interval_seconds':r['end_unix']-r['start_unix'],
        'imported_charged_seconds':sum(s['seconds'] for s in segments if any(
            c['request_id']==r['request_id'] for c in s['contributors']))} for r in failures}
    for summary in interval_summary.values():
        summary['unimported_wall_difference_seconds'] = (
            summary['silent_wall_interval_seconds']-summary['imported_charged_seconds'])
    return segments, dict(method='whole checkpoint brackets; minimum charged lane/global delta; no interpolation',
        partial_boundary_brackets_retained=True, shutdown_uncertainty_brackets_retained=True,
        maximum_wall_monotonic_residual_seconds=maximum_residual, not_subsecond_exact=True,
        interval_summary=interval_summary, brackets=brackets)


def plan_legacy_feedback_import(source_dir):
    """Read-only proposal; caller establishes stopped service/native liveness."""
    source = Path(source_dir).resolve(); run = read_artifact(source/'run.json')
    ledger = Ledger(source/'effective-generation-budget', run['run_id'])
    if not ledger.rows or ledger.rows[-1]['kind'] != CHECKPOINT_KIND:
        raise ValueError('legacy source must end at a clean clock checkpoint')
    last = ledger.rows[-1]; state = last['data']['state']
    if (state.get('policy') != POLICY or state.get('paused') is not True
            or state.get('gate_open') is not False or state.get('finished') is not False
            or state.get('unclean_recoveries', 0) or state.get('planning_active')
            or state.get('reason') != 'effective_clock_evidence_error'
            or state.get('provider_request_accounting') is not None):
        raise ValueError('legacy import requires an unconverted clean paused feedback failure')
    paths = sorted((source/'parallel-sessions').glob('**/session-transcript.jsonl'))
    if not paths or len({p.relative_to(source).parts[1] for p in paths}) != 1:
        raise ValueError('legacy review requires one terminal coordinator cohort')
    failures = [_terminal_request(source, path) for path in paths]
    if len({r['theory'] for r in failures}) != len(failures):
        raise ValueError('legacy review requires distinct theory lanes')
    primary = [r for r in failures if r['exception_type'] == 'SessionStalled']
    if len(primary) != 1 or any(abs(r['end_unix']-primary[0]['end_unix']) > 5 for r in failures):
        raise ValueError('legacy cancellation lacks a matching stalled coordinator peer')
    uncertain = [r for r in ledger.rows if r['kind'] == 'generation_clock_untrusted']
    if not uncertain:
        raise ValueError('missing legacy shutdown uncertainty receipt')
    by_theory = {r['theory']: r for r in failures}
    for row in uncertain:
        data = row['data']; evidence = data.get('evidence', {}); failure = by_theory.get(data.get('theory'))
        if (failure is None or evidence.get('reason') != 'session_stopped_with_open_model_item'
                or evidence.get('thread_id') != failure['thread_id']
                or evidence.get('turn_id') != failure['turn_id']
                or abs(row['time_unix']-failure['end_unix']) > 5
                or not evidence.get('item_ids')):
            raise ValueError('unsupported legacy clock uncertainty')
    segments, boundary = _segments(ledger.rows, failures)
    requests = [dict(request_id=r['request_id'], worker_id=r['theory'], theory=r['theory'],
        failure={'kind':'legacy_no_feedback', 'policy':REVIEW_POLICY, **r}) for r in failures]
    proposal = dict(policy=REVIEW_POLICY, source_run=str(source), source_run_sha256=digest(run),
        source_tail_event_id=last['event_id'], source_rows_sha256=digest(ledger.rows),
        source_configuration_sha256=digest(read_artifact(source/'parallel-workspace-policy.json')),
        source_state_sha256=digest(state), requests=requests, segments=segments, boundary_review=boundary,
        clock_review=dict(source_tail_event_id=last['event_id'],
            resolved_event_ids=[r['event_id'] for r in uncertain],
            disposition='retain_all_charged_time_no_step_refund'),
        refund_seconds=0, charge_failed_requests_until_success=True,
        preserve_tokens_proofs_drafts_and_planning=True)
    proposal['review_id'] = digest(proposal)
    return proposal


def append_legacy_feedback_import(source_dir, destination, *, authorization, proposal):
    """Called after full budget carry, before service creation, under source lock."""
    source, target = Path(source_dir).resolve(), Path(destination).resolve()
    if not authorization or (target/'service-launch.json').exists():
        raise ValueError('legacy pending import requires authorized unlaunched continuation')
    if proposal != plan_legacy_feedback_import(source):
        raise ValueError('legacy feedback proposal or source evidence changed')
    origin = read_artifact(target/'workspace-continuation.json')
    if Path(origin['source_run']).resolve() != source or (target/'checkpoint.json').exists():
        raise ValueError('legacy pending import requires latest full-workspace continuation')
    ledger = Ledger(target/'effective-generation-budget', read_artifact(target/'run.json')['run_id'])
    if (not any(r['kind']=='parallel_budget_continuation_completed' for r in ledger.rows)
            or any(r['kind']=='provider_feedback_retry_scheduled' for r in ledger.rows)):
        raise ValueError('legacy pending import requires untouched complete budget carry')
    checkpoint = [r for r in ledger.rows if r['kind']==CHECKPOINT_KIND][-1]
    state = deepcopy(checkpoint['data']['state'])
    if digest(state) != proposal['source_state_sha256']:
        raise ValueError('carried clock changed before legacy pending import')
    accounting = ProviderRequestAccounting(state['effective_seconds'], state['worker_seconds'], state['theory_seconds'])
    accounting.import_failed_requests(proposal['requests'], proposal['segments'])
    state['provider_request_accounting'] = accounting.state()
    state.update(accounting.summary())
    receipt = dict(**proposal, authorization=authorization, target_run=str(target),
        target_run_id=ledger.run_id, original_checkpoint_event_id=checkpoint['event_id'])
    durable(target/'provider-feedback-continuation.json', receipt, immutable=True)
    ledger.append(CHECKPOINT_KIND, boundary=IMPORT_BOUNDARY, state=state,
        review_id=proposal['review_id'], authorization=authorization, refund_seconds=0)
    for request in proposal['requests']:
        ledger.append('provider_feedback_retry_scheduled', theory=request['theory'],
            failed_request_id=request['request_id'], failure=request['failure'],
            retry_policy=RETRY_POLICY, retry_number=1, next_attempt=1,
            prior_attempt_directory=str(source/Path(request['failure']['transcript']).parent),
            accepted_state_preserved=True, replay_executed_tools=False,
            legacy_import_review_id=proposal['review_id'])
    return receipt
