"""One real saved-prefix Isabelle check through incremental request/evidence I/O.

No model call, acceptance event, experiment restart or new proof is produced.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

from regen.execution import isabelle_node_checker as native
from regen.execution.check_executor import NativeResources, CURRENT_RESOURCES
from regen.execution.proof_commits import durable, freeze_evidence, digest
from regen.infrastructure.incremental_log import enable, read_artifact
from regen.tools.harness_acceptance_baseline import protected_snapshot
from regen.tools.native_backend_acceptance import Limits
from regen.tools.profile_saved_node_phases import prefix_inputs
from regen.tools.replay_finish_step import load_inputs, validate_destination, validate_frozen_context, readonly_command


def run(source,output):
    source,output=validate_destination(source,output)
    inputs=load_inputs(source)
    context,_=validate_frozen_context(source,inputs)
    config,directory,graph,proofs,ancestors,*_=inputs
    graph,proofs=prefix_inputs(graph,proofs)
    before=protected_snapshot(source)
    enable(output)
    shutil.copytree(directory/'generated',output/'generated')
    (output/'build-heaps').mkdir()
    request=output/'batch-requests/000001'
    durable(request/'input.json',{'graph':graph,'proofs':proofs},immutable=True)
    recovered=read_artifact(request/'input.json')
    assert recovered=={'graph':graph,'proofs':proofs}
    checker=native.IsabelleChecker(output,output/'isabelle',timeout=300,
        architecture=context['architecture'],previous_theories=ancestors,import_all_previous=True,
        generated_dir=output/'generated',heaps_dir=directory/'heaps',image=config['image'],
        l4v_dir=Path(config['l4v']),isabelle_dir=Path(config['isabelle_dir']),
        semantic_workspace=Path(config['semantic_workspace']),base_session=config['base_session'],
        base_import=config['base_import'],base_catalog=config['base_catalog'],
        complete_context_policy='arch-reuse-v1')
    limits=Limits(output,360,1024**3)
    resources=NativeResources(slots=1,threads=4,resident_limit=1)
    token=CURRENT_RESOURCES.set(resources)
    original=native.native_run
    def invoke(command,*,timeout,**kwargs):
        args=readonly_command(command,output)
        limits.register(args[args.index('--name')+1])
        durable(output/'command.json',args,immutable=True)
        return original(args,timeout=timeout,**kwargs)
    result={'pass':False,'model_calls':0,'experiment_resumed':False,'seal_published':False,
            'nodes':len(graph['nodes']),'scope':'real saved-prefix complete check; no audit/build or acceptance'}
    try:
        limits.check();limits.monitor.start()
        with patch.object(native,'native_run',side_effect=invoke):
            check=freeze_evidence(checker.check_complete(recovered['graph'],recovered['proofs']))
        durable(request/'check.json',check,immutable=True)
        decoded=read_artifact(request/'check.json')
        if decoded!=check or check['verdict']!='green':
            raise ValueError('native check or evidence roundtrip failed')
        verify=subprocess.run([sys.executable,'-m','regen.tools.experiment_log','verify-storage',
                               str(request/'check.json')],capture_output=True,text=True)
        if verify.returncode:raise RuntimeError(verify.stderr)
        result.update(pass_=True,verdict=check['verdict'],wall_ms=check['wall_ms'],
                      evidence_sha256=digest(check),fresh_process_storage=json.loads(verify.stdout))
    except Exception as error:
        result['failure']={'type':type(error).__name__,'message':str(error)}
    finally:
        CURRENT_RESOURCES.reset(token);resources.close();limits.stop.set()
        if limits.monitor.ident is not None:limits.monitor.join(timeout=20)
        result['cleanup']=limits.cleanup()
        result['guard_failure']=limits.failure
        result['protected_unchanged']=before==protected_snapshot(source)
        result['pass']=(result.pop('pass_',False) and result['protected_unchanged']
                        and not limits.failure and all(x['confirmed_absent'] for x in result['cleanup']))
        durable(output/'report.json',result,immutable=True)
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();result=run(args.run,args.output);print(json.dumps(result,indent=2))
    return 0 if result['pass'] else 1


if __name__=='__main__':raise SystemExit(main())
