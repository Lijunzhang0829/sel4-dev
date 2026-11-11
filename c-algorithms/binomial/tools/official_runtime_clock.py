"""Trusted, evidence-bound retrospective clock corrections; never infer outages."""
import hashlib
import json
import os
import stat
from pathlib import Path


def union(intervals):
    out=[]
    for a,b in sorted(intervals):
        if b<=a:continue
        if out and a<=out[-1][1]:out[-1]=(out[-1][0],max(b,out[-1][1]))
        else:out.append((a,b))
    return out


def charged_intervals(state):
    # Reconstruct raw charged regions from lifecycle boundaries, independently
    # of later refunds. Snapshot checks need not emit a boundary every tick.
    out=[];last=0.;running=False;blocked=set()
    for row in state['history']:
        end=row['wall_seconds']
        if running and not blocked:out.append((last,end))
        action=row['action']
        if action=='resume':running=True;blocked.discard('controller-offline')
        elif action in {'close','finish','offline_recovery'}:running=False
        elif action=='exclude_started':blocked.add(row['identity'])
        elif action=='exclude_finished':blocked.discard(row['identity'])
        last=end
    if running and not blocked:out.append((last,state['wall_seconds']))
    return union(out)


def credit(clock, record):
    if (record.get('run_id')!=clock.state['run_id'] or record.get('all_workers_blocked') is not True
            or record.get('reason') not in {'provider_interruption','native_unresponsive','experiment_pause','offline'}
            or not isinstance(record.get('evidence'),dict) or not record['evidence']):
        raise ValueError('clock correction requires bound all-worker interruption evidence')
    key=record.get('id')
    if not isinstance(key,str) or not key:raise ValueError('correction identity required')
    with clock._guard:
        clock._settle()
        previous=clock.state.setdefault('trusted_corrections',{})
        if key in previous:
            if previous[key]['record']!=record:raise ValueError('correction identity changed')
            return 0.
        a,b=record['start_wall_seconds'],record['end_wall_seconds']
        from vero.isabelle.active_clock import number
        number(a);number(b)
        if not a<b<=clock.state['wall_seconds']:raise ValueError('correction outside observed interval')
        raw=charged_intervals(clock.state)
        proposed=[(max(a,x),min(b,y)) for x,y in raw if max(a,x)<min(b,y)]
        old=[tuple(v) for v in clock.state.get('retrospective_intervals',[])]
        combined=union(old+proposed)
        amount=sum(y-x for x,y in combined)-sum(y-x for x,y in old)
        clock.state['effective_seconds']-=amount;clock.state['excluded_seconds']+=amount
        clock.state['retrospective_intervals']=combined
        previous[key]={'record':record,'refund_seconds':amount}
        clock._event('trusted_retrospective_credit',identity=key,seconds=amount,
                     evidence='root-owned correction with persisted evidence')
        clock._write()
        return amount


def apply_trusted_corrections(clock, results):
    folder=Path('/run/trusted-clock-corrections')
    if not folder.exists():return
    if folder.is_symlink() or folder.stat().st_uid!=0 or folder.stat().st_mode & 0o022:
        raise ValueError('correction directory must be supervisor owned')
    for path in sorted(folder.glob('*.json')):
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022:
            raise ValueError('correction file must be supervisor owned')
        record=json.loads(path.read_text());amount=credit(clock,record)
        receipt=Path(results)/'clock-corrections';receipt.mkdir(exist_ok=True)
        target=receipt/(hashlib.sha256(record['id'].encode()).hexdigest()+'.json')
        if not target.exists():target.write_text(json.dumps({'record':record,'refund_seconds':amount},indent=2)+'\n')
