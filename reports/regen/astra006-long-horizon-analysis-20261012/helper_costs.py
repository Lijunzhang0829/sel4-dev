"""Read-only experiment analysis; writes derived reports in this directory only."""
import collections
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))
from regen.infrastructure.incremental_log import read_artifact

RUN = ROOT / 'reports/regen/refine-astra-medium-20261005-006/proof'
REUSE = OUT.parent / 'astra006-repository-reuse-20261013/analysis.json'
reuse = json.loads(REUSE.read_text())
records = reuse['records']
text_edges = {tuple(e) for e in reuse['text_edges']}
native_edges = {tuple(e) for e in reuse['native_edges']}
consumers = collections.defaultdict(set)
adj = collections.defaultdict(set)
for a, b in text_edges:
    consumers[b].add(a)
    adj[a].add(b)
contract_closure = set()
for identity, row in records.items():
    if row['role'] != 'contract':
        continue
    todo, seen = [identity], set()
    while todo:
        x = todo.pop()
        if x in seen:
            continue
        seen.add(x)
        todo.extend(adj[x] - seen)
    contract_closure.update(seen)

FIELDS = ['inputTokens', 'cachedInputTokens', 'outputTokens']
def tokens(events, lo=float('-inf'), hi=float('inf')):
    totals = collections.Counter()
    # Deltas, not repeated cumulative totals or both local and protocol receipts.
    prev = collections.defaultdict(dict)
    resets = 0
    for e in events:
        m = e['message']
        if m.get('method') != 'thread/tokenUsage/updated':
            continue
        p = m['params']; thread = p['threadId']; cur = p['tokenUsage']['total']
        old = prev[thread]
        if old and cur.get('totalTokens', 0) < old.get('totalTokens', 0):
            resets += 1
            old = {}
        if lo <= e['time'] <= hi:
            for k in FIELDS:
                totals[k] += max(0, cur.get(k, 0) - old.get(k, 0))
        prev[thread] = cur
    return {'input': totals['inputTokens'], 'cached_input': totals['cachedInputTokens'],
            'uncached_input': totals['inputTokens'] - totals['cachedInputTokens'],
            'output': totals['outputTokens'],
            'total': totals['inputTokens'] + totals['outputTokens'], 'counter_resets': resets}

result = {'method': {
    'generated': 'Unique helper IDs in initial/final graphs, saved proposals, and transcript edit_theory sources; IDs, not distinct statement versions.',
    'unused': 'No explicit identifier reference from any final accepted proof in repository; NOT native-certified unused.',
    'cost': 'Only 006 requests above initial batch_requests. Native completed marker intervals include local context setup. Missing END is unknown, not zero.',
    'tokens': 'Cumulative thread/tokenUsage/updated deltas; cached input is a subset of input. No exact per-helper LLM attribution.',
    'wall': 'Observed transcript spans summed across process instances, excluding inter-instance gaps; not effective-generation time.'},
    'global_snapshot': reuse['summary'], 'theories': {}, 'cases': {}, 'sources': []}

