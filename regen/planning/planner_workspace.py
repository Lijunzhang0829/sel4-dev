"""Bound read-only observations and an explicitly selected planner workspace."""
from copy import deepcopy
from pathlib import Path
import re

from regen.execution.proof_commits import digest


def attempt_evidence(directory, state, number):
    from regen.planning.batch_file_planner import read_bound_json
    history = {h['request_number']:h for h in state.get('batch_history', [])}
    if type(number) is not int or number not in history:
        raise ValueError('request_number must identify a recorded historical attempt')
    h = history[number]
    if not h.get('feedback_sha256') or not h.get('proposal_sha256'):
        raise ValueError('attempt needs bound proposal and feedback evidence')
    d = Path(directory)/'batch-requests'/f'{number:06d}'
    try:
        return (read_bound_json(d/'proposal.json',h['proposal_sha256']),
                read_bound_json(d/'feedback.json',h['feedback_sha256']))
    except OSError as error:
        raise ValueError('bound historical attempt artifacts unavailable; choose an available observation') from error


def diagnostic_goals(check):
    """Parse printed goals conservatively; never manufacture terms or split assumptions."""
    text = check.get('diagnostic','')
    header = re.search(r'goal \((\d+) subgoals?\):\n',text)
    if not header:
        return {'origin':'native_diagnostic_text','goals':[], 'parse_status':'no_numbered_goals',
                'diagnostic_sha256':digest(text)}
    body = text[header.end():].split('\nAt command ',1)[0]
    total = int(header[1]); truncated = re.search(r'\nA total of \d+ subgoals',body)
    if truncated: body = body[:truncated.start()]
    matches = list(re.finditer(r'(?m)^\s?(\d+)\. ',body))
    goals = []
    for i,m in enumerate(matches):
        goal = body[m.end():matches[i+1].start() if i+1<len(matches) else len(body)].rstrip()
        complete = bool(goal) and not ('...' in goal or '…' in goal)
        goals.append({'goal_index':int(m[1]),'text':goal,'complete':complete,'sha256':digest(goal),
                      'assumptions_notice':'Local hypotheses, if printed, remain inside this exact goal text.'})
    return {'origin':'native_diagnostic_text','goal_count':total,'goals':goals,
            'all_goals_present':len(goals)==total and all(g['complete'] for g in goals),
            'parse_status':'parsed','diagnostic_sha256':digest(text)}


def proof_state(directory, state, request, *, expand=False):
    proposal, feedback = attempt_evidence(directory,state,request.get('request_number'))
    check = feedback.get('check',{})
    parsed = diagnostic_goals(check)
    native = deepcopy(check.get('native_proof_state_full'))
    if not expand:
        for goal in parsed['goals']:
            text = goal.pop('text')
            goal.update(text_excerpt=text[:1600], excerpt_incomplete=len(text)>1600)
        if native:
            native['goals'] = [{'goal_index':i+1,'text_excerpt':text[:1600],
                'excerpt_incomplete':len(text)>1600} for i,text in enumerate(native.get('goals',[]))]
            native['local_assumptions'] = [{'text_excerpt':text[:1600],
                'excerpt_incomplete':len(text)>1600} for text in native.get('local_assumptions',[])]
    return {'status':'available','proof_state':{
        'request_number':request['request_number'], 'submitted_node_ids':list(proposal.get('proofs',{})),
        'failed_node_ids':[x['node_id'] for x in feedback.get('node_results',[]) if x.get('status')=='failed'],
        'diagnostic':parsed, 'native_before_command':native,
        'failure_locations':[{'line':int(n),'source':p} for n,p in
            re.findall(r'line\s+(\d+)\s+of\s+"([^"]+)"',check.get('diagnostic',''))],
        'notice':'Before-command state and diagnostic residuals are distinct observations; neither is a proved premise.'}}


