"""Append-only experiment accounting. Provider responses and thread counters never add."""
from copy import deepcopy
from pathlib import Path
import json, threading, time
from regen.execution.proof_commits import digest, durable
from regen.infrastructure.incremental_log import read_artifact, find_root, write_artifact

TOKEN_KEYS=('inputTokens','cachedInputTokens','cacheWriteInputTokens','outputTokens','reasoningOutputTokens','totalTokens')

class Ledger:
    def __init__(self, root, run_id, *, clock=time.time):
        self.root=Path(root);self.run_id=run_id;self.clock=clock;self.lock=threading.RLock()
        self.rows=[];parent=None
        for i,p in enumerate(sorted((self.root/'events').glob('*.json')),1):
            row=read_artifact(p);core={k:v for k,v in row.items() if k!='event_id'}
            if p.name!=f'{i:09d}.json' or row['sequence']!=i or row['parent']!=parent or row['run_id']!=run_id or digest(core)!=row['event_id']:
                raise ValueError('experiment ledger chain mismatch')
            self.rows.append(row);parent=row['event_id']
        self.responses={r['response_id']:r for r in self.rows if r['kind']=='response'}
    def append(self,kind,**data):
        with self.lock:
            row={'run_id':self.run_id,'sequence':len(self.rows)+1,'parent':self.rows[-1]['event_id'] if self.rows else None,
                 'time_unix':self.clock(),'kind':kind,'data':deepcopy(data)}
            # Stable top-level response identity supports recovery without summing snapshots.
            if kind=='response':row['response_id']=data['response_id']
            row['event_id']=digest(row)
            path=self.root/'events'/f"{row['sequence']:09d}.json"
            if find_root(path) is not None:write_artifact(path,row,immutable=True)
            else:durable(path,row,immutable=True)
            self.rows.append(row);return row
    def response(self,params,*,scope,invocation,model,effort):
        rid=params['responseId'];usage=params.get('usage')
        if not isinstance(rid,str) or not rid:raise ValueError('missing response identity')
        if usage is not None:
            if any(type(usage.get(k,0)) is not int or usage.get(k,0)<0 for k in TOKEN_KEYS):raise ValueError('invalid provider usage')
            if any(k not in usage for k in ('inputTokens','outputTokens','totalTokens')):raise ValueError('incomplete provider usage')
        with self.lock:
            if rid in self.responses:
                old=self.responses[rid]['data']
                if old['usage']!=usage or old['thread_id']!=params['threadId'] or old['turn_id']!=params['turnId']:raise ValueError('conflicting response usage')
                return self.responses[rid]
            row=self.append('response',response_id=rid,thread_id=params['threadId'],turn_id=params['turnId'],
                usage=usage,scope=scope,invocation_id=invocation,requested_model=model,reasoning_effort=effort,
                actual_response_model=None,actual_response_model_status='not_exposed_by_protocol',
                accounting_basis='provider_response_id',usage_metadata=params.get('usageMetadata'))
            self.responses[rid]=row;return row

class Stage:
    """Wall deadline persists across crashes; only an explicit extension creates a stage."""
    def __init__(self,ledger,seconds,*,extend=False):
        if type(seconds) not in (int,float) or seconds<=0:raise ValueError('positive stage duration required')
        self.ledger=ledger;self.lock=threading.RLock();self.event=threading.Event()
        stages=[r for r in ledger.rows if r['kind']=='stage_started']
        if not stages or extend:
            self.record=ledger.append('stage_started',seconds=seconds,deadline_unix=ledger.clock()+seconds,
                stage_number=len(stages)+1,explicit_extension=bool(stages))
        else:self.record=stages[-1]
        self.deadline=self.record['data']['deadline_unix'];self.stage_id=self.record['event_id']
    def check(self):
        from regen.execution.check_tasks import CheckCancelled
        if self.event.is_set() or self.ledger.clock()>=self.deadline:
            self.event.set();raise CheckCancelled('global wall-clock stage paused; retain results and cumulative costs')
    def cancel(self):
        with self.lock:self.event.set()
    def cutoff(self,reason):
        path=self.ledger.root/'stage-cutoffs'/(self.stage_id+'.json')
        if not path.exists():
            row=self.ledger.append('stage_cutoff',stage_id=self.stage_id,deadline_unix=self.deadline,reason=reason,
                last_event_id=self.ledger.rows[-1]['event_id'],known_tokens=sum((r['data']['usage'] or {}).get('totalTokens',0) for r in self.ledger.responses.values()))
            durable(path,row,immutable=True)
        return json.loads(path.read_text())

class RolloutUsageReader:
    """Read only this run's bound rollout accounting; never add cumulative counters."""
    def __init__(self,home,binding,ledger, *, snapshots=None):
        self.home=Path(home);self.binding=binding;self.ledger=ledger;self.offsets={}
        self.snapshots=snapshots;self.snapshot_threads=set()
    def poll(self,*,scope,invocation,model,effort):
        thread=self.binding.get('thread_id')
        if not thread:return 0
        if any(c not in '0123456789abcdef-' for c in thread):raise ValueError('invalid bound thread identity')
        paths=list((self.home/'sessions').rglob('*'+thread+'.jsonl'))
        if len(paths)>1:raise ValueError('ambiguous thread rollout')
        count=0
        for path in paths:
            if path.is_symlink() or not path.resolve().is_relative_to(self.home.resolve()):raise ValueError('rollout escaped private home')
            offset=self.offsets.get(path,0)
            if path.stat().st_size<offset:raise ValueError('rollout accounting was truncated')
            with path.open('rb') as f:
                first=f.readline()
                if not first.endswith(b'\n'):continue
                meta=json.loads(first)
                if meta.get('type')!='session_meta' or meta['payload'].get('id')!=thread:raise ValueError('rollout session identity mismatch')
                f.seek(offset)
                while True:
                    start=f.tell();line=f.readline()
                    if not line or not line.endswith(b'\n'):break
                    row=json.loads(line)
                    payload=row.get('payload',{})
                    if self.snapshots is not None and row.get('type')=='event_msg' and payload.get('type')=='token_count':
                        counters=(payload.get('info') or {}).get('total_token_usage')
                        if counters is not None:
                            snake=('input_tokens','cached_input_tokens','cache_write_input_tokens','output_tokens','reasoning_output_tokens','total_tokens')
                            self.snapshots.record(thread,{k:counters.get(v) for k,v in zip(TOKEN_KEYS,snake)},
                                scope=scope,source='bound rollout token_count',turn_id=payload.get('turn_id'))
                            self.snapshot_threads.add(thread)
                    if row.get('type')=='token_usage_record':
                        p=row['payload']
                        if p['thread_id']!=thread or p['session_id']!=thread:raise ValueError('foreign response accounting')
                        snake=('input_tokens','cached_input_tokens','cache_write_input_tokens','output_tokens','reasoning_output_tokens','total_tokens')
                        usage=None if p.get('usage') is None else {k:p['usage'][v] for k,v in zip(TOKEN_KEYS,snake) if v in p['usage']}
                        self.ledger.response({'responseId':p['response_id'],'threadId':thread,'turnId':p['turn_id'],'usage':usage},scope=scope,invocation=invocation,model=model,effort=effort)
                        self.ledger.append('rollout_usage_receipt',response_id=p['response_id'],path=str(path),byte_offset=start,record_sha256=digest(row),provider_timestamp=row.get('timestamp'))
                        count+=1
                    self.offsets[path]=f.tell()
        return count
