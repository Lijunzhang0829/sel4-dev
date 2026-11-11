"""Durable charged intervals and evidence-bound, idempotent outage corrections.

Intervals are appended once, not copied into every checkpoint. Replaying the
ledger reconstructs requests and corrections, including requests diagnosed late.
"""
from copy import deepcopy
import math

PREFIX = 'experiment_runtime_'


def duration(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('invalid experiment runtime interval')
    return float(value)


class RuntimeIntervals:
    def __init__(self, ledger):
        self.ledger=ledger;self.requests={};self.active={};self.segments=[];self.pending=None;self.faults={}
        for row in ledger.rows:
            data=row['data'];kind=row['kind']
            if kind==PREFIX+'request_started':
                self.requests[data['key']]=deepcopy(data)
            elif kind==PREFIX+'request_finished':
                self.requests[data['key']].update(deepcopy(data))
            elif kind==PREFIX+'segment':self.segments.append(deepcopy(data))
            elif kind=='time_budget_checkpoint' and data.get('boundary')=='experiment_interruption_corrected':
                item=data['correction']['interruption'];self.faults[item['evidence_id']]=deepcopy(item)
        # Restored processes do not inherit live native/provider ownership.

    def begin(self, kind, worker, theory, uid, binding, now):
        if any(not isinstance(x,str) or not x for x in (kind,worker,uid)):
            raise ValueError('runtime request requires identities')
        key=kind+':'+uid
        if key in self.requests or (kind,worker) in self.active:
            raise ValueError('duplicate or overlapping runtime request')
        row=dict(key=key,request_kind=kind,worker=worker,theory=theory,request_id=uid,
                 binding=deepcopy(binding),start=duration(now),end=None,status='open')
        self.ledger.append(PREFIX+'request_started',**row)
        self.requests[key]=row;self.active[kind,worker]=key
        return key

    def finish(self, kind, worker, uid, status, now):
        key=kind+':'+uid;row=self.requests.get(key)
        if row is None or row['worker']!=worker:raise ValueError('unmatched runtime request')
        if row['end'] is not None:
            if row['status']!=status:raise ValueError('runtime request outcome changed')
            return row
        end=duration(now)
        if end<row['start']:raise ValueError('runtime request ends before its start')
        self.flush()
        data=dict(key=key,end=end,status=status)
        self.ledger.append(PREFIX+'request_finished',**data);row.update(data)
        if self.active.get((kind,worker))==key:self.active.pop((kind,worker))
        return row

    def record(self,start,end,lanes,model_workers):
        if end<=start:return
        contributors=[]
        for worker,(theory,role) in sorted(lanes.items()):
            contributors.append(dict(worker=worker,theory=theory,role=role,
                native=self.active.get(('native',worker)),provider=self.active.get(('provider',worker)),
                model=worker in model_workers))
        if self.pending and self.pending['end']==start and self.pending['contributors']==contributors:
            self.pending['end']=end
        else:
            self.flush();self.pending=dict(start=start,end=end,contributors=contributors)

    def flush(self):
        if self.pending is not None:
            self.ledger.append(PREFIX+'segment',**self.pending)
            self.segments.append(self.pending);self.pending=None

    def confirm(self, key, start, end, evidence_id, evidence):
        row=self.requests.get(key)
        if row is None:raise ValueError('interruption references unknown runtime request')
        if not isinstance(evidence_id,str) or not evidence_id:raise ValueError('interruption requires evidence identity')
        start,end=duration(start),duration(end)
        if start<row['start'] or end<start or (row['end'] is not None and end>row['end']):
            raise ValueError('interruption escapes its measured request')
        item=dict(key=key,start=start,end=end,evidence_id=evidence_id,evidence=deepcopy(evidence))
        old=self.faults.get(evidence_id)
        if old is not None:
            if old!=item:raise ValueError('interruption evidence changed')
            return
        # Counter changes and this evidence are published together by the
        # budget's immutable correction checkpoint, never in separate commits.
        self.flush();self.faults[evidence_id]=item

    def totals(self):
        total=dict(effective_seconds=0.,worker_seconds=0.,planning_seconds=0.,theory_seconds={})
        faults=list(self.faults.values())
        for segment in [*self.segments,*([self.pending] if self.pending else [])]:
            nearby=[f for f in faults if f['start']<segment['end'] and f['end']>segment['start']]
            if not nearby:continue
            cuts=sorted({segment['start'],segment['end'],
                *(max(segment['start'],f['start']) for f in nearby),
                *(min(segment['end'],f['end']) for f in nearby)})
            for a,b in zip(cuts,cuts[1:]):
                invalid={f['key'] for f in nearby if f['start']<=a and f['end']>=b}
                kept=[];removed=[]
                for c in segment['contributors']:
                    jobs=[key for key in (c['native'],c['provider']) if key is not None]
                    healthy=(not jobs or any(key not in invalid for key in jobs)
                             or (c['model'] and c['provider'] not in invalid))
                    (kept if healthy else removed).append(c)
                if not removed:continue
                seconds=b-a
                if not kept:total['effective_seconds']+=seconds
                total['worker_seconds']+=seconds*sum(c['role']=='worker' for c in removed)
                if any(c['role']=='planning' for c in removed) and not any(c['role']=='planning' for c in kept):
                    total['planning_seconds']+=seconds
                for theory in {c['theory'] for c in removed if c['role']=='worker'}-{c['theory'] for c in kept if c['role']=='worker'}:
                    total['theory_seconds'][theory]=total['theory_seconds'].get(theory,0.)+seconds
        return total