def intermediate_target(directory, state, request):
    selector = request.get('proof_state_query')
    if not isinstance(selector,dict) or set(selector)-{'request_number','goal_index','origin'}:
        raise ValueError('proof_state_query requires request_number and goal_index')
    if type(selector.get('goal_index')) is not int:
        raise ValueError('goal_index must be an integer')
    observed = proof_state(directory,state,selector,expand=True)['proof_state']
    origin = selector.get('origin','diagnostic')
    if origin not in {'diagnostic','before_command'}:
        raise ValueError('proof state origin must be diagnostic or before_command')
    goals = {g['goal_index']:g for g in observed['diagnostic']['goals']}
    if origin == 'before_command':
        native = observed.get('native_before_command') or {}
        assumptions = native.get('local_assumptions',[])
        goals = {i+1:{'text':'\n'.join([*assumptions,text]),'complete':'...' not in text and '…' not in text,
            'sha256':digest([assumptions,text])} for i,text in enumerate(native.get('goals',[]))}
    goal = goals.get(selector['goal_index'])
    if not goal or not goal['complete']:
        raise ValueError('selected residual is unavailable or incomplete; inspect original diagnostic')
    context_id = request.get('context_node_id')
    # A helper draft may be absent from the accepted graph. Bind locale context
    # to the historical candidate declaration, never a model supplied proposition.
    proposal, _ = attempt_evidence(directory,state,selector['request_number'])
    nodes = {n['id']:n for n in state['graph']['nodes']}
    nodes.update({n['id']:n for n in proposal.get('graph',{}).get('generated_nodes',[])})
    if context_id not in observed['submitted_node_ids'] or context_id not in nodes:
        raise ValueError('context_node_id must be one of this historical batch\'s submitted nodes')
    if len(observed['submitted_node_ids'])!=1 and observed['failed_node_ids'] != [context_id]:
        raise ValueError('ambiguous multi-node residual; use native failed-node evidence before recall')
    current = next((n for n in state['graph']['nodes'] if n['id']==context_id),None)
    node = nodes[context_id]
    if current and any(current.get(k)!=node.get(k) for k in ('statement','context')):
        raise ValueError('historical target declaration differs from current graph')
    return {**node,'statement':goal['text']}, {
        'request_number':selector['request_number'],'goal_index':selector['goal_index'],
        'origin':origin,'goal_sha256':goal['sha256'],'diagnostic_sha256':observed['diagnostic']['diagnostic_sha256']}


def validate_workspace(state, value, *, candidate_nodes=()):
    if not isinstance(value,dict) or set(value)-{'focus_node_ids','record_ids','summary'}:
        raise ValueError('workspace supports focus_node_ids, record_ids and summary')
    known=({n['id'] for n in state['graph']['nodes']} | set(state.get('batch_target_memory',{}))
           | {n['id'] for n in candidate_nodes})
    for field,available,limit in [('focus_node_ids',known,4),
        ('record_ids',{f['record_id'] for f in state.get('batch_facts',[])},24)]:
        ids=value.get(field,[])
        if not isinstance(ids,list) or len(ids)>limit or any(not isinstance(k,str) or k not in available for k in ids):
            raise ValueError('invalid workspace '+field)
    if not isinstance(value.get('summary',''),str) or len(value.get('summary',''))>6000:
        raise ValueError('workspace summary must be at most 6000 characters')
    return deepcopy(value)


