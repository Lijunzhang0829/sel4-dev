"""Read-only final-snapshot dependency and retrieval analysis."""
import collections
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from regen.infrastructure.incremental_log import read_artifact

RUN = ROOT / 'reports/regen/refine-astra-medium-20261005-006/proof'
OUT = Path(__file__).resolve().parent
records, states, commits, aliases, queries = {}, {}, {}, {}, []
for p in sorted((RUN / 'packages').glob('*.json')):
    package = read_artifact(p)
    theory = package['theory']
    files = sorted((RUN / 'theories' / theory / 'acceptance/commits').glob('*.json'))
    if not files:
        continue
    commit = read_artifact(files[-1])
    commits[theory] = commit
    state = states[theory] = commit['state']
    accepted = set(state['accepted_order'])
    for node in state['graph']['nodes']:
        if node['id'] not in accepted:
            continue
        records[node['id']] = {k: node[k] for k in ['id', 'name', 'role']}
        records[node['id']]['theory'] = theory
        for prefix in ['Regen_' + theory, 'Complete_Audit_' + theory, theory]:
            aliases[prefix + '.' + node['name']] = node['id']
    queries.extend({'theory': theory, **q} for q in state.get('observation_queries', []))

def resolve(name):
    if name in aliases:
        return aliases[name]
    for prefix in ['Generated_Refine.', 'Draft.']:
        if name.startswith(prefix) and name[len(prefix):] in aliases:
            return aliases[name[len(prefix):]]
    return None

native_edges, native_targets, native_rows = set(), set(), {}
unresolved_generated = collections.Counter()
for theory, commit in commits.items():
    dependencies = commit['evidence'].get('dependencies', {}).get('complete_theory_audit', {}).get('proof_dependencies', {})
    for identity, node in records.items():
        if node['theory'] != theory or identity not in dependencies:
            continue
        native_targets.add(identity)
        native_rows[identity] = dependencies[identity]
        for dependency in dependencies[identity].get('dependencies', []):
            name = dependency['canonical_name']
            target = resolve(name)
            if target and target != identity:
                native_edges.add((identity, target))
            elif not target and ('Regen_' in name or 'Complete_Audit_' in name):
                unresolved_generated[name] += 1

text_edges = set()
by_name = collections.defaultdict(list)
for identity, node in records.items():
    by_name[node['name']].append(identity)
for theory, state in states.items():
    imports = {x.removeprefix('Regen_') for x in commits[theory]['binding'].get('imports', [])}
    for identity, proof in state.get('proofs', {}).items():
        if identity not in records or not isinstance(proof, str):
            continue
        for token in set(re.findall(r"[A-Za-z_][A-Za-z0-9_'.]*", proof)):
            target = resolve(token)
            if target and target != identity:
                text_edges.add((identity, target))
            elif '.' not in token:
                matches = [x for x in by_name.get(token, []) if records[x]['theory'] == theory or records[x]['theory'] in imports]
                if len(matches) == 1 and matches[0] != identity:
                    text_edges.add((identity, matches[0]))

def stats(edges, targets):
    cross = {(a, b) for a, b in edges if records[a]['theory'] != records[b]['theory']}
    helpers = {(a, b) for a, b in edges if records[b]['role'] != 'contract'}
    contracts = {a for a in targets if records[a]['role'] == 'contract'}
    return {
        'targets': len(targets), 'contract_targets': len(contracts),
        'generated_fact_edges': len(edges), 'cross_theory_edges': len(cross),
        'theory_pairs': len({(records[a]['theory'], records[b]['theory']) for a, b in cross}),
        'contracts_using_cross_theory_fact': len({a for a, b in cross if a in contracts}),
        'helper_edges': len(helpers), 'contracts_using_helper': len({a for a, b in helpers if a in contracts}),
        'helpers_used_by_any_target': len({b for a, b in helpers}),
        'helpers_used_by_multiple_contracts': sum(len({a for a, dest in helpers if dest == b and a in contracts}) >= 2 for b in {y for x, y in helpers}),
    }

consumers = collections.defaultdict(set)
for a, b in native_edges:
    consumers[b].add(a)
top_helpers = []
for identity, users in consumers.items():
    if records[identity]['role'] == 'contract':
        continue
    top_helpers.append({**records[identity], 'consumers': len(users),
        'contract_consumers': sum(records[a]['role'] == 'contract' for a in users),
        'consumer_theories': sorted({records[a]['theory'] for a in users}),
        'consumer_names': sorted(records[a]['theory'] + '.' + records[a]['name'] for a in users)})
top_helpers.sort(key=lambda x: (-x['contract_consumers'], -x['consumers'], x['name']))
pairs = collections.Counter((records[a]['theory'], records[b]['theory']) for a, b in native_edges if records[a]['theory'] != records[b]['theory'])

retrieved = {i for q in queries for i in q.get('returned_record_ids', []) if isinstance(i, str)}
premise_ids = {i for i in retrieved if i.startswith('premise:')}
generated_ids = {i for i in retrieved if i.startswith('generated:')}
generated_matches = {}
for identity in generated_ids:
    parts = identity.split(':', 2)
    if len(parts) != 3:
        continue
    matches = [i for i, n in records.items() if n['theory'] == parts[1] and n['name'] == parts[2]]
    if len(matches) == 1:
        generated_matches[identity] = matches[0]
