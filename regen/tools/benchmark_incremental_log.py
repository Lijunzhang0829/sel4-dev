"""Seeded saved-step replay, crash injection and isolated-process I/O benchmark.

No models or Isabelle are launched. Adjacent real acceptance transitions use the
production validator. The selected predecessor is an explicitly hash-bound test
anchor, NOT a substitute for verifying the original run's entire history.
"""
import argparse
import gc
import json
import os
from pathlib import Path
import random
import resource
import statistics
import subprocess
import sys
import time
from unittest.mock import patch

from regen.execution.proof_commits import ProofCommits, digest, durable
from regen.execution.repository_execution import validator
from regen.infrastructure import incremental_log as logs
from regen.tools.harness_acceptance_baseline import protected_snapshot


def read(path):
    return logs.read_artifact(path)


def get_journal(root):
    config = read(root/'case.json')
    native_validate = validator(read(root/'package.json'), read(root/'source-run.json'))
    def validate(old,new,event,evidence):
        if old is None:
            if event != 'saved-step-anchor' or digest(new) != config['anchor_state_sha256']:
                raise ValueError('invalid saved-step benchmark anchor')
        else:
            native_validate(old,new,event,evidence)
    return ProofCommits(root/'acceptance',config['binding'],validate)


def measure(root):
    start=time.perf_counter()
    state=get_journal(root).replay(repair=False)
    elapsed=time.perf_counter()-start
    # Capture high-water memory before the reporting hash, equally for both modes.
    # Linux getrusage can include the launching parent's pre-exec high-water
    # mark. /proc reports this executable's own high-water RSS instead.
    status = dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines())
    memory=int(status['VmHWM'].split()[0])*1024
    result={'seconds':elapsed,'peak_rss_bytes':memory,
            'getrusage_peak_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
            'rss_method':'/proc/self/status VmHWM after exec', 'state_sha256':digest(state)}
    print(json.dumps(result))


def interrupted(root, stage):
    j=get_journal(root)
    next_record=read(root/'next.json')
    original=logs.atomic_bytes
    from regen.execution import proof_commits
    original_durable=proof_commits.durable
    def publication(path,raw,**kwargs):
        if Path(path).name=='000002.json':
            if stage=='before': os._exit(71)
            result=original(path,raw,**kwargs)
            if stage=='after': os._exit(72)
            return result
        return original(path,raw,**kwargs)
    def head(path,value,**kwargs):
        if stage=='head' and Path(path).name=='HEAD.json': os._exit(73)
        return original_durable(path,value,**kwargs)
    with patch.object(logs,'atomic_bytes',side_effect=publication), \
         patch.object(proof_commits,'durable',side_effect=head):
        j.append(next_record['event'],next_record['state'],next_record['evidence'])
    raise AssertionError('fault injection did not fire')


def run_worker(root, operation, stage=None):
    command=[sys.executable,'-m',__name__ if __name__!='__main__' else
             'regen.tools.benchmark_incremental_log','--worker',operation,'--case',str(root)]
    if stage: command += ['--stage',stage]
    return subprocess.run(command,text=True,capture_output=True)


