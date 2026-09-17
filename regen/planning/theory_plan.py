"""Theory-level repository proposal schema. Exact local proof DAGs belong to File Planner."""
import json
from regen.preparation.refine_campaign import sha256_json


def prompt(payload, feedback):
    schema={'schema_version':3,'ordering_rationale':'Explain the overall order and why independent theories are prioritized.',
        'theories':[{'name':'Generated_Operations','purpose':'Results this theory should establish',
            'grouping_reason':'Why these targets/results belong together; consider alternatives to original owners.',
            'contract_ids':['exact frozen contract ID'],
            'auxiliary_intents':[{'id':'shared_bridge','intent':'Describe a useful shared intermediate result, not an exact statement',
                                  'for_contracts':['exact frozen contract ID']}],
            'suggested_predecessors':[{'theory':'Generated_Foundation','reason':'What established results may help this theory'}],
            'premise_ids':['visible legal premise ID']}]}
    return ('Plan a repository of Isabelle proofs at THEORY level. Use only LEGAL INPUT; never read or reconstruct deleted human proofs.\n'
        'Distribute all fixed contracts exactly once, without modifying statements/context/attributes. Original owners are provenance only. '
        'Consider useful grouping, shared results, and cross-theory collaboration across the complete contract set. '
        'Output a theory DAG directly via suggested_predecessors. Each edge needs a concrete reason describing expected assistance. '
        'These are revisable scheduling suggestions, not mandatory premise use, imports, or cross-file helper commitments. '
        'Do not output contract/helper dependency edges, a local proof DAG, exact helper statements, proofs, tactics, or assumptions. '
        'File Planner alone refines exact local auxiliary statements and scheduling prerequisites. '
        'Auxiliary intents are optional coarse descriptions of shared results, linked to the fixed targets they serve; auxiliary-only theories are allowed. '
        'Zero auxiliary intents and zero predecessor edges are allowed when justified by the grouping/ordering rationale; never invent edges to fill a quota. '
        'Unproved intents and contracts are not premises. Failed auxiliaries may be revoked; fixed contracts may not. '
        'Every generated theory must carry contracts or auxiliary intents. Use unique simple theory names and intent IDs. '
        'The theories ARRAY ORDER is your explicit tie-break priority among currently ready theories. Dependencies override this priority. '
        'Explain that priority in ordering_rationale, including priorities when theories are independent. '
        'Only cite visible legal premise IDs; absence from lexical retrieval does not establish absence from the legal environment. '
        'Do not claim mathematical independence, successful proofs or complete semantic analysis from structural validation. '
        'Return JSON matching this schema (all fields required; lists may be empty):\n'+json.dumps(schema,ensure_ascii=False,sort_keys=True)
        +'\nPREVIOUS VALIDATION ERRORS:\n'+json.dumps(feedback or [],ensure_ascii=False)
        +'\nLEGAL INPUT:\n'+json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')))


def normalize(payload, proposal):
    from regen.planning.repository_planner import NAME, id_list, topological
    if set(proposal)!={'schema_version','theories','ordering_rationale'} or proposal['schema_version']!=3:
        raise ValueError('expected theory-level proposal schema 3; local proof nodes/edges are not accepted')
    def prose(value,label):
        if not isinstance(value,str) or not value.strip(): raise ValueError(f'{label} requires a nonempty rationale')
    prose(proposal['ordering_rationale'],'theory ordering')
    raw=proposal['theories']
    if not isinstance(raw,list) or not raw: raise ValueError('theories must be nonempty')
    contracts={c['contract_id']:c for c in payload['contracts']}
    premises={p['record_id'] for p in payload['legal_premises']}
    names=set();intent_ids=set();assigned=[];theories=[];edges=[]
    for rank,t in enumerate(raw):
        if not isinstance(t,dict) or set(t)!={'name','purpose','grouping_reason','contract_ids','auxiliary_intents','suggested_predecessors','premise_ids'}:
            raise ValueError('theory fields must match schema 3; no local nodes, proofs, or imports')
        name=t['name']
        if not isinstance(name,str) or not NAME.fullmatch(name) or name in names: raise ValueError('invalid/duplicate theory name')
        names.add(name);prose(t['purpose'],name+' purpose');prose(t['grouping_reason'],name+' grouping')
        ids=id_list(t['contract_ids'],name+' contracts')
        if not set(ids)<=contracts.keys(): raise ValueError('unknown contract ID')
        assigned+=ids
        if len({contracts[key]['name'] for key in ids})!=len(ids): raise ValueError('contract declaration name collision')
        cited=id_list(t['premise_ids'],name+' premises')
        if not set(cited)<=premises: raise ValueError('premise not visible in legal input')
        intents=t['auxiliary_intents']
        if not isinstance(intents,list): raise ValueError('auxiliary_intents must be a list')
        for intent in intents:
            if not isinstance(intent,dict) or set(intent)!={'id','intent','for_contracts'}: raise ValueError('auxiliary intent cannot contain a proof DAG or exact statement')
            key=intent['id']
            if not isinstance(key,str) or not NAME.fullmatch(key) or key in intent_ids: raise ValueError('invalid/duplicate auxiliary intent ID')
            intent_ids.add(key);prose(intent['intent'],'auxiliary intent')
            served=id_list(intent['for_contracts'],'intent targets')
            if not served or not set(served)<=contracts.keys(): raise ValueError('auxiliary intent must serve known fixed targets')
        if not ids and not intents: raise ValueError('theory has no targets or auxiliary intents')
        predecessors=t['suggested_predecessors']
        if not isinstance(predecessors,list): raise ValueError('theory predecessors must be a list')
        seen=set()
        for pred in predecessors:
            if not isinstance(pred,dict) or set(pred)!={'theory','reason'}: raise ValueError('theory edge requires provider and reason')
            provider=pred['theory']
            if not isinstance(provider,str) or provider==name or provider in seen: raise ValueError('self/duplicate/invalid theory predecessor')
            seen.add(provider);prose(pred['reason'],'theory edge')
            edges.append({'provider':provider,'consumer':name,'reason':pred['reason']})
        theories.append({**t,'file_slot':name+'.thy','priority_rank':rank,
                         'contracts':[contracts[key] for key in ids]})
    if sorted(assigned)!=sorted(contracts): raise ValueError('every fixed contract must occur exactly once')
    if any(e['provider'] not in names for e in edges): raise ValueError('unknown predecessor theory')
    priority={t['name']:t['priority_rank'] for t in theories}
    order,depths=topological(list(priority),[(e['provider'],e['consumer']) for e in edges],priority=priority.__getitem__)
    by_name={t['name']:t for t in theories}
    return {'theories':[by_name[name] for name in order], 'theory_order':order,'theory_depths':depths,
            'priority_order':[t['name'] for t in theories],'ordering_rationale':proposal['ordering_rationale'],
            'suggested_theory_edges':edges}


def build_bundle(payload,proposal):
    normalized=normalize(payload,proposal)
    body={'schema_version':3,'artifact_type':'repository-proof-plan','campaign_id':payload['campaign_id'],
        'environment_gate_id':payload['environment_gate_id'],'environment_dataflow_id':payload['environment_dataflow_id'],
        'planner_input_sha256':sha256_json(payload),'proposal_sha256':sha256_json(proposal),'policy_sha256':payload['policy_sha256'],
        'status':'proposed',**normalized,'semantics':{'local_proof_graph_included':False,'node_statements_certified':False,
            'suggested_edges_are_proof_dependencies':False,'generated_facts_published':False,'rationale_semantically_verified':False}}
    plan={**body,'plan_id':'repository-plan:'+sha256_json(body)[:20]}
    artifacts={'repository-plan.json':plan};files=[];by_name={t['name']:t for t in plan['theories']}
    premises={p['record_id']:p for p in payload['legal_premises']}
    for t in plan['theories']:
        projection={'schema_version':3,'artifact_type':'repository-file-projection','plan_id':plan['plan_id'],
            'environment_gate_id':plan['environment_gate_id'],'environment_dataflow_id':plan['environment_dataflow_id'],
            'theory':t['name'],'file_slot':t['file_slot'],'purpose':t['purpose'],'grouping_reason':t['grouping_reason'],
            'contracts':t['contracts'],'auxiliary_intents':t['auxiliary_intents'],'suggested_predecessors':t['suggested_predecessors'],
            'priority_rank':t['priority_rank'],'ordering_rationale':plan['ordering_rationale'],
            'suggested_providers':[{'theory':p['theory'],'purpose':by_name[p['theory']]['purpose'],
                'contract_ids':by_name[p['theory']]['contract_ids'],'auxiliary_intents':by_name[p['theory']]['auxiliary_intents'],
                'reason':p['reason'],'usable_as_premise':False} for p in t['suggested_predecessors']],
            'legal_premises':[premises[key] for key in t['premise_ids']], 'status':'requires-file-planning-and-Isabelle-checks'}
        filename='files/'+t['name']+'.json';artifacts[filename]=projection
        files.append({'theory':t['name'],'file_slot':t['file_slot'],'contract_ids':t['contract_ids'],
            'projection':filename,'projection_sha256':sha256_json(projection)})
    artifacts['file-generation-list.json']={'schema_version':4,'artifact_type':'repository-file-generation-list',
        'plan_id':plan['plan_id'],'environment_gate_id':plan['environment_gate_id'],'environment_dataflow_id':plan['environment_dataflow_id'],
        'files':files,'serial_fallback':plan['theory_order'],'priority_order':plan['priority_order'],
        'contract_count':len(payload['contracts']),'file_count':len(files),'next_module':'file-planning','execution_ready':False}
    return artifacts