for theory, quote_line, ranges in [
    ('Scheduler_Dispatch', 4135, {'schedule': (65, 96), 'tcb_valid': (77, 85)}),
    ('Untyped_Object_Lifecycle', 5242, {'reset': (68, 77), 'machine_op': (68, 73)})]:
    commits = sorted((RUN/'theories'/theory/'acceptance/commits').glob('*.json'))
    first = read_artifact(commits[0])['state']; final = read_artifact(commits[-1])['state']
    baseline = first['batch_requests']; accepted = set(final['accepted_order'])
    initial_nodes = {n['id']: n for n in first['graph']['nodes'] if n['role'] == 'helper'}
    nodes = dict(initial_nodes)
    nodes.update({n['id']: n for n in final['graph']['nodes'] if n['role'] == 'helper'})
    histories = {h['request_number']: h for h in final['batch_history']}
    batches = {}
    sha_to_request = {}
    for p in sorted((RUN/'theories'/theory/'batch-requests').glob('*/proposal.json')):
        n = int(p.parent.name)
        if n <= baseline:
            continue
        proposal = read_artifact(p)
        for node in proposal.get('graph', {}).get('generated_nodes', []):
            if node.get('role') == 'helper':
                nodes[node['id']] = node
        checkpath = p.with_name('check.json')
        data = read_artifact(checkpath) if checkpath.exists() else {}
        c = data.get('check', {})
        sha = data.get('document_source_sha256')
        if sha:
            sha_to_request[sha] = n
        starts = {}; durations = collections.defaultdict(float); incomplete = set()
        for kind, identity, value in re.findall(r'^REGEN_TIMING_(START|END) (\S+) ([0-9.]+)', c.get('log_content', ''), re.M):
            if kind == 'START':
                starts[identity] = float(value); incomplete.add(identity)
            elif identity in starts:
                durations[identity] += float(value) - starts.pop(identity)
                incomplete.discard(identity)
        batches[n] = {'request': n, 'reason': proposal.get('reason'),
            'node_results': data.get('node_results', []),
            'action_s': histories.get(n, {}).get('cost_seconds', {}).get('action'),
            'candidate_s': c.get('wall_ms', 0) / 1000 if 'wall_ms' in c else None,
            'prefix_s': c.get('prefix_prepare_ms', 0) / 1000 if 'prefix_prepare_ms' in c else None,
            'node_completed_s': dict(durations), 'node_unfinished_timing': sorted(incomplete),
            'source': str(checkpath.relative_to(ROOT))}
    transcripts = []
    first_seen = {}
    phase_start = None
    for p in sorted((RUN/'parallel-sessions').glob('*/'+theory+'/**/session-transcript.jsonl')):
        events=[]
        for ln, line in enumerate(p.open(), 1):
            e=json.loads(line); m=e.get('message', {}); m=m.get('parsed', m)
            events.append({'time': e['time_unix'], 'line': ln, 'message': m})
            if 'retry-' not in str(p) and ln == quote_line:
                phase_start=e['time_unix']
            if m.get('method') == 'item/started':
                it=m.get('params', {}).get('item', {})
                args=it.get('arguments', {})
                if isinstance(args, str):
                    try: args=json.loads(args)
                    except ValueError: args={}
                prop=args.get('proposal', {}) if isinstance(args, dict) else {}
                if prop.get('action') == 'edit_theory':
                    source = prop.get('source', '') + '\n'.join(x.get('new', '') for x in prop.get('edits', []))
                    for raw in re.findall(r'\(\* REGEN-BEGIN (\{.*?\}) \*\)', source):
                        marker=json.loads(raw)
                        if marker.get('role') == 'helper':
                            nodes.setdefault(marker['id'], {'id': marker['id'], 'name': marker['id'].removeprefix('helper:'), 'role': 'helper'})
                            first_seen[marker['id']] = min(first_seen.get(marker['id'], float('inf')), e['time_unix'])
                req=sha_to_request.get(prop.get('source_sha256'))
                if req and prop.get('action') == 'check_theory':
                    batches[req]['call_start']=e['time_unix']
                    batches[req]['transcript_line']=ln
        transcripts.append((p, events))
        result['sources'].append({'path':str(p.relative_to(ROOT)), 'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
    def window(lo, hi=float('inf')):
        sums=collections.Counter(); spans=[]
        for p, ev in transcripts:
            sums.update(tokens(ev, lo, hi))
            start=max(lo, ev[0]['time']); end=min(hi, ev[-1]['time'])
            if end >= start:
                spans.append({'path':str(p.relative_to(ROOT)), 'start':start, 'end':end, 'seconds':end-start})
        return {'tokens':dict(sums), 'observed_wall_s':sum(x['seconds'] for x in spans), 'spans':spans}
    helper_rows=[]
    for identity, node in sorted(nodes.items()):
        hit=[b for b in batches.values() if any(x['node_id']==identity for x in b['node_results'])]
        outcomes=collections.Counter(x['status'] for b in hit for x in b['node_results'] if x['node_id']==identity)
        helper_rows.append({'id':identity, 'name':node.get('name'), 'in_initial_graph':identity in initial_nodes,
            'first_seen_in_target_phase':identity not in initial_nodes and first_seen.get(identity, 0) >= phase_start,
            'accepted':identity in accepted, 'accepted_initially':identity in first['accepted_order'],
            'text_consumers':sorted(consumers[identity]), 'in_accepted_contract_text_closure':identity in contract_closure,
            'native_consumers':sorted(a for a,b in native_edges if b==identity),
            'requests':[b['request'] for b in hit], 'outcomes':dict(outcomes),
            'completed_native_s':sum(b['node_completed_s'].get(identity,0) for b in hit),
            'incomplete_native_intervals':sum(identity in b['node_unfinished_timing'] for b in hit),
            'shared_batch_action_s':sum(b['action_s'] or 0 for b in hit),
            'llm_tokens':None})
    accepted_helpers=[h for h in helper_rows if h['accepted']]
    summary={'initial_batch_requests':baseline,'generated_helper_ids':len(nodes),
        'initial_graph_helpers':len(initial_nodes),'new_helper_ids':len(set(nodes)-set(initial_nodes)),
        'accepted_helpers':len(accepted_helpers),
        'inherited_accepted_helpers':sum(h['accepted_initially'] for h in helper_rows),
        'newly_accepted_helpers':sum(h['accepted'] and not h['accepted_initially'] for h in helper_rows),
        'not_accepted_helper_ids':sum(not h['accepted'] for h in helper_rows),
        'target_phase_new_helper_ids':[h['name'] for h in helper_rows if h['first_seen_in_target_phase']],
        'accepted_without_text_consumers':[h['name'] for h in accepted_helpers if not h['text_consumers']],
        'accepted_outside_contract_text_closure':[h['name'] for h in accepted_helpers if not h['in_accepted_contract_text_closure']]}
    result['theories'][theory]={'summary':summary,'whole_006_observed':window(float('-inf')),
        'target_phase_observed':window(phase_start),'helpers':helper_rows,'batches':batches}
    for label,(lo,hi) in ranges.items():
        bs=[b for n,b in batches.items() if lo<=n<=hi]
        result['cases'][label]={'theory':theory,'requests':[b['request'] for b in bs],
            'action_s':sum(b['action_s'] or 0 for b in bs),
            'candidate_s':sum(b['candidate_s'] or 0 for b in bs),
            'prefix_s':sum(b['prefix_s'] or 0 for b in bs),
            'missing_candidate_requests':[b['request'] for b in bs if b['candidate_s'] is None],
            'observed':window(phase_start) if label in ['reset','schedule'] else None}
    print(theory,json.dumps(summary,ensure_ascii=False),flush=True)
    print('PHASE',json.dumps(result['theories'][theory]['target_phase_observed'],ensure_ascii=False),flush=True)

(OUT/'helper-costs.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
with (OUT/'helper-costs.csv').open('w') as f:
    columns=['theory','name','in_initial_graph','accepted_initially','accepted','first_seen_in_target_phase',
             'text_consumers','in_accepted_contract_text_closure','requests','outcomes','completed_native_s',
             'incomplete_native_intervals','shared_batch_action_s','llm_tokens']
    writer=csv.DictWriter(f,fieldnames=columns,lineterminator='\n');writer.writeheader()
    for theory,v in result['theories'].items():
        for h in v['helpers']:
            row={'theory':theory,**h}
            writer.writerow({k:json.dumps(row[k],ensure_ascii=False) if isinstance(row[k],(dict,list)) else row[k] for k in columns})
print('CASES',json.dumps(result['cases'],ensure_ascii=False),flush=True)
