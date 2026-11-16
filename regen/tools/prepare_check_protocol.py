"""Create a new checker context with the shared protocol; never edit a run.

Definitions, contracts and the legal parent database are unchanged. Only runtime
policy is added to the source seal. Owned-environment terms and parameter
instances are freshly checked and rebound; no benchmark proof is imported.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil

from regen.execution.check_protocol import descriptor, FILENAME
from regen.execution.check_executor import CURRENT_RESOURCES, NativeResources
from regen.execution.contract_refutation import check_export_scan
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.proof_commits import durable
from regen.execution.result_scope import environment_bundle
from regen.preparation.calg_environment_evidence import check_parameter_instances
from regen.preparation.checker_context import load_context
from regen.preparation.premise_database import digest
from regen.preparation.refine_campaign import sha256_json
from regen.tools.document_acceptance_validation import readonly_heaps


def prepare(source, output):
    source,output=Path(source).resolve(),Path(output).resolve()
    if output.is_relative_to(source):
        raise ValueError('new protocol input must be outside the old dataset')
    ctx=load_context(source)
    output.mkdir(parents=True,exist_ok=False)
    for p in source.iterdir():
        if p.is_file() and p.suffix in {'.json','.sqlite'}:
            shutil.copy2(p,output/p.name)
    assets=output/'checker-assets';assets.mkdir()
    semantic=assets/'semantic-base'
    shutil.copytree(ctx['semantic_workspace'],semantic,symlinks=True)
    (semantic/FILENAME).write_text(json.dumps(descriptor(),indent=2)+'\n')
    for name,key in (('l4v','l4v'),('heaps','heaps'),('isabelle','isabelle')):
        (assets/name).symlink_to(ctx[key],target_is_directory=True)
    core=json.loads((source/'checker-context.json').read_text())
    core.pop('checker_context_id')
    core['paths'].update(l4v='checker-assets/l4v',heaps='checker-assets/heaps',
                         isabelle='checker-assets/isabelle',semantic_workspace='checker-assets/semantic-base')
    core['semantic_files_sha256']={str(p.relative_to(semantic)):digest(p)
        for p in semantic.rglob('*') if p.is_file()}
    core['checker_context_id']='checker-context:'+sha256_json(core)[:20]
    (output/'checker-context.json').write_text(json.dumps(core,indent=2)+'\n')
    new=load_context(output)
    payload=json.loads((output/'repository-planning-input.json').read_text())
    resources=NativeResources(1,4,1,cpus=4,memory_mb=16384)
    token=CURRENT_RESOURCES.set(resources);checker=None
    try:
        checker=IsabelleChecker(output,output/'protocol-evidence/checks',
            image=new['image'],architecture=new['architecture'],timeout=300,
            generated_dir=output/'protocol-evidence/generated',heaps_dir=new['heaps'],
            l4v_dir=new['l4v'],semantic_workspace=new['semantic_workspace'],
            base_session=new['base_session'],base_import=new['base_import'],
            base_catalog=new['base_catalog'],isabelle_dir=new['isabelle'])
        original=checker._command
        checker._command=lambda *a,**kw:readonly_heaps(original(*a,**kw))
        scan=check_export_scan(checker,payload['contracts'],output/'protocol-evidence/export',
                               result_scope=new['result_scope'])
        if not scan['passed']:raise RuntimeError('owned-context export failed')
        instances=check_parameter_instances(checker,scan,output/'protocol-evidence/instances')
        if not instances['passed']:raise RuntimeError('environment parameter instances failed')
        bundle=environment_bundle(payload['contracts'],scan,new['checker_context_id'],
                                  parameter_instance_evidence=instances)
        (output/'result-scope.json').write_text(json.dumps(bundle,indent=2)+'\n')
        receipt=json.loads((source/'preparation.json').read_text())
        receipt.update(native_check_protocol=descriptor(),protocol_source_dataset=str(source),
            protocol_source_checker_context_id=ctx['checker_context_id'],
            checker_context_id=new['checker_context_id'],
            semantic_files=core['semantic_files_sha256'],
            environment_evidence_sha256=bundle['bundle_sha256'],
            parameter_instance_evidence_sha256=instances['evidence_sha256'],
            model_calls=0,target_proofs_added=0)
        (output/'preparation.json').write_text(json.dumps(receipt,indent=2)+'\n')
        result=dict(status='pass',native_check_protocol=descriptor(),
            checker_context_id=new['checker_context_id'],contracts=len(payload['contracts']),
            fixed_input_unchanged=True,parent_facts_unchanged=True,model_calls=0,
            benchmark_proofs_imported=0)
        durable(output/'protocol-preparation.json',result,immutable=True)
        return result
    finally:
        try:
            if checker is not None:checker.close_prefix()
            resources.close()
        finally:CURRENT_RESOURCES.reset(token)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(prepare(args.source,args.output)))
