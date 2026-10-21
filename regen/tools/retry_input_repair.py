"""Bounded GPT-6 diagnostic after input repair; no campaign acceptance writes."""
import json
import hashlib
from copy import deepcopy
from pathlib import Path
from regen.execution.repository_execution import journal, package_for
from regen.execution.lemma_prover import prove_node
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.proof_commits import durable, freeze_evidence
from regen.infrastructure.audited_llm import model_settings


def main():
    source=Path('reports/regen/projection-run-v3-002').resolve()
    out=Path('reports/regen/input-repair-gpt6-001').resolve()
    if out.exists(): raise ValueError('output already exists')
    run=json.loads((source/'run.json').read_text())
    state=journal(source,run,package_for(run,'Kernel_Object_Access.thy')).replay()
    graph=deepcopy(state['graph']); proofs=deepcopy(state['proofs'])
    helper='node:Kernel_Object_Access:regen_lookupAround2_exact_hit'
    contract='contract:Refine.KHeap_R:getEndpoint_corres:841481e73259'
    graph['accepted_order']=graph['accepted_order'][:graph['accepted_order'].index(helper)]
    proofs={k:proofs[k] for k in graph['accepted_order']}
    root=Path(__file__).resolve().parents[2]
    durable(out/'manifest.json',{'source':str(source),'graph':graph,'proofs':proofs,
        'model_settings':model_settings(),'model':'gpt-6-astra','max_attempts':3,
        'campaign_acceptance':False,'setting':'regenerate helper then contract from repaired inputs; no old target proof supplied',
        'implementation':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for folder in ('execution','planning','infrastructure','tools') for p in (root/'regen'/folder).glob('*.py')}},immutable=True)
    for label,key in [('helper',helper),('contract',contract)]:
        dest=out/label
        checker=IsabelleChecker(source,dest/'isabelle',heaps_dir=source/'heaps',generated_dir=source/'generated',
            l4v_dir=Path(run['l4v']),semantic_workspace=Path(run['semantic_workspace']),
            isabelle_dir=Path(run['isabelle_dir']),image=run['image'],base_session=run['base_session'],
            base_import=run['base_import'],base_catalog=run['base_catalog'],timeout=300)
        result=prove_node(graph,key,proofs,checker=checker,premise_database=Path(run['premise_database']),
            output_dir=dest,provider='codex',model='gpt-6-astra',timeout=180,max_attempts=3,
            think_timeout=120)
        durable(dest/'result.json',freeze_evidence(result),immutable=True)
        print(label,result['status'],flush=True)
        if result['status']!='green':break
        proofs[key]=result['proof_body'];graph['accepted_order'].append(key)
    print('diagnostic complete',flush=True)

if __name__=='__main__':main()
