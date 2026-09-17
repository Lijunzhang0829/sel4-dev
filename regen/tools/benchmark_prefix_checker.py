"""Replay saved generated candidates against the fixed-prefix checker; no model calls."""
import argparse
import json
import time
from pathlib import Path
from regen.execution.isabelle_node_checker import IsabelleChecker


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run',type=Path,default=Path('reports/regen/projection-run-v3-002'))
    parser.add_argument('--candidates-run',type=Path,default=Path('reports/regen/input-repair-gpt6-001'))
    parser.add_argument('--output',type=Path,default=Path('reports/regen/persistent-checker-benchmark-001'))
    args=parser.parse_args()
    source=args.source_run.resolve();previous=args.candidates_run.resolve();out=args.output.resolve()
    if out.exists(): raise ValueError('output exists')
    out.mkdir()
    run=json.loads((source/'run.json').read_text());snapshot=json.loads((previous/'manifest.json').read_text())
    graph=snapshot['graph'];proofs=snapshot['proofs']
    helper=json.loads((previous/'helper/result.json').read_text());proofs[helper['node_id']]=helper['proof_body'];graph['accepted_order'].append(helper['node_id'])
    result=json.loads((previous/'contract/result.json').read_text());key=result['node_id']
    c=IsabelleChecker(source,out,heaps_dir=source/'heaps',generated_dir=source/'generated',l4v_dir=Path(run['l4v']),semantic_workspace=Path(run['semantic_workspace']),isabelle_dir=Path(run['isabelle_dir']),image=run['image'],base_session=run['base_session'],base_import=run['base_import'],base_catalog=run['base_catalog'],timeout=300)
    c.persistent=True
    rows=[]
    try:
        for a in result['attempts']:
            start=time.monotonic();check=c.check_node(graph,{**proofs,key:a['proof_body']},key)
            row={'attempt':a['attempt'],'cold_previous_ms':a['check']['wall_ms'],'wall_ms':round((time.monotonic()-start)*1000),'check':check}
            rows.append(row);(out/'results.json').write_text(json.dumps(rows,indent=2));print(json.dumps(row),flush=True)
            assert check['verdict']==a['check']['verdict']
            assert check.get('persistent_prefix'), 'worker unexpectedly fell back'
    finally:c.close_prefix()

if __name__=='__main__':main()
