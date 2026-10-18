"""Read-only pre-freeze interface review. History is only used after review commits.

No proof generation, admissions, production GREEN, or automatic plan mutation.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from regen.planning.import_contract import required_imports


def read(path):
    return json.loads(Path(path).read_text())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def analyze(plan, contracts):
    """Deterministic structural facts, not a difficulty/quality score."""
    theories = plan['theories']
    names = [t['name'] for t in theories]
    if len(names) != len(set(names)):
        raise ValueError('Duplicate theory')
    assigned = [c for t in theories for c in t['contract_ids']]
    expected = [c['contract_id'] for c in contracts]
    if Counter(assigned) != Counter(expected) or len(assigned) != len(set(assigned)):
        raise ValueError('Plan must cover every exact contract once')
    deps = {t['name']: [x['theory'] for x in required_imports({**t, 'schema_version': plan['schema_version']})] for t in theories}
    closure = {}
    def visit(name, stack):
        if name not in deps:
            raise ValueError('Unknown provider: ' + name)
        if name in stack:
            raise ValueError('Cyclic imports')
        if name not in closure:
            result = set()
            for provider in deps[name]:
                result.add(provider)
                result.update(visit(provider, stack | {name}))
            closure[name] = result
        return closure[name]
    for name in names:
        visit(name, set())
    sizes = {t['name']: len(t['contract_ids']) for t in theories}
    rows = []
    for name in names:
        downstream = [n for n in names if name in closure[n]]
        rows.append({'theory': name, 'contracts': sizes[name], 'required_imports': deps[name],
                     'transitive_providers': sorted(closure[name]),
                     'blocked_theories_if_unsealed': downstream,
                     'blocked_contracts_if_unsealed': sum(sizes[n] for n in downstream),
                     'provider_contracts_waited_for': sum(sizes[n] for n in closure[name])})
    return {'coverage': len(assigned), 'theories': rows,
            'interpretation': 'Reachability and waiting scope only; not proof necessity or measured latency.'}


def review_input(plan, contracts, structural):
    # Allowlist only: no original imports, paths, historical proofs or outcomes.
    return {'plan': plan, 'contracts': [{k: c[k] for k in
            ('contract_id', 'name', 'exact_statement', 'contract_owned_context', 'contract_owned_attributes') if k in c}
            for c in contracts], 'structural': structural}


INSTRUCTIONS = '''Review this fixed proof-repository plan BEFORE generation. Act as a consumer of each theory's interfaces.
No tools, proof scripts, historical knowledge of removed human proofs, or changes to fixed statements.
Inspect ALL theories. For each, report concrete external interfaces consumers expect, any precondition/coverage gap,
and what can remain a local helper. Do not infer proof dependencies from shared symbols alone.
Pending contracts are proposed interfaces, never established facts. Transitive imports count as available after sealing.
Evaluate seal barriers, misplaced tightly related contracts, speculative hard imports and unmet interface needs.
A large group or long chain is a risk signal, not automatically an error. A shared name is not a matching theorem.
Return JSON with exactly two arrays:
"theory_reviews": one object for EVERY theory, with "theory", "assessment" (nonempty text),
"consumed_contract_ids" (list of concrete expected external interfaces; may be empty).
"issues": at most 8 objects, each with "id", "kind" (missing_interface, precondition_gap, seal_barrier,
speculative_import, or grouping), "theories" (list), "contract_ids" (nonempty list),
"evidence" (nonempty explanation based on exact statements/plan), "scenario" (concrete downstream consequence),
"suggested_change", "tradeoff", "uncertainty" (all nonempty strings).
These are hypotheses, not certified defects. Report no issue if evidence is inadequate.
Do not optimize a numeric score or remove an import just because its use is unproved.
'''


def validate_review(value, plan, contracts):
    if set(value) != {'theory_reviews', 'issues'}:
        raise ValueError('Unexpected review fields')
    names = {t['name'] for t in plan['theories']}
    owner = {c: t['name'] for t in plan['theories'] for c in t['contract_ids']}
    ids = {c['contract_id'] for c in contracts}
    rows = value['theory_reviews']
    if not isinstance(rows, list) or Counter(r['theory'] for r in rows) != Counter(names):
        raise ValueError('Each theory needs exactly one consumer review')
    for row in rows:
        if not isinstance(row['assessment'], str) or not row['assessment'].strip():
            raise ValueError('Empty assessment')
        consumed = row['consumed_contract_ids']
        if not isinstance(consumed, list) or any(c not in ids or owner[c] == row['theory'] for c in consumed):
            raise ValueError('Unknown or non-external consumed contract')
    if not isinstance(value['issues'], list) or len(value['issues']) > 8:
        raise ValueError('Too many issues')
    seen = set()
    for issue in value['issues']:
        if not isinstance(issue['id'], str) or not issue['id'] or issue['id'] in seen:
            raise ValueError('Invalid issue identity')
        seen.add(issue['id'])
        if issue['kind'] not in {'missing_interface','precondition_gap','seal_barrier','speculative_import','grouping'}:
            raise ValueError('Unknown issue kind')
        for key, allowed in [('theories', names), ('contract_ids', ids)]:
            if not isinstance(issue[key], list) or not issue[key] or not set(issue[key]) <= allowed:
                raise ValueError('Ungrounded issue reference')
        for key in ['evidence','scenario','suggested_change','tradeoff','uncertainty']:
            if not isinstance(issue[key], str) or not issue[key].strip():
                raise ValueError('Missing issue explanation')
    return value


def history_comparison(plan, structural, review, history):
    """Exact-ID native dependency paths, including helper intermediates.

    A missing planned path means this historical proof route would need adaptation;
    it does not establish mathematical necessity or a failure of this new plan.
    """
    owner = {c: t['name'] for t in plan['theories'] for c in t['contract_ids']}
    reachable = {r['theory']: set(r['transitive_providers']) for r in structural['theories']}
    adjacency = defaultdict(list)
    for a, b in history['native_edges']:
        adjacency[a].append(b)
    paths = []
    for source in sorted(owner):
        queue = [(source, [source])]; seen = {source}
        for node, path in queue:
            for dest in adjacency[node]:
                if dest in seen:
                    continue
                seen.add(dest)
                witness = path + [dest]
                queue.append((dest, witness))
                if dest in owner and owner[source] != owner[dest]:
                    paths.append({'consumer': source, 'provider': dest, 'native_path': witness,
                                  'covered_by_plan': owner[dest] in reachable[owner[source]]})
    matches = []
    for issue in review['issues']:
        selected = set(issue['contract_ids'])
        witnesses = [p for p in paths if p['consumer'] in selected and p['provider'] in selected]
        matches.append({'issue_id': issue['id'], 'status': 'historical_dependency_evidence' if witnesses else 'not_established',
                        'witnesses': witnesses,
                        'actual_planning_failure_confirmed': False})
    return {'historical_plan_is_different': True, 'issues': matches, 'cross_theory_contract_paths': paths,
            'uncovered_paths': [p for p in paths if not p['covered_by_plan']],
            'interpretation': 'Post-review evaluation only. No historical proofs enter review. Missing evidence is not refutation. Native use is not necessity; no new-plan execution failure is established.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--history', type=Path)
    parser.add_argument('--allow-external-call', action='store_true')
    parser.add_argument('--model', default='gpt-6-astra')
    parser.add_argument('--reasoning-effort', default='medium')
    parser.add_argument('--timeout', type=int, default=600)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('Timeout must be positive')
    plan, payload = read(args.plan), read(args.input)
    contracts = payload['contracts']
    structural = analyze(plan, contracts)
    args.output.mkdir(parents=True, exist_ok=False)
    packet = review_input(plan, contracts, structural)
    write(args.output/'structural.json', structural)
    write(args.output/'review-input.json', packet)
    binding = {'plan_sha256': digest(plan), 'input_sha256': digest(payload),
               'review_input_sha256': digest(packet), 'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               'model': args.model, 'reasoning_effort': args.reasoning_effort, 'timeout': args.timeout,
               'historical_data_in_review': False, 'mutates_plan': False}
    write(args.output/'binding.json', binding)
    if not args.allow_external_call:
        write(args.output/'status.json', {'state': 'prepared', 'model_called': False})
        return
    from regen.infrastructure.llm_transcript import call_codex, validate_call_artifact
    from regen.planning.planner_io import parse_model_json
    prefix = args.output/'review'
    write(args.output/'status.json', {'state': 'running'})
    try:
        text = call_codex(INSTRUCTIONS + json.dumps(packet, ensure_ascii=False), prefix,
                          model=args.model, reasoning_effort=args.reasoning_effort,
                          timeout=args.timeout, clean_runtime=True, stage='plan-interface-review', theory='Repository')
        meta = validate_call_artifact(prefix, require_real=True)
        if not meta.get('sealable_generation'):
            raise ValueError('Unaudited review')
        review = validate_review(parse_model_json(text), plan, contracts)
        write(args.output/'review.json', review)
        # Commit predictions before opening any historical evidence.
        write(args.output/'review-commit.json', {**binding, 'review_sha256': digest(review)})
        if args.history:
            history = read(args.history)
            result = history_comparison(plan, structural, review, history)
            result['history_sha256'] = digest(history)
            result['review_sha256'] = digest(review)
            write(args.output/'history-comparison.json', result)
        write(args.output/'status.json', {'state': 'complete', 'issues': len(review['issues'])})
    except Exception as error:
        write(args.output/'status.json', {'state': 'failed', 'error': str(error)})
        raise


if __name__ == '__main__':
    main()
