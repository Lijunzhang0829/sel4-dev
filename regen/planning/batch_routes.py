"""Batch reuse and observational route feedback; no partial GREEN inference."""
from copy import deepcopy
from collections import Counter
import re
from regen.execution.proof_commits import digest


def expand_reuse(directory, state, proposal):
    from regen.planning.planner_workspace import attempt_evidence
    out=deepcopy(proposal);selections=out.pop('reuse_proofs',[])
    if not isinstance(selections,list) or len(selections)>8:
        raise ValueError('reuse_proofs must contain at most 8 historical selections')
    current={n['id']:n for n in state['graph']['nodes']}
    proofs=out.setdefault('proofs',{})
    if not isinstance(proofs,dict):raise ValueError('proofs must be a map')
    for selection in selections:
        if not isinstance(selection,dict) or set(selection)!={'request_number','node_ids'}:
            raise ValueError('reuse_proofs entries require request_number and node_ids')
        prior,_=attempt_evidence(directory,state,selection['request_number'])
        # Reuse references are flattened before saving an executed proposal, so
        # history always contains the actual complete bodies and graph patch.
        ids=selection['node_ids']
        if not isinstance(ids,list) or not 1<=len(ids)<=16 or any(not isinstance(k,str) for k in ids):
            raise ValueError('reuse_proofs node_ids must be 1-16 exact IDs')
        previous_nodes={n['id']:n for n in prior.get('graph',{}).get('generated_nodes',[])}
        for key in ids:
            if key in proofs or key in state['proofs'] or key not in prior.get('proofs',{}):
                raise ValueError('duplicate, already GREEN or missing historical proof: '+key)
            graph=out.setdefault('graph',{});explicit={n['id']:n for n in graph.get('generated_nodes',[])}
            declaration=previous_nodes.get(key)
            active=explicit.get(key,current.get(key))
            if declaration and active and any(declaration.get(f,[])!=active.get(f,[]) for f in ('statement','context')):
                raise ValueError('historical proof declaration differs; use an explicit new candidate')
            if key not in current and key not in explicit:
                if declaration is None:raise ValueError('historical helper declaration unavailable')
                graph.setdefault('generated_nodes',[]).append(deepcopy(declaration))
            proofs[key]=prior['proofs'][key]
    if selections:
        out['reuse_origin']=[deepcopy(x) for x in selections]
    return out


def node_results(old, candidate, evidence):
    check=evidence['check'];new=[k for k in candidate['accepted_order'] if k not in old['proofs']]
    log=check.get('log_content','')
    started=re.findall(r'^REGEN_NODE (.+)$',log,re.M)
    ended=set(re.findall(r'^REGEN_TIMING_END (\S+) ',log,re.M))
    failed=next((k for k in reversed(started) if k in new and k not in ended),None)
    result=[]
    graph_failed=evidence.get('graph_check') is not None and evidence['graph_check'].get('verdict')!='green'
    for key in new:
        status=('audited_green' if check.get('verdict')=='green' else
                'declaration_rejected' if graph_failed else
                'completed_uncommitted' if key in ended else
                'failed' if key==failed else 'not_reached' if failed and key not in started else 'unknown')
        result.append({'node_id':key,'status':status,'accepted':False,
                       'notice':'Only an entirely GREEN batch may be committed. Completed commands are not independent proof audit.'})
    return result


def route_signals(state):
    history=state.get('batch_history',[]);proofs=[h for h in history if h.get('action')=='submit_batch' and h.get('proof_node_ids')]
    candidates=Counter();failures=Counter();seconds=Counter()
    for item in proofs:
        if item.get('candidate_signature'):candidates[item['candidate_signature']]+=1
        if item.get('failure_signature'):failures[item['failure_signature']]+=1
        seconds.update(item.get('cost_seconds',{}))
    nodes={n['id']:n for n in state['graph']['nodes']};green=set(state['proofs'])
    ready=[k for k in state['graph']['execution_order'] if k not in green and set(nodes[k].get('dependencies',[]))<=green]
    groups=[]
    for key in state['graph']['execution_order']:
        missing=[k for k in nodes[key].get('dependencies',[]) if k not in green]
        if key not in green and missing and all(k in ready for k in missing):
            groups.append({'consumer':key,'ready_helpers':missing,'suggested_order':[*missing,key]})
    return {'proof_batches':len(proofs),'single_node_batches':sum(len(h['proof_node_ids'])==1 for h in proofs),
            'multi_node_batches':sum(len(h['proof_node_ids'])>1 for h in proofs),
            'repeated_candidate_signatures':{k:v for k,v in candidates.items() if v>1},
            'repeated_failure_signatures':{k:v for k,v in failures.items() if v>1},
            'recorded_cost_seconds':dict(seconds),'cost_coverage_batches':sum('cost_seconds' in h for h in proofs),
            'ready_independent_nodes':ready,'helper_consumer_batches':groups,
            'notice':'Hints, not mandatory batching or iteration limits. Residual count changes are not a proof of progress. '
                     'Historical records without costs/signatures are not inferred to be free or distinct.'}
