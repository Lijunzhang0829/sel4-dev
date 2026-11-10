"""Local official OTLP receiver; stores timing metadata only, never API payloads.

This is not an LLM proxy. It cannot send/retry a model request or change budgets.
"""
import gzip,io,json,math,os,re,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

SAFE_KEYS=frozenset({'event.name','event_name','model','attempt','status','status_code',
    'http.status_code','http_status_code','http.response.status_code','success','duration_ms','duration','kind','event.kind','event_kind',
    'request_id','server_request_id','provider_request_id','response_id','conversation.id','conversation_id','thread_id','session_id',
    'request.id','response.id','tool','tool_name','error.type','error_type','error.code',
    'input_token_count','output_token_count','cached_token_count','reasoning_token_count',
    'input_tokens','output_tokens','cached_input_tokens','reasoning_output_tokens',
    'service.name','service.version','app.version','environment','prompt_length','prompt_len'})
SAFE_NAME=re.compile(r'^[A-Za-z0-9_.:/-]{1,160}$')
MAX_BODY=16*1024*1024

def scalar(value):
    if not isinstance(value,dict):return None
    for key in ('stringValue','boolValue','intValue','doubleValue'):
        if key in value:
            v=value[key]
            if key=='intValue':
                try:return int(v)
                except (TypeError,ValueError):return None
            if isinstance(v,float) and not math.isfinite(v):return None
            if isinstance(v,(str,bool,int,float)):return v
    return None

def attributes(values):
    out={}
    for row in values or []:
        key=row.get('key','')
        if key not in SAFE_KEYS and not (key.startswith('responses_api_') and key.endswith(('_ms','.duration_ms'))):continue
        v=scalar(row.get('value'))
        if isinstance(v,str) and not SAFE_NAME.fullmatch(v):continue
        if v is not None:out[key]=v
    return out

def number(value):
    try:v=float(value)
    except (ValueError,TypeError):return None
    return v if math.isfinite(v) else None

def normalize(path,payload):
    if not isinstance(payload,dict):raise ValueError('expected OTLP object')
    rows=[]
    if path=='/v1/logs':
        for resource in payload.get('resourceLogs',[]):
            for scope in resource.get('scopeLogs',[]):
                for item in scope.get('logRecords',[]):
                    attrs=attributes(item.get('attributes'));body=scalar(item.get('body'))
                    event=attrs.get('event.name',attrs.get('event_name'))
                    if event is None and isinstance(body,str) and re.fullmatch(r'codex\.[a-zA-Z0-9_.]+',body):event=body
                    rows.append({'source':'official_otel_log','event':event or 'unrecognized_event',
                        'time_unix_nano':item.get('timeUnixNano'),'observed_unix_nano':item.get('observedTimeUnixNano'),'trace_id':item.get('traceId'),
                        'span_id':item.get('spanId'),'attributes':attrs})
    elif path=='/v1/traces':
        for resource in payload.get('resourceSpans',[]):
            for scope in resource.get('scopeSpans',[]):
                for item in scope.get('spans',[]):
                    name=item.get('name','');name=name if SAFE_NAME.fullmatch(name) else 'unrecognized_span'
                    rows.append({'source':'official_otel_span','event':name,'trace_id':item.get('traceId'),
                        'span_id':item.get('spanId'),'parent_span_id':item.get('parentSpanId'),
                        'start_unix_nano':item.get('startTimeUnixNano'),'end_unix_nano':item.get('endTimeUnixNano'),
                        'attributes':attributes(item.get('attributes'))})
    elif path=='/v1/metrics':
        for resource in payload.get('resourceMetrics',[]):
            for scope in resource.get('scopeMetrics',[]):
                for item in scope.get('metrics',[]):
                    name=item.get('name','')
                    if not name.startswith('codex.') or not SAFE_NAME.fullmatch(name):continue
                    for typ in ('sum','gauge','histogram','exponentialHistogram'):
                        for point in (item.get(typ) or {}).get('dataPoints',[]):
                            row={'source':'official_otel_metric','event':name,'metric_type':typ,
                                'time_unix_nano':point.get('timeUnixNano'),'start_unix_nano':point.get('startTimeUnixNano'),
                                'aggregation_temporality':item[typ].get('aggregationTemporality'),
                                'attributes':attributes(point.get('attributes'))}
                            for key in ('count','sum','min','max','asDouble','asInt'):
                                if key in point:row[key]=number(point[key])
                            rows.append(row)
    else:raise ValueError('unsupported OTLP path')
    # IDs and timestamps are metadata, but validate their shape before storage.
    for row in rows:
        event=row['event']
        row['phase']=('server_reported_inference_aggregate' if 'responses_api_inference_time' in event
            else 'server_reported_overhead_aggregate' if 'responses_api_overhead' in event
            else 'client_request_operation' if event in ('codex.api_request','codex.websocket_request')
            else 'client_stream_event' if event in ('codex.sse_event','codex.websocket_event')
            else 'client_tool_operation' if event=='codex.tool_result'
            else 'other_official_metadata')
        for key in list(row):
            value=row[key]
            if key.endswith('_nano'):
                try:row[key]=int(value) if value is not None else None
                except (ValueError,TypeError):row[key]=None
            elif key.endswith('_id') and value is not None:
                if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_=+/-]{1,192}',value):row[key]=None
    return rows


