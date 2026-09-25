"""Task-bound observation memory, ordered independently of proof decisions."""
from copy import deepcopy
import json
from pathlib import Path
from regen.execution.proof_commits import digest

SELECTIONS = {
    'read_facts':('batch_requested_fact_ids',),
    'read_queries':('batch_requested_queries',),
    'read_attempts':('batch_requested_attempts',),
    'read_diagnostic':('batch_diagnostic_page','batch_diagnostic_pages'),
    'read_proof_state':('batch_requested_proof_state','batch_requested_proof_states'),
    'resolve_symbols':('batch_symbols','batch_symbol_observations'),
    'read_proofs':('batch_examples',),
}


def task_order(row):
    return [row.get('submission_sequence',0),row['submitted_at'],row['task_id']]


def merge(state, rows, directory):
    from regen.planning.batch_file_planner import apply_feedback
    new=deepcopy(state)
    facts={f['record_id']:f for f in new.get('batch_facts',[])}
    queries={q['query_id']:q for q in new.get('async_queries',[])}
    versions=new.setdefault('async_selection_versions',{})
    applied=set(new.get('async_observation_tasks',[]))
    def collect(proposal,result,row,path=()):
        if result.get('status')!='available':return set()
        touched=set(SELECTIONS.get(proposal.get('action'),()))
        for index,item in enumerate(result.get('observations',[])):
            touched.update(collect(item['request'],item['result'],row,(*path,index)))
        if result.get('kind')=='fact_selection':
            facts.update({f['record_id']:deepcopy(f) for f in result.get('records',[])})
            identity=result['query_identity'];previous=queries.get(identity,{})
            tasks=list(dict.fromkeys([*previous.get('task_ids',[]),row['task_id']]))
            if task_order(row)>=previous.get('observation_order',[]):
                queries[identity]={'query_id':identity,'request':proposal.get('request',{}),
                    'requests':[], 'task_ids':tasks,'identity_bound':True,'last_status':'available',
                    'returned_record_ids':[f['record_id'] for f in result.get('records',[])],
                    'observation_order':task_order(row),'cache':{'task_id':row['task_id'],
                        'result_path':str(Path(directory)/row['task_id']/'result.json'),
                        'result_sha256':row['result_sha256'],'observation_path':list(path)}}
            else:previous['task_ids']=tasks
        if 'workspace' in proposal:touched.add('batch_workspace')
        if 'notes' in proposal:touched.add('batch_notes')
        return touched
    for row in sorted(rows,key=task_order):
        if row['task_id'] in applied:continue
        proposal=row['request']['proposal'];result=row['result']
        touched=collect(proposal,result,row)
        new['batch_facts']=list(facts.values())
        # Reuse the normal observation reducer, discarding its action-history and
        # proof-request counters. Only explicitly touched selections are merged.
        request=max([new.get('batch_requests',0),*[v for k,v in new.items()
            if (k.endswith('_request_number') or k.endswith('_request')) and type(v) is int]])+1
        clean=deepcopy(proposal)
        def prune_workspace(value):
            if isinstance(value,dict):
                if 'workspace' in value:
                    from regen.planning.planner_workspace import validate_workspace
                    try:validate_workspace(new,value['workspace'])
                    except ValueError:value.pop('workspace')
                for item in value.values():prune_workspace(item)
            elif isinstance(value,list):
                for item in value:prune_workspace(item)
        prune_workspace(clean)
        clean_result=deepcopy(result)
        def prune_reads(value):
            for item in value.get('observations',[]):
                prune_workspace(item['request']);prune_reads(item['result'])
        prune_reads(clean_result)
        reduced=apply_feedback(new,clean,clean_result,request,observation_order=task_order(row))
        order=task_order(row)
        for key in touched:
            if key in reduced and order>=versions.get(key,[]):
                new[key]=deepcopy(reduced[key]);versions[key]=order
        applied.add(row['task_id'])
    new['batch_facts']=list(facts.values());new['async_queries']=list(queries.values())
    new['async_observation_tasks']=sorted(applied)
    return new


def cached_query(state, identity):
    for query in state.get('async_queries',[]):
        if query['query_id']!=identity:continue
        ref=query.get('cache',{})
        try:
            result=json.loads(Path(ref['result_path']).read_text())
            if digest(result)!=ref['result_sha256']:continue
            for index in ref['observation_path']:result=result['observations'][index]['result']
            if result.get('query_identity')==identity and result.get('status')=='available' and result.get('kind')=='fact_selection':
                return result,ref['task_id']
        except (OSError,ValueError,KeyError,IndexError,TypeError):pass
    return None,None
