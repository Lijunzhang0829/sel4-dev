"""Supervise the existing reviewed planner, initializer and workspace service.

No proof search, retries or scheduling live here. A failed stage stops and keeps
its evidence; no experiment is restarted automatically.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from regen.execution.proof_commits import durable


def run(root):
    from regen.tools.application_release import verify
    from regen.execution.proof_session_runner import init_run
    from regen.tools.parallel_workspace_service import start
    from regen.tools.parallel_workspace_memory_guard import monitor
    root=Path(root).resolve()
    config=json.loads((root/'experiment-config.json').read_text())
    if (root/'phase-status.json').exists():raise ValueError('fresh launch only; inspect existing evidence before recovery')
    if json.loads((root/'dataset-acceptance.json').read_text())['status']!='pass':
        raise ValueError('dataset acceptance required')
    verify(root/'application',root/'release/manifest.json')
    os.environ['REGEN_MODEL_CONFIG']=str(root/'config/models.json')
    planning=root/'planning';proof=root/'proof'
    def phase(stage,**fields):
        durable(root/'phase-status.json',dict(stage=stage,time_unix=time.time(),**fields))
    command=[sys.executable,'-u','-m','regen.tools.reviewed_planning',
        '--input',str(root/'dataset/repository-planning-input.json'),
        '--policy',str(root/'config/planning-policy.json'),'--output',str(planning),
        '--model',config['model'],'--reasoning-effort',config['reasoning_effort'],
        '--budget-seconds',str(config['budget_seconds']),'--allow-external-call']
    durable(root/'planning-invocation.json',dict(command=command,cwd=str(root/'application')),immutable=True)
    phase('repository_planning',status='running')
    try:
        with (root/'planning.log').open('w') as log:
            result=subprocess.run(command,cwd=root/'application',stdout=log,stderr=subprocess.STDOUT)
        if result.returncode:
            phase('repository_planning',status='stopped',returncode=result.returncode)
            return result.returncode
        frozen=json.loads((planning/'frozen-plan.json').read_text())
        if frozen['state']!='frozen':raise ValueError('reviewed plan is not frozen')
        phase('initializing_generation',status='running')
        init_run(proof,root/'dataset',run_id=root.name,schedule_path=Path(frozen['file_generation_list']),
            log_storage='regen-merkle-json-v1',contract_outcome_policy='contract-dispositions-v1',
            unresolved_evidence_policy='proof-or-refutation-v1')
        command=[sys.executable,'-u','-m','regen.execution.proof_session_runner','parallel-workspace',
            '--run-dir',str(proof),'--planner-model',config['model'],
            '--max-parallel-theories','1','--native-slots','1','--resident-limit','1',
            '--native-threads','4','--native-cpus','4','--native-memory-mb','16384',
            '--native-pids-limit','1024','--build-ml-heap-mb','8192',
            '--provider-build-timeout','1800','--call-timeout','600','--check-timeout','900',
            '--candidate-timeout','300','--generation-strategy','sketch-reviewed-v2',
            '--budget-seconds',str(config['budget_seconds']),'--planning-budget',str(planning)]
        durable(proof/'experiment.json',dict(command=command,cwd=str(root/'application'),
            resource_requirements=dict(min_free_disk_bytes=5*1024**3,min_available_memory_bytes=18*1024**3)),immutable=True)
        observed=start(proof)
        durable(root/'generation-start.json',observed,immutable=True)
        phase('generation',status='running',service_unit=observed['unit'])
        print(json.dumps(dict(status='generation_started',service_unit=observed['unit'])),flush=True)
        guarded=monitor(proof,min_available_bytes=6*1024**3,min_free_disk_bytes=5*1024**3)
        phase('generation_stopped',status='stopped',guard=guarded)
        return 0
    except BaseException as error:
        phase('stopped',status='error',error_type=type(error).__name__,diagnostic=str(error))
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    args=parser.parse_args()
    raise SystemExit(run(args.root))