class TimingObserver:
    def __init__(self,directory):
        self.directory=Path(directory);self.directory.mkdir(mode=0o700,parents=True,exist_ok=False)
        fd=os.open(self.directory/'events.jsonl',os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_EXCL,0o600)
        self.stream=os.fdopen(fd,'w');self.lock=threading.Lock();self.records=0;self.errors=0;self.phases={};owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                self.connection.settimeout(5)
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0<=length<=MAX_BODY:raise ValueError('body size')
                    body=self.rfile.read(length)
                    if self.headers.get('Content-Encoding')=='gzip':
                        with gzip.GzipFile(fileobj=io.BytesIO(body)) as f:body=f.read(MAX_BODY+1)
                    if len(body)>MAX_BODY:raise ValueError('expanded body size')
                    rows=normalize(self.path,json.loads(body))
                    with owner.lock:
                        for row in rows:
                            row.update(received_unix=time.time(),received_monotonic=time.monotonic())
                            owner.stream.write(json.dumps(row,separators=(',',':'))+'\n')
                            phase=row['phase'];owner.phases[phase]=owner.phases.get(phase,0)+1
                        owner.stream.flush();owner.records+=len(rows)
                    result=b'{}';self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(result)));self.end_headers();self.wfile.write(result)
                except (ValueError,TypeError,KeyError,AttributeError,OverflowError,OSError):
                    with owner.lock:owner.errors+=1
                    self.send_response(400);self.end_headers()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads=False
        self.closed=False
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
    def config_args(self):
        base='http://127.0.0.1:'+str(self.server.server_address[1]);args=[]
        for field,path in [('exporter','logs'),('trace_exporter','traces'),('metrics_exporter','metrics')]:
            args+=['-c','otel.'+field+'={otlp-http={endpoint="'+base+'/v1/'+path+'",protocol="json"}}']
        for value in ['otel.log_user_prompt=false','otel.log_agent_responses=false','otel.log_guardian_assessments=false','otel.tool_result.max_bytes=0']:
            args+=['-c',value]
        return args
    def command(self,command):
        if not command or command[-1]!='-':raise ValueError('expected stdin-prompt official CLI command')
        return list(command[:-1])+self.config_args()+['-']
    def snapshot(self):
        with self.lock:return {'records':self.records,'rejected_batches':self.errors,
            'phase_record_counts':dict(self.phases),
            'server_inference_available':self.phases.get('server_reported_inference_aggregate',0)>0,
            'request_timing_available':self.phases.get('client_request_operation',0)>0,
            'scope':'loopback-only official timing metadata; absent durations remain unknown, not zero'}
    def close(self):
        if self.closed:return
        self.closed=True
        self.server.shutdown();self.server.server_close();self.thread.join()
        with self.lock:self.stream.close()
