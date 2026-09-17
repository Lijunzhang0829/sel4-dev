"""Minimal GRA observations; visibility does not restrict revision authority."""
from regen.execution.proof_input import failure_observation
from regen.execution.observation_context import round_summaries


def node_view(node, green_ids):
    return {**{k:node[k] for k in ('id','name','role','statement','context','dependencies') if k in node},
            'status':'green' if node['id'] in green_ids else 'unproved'}


def gra_view(payload):
    graph=payload['current_graph']; by_id={n['id']:n for n in graph['nodes']}
    red=payload['failed_node']; context=payload.get('decision_context',{})
    target=context.get('failed_statement_node_id') or red.get('node_id')
    visible={target} if target in by_id else set()
    if target in by_id:
        visible.update(by_id[target].get('dependencies',[]))
        visible.update(n['id'] for n in graph['nodes'] if target in n.get('dependencies',[]))
        consumers={target}
        while True:
            expanded=consumers | {n['id'] for n in graph['nodes'] if consumers.intersection(n.get('dependencies',[]))}
            if expanded==consumers: break
            consumers=expanded
        visible.update(k for k in consumers if by_id[k]['role']=='contract')
    green=set(payload.get('green_node_ids',[]))
    attempts=[]
    for item in red.get('attempts',[]):
        check=item.get('check') or {}
        record={'attempt':item.get('attempt'),'proof_body':item.get('proof_body',''),
                'verdict':check.get('verdict'),'isabelle_called':check.get('isabelle_called',True),
                'feedback':failure_observation(check.get('diagnostic',''))}
        if record not in attempts: attempts.append(record)
    rules={k:v for k,v in context.items() if k in {
        'phase','failed_statement_node_id','proof_round','proof_rounds_used',
        'proof_retry_limit','context_retry_rounds_remaining','retry_proof_allowed',
        'delete_only_forbidden','auxiliary_failure_policy','split_policy',
        'trusted_framework_semantic_evidence'}}
    view={'failed_node_id':target,'local_graph':[node_view(n,green) for n in graph['nodes'] if n['id'] in visible],
          'graph_revision':graph.get('revision'),'other_node_count':len(by_id)-len(visible),
          'recent_attempts':attempts[-1:],
          'previous_rounds':round_summaries(red.get('previous_round_summaries',[])),
          'failure_feedback':{'residual':failure_observation(red.get('residual',''))},
          'constraints':rules,'decision_policy':payload.get('decision_policy',{})}
    from regen.execution.observation_context import attempt_brief
    view['attempt_summaries']=[attempt_brief(x) for x in red.get('attempts',[])[:-1]]
    if not attempts and red.get('checked_history'):
        baseline=red['checked_history'][-1]
        view['repair_baseline']={'proof_body':baseline.get('proof_body',''),
                                'feedback':failure_observation(baseline.get('check',{}).get('diagnostic',''))}
    from regen.execution.proof_feedback import semantic_goal
    if not semantic_goal(str(red.get('residual',''))):
        for item in reversed(red.get('checked_history',[])+red.get('attempts',[])):
            check=item.get('check') or {}
            diagnostic=check.get('diagnostic','')
            if isinstance(diagnostic,str) and check.get('isabelle_called') is not False and semantic_goal(diagnostic):
                view['failure_feedback']['residual']=failure_observation(diagnostic)
                break
    history=context.get('prior_node_decisions',[])
    if history:
        view['previous_decision']={k:history[-1][k] for k in ('decision','operation','failure_class','reason','proof_round','retry_directive') if k in history[-1]}
    rejections=context.get('previous_revision_rejections')
    if rejections:
        rejection=rejections[-1] if isinstance(rejections,list) else rejections
        proposal=rejection.get('proposal',{})
        view['previous_revision_rejection']={
            'candidate_revision':rejection.get('candidate_revision'),
            'base_revision':rejection.get('base_revision'),
            'proposal':{k:proposal[k] for k in ('decision','operation','generated_nodes','contract_dependencies','contract_strategies','raw_response') if k in proposal},
            'feedback':failure_observation(rejection.get('check',{}).get('diagnostic','')),
            'committed':False}

    from regen.execution.observation_context import working_context
    view.update(working_context(payload.get('observations',[])))
    view['interaction_state']=red.get('interaction_state',{})
    frozen=any(p.get('kind')=='fact_selection' and p.get('status')=='frozen' for p in payload.get('observations',[]))
    view['gra_interaction_state']={'facts_frozen':frozen,'fact_batches_remaining':int(not frozen),'queries_remaining':max(0,7-len(payload.get('observations',[]))),
                                   'must_decide':len(payload.get('observations',[]))>=7}
    view['prover_query_count']=len(red.get('observation_requests',[]))
    for k in ('failure_class','failure_scope','early_handoff'):
        if k in red: view['failure_feedback'][k]=red[k]
    return view


def graph_page(graph, green_ids, request):
    if not isinstance(request,dict):
        raise ValueError('graph_query must be an object')
    ids=request.get('node_ids',[]); query=request.get('query','')
    offset=request.get('offset',0)
    if not isinstance(ids,list) or not all(isinstance(x,str) for x in ids) or not isinstance(query,str):
        raise ValueError('invalid graph query')
    if not isinstance(offset,int) or isinstance(offset,bool) or offset<0:
        raise ValueError('invalid graph offset')
    records=[node_view(n,green_ids) for n in graph['nodes']
             if (not ids or n['id'] in ids) and (not query or query.casefold() in
                 (n['id']+' '+n['name']+' '+n['statement']).casefold())]
    page=records[offset:offset+5]
    return {'kind':'graph','nodes':page,'matched_nodes':len(records),
            'next_offset':offset+len(page) if offset+len(page)<len(records) else None}