database = Path(read_artifact(RUN / 'run.json')['premise_database'])
connection = sqlite3.connect('file:' + str(database) + '?mode=ro', uri=True)
premise_names = {}
for (table,) in connection.execute("select name from sqlite_master where type='table'").fetchall():
    columns = [c[1] for c in connection.execute('pragma table_info("' + table + '")')]
    if 'record_id' in columns and 'canonical_name' in columns:
        premise_names = {row[0]: row[1] for row in connection.execute('select record_id, canonical_name from "' + table + '"') if row[0] in premise_ids}
        break
connection.close()
used_names = {d['canonical_name'] for row in native_rows.values() for d in row.get('dependencies', [])}
used_ids = {b for a, b in native_edges}
recall = {
    'query_index_rows': len(queries), 'theories_with_queries': len({q['theory'] for q in queries}),
    'rows_with_nonempty_result': sum(bool(q.get('returned_record_ids')) for q in queries),
    'unique_returned_ids': len(retrieved), 'returned_premise_ids': len(premise_ids),
    'returned_generated_ids': len(generated_ids), 'matched_premise_names': len(premise_names),
    'recalled_premises_also_in_native_dependencies': sum(n in used_names for n in premise_names.values()),
    'matched_accepted_generated_facts': len(generated_matches),
    'recalled_generated_also_used_native': sum(i in used_ids for i in generated_matches.values()),
    'note': 'Final query index overlap, not chronological causality or invocation totals.',
}
calls, dialogue = {}, []
for path in sorted((RUN / 'parallel-sessions').glob('*/*/**/session-transcript.jsonl')):
    for line_number, line in enumerate(path.open(), 1):
        event = json.loads(line)
        message = event.get('message', {})
        message = message.get('parsed', message)
        item = message.get('params', {}).get('item', {})
        if message.get('method') == 'item/started' and item.get('type') == 'dynamicToolCall':
            arguments = item.get('arguments', {})
            arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
            action = arguments.get('proposal', {}).get('action') if isinstance(arguments, dict) else None
            if action in ['query', 'read_facts', 'read_proofs', 'read_queries', 'observe']:
                calls[item['id']] = {'path': str(path.relative_to(ROOT)), 'line': line_number,
                    'action': action, 'arguments': arguments, 'time_unix': event.get('time_unix')}
        if message.get('method') == 'item/completed' and item.get('type') == 'agentMessage':
            text = item.get('text', '')
            if re.search(r'imported|retriev|query|search|sealed|bitmap', text, re.I) and not re.search(r'still|pending|no new|waiting|not returned', text, re.I):
                dialogue.append({'path': str(path.relative_to(ROOT)), 'line': line_number, 'text': text})
summary = {
    'accepted_contracts': sum(n['role'] == 'contract' for n in records.values()),
    'accepted_helpers': sum(n['role'] != 'contract' for n in records.values()),
    'native': stats(native_edges, native_targets), 'script_text': stats(text_edges, set(records)),
    'accepted_contracts_without_complete_audit': sum(n['role'] == 'contract' and i not in native_targets for i, n in records.items()),
    'recall': recall, '006_observed_recall_tool_calls': dict(collections.Counter(x['action'] for x in calls.values())),
    'unresolved_generated_dependency_names': dict(unresolved_generated),
}
adjacency = collections.defaultdict(set)
for source, target in native_edges:
    adjacency[source].add(target)
cross_contracts, helper_contracts = set(), set()
for source in native_targets:
    if records[source]['role'] != 'contract':
        continue
    seen, pending = set(), list(adjacency[source])
    while pending:
        target = pending.pop()
        if target in seen:
            continue
        seen.add(target)
        pending.extend(adjacency[target] - seen)
    if any(records[x]['theory'] != records[source]['theory'] for x in seen):
        cross_contracts.add(source)
    if any(records[x]['role'] == 'helper' for x in seen):
        helper_contracts.add(source)
summary['native']['contracts_with_cross_theory_dependency_via_generated_closure'] = len(cross_contracts)
summary['native']['contracts_with_helper_via_generated_closure'] = len(helper_contracts)
subactions = collections.Counter()
for call in calls.values():
    if call['action'] == 'observe':
        subactions.update(x.get('action') for x in call['arguments']['proposal'].get('reads', []))
summary['006_observe_subactions'] = dict(subactions)
summary['006_query_actions_including_observe'] = summary['006_observed_recall_tool_calls'].get('query', 0) + subactions['query']
result = {'summary': summary, 'records': records, 'native_edges': sorted(native_edges),
    'text_edges': sorted(text_edges), 'top_helpers': top_helpers,
    'theory_pairs': [{'consumer': a, 'provider': b, 'edges': count} for (a, b), count in pairs.most_common()],
    'queries': queries, 'generated_matches': generated_matches, 'premise_names': premise_names,
    'recall_calls_006': list(calls.values()), 'recall_dialogue_006': dialogue}
(OUT / 'analysis.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(summary, ensure_ascii=False, indent=2))
print('TOP_HELPERS', json.dumps(top_helpers[:5], ensure_ascii=False))
print('THEORY_PAIRS', pairs.most_common(8))
