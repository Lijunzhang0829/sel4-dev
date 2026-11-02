"""Offline, synthetic retrieval diagnostics; never produces trusted proof facts.

Run with python3 -m regen.tools.premise_retrieval_lab --help.  Gold labels are
available to the evaluator only, not the retrieval strategies.  No LLM,
Isabelle, network, production database migration or proof generation is run.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sqlite3
import statistics
import time

from regen.analysis.premise_lab_fixtures import build_fixture
from regen.analysis.premise_lab_strategies import (
    VARIANTS, build_catalog, prepare_variant, retrieve,
)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def validate_fixture(fixture):
    """Reject inconsistent judgments rather than silently inflating recall."""
    if fixture.get('kind') != 'synthetic':
        raise ValueError('this lab accepts explicitly synthetic fixtures only')
    records, queries = fixture['records'], fixture['queries']
    ids = [r['record_id'] for r in records]
    if len(set(ids)) != len(ids) or not records:
        raise ValueError('fixture record IDs must be unique and nonempty')
    query_ids = [q['id'] for q in queries]
    if len(set(query_ids)) != len(query_ids) or not queries:
        raise ValueError('fixture query IDs must be unique and nonempty')
    by_id = {r['record_id']: r for r in records}
    for query in queries:
        if query['mode'] not in {'exact', 'definition', 'search', 'goal'}:
            raise ValueError('unsupported fixture query mode')
        relevant = set(query.get('relevant_ids', []))
        forbidden = set(query.get('forbidden_ids', []))
        if not relevant.union(forbidden) <= set(ids):
            raise ValueError('judgment references an unknown record')
        if relevant & forbidden or query.get('expected_empty') and relevant:
            raise ValueError('contradictory fixture judgment')
        relevant_groups = {by_id[r]['proposition_digest'] for r in relevant}
        forbidden_groups = {by_id[r]['proposition_digest'] for r in forbidden}
        if relevant_groups & forbidden_groups:
            raise ValueError('same proposition group is both relevant and forbidden')
        if query.get('judgment') != 'synthetic_authored':
            raise ValueError('queries must disclose authored synthetic judgments')


def score_query(records, query, returned, top_k):
    """Group-based relevance is an authored diagnostic, not proof applicability."""
    by_id = {r['record_id']: r for r in records}
    relevant_groups = {by_id[r]['proposition_digest'] for r in query.get('relevant_ids', [])}
    page = returned[:top_k]
    groups = [by_id[r['record_id']]['proposition_digest'] for r in page]
    hits = set(groups) & relevant_groups
    first = next((i + 1 for i, group in enumerate(groups) if group in relevant_groups), None)
    forbidden_groups = {by_id[r]['proposition_digest'] for r in query.get('forbidden_ids', [])}
    return {
        'returned_count': len(page),
        'relevant_group_count': len(relevant_groups),
        'relevant_groups_found': len(hits),
        'group_recall_at_k': len(hits) / len(relevant_groups) if relevant_groups else None,
        'reciprocal_rank_at_k': (1 / first if first else 0.0) if relevant_groups else None,
        'duplicate_proposition_slots': len(groups) - len(set(groups)),
        'unique_proposition_fraction': len(set(groups)) / len(groups) if groups else None,
        'forbidden_group_hits': len(set(groups) & forbidden_groups),
        'expected_empty_violation': bool(query.get('expected_empty') and page),
        'statements_intact': all(r['exact_statement'] == by_id[r['record_id']]['exact_statement']
                                 for r in page),
    }


def evaluate_query(database, records, query, variant, prepared, top_k, candidate_budget):
    # Deliberately pass no gold labels, rationale, categories or expectations.
    request = {'query': query['query'], 'mode': query['mode']}
    started = time.perf_counter()
    first = retrieve(database, request, variant=variant, prepared=prepared,
                     limit=top_k, candidate_budget=candidate_budget)
    first_seconds = time.perf_counter() - started
    ranked = list(first['records'])
    pages = [{'offset': 0, 'record_ids': [r['record_id'] for r in ranked]}]
    offset = first.get('next_offset')
    previous = 0
    while offset is not None:
        if offset <= previous or len(pages) > candidate_budget:
            raise AssertionError('pagination failed to make progress')
        page = retrieve(database, request, variant=variant, prepared=prepared,
                        limit=top_k, offset=offset, candidate_budget=candidate_budget)
        pages.append({'offset': offset, 'record_ids': [r['record_id'] for r in page['records']]})
        ranked.extend(page['records'])
        previous, offset = offset, page.get('next_offset')
    traversal_seconds = time.perf_counter() - started
    if len({r['record_id'] for r in ranked}) != len(ranked):
        raise AssertionError('same record repeated across pages')
    # This tests replay from a serializable index descriptor, not controller recovery.
    replay = retrieve(database, request, variant=variant,
                      prepared=json.loads(json.dumps(prepared)),
                      limit=top_k, candidate_budget=candidate_budget)
    replay_stable = first['records'] == replay['records']
    if not replay_stable:
        raise AssertionError('identical query changed on an unchanged snapshot')
    return {
        'query_id': query['id'], 'category': query['category'], 'mode': query['mode'],
        'variant': variant, 'request': request, 'judgment': query['judgment'],
        'rationale': query.get('rationale', ''),
        'relevant_ids': query.get('relevant_ids', []),
        'metrics': score_query(records, query, first['records'], top_k),
        'within_budget_metrics': score_query(records, query, ranked, len(ranked)),
        'first_page_wrapper_seconds': first_seconds,
        'all_pages_wrapper_seconds': traversal_seconds,
        'serializable_descriptor_replay_stable': replay_stable,
        'pages': pages, 'first_response': first,
        'ranked_record_ids_within_budget': [r['record_id'] for r in ranked],
    }


def aggregate(rows):
    def mean(key):
        values = [r['metrics'][key] for r in rows if r['metrics'][key] is not None]
        return statistics.mean(values) if values else None
    return {
        'queries': len(rows),
        'positive_queries': sum(r['metrics']['relevant_group_count'] > 0 for r in rows),
        'mean_group_recall_at_k': mean('group_recall_at_k'),
        'mean_reciprocal_rank_at_k': mean('reciprocal_rank_at_k'),
        'duplicate_proposition_slots': sum(r['metrics']['duplicate_proposition_slots'] for r in rows),
        'forbidden_group_hits': sum(r['metrics']['forbidden_group_hits'] for r in rows),
        'expected_empty_violations': sum(r['metrics']['expected_empty_violation'] for r in rows),
        'all_statements_intact': all(r['metrics']['statements_intact'] for r in rows),
        'median_first_page_wrapper_seconds': statistics.median(
            r['first_page_wrapper_seconds'] for r in rows) if rows else None,
    }


def implementation_hashes():
    root = Path(__file__).resolve().parents[2]
    paths = [Path(__file__).resolve(),
             root / 'regen/analysis/premise_lab_fixtures.py',
             root / 'regen/analysis/premise_lab_strategies.py',
             root / 'regen/execution/premise_recall.py',
             root / 'regen/execution/proof_state_recall.py',
             root / 'regen/preparation/premise_database.py']
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def render_report(summary):
    lines = [
        '# Premise retrieval laboratory', '',
        'Synthetic diagnostic judgments only. No model or Isabelle was run. '
        'These scores do not measure mathematical applicability or proof success.', '',
        f"First page: {summary['parameters']['top_k']} records. "
        f"Candidate budget before grouping: {summary['parameters']['candidate_budget']}.", '',
        '| Noise | Seed | Variant | Mode | Queries | Group Recall@k | MRR@k | Duplicate slots | Forbidden groups |',
        '|---:|---:|---|---|---:|---:|---:|---:|---:|',
    ]
    def number(value):
        return '—' if value is None else f'{value:.3f}'
    for scenario in summary['scenarios']:
        for variant, groups in scenario['by_variant'].items():
            for mode, values in groups['by_mode'].items():
                lines.append(f"| {scenario['noise_records']} | {scenario['seed']} | {variant} | {mode} | "
                             f"{values['queries']} | {number(values['mean_group_recall_at_k'])} | "
                             f"{number(values['mean_reciprocal_rank_at_k'])} | "
                             f"{values['duplicate_proposition_slots']} | {values['forbidden_group_hits']} |")
    lines.extend(['', '## Per-case changes relative to baseline', '',
                  'Positive values mean higher authored group recall. Failures and unchanged cases '
                  'remain in the detailed results; there is no automatic promotion to production.', ''])
    for scenario in summary['scenarios']:
        for change in scenario['changes']:
            if change['recall_delta'] or change['forbidden_delta']:
                lines.append(f"- noise={scenario['noise_records']}, seed={scenario['seed']}, "
                             f"{change['variant']}, {change['query_id']}: "
                             f"recall delta {change['recall_delta']:+.3f}, "
                             f"forbidden-group delta {change['forbidden_delta']:+d}.")
    lines.extend(['', '## Interpretation limits', ''])
    lines.extend('- ' + item for item in summary['limitations'])
    return '\n'.join(lines) + '\n'


def run(output, *, noise_records=(0, 1000, 10000), seeds=(0,), variants=VARIANTS,
        top_k=5, candidate_budget=50, fixture_path=None):
    if not 1 <= top_k <= 5 or not top_k <= candidate_budget <= 50:
        raise ValueError('require 1 <= top_k <= 5 and top_k <= candidate_budget <= 50')
    if not variants or not set(variants) <= set(VARIANTS) or len(set(variants)) != len(variants):
        raise ValueError('variants must be nonempty, distinct known strategy names')
    if not noise_records or any(type(n) is not int or n < 0 for n in noise_records):
        raise ValueError('noise sizes must be nonnegative integers')
    if len(set(noise_records)) != len(noise_records) or len(set(seeds)) != len(seeds) or not seeds:
        raise ValueError('sizes and seeds must be distinct and nonempty')
    if fixture_path and (tuple(noise_records) != (0,) or tuple(seeds) != (0,)):
        raise ValueError('custom fixture requires --noise-records 0 --seeds 0')
    custom = json.loads(Path(fixture_path).read_text()) if fixture_path else None
    if custom is not None:
        validate_fixture(custom)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    summary = {
        'schema': 'premise-retrieval-lab-results-v1', 'kind': 'synthetic',
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'parameters': {'noise_records': list(noise_records), 'seeds': list(seeds),
                       'variants': list(variants), 'top_k': top_k, 'candidate_budget': candidate_budget,
                       'fixture_path': str(fixture_path) if fixture_path else None},
        'runtime': {'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version},
        'implementation_sha256': implementation_hashes(),
        'limitations': [
            'All relevance judgments are synthetic_authored, not Isabelle applicability certificates.',
            'Handcrafted cases diagnose mechanisms; the combined strategy is not independently held-out.',
            'Recall/MRR exclude queries without positive labels; negative controls are reported separately.',
            'The same pre-grouping candidate budget applies to all variants; pagination is bounded by it.',
            'Pagination and descriptor replay are lab features, not new production harness capabilities.',
            'Timing includes strategy wrappers and repeated database verification; it is not a speedup claim.',
            'New tokenization changes BM25 document lengths and frequencies as well as component matching.',
            'There is no learned model, proof execution, native typed matching, or generated-import lifecycle test.',
            'Existing production databases and retrieval behavior are unchanged.',
        ],
        'scenarios': [],
    }
    write_json(output / 'parameters.json', {k: v for k, v in summary.items() if k != 'scenarios'})
    for noise in noise_records:
        for seed in seeds:
            directory = output / f'noise-{noise}-seed-{seed}'
            directory.mkdir()
            fixture = custom if custom is not None else build_fixture(noise_records=noise, seed=seed)
            validate_fixture(fixture)
            write_json(directory / 'fixture.json', fixture)
            start = time.perf_counter()
            database = build_catalog(directory / 'catalog', fixture['records'])
            build_seconds = time.perf_counter() - start
            scenario = {'noise_records': noise, 'seed': seed, 'records': len(fixture['records']),
                        'queries': len(fixture['queries']), 'directory': directory.name,
                        'fixture_sha256': hashlib.sha256((directory / 'fixture.json').read_bytes()).hexdigest(),
                        'catalog_build_seconds': build_seconds, 'by_variant': {}, 'changes': []}
            all_rows = []
            for variant in variants:
                start = time.perf_counter()
                prepared = prepare_variant(database, variant, directory / variant)
                prepare_seconds = time.perf_counter() - start
                rows = [evaluate_query(database, fixture['records'], q, variant, prepared,
                                       top_k, candidate_budget) for q in fixture['queries']]
                all_rows.extend(rows)
                scenario['by_variant'][variant] = {
                    'prepare_seconds': prepare_seconds, 'overall': aggregate(rows),
                    'by_mode': {mode: aggregate([r for r in rows if r['mode'] == mode])
                                for mode in sorted({r['mode'] for r in rows})},
                    'by_category': {category: aggregate([r for r in rows if r['category'] == category])
                                    for category in sorted({r['category'] for r in rows})},
                }
                print(f"noise={noise} seed={seed} variant={variant} queries={len(rows)} complete", flush=True)
            baseline = {r['query_id']: r for r in all_rows if r['variant'] == 'baseline'}
            for row in all_rows:
                base = baseline.get(row['query_id'])
                if base is not None and row['variant'] != 'baseline':
                    recall, old = row['metrics']['group_recall_at_k'], base['metrics']['group_recall_at_k']
                    scenario['changes'].append({
                        'query_id': row['query_id'], 'variant': row['variant'],
                        'recall_delta': recall - old if recall is not None and old is not None else 0.0,
                        'forbidden_delta': row['metrics']['forbidden_group_hits'] - base['metrics']['forbidden_group_hits'],
                    })
            with (directory / 'queries.jsonl').open('w') as stream:
                for row in all_rows:
                    stream.write(json.dumps(row, ensure_ascii=False) + '\n')
            write_json(directory / 'summary.json', scenario)
            summary['scenarios'].append(scenario)
            write_json(output / 'summary.json', summary)
    (output / 'REPORT.md').write_text(render_report(summary))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='new output directory; never overwritten')
    parser.add_argument('--noise-records', nargs='+', type=int, default=[0, 1000, 10000])
    parser.add_argument('--seeds', nargs='+', type=int, default=[0])
    parser.add_argument('--variants', nargs='+', choices=VARIANTS, default=list(VARIANTS))
    parser.add_argument('--top-k', type=int, default=5)
    parser.add_argument('--candidate-budget', type=int, default=50)
    parser.add_argument('--fixture', type=Path, help='custom synthetic fixture; use --noise-records 0 --seeds 0')
    args = parser.parse_args()
    try:
        summary = run(args.output, noise_records=args.noise_records, seeds=args.seeds,
                      variants=args.variants, top_k=args.top_k,
                      candidate_budget=args.candidate_budget, fixture_path=args.fixture)
    except (ValueError, FileExistsError) as error:
        parser.error(str(error))
    print(json.dumps({'output': str(args.output.resolve()), 'scenarios': len(summary['scenarios'])}))


if __name__ == '__main__':
    main()
