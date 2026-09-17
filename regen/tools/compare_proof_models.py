"""Small paired model experiment; never writes campaign acceptance journals."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.lemma_prover import proof_prompt
from regen.execution.proof_commits import digest, durable, freeze_evidence
from regen.infrastructure.audited_llm import model_settings
from regen.infrastructure.llm_transcript import call_llm
from regen.planning.premise_retrieval import retrieve_proof_premises
from regen.planning.proof_graph import parse_json_response


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-run',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();source=args.source_run.resolve();out=args.output.resolve()
    if out.exists():raise ValueError('use a new experiment directory')
    run=json.loads((source/'run.json').read_text())
    directory=source/'theories/Kernel_Object_Access'
    # Replay the source journal (including its embedded predecessor history).
    from regen.execution.repository_execution import journal, package_for
    state=journal(source,run,package_for(run,'Kernel_Object_Access.thy')).replay()
    graph=state['graph'];proofs=state['proofs'];settings=model_settings()
    helper='node:Kernel_Object_Access:regen_lookupAround2_exact_hit'
    contract='contract:Refine.KHeap_R:getEndpoint_corres:841481e73259'
    tasks=[]
    for name,key,rel in [
        ('lookup_exact_hit',helper,'node_Kernel_Object_Access_regen_lookupAround2_exact_hit/round-02'),
        ('endpoint_continuation',contract,'contract_Refine.KHeap_R_getEndpoint_corres_841481e73259/round-06')]:
        prompt=(directory/'nodes'/rel/'llm/call-001.prompt.txt').read_text()
        tasks.append((name,key,graph,proofs,prompt))
    node={'id':'diagnostic:unless_when_state','name':'diagnostic_unless_when_state','role':'helper',
        'statement':'lemma diagnostic_unless_when_state:\n  assumes first: "((), u) \\<in> fst (unless A fail s)"\n      and second: "((), t) \\<in> fst (when B fail u)"\n  shows "t = s"',
        'context':[],'dependencies':[],'consumers':[],
        'strategy':'Show successful unless/when computations with fail preserve the state. Keep the statement fixed.'}
    local={'nodes':[node],'execution_order':[node['id']],'context_policy':'committed-prefix-v1',
           'accepted_order':[],'theory':'Diagnostic','file_slot':'Diagnostic.thy'}
    premises=retrieve_proof_premises(Path(run['premise_database']),node['statement']+' unless_def when_def in_fail in_return',limit=32)
    tasks.insert(1,('unless_when_state',node['id'],local,{},proof_prompt(node,[],premises,None)))
    implementation={str(p.relative_to(Path(__file__).resolve().parents[2])):hashlib.sha256(p.read_bytes()).hexdigest()
                    for folder in ('execution','infrastructure','planning','tools')
                    for p in (Path(__file__).resolve().parents[1]/folder).glob('*.py')}
    manifest={'source_run':str(source),'source_head':json.loads((directory/'acceptance/HEAD.json').read_text()),
        'models':settings['comparison_models'],'reasoning_effort':'medium','model_timeout':180,
        'checker_timeout':300,'candidates_per_model_task':1,'model_settings':settings,
        'implementation':implementation,'campaign_acceptance':False,
        'endpoint_setting':'continuation with identical generated helper and prior generated failures; not scratch',
        'tasks':[{'name':name,'node_id':key,'graph':g,'proofs':pr,'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest()} for name,key,g,pr,prompt in tasks]}
    durable(out/'manifest.json',manifest,immutable=True);results=[]
    for index,(name,key,g,pr,prompt) in enumerate(tasks):
        durable(out/name/'input.json',{'prompt':prompt,'graph':g,'proofs':pr},immutable=True)
        models=list(settings['comparison_models'])
        if index%2:models.reverse()
        for model in models:
            dest=out/name/model;dest.mkdir(parents=True)
            record={'task':name,'model':model,'reasoning_effort':'medium','prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest()}
            try:
                response=call_llm('codex',prompt,dest/'candidate',model=model,timeout=180,
                    clean_runtime=True,reasoning_effort='medium',theory='Kernel_Object_Access',stage='paired-model-comparison')
                meta=json.loads((dest/'candidate.meta.json').read_text())
                if not meta.get('sealable_generation') or meta.get('codex_sha256')!=settings['codex_sha256']:
                    raise ValueError('model call differs from frozen audited runtime')
                record['model_wall_ms']=meta['wall_ms']
                candidate=parse_json_response(response);proof=candidate.get('proof_body','').strip()
                if not proof:raise ValueError('empty proof')
                checker=IsabelleChecker(source,dest/'isabelle',heaps_dir=source/'heaps',generated_dir=source/'generated',
                    l4v_dir=Path(run['l4v']),semantic_workspace=Path(run['semantic_workspace']),
                    isabelle_dir=Path(run['isabelle_dir']),image=run['image'],base_session=run['base_session'],
                    base_import=run['base_import'],base_catalog=run['base_catalog'],timeout=300)
                record['proof_body']=proof
                record['check']=freeze_evidence(checker.check_node(g,{**pr,key:proof},key))
                record['status']=record['check']['verdict']
            except Exception as error:
                record['status']='error';record['error']=f'{type(error).__name__}: {error}'
            durable(dest/'result.json',record,immutable=True);results.append(record)
            durable(out/'results.json',results)
            print(name,model,record['status'],flush=True)
    print('comparison complete',out,flush=True)


if __name__=='__main__':main()
