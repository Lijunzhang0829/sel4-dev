"""Audited dynamic proof actions, distinct from the legacy no-tool provider.

The prompt artifact is a harness context snapshot, not a claim that the snapshot
was a new model request. Actual provider turn inputs are bound by the transcript.
Trust assumes the local harness owns its logs, as with existing call artifacts;
this is provenance/integrity checking, not a provider cryptographic attestation.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from regen.execution.proof_commits import durable
from regen.infrastructure.audited_llm import model_settings

PROVIDER = 'codex-workspace-audited'
ALLOWED_TOOLS = {'list_workspace','read_theory','list_generated','read_generated',
                 'read_attempt','prepare_reuse','proof_action'}
PASSIVE_ITEMS = {'agentMessage','reasoning','userMessage','dynamicToolCall','contextCompaction','plan'}


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _load_prefix(path, offset=None):
    path = Path(path)
    with path.open('rb') as stream:
        raw = stream.read() if offset is None else stream.read(offset)
    if offset is not None and (type(offset) is not int or offset <= 0 or len(raw) != offset):
        raise ValueError('invalid workspace transcript offset')
    if not raw or not raw.endswith(b'\n'):
        raise ValueError('incomplete workspace transcript boundary')
    return raw


def _audit(raw, *, require_terminal, require_real):
    events = [json.loads(line) for line in raw.splitlines()]
    launches = [e['message'] for e in events if e.get('direction') == 'local'
                and e.get('message', {}).get('event') == 'launch']
    if len(launches) != 1:
        raise ValueError('workspace transcript requires exactly one launch')
    launch = launches[0]
    real = launch.get('provider') == 'codex-app-server' and launch.get('synthetic') is False
    if require_real and not real:
        raise ValueError('synthetic or unmarked workspace launch cannot seal')
    if real:
        settings = model_settings()
        command = launch.get('command', [])
        binary = Path(settings['codex_bin']).resolve()
        if (len(command) < 3 or Path(command[0]).resolve() != binary
                or command[1:3] != ['app-server','--stdio']
                or launch.get('executable_sha256') != settings['codex_sha256']
                or not binary.is_file() or _hash(binary.read_bytes()) != settings['codex_sha256']):
            raise ValueError('workspace launch does not match the trusted Codex executable')
    requests, threads, turns, tool_requests = {}, {}, {}, []
    terminal = False
    seen_calls = set()
    pending_tools = set()
    for event in events:
        direction, message = event.get('direction'), event.get('message')
        if direction == 'local':
            if message.get('nonsealable') or message.get('event') in {'session_failed','deadline_expired'}:
                raise ValueError('workspace session contains a failed or unsafe event')
            continue
        if direction == 'in':
            if json.loads(message['raw_json']) != message['parsed']:
                raise ValueError('raw workspace event differs from parsed event')
            message = message['parsed']
        if direction not in {'in','out'} or not isinstance(message, dict):
            raise ValueError('invalid workspace transcript event')
        method, params = message.get('method'), message.get('params', {})
        if direction == 'out' and method is None:
            if message.get('id') not in pending_tools or 'result' not in message:
                raise ValueError('unmatched workspace tool result')
            pending_tools.remove(message['id'])
            continue
        if direction == 'out' and method:
            if method not in {'initialize','initialized','thread/start','turn/start'}:
                raise ValueError('unapproved client method: '+method)
            if 'id' in message:
                if message['id'] in requests:
                    raise ValueError('duplicate client request ID')
                requests[message['id']] = message
            if method == 'thread/start':
                tools = params.get('dynamicTools', [])
                names = [t.get('name') for t in tools]
                if (not names or len(set(names)) != len(names) or not set(names) <= ALLOWED_TOOLS
                        or any(t.get('type') != 'function' for t in tools)):
                    raise ValueError('unapproved dynamic tool registry')
                if params.get('sandbox') != 'read-only' or params.get('approvalPolicy') != 'never':
                    raise ValueError('workspace launch lacks read-only/never policy')
            continue
        if direction == 'in' and 'id' in message and method is None:
            request = requests.get(message['id'])
            if request is None or 'error' in message:
                raise ValueError('unmatched or failed server response')
            if request['method'] == 'thread/start':
                thread_id = message['result']['thread']['id']
                if threads:
                    raise ValueError('workspace session changed thread')
                threads[thread_id] = request['params']
            if request['method'] == 'turn/start':
                thread_id = request['params']['threadId']
                if thread_id not in threads:
                    raise ValueError('turn has unknown thread')
                turns[(thread_id,message['result']['turn']['id'])] = request['params']
                terminal = False
            continue
        if direction == 'in' and 'id' in message and method:
            if method != 'item/tool/call':
                raise ValueError('unapproved server request: '+method)
            key = (params.get('threadId'), params.get('turnId'))
            if key not in turns:
                # App-server may queue calls before replying to turn/start. Bind
                # only to a response already present in this durable prefix and
                # an outbound request seen before this tool event.
                for later in events:
                    if later.get('direction') != 'in':
                        continue
                    answer = later['message']['parsed']
                    start = requests.get(answer.get('id'), {})
                    if (start.get('method') == 'turn/start' and 'error' not in answer
                            and (start['params'].get('threadId'),
                                 answer.get('result', {}).get('turn', {}).get('id')) == key
                            and key[0] in threads):
                        turns[key] = start['params']
                        break
            if key not in turns:
                raise ValueError('tool has unknown or unconfirmed thread or turn')
            tool = params.get('tool')
            schemas = {t['name']:t['inputSchema'] for t in threads[key[0]]['dynamicTools']}
            if tool not in ALLOWED_TOOLS or tool not in schemas or params.get('namespace') is not None:
                raise ValueError('unapproved dynamic tool call')
            Draft202012Validator(schemas[tool]).validate(params['arguments'])
            call = (*key,params['callId'])
            if call in seen_calls:
                raise ValueError('duplicate dynamic call ID')
            seen_calls.add(call)
            if message['id'] in pending_tools:
                raise ValueError('duplicate pending server request ID')
            pending_tools.add(message['id'])
            tool_requests.append(message)
        if direction == 'in' and method in {'item/started','item/completed'}:
            item = params.get('item', {})
            if item.get('type') not in PASSIVE_ITEMS:
                raise ValueError('workspace session used a built-in tool')
            if item.get('type') == 'dynamicToolCall' and (item.get('tool') not in ALLOWED_TOOLS
                    or item.get('namespace') is not None):
                raise ValueError('workspace session observed an unknown dynamic tool')
        if direction == 'in' and method == 'error':
            raise ValueError('workspace session reported error')
        if direction == 'in' and method == 'turn/completed':
            key = (params.get('threadId'),params['turn']['id'])
            if key not in turns or params['turn'].get('status') != 'completed':
                raise ValueError('workspace turn failed or has unknown identity')
            terminal = True
    if not threads or not turns:
        raise ValueError('workspace provenance lacks started thread/turn')
    if require_terminal and (not terminal or pending_tools):
        raise ValueError('workspace session has no completed terminal turn')
    return {'real':real, 'launch':launch, 'threads':threads, 'turns':turns,
            'tool_requests':tool_requests, 'terminal':terminal, 'events':events}


def audit_workspace_session(path, *, require_terminal=True, require_real=True):
    raw = _load_prefix(path)
    report = _audit(raw, require_terminal=require_terminal, require_real=require_real)
    return {'provider':PROVIDER, 'assurance':'audited-tool-allowlist', 'no_tool_events':False,
            'transcript_sha256':_hash(raw), 'transcript_bytes':len(raw),
            'thread_ids':list(report['threads']), 'tool_calls':len(report['tool_requests']),
            'terminal':report['terminal'], 'sealable_generation':report['real']}


def _binding(raw, proposal, metadata, theory, model, *, require_real):
    report = _audit(raw, require_terminal=False, require_real=require_real)
    request = metadata['raw_request']
    request_offset = metadata.get('tool_request_offset', metadata.get('transcript_offset'))
    if (type(request_offset) is not int or not 0 < request_offset <= len(raw)
            or not raw[:request_offset].endswith(b'\n')):
        raise ValueError('invalid workspace tool request offset')
    receipt = json.loads(raw[:request_offset].splitlines()[-1])
    if (receipt.get('direction') != 'in' or receipt.get('message', {}).get('parsed') != request
            or request not in report['tool_requests']):
        raise ValueError('workspace action differs from its exact tool request receipt')
    params = request['params']
    if (params.get('tool') != 'proof_action'
            or params.get('arguments') != {'theory':theory,'proposal':proposal}
            or params.get('threadId') != metadata.get('thread_id')
            or params.get('turnId') != metadata.get('turn_id')
            or params.get('callId') != metadata.get('call_id')):
        raise ValueError('workspace action/proposal/theory/identity mismatch')
    turn = report['turns'][(params['threadId'],params['turnId'])]
    if turn.get('model') != model or report['launch'].get('model') != model:
        raise ValueError('workspace model differs from bound model')
    return report


def record_workspace_action(prefix, prompt, proposal, metadata, *, theory, model):
    from regen.infrastructure.llm_transcript import artifact_paths, _hashes
    paths = artifact_paths(Path(prefix))
    if any(p.exists() for p in paths.values()):
        raise FileExistsError('refusing to overwrite workspace action artifacts')
    if type(metadata.get('transcript_offset')) is not int:
        raise ValueError('workspace transcript offset must be an integer')
    raw = _load_prefix(metadata['transcript_path'], metadata['transcript_offset'])
    report = _binding(raw, proposal, metadata, theory, model, require_real=False)
    response = json.dumps(proposal, ensure_ascii=False)
    binding = {k:deepcopy(metadata[k]) for k in ('thread_id','turn_id','call_id','raw_request','transcript_offset')}
    if 'tool_request_offset' in metadata:
        binding['tool_request_offset'] = metadata['tool_request_offset']
    binding.update(transcript_path=str(Path(metadata['transcript_path']).resolve()), transcript_prefix_sha256=_hash(raw))
    audit = {'provider':PROVIDER, 'no_tool_events':False, 'allowed_dynamic_tools':sorted(ALLOWED_TOOLS),
             'assurance':'audited-tool-allowlist', 'workspace_binding':binding,
             'prompt_role':'harness-context-snapshot', 'sealable_generation':report['real']}
    for key, value in [('prompt',prompt),('raw',json.dumps(metadata['raw_request'],ensure_ascii=False)+'\n'),
                       ('final',response),('stderr','')]:
        paths[key].parent.mkdir(parents=True, exist_ok=True)
        with paths[key].open('x',encoding='utf-8') as stream: stream.write(value)
    durable(paths['audit'], audit, immutable=True)
    meta = {'schema_version':3, 'state':'complete', 'provider':PROVIDER, 'model':model,
            'theory':theory, 'stage':'file-planner-workspace-action', 'returncode':0, 'timed_out':False,
            'provider_network_call':report['real'], 'sealable_generation':report['real'],
            'dispatch_kind':'dynamic_tool_call', 'provider_dispatches':0,
            'no_tool_events':False, **_hashes(paths)}
    durable(paths['meta'],meta,immutable=True)
    return response


def verify_workspace_action(prefix, *, require_real=True, require_success=True):
    from regen.infrastructure.llm_transcript import artifact_paths, _hashes
    paths = artifact_paths(Path(prefix))
    meta = json.loads(paths['meta'].read_text()); audit = json.loads(paths['audit'].read_text())
    if meta.get('provider') != PROVIDER or audit.get('provider') != PROVIDER:
        raise ValueError('invalid workspace artifact provider')
    for field, value in _hashes(paths).items():
        if meta.get(field) != value:
            raise ValueError('workspace artifact hash mismatch: '+field)
    binding = audit['workspace_binding']
    raw = _load_prefix(binding['transcript_path'], binding['transcript_offset'])
    if _hash(raw) != binding['transcript_prefix_sha256']:
        raise ValueError('workspace transcript prefix hash mismatch')
    proposal = json.loads(paths['final'].read_text())
    report = _binding(raw,proposal,binding,meta['theory'],meta['model'],require_real=require_real)
    if json.loads(paths['raw'].read_text()) != binding['raw_request']:
        raise ValueError('workspace raw request mismatch')
    if meta.get('dispatch_kind') != 'dynamic_tool_call' or meta.get('provider_dispatches') != 0:
        raise ValueError('workspace tool action must not claim a new provider dispatch')
    if meta.get('no_tool_events') is not False or audit.get('no_tool_events') is not False:
        raise ValueError('workspace provider must not claim no-tool evidence')
    if (meta.get('sealable_generation') != report['real'] or meta.get('provider_network_call') != report['real']):
        raise ValueError('workspace real-provider metadata differs from launch evidence')
    if require_success and (meta.get('returncode') != 0 or meta.get('timed_out')):
        raise ValueError('workspace action did not succeed')
    return meta
