"""Read-only operation boundaries, not a profiler of hidden model computation."""
import argparse,collections,json,sqlite3
from datetime import datetime
from pathlib import Path


def category(command):
    text=command if isinstance(command,str) else ' '.join(map(str,command))
    if '/backend/tool.py check' in text:return 'native_check'
    if '/backend/tool.py search' in text:return 'fact_query'
    if '/backend/tool.py budget' in text:return 'budget_read'
    if '/backend/tool.py progress' in text:return 'progress_read'
    if any(t in text for t in ('write_text','write_bytes','sed -i',"open(p,'w')",'open(p,"w")')):return 'edit'
    if any(t in text for t in ('rg ','cat ','sed -n','read_text','read_bytes')):return 'inspect'
    return 'other_shell'


def extract(root=Path('/results'),home=Path('/home/agent/.codex'),sandbox=Path('/sandbox')):
    final=json.loads((root/'final-summary.json').read_text());start=final['clock']['started_unix'];events=[];responses={}
    def add(t,kind,**data):
        if t is not None:events.append({'unix':t,'elapsed_seconds':t-start,'kind':kind,**data})
    for path in (home/'sessions').rglob('*.jsonl'):
        with path.open() as stream:
            for line in stream:
                try:x=json.loads(line)
                except ValueError:continue
                p=x.get('payload') or {};kind=x.get('type');stamp=x.get('timestamp')
                t=datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp() if isinstance(stamp,str) else None
                if kind=='token_usage_record':
                    rid=p.get('response_id')
                    if rid not in responses:
                        responses[rid]={'unix':t,'response_id':rid};add(t,'model.response_usage_recorded',response_id=rid)
                elif kind=='response_item':
                    typ=p.get('type');cid=p.get('call_id')
                    if typ in ('custom_tool_call','function_call'):add(t,'client.tool_dispatch_recorded',call_id=cid,tool=p.get('name'))
                    elif typ in ('custom_tool_call_output','function_call_output'):add(t,'client.tool_result_recorded',call_id=cid)
                    elif typ=='reasoning' or typ=='message' and p.get('role')=='assistant':
                        add(t,'model.item_recorded',item_type=typ,item_id=p.get('id'),not_a_first_byte_timestamp=True)
                elif kind=='event_msg' and p.get('type')=='item_completed':
                    item=p.get('item') or {};a=p.get('started_at_ms');b=p.get('completed_at_ms')
                    if item.get('type')=='CommandExecution':
                        desc={'item_id':item.get('id'),'operation':category(item.get('command',[]))}
                        add(a/1000 if a is not None else None,'shell.start',**desc)
                        add(b/1000 if b is not None else t,'shell.end',**desc,exit_code=item.get('exit_code'))
                    elif item.get('type')=='ContextCompaction':
                        add(a/1000 if a is not None else None,'context_compaction.start')
                        add(b/1000 if b is not None else t,'context_compaction.end')
    for path in sorted((sandbox/'.vero-attempts').glob('*')):
        if not (path/'submitted.json').exists():continue
        sub=json.loads((path/'submitted.json').read_text());add(sub['started_at'],'native.submitted',attempt=path.name)
        if (path/'result.json').exists():
            res=json.loads((path/'result.json').read_text());add(res['ended_at'],'native.completed',attempt=path.name,status=res.get('status'),audited=len(res.get('audited',[])))
    events.sort(key=lambda e:e['unix']);rr=sorted(responses.values(),key=lambda e:e['unix']);gaps=[]
    local_ends={'client.tool_result_recorded','shell.end','native.completed','context_compaction.end'}
    for previous,current in zip(rr,rr[1:]):
        if current['unix']-previous['unix']<=180:continue
        within=[e for e in events if previous['unix']<=e['unix']<=current['unix']]
        ends=[e for e in events if e['kind'] in local_ends and e['unix']<=current['unix']]
        last=ends[-1] if ends else None
        next_items=[e for e in within if e['kind'] in ('model.item_recorded','client.tool_dispatch_recorded') and (last is None or e['unix']>last['unix'])]
        first=next_items[0] if next_items else None
        gaps.append({'from_response':previous['response_id'],'to_response':current['response_id'],
            'start_seconds':previous['unix']-start,'end_seconds':current['unix']-start,
            'last_observed_local_completion':last,'next_recorded_model_item':first,
            'local_completion_to_model_record_seconds':first['unix']-last['unix'] if first and last else None,
            'request_sent_at':None,'first_network_byte_at':None,'server_inference_seconds':None,
            'unknown_reason':'historical_request_stream_timing_not_exported','events':within})
    # Build in-flight intervals. A model response can overlap a background check.
    intervals=[];pending={}
    for e in events:
        kind=e['kind'];key=None;opening=False
        if kind=='client.tool_dispatch_recorded':key=('tool',e.get('call_id'));opening=True
        elif kind=='client.tool_result_recorded':key=('tool',e.get('call_id'))
        elif kind=='shell.start':key=('shell',e.get('item_id'));opening=True
        elif kind=='shell.end':key=('shell',e.get('item_id'))
        elif kind=='native.submitted':key=('native',e.get('attempt'));opening=True
        elif kind=='native.completed':key=('native',e.get('attempt'))
        elif kind=='context_compaction.start':key=('compaction',None);opening=True
        elif kind=='context_compaction.end':key=('compaction',None)
        if key is not None:
            if opening:pending[key]=e['unix']
            elif key in pending:intervals.append((pending.pop(key),e['unix']))
    end=start+final['clock']['wall_seconds']
    intervals.extend((t,end) for t in pending.values())
    def subtract(lo,hi):
        pieces=[(lo,hi)]
        for a,b in intervals:
            pieces=[p for x,y in pieces for p in ((x,min(y,a)),(max(x,b),y)) if p[1]>p[0]] if b>lo and a<hi else pieces
        return pieces
    silences=[]
    for previous,current in zip(events,events[1:]):
        if current['unix']-previous['unix']<=30:continue
        for a,b in subtract(previous['unix'],current['unix']):
            if b-a>30:
                silences.append({'start_seconds':a-start,'end_seconds':b-start,'seconds':b-a,
                    'previous_event':previous,'next_event':current,
                    'phase':'client_response_path_unobserved','server_inference_seconds':None,
                    'request_sent_at':None,'first_network_byte_at':None})
    # Complete boundaries, not just differences between two usage counters.
    for gap in gaps:
        choices=[v for v in silences if v['start_seconds']>=gap['start_seconds']-.1 and v['end_seconds']<=gap['end_seconds']+.1]
        gap['operation_silences']=choices
        gap['largest_silent_operation_boundary']=max(choices,key=lambda v:v['seconds']) if choices else None
        for obsolete in ('last_observed_local_completion','next_recorded_model_item','local_completion_to_model_record_seconds'):
            gap.pop(obsolete,None)
    database=home/'logs_2.sqlite';n=None
    if database.exists():
        connection=sqlite3.connect('file:'+str(database)+'?mode=ro',uri=True)
        try:n=connection.execute('select count(*) from logs').fetchone()[0]
        finally:connection.close()
    return {'schema':'agent-operation-timeline-v1','start_unix':start,'events':events,'long_response_gaps':gaps,
        'operation_silences':silences,'official_log_database_rows':n,'limits':['model item records are not first-token timestamps',
        'tool completion to model record is client-observed response-path time, not measured server inference',
        'overlapping operations are retained; durations must not be naively summed']}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('/results'));p.add_argument('--home',type=Path,default=Path('/home/agent/.codex'));p.add_argument('--sandbox',type=Path,default=Path('/sandbox'));a=p.parse_args()
    print(json.dumps(extract(a.root,a.home,a.sandbox)))