#!/usr/bin/env python3
"""Validate a seal repair in isolation, using accepted proofs without generation.

Never modifies the source experiment, its dataset, proof journal, or budget.
The repaired dataset and native seal certificates remain preparation artifacts.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')


def source_hashes(source):
    paths=[source/'proof/run.json',source/'proof/experiment.json',source/'dataset/checker-context.json',
           source/'dataset/premise-audit-context.json',source/'application-manifest.json']
    for directory in [source/'proof/theories',source/'proof/effective-generation-budget']:
        paths += [p for p in directory.rglob('*') if p.is_file() and
                  ('acceptance' in p.parts or directory.name=='effective-generation-budget')
                  and not p.name.endswith('.lock')]
    return {str(p.relative_to(source)):digest(p) for p in sorted(set(paths))}


def stopped(source):
    receipt=json.loads((source/'generation-resume-001.json').read_text())
    output=subprocess.check_output(['systemctl','--user','show',receipt['unit'],
        '--property=ActiveState','--property=SubState','--property=MainPID'],text=True)
    state=dict(line.split('=',1) for line in output.splitlines() if '=' in line)
    if int(state.get('MainPID','0')) or state.get('ActiveState') in {'active','activating','deactivating'}:
        raise ValueError('source experiment must remain stopped')
    return {'unit':receipt['unit'],**state}


def prepare_dataset(source, root):
    from regen.preparation.intervaltree_dataset import premise_audit_context
    from regen.preparation.refine_campaign import sha256_json
    from regen.preparation.checker_context import load_context
    src=source/'dataset'; target=root/'dataset';target.mkdir(exist_ok=False)
    for path in src.iterdir():
        if path.is_file():shutil.copy2(path,target/path.name)
    assets=target/'checker-assets';assets.mkdir()
    for path in (src/'checker-assets').iterdir():
        (assets/path.name).symlink_to(path.resolve(),target_is_directory=True)
    (target/'native-evidence').symlink_to(src/'native-evidence',target_is_directory=True)
    payload=json.loads((target/'repository-planning-input.json').read_text())
    audit=target/'premise-audit-context.json'
    save(audit,premise_audit_context(payload['planning_input_id'],digest(src/'native-evidence/premises.xml')))
    context=json.loads((target/'checker-context.json').read_text())
    previous=context.pop('checker_context_id')
    context['premise_audit_context_sha256']=digest(audit)
    context['checker_context_id']='checker-context:'+sha256_json(context)[:20]
    save(target/'checker-context.json',context)
    save(target/'repair-provenance.json',{'original_dataset':str(src),
        'original_checker_context_id':previous,'new_checker_context_id':context['checker_context_id'],
        'changed_metadata':['campaign_id','premise_audit_context_sha256','checker_context_id'],
        'original_audit_sha256':digest(src/'premise-audit-context.json'),
        'new_audit_sha256':digest(audit), 'contracts_sha256':digest(target/'repository-planning-input.json'),
        'parent_database_sha256':digest(target/'parent-premises.sqlite'),
        'semantic_inputs_changed':False,'target_proofs_changed':False})
    return load_context(target)


def validate(source, root):
    sys.path.insert(0,str(root/'application'))
    from regen.tools.application_release import verify
    from regen.execution.shared_proof_workspace import SharedProofWorkspace
    from regen.execution.isabelle_node_checker import IsabelleChecker
    from regen.execution.proof_commits import digest as object_digest
    from regen.infrastructure.resource_budget import clone_heaps
    from regen.planning.batch_file_planner import seal_theory
    from regen.execution.check_executor import NativeResources,CURRENT_RESOURCES
    from regen.preparation.checker_context import load_context
    from regen.execution.check_tasks import NativePreparationError
    started=time.time()
    service=stopped(source)
    before=source_hashes(source)
    save(root/'source-before.json',before)
    verify(root/'application',root/'application-manifest.json')
    # Reproduce the old failure at the early gate, with no native/model work.
    try:load_context(source/'dataset')
    except NativePreparationError as error:
        if 'campaign_id' not in str(error):raise
        save(root/'old-context-rejection.json',{'status':'expected_infrastructure_error',
            'diagnostic':str(error),'native_calls':0,'model_calls':0})
    else:raise ValueError('old missing-campaign regression was not reproduced')
    context=prepare_dataset(source,root)
    workspace=SharedProofWorkspace(source/'proof')
    theory='Interval_Normalization'
    package=workspace._package(theory)
    snapshot=workspace._snapshot(theory)
    if snapshot is None:raise ValueError('missing accepted source proofs')
    state=snapshot['state']
    if len(state['proofs']) != 13 or len(package['contracts']) != 13:
        raise ValueError('unexpected source contract coverage')
    imports=json.loads((Path(snapshot['directory'])/'imports.json').read_text())
    if imports:raise ValueError('validation currently expects this independent first theory')
    out=root/'native-validation';out.mkdir(exist_ok=False)
    clone_heaps(context['heaps'],out/'heaps')
    checker=IsabelleChecker(out,out/'checks',image=context['image'],timeout=900,
        generated_dir=out/'generated',heaps_dir=out/'heaps',l4v_dir=context['l4v'],
        semantic_workspace=context['semantic_workspace'],base_session=context['base_session'],
        base_import=context['base_import'],base_catalog=context['base_catalog'],isabelle_dir=context['isabelle'])
    runtime={**workspace.run,'premise_snapshot':str(context['premise_audit_context_path'])}
    resources=NativeResources(1,2,1,cpus=4,memory_mb=8192,pids_limit=1024,
        provider_build_timeout=900,build_ml_heap_mb=2048)
    token=CURRENT_RESOURCES.set(resources)
    try:
        sealed,evidence=seal_theory(out,runtime,package,out,checker,[],deepcopy(state))
        save(out/'seal-evidence.json',evidence)
        if sealed is None:raise RuntimeError('native seal failed: '+str(evidence.get('stage')))
        save(out/'sealed-state.json',sealed)
    finally:
        try:checker.close_prefix()
        finally:
            CURRENT_RESOURCES.reset(token);resources.close()
    # Native certificates have already passed validate_seal inside seal_theory.
    if sealed['proofs'] != state['proofs'] or sealed['graph'] != state['graph']:
        raise ValueError('repair changed accepted mathematical content')
    after=source_hashes(source)
    if before != after:raise ValueError('source experiment changed during repair validation')
    summary={'status':'pass','started_unix':started,'finished_unix':time.time(),
        'original_experiment':str(source),'repair_directory':str(root),'theory':theory,
        'validated_contracts':13,'source_commit_id':snapshot['commit_id'],
        'proofs_sha256':object_digest(state['proofs']),'graph_sha256':object_digest(state['graph']),
        'complete_check':evidence['complete']['verdict'],
        'native_dependency_audit':evidence['dependencies']['verdict'],
        'semantic_impact':evidence['impact']['verdict'],
        'native_sealed_build':evidence['build']['verdict'],
        'seal_certificate_replay':'pass','validation_seal_id':sealed['seal_id'],
        'source_unchanged':True,'source_files_verified':len(before),
        'published_to_experiment':False,'model_calls':0,'new_target_proofs':0,
        'experiment_resumed':False,'service':stopped(source)}
    save(root/'validation.json',summary)
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--repair',type=Path,required=True)
    args=parser.parse_args()
    validate(args.source.resolve(),args.repair.resolve())
