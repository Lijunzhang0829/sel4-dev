"""Check contextual target-export coverage without model calls or target proofs."""
import argparse
import json
from pathlib import Path

from regen.execution.contract_refutation import check_export_scan, check_refutation
from regen.execution.proof_commits import durable
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.check_executor import CURRENT_RESOURCES, NativeResources
from regen.preparation.checker_context import load_context


def run(dataset, output, *, check_arch_probes=False):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    context=load_context(Path(dataset))
    payload=json.loads((Path(dataset)/'repository-planning-input.json').read_text())
    resources=NativeResources(1,2,1,cpus=2,memory_mb=12288)
    token=CURRENT_RESOURCES.set(resources)
    checker=IsabelleChecker(output,output/'isabelle',image=context['image'],
        architecture=context['architecture'],timeout=300,heaps_dir=context['heaps'],
        l4v_dir=context['l4v'],semantic_workspace=context['semantic_workspace'],
        base_session=context['base_session'],base_import=context['base_import'],
        base_catalog=context['base_catalog'],isabelle_dir=context['isabelle'],generated_dir=output/'generated')
    command=checker._command
    def readonly(*args,**kwargs):
        return [v+',readonly' if v.startswith('type=bind,') and v.endswith('dst=/root/.isabelle/heaps')
                else v for v in command(*args,**kwargs)]
    checker._command=readonly
    try:
        result=check_export_scan(checker,payload['contracts'],output/'export-scan')
        if check_arch_probes:
            scopes={tuple(c.get('contract_owned_context',[])) for c in payload['contracts']}
            scopes=[scope for scope in scopes if all(part=='context begin interpretation Arch .' for part in scope)]
            probes=[]
            for number,scope in enumerate(sorted(scopes,key=len)):
                fixture={'contract_id':f'synthetic-export-probe-{number}',
                         'exact_statement':'lemma synthetic_probe: "False"',
                         'contract_owned_context':list(scope)}
                checked=check_refutation(checker,fixture,'by simp',output/f'synthetic-probe-{number}')
                probes.append({'context':list(scope),'synthetic_proposition':'False',
                               'status':checked['status'],'passed':checked['status']=='refuted'})
            result['synthetic_arch_probes']=probes
            result['passed']=result['passed'] and bool(probes) and all(p['passed'] for p in probes)
        durable(output/'report.json',result,immutable=True)
        print(json.dumps({k:v for k,v in result.items() if k not in {'records','native_check'}},indent=2))
        return result
    finally:
        checker.close_prefix();CURRENT_RESOURCES.reset(token);resources.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--check-arch-probes',action='store_true',
                        help='Also refute authored False fixtures in the observed empty/Arch contexts; no benchmark target is proved')
    args=parser.parse_args()
    raise SystemExit(0 if run(args.dataset,args.output,check_arch_probes=args.check_arch_probes)['passed'] else 1)
