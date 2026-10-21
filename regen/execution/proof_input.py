"""Bounded model views; raw checker evidence never changes."""
import copy
import hashlib
import json

MAX_INPUT_CHARS = 10000
DIAGNOSTIC_CHARS = 2200


class InputBudgetExceeded(ValueError):
    pass


def evidence_id(text):
    return hashlib.sha256(text.encode()).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def excerpt(text, limit=700):
    """An explicitly incomplete observation, never a replacement proposition."""
    text = str(text or '')
    if len(text) <= limit:
        return text
    return {'diagnostic_ref': evidence_id(text), 'original_chars': len(text),
            'complete': False, 'head': text[:limit*2//3],
            'tail': text[-limit//3:] if limit else '',
            'notice': 'Incomplete exact excerpt; omitted text is not proved.'}


def attempt_view(item, *, proof=False):
    item = item or {}
    check = item.get('check') or {}
    result = {k: item[k] for k in ('attempt', 'failure_class') if k in item}
    result['verdict'] = check.get('verdict', item.get('status'))
    result['diagnostic'] = excerpt(check.get('diagnostic', item.get('diagnostic', '')))
    body = item.get('proof_body')
    if isinstance(body, str):
        result['proof_sha256'] = evidence_id(body)
        if proof and len(body) <= 2200:
            result['proof_body'] = body
        elif proof:
            result['proof_omitted'] = 'Body exceeds view budget; produce a fresh complete body.'
    return result


def feedback_view(value):
    value = value or {}
    result = {k: value[k] for k in ('node_id', 'status', 'round_id', 'failure_class',
                                  'failure_scope') if k in value}
    for key in ('residual', 'last_model_error', 'early_handoff'):
        if value.get(key):
            result[key] = excerpt(value[key] if isinstance(value[key], str) else encoded(value[key]))
    history = value.get('checked_history', []) + value.get('attempts', [])
    result['attempt_count'] = len(value.get('attempts', []))
    unique = []
    for item in history:
        summary = attempt_view(item)
        if summary not in unique:
            unique.append(summary)
    result['recent_failures'] = unique[-2:]
    from regen.execution.proof_feedback import semantic_goal
    semantic = [x for x in history
                if isinstance(x.get('check', {}).get('diagnostic'),str)
                and x.get('check', {}).get('isabelle_called') is not False
                and semantic_goal(x['check']['diagnostic'])]
    if semantic and not semantic_goal(str(value.get('residual',''))):
        result['semantic_failure'] = excerpt(semantic[-1]['check']['diagnostic'])
    if value.get('last_proof_body') and not history:
        result['last_proof_body'] = excerpt(value['last_proof_body'], 500)
    return result


def failure_observation(text):
    """Keep ordinary feedback verbatim; huge logs are addressed via evidence pages."""
    if not isinstance(text,str):
        return text
    lines = text.splitlines()
    if len(lines)<=80 and all(len(line)<=2000 for line in lines):
        return text
    selected = [line for line in lines if len(line)<=2000 and
                any(tag in line.lower() for tag in ('at command','error','exception',
                    'undefined','failed','timeout','goal:'))][:12]
    return {'diagnostic_ref':evidence_id(text),'complete':False,
            'original_chars':len(text),'original_lines':len(lines),
            'diagnostic_lines':selected,
            'notice':'Large checker output; request diagnostic pages for the omitted state.'}


def fact_view(item):
    # Exact propositions and identity survive; ranking/source paths stay in audit.
    return {k: item[k] for k in ('record_id', 'canonical_name', 'id', 'name',
                               'exact_statement', 'statement', 'context',
                               'attributes') if k in item}


def graph_view_scope(graph, failed_node_id):
    """Whole connected proof component; excluded components are immutable."""
    by_id = {n['id']:n for n in graph['nodes']}
    if failed_node_id not in by_id:
        return set(by_id)
    scope = {failed_node_id}
    while True:
        expanded = scope | {dep for key in scope for dep in by_id[key].get('dependencies', [])}
        expanded |= {n['id'] for n in graph['nodes'] if scope.intersection(n.get('dependencies', []))}
        if expanded == scope:
            return scope
        scope = expanded


def graph_id_aliases(graph):
    return {f'@{i}': node['id'] for i, node in enumerate(graph['nodes'])}


def expand_graph_decision(decision, graph, failed_node_id):
    result = replace_ids(decision, graph_id_aliases(graph))
    scope = graph_view_scope(graph, failed_node_id)
    contexts = []
    for node in graph['nodes']:
        value = node.get('context')
        if node['id'] in scope and value and value not in contexts:
            contexts.append(value)
    for node in result.get('generated_nodes', []):
        if 'context_ref' in node:
            index = node.pop('context_ref')
            if not isinstance(index,int) or isinstance(index,bool) or not 0 <= index < len(contexts):
                raise ValueError('invalid model context_ref')
            if 'context' in node and node['context'] != contexts[index]:
                raise ValueError('model context conflicts with context_ref')
            node['context'] = copy.deepcopy(contexts[index])
    return result


def replace_ids(value, mapping):
    if isinstance(value, dict):
        return {mapping.get(k,k):replace_ids(v,mapping) for k,v in value.items()}
    if isinstance(value, list):
        return [replace_ids(x,mapping) for x in value]
    return mapping.get(value,value) if isinstance(value,str) else value


def project(payload, *, budget=MAX_INPUT_CHARS, excerpt_limit=DIAGNOSTIC_CHARS,
            role=None):
    """Pure model projection. Raw input is never mutated. Budget includes JSON."""
    if role is None:
        # Compatibility for diagnostic-only callers; production uses explicit roles.
        evidence = {}
        def visit(value, key=''):
            if isinstance(value, dict):
                return {k: visit(v, k) for k, v in value.items()}
            if isinstance(value, list):
                return [visit(v, key) for v in value]
            if isinstance(value, str) and key in {'diagnostic', 'residual', 'semantic_goal', 'last_model_error'} and len(value)>1000:
                ident = evidence_id(value)
                evidence.setdefault(ident, {'complete': True, 'text': value} if len(value)<=excerpt_limit else excerpt(value, excerpt_limit))
                return {'diagnostic_ref': ident}
            return value
        view = visit(copy.deepcopy(payload))
        if view.get('best_previous_attempt') is not None and view.get('best_previous_attempt') == view.get('latest_attempt'):
            view['latest_attempt'] = {'same_as': 'best_previous_attempt'}
        if evidence:
            view['diagnostic_observations'] = evidence
        result = encoded(view)
        if len(result)>budget:
            raise InputBudgetExceeded('INPUT_BUDGET_EXCEEDED: fixed input exceeds character budget')
        return result

    facts = list(payload.get('retrieved_legal_premises', []))
    optional = []
    if role == 'prover':
        node = payload['node']
        view = {'target':{k:node[k] for k in ('statement','context') if k in node},
                'recent_attempts':[]}
        history = payload.get('recent_attempts')
        if history is None:
            prior = payload.get('previous_round_feedback') or {}
            history = prior.get('checked_history',[]) + prior.get('attempts',[])
            if not history and prior.get('last_proof_body'):
                history = [{'proof_body':prior['last_proof_body'], 'check':{'diagnostic':prior.get('residual','')}}]
            history += [x for x in (payload.get('best_previous_attempt'),payload.get('latest_attempt')) if x]
        unique = []
        for attempt in history:
            check = attempt.get('check') or {}
            item = {'attempt':attempt.get('attempt'), 'proof_body':attempt.get('proof_body',''),
                    'feedback':failure_observation(check.get('diagnostic','')),
                    'verdict':check.get('verdict'),
                    'isabelle_called':check.get('isabelle_called',True)}
            if item not in unique:
                unique.append(item)
        from regen.execution.observation_context import attempt_brief
        view['recent_attempts'] = unique[-1:]
        source_attempts = history
        view['attempt_summaries'] = [attempt_brief(x) for x in source_attempts[:-1]]
        if payload.get('green_generated_dependencies'):
            view['accepted_auxiliary_index']=[{k:f[k] for k in ('id','name','context','proof_sha256') if k in f}
                for f in payload['green_generated_dependencies']]
        from regen.execution.observation_context import working_context, prior_round_summary
        view.update(working_context(payload.get('observations',[]),payload.get('recalled_facts',[])))
        if payload.get('interaction_state') is not None:
            view['interaction_state']=payload['interaction_state']
            view['checker_context']=payload.get('checker_context',{})
            view['forbidden_facts']=payload.get('forbidden_facts',[])
            view['previous_rounds']=prior_round_summary(payload.get('previous_round_feedback'))
            if payload.get('proof_guidance'): view['repair_guidance']=payload['proof_guidance']
            if not unique and payload.get('best_previous_attempt'):
                previous=payload['best_previous_attempt']
                view['repair_baseline']={'proof_body':previous.get('proof_body',''),
                                        'feedback':failure_observation(previous.get('check',{}).get('diagnostic',''))}
        return encoded(view)
    if role == 'gra':
        from regen.execution.gra_input import gra_view
        return encoded(gra_view(payload))
    raise ValueError(f'unknown model view role: {role}')


def render_prompt(instructions, payload, *, role, audit_path=None):
    prefix = instructions + "\nINPUT:\n"
    if audit_path:
        from pathlib import Path
        path = Path(audit_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = encoded(payload)+'\n'
        preferred = path
        index = 0
        while True:
            try:
                with path.open('x', encoding='utf-8') as handle:
                    handle.write(raw)
                break
            except FileExistsError:
                index += 1
                path = preferred.with_name(f'{preferred.stem}-{index:03d}{preferred.suffix}')
    try:
        result = prefix + project(payload, role=role, budget=MAX_INPUT_CHARS-len(prefix))
    except InputBudgetExceeded as error:
        if audit_path:
            path.with_suffix('.selection.json').write_text(encoded({'status':'blocked', 'error':str(error), 'limit_chars':MAX_INPUT_CHARS})+'\n')
        raise
    if audit_path:
        view = json.loads(result[len(prefix):])
        path.with_suffix('.selection.json').write_text(encoded({
            'status':'ready', 'role':role, 'limit_chars':None,
            'prompt_chars':len(result), 'prompt_sha256':evidence_id(result),
            'raw_input_path':str(path),
            'section_chars':{k:len(encoded(v)) for k,v in view.items()},
            'raw_payload_sha256':evidence_id(encoded(payload)),
            'input_selection':view.get('input_selection', {'policy':role+'-on-demand-v2'}),
            'visible_premise_ids':[x.get('record_id',x.get('id')) for x in (view.get('retrieved_legal_premises',[]) + view.get('recalled_facts',[]) + [r for page in view.get('recall_results',[]) for r in page.get('records',[])])]
        })+'\n', encoding='utf-8')
    return result


def diagnostic_page(feedbacks, ident, offset, *, size=1200):
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError('invalid diagnostic offset')
    def strings(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if k in {'diagnostic', 'residual', 'semantic_goal'} and isinstance(v, str): yield v
                else: yield from strings(v)
        elif isinstance(value, list):
            for v in value: yield from strings(v)
    for text in strings(feedbacks):
        if evidence_id(text) == ident:
            if offset >= len(text): raise ValueError('diagnostic offset out of range')
            return {'diagnostic_id': ident, 'offset': offset, 'text': text[offset:offset+size],
                    'total_chars': len(text), 'complete': offset == 0 and size >= len(text),
                    'next_offset': min(offset+size, len(text))}
    raise ValueError('diagnostic not present in this node feedback')


VIEW_INSTRUCTIONS = """
Excerpts marked complete=false are incomplete; never infer omitted goals are proved.
same_as/statement_ref refers to an existing field/node; context_ref indexes contexts.
GRA fixed_contracts IDs refer to complete current_graph statements. node_ids maps
short @ IDs to original IDs; use short IDs in output, Runner expands them exactly. Omitted facts may exist.
Prover may return {"observation_request":{"proof_state_query":true}} for goal/context retrieval from a v2 catalog, or {"observation_request":{"premise_query":"specific fact or definition"}}
or {"observation_request":{"diagnostic_id":"hash","offset":0}} instead of proof.
Provider assumptions may be read from its complete statement along consumer_path;
the conclusion is never an assumption. Requests consume an attempt; no arbitrary
file reads or deleted proofs.
"""
