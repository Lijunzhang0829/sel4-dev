"""Export final reference results, then explicitly retire enumerated old runs.

No proof is reverified. This is NOT a resumable or certified journal backup.
Never removes the campaign root, shared datasets, preparation or environments.
"""
import argparse
from collections import Counter
import hashlib
import json
import mmap
import os
from pathlib import Path
import shutil
import subprocess

from regen.execution.proof_commits import durable


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def read(path):return json.loads(Path(path).read_text())


def field(mm,key,*,indent=2,start=0,stop=None):
    """Read one pretty-JSON field without loading GB of inherited evidence.

    Durable journals use two-space indentation. Root/state field boundaries
    cannot occur inside JSON strings (newlines there are escaped).
    """
    token=b'\n'+b' '*indent+json.dumps(key).encode()+b': '
    at=mm.find(token,start,len(mm) if stop is None else stop)
    if at<0:raise ValueError('missing journal field: '+key)
    begin=at+len(token)
    end=mm.find(b'\n'+b' '*indent+b'"',begin,len(mm) if stop is None else stop)
    if end<0:end=len(mm) if stop is None else stop
    return json.JSONDecoder().raw_decode(mm[begin:end].decode())[0]


def final_state(path):
    with path.open('rb') as stream,mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ) as mm:
        at=mm.find(b'\n  "state": {')
        if at<0:raise ValueError('unsupported journal layout: '+str(path))
        # State is the last sorted root field in the existing durable format.
        state={key:field(mm,key,indent=4,start=at) for key in
               ('graph','proofs','accepted_order','status')}
        identity={key:field(mm,key,stop=at) for key in ('commit_id','sequence','event')}
    if path.name!=f"{identity['sequence']:06d}.json":raise ValueError('sequence mismatch')
    nodes={n['id']:n for n in state['graph']['nodes']}
    if (set(state['proofs'])!=set(state['accepted_order']) or
            not set(state['proofs'])<=nodes.keys() or
            len(state['accepted_order'])!=len(set(state['accepted_order']))):
        raise ValueError('final accepted prefix is inconsistent')
    return state,identity


def inventory(root):
    h=hashlib.sha256();count=size=blocks=0
    for directory,dirs,files in os.walk(root,followlinks=False):
        dirs.sort()
        for name in sorted(files+[d for d in dirs if (Path(directory)/d).is_symlink()]):
            p=Path(directory)/name;s=p.lstat()
            h.update(json.dumps([str(p.relative_to(root)),s.st_size,s.st_mtime_ns,
                                 os.readlink(p) if p.is_symlink() else None]).encode())
            count+=1;size+=s.st_size;blocks+=s.st_blocks*512
    return {'files':count,'logical_bytes':size,'allocated_bytes':blocks,'inventory_sha256':h.hexdigest()}


def copy(source,dest):
    dest.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source,dest)
    if sha(source)!=sha(dest):raise ValueError('copy mismatch')


