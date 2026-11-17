"""P4 read-only helper use and independent-check accounting from saved artifacts."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time

from regen.infrastructure.incremental_log import read_artifact
from regen.execution.proof_commits import digest


def reachable(start, edges):
    seen, todo = set(), list(edges.get(start, []))
    while todo:
        node = todo.pop()
        if node not in seen:
            seen.add(node); todo.extend(edges.get(node, []))
    return seen


def dependency_names(receipt):
    raw = receipt['raw_content']
    if hashlib.sha256(raw.encode()).hexdigest() != receipt['raw_sha256']:
        raise ValueError('dependency receipt hash mismatch')
    return {bytes.fromhex(line.split('\t')[1]).decode() for line in raw.splitlines()
            if line.startswith('THM_DEP\t')}


def analyze(root):
    root = Path(root); cutoff = time.time()
    scan_root = root if (root/'theories').exists() else root/'proof'
    theories, attempts, helpers = [], [], []
    counts, costs, missing = Counter(), Counter(), Counter()
    native_seen, body_seen, context_seen = set(), set(), set()
    for pointer in sorted(scan_root.glob('theories/*/acceptance/HEAD.json')):
        theory = pointer.parents[1].name
        head = read_artifact(pointer)
        commit = read_artifact(pointer.parent/'commits'/('%06d.json' % head['sequence']))
        state = commit['state']; nodes = {n['id']: n for n in state['graph']['nodes']}
        proofs = state['proofs']; contracts = {k for k, n in nodes.items() if n['role'] == 'contract'}
        accepted_contracts = contracts & set(proofs)
        local_helpers = {k for k, n in nodes.items() if n['role'] == 'helper'}
        planned = {k: set(n.get('dependencies', [])) for k, n in nodes.items()}
        lexical = {}
        for key, body in proofs.items():
            # A lexical indicator only; comments/automation can make it imperfect.
            lexical[key] = {h for h in local_helpers if h != key and re.search(
                r'(?<![\w\x27])'+re.escape(nodes[h]['name'])+r'(?![\w\x27])', body)}
        receipts = {}
        if state.get('seal_id'):
            for sequence in range(head['sequence'], -1, -1):
                path = pointer.parent/'commits'/('%06d.json' % sequence)
                if not path.exists(): continue
                record = read_artifact(path)
                if record['event'] == 'seal':
                    if record['state']['proofs'] == proofs and record['state']['graph'] == state['graph']:
                        receipts = {k: dependency_names(v) for k, v in
                                    record['evidence'].get('dependency_receipts', {}).items()}
                    break
        helper_names = Counter(nodes[h]['name'] for h in local_helpers)
        for h in sorted(local_helpers):
            name = nodes[h]['name']
            named = [k for k in accepted_contracts if k in receipts and helper_names[name] == 1
                     and any(n == name or n.endswith('.'+name) for n in receipts[k])]
            helpers.append({'theory': theory, 'entry_id': h, 'name': name, 'accepted': h in proofs,
                'planned_contract_consumers': sorted(k for k in contracts if h in reachable(k, planned)),
                'lexical_accepted_contract_consumers': sorted(k for k in accepted_contracts if h in reachable(k, lexical)),
                'native_named_contract_matches': sorted(named),
                'native_contract_receipts_available': len(accepted_contracts & receipts.keys())})
        counts.update(accepted_contracts=len(accepted_contracts), current_helpers=len(local_helpers),
                      accepted_helpers=len(local_helpers & proofs.keys()),
                      contracts_with_seal_receipts=len(accepted_contracts & receipts.keys()))
        theories.append({'theory': theory, 'head': head, 'status': state['status'],
                         'accepted_contracts': len(accepted_contracts), 'helpers':len(local_helpers)})
    for path in sorted(scan_root.glob('theories/*/batch-requests/*/independent-entries/*/check.json')):
        if path.stat().st_mtime > cutoff: continue
        evidence = read_artifact(path); check = evidence.get('check') or {}
        candidate_path = path.with_name('candidate.json')
        if not candidate_path.exists(): missing['candidate'] += 1; continue
        candidate = read_artifact(candidate_path)
        key = evidence.get('independent_entries', {}).get('entry_id')
        node = next((n for n in candidate['graph']['nodes'] if n['id'] == key), None)
        if node is None: missing['entry'] += 1; continue
        theory = path.parents[4].name
        body = candidate['proofs'].get(key)
        if body is None: missing['proof_body'] += 1; continue
        body_hash = digest(body)
        same_body_key = (theory, key, body_hash)
        context = {'old_binding': evidence.get('old_binding'), 'imports': evidence.get('imports'),
                   'prefix_sha256': check.get('prefix_sha256'),
                   'declaration': {k: node.get(k) for k in ('statement','context')}}
        same_context_key = (*same_body_key, digest(context))
        identity = (check.get('log'), check.get('log_sha256'))
        duration = check.get('execution_wall_ms', check.get('wall_ms'))
        # Count native execution once; do not add candidate time to its enclosing wall time.
        charge = identity[0] is not None and identity[1] is not None and identity not in native_seen
        if charge and isinstance(duration, (int, float)):
            costs[node['role']+'_execution_ms'] += duration
        else: missing['missing_or_duplicate_execution_timing'] += 1
        native_seen.add(identity)
        attempts.append({'theory':theory, 'entry_id':key, 'name':node['name'], 'role':node['role'],
            'verdict':check.get('verdict'), 'body_sha256':body_hash,
            'same_body_seen_before':same_body_key in body_seen,
            'same_body_and_context_seen_before':same_context_key in context_seen,
            'context_sha256':digest(context), 'execution_ms':duration if charge else None,
            'path':str(path)})
        body_seen.add(same_body_key); context_seen.add(same_context_key)
    helper_keys = {(h['theory'], h['entry_id']) for h in helpers}
    for h in helpers:
        rows = [a for a in attempts if (a['theory'], a['entry_id']) == (h['theory'], h['entry_id'])]
        h['independent_attempts'] = len(rows)
        h['recorded_execution_ms'] = sum(a['execution_ms'] or 0 for a in rows)
    counts.update(independent_attempts=len(attempts),
        helper_attempts=sum(a['role']=='helper' for a in attempts),
        contract_attempts=sum(a['role']=='contract' for a in attempts),
        same_body_retries=sum(a['same_body_seen_before'] for a in attempts),
        same_body_and_context_retries=sum(a['same_body_and_context_seen_before'] for a in attempts),
        helper_attempts_absent_from_current_graph=sum(a['role']=='helper' and (a['theory'],a['entry_id']) not in helper_keys for a in attempts),
        helpers_lexically_reaching_contract=sum(bool(h['lexical_accepted_contract_consumers']) for h in helpers),
        helpers_with_native_named_matches=sum(bool(h['native_named_contract_matches']) for h in helpers))
    return {'observed_at_utc':datetime.now(timezone.utc).isoformat(), 'run':str(root),
        'counts':dict(counts), 'costs':dict(costs), 'missing':dict(missing),
        'theories':theories, 'helpers':helpers, 'attempts':attempts,
        'limitations':['HEADs pinned per theory, not an atomic repository snapshot during live generation.',
            'Reads recorded commits/receipt hashes; does not rerun native acceptance or journal validation.',
            'Declared dependencies are plans; lexical references are heuristics, not proof dependencies.',
            'Native named matches use exact or qualified suffix names; aliases/scope and inlining can obscure attribution.',
            'No match or no seal receipt does not establish that a helper is unused or unnecessary.',
            'Costs cover independent-entry checks only, not whole checks, sketches, publication, LLM time or total experiment time.',
            'Same body/context retries are candidates for review, never automatically safe to skip.']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir',type=Path); parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args(); report=analyze(args.run_dir)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
