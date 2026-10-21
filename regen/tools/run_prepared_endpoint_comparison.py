"""Execute the two reviewed endpoint prompts without rewriting their inputs."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.proof_commits import durable, freeze_evidence
from regen.infrastructure.llm_transcript import call_llm
from regen.infrastructure.audited_llm import model_settings
from regen.planning.proof_graph import parse_json_response


def main():
    prepared=Path('reports/regen/endpoint-local-diagnosis-001/prepared-comparison.json')
    out=Path('reports/regen/endpoint-input-comparison-001').resolve()
    if out.exists():raise ValueError('use a fresh output directory')
    config=json.loads(prepared.read_text());prompts={}
    for arm in config['arms']:
        text=Path(arm['prompt']).read_text()
        assert hashlib.sha256(text.encode()).hexdigest()==arm['sha256']
        prompts[arm['name']]=text
    payloads=[json.loads(text.split('INPUT:\n')[1]) for text in prompts.values()]
    assert {k:v for k,v in payloads[0].items() if k!='retrieved_legal_premises'}=={k:v for k,v in payloads[1].items() if k!='retrieved_legal_premises'}
    source=Path('reports/regen/projection-run-v3-002').resolve();prior=Path('reports/regen/input-repair-gpt6-001')
    run=json.loads((source/'run.json').read_text());snapshot=json.loads((prior/'manifest.json').read_text());graph=snapshot['graph'];proofs=snapshot['proofs']
    helper=json.loads((prior/'helper/result.json').read_text());proofs[helper['node_id']]=helper['proof_body'];graph['accepted_order'].append(helper['node_id'])
    key=payloads[0]['node']['id']
    durable(out/'manifest.json',{'prepared':config,'model_settings':model_settings(),'graph':graph,'proofs':proofs,'source_run':str(source),'campaign_acceptance':False,'model_calls_parallel':True},immutable=True)
    checker=IsabelleChecker(source,out/'isabelle',heaps_dir=source/'heaps',generated_dir=source/'generated',l4v_dir=Path(run['l4v']),semantic_workspace=Path(run['semantic_workspace']),isabelle_dir=Path(run['isabelle_dir']),image=run['image'],base_session=run['base_session'],base_import=run['base_import'],base_catalog=run['base_catalog'],timeout=300)
    checker.persistent=True
    results=[]
    def generate(label):
        return call_llm('codex',prompts[label],out/label/'candidate',model='gpt-6-astra',reasoning_effort='medium',timeout=180,clean_runtime=True,theory=graph['theory'],stage='prepared-input-comparison')
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures={label:pool.submit(generate,label) for label in prompts}
            for label,future in futures.items():
                record={'arm':label}
                try:
                    response=future.result();meta=json.loads((out/label/'candidate.meta.json').read_text())
                    assert meta['sealable_generation'] and meta['reasoning_effort']=='medium' and meta['model']=='gpt-6-astra'
                    proof=parse_json_response(response)['proof_body'];assert proof.strip()
                    check=freeze_evidence(checker.check_node(graph,{**proofs,key:proof},key))
                    record.update(proof_body=proof,check=check,status=check['verdict'],model_wall_ms=meta['wall_ms'])
                except Exception as error:record.update(status='error',error=str(error))
                durable(out/label/'result.json',record,immutable=True);results.append(record);durable(out/'results.json',results)
                print(label,record['status'],flush=True)
    finally:checker.close_prefix()

if __name__=='__main__':main()