def read_observation(directory, state, request, checker=None, *, query_handler=None,
                     item_index=None):
    from regen.planning.batch_file_planner import (compact_feedback, validate_read_ids,
        observation_summary, read_diagnostic)
    action=request.get('action')
    if action=='query':
        query=request.get('request')
        if not isinstance(query,dict):
            raise ValueError('query request must be an object')
        if query_handler is None:
            raise ValueError('query observation requires the bound query executor')
        # The controller binds the frozen run/context and a distinct audit path
        # for this item. Never derive those bindings from model-supplied paths.
        result=query_handler(deepcopy(query),item_index)
        if not isinstance(result,dict):
            raise ValueError('query executor returned an invalid observation')
        return deepcopy(result)
    if action=='read_attempts':
        from regen.planning.attempt_views import attempt_view
        numbers=request.get('request_numbers')
        if not isinstance(numbers,list) or not 1<=len(numbers)<=4:
            raise ValueError('read_attempts requires 1-4 request numbers')
        attempts=[]
        for number in numbers:
            candidate,feedback=attempt_evidence(directory,state,number)
            attempts.append(attempt_view(number, candidate, feedback, view=request.get('view', 'summary'),
                theory=state.get('graph', {}).get('theory'), run_id=state.get('graph', {}).get('run_id')))
        return {'status':'available','attempts':attempts}
    if action=='read_facts':
        facts={f['record_id']:f for f in state.get('batch_facts',[])}
        ids=request.get('record_ids');validate_read_ids(ids,facts,action=action,field='record_ids',maximum=24,index='recalled_fact_index')
        return {'status':'available','records':[deepcopy(facts[k]) for k in ids]}
    if action=='read_proofs':
        ids=request.get('node_ids');validate_read_ids(ids,state['proofs'],action=action,field='node_ids',maximum=4,index='green_node_ids')
        nodes={n['id']:n for n in state['graph']['nodes']}
        return {'status':'available','proofs':[{**nodes[k],'proof_body':state['proofs'][k],'proof_sha256':digest(state['proofs'][k])} for k in ids]}
    if action=='read_queries':
        queries={q['query_id']:q for q in observation_summary(state)[0]};ids=request.get('query_ids')
        validate_read_ids(ids,queries,action=action,field='query_ids',maximum=4,index='query_index')
        return {'status':'available','queries':[queries[k] for k in ids]}
    if action=='read_diagnostic':return read_diagnostic(directory,state,request)
    if action=='read_proof_state':
        origin = request.get('origin', 'diagnostic')
        if origin not in {'diagnostic', 'before_command'}:
            raise ValueError('proof state origin must be diagnostic or before_command')
        if 'goal_indices' not in request:
            result = proof_state(directory,state,request)
            if 'origin' in request:
                result['proof_state']['selected_origin'] = origin
            return result
        indices=request['goal_indices']
        if (not isinstance(indices,list) or not 1<=len(indices)<=4
                or any(type(i) is not int or i<1 for i in indices) or len(set(indices))!=len(indices)):
            raise ValueError('goal_indices requires 1-4 distinct positive integers')
        observed=proof_state(directory,state,request,expand=True)['proof_state']
        if origin == 'before_command':
            native = observed.get('native_before_command')
            if not isinstance(native, dict) or native.get('origin') == 'unavailable':
                raise ValueError('native before-command state unavailable; no diagnostic fallback was used')
            raw_goals = native.get('goals', [])
            if any(i > len(raw_goals) for i in indices):
                raise ValueError('selected native before-command goal missing')
            assumptions = native.get('local_assumptions', [])
            texts = [raw_goals[i-1] for i in indices]
            if any(not text or '...' in text or '…' in text for text in [*texts, *assumptions]):
                raise ValueError('selected native before-command state contains incomplete printed terms')
            if sum(len(text) for text in [*texts, *assumptions]) > 60000:
                raise ValueError('selected native state exceeds 60000 characters; select fewer goals or read_attempts view=full')
            selected = [{'goal_index': i, 'text': raw_goals[i-1], 'complete': True,
                         'sha256': digest([assumptions, raw_goals[i-1]])} for i in indices]
            return {'status': 'available', 'proof_state': {
                'request_number': request['request_number'], 'selected_origin': origin,
                'submitted_node_ids': observed['submitted_node_ids'],
                'native_before_command': {k: deepcopy(native[k]) for k in ('origin', 'command', 'line') if k in native}
                    | {'goals': selected, 'local_assumptions': assumptions,
                       'goal_count': len(raw_goals), 'all_goals_present': len(indices) == len(raw_goals)},
                'notice': 'Exact selected state before the failed command, not post-failure diagnostic residuals or proved premises.'}}
        goals={g['goal_index']:g for g in observed['diagnostic']['goals']}
        if any(i not in goals or not goals[i]['complete'] for i in indices):
            raise ValueError('selected diagnostic goal missing or incomplete; read original diagnostic pages')
        selected=[goals[i] for i in indices]
        if sum(len(g['text']) for g in selected)>60000:
            raise ValueError('selected goals exceed 60000 characters; select fewer or read_diagnostic pages')
        return {'status':'available','proof_state':{
            'request_number':request['request_number'],'selected_origin':origin,
            'submitted_node_ids':observed['submitted_node_ids'],
            'diagnostic':{**observed['diagnostic'],'goals':selected,
                'all_goals_present':len(selected)==observed['diagnostic']['goal_count']},
            'notice':'Exact selected diagnostic goals including printed assumptions; not proved premises.'}}

    if action=='resolve_symbols':
        names=request.get('names');nodes={n['id']:n for n in state['graph']['nodes']};node=request.get('context_node_id')
        if (not isinstance(names,list) or not 1<=len(names)<=16 or
            any(not isinstance(n,str) or not n.strip() or len(n)>256 for n in names) or node not in nodes):
            raise ValueError('resolve_symbols requires 1-16 names and a current context_node_id')
        return {'status':'available','symbols':checker.resolve_symbols(names,nodes[node].get('context',[]))}
    raise ValueError('observe permits read-only observations, not nested observe or proof execution')


