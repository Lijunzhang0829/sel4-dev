"""Read-only P0 audit of model/tool interactions; never changes proof state.

Usage: python -m regen.tools.audit_interaction_cost TRANSCRIPT... --output REPORT
Counts usage updates, not HTTP requests. Waiting attribution is temporal only.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from regen.infrastructure.codex_workspace_session import tool_result_confirmed


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def content(response):
    return ''.join(item.get('text', '') for item in response.get('contentItems', [])
                   if item.get('type') == 'inputText')


def audit_bytes(raw):
    calls, rpc_keys, sent, completions = {}, {}, {}, {}
    tools, actions, counts, usage, wait_usage = (Counter() for _ in range(5))
    last_usage, invalid_usage_threads = {}, set()
    usage_events, waits, problems = [], [], []
    pending, last_wait = {}, {}
    payloads = defaultdict(lambda: {'calls': 0, 'bytes': 0, 'text_characters': 0,
                                   'top_level_bytes': Counter()})
    lines = raw.splitlines(keepends=True)
    for number, line in enumerate(lines, 1):
        try:
            row = json.loads(line)
        except ValueError:
            # A reader may observe an unfinished final write, but not corrupt history.
            if not line.endswith(b'\n') and number == len(lines):
                counts['partial_tail_lines'] += 1
                continue
            raise ValueError(f'invalid transcript JSON at line {number}')
        message = row.get('message', {})
        message = message.get('parsed', message)
        params = message.get('params', {})
        method, event = message.get('method'), message.get('event')
        stamp = row.get('time_unix')
        direction = row.get('direction')
        if method == 'item/tool/call':
            key = (params['threadId'], params['turnId'], params['callId'])
            if key in calls:
                counts['duplicate_tool_requests'] += 1
                continue
            calls[key] = {'tool': params['tool'], 'arguments': params.get('arguments', {}),
                          'request_time': stamp, 'line': number}
            pending[params['callId']] = key
            rpc_keys[message.get('id')] = key
            tools[params['tool']] += 1
            proposal = params.get('arguments', {}).get('proposal', {})
            if proposal.get('action'):
                actions[proposal['action']] += 1
        if method == 'thread/tokenUsage/updated':
            thread = params.get('threadId', 'unspecified')
            total = params.get('tokenUsage', {}).get('total', {})
            if not total:
                continue
            previous = last_usage.get(thread, {})
            if total == previous:
                counts['duplicate_usage_updates'] += 1
                continue
            delta = {k: v-previous.get(k, 0) for k, v in total.items()
                     if isinstance(v, (int, float)) and not isinstance(v, bool)}
            if any(v < 0 for v in delta.values()):
                invalid_usage_threads.add(thread)
                counts['nonmonotonic_usage_updates'] += 1
            counts['unique_usage_updates'] += 1
            last_usage[thread] = total
            usage_events.append((stamp, thread, delta))
        if event == 'tool_started' and message.get('call_id') in pending:
            calls[pending[message['call_id']]]['start_time'] = stamp
        if event == 'tool_finished' and message.get('call_id') in pending:
            key = pending[message['call_id']]
            call = calls[key]
            response = message.get('response', {})
            call['end_time'] = stamp
            call['response_sha256'] = hashlib.sha256(encoded(response)).hexdigest()
            text = content(response)
            size = payloads[call['tool']]
            size['calls'] += 1
            size['bytes'] += len(encoded(response))
            size['text_characters'] += len(text)
            try:
                data = json.loads(text)
            except ValueError:
                data = None
            if isinstance(data, dict):
                call['reported_task_ids'] = [t['task_id'] for t in data.get('tasks', [])
                                             if isinstance(t, dict) and 'task_id' in t]
                for field, value in data.items():
                    size['top_level_bytes'][field] += len(encoded(value))
            if call['tool'] == 'wait_checks':
                start = call.get('start_time', call['request_time'])
                if start is not None and stamp is not None:
                    waits.append((start, stamp, key[0]))
                ids = tuple(call['arguments'].get('task_ids', []))
                identity = (key[0], ids)
                serialized = encoded(response)
                if last_wait.get(identity) == serialized:
                    counts['identical_wait_replies'] += 1
                last_wait[identity] = serialized
                if isinstance(data, dict):
                    for task in data.get('tasks', []):
                        counts['wait_task_status:'+str(task.get('status'))] += 1
        if direction == 'out' and 'result' in message and message.get('id') in rpc_keys:
            sent[rpc_keys[message['id']]] = (stamp, message['result'], number)
        if method == 'item/completed' and params.get('item', {}).get('type') == 'dynamicToolCall':
            item = params['item']
            key = (params.get('threadId'), params.get('turnId'), item.get('id'))
            completions.setdefault(key, []).append((stamp, params, number))
    for thread, total in last_usage.items():
        if thread not in invalid_usage_threads:
            usage.update({k: v for k, v in total.items() if isinstance(v, (int, float))})
    for stamp, thread, delta in usage_events:
        if thread not in invalid_usage_threads and stamp is not None and any(
                owner == thread and begin+5 < stamp < end-1 for begin, end, owner in waits):
            counts['wait_interior_usage_updates'] += 1
            wait_usage.update(delta)
    for key, (stamp, response, line) in sent.items():
        receipts = completions.get(key, [])
        if not receipts:
            counts['sent_without_completion'] += 1
        for completed_at, params, completion_line in receipts:
            try:
                confirmed = tool_result_confirmed(params, response, owner=key, tool=calls[key]['tool'])
            except ValueError as error:
                item = params['item']
                early = stamp is not None and completed_at is not None and completed_at < stamp
                classification = ('failed_before_response_sent' if early and item.get('status') == 'failed'
                                  else 'result_mismatch')
                problems.append({'owner': list(key), 'tool': calls[key]['tool'],
                    'classification': classification, 'error': str(error),
                    'sent_line': line, 'completion_line': completion_line,
                    'sent_time': stamp, 'completion_time': completed_at,
                    'sent_sha256': hashlib.sha256(encoded(response)).hexdigest(),
                    'sent_success': response.get('success'), 'completion_success': item.get('success'),
                    'completion_status': item.get('status'),
                    'sent_items': len(response.get('contentItems', [])),
                    'completion_items': len(item.get('contentItems', []))})
            else:
                counts['matching_completion_records' if confirmed else 'incomplete_completion_records'] += 1
    call_records = []
    for key, call in calls.items():
        args = call['arguments']
        proposal = args.get('proposal', {})
        call_records.append({'owner': list(key),
            **{k: v for k, v in call.items() if k != 'arguments'},
            'action': proposal.get('action'),
            'entry_ids': proposal.get('entry_ids', [proposal['entry_id']] if 'entry_id' in proposal else []),
            'source_binding': {k: proposal[k] for k in ('expected_hash', 'base_sha256', 'source_sha256')
                               if k in proposal},
            'requested_task_ids': args.get('task_ids', [args['task_id']] if 'task_id' in args else [])})
    return {'schema': 'interaction-cost-audit-v1', 'snapshot_sha256': hashlib.sha256(raw).hexdigest(),
            'snapshot_bytes': len(raw), 'counts': dict(counts), 'tools': dict(tools),
            'actions': dict(actions), 'recorded_usage': dict(usage),
            'usage_excluded_threads': sorted(invalid_usage_threads),
            'wait_interior_recorded_usage': dict(wait_usage),
            'payloads': dict(payloads), 'confirmation_problems': problems, 'call_records': call_records,
            'limitations': ['Usage updates are not HTTP requests or proof attempts.',
                'Cached input and reasoning output are subsets, not additive categories.',
                'Wait attribution excludes first 5s and last 1s; overlap is not proof of wasted work.',
                'Interrupted or active sessions may have unreported usage tails.',
                'Nonmonotonic cumulative usage excludes that thread from token sums.',
                'Payload bytes use canonical JSON, not wire bytes or tokenizer estimates.']}


def audit_baseline_export(raw):
    """Normalize existing official telemetry exports without inventing missing data."""
    export = json.loads(raw)
    sessions = []
    for name, record in export['runs'].items():
        summary = record.get('usage_summary', {}).get('usage', {})
        sessions.append({'run': name, 'response_usage_records': summary.get('reported_responses'),
            'usage_complete': summary.get('usage_complete'),
            'recorded_usage': summary.get('usage'),
            'stream_completion_records': record.get('stream_event_kinds', {}).get('response.completed'),
            'tool_operation_records_by_name': record.get('tool_names'),
            'payload_sizes': None, 'unchanged_wait_replies': None})
    return {'schema': 'official-interaction-export-audit-v1',
            'snapshot_sha256': hashlib.sha256(raw).hexdigest(), 'sessions': sessions,
            'limitations': ['Exported official telemetry, not an independent raw-log replay.',
                'Wrapper and nested tool operations may both be counted; do not sum as model rounds.',
                'Response usage records and app-server cumulative updates are approximate comparison units.',
                'Payload sizes and unchanged polls are unknown here, not zero.',
                'Incomplete usage is a recorded lower bound, not an exact total.']}


def audit_file(path):
    raw = path.read_bytes()
    try:
        document = json.loads(raw)
    except ValueError:
        document = None
    report = (audit_baseline_export(raw) if isinstance(document, dict) and 'runs' in document
              else audit_bytes(raw))
    return {'source': str(path), **report}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('transcripts', type=Path, nargs='+')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() in {p.resolve() for p in args.transcripts}:
        parser.error('output must not overwrite an input transcript')
    reports = [audit_file(path) for path in args.transcripts]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'reports': reports}, ensure_ascii=False, indent=2)+'\n')


if __name__ == '__main__':
    main()
