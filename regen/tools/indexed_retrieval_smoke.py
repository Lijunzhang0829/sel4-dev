"""Synthetic candidate-generation ablations using production retrieval functions.

Compares legacy vector expansion, bounded legacy-FTS reranking, and bounded
normalized-FTS reranking. Labels never enter retrieval; no native proof is run.
"""
import argparse
import json
from pathlib import Path
from unittest.mock import patch

from regen.analysis.premise_lab_fixtures import build_fixture
from regen.analysis.premise_lab_strategies import build_catalog
from regen.execution.indexed_retrieval import build_upstream_index
from regen.execution import proof_state_recall
from regen.execution.proof_state_recall import build_vectors, recall_for_proof_state
from regen.preparation.premise_database import digest


def run(output, noise_sizes=(0, 1000, 10000), budgets=(5, 20, 100)):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    results = []
    for noise in noise_sizes:
        fixture = build_fixture(noise_records=noise)
        directory = output / f'noise-{noise}'
        directory.mkdir()
        (directory / 'fixture.json').write_text(json.dumps(fixture, ensure_ascii=False, indent=2) + '\n')
        database = build_catalog(directory / 'catalog', fixture['records'])
        vectors = directory / 'vectors.sqlite'
        build_vectors(database, vectors)
        lexical = build_upstream_index(database, directory / 'normalized')
        for query in [q for q in fixture['queries'] if q['mode'] == 'goal']:
            for policy, index in [('legacy-v1', None), ('indexed-v1', None), ('indexed-v1', lexical)]:
                for budget in ([100] if policy == 'legacy-v1' else budgets):
                    unpack = proof_state_recall.struct.iter_unpack
                    with patch('regen.execution.proof_state_recall.struct.iter_unpack', wraps=unpack) as observe:
                        response = recall_for_proof_state(database, query['query'], vector_index=vectors,
                            retrieval_policy=policy, candidate_budget=budget, lexical_index=index)
                    returned = {r['record_id'] for r in response['records']}
                    results.append({'noise_records': noise, 'record_count': len(fixture['records']),
                        'query_id': query['id'], 'query': query['query'], 'relevant_ids': query['relevant_ids'],
                        'policy': policy, 'normalized_index': index is not None, 'candidate_budget': budget,
                        'vector_records_scored': observe.call_count,
                        'positive_hit': bool(returned.intersection(query['relevant_ids'])),
                        'response': response})
    summary = {'schema': 'indexed-retrieval-smoke-v1', 'synthetic': True,
        'noise_sizes': list(noise_sizes), 'budgets': list(budgets),
        'source_sha256': {str(p): digest(p) for p in [Path(__file__),
            Path(proof_state_recall.__file__), Path('regen/execution/indexed_retrieval.py')]},
        'limitations': ['Two authored goal diagnostics, not a benchmark of theorem applicability.',
            'Vector decoding calls are counted by an observational wrapper; no speed comparison is claimed.',
            'Candidate budgets are experimental settings, not trained or optimal thresholds.',
            'No LLM, Isabelle, native type matcher, or proof success measurement.'],
        'results': results}
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--noise-sizes', nargs='+', type=int, default=[0, 1000, 10000])
    parser.add_argument('--budgets', nargs='+', type=int, default=[5, 20, 100])
    args = parser.parse_args()
    result = run(args.output, args.noise_sizes, args.budgets)
    print(json.dumps({'output': str(args.output), 'queries': len(result['results']),
                      'positive_hits': sum(r['positive_hit'] for r in result['results'])}))
