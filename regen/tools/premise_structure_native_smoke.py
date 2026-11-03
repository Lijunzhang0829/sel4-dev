"""Native typed retrieval diagnostics over small authored facts; zero model calls."""
import argparse
import json
from pathlib import Path
import time
from unittest.mock import patch

from regen.execution.check_executor import CURRENT_RESOURCES, NativeResources
from regen.execution.premise_structure import rerank
from regen.preparation.checker_context import load_context
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution import premise_structure


PRELUDE = '''
lemma lab_nat: "(n::nat) + 0 = n" by simp
lemma lab_word: "(w::32 word) + 0 = w" by simp
lemma lab_forward: "(xs::nat list) = [] ==> length xs = 0" by simp
lemma lab_reverse: "length (xs::nat list) = 0 ==> xs = []" by simp
lemma lab_append: "(xs::nat list) @ [] = xs" by simp
locale lab_locale = fixes z :: nat assumes lab_zero: "z = 0"
'''


def run(dataset, output, *, hot_worker=False):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    ctx = load_context(Path(dataset))
    token = CURRENT_RESOURCES.set(NativeResources(1, 2, 1, cpus=2, memory_mb=12288))
    checker = IsabelleChecker(output, output/'isabelle', image=ctx['image'],
        architecture=ctx['architecture'], timeout=120, heaps_dir=ctx['heaps'],
        l4v_dir=ctx['l4v'], semantic_workspace=ctx['semantic_workspace'],
        base_session=ctx['base_session'], base_import=ctx['base_import'],
        base_catalog=ctx['base_catalog'], isabelle_dir=ctx['isabelle'], generated_dir=output/'generated')
    # These observations only load a heap; they never build one. Mount the
    # prepared heap read-only so repeated diagnostics need no large heap copies.
    command = checker._command
    def readonly_heap(*args, **kwargs):
        return [value + ',readonly' if value.startswith('type=bind,')
                and value.endswith('dst=/root/.isabelle/heaps') else value
                for value in command(*args, **kwargs)]
    checker._command = readonly_heap
    report = {'model_calls': 0, 'benchmark_contracts_proved': 0,
              'scope': 'synthetic native matching only, no GREEN publication',
              'hot_worker': hot_worker, 'cases': []}
    started = time.monotonic()
    def record(name, statement, names, expected, context=()):
        candidates = [{'record_id': n, 'canonical_name': n, 'exact_statement': 'synthetic fixture'} for n in names]
        if 'lab_nat' in names and name == 'digest_mismatch':
            candidates[names.index('lab_nat')]['proposition_digest'] = 'isabelle-term-yxml-sha1:' + '0'*40
        original_source = premise_structure.source
        def synthetic_source(*args):
            return original_source(*args).replace('unbundle l4v_word_context\n',
                                                  'unbundle l4v_word_context\n' + PRELUDE, 1)
        with patch.object(premise_structure, 'source', side_effect=synthetic_source):
            result = rerank(checker, {'node': {'id': name, 'statement': statement, 'context': list(context)},
                                      'observation': None}, candidates)
        statuses = {key: item['status'] for key, item in result['evidence'].items()}
        passed = all(statuses.get(key) == value for key, value in expected.items())
        if name == 'type_distinction':
            passed = passed and result['ordered_record_ids'][0] == 'lab_nat'
        if name == 'implication_direction':
            passed = passed and bool(result['evidence']['lab_forward']['required_premises'])
        report['cases'].append({'case': name, 'passed': passed, 'expected': expected, 'result': result})
        print(json.dumps({'case': name, 'passed': passed, 'statuses': statuses}), flush=True)
        (output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    try:
        if hot_worker:
            from regen.execution.batch_checker import BatchWorker
            prefix = '\n'.join(['theory Structure_Hot_Worker', 'imports',
                *[json.dumps(name) for name in checker.imports()], 'begin',
                'unbundle l4v_word_context', 'lemma worker_accepted: "True" by simp', 'end', ''])
            worker = BatchWorker(checker, prefix, ['worker_accepted'], checker.imports())
            checker._batch_worker = worker
            pending = prefix.replace('\nend\n', '\nlemma worker_pending: "True" by simp\nend\n')
            checked = worker.check_batch(pending, ['worker_pending'])
            if checked['verdict'] != 'green':
                raise RuntimeError('synthetic pending prefix failed: ' + checked['diagnostic'])
            before = (worker.base, worker.pending)
            record('prefix_isolation', 'lemma target: "True"', ['worker_accepted', 'worker_pending'],
                   {'worker_accepted':'unresolved', 'worker_pending':'unresolved'})
        record('type_distinction', 'lemma target: "(n::nat) + 0 = n"',
               ['lab_word','lab_nat','lab_missing'],
               {'lab_word':'no_direct_match','lab_nat':'conclusion_match','lab_missing':'unresolved'})
        record('implication_direction', 'lemma target: "length (xs::nat list) = 0"',
               ['lab_reverse','lab_forward'], {'lab_reverse':'no_direct_match','lab_forward':'conclusion_match'})
        record('rewrite_subterm', 'lemma target: "length ((xs::nat list) @ []) = length xs"',
               ['lab_append'], {'lab_append':'rewrite_match'})
        record('locale_context', 'lemma target: "z = 0"', ['lab_zero'],
               {'lab_zero':'conclusion_match'}, ['context lab_locale begin'])
        record('digest_mismatch', 'lemma target: "(n::nat) + 0 = n"',
               ['lab_nat'], {'lab_nat':'catalog_mismatch'})
        # Exercise the real query dispatcher, indexed candidate generator, native
        # callback and final response together. Labels never enter the catalog.
        from regen.analysis.premise_lab_strategies import build_catalog
        from regen.planning.batch_file_planner import batch_query
        rows = [{'record_id': 'lab_nat', 'canonical_name': 'lab_nat',
                 'exact_statement': '(n::nat) + 0 = n'}]
        rows += [{'record_id': f'noise_{i}', 'canonical_name': f'noise_{i}',
                  'exact_statement': f'irrelevant_constant_{i} = irrelevant_constant_{i}'}
                 for i in range(1000)]
        database = build_catalog(output/'indexed-catalog', rows)
        request_dir = output/'theory'/'batch-requests'/'000001'
        request_dir.mkdir(parents=True)
        (request_dir.parent.parent/'sealed-premises.json').write_text('{"records":[]}\n')
        node = {'id':'target', 'statement':'lemma target: "(n::nat) + 0 = n"', 'context':[]}
        original_source = premise_structure.source
        def synthetic_source(*args):
            return original_source(*args).replace('unbundle l4v_word_context\n',
                                                  'unbundle l4v_word_context\n' + PRELUDE, 1)
        with patch.object(premise_structure, 'source', side_effect=synthetic_source):
            result = batch_query({'premise_database':str(database)}, {},
                {'graph':{'nodes':[node]}}, checker, request_dir,
                {'goal_query':True, 'context_node_id':'target', 'retrieval_policy':'indexed-v1',
                 'candidate_budget':5, 'structural_rerank':True})
        pipeline = result['candidate_pipeline']
        passed = (len(result['records']) == 1
                  and result['records'][0]['record_id'] == 'lab_nat'
                  and result['records'][0]['rerank_evidence']['status'] == 'conclusion_match'
                  and pipeline['structural_rerank']['examined_candidates'] <= 5)
        report['cases'].append({'case':'indexed_query_to_native', 'passed':passed,
                                'catalog_records':len(rows), 'result':result})
        print(json.dumps({'case':'indexed_query_to_native', 'passed':passed}), flush=True)
        if hot_worker:
            preserved = (worker.base, worker.pending) == before
            observations = [json.loads(path.read_text()) for path in
                            (checker.work_dir/'premise-structure').rglob('check.json')]
            routed = all(check.get('check_scope') == 'isolated-imports-observation'
                         and check.get('observation_output_binding') for check in observations)
            # Exercise the legacy output protocol in the same live worker too.
            term = checker.resolve_symbols(['True'], [])['True']
            passed = preserved and routed and term['status'] == 'resolved'
            report['cases'].append({'case':'hot_route_and_prefix_preserved', 'passed':passed,
                'all_structural_observations_hot':routed, 'observation_count':len(observations),
                'accepted_and_pending_preserved':preserved, 'symbol_resolution':term})
            print(json.dumps({'case':'hot_route_and_prefix_preserved', 'passed':passed}), flush=True)
        report['passed'] = all(case['passed'] for case in report['cases'])
        report['wall_seconds'] = time.monotonic() - started
        (output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    finally:
        checker.close_prefix()
        CURRENT_RESOURCES.reset(token)
    if not report['passed']:
        raise RuntimeError('native structure diagnostics failed; inspect report.json')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--hot-worker', action='store_true',
                        help='Prewarm a real batch worker and verify observation isolation/output transport')
    args = parser.parse_args()
    run(args.dataset, args.output, hot_worker=args.hot_worker)
