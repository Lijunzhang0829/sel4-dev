"""Review known local stops while retaining every charged second and old uncertainty."""
import hashlib
import json
from pathlib import Path


def review_handler_stop(source_dir, event):
    """Bind a legacy open-item fault to the accepted tool result and local stop.

    This authorizes carrying costs, not refunding a possibly overcharged interval
    or declaring historical measurement exact. Local logs remain the authority.
    """
    source_dir = Path(source_dir).resolve()
    evidence = event['data'].get('evidence', {})
    if evidence.get('reason') != 'session_stopped_with_open_model_item':
        raise ValueError('not a handler shutdown clock fault')
    theory = event['data'].get('theory')
    if not isinstance(theory, str) or Path(theory).name != theory:
        raise ValueError('handler clock review requires a theory identity')
    owner = evidence.get('thread_id'), evidence.get('turn_id')
    if not all(isinstance(v, str) and v for v in (*owner, evidence.get('generation_id'))):
        raise ValueError('handler clock review lacks session identity')
    matches = []
    for path in sorted((source_dir/'parallel-sessions').glob('*/'+theory+'/session-transcript.jsonl')):
        raw = path.read_bytes()
        opened, requests = set(), {}
        response = None
        retained = False
        terminal_seen = False
        for line, value in enumerate(raw.splitlines(), 1):
            row = json.loads(value)
            message = row['message']
            if 'parsed' in message:
                if json.loads(message['raw_json']) != message['parsed']:
                    raise ValueError('raw handler transcript differs from parsed message')
                message = message['parsed']
            params = message.get('params', {})
            if (row['direction'] == 'in' and message.get('method') == 'turn/completed'
                    and (params.get('threadId'), params.get('turn', {}).get('id')) == owner):
                terminal_seen = True
                opened.clear()
            if row['direction'] == 'in' and (params.get('threadId'), params.get('turnId')) == owner:
                method, item = message.get('method'), params.get('item', {})
                if method in ('item/started', 'item/completed') and item.get('type') in {'reasoning','agentMessage'}:
                    key = item.get('id')
                    if method == 'item/started': opened.add(key)
                    else: opened.discard(key)
                elif method in ('item/agentMessage/delta','item/reasoning/textDelta','item/reasoning/summaryTextDelta'):
                    opened.add(params.get('itemId'))
                elif method == 'turn/completed':
                    opened.clear()
                elif method == 'item/tool/call':
                    requests[message['id']] = params
                    response = None
            if row['direction'] == 'out' and message.get('id') in requests and 'result' in message:
                result = message['result']
                if result.get('success') is True:
                    texts = [v['text'] for v in result.get('contentItems', []) if v.get('type') == 'inputText']
                    if len(texts) == 1:
                        response = (requests[message['id']], json.loads(texts[0]), line)
            if row['direction'] != 'local' or (message.get('thread_id'), message.get('turn_id')) != owner:
                continue
            if (message.get('event') == 'provider_request_finished'
                    and message.get('generation_id') == evidence['generation_id']
                    and message.get('outcome') == 'retained'
                    and message.get('reason') == 'theory_outcome'):
                retained = True
            if message.get('event') != 'turn_stopped_by_handler':
                continue
            if (message.get('status') != 'stopped_by_handler' or message.get('stop_reason') != 'theory_outcome'
                    or terminal_seen or not retained or response is None
                    or set(evidence.get('item_ids', [])) != opened or not opened or None in opened
                    or not 0 <= row['time_unix'] - event['time_unix'] <= 5):
                raise ValueError('handler shutdown evidence does not match the clock fault')
            request, payload, response_line = response
            outcome = payload.get('theory_outcome', {})
            if (request.get('tool') != 'proof_action'
                    or request.get('arguments', {}).get('theory') != theory
                    or request.get('arguments', {}).get('proposal', {}).get('action') != 'report_unresolved'
                    or payload.get('theory_stop') is not True or outcome.get('status') != 'unknown'
                    or outcome.get('binding', {}).get('theory') != theory):
                raise ValueError('handler shutdown lacks an accepted unresolved tool result')
            matches.append(dict(policy='verified-handler-shutdown-no-refund-v1',
                source_run=str(source_dir), event_id=event['event_id'],
                transcript=str(path.relative_to(source_dir)),
                transcript_sha256=hashlib.sha256(raw).hexdigest(),
                tool_response_line=response_line, handler_stop_line=line,
                thread_id=owner[0], turn_id=owner[1], item_ids=sorted(opened),
                historical_uncertainty_retained=True, refund_seconds=0))
    if len(matches) != 1:
        raise ValueError('handler shutdown requires exactly one bound transcript witness')
    return matches[0]
