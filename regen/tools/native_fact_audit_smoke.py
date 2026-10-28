"""Synthetic native validation of declaration-bound fact audits; no model or GREEN publication.

Uses the dataset's stripped semantic heap, never its benchmark target statements
or original proofs. All source declarations below are generic test fixtures.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import time

from regen.execution.check_executor import CURRENT_RESOURCES, NativeResources
from regen.execution.isabelle_node_checker import (IsabelleChecker,
    automation_context_audit_ml, parse_proof_dependencies)
from regen.infrastructure.resource_budget import clone_heaps
from regen.planning.sketch_check import render_check
from regen.planning.sketch_workflow import render_probe, PROBE_SECONDS
from regen.preparation.checker_context import load_context


LOCALE = 'locale Audit_Locale = fixes p :: bool assumes premise: p\n'
EMPTY_FACT = r'''local_setup \<open>fn lthy =>
  #2 (Local_Theory.note ((Binding.name "target", []), []) lthy)\<close>'''


def node(name='target', statement='lemma target: "True"', context=(), ident='target'):
    return dict(id=ident, name=name, role='contract', statement=statement,
                context=list(context), dependencies=[])


def graph(nodes):
    return dict(theory='Fact_Audit_Synthetic', file_slot='Fact_Audit_Synthetic.thy',
                revision=1, run_id='synthetic-fact-audit', nodes=nodes,
                execution_order=[n['id'] for n in nodes], accepted_order=[])


class SyntheticChecker(IsabelleChecker):
    """Add only the synthetic locale/parent declarations to production-rendered sources."""
    prelude = ''
    synthetic_parent = False

    def imports(self):
        return ['Draft.Audit_Synthetic_Base'] if self.synthetic_parent else super().imports()

    def _command(self, source, **kwargs):
        command = super()._command(source, **kwargs)
        if self.synthetic_parent:
            command[-2:-2] = ['-e', 'use_thy \"/candidate/Audit_Synthetic_Base\";']
        return command

    def _check(self, source_text, label, **kwargs):
        source_text = source_text.replace('unbundle l4v_word_context\n',
            'unbundle l4v_word_context\n' + self.prelude, 1)
        return super()._check(source_text, label, **kwargs)


def run(dataset, output, *, only="all"):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    ctx = load_context(Path(dataset))
    clone_heaps(Path(ctx['heaps']), output/'heaps')
    resources = NativeResources(1, 4, 1, cpus=4, memory_mb=16384)
    token = CURRENT_RESOURCES.set(resources)
    report = dict(pass_=False, model_calls=0, benchmark_contracts_proved=0,
                  scope='synthetic fixture checks only; no acceptance journal', selection=only, cases=[])
    started = time.monotonic()

    def checker(label, prelude=''):
        work = output/label
        result = SyntheticChecker(work, work/'isabelle', image=ctx['image'],
            architecture=ctx['architecture'], timeout=900 if label == 'seal_ordinary' else 300, heaps_dir=output/'heaps',
            l4v_dir=ctx['l4v'], semantic_workspace=ctx['semantic_workspace'],
            base_session=ctx['base_session'], base_import=ctx['base_import'],
            base_catalog=ctx['base_catalog'], isabelle_dir=ctx['isabelle'], generated_dir=work/'generated')
        result.prelude = prelude
        result.smoke_full_snapshot = label == 'seal_ordinary'
        return result

    def record(label, result, expected, diagnostic=None, marker=None):
        ok = result['verdict'] == expected
        if diagnostic:
            ok = ok and diagnostic in result['diagnostic']
        if marker:
            ok = ok and marker in Path(result['log']).read_text().splitlines()
        report['cases'].append(dict(case=label, expected=expected, passed=ok,
                                    check=result, diagnostic_required=diagnostic))
        print(json.dumps(dict(case=label, passed=ok, verdict=result['verdict']), sort_keys=True), flush=True)
        (output/'report.json').write_text(json.dumps(report, indent=2, sort_keys=True)+'\n')

    def seal_check(c, index, proofs):
        if c.smoke_full_snapshot:
            result = c.audit_complete_theory(index, proofs)
            result['smoke_scope'] = 'complete-seal-with-final-global-automation-snapshot'
        else:
            # The production complete renderer supplies declaration-bound audits.
            # Omit only the enormous unchanged global automation dump after the
            # last declaration; one separate case runs that full path below.
            label = 'Complete_Audit_Fact_Audit_Synthetic'
            source = c.complete_audit_source(index, proofs, theory_name=label)
            source = source.replace(automation_context_audit_ml(), '', 1)
            audit_dir = c.work_dir/'complete-theory-audit'
            audit_dir.mkdir(parents=True, exist_ok=True)
            result = c._check(source, label, writable_mounts=[(audit_dir, '/audit-output')])
            result['smoke_scope'] = 'production-complete-dependency-audits-without-repeated-automation-dump'
            if result['verdict'] == 'green':
                paths = sorted(audit_dir.glob('proof-dependencies-*.tsv'))
                if len(paths) != len(index['nodes']):
                    raise AssertionError('missing synthetic dependency audit')
                result['synthetic_dependency_audits'] = [parse_proof_dependencies(p,node_id=p.stem) for p in paths]
                if any(a['has_skip_proof'] for a in result['synthetic_dependency_audits']):
                    raise AssertionError('unexpected synthetic admission')
        result['smoke_timeout_seconds'] = c.timeout
        return result

    try:
        locale = ['context Audit_Locale begin']
        fixtures = [
            ('ordinary', node(), 'by simp', '', 'green', None),
            ('locale', node(statement='lemma target: p', context=locale),
             'by (rule premise)', LOCALE, 'green', None),
            ('locale_with_old_short_name', node(statement='lemma target: p', context=locale),
             'by (rule premise)', 'lemma target: \"True\" by simp\n' + LOCALE, 'green', None),
            ('locale_qualified', node(name='Audit_Locale.target', statement='lemma target: p', context=locale),
             'by (rule premise)', LOCALE, 'green', None),
            ('bundle', node(statement='lemma target: "True" "(0::nat) = 0"'),
             'by simp_all', '', 'green', None),
            ('locale_bundle', node(statement='lemma target: p "p | False"', context=locale),
             'using premise by auto', LOCALE, 'green', None),
            ('missing', node(name='absent'), 'by simp', '', 'red', 'Missing newly declared audit fact'),
            ('stale_parent', node(name='stale'), 'by simp', 'lemma stale: "True" by simp\n',
             'red', 'Missing newly declared audit fact'),
            ('wrong_namespace', node(name='Other_Locale.target', context=locale),
             'by simp', LOCALE, 'red', 'Missing newly declared audit fact'),
            ('empty_bundle', node(statement=EMPTY_FACT), '', '',
             'red', 'Audit target has no theorems'),
        ]
        for label, entry, proof, prelude, expected, diagnostic in fixtures:
            if only in {'sketch','probe'} or label == 'ordinary':
                continue  # Run the large complete automation dump once, last.
            c = checker('seal_'+label, prelude)
            try:
                record('seal_'+label, seal_check(c, graph([entry]), {'target':proof}),
                       expected, diagnostic)
            finally:
                c.close_prefix()

        # Bundle selection is a legal proof dependency, never an audit target
        # selector: the source and target audits must both inspect full bundles.
        entries = [node(name='source', ident='source',
                        statement='lemma source: "True" "(0::nat) = 0"'),
                   node(statement='lemma target: "(0::nat) = 0"')]
        if only in {'all','seal'}:
            c = checker('seal_bundle_selection')
            record('seal_bundle_selection', seal_check(c, graph(entries),
                {'source':'by simp_all','target':'using source(2) by assumption'}), 'green')
            c.close_prefix()

        for label, entry, proof, prelude, expected, diagnostic in fixtures:
            if only in {'seal','probe'}:
                continue
            # Public sketch statements are lemma-only. This one trusted
            # malformed-result fixture replaces a rendered dummy lemma with a
            # valid empty binding, solely to exercise the native fail-closed
            # check; it cannot be submitted through the model-facing parser.
            rendered_entry = node() if label == 'empty_bundle' else entry
            rendered_proof = 'by simp' if label == 'empty_bundle' else proof
            helper = node(name='helper', ident='helper', context=entry['context'],
                          statement='lemma helper: "True"')
            helper['role'] = 'helper'
            state = dict(graph=graph([helper,entry]), proofs={}, accepted_order=[])
            signature = 'synthetic_'+label
            c = checker('sketch_'+label)
            # Locale assumptions legitimately add axioms to their defining
            # parent. Import that trusted synthetic fixture before the worker
            # freezes its baseline, exactly as real semantic locales are
            # imported; never weaken the worker's new-axiom audit.
            (c.work_dir/'Audit_Synthetic_Base.thy').write_text(
                'theory Audit_Synthetic_Base imports \"'+ctx['base_import']+'\" begin\n'
                + prelude + '\nend\n')
            c.synthetic_parent = True
            source, prefix, admitted = render_check(state, {'helper':helper, 'target':rendered_entry},
                {'target':rendered_proof}, {'target_entry_id':'target','helper_ids':['helper']},
                c.imports(), signature)
            if label == 'empty_bundle':
                source = source.replace(rendered_entry['statement']+'\n'+rendered_proof, EMPTY_FACT, 1)
            # Run the actual conditional BatchWorker transaction. Cold use_thy
            # rejects temporary sorry admissions and is not the sketch protocol.
            c.batch_incremental = True
            c.batch_candidate_timeout = 300
            result = c.check_sketch_source(source, prefix, admitted, timeout=300)
            record('sketch_'+label, result, expected, diagnostic,
                   'REGEN_SKETCH_CLOSED '+signature if expected == 'green' else None)
            c.close_prefix()

        if only in {'all','probe'}:
            for label, entry, proof, prelude, expected, diagnostic in fixtures:
                c = checker('probe_'+label)
                (c.work_dir/'Audit_Synthetic_Base.thy').write_text(
                    'theory Audit_Synthetic_Base imports "'+ctx['base_import']+'" begin\n'
                    + prelude + '\nend\n')
                c.synthetic_parent = True
                prefix = 'theory Probe_Smoke\nimports "Draft.Audit_Synthetic_Base"\nbegin\n\nend\n'
                marker = 'REGEN_HELPER_PROBE_CLEAN synthetic_'+label
                rendered_entry = node() if label == 'empty_bundle' else entry
                rendered_proof = 'by simp' if label == 'empty_bundle' else proof
                source = render_probe(prefix, rendered_entry, rendered_proof, marker)
                if label == 'empty_bundle':
                    source = source.replace(rendered_entry['statement']+'\n'+rendered_proof, EMPTY_FACT, 1)
                c.batch_incremental = True
                c.batch_candidate_timeout = 300
                try:
                    result = c.check_sketch_source(source, prefix, [], timeout=PROBE_SECONDS)
                    record('probe_'+label, result, expected, diagnostic,
                           marker if expected == 'green' else None)
                finally:
                    c.close_prefix()

        # Empty names fail before native work in both paths; no fallback to the
        # first/old theorem in a theory can make the audit succeed.
        for mode in ('seal','sketch','probe'):
            entry = node(name='')
            try:
                if mode == 'seal':
                    checker('empty_name').complete_audit_source(graph([entry]), {'target':'by simp'})
                elif mode == 'sketch':
                    render_check(dict(graph=graph([entry]),proofs={},accepted_order=[]), {'target':entry},
                        {'target':'by simp'}, {'target_entry_id':'target','helper_ids':[]},
                        [ctx['base_import']], 'empty')
                else:
                    render_probe('end\n', entry, 'by simp', 'empty')
            except ValueError as error:
                passed = 'theorem name' in str(error)
            else:
                passed = False
            report['cases'].append(dict(case=mode+'_empty_name', passed=passed, expected='ValueError'))
        if only in {'all','seal'}:
            c = checker('seal_ordinary')
            record('seal_ordinary', seal_check(c, graph([node()]), {'target':'by simp'}), 'green')
            c.close_prefix()
        report['pass_'] = all(c['passed'] for c in report['cases'])
    finally:
        resources.close()
        CURRENT_RESOURCES.reset(token)
        report['pass'] = report.pop('pass_')
        report.update(wall_seconds=time.monotonic()-started, peak_native_active=resources.peak_active,
                      native_operations=deepcopy(resources.metrics))
        (output/'report.json').write_text(json.dumps(report, indent=2, sort_keys=True)+'\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--only', choices=('all','seal','sketch','probe'), default='all')
    args = parser.parse_args()
    result = run(args.dataset, args.output, only=args.only)
    print(json.dumps({'pass':result['pass'], 'cases':len(result['cases']),
                      'wall_seconds':result['wall_seconds']}, sort_keys=True))
    raise SystemExit(0 if result['pass'] else 1)