def export_run(run,dest):
    config=read(run/'run.json')
    dest.mkdir(parents=True)
    result={'format':'proof-reference-results-v1','run':run.name,'resumable':False,
            'reverified':False,'reference_only':True,'theories':[]}
    for path in sorted((run/'packages').glob('*.json')):
        package=read(path);theory=package['theory'];slot=package['file_slot']
        if Path(slot).name!=slot:raise ValueError('unsafe theory file slot')
        directory=run/'theories'/Path(slot).stem
        commits=sorted((directory/'acceptance/commits').glob('*.json'))
        row={'theory':theory,'file_slot':slot,'status':'not_started','contracts':[],
             'helpers':[],'accepted_order':[],'imports':read(directory/'imports.json')
                 if (directory/'imports.json').exists() else []}
        nodes={};proofs={}
        if commits:
            state,identity=final_state(commits[-1])
            head=directory/'acceptance/HEAD.json'
            if head.exists():
                h=read(head)
                if h['sequence']>identity['sequence'] or (h['sequence']==identity['sequence'] and h['commit_id']!=identity['commit_id']):
                    raise ValueError('HEAD/last commit mismatch')
            row.update(status=state['status'],accepted_order=state['accepted_order'],
                       source_commit=identity['commit_id'],source_record_sha256=sha(commits[-1]))
            nodes={n['id']:n for n in state['graph']['nodes']};proofs=state['proofs']
        for c in package['contracts']:
            key=c['contract_id'];node=nodes.get(key,{})
            row['contracts'].append({'id':key,'name':node.get('name',c.get('name')),
                'statement':node.get('statement',c.get('exact_statement')),
                'context':node.get('context',c.get('contract_owned_context',[])),
                'attributes':c.get('contract_owned_attributes',[]),
                'proof_body':proofs.get(key),
                'status':('historically_sealed' if row['status']=='sealed' else 'historically_green')
                    if key in proofs else 'unproved' if commits else 'not_started'})
        contract_ids={c['id'] for c in row['contracts']}
        for key,node in nodes.items():
            if key in contract_ids:continue
            row['helpers'].append({**{k:node[k] for k in ('id','name','role','statement','context','dependencies') if k in node},
                'proof_body':proofs.get(key),'status':('historically_sealed' if row['status']=='sealed' else 'historically_green')
                    if key in proofs else 'unproved'})
        if set(proofs)!={n['id'] for n in row['contracts']+row['helpers'] if n['proof_body'] is not None}:
            raise ValueError('accepted proof export coverage mismatch')
        if row['status']=='sealed' and any(c['proof_body'] is None for c in row['contracts']):
            raise ValueError('sealed theory missing contract proof')
        # Keep the last draft separate; its text alone does not establish success.
        draft=directory/'document/draft.thy'
        if draft.is_file():copy(draft,dest/'final-drafts'/slot)
        durable(dest/'theories'/(Path(slot).stem+'.json'),row,immutable=True)
        result['theories'].append({k:v for k,v in row.items() if k not in {'contracts','helpers','accepted_order'}} | {
            'result_file':'theories/'+Path(slot).stem+'.json',
            'contracts':len(row['contracts']),'proved_contracts':sum(c['proof_body'] is not None for c in row['contracts']),
            'helpers':len(row['helpers']),'proved_helpers':sum(c['proof_body'] is not None for c in row['helpers'])})
    for path in sorted((run/'generated').glob('*.thy')):copy(path,dest/'generated-theories'/path.name)
    schedule=Path(config.get('session_schedule',{}).get('source',''))
    if schedule.is_file():
        copy(schedule,dest/'plan/file-generation-list.json')
        if (schedule.parent/'repository-plan.json').is_file():copy(schedule.parent/'repository-plan.json',dest/'plan/repository-plan.json')
    # Root-level launch/analysis scripts are code, not disposable logs.
    for path in run.iterdir():
        if path.is_file() and path.suffix in {'.py','.sh'}:copy(path,dest/'support-code'/path.name)
    result['counts']={'theories':len(result['theories']),
        'sealed_theories':sum(t['status']=='sealed' for t in result['theories']),
        'contracts':sum(t['contracts'] for t in result['theories']),
        'proved_contracts':sum(t['proved_contracts'] for t in result['theories']),
        'proved_helpers':sum(t['proved_helpers'] for t in result['theories'])}
    durable(dest/'results.json',result,immutable=True)
    return result['counts']


def export(root,output):
    root,output=root.resolve(),output.resolve()
    if output.exists() or output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError('archive must be new and separate from campaign')
    output.mkdir(parents=True)
    runs=sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink() and (p/'run.json').is_file())
    rows=[]
    for run in runs:
        before=inventory(run)
        counts=export_run(run,output/'runs'/run.name)
        if inventory(run)!=before:raise ValueError('source changed during export')
        rows.append({'source':str(run),'archive':'runs/'+run.name,'counts':counts,'inventory':before})
        print(run.name,counts,flush=True)
    # Preserve all completed repository grouping plans, independently of runs.
    for directory in sorted(root.iterdir()):
        if directory.is_dir() and not (directory/'run.json').exists() and (directory/'repository-plan.json').is_file():
            for name in ('repository-plan.json','file-generation-list.json'):
                if (directory/name).is_file():copy(directory/name,output/'plans'/directory.name/name)
    durable(output/'INDEX.json',{'reference_only':True,'resumable':False,'runs':rows,
        'notice':'Historical final accepted states, not a fresh proof verification. Do not automatically supply these proofs to fresh benchmark agents.'},immutable=True)
    files={str(p.relative_to(output)):sha(p) for p in sorted(output.rglob('*')) if p.is_file()}
    durable(output/'MANIFEST.json',files,immutable=True)
    verify(output)