def size(root):
    return sum(p.stat().st_size for p in root.rglob('*') if p.is_file())


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--worker',choices=['measure','crash'])
    parser.add_argument('--case',type=Path)
    parser.add_argument('--stage',choices=['before','after','head'])
    parser.add_argument('--run',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--seed',type=int,default=20260930)
    parser.add_argument('--measure-existing',type=Path,
                        help='Remeasure existing cases without remigration or crash injection')
    args=parser.parse_args()
    if args.worker:
        return measure(args.case) if args.worker=='measure' else interrupted(args.case,args.stage)
    if args.measure_existing:
        if args.output.exists():raise ValueError('measurement report must be new')
        base=read(args.measure_existing/'report.json')
        measurements=[]
        for case in base['cases']:
            directory=args.measure_existing/f"{case['theory']}-{case['sequence']:06d}"
            row={'theory':case['theory'],'sequence':case['sequence'],'event':case['event'],'modes':{}}
            for mode in ('inline','incremental','incremental','inline'):
                process=run_worker(directory/mode,'measure')
                if process.returncode:raise RuntimeError(process.stderr)
                result=json.loads(process.stdout)
                if result['state_sha256']!=read(directory/mode/'case.json')['expected_state_sha256']:
                    raise ValueError('recovery state differs')
                row['modes'].setdefault(mode,{'recovery':[]})['recovery'].append(result)
            for name,mode in row['modes'].items():
                mode['median_recovery_seconds']=statistics.median(x['seconds'] for x in mode['recovery'])
                mode['median_peak_rss_bytes']=statistics.median(x['peak_rss_bytes'] for x in mode['recovery'])
                mode['logical_bytes']=size(directory/name)
                mode['allocated_bytes']=sum(p.stat().st_blocks*512 for p in (directory/name).rglob('*') if p.is_file())
            measurements.append(row)
            print(json.dumps(row),flush=True)
        durable(args.output,{'cases':measurements,'order':'ABBA; fresh process each time',
            'rss_method':'/proc/self/status VmHWM after exec',
            'scope':'saved predecessor anchor plus next step with production transition validator',
            'os_page_cache_flushed':False},immutable=True)
        return
    source,output=args.run.resolve(),args.output.resolve()
    if output.exists() or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('benchmark requires a new, separate destination')
    before=protected_snapshot(source)
    output.mkdir(parents=True)
    rng=random.Random(args.seed)
    selections=[]
    # Avoid giant inherited initial commits: this is checkpoint/step recovery,
    # not an unbounded replay of predecessor experiments.
    for theory,count in [('Scheduler_Queues',3),('Capability_Access',1)]:
        files=sorted((source/'theories'/theory/'acceptance/commits').glob('*.json'))
        selections.extend((theory,n) for n in sorted(rng.sample(range(3,len(files)+1),count)))
    durable(output/'selection.json',{'seed':args.seed,'selection':selections,
        'population':'Scheduler_Queues 3..75 (3 samples); Capability_Access 3..17 (1 sample)',
        'scope':'adjacent saved-step transitions with explicit predecessor anchor'},immutable=True)
    report=[]
    for theory,number in selections:
        print('case',theory,number,flush=True)
        paths=source/'theories'/theory/'acceptance/commits'
        old=read(paths/f'{number-1:06d}.json');new=read(paths/f'{number:06d}.json')
        for record in (old,new):
            if digest({k:v for k,v in record.items() if k!='commit_id'})!=record['commit_id']:
                raise ValueError('source record hash mismatch')
        if new['parent']!=old['commit_id']: raise ValueError('source parent mismatch')
        package=read(source/'packages'/(theory+'.thy.json'));run=read(source/'run.json')
        validator(package,run)(old['state'],new['state'],new['event'],new['evidence'])
        case=output/f'{theory}-{number:06d}';case.mkdir()
        config={'anchor_state_sha256':digest(old['state']),
                'binding':{'benchmark':'saved-step-log-v1','source_commit':old['commit_id']},
                'expected_state_sha256':digest(new['state'])}
        row={'theory':theory,'sequence':number,'event':new['event'],
             'source_commit':new['commit_id'],'source_transition_valid':True,'modes':{}}
        for mode in ('inline','incremental'):
            root=case/mode
            if mode=='incremental':logs.enable(root)
            else:root.mkdir()
            for name,value in [('case.json',config),('package.json',package),('source-run.json',run)]:
                durable(root/name,value,immutable=True)
            start=time.perf_counter()
            j=get_journal(root)
            j.append('saved-step-anchor',old['state'],{})
            j.append(new['event'],new['state'],new['evidence'])
            row['modes'][mode]={'write_seconds':time.perf_counter()-start,'bytes':size(root)}
        # Separate processes, alternating ABBA order. OS page cache is not flushed.
        for mode in ('inline','incremental','incremental','inline'):
            result=run_worker(case/mode,'measure')
            if result.returncode:raise RuntimeError(result.stderr)
            measured=json.loads(result.stdout)
            if measured['state_sha256']!=config['expected_state_sha256']:
                raise ValueError('recovery state differs')
            row['modes'][mode].setdefault('recovery',[]).append(measured)
        # Real process termination at three publication windows; only new store.
        row['interruptions']=[]
        for stage in ('before','after','head'):
            root=case/('crash-'+stage);logs.enable(root)
            for name,value in [('case.json',config),('package.json',package),('source-run.json',run)]:
                durable(root/name,value,immutable=True)
            # Inputs are non-authoritative, stored once in the same object store.
            logs.write_artifact(root/'next.json',new,immutable=True)
            get_journal(root).append('saved-step-anchor',old['state'],{})
            result=run_worker(root,'crash',stage)
            if result.returncode != {'before':71,'after':72,'head':73}[stage]:
                raise RuntimeError('wrong crash result: '+str(result.returncode)+' '+result.stderr)
            recovered=get_journal(root);state=recovered.replay()
            expected=old['state'] if stage=='before' else new['state']
            if state!=expected:raise ValueError('wrong crash recovery state')
            if stage=='before':recovered.append(new['event'],new['state'],new['evidence'])
            if recovered.replay()!=new['state']:raise ValueError('resume did not reach saved next step')
            if len(list((root/'acceptance/commits').glob('*.json')))!=2:
                raise ValueError('duplicate acceptance after recovery')
            row['interruptions'].append({'stage':stage,'exit_code':result.returncode,
                                         'recovered_exact':True,'continued_exact':True})
        for mode in row['modes'].values():
            mode['median_recovery_seconds']=statistics.median(x['seconds'] for x in mode['recovery'])
            mode['median_peak_rss_bytes']=statistics.median(x['peak_rss_bytes'] for x in mode['recovery'])
        report.append(row);durable(case/'result.json',row,immutable=True)
        print(json.dumps(row),flush=True)
        del old,new,j,recovered,state,expected
        gc.collect();logs.clear_cache()
    after=protected_snapshot(source)
    summary={'seed':args.seed,'cases':report,'protected_unchanged':before==after,
             'model_calls':0,'native_checks':0,'experiment_resumed':False,
             'limitations':['checkpoint-relative recovery, not full original history replay',
                'fresh Python processes; OS page cache not flushed',
                'real saved evidence revalidated, no new proof generation'],
             'protected_before':before,'protected_after':after}
    durable(output/'report.json',summary,immutable=True)
    if before!=after:raise ValueError('protected source changed')


if __name__=='__main__':main()
