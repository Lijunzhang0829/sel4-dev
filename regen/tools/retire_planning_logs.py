"""Retire planning process logs while retaining fixed inputs, code and results."""
import argparse
from pathlib import Path
import shutil
from regen.execution.proof_commits import durable
from regen.tools.archive_reference_results import copy,sha,inventory


def run(campaign,output,*,delete=False):
    campaign,output=campaign.resolve(),output.resolve()
    if output.exists() or output.is_relative_to(campaign):raise ValueError('new separate output required')
    output.mkdir(parents=True)
    # Explicit legacy artifact names, not a recursive wildcard deletion.
    log_dirs={'queries','snapshots','transcript','preflight','control','workspace','public','planning'}
    keep={'planner-input.json','planner-policy.json','planner-validation-input.json',
          'agent-input.json','private-contract-map.json','repository-plan.json','file-generation-list.json',
          'planner-proposal.json','agent-proposal.json'}
    targets=[];rows=[]
    for directory in sorted(campaign.iterdir()):
        if directory.is_symlink() or not directory.is_dir() or not (directory/'planner-input.json').is_file():continue
        if not directory.name.startswith('repository-plan'):continue
        dest=output/directory.name;dest.mkdir()
        completed=(directory/'repository-plan.json').is_file()
        for name in ('repository-plan.json','file-generation-list.json','planner-proposal.json','agent-proposal.json'):
            if (directory/name).is_file():copy(directory/name,dest/name)
        if (directory/'workspace/plan.json').is_file():copy(directory/'workspace/plan.json',dest/'last-draft-not-certified.json')
        for p in directory.iterdir():
            if p.is_file() and p.suffix in {'.py','.sh','.patch'}:copy(p,dest/'support-code'/p.name)
        durable(dest/'STATUS.json',{'completed_plan':completed,'reference_only':True,'resumable':False},immutable=True)
        for p in directory.iterdir():
            if p.name in log_dirs or (p.is_file() and p.name not in keep and p.suffix in {'.log','.json','.txt'}):
                if p.is_symlink():raise ValueError('unexpected planning artifact symlink')
                targets.append({'path':str(p),'inventory':inventory(p) if p.is_dir() else {'sha256':sha(p)}})
        rows.append({'source':str(directory),'completed':completed})
    files={str(p.relative_to(output)):sha(p) for p in output.rglob('*') if p.is_file()}
    durable(output/'MANIFEST.json',files,immutable=True)
    durable(output/'cleanup-plan.json',{'targets':targets,'plans':rows},immutable=True)
    if not delete:return
    for name,value in files.items():
        if sha(output/name)!=value:raise ValueError('planning archive drift')
    for row in targets:
        p=Path(row['path'])
        if p.parent.parent!=campaign or p.is_symlink():raise ValueError('unsafe target')
        actual=inventory(p) if p.is_dir() else {'sha256':sha(p)}
        if actual!=row['inventory']:raise ValueError('planning source changed')
    before=shutil.disk_usage(campaign).free
    for row in targets:
        p=Path(row['path'])
        if p.is_dir():shutil.rmtree(p)
        else:p.unlink()
    for row in rows:
        durable(Path(row['source'])/'FINAL-RESULTS.json',{'archive':str(output/Path(row['source']).name),
            'resumable':False,'fixed_inputs_retained':True,'logs_removed':True},immutable=True)
    durable(output/'CLEANUP.json',{'removed_entries':len(targets),'planning_directories':len(rows),
        'free_before':before,'free_after':shutil.disk_usage(campaign).free,'recoverable':False},immutable=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--campaign',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--delete',action='store_true')
    a=p.parse_args();run(a.campaign,a.output,delete=a.delete)