def observe(directory, state, proposal, checker, *, query_handler=None):
    """Execute independent read-only requests against the same accepted state.

    Repeated action kinds are allowed, for example two bound diagnostic pages.
    Each query gets its item number so the controller can preserve separate
    evidence without changing the theory-relative lookup directory.
    """
    reads=proposal.get('reads')
    if not isinstance(reads,list) or not 1<=len(reads)<=8 or any(not isinstance(r,dict) for r in reads):
        raise ValueError('observe requires 1-8 read requests')
    results=[]
    for item_index,request in enumerate(reads,1):
        try:
            if 'workspace' in request:
                validate_workspace(state, request['workspace'])
            result=read_observation(directory,state,request,checker,
                query_handler=query_handler,item_index=item_index)
        except (ValueError,KeyError,TypeError,OSError,RuntimeError) as error:
            result={'status':'unavailable','diagnostic':str(error)}
            if isinstance(getattr(error,'details',None),dict):
                result.update(deepcopy(error.details))
        results.append({'item_index':item_index,'request':deepcopy(request),'result':result})
    return {'status':'available','observations':results}


def consumer_feedback(state):
    nodes={n['id']:n for n in state['graph']['nodes']}
    history=state.get('batch_history',[]);green_at={}
    latest_trial={}
    for h in history:
        if h.get('action')=='submit_batch':
            for node in h.get('proof_node_ids',[]):
                latest_trial[node]=h['request_number']
                if h.get('status')=='accepted':green_at[node]=h['request_number']
    contracts=[n['id'] for n in nodes.values() if n['role']=='contract' and n['id'] not in state['proofs']]
    helper_status=[]
    for key in state['accepted_order']:
        if nodes[key]['role']=='contract':continue
        consumers=[n['id'] for n in nodes.values() if key in n.get('dependencies',[])]
        helper_status.append({'helper_id':key,'green_request':green_at.get(key),
            'planned_consumers':consumers,
            'consumer_trials_after_green':[{ 'node_id':c,'request_number':latest_trial[c]}
                for c in consumers if latest_trial.get(c,0)>=green_at.get(key,float('inf'))],
            'actual_proof_use':'not_inferred_from_planned_dependencies'})
    return {'pending_contracts':[{'node_id':k,'last_trial_request':latest_trial.get(k),
               'requests_since_trial':state.get('batch_requests',0)-latest_trial[k] if k in latest_trial else None} for k in contracts],
            'green_helpers':helper_status,
            'notice':'Validate helper usefulness with its consumer, or record why further decomposition is needed. '
                     'Missing planned consumers does not prove a helper is unused. No retry/creation quota.'}


def observation_inventory(payload):
    """Describe bytes already visible; a short target preview is not a missing read."""
    entries=[]
    attempts=[]
    latest=payload.get('last_proof_feedback')
    if isinstance(latest,dict) and 'request_number' in latest:
        attempts.append(('last_proof_feedback',latest))
    attempts.extend((f'requested_attempts[{i}]',a) for i,a in enumerate(payload.get('requested_attempts',[])))
    for path,attempt in attempts:
        check=attempt.get('feedback',{}).get('check',{})
        diagnostic=check.get('diagnostic')
        if not isinstance(diagnostic,str):continue
        excerpt=check.get('diagnostic_excerpt',{})
        incomplete=isinstance(excerpt,dict) and excerpt.get('incomplete',False)
        entries.append({'request_number':attempt['request_number'],'kind':'diagnostic',
            'path':path+'.feedback.check.diagnostic','offset':0,'end':len(diagnostic),
            'total_chars':excerpt.get('total_chars',len(diagnostic)) if isinstance(excerpt,dict) else len(diagnostic),
            'complete':not incomplete})
    # References are navigation, not another copy of diagnostic text. Include
    # only canonical bodies/pages so every listed path directly contains bytes.
    pages=[(f'diagnostic_pages[{i}]',page) for i,page in enumerate(payload.get('diagnostic_pages',[]))]
    pages.append(('diagnostic_page',payload.get('diagnostic_page')))
    for path,page in pages:
        if not isinstance(page,dict) or not isinstance(page.get('text'),str):continue
        entries.append({'request_number':page['request_number'],'kind':'diagnostic',
            'path':path+'.text','offset':page['offset'],'end':page['end'],
            'total_chars':page['total_chars'],'complete':not page.get('incomplete',True)})
    return {'diagnostics':entries,
        'notice':'These exact ranges are already in this input. target_memory is only a short navigation preview; '
                 'its incomplete flag does not mean the complete diagnostic is absent. Read only missing ranges, '
                 'or explain the need for an intentional reread.'}
