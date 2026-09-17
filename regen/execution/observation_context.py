"""Lossless working set for facts and observations explicitly requested by a model."""
import copy


def working_context(pages, facts=()):
    records={x['record_id']:copy.deepcopy(x) for x in facts}
    nodes={}; results=[]
    for page in pages:
        summary=copy.deepcopy(page)
        if 'records' in summary:
            entries=summary.pop('records')
            for x in entries: records[x['record_id']]=x
            summary['record_refs']=[x['record_id'] for x in entries]
        if 'nodes' in summary:
            entries=summary.pop('nodes')
            for x in entries: nodes[x['id']]=x
            summary['node_refs']=[x['id'] for x in entries]
        if summary not in results: results.append(summary)
    view={}
    if records: view['recalled_facts']=list(records.values())
    if nodes: view['observed_nodes']=list(nodes.values())
    if results: view['recall_results']=results
    return view


def prior_round_summary(feedback):
    if not feedback: return []
    current={k:feedback[k] for k in ('round_id','status','failure_scope','failure_class','early_handoff') if k in feedback}
    current['attempt_count']=len(feedback.get('attempts',feedback.get('proof_candidates',[])))
    current['feedback']=diagnostic_brief(feedback.get('residual',''))
    current['queries']=len(feedback.get('observation_requests',[]))
    current['attempts']=[attempt_brief(x) for x in feedback.get('attempts',[])][-5:]
    history=round_summaries(feedback.get('previous_round_summaries',[]))
    if current not in history: history.append(current)
    return history[-3:]


def restore_working_set(feedback, local_facts, generated_records):
    """Revalidate recalled generated facts against the current accepted context."""
    import json
    feedback=feedback or {}
    local={x['id']:x for x in local_facts}
    sealed={}
    if generated_records and generated_records.is_file():
        sealed={x['record_id']:x for x in json.loads(generated_records.read_text()).get('records',[])
                if x.get('theory_seal_id')}
    records={x['record_id']:x for x in feedback.get('observation_facts',[])}
    for page in feedback.get('observation_pages',[]):
        records.update({x['record_id']:x for x in page.get('records',[])})
    valid={}; rejected=[]
    for key,fact in records.items():
        source=fact.get('source_class')
        if source=='same_theory_green':
            current=local.get(key)
            okay=bool(current and current['statement']==fact['exact_statement']
                      and current.get('context',[])==fact.get('context',[])
                      and (not fact.get('proof_sha256') or fact['proof_sha256']==current.get('proof_sha256')))
        elif source=='generated_theorem':
            current=sealed.get(key)
            okay=bool(current and current['theory_seal_id']==fact.get('theory_seal_id')
                      and current['exact_statement']==fact['exact_statement'])
        else:
            okay=source in {'frozen_parent','retained_semantic_export'}
        if okay: valid[key]=copy.deepcopy(fact)
        else: rejected.append(key)
    pages=copy.deepcopy(feedback.get('observation_pages',[]))
    for page in pages:
        if 'records' in page:
            removed=[x['record_id'] for x in page['records'] if x['record_id'] not in valid]
            page['records']=[valid[x['record_id']] for x in page['records'] if x['record_id'] in valid]
            if removed: page['unavailable_record_ids']=removed
    return list(valid.values()),pages,rejected


def attempt_brief(attempt):
    """Describe a past tactic attempt without copying its proof or goal dump."""
    import hashlib
    import re
    proof=attempt.get('proof_body') or ''
    check=attempt.get('check') or {}
    # This is a lexical outline, not a claim that a particular tactic succeeded.
    methods=list(dict.fromkeys(re.findall(
        r'\b(?:simp|simp_all|auto|blast|fastforce|clarsimp|rule|erule|cases|induct|metis|smt|corres|tactic)\b', proof)))
    return {'attempt':attempt.get('attempt'), 'methods':methods,
            'proof_sha256':hashlib.sha256(proof.encode()).hexdigest(),
            'verdict':check.get('verdict'), 'isabelle_called':check.get('isabelle_called',True),
            'feedback':diagnostic_brief(check.get('diagnostic',''))}


def diagnostic_brief(text):
    """Index an older failure by category/symbols; full diagnostics stay pageable."""
    import re
    from regen.execution.proof_input import evidence_id
    if isinstance(text,dict):
        return {k:copy.deepcopy(text[k]) for k in ('diagnostic_ref','categories','symbols','complete') if k in text}
    text=str(text or '')
    categories=[name for name,pattern in (
        ('syntax',r'malformed|syntax error|outer syntax'),
        ('unknown_name',r'undefined|unknown (?:fact|constant|type|name)'),
        ('type_error',r'type (?:unification|clash|error)|ill-typed'),
        ('timeout',r'timeout|timed out'),
        ('proof_failure',r'failed|unsolved|goal')) if re.search(pattern,text,re.I)]
    cleaned=re.sub(r'\\<[^>]+>', ' ', text)
    words=re.findall(r"[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*",cleaned)
    ignore={'the','and','at','line','command','goal','goals','subgoal','subgoals','Failed','to','apply','proof','method','state'}
    symbols=list(dict.fromkeys(w for w in words if w not in ignore and len(w)>1))[:12]
    return {'diagnostic_ref':evidence_id(text),'categories':categories,'symbols':symbols,'complete':False}


def round_summaries(history):
    """Sanitize inherited summaries, including records written by older runners."""
    result=[]
    for item in history[-3:]:
        value={k:copy.deepcopy(item[k]) for k in ('round_id','status','failure_scope','failure_class','early_handoff','attempt_count','queries') if k in item}
        value['feedback']=diagnostic_brief(item.get('feedback',''))
        value['attempts']=[]
        for attempt in item.get('attempts',[])[-5:]:
            brief={k:copy.deepcopy(attempt[k]) for k in ('attempt','methods','proof_sha256','verdict','isabelle_called') if k in attempt}
            brief['feedback']=diagnostic_brief(attempt.get('feedback',''))
            value['attempts'].append(brief)
        result.append(value)
    return result
