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

from jsonschema import Draft202012Validator, ValidationError

from regen.execution.proof_commits import durable
from regen.infrastructure.audited_llm import model_settings
from regen.infrastructure.codex_workspace_session import validate_sleep_event
from regen.infrastructure.incremental_log import read_artifact

PROVIDER = 'codex-workspace-audited'
ALLOWED_TOOLS = {'list_workspace','read_theory','list_generated','read_generated',
                 'read_attempt','list_attempts','prepare_reuse','proof_action',
                 'submit_check','get_check_result','wait_checks','cancel_check'}
PASSIVE_ITEMS = {'agentMessage','reasoning','userMessage','dynamicToolCall','contextCompaction','plan'}

def _pre_history_registry(registry):
    legacy = deepcopy([tool for tool in registry if tool['name'] != 'list_attempts'])
    for tool in legacy:
        if tool['name'] == 'read_attempt':
            tool['description'] = 'Read a bound failed candidate and feedback in any theory as reference only.'
            tool['inputSchema']['properties'].pop('view', None)
    return legacy


def _pre_delta_registry(registry):
    """Exact legacy shape, not a wildcard approval of unreviewed arguments."""
    previous = deepcopy(registry)
    for tool in previous:
        if tool['name'] == 'read_theory':
            tool['inputSchema']['properties'].pop('if_source_sha256',None)
        if tool['name'] == 'proof_action':
            proposal = tool['inputSchema']['properties'].get('proposal',{})
            for alternative in proposal.get('oneOf',[]):
                values = alternative.get('properties',{}).get('action',{}).get('enum')
                if values and 'read_context_index' in values:
                    values.remove('read_context_index')
    return previous


def _pre_sketch_registry(registry):
    """Exact source-action schema before conditional groups/selected checks."""
    previous = deepcopy(registry)
    for tool in previous:
        if tool['name'] != 'proof_action':
            continue
        proposal = tool['inputSchema']['properties'].get('proposal', {})
        if 'oneOf' not in proposal:
            continue
        alternatives = []
        for action in proposal['oneOf']:
            kind = action.get('properties', {}).get('action', {}).get('const')
            if kind in {'check_sketch','read_sketch'}:
                continue
            if kind == 'edit_theory':
                action['properties'].pop('sketches', None)
            elif kind == 'check_theory':
                action['properties'].pop('entry_ids', None)
            alternatives.append(action)
        proposal['oneOf'] = alternatives
    return previous


