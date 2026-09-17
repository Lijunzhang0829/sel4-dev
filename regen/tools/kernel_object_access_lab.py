"""Isolated, resumable interactive proof lab over fixed contracts and legal frozen inputs."""
import argparse
import copy
import hashlib
import json
import time
from pathlib import Path
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.proof_commits import durable, freeze_evidence
from regen.planning.proof_graph import validate_graph, execution_order, graph_payload_sha256
from regen.tools.proof_lab_journal import replay_lab


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--timeout',type=int,default=900);args=parser.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    source=Path('reports/regen/projection-run-v3-002').resolve();prior=Path('reports/regen/input-repair-gpt6-001')
    run=json.loads((source/'run.json').read_text())
    if not (out/'initial.json').exists():
        initial=json.loads((prior/'manifest.json').read_text());graph=initial['graph'];proofs=initial['proofs'];h=json.loads((prior/'helper/result.json').read_text());proofs[h['node_id']]=h['proof_body'];graph['accepted_order'].append(h['node_id'])
        durable(out/'initial.json',{'source_run':str(source),'graph':graph,'proofs':proofs,'fixed_contracts':{n['id']:{k:n.get(k) for k in ('statement','context','role')} for n in graph['nodes'] if n['role']=='contract'},'setting':'developer/LLM collaborative experiment; final complete cold check required'},immutable=True)
        durable(out/'state.json',{'graph':graph,'proofs':proofs,'accepted':[]})
    durable(out/'state.json', replay_lab(out, check_pointer=False))
    frozen=json.loads((out/'initial.json').read_text())
    package={'repository_projection': True, 'contracts': [{'contract_id': n['id'], 'name': n['name'], 'exact_statement': n['statement'], 'contract_owned_context': n.get('context', [])} for n in frozen['graph']['nodes'] if n['role']=='contract']}
    checker=IsabelleChecker(source,out/'checks',heaps_dir=source/'heaps',generated_dir=source/'generated',l4v_dir=Path(run['l4v']),semantic_workspace=Path(run['semantic_workspace']),isabelle_dir=Path(run['isabelle_dir']),image=run['image'],base_session=run['base_session'],base_import=run['base_import'],base_catalog=run['base_catalog'],timeout=args.timeout);checker.persistent=True
    try:
        while not (out/'STOP').exists():
            requests=sorted((out/'requests').glob('*.json')) if (out/'requests').exists() else []
            for path in requests:
                result_path=out/'results'/path.name
                if result_path.exists():continue
                committed=out/'acceptance'/path.name
                if committed.exists():
                    record=json.loads(committed.read_text())
                    # Startup replay already recovered the latest committed state.
                    # Recreate a missing result without rewinding that pointer.
                    durable(result_path,record['result'],immutable=True)
                    continue
                req=json.loads(path.read_text());state=json.loads((out/'state.json').read_text());g=state['graph'];proofs=state['proofs'];result={'request':req,'process_timeout_seconds':args.timeout,'command_timeout_seconds':60}
                try:
                    validate_graph(g,package)
                    if req.get('operation')=='complete':
                        result['check']=freeze_evidence(checker.check_complete(g,proofs))
                    else:
                        key=req['node_id'];proof=req['proof_body']
                        if req.get('new_node'):
                            node=copy.deepcopy(req['new_node'])
                            assert node['id']==key and node['role']=='helper'
                            assert key not in {n['id'] for n in g['nodes']}
                            assert set(node['dependencies']) <= set(proofs)
                            g['nodes'].append(node)
                            for n in g['nodes']:
                                n['consumers']=[m['id'] for m in g['nodes'] if n['id'] in m['dependencies']]
                            g['parent_revision']=g['revision'];g['revision']+=1
                            g['source_decision']={'experiment_request':str(path)}
                            validate_graph(g,package)
                            g['execution_order']=execution_order(g)
                            g['graph_payload_sha256']=graph_payload_sha256(g)
                        assert key in {n['id'] for n in g['nodes']}
                        if key in proofs:raise ValueError('accepted proofs are immutable')
                        check=checker.check_node(g,{**proofs,key:proof},key);result['check']=freeze_evidence(check)
                        if check['verdict']=='green':
                            state['proofs'][key]=proof;state['graph']['accepted_order'].append(key);state['accepted'].append(path.name)
                            state['graph']['graph_payload_sha256']=graph_payload_sha256(state['graph'])
                            durable(out/'acceptance'/path.name,{'previous_state_sha256':hashlib.sha256((out/'state.json').read_bytes()).hexdigest(),'state':state,'result':result},immutable=True);durable(out/'state.json',state)
                    durable(result_path,result,immutable=True);print(path.name,result.get('check',{}).get('verdict'),flush=True)
                except Exception as e:
                    result['error']=repr(e);durable(result_path,result,immutable=True);print(path.name,'ERROR',repr(e),flush=True)
            time.sleep(.2)
    finally:checker.close_prefix()

if __name__=='__main__':main()