def verify(output):
    for name,expected in read(output/'MANIFEST.json').items():
        p=output/name
        if not p.resolve().is_relative_to(output.resolve()) or sha(p)!=expected:raise ValueError('archive hash mismatch: '+name)
    for run in read(output/'INDEX.json')['runs']:
        result=read(output/run['archive']/'results.json')
        for theory in result['theories']:
            data=read(output/run['archive']/theory['result_file'])
            proved=[n for n in data['contracts']+data['helpers'] if n['status'].startswith('historically_')]
            if any(not isinstance(n['proof_body'],str) or not n['proof_body'].strip() for n in proved):
                raise ValueError('historical success without proof body')
            if len(proved)!=len(data['accepted_order']):raise ValueError('accepted body count mismatch')
    return True


def clean(root,output):
    root,output=root.resolve(),output.resolve();verify(output)
    index=read(output/'INDEX.json');targets=[Path(r['source']) for r in index['runs']]
    for path,row in zip(targets,index['runs']):
        if path.parent!=root or path.is_symlink() or not (path/'run.json').is_file():raise ValueError('unsafe cleanup target')
        if inventory(path)!=row['inventory']:raise ValueError('cleanup source changed after export')
    # Never delete an active container's mounts or running generation directory.
    live=subprocess.run(['docker','ps','-q'],check=True,capture_output=True,text=True)
    if live.stdout.strip():raise ValueError('active Docker containers; manual mount review required')
    procs=subprocess.run(['ps','-eo','pid,args'],check=True,capture_output=True,text=True).stdout
    for line in procs.splitlines():
        if str(os.getpid())==line.strip().split(' ',1)[0]:continue
        if 'python' in line and any(s in line for s in ('proof_session_runner','parallel_file_planner','proof_workspace_agent')):
            raise ValueError('active generation process')
    for base,dirs,files in os.walk(root.parent,followlinks=False):
        directory=Path(base).resolve()
        if directory in targets:dirs[:]=[];continue
        for name in dirs+files:
            path=Path(base)/name
            if path.is_symlink() and any(path.resolve().is_relative_to(t) for t in targets):
                raise ValueError('external reference to cleanup target: '+str(path))
    free_before=shutil.disk_usage(root).free
    receipt=[]
    for path,row in zip(targets,index['runs']):
        # Exact enumerated run children only, never campaign/workspace roots.
        for child in list(path.iterdir()):
            if child.is_symlink() or child.is_file():child.unlink()
            elif child.is_dir():shutil.rmtree(child)
            else:raise ValueError('unexpected special file: '+str(child))
        durable(path/'FINAL-RESULTS.json',{'archive':str(output/row['archive']),
            'reference_only':True,'resumable':False,'logs_and_private_heaps_removed':True},immutable=True)
        receipt.append({'source':str(path),'previous':row['inventory']})
        print('retired',path.name,flush=True)
    verify(output)
    durable(output/'CLEANUP.json',{'targets':receipt,'free_before':free_before,
        'free_after':shutil.disk_usage(root).free,'recoverable':False,
        'shared_datasets_and_environments_removed':False},immutable=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('operation',choices=['export','verify','clean'])
    p.add_argument('--campaign',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.operation=='export':export(args.campaign,args.output)
    elif args.operation=='clean':clean(args.campaign,args.output)
    else:verify(args.output)


if __name__=='__main__':main()