def approved_registry(tools):
    names = [t.get('name') for t in tools]
    if not names or len(set(names)) != len(names) or any(t.get('type') != 'function' for t in tools):
        return False
    if set(names) <= ALLOWED_TOOLS:
        return True
    # The document help extension is accepted only with the exact shared registry.
    from regen.planning.parallel_file_planner import tools as document_tools
    if tools == [{'type':'function', **tool} for tool in document_tools(True)]:
        return True
    # Continue to audit frozen pre-delta-source and pre-history-view sessions by
    # their exact registry. Do not bless arbitrary changes to the help extension.
    # Do not replace this with a name-only approval of arbitrary help extensions.
    legacy_document = _pre_history_registry([{'type':'function', **tool} for tool in document_tools(True)])
    if tools == legacy_document:
        return True
    before_delta = _pre_delta_registry([{'type':'function', **tool} for tool in document_tools(True)])
    if tools == before_delta or tools == _pre_history_registry(before_delta):
        return True
    # A general repository agent has additional planning/integration tools.
    # Accept only its complete reviewed registry, never a union of name strings.
    from regen.planning.generic_codex_agent import registry
    current=[{'type':'function', **tool} for tool in registry()]
    if tools == current or tools == _pre_history_registry(current):return True
    before_sketch = _pre_sketch_registry(current)
    for previous_sketch in (before_sketch, _pre_delta_registry(before_sketch)):
        if tools == previous_sketch or tools == _pre_history_registry(previous_sketch):
            return True
    previous = _pre_delta_registry(current)
    if tools == previous or tools == _pre_history_registry(previous):return True
    # Replay the exact previously exported generic registry; never launch it anew.
    legacy=deepcopy([t for t in current if t['name']!='proof_tool_help'])
    for tool in legacy:
        if tool['name']=='experiment_status':
            tool['description']='Read deadline, accounting, plan and previous revision identities; optional notes persist across resume.'
    previous = _pre_delta_registry(legacy)
    return (tools == legacy or tools == _pre_history_registry(legacy)
            or tools == previous or tools == _pre_history_registry(previous)
            or tools == _pre_sketch_registry(legacy)
            or tools == _pre_history_registry(_pre_sketch_registry(legacy))
            or tools == _pre_delta_registry(_pre_sketch_registry(legacy))
            or tools == _pre_history_registry(_pre_delta_registry(_pre_sketch_registry(legacy))))


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
    # Old frozen transcripts have no version marker and retain the original
    # 120-second cumulative policy. Never reinterpret historical receipts using
    # today's runtime policy: a new launch must opt in explicitly. Version 2
    # validates sleep protocol shape/lifecycle only; the live progress watchdog
    # owns inactivity and native callbacks retain their own deadlines.
    sleep_policy_version = launch.get('sleep_policy_version', 1)
    if type(sleep_policy_version) is not int or sleep_policy_version not in {1, 2}:
        raise ValueError('unknown workspace sleep policy version')
    terminal_policy = launch.get('pending_terminal_policy_version', 1)
    if type(terminal_policy) is not int or terminal_policy not in {1, 2}:
        raise ValueError('unknown pending terminal policy version')
    argument_policy = launch.get('tool_argument_policy_version', 1)
    if type(argument_policy) is not int or argument_policy not in {1, 2}:
        raise ValueError('unknown tool argument policy version')
    real = launch.get('provider') == 'codex-app-server' and launch.get('synthetic') is False
    if require_real and not real:
        raise ValueError('synthetic or unmarked workspace launch cannot seal')
    if real:
        settings = model_settings()
        isolation_policy = settings.get('model_isolation', {})
        if isolation_policy.get('required'):
            isolation = launch.get('isolation') or {}
            if (launch.get('exclusive_os_isolation') is not True
                    or isolation.get('version') != 'model-os-isolation-v1'
                    or isolation.get('image') != isolation_policy.get('image')
                    or isolation.get('network') != 'none'):
                raise ValueError('workspace launch lacks required OS isolation evidence')
        from regen.infrastructure.codex_runtime import verify_codex_launch
        verify_codex_launch(settings, launch)
    requests, threads, turns, tool_requests = {}, {}, {}, []
    terminal = False
    seen_calls = set()
    # Keep ownership as well as the RPC identity: a terminal event must follow
    # every tool result for THAT turn, even if another turn still has a request.
    pending_tools = {}
    invalid_tools = {}
    rejected_tool_calls = 0
    sleep_state = {}
    finished_turns = set()
    for event in events:
        direction, message = event.get('direction'), event.get('message')
        if direction == 'local':
            if message.get('nonsealable') or message.get('event') in {'session_failed','deadline_expired'}:
                raise ValueError('workspace session contains a failed or unsafe event')
            if message.get('event') == 'tool_arguments_rejected':
                matches = [entry for entry in invalid_tools.values()
                           if entry['identity'] == (message.get('thread_id'),
                               message.get('turn_id'), message.get('call_id'), message.get('tool'))]
                if (argument_policy != 2 or len(matches) != 1 or matches[0]['rejected']
                        or matches[0]['error'] != message.get('error')):
                    raise ValueError('unmatched or inconsistent tool argument rejection')
                matches[0]['rejected'] = True
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
            invalid = invalid_tools.get(message['id'])
            if invalid is not None:
                result = message['result']
                items = result.get('contentItems', [])
                try:
                    feedback = json.loads(items[0]['text']) if len(items) == 1 else None
                except (ValueError, KeyError, TypeError):
                    feedback = None
                if (not invalid['rejected'] or result.get('success') is not False
                        or len(items) != 1 or items[0].get('type') != 'inputText'
                        or feedback != invalid['error']):
                    raise ValueError('invalid tool arguments lack matching rejection feedback')
                del invalid_tools[message['id']]
            pending_tools.pop(message['id'])
            continue
        if direction == 'out' and method:
            if method == 'turn/start' and pending_tools and terminal_policy == 2:
                raise ValueError('new turn started before pending tool results')
            if method not in {'initialize','initialized','thread/start','turn/start'}:
                raise ValueError('unapproved client method: '+method)
            if 'id' in message:
                if message['id'] in requests:
                    raise ValueError('duplicate client request ID')
                requests[message['id']] = message
            if method == 'thread/start':
                tools = params.get('dynamicTools', [])
                names = [t.get('name') for t in tools]
                if not approved_registry(tools):
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
            if key in finished_turns:
                raise ValueError('workspace tool belongs to a finished turn')
            tool = params.get('tool')
            schemas = {t['name']:t['inputSchema'] for t in threads[key[0]]['dynamicTools']}
            if tool not in schemas or params.get('namespace') is not None:
                raise ValueError('unapproved dynamic tool call')
            invalid = None
            try:
                Draft202012Validator(schemas[tool]).validate(params['arguments'])
            except ValidationError as error:
                if argument_policy != 2:
                    raise
                invalid = {'identity': (*key, params['callId'], tool), 'rejected': False,
                           'error': {'error_type': 'ToolArgumentValidationError',
                               'code': 'invalid_tool_arguments', 'message': error.message,
                               'path': list(error.absolute_path),
                               'schema_path': list(error.absolute_schema_path)}}
            call = (*key,params['callId'])
            if call in seen_calls:
                raise ValueError('duplicate dynamic call ID')
            seen_calls.add(call)
            if message['id'] in pending_tools:
                raise ValueError('duplicate pending server request ID')
            pending_tools[message['id']] = key
            if invalid is None:
                tool_requests.append(message)
            else:
                # Never let a rejected proof_action/submit_check authorize a
                # proof artifact, including an unfinished durable prefix.
                invalid_tools[message['id']] = invalid
                rejected_tool_calls += 1
        if direction == 'in' and method in {'item/started','item/completed'}:
            item = params.get('item', {})
            if item.get('type') == 'sleep':
                validate_sleep_event(params, method, set(turns) - finished_turns,
                                     sleep_state, policy_version=sleep_policy_version)
            elif item.get('type') not in PASSIVE_ITEMS:
                raise ValueError('workspace session used a built-in tool')
            if item.get('type') == 'dynamicToolCall':
                if argument_policy == 2:
                    owner = (params.get('threadId'), params.get('turnId'))
                    if owner not in turns:
                        # As with early RPCs, a notification can precede the
                        # start response, but that response must be present in
                        # this durable prefix and confirm an already sent start.
                        for later in events:
                            if later.get('direction') != 'in':
                                continue
                            answer = later['message']['parsed']
                            start = requests.get(answer.get('id'), {})
                            if (start.get('method') == 'turn/start' and 'error' not in answer
                                    and (start['params'].get('threadId'),
                                         answer.get('result', {}).get('turn', {}).get('id')) == owner
                                    and owner[0] in threads):
                                turns[owner] = start['params']
                                break
                    # A v2 deferred terminal can precede a tool's display-only
                    # completion notification. It is not a new RPC and cannot
                    # authorize execution or proof binding.
                    if owner not in turns or (owner in finished_turns and terminal_policy != 2):
                        raise ValueError('workspace dynamic tool notification has unknown or finished owner')
                registered = {t['name'] for t in threads.get(params.get('threadId'), {}).get('dynamicTools', [])}
                if item.get('tool') not in registered or item.get('namespace') is not None:
                    raise ValueError('workspace session observed an unknown dynamic tool')
        if direction == 'in' and method == 'error':
            key = (params.get('threadId'),params.get('turnId'))
            if params.get('willRetry') is not True or key not in turns:
                raise ValueError('workspace session reported terminal or unbound error')
        if direction == 'in' and method == 'turn/completed':
            key = (params.get('threadId'),params['turn']['id'])
            if key not in turns or params['turn'].get('status') != 'completed':
                raise ValueError('workspace turn failed or has unknown identity')
            if key in finished_turns:
                raise ValueError('duplicate workspace terminal')
            if key in pending_tools.values() and terminal_policy == 1:
                raise ValueError('workspace turn completed before pending tool results')
            if any(owner[:2] == key for owner in sleep_state.get('pending', {})):
                raise ValueError('workspace turn completed with unfinished sleep item')
            terminal = True
            finished_turns.add(key)
    if not threads or not turns:
        raise ValueError('workspace provenance lacks started thread/turn')
    if require_terminal and (not terminal or pending_tools):
        raise ValueError('workspace session has no completed terminal turn')
    return {'real':real, 'launch':launch, 'threads':threads, 'turns':turns,
            'tool_requests':tool_requests, 'rejected_tool_calls':rejected_tool_calls,
            'terminal':terminal and not pending_tools, 'events':events}


