"""Evidence-producing protocol acceptance checks; no provider or Isabelle calls.

Exit 0 means all controls AND acceptance checks passed, 1 means a measured
acceptance/control failure, and 2 means the validation itself could not finish.
Known production defects are reported as failures, not successful acceptance.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import time

from regen.infrastructure.codex_workspace_session import (
    CodexWorkspaceSession, ProgressWatch, SessionStalled, SLEEP_POLICY_VERSION,
    validate_sleep_event,
)
from regen.infrastructure.workspace_transcript import audit_workspace_session


DEFAULT_TRANSCRIPT = Path(
    'reports/regen/refine-first-run-001/'
    'repository-theory-document-astra-20260929-015/parallel-sessions/'
    'cee033ef71434c8cae3412fe6004919d/Scheduler_Queues/session-transcript.jsonl')
TOOLS = [{'name': 'proof_action', 'description': 'Fixed infrastructure fixture only',
          'inputSchema': {'type': 'object', 'properties': {'action': {'type': 'string'}},
                          'required': ['action'], 'additionalProperties': False}}]

# A real child process with concurrent output during the synchronous callback.
# This is deliberately NOT a Codex process, a model, or an Isabelle checker.
SERVER = r'''
import json, os, sys, threading, time
mode, wait_seconds, trace_path = sys.argv[1], float(sys.argv[2]), sys.argv[3]
trace = open(trace_path, 'x', encoding='utf-8')
lock = threading.Lock()
serial = 0
def record(direction, message):
    trace.write(json.dumps({'time_unix': time.time(), 'direction': direction,
                            'message': message}) + '\n')
    trace.flush()
def send(message):
    global serial
    with lock:
        serial += 1
        message.update(emittedAtMs=int(time.time()*1000), fixtureEventId=str(serial))
        record('out', message)
        print(json.dumps(message), flush=True)
def sleep_event(method, number):
    send({'method': method, 'params': {'threadId': 'fixture-thread',
        'turnId': 'fixture-turn', 'item': {'type': 'sleep',
        'id': 'fixture-sleep-'+str(number), 'durationMs': 40000}}})
def produce():
    try:
        time.sleep(.1)
        if mode == 'sleep_during_callback':
            # Long mode uses actual forty-second sleeps, short mode compresses
            # wall time but leaves the protocol duration unchanged and labels it.
            gap = 40 if wait_seconds >= 165 else (wait_seconds-.3)/4
            for number in range(4):
                sleep_event('item/started', number)
                time.sleep(gap)
                sleep_event('item/completed', number)
        else:
            for number in range(4):
                send({'method': 'item/agentMessage/delta',
                      'params': {'threadId': 'fixture-thread', 'turnId': 'fixture-turn',
                                 'delta': 'callback still running '+str(number)}})
                time.sleep((wait_seconds-.3)/4)
    except (BrokenPipeError, OSError):
        pass
for raw in sys.stdin:
    message = json.loads(raw)
    with lock:
        record('in', message)
    method = message.get('method')
    if method == 'initialize':
        send({'id': message['id'], 'result': {'userAgent': 'protocol-validation-fixture'}})
    elif method == 'thread/start':
        send({'id': message['id'], 'result': {'thread': {'id': 'fixture-thread'}}})
    elif method == 'turn/start':
        send({'id': message['id'], 'result': {'turn': {'id': 'fixture-turn'}}})
        if mode == 'chatter_without_work':
            while True:
                send({'method': 'item/agentMessage/delta', 'params': {'delta': 'no work'}})
                time.sleep(.025)
        send({'id': 100, 'method': 'item/tool/call', 'params': {
            'threadId': 'fixture-thread', 'turnId': 'fixture-turn',
            'callId': 'fixture-call', 'namespace': None, 'tool': 'proof_action',
            'arguments': {'action': 'check_theory'}}})
        threading.Thread(target=produce, daemon=True).start()
    elif 'result' in message:
        send({'method': 'item/agentMessage/delta',
              'params': {'delta': 'fixture consumed tool result'}})
        send({'method': 'turn/completed', 'params': {'threadId': 'fixture-thread',
              'turn': {'id': 'fixture-turn', 'status': 'completed'}}})
'''


def write_json(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write('\n')


def history_events(path):
    """Stream only the small selected protocol events, not proof-state commits."""
    events, digest, size = [], hashlib.sha256(), 0
    with Path(path).open('rb') as stream:
        for line_number, raw in enumerate(stream, 1):
            digest.update(raw)
            size += len(raw)
            event = json.loads(raw)
            message = event['message'].get('parsed', event['message'])
            if message.get('params', {}).get('item', {}).get('type') == 'sleep':
                events.append({'source_line': line_number, 'event': event})
    return events, {'path': str(Path(path).resolve()), 'sha256': digest.hexdigest(),
                    'bytes': size, 'selected_sleep_lifecycle_events': len(events)}


def replay_sleep(events, *, policy_version=1):
    state, rows, owners, starts = {}, [], set(), 0
    for selected in events:
        event = selected['event']
        message = event['message'].get('parsed', event['message'])
        params = message['params']
        owners.add((params['threadId'], params['turnId']))
        starts += message['method'] == 'item/started'
        row = {'source_line': selected['source_line'], 'method': message['method'],
               'item': params['item'], 'received_time_unix': event['time_unix'],
               'emitted_at_ms': message.get('emittedAtMs')}
        try:
            validate_sleep_event(params, message['method'], owners, state,
                                 policy_version=policy_version)
            row['accepted'] = True
        except ValueError as error:
            row.update(accepted=False, error_type=type(error).__name__, diagnostic=str(error))
        rows.append(row)
        if not row['accepted']:
            break
    rejected = [row for row in rows if not row['accepted']]
    return {'acceptance_pass': not rejected, 'policy_version': policy_version,
            'status': 'reproduced' if rejected else 'accepted',
            'rejected_on_sleep_start_number': starts if rejected else None,
            'rows': rows, 'pending_sleep_count': len(state.get('pending', {})),
            'limitation': 'Validator-only historical EVENT PREFIX replay; pending lifecycle '
                          'items may remain. Acceptance here is not whole-session sealability, '
                          'asynchronous I/O, or live model validation.'}


def historical_comparison(events, transcript):
    legacy = replay_sleep(events, policy_version=1)
    current = replay_sleep(events, policy_version=SLEEP_POLICY_VERSION)
    try:
        audit_workspace_session(transcript, require_real=False)
        rejected = {'pass': False, 'diagnostic': 'Historical failed session unexpectedly accepted'}
    except ValueError as error:
        rejected = {'pass': True, 'error_type': type(error).__name__, 'diagnostic': str(error)}
    return {'historical_legacy_reproduced': not legacy['acceptance_pass']
                and legacy['rejected_on_sleep_start_number'] == 4,
            'current_policy_event_prefix_accepted': current['acceptance_pass'],
            'historical_failed_whole_session_rejected': rejected,
            'legacy': legacy, 'current_policy': current}


def controls():
    owner = ('fixture-thread', 'fixture-turn')
    base = {'threadId': owner[0], 'turnId': owner[1],
            'item': {'type': 'sleep', 'id': 'one', 'durationMs': 40000}}
    results = []
    for version, name in ((v, n) for v in (1, SLEEP_POLICY_VERSION)
                         for n in ('valid_pair', 'wrong_owner', 'duplicate_id', 'changed_completion')):
        state, params = {}, deepcopy(base)
        rejected = False
        try:
            if name == 'wrong_owner':
                params['threadId'] = 'wrong-thread'
            validate_sleep_event(params, 'item/started', {owner}, state, policy_version=version)
            if name == 'duplicate_id':
                validate_sleep_event(params, 'item/started', {owner}, state, policy_version=version)
            if name == 'changed_completion':
                params['item']['durationMs'] = 30000
            validate_sleep_event(params, 'item/completed', {owner}, state, policy_version=version)
        except ValueError:
            rejected = True
        results.append({'name': name, 'policy_version': version,
                        'pass': rejected == (name != 'valid_pair')})
    now = [0.0]
    watch = ProgressWatch(900, clock=lambda: now[0])
    now[0] = 899.999
    watch.check()
    now[0] = 900
    try:
        watch.check()
        boundary = False
    except SessionStalled:
        boundary = True
    results.append({'name': '900_second_boundary_mock_clock', 'pass': boundary,
                    'real_wall_clock_900_seconds': False})
    now[0] = 0
    watch = ProgressWatch(900, clock=lambda: now[0])
    now[0] = 950
    watch.observe('proof_action', {'action': 'check_theory'}, {'verdict': 'red'}, 950)
    watch.check()
    results.append({'name': '950_second_callback_excluded_mock_clock',
                    'pass': watch.last_progress == 950 and watch.generation == 1,
                    'real_native_callback': False})
    watch = ProgressWatch(900, clock=lambda: now[0])
    watch.observe('read_theory', {}, {'same': True}, 0)
    for _ in range(7):
        watch.observe('read_theory', {}, {'same': True}, 0)
    try:
        watch.observe('read_theory', {}, {'same': True}, 0)
        repeats_bounded = False
    except SessionStalled:
        repeats_bounded = True
    results.append({'name': 'eighth_repeated_read_stalls_without_proof_work',
                    'pass': repeats_bounded})
    return results


def latency_assessment(lags, limit, *, received_while_callback, required_count):
    """Evaluate externally measured receipt delay, independently of turn success."""
    ordered = sorted(lags)
    return {'events_received': len(lags), 'events_required': required_count,
            'received_while_callback_active': received_while_callback,
            'max_receive_delay_seconds': max(lags) if lags else None,
            'p99_receive_delay_seconds': ordered[max(0, math.ceil(len(ordered)*.99)-1)] if lags else None,
            'percentile_note': 'Empirical nearest-rank order statistic only; '
                               'small fixture samples do not estimate population p99. '
                               'Acceptance uses maximum delay and complete event count.',
            'maximum_allowed_seconds': limit,
            'pass': len(lags) == required_count and received_while_callback == required_count
                    and max(lags, default=float('inf')) <= limit}


def subprocess_case(root, scenario, wait_seconds, latency_limit):
    directory = root / scenario
    directory.mkdir()
    callbacks = []
    error = None
    started = time.monotonic()
    def handler(*args):
        callback = {'started_at_unix': time.time(), 'call_id': args[2]['call_id']}
        callbacks.append(callback)
        time.sleep(wait_seconds)
        callback['finished_at_unix'] = time.time()
        return {'fixture': 'completed', 'real_native_or_model': False}
    session = CodexWorkspaceSession(tools=TOOLS, allow_external_call=True,
        command=[sys.executable, '-u', '-c', SERVER, scenario, str(wait_seconds),
                 str(directory / 'fixture-stdio.jsonl')],
        progress_timeout=.75 if scenario == 'chatter_without_work' else wait_seconds+30)
    try:
        with session:
            session.run('Run fixed protocol fixture.', handler, artifact_dir=directory,
                        model='synthetic-no-provider', timeout=wait_seconds+30)
    except Exception as caught:
        error = {'type': type(caught).__name__, 'message': str(caught)}
    wall = time.monotonic() - started
    write_json(directory / 'callback-timing.json', callbacks)
    result_receipts = 0
    with (directory / 'fixture-stdio.jsonl').open() as stream:
        for raw in stream:
            event = json.loads(raw)
            if (event['direction'] == 'in' and event['message'].get('id') == 100
                    and 'result' in event['message']):
                result_receipts += 1
    received, response_consumed = [], False
    for raw in (directory / 'session-transcript.jsonl').open():
        event = json.loads(raw)
        if event['direction'] != 'in':
            continue
        message = event['message']['parsed']
        params = message.get('params', {})
        if params.get('delta', '').startswith('fixture consumed'):
            response_consumed = True
        if (params.get('item', {}).get('type') == 'sleep'
                or params.get('delta', '').startswith('callback still running')):
            received.append((event['time_unix'], message['emittedAtMs']/1000))
    inside = sum(any(row['started_at_unix'] <= receipt <= row.get('finished_at_unix', 0)
                     for row in callbacks) for receipt, _ in received)
    timing = latency_assessment([receipt-emitted for receipt, emitted in received],
        latency_limit, received_while_callback=inside,
        required_count=8 if scenario == 'sleep_during_callback' else 4)
    try:
        audit_workspace_session(directory / 'session-transcript.jsonl', require_real=False)
        audit = {'pass': True}
    except Exception as caught:
        audit = {'pass': False, 'type': type(caught).__name__, 'message': str(caught)}
    dead = session.process is not None and session.process.poll() is not None
    if scenario == 'chatter_without_work':
        passed = bool(error and error['type'] == 'SessionStalled' and not callbacks and dead)
    else:
        passed = (error is None and len(callbacks) == 1 and timing['pass']
                  and audit['pass'] and response_consumed and result_receipts == 1 and dead)
    result = {'scenario': scenario, 'pass': passed, 'exception': error,
              'wall_seconds': wall, 'callback_count': len(callbacks),
              'child_exited': dead, 'child_returncode': session.process.poll() if session.process else None,
              'synthetic_server_consumed_response_observed': response_consumed,
              'synthetic_server_result_receipt_count': result_receipts,
              'event_latency': timing, 'audit': audit,
              'synthetic_server': True, 'real_subprocess_stdio': True,
              'real_model': False, 'real_isabelle': False,
              'sleep_wall_time_compressed': scenario == 'sleep_during_callback' and wait_seconds < 165,
              'note': 'Consumed-response-observed means client received the fixture acknowledgment; '
                      'result_receipt_count independently measures child stdin receipt, not model consumption.'}
    write_json(directory / 'result.json', result)
    return result


def run(output, transcript, *, wait_seconds=2, latency_limit=.5):
    output, transcript = Path(output).resolve(), Path(transcript).resolve()
    if not math.isfinite(wait_seconds) or wait_seconds < 1 or not 0 < latency_limit < wait_seconds:
        raise ValueError('wait must be at least one second and greater than the positive latency limit')
    # This test only writes a NEW report tree, never inside its source experiment.
    if 'parallel-sessions' in transcript.parts:
        source_run = Path(*transcript.parts[:transcript.parts.index('parallel-sessions')])
        if output.is_relative_to(source_run):
            raise ValueError('output must not be inside the historical source experiment')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'driver-snapshot.py').write_bytes(Path(__file__).read_bytes())
    events, source = history_events(transcript)
    write_json(output / 'historical-sleep-events.json', events)
    write_json(output / 'input-summary.json', {'source': source,
        'production_implementation_sha256': hashlib.sha256(
            Path(sys.modules[CodexWorkspaceSession.__module__].__file__).read_bytes()).hexdigest(),
        'validation_tool_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'wait_seconds': wait_seconds, 'latency_limit_seconds': latency_limit,
        'claims': {'provider_calls': 0, 'isabelle_calls': 0,
                   'production_implementation_modified': False,
                   '900_second_wall_clock_test': False}})
    control_results = controls()
    starts = [e['event']['message']['parsed']['params']['item'] for e in events
              if e['event']['message']['parsed']['method'] == 'item/started']
    control_results.append({'name': 'historical_fixture_four_distinct_40_second_sleeps',
        'pass': len(starts) == 4 and len({s['id'] for s in starts}) == 4
                and all(s['durationMs'] == 40000 for s in starts)})
    historical = historical_comparison(events, transcript)
    write_json(output / 'historical-replay-result.json', historical)
    control_results.extend([
        {'name': 'historical_legacy_failure_reproduced',
         'pass': historical['historical_legacy_reproduced']},
        {'name': 'historical_failed_whole_session_remains_rejected',
         **historical['historical_failed_whole_session_rejected']}])
    cases = [subprocess_case(output, name, wait_seconds, latency_limit)
             for name in ('event_pump', 'sleep_during_callback', 'chatter_without_work')]
    control_results.append({'name': 'real_wall_clock_chatter_stalls_without_work',
                            'pass': cases[-1]['pass']})
    accepted = (historical['current_policy_event_prefix_accepted']
                and all(case['pass'] for case in cases[:2]))
    result = {'schema_version': 2, 'completed_at': datetime.now(timezone.utc).isoformat(),
              'controls_pass': all(row['pass'] for row in control_results),
              'acceptance_pass': accepted, 'controls': control_results,
              'historical_replay': historical, 'subprocess_cases': cases,
              'limitations': ['No production code was repaired by this tool.',
                  'No real Codex app-server, provider/model request, or Isabelle/native process.',
                  '900 second deadline boundary is a mock-clock check, not 900 seconds of elapsed time.',
                  'Cancellation, recovery, duplicate tool call and real-model acceptance need separate tests.']}
    result['exit_code'] = 0 if result['controls_pass'] and accepted else 1
    write_json(output / 'result.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--transcript', type=Path, default=DEFAULT_TRANSCRIPT)
    parser.add_argument('--long-wait-seconds', type=float,
                        help='Real blocking handler duration; >=165 uses four real 40-second child sleeps. NOT Isabelle.')
    parser.add_argument('--event-latency-limit-seconds', type=float, default=.5)
    args = parser.parse_args(argv)
    try:
        result = run(args.output, args.transcript,
                     wait_seconds=args.long_wait_seconds if args.long_wait_seconds is not None else 2,
                     latency_limit=args.event_latency_limit_seconds)
    except Exception as error:
        print(json.dumps({'status': 'validation_error', 'type': type(error).__name__,
                          'message': str(error), 'exit_code': 2}), file=sys.stderr)
        return 2
    print(json.dumps({key: result[key] for key in ('controls_pass', 'acceptance_pass', 'exit_code')}, sort_keys=True))
    return result['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
