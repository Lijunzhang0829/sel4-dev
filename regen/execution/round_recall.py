"""One bounded, explicit fact-selection transaction per proof/GRA round."""
import json
from pathlib import Path

MAX_QUERIES = 4
MAX_FACTS = 12


def is_fact_request(request):
    return isinstance(request, dict) and ('premise_query' in request or 'premise_queries' in request or 'proof_state_query' in request)


def frozen_response():
    return {'kind':'fact_selection', 'status':'rejected_frozen', 'records':[],
            'notice':'This round fact set is frozen. Use its facts or request diagnostics; choose new facts next round.'}


def select_round_facts(database, request, *, generated_records=None, local_facts=(), audit_path, symbol_resolver=None, proof_state=None, vector_index=None):
    from regen.execution.premise_recall import recall_premises
    if 'proof_state_query' in request:
        if request['proof_state_query'] is not True or any(k in request for k in ('premise_query','premise_queries')):
            raise ValueError('proof_state_query must be true and used alone')
        if proof_state is None:
            raise ValueError('trusted harness proof state unavailable')
        from regen.execution.proof_state_recall import recall_for_proof_state
        if request.get('use_vectors', False):
            vector_index = vector_index or Path(database).parent / 'premise-vectors.sqlite'
            if not Path(vector_index).is_file():
                raise ValueError('requested vector index unavailable')
        page = recall_for_proof_state(database, proof_state, limit=5, vector_index=vector_index)
        audit = {'policy':'one-batch-per-round-v1','request':request,'pages':[page],
                 'selected_record_ids':[r['record_id'] for r in page['records']]}
        path=Path(audit_path);path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('x') as stream: json.dump(audit,stream,ensure_ascii=False,indent=2)
        return {'kind':'fact_selection','status':'frozen','policy':audit['policy'],
                'records':page['records'],'queries':[{'mode':'proof_state','proof_state_sha256':page['proof_state_sha256']}],
                'notice':'Frozen candidates from the trusted goal/context; verify references in Isabelle.'}
    if 'premise_query' in request and 'premise_queries' in request:
        raise ValueError('use premise_query or premise_queries, not both')
    queries = request.get('premise_queries')
    if queries is None:
        queries = [{'query':request.get('premise_query'),
                    **{k:request[k] for k in ('mode','scope','offset') if k in request}}]
    if not isinstance(queries,list) or not 1 <= len(queries) <= MAX_QUERIES:
        raise ValueError('one recall batch must contain 1 to 4 queries')
    pages=[]
    for query in queries:
        if not isinstance(query,dict): raise ValueError('recall query must be an object')
        pages.append(recall_premises(database,query.get('query'),
            generated_records=generated_records,local_facts=local_facts,
            scope=query.get('scope','all'),mode=query.get('mode','auto'),offset=query.get('offset',0)))
    # Resolve only explicitly requested definition needs. This is part of this
    # single selection transaction, not a later round-internal fact addition.
    from regen.execution.premise_recall import _NAME, _DEF_SUFFIX
    needs={i:_DEF_SUFFIX.sub('',q['query'].strip()) for i,q in enumerate(queries)
           if symbol_resolver is not None and q.get('scope','all')!='local'
           and _NAME.fullmatch(q['query'].strip())
           and (q.get('mode','auto')=='definition' or
                q.get('mode','auto')=='auto' and pages[i].get('effective_mode')!='exact')}
    resolutions=symbol_resolver(list(dict.fromkeys(needs.values()))) if needs else {}
    for index,name in needs.items():
        resolution=resolutions[name]
        pages[index]['symbol_resolution']=resolution
        # A genuine expansion may contain definitions of several constants.
        # Retrieve only catalog facts; the elaborated expression is not a theorem.
        if not pages[index]['records'] and resolution['status']=='resolved':
            records={}
            constants=resolution['constant_names']
            pages[index]['definition_constants_considered']=constants[:4]
            pages[index]['definition_constants_omitted']=constants[4:]
            for constant in constants[:4]:
                found=recall_premises(database,constant,generated_records=generated_records,
                    local_facts=local_facts,scope=queries[index].get('scope','all'),mode='definition')
                for record in found['records']:records.setdefault(record['record_id'],record)
            pages[index]['records']=list(records.values())[:5]
            pages[index]['status']='resolved_definition_candidates' if records else 'resolved_without_catalog_definition'
    # Rank within each focused query, then interleave so the first query cannot
    # consume the complete working set. Unselected candidates stay in audit only.
    selected={}
    for rank in range(5):
        for page in pages:
            if rank<len(page['records']) and len(selected)<MAX_FACTS:
                record=page['records'][rank]
                selected.setdefault(record['record_id'],record)
    audit={'policy':'one-batch-per-round-v1','request':request,'pages':pages,
           'selected_record_ids':list(selected),'max_queries':MAX_QUERIES,'max_facts':MAX_FACTS}
    path=Path(audit_path);path.parent.mkdir(parents=True,exist_ok=True)
    # One selection per invocation; never overwrite prior evidence.
    with path.open('x') as stream: json.dump(audit,stream,ensure_ascii=False,indent=2)
    return {'kind':'fact_selection','status':'frozen','policy':audit['policy'],
            'records':list(selected.values()),
            'queries':[{**{k:p[k] for k in ('query','scope','mode','effective_mode','status',
                       'exact_match_found','matched_candidates','lookup_results','symbol_resolution',
                       'definition_constants_considered','definition_constants_omitted') if k in p},
                       'selected_record_ids':[x['record_id'] for x in p['records'] if x['record_id'] in selected]}
                       for p in pages],
            'notice':'Complete selected statements, fixed for this round. Other candidates and old facts remain in audit only.'}