def audit_workspace_session(path, *, require_terminal=True, require_real=True, offset=None):
    raw = _load_prefix(path, offset)
    report = _audit(raw, require_terminal=require_terminal, require_real=require_real)
    isolated = report['launch'].get('exclusive_os_isolation') is True
    return {'provider':PROVIDER, 'assurance':'os-isolated-tool-allowlist' if isolated else 'audited-tool-allowlist',
            'exclusive_os_isolation':isolated, 'no_tool_events':False,
            'transcript_sha256':_hash(raw), 'transcript_bytes':len(raw),
            'thread_ids':list(report['threads']),
            'tool_calls':len(report['tool_requests']) + report['rejected_tool_calls'],
            'rejected_tool_calls':report['rejected_tool_calls'],
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
    arguments = params.get('arguments', {})
    expected = {'theory':theory,'proposal':proposal}
    if params.get('tool') == 'submit_check':
        kind = {'submit_batch':'proof_batch','finish':'seal'}.get(proposal.get('action'))
        if kind is None:
            raise ValueError('read-only task cannot establish proof generation provenance')
        expected['kind'] = kind
    if (params.get('tool') not in {'proof_action','submit_check'}
            or arguments != expected
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
    isolated = report['launch'].get('exclusive_os_isolation') is True
    registered = report['threads'][metadata['thread_id']]['dynamicTools']
    audit = {'provider':PROVIDER, 'no_tool_events':False, 'allowed_dynamic_tools':sorted(t['name'] for t in registered),
             'assurance':'os-isolated-tool-allowlist' if isolated else 'audited-tool-allowlist',
             'exclusive_os_isolation':isolated, 'workspace_binding':binding,
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
    meta = read_artifact(paths['meta']); audit = read_artifact(paths['audit'])
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
