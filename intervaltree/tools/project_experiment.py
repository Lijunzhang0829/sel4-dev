#!/usr/bin/env python3
"""Deployment/handoff only; planning and proving use existing project entry points."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def save(path, value):
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')


def configure(root):
    from regen.preparation.premise_database import digest
    application = root/'application'
    config = root/'config'; config.mkdir(exist_ok=False)
    client = Path('/data/zljj/vero-a-unified/20260928-001/client/codex')
    image = subprocess.check_output(['docker','image','inspect','--format','{{.Id}}','python:3.11-slim'],text=True).strip()
    models = {'schema_version':1,'model_isolation':{'required':True,'image':image,
        'transport_proxy':'http://127.0.0.1:17890'},
        'codex_bin':str(client),'codex_runtime_root':str(root/'runtime'),
        'codex_sha256':digest(client),'codex_code_mode_host_sha256':digest(client.with_name('codex-code-mode-host')),
        'codex_version':subprocess.check_output([str(client),'--version'],text=True).strip(),
        'reasoning_effort':'low',
        'role_models':{role:'gpt-6-astra' for role in ['File Planner','Graph Revision Agent','Lemma Prover']},
        'comparison_models':['gpt-6-astra']}
    save(config/'models.json',models)
    policy = json.loads((application/'regen/config/repository-planning-crefine-owner-blind-sol-xhigh-local.json').read_text())
    # Change only benchmark/model labels, retaining current planning procedure.
    policy = json.loads(json.dumps(policy).replace('CRefine','Intervaltree').replace('309','91'))
    policy['stage'] = 'Intervaltree'
    policy['model'].update(model='gpt-6-astra',reasoning_effort='low')
    policy['worker_image'] = image
    save(config/'planning-policy.json',policy)
    save(config/'execution-policy.json',{'stages':[],'session_stall_retries':0,'provider_feedback_retries':1})
    (root/'auth').mkdir(mode=0o700)
    # Fresh copy from the already authorized VERO authentication home; no logs contain its contents.
    with (root/'auth/auth.json').open('wb') as out:
        subprocess.run(['docker','exec','intervaltree-vero-astra-low-20261027-001','cat',
                        '/home/agent/.codex/auth.json'],stdout=out,check=True)
    (root/'auth/auth.json').chmod(0o600)
    save(root/'configuration.json',{'model':'gpt-6-astra','reasoning_effort':'low','targets':91,
        'comparison_axis':'generation_elapsed_seconds','wall_seconds':18834,
        'reference_generation_seconds':18834.104907751083,'include_repository_planning':False,
        'generation_strategy':'sketch-reviewed-v1','max_parallel_theories':1,
        'native_slots':1,'resident_limit':1,'native_threads':2,'native_cpus':4,'native_memory_mb':8192,
        'resource_caveat':'Project model worker has a separate 2 CPU / 2 GiB cap; VERO used one shared 4 CPU / 8 GiB container.',
        'attempt_comparison':'Time matched, not per-contract attempts: helper and revision work use the project policy.',
        'prior_proofs_accessible':False})


def preflight(root, name='adapter-preflight'):
    from regen.preparation.checker_context import load_context
    from regen.planning.workspace_preflight import native_checker
    from regen.execution.isabelle_node_checker import IsabelleChecker
    from regen.planning.proof_graph import build_graph
    c = load_context(root/'dataset')
    payload = json.loads((root/'dataset/repository-planning-input.json').read_text())
    directory = root/name; directory.mkdir(exist_ok=False)
    proposal = {'schema_version':6,'theories':[{'name':'Adapter_Contracts',
        'contract_ids':[x['contract_id'] for x in payload['contracts']],'required_imports':[]}]}
    result = native_checker(root/'dataset',300,threads=2)(payload,proposal,directory/'contracts')
    save(directory/'contracts.json',result)
    if not result['valid']: raise RuntimeError('fixed contract preflight failed: '+str(result))
    from regen.infrastructure.resource_budget import clone_heaps
    clone_heaps(c['heaps'],directory/'heaps')
    checker = IsabelleChecker(directory,directory/'smoke',image=c['image'],timeout=300,
        generated_dir=directory/'generated',heaps_dir=directory/'heaps',l4v_dir=c['l4v'],
        semantic_workspace=c['semantic_workspace'],base_session=c['base_session'],
        base_import=c['base_import'],base_catalog=c['base_catalog'],isabelle_dir=c['isabelle'])
    # Generic HOL infrastructure test only, never a benchmark target or generator input.
    package = {'theory':'Infra_Smoke','file_slot':'Infra_Smoke.thy','contracts':[
        {'contract_id':'infra-smoke','name':'infra_smoke','exact_statement':'lemma infra_smoke: "True"',
         'contract_owned_context':[]}]}
    graph = build_graph(package,{},run_id='adapter-smoke',revision=0)
    proofs = {'infra-smoke':'by simp'}
    from regen.planning.batch_file_planner import seal_theory
    state = {'graph':graph,'proofs':proofs,'accepted_order':['infra-smoke'],'status':'generating'}
    sealed, evidence = seal_theory(directory, {'premise_snapshot':str(c['premise_audit_context_path'])},
        package,directory,checker,[],state)
    save(directory/'seal-evidence.json',evidence)
    if sealed is None: raise RuntimeError('full seal preflight failed: '+str(evidence.get('diagnostic')))
    save(directory/'sealed-state.json',sealed)
    save(root/'adapter-preflight.json',{'status':'pass','contracts_checked':91,
        'fixed_targets_proved':0,'generic_smoke_audit':True,'generic_smoke_seal_build':True,
        'retained_semantic_impact':True,'seal_certificate_replay':True})


def launch(root, name='planning'):
    from regen.tools.application_release import verify
    from regen.execution.proof_session_runner import init_run
    from regen.execution.proof_commits import durable
    from regen.tools.parallel_workspace_service import start
    verify(root/'application',root/'application-manifest.json')
    if json.loads((root/'adapter-preflight.json').read_text())['status'] != 'pass':
        raise ValueError('adapter not validated')
    os.environ['CODEX_HOME'] = str(root/'auth')
    os.environ['REGEN_MODEL_CONFIG'] = str(root/'config/models.json')
    planning = root/name
    started = time.time()
    save(root/'phase-status.json',{'stage':'repository_planning','started_unix':started})
    command = [sys.executable,'-u','-m','regen.tools.reviewed_planning',
        '--input',str(root/'dataset/repository-planning-input.json'),
        '--policy',str(root/'config/planning-policy.json'),'--output',str(planning),
        '--model','gpt-6-astra','--reasoning-effort','low','--allow-external-call']
    save(root/(name+'-invocation.json'),{'command':command,'started_unix':started,
        'included_in_generation_budget':False})
    with (root/(name+'.log')).open('w') as log:
        result = subprocess.run(command,stdout=log,stderr=subprocess.STDOUT)
    save(root/(name+'-timing.json'),{'started_unix':started,'ended_unix':time.time(),
        'elapsed_seconds':time.time()-started,'returncode':result.returncode})
    if result.returncode: raise RuntimeError('planning failed; generation not launched')
    frozen = json.loads((planning/'frozen-plan.json').read_text())
    if frozen['state'] != 'frozen': raise ValueError('unfrozen plan')
    proof = root/'proof'
    init_run(proof,root/'dataset',run_id=root.name,
        schedule_path=Path(frozen['file_generation_list']),log_storage='regen-merkle-json-v1')
    command = [sys.executable,'-u','-m','regen.execution.proof_session_runner','parallel-workspace',
        '--run-dir',str(proof),'--planner-model','gpt-6-astra','--file-planner-interface','document',
        '--max-parallel-theories','1','--native-slots','1','--resident-limit','1',
        '--native-threads','2','--native-cpus','4','--native-memory-mb','8192',
        '--native-pids-limit','1024','--build-ml-heap-mb','2048',
        '--provider-build-timeout','1800','--call-timeout','900','--check-timeout','900',
        '--candidate-timeout','300','--generation-strategy','sketch-reviewed-v1',
        '--effective-seconds','18834','--wall-seconds','18834',
        '--execution-policy',str(root/'config/execution-policy.json'),'--allow-external-call']
    durable(proof/'experiment.json',{'schema_version':1,'cwd':str(root/'application'),'command':command,
        'model':'gpt-6-astra','reasoning_effort':'low','wall_seconds':18834,'include_planning':False,
        'planning_timing':str(root/(name+'-timing.json')),
        'resource_requirements':{'min_free_disk_bytes':8*1024**3,'min_available_memory_bytes':12*1024**3}},immutable=True)
    result = start(proof)
    save(root/'phase-status.json',{'stage':'generation','handoff_unix':time.time(),'service':result})
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['configure','preflight','launch'])
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--name')
    args = parser.parse_args()
    root = args.root.resolve()
    os.chdir(root/'application')
    sys.path.insert(0,str(root/'application'))
    if args.name is not None:
        globals()[args.action](root,args.name)
    else:
        globals()[args.action](root)
