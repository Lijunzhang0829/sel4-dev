"""Read-only extraction from Astra 006; writes only this analysis directory."""
import collections
import hashlib
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from regen.infrastructure.incremental_log import read_artifact
RUN = ROOT / 'reports/regen/refine-astra-medium-20261005-006/proof'
OUT = Path(__file__).resolve().parent
SELECTED = {'Scheduler_Dispatch': {'activateThread_corres', 'chooseThread_corres', 'schedule_corres'},
            'Untyped_Object_Lifecycle': {'deleteObjects_corres', 'resetUntypedCap_corres'}}
QUOTE_LINES = {'Scheduler_Dispatch': {1237,1303,2051,2210,2274,2655,2832,4135,4457,4790,6428,6823,6923,7260,7988},
               'Untyped_Object_Lifecycle': {3864,4293,4342,4411,4472,4967,5069,5137,5242,5285}}
RETRY_LINES = {'Scheduler_Dispatch': {115}, 'Untyped_Object_Lifecycle': {570,730,829,962,1049,1144,1534,1633,1722,1847,1933}}
rows, cases, sources = [], {}, []
for package_path in sorted((RUN/'packages').glob('*.json')):
    package = read_artifact(package_path)
    theory = package['theory']
    contracts = {c['contract_id']: c for c in package['contracts']}
    commits = sorted((RUN/'theories'/theory/'acceptance/commits').glob('*.json'))
    state = read_artifact(commits[-1])['state'] if commits else {}
    accepted = set(state.get('accepted_order', []))
    rows.append({'theory':theory, 'contracts':len(contracts), 'accepted':len(accepted & contracts.keys()),
                 'status':state.get('status','uninitialized'),
                 'open_contracts':[c['name'] for id,c in contracts.items() if id not in accepted]})
    if theory not in SELECTED: continue
    path = commits[-1]
    sources.append({'path':str(path.relative_to(ROOT)), 'file_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    history = state.get('batch_history', [])
    target_cases = []
    for node in state.get('graph',{}).get('nodes',[]):
        if node.get('name') not in SELECTED[theory]: continue
        id = node['id']
        mentions = [h for h in history if any(n['node_id']==id for n in h.get('node_results',[]))]
        counts = collections.Counter(n['status'] for h in mentions for n in h.get('node_results',[]) if n['node_id']==id)
        target_cases.append({'id':id,'name':node['name'],'accepted':id in accepted,'statement':node['statement'],
                             'final_proof':state.get('proofs',{}).get(id,''),'direct_outcomes':dict(counts),
                             'requests_reporting_target':[h.get('request_number') for h in mentions]})
    late_attempts=[]
    for h in history[-15:]:
        late_attempts.append({k:h[k] for k in ['request_number','action','node_results','proof_node_ids','cost_seconds'] if k in h})
    objects=[]
    for obj in state.get('batch_attempt_objects',{}).values():
        feedback=obj.get('feedback',{}); check=feedback.get('check',{})
        objects.append({'request_number':obj.get('request_number'),'reason':obj.get('candidate',{}).get('reason'),
                        'diagnostic':check.get('diagnostic',''),'node_results':feedback.get('node_results',[]),
                        'timing':{k:check.get(k) for k in ['execution_wall_ms','prefix_prepare_ms','wall_ms','timeout_scope']},
                        'cost_seconds':feedback.get('cost_seconds',{})})
    dialogue=[]
    for transcript in sorted((RUN/'parallel-sessions').glob('*/'+theory+'/**/session-transcript.jsonl')):
        keep=RETRY_LINES[theory] if 'retry-' in str(transcript) else QUOTE_LINES[theory]
        sources.append({'path':str(transcript.relative_to(ROOT)), 'file_sha256':hashlib.sha256(transcript.read_bytes()).hexdigest()})
        for line_number,line in enumerate(transcript.open(),1):
            if line_number not in keep: continue
            event=json.loads(line); msg=event.get('message',{}); msg=msg.get('parsed',msg)
            item=msg.get('params',{}).get('item',{})
            if msg.get('method')=='item/completed' and item.get('type')=='agentMessage':
                dialogue.append({'path':str(transcript.relative_to(ROOT)), 'line':line_number,
                                 'time_unix':event.get('time_unix'),'text':item.get('text','')})
    cases[theory]={'commit':str(path.relative_to(ROOT)), 'history_requests':len(history),
                  'contracts':target_cases,'last_requests':late_attempts,'retained_attempts':objects,'dialogue':dialogue}
summary={'accepted_contracts':sum(r['accepted'] for r in rows),'contracts':sum(r['contracts'] for r in rows),
         'sealed_theories':sum(r['status']=='sealed' for r in rows),
         'uninitialized_contracts':sum(r['contracts'] for r in rows if r['status']=='uninitialized'),
         'initialized_open_contracts':sum(r['contracts']-r['accepted'] for r in rows if r['status']!='uninitialized')}
assert summary['accepted_contracts']==199 and summary['contracts']==287
(OUT/'evidence.json').write_text(json.dumps({'summary':summary,'theories':rows,'cases':cases,'sources':sources},ensure_ascii=False,indent=2)+'\n')
print(json.dumps(summary))
