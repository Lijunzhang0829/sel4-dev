"""Small, frozen-prefix timing study. No model calls, proof edits or seals."""
import argparse
from copy import deepcopy
from pathlib import Path
import shutil
import time
from unittest.mock import patch

from regen.execution import isabelle_node_checker as native
from regen.execution.check_executor import NativeResources, CURRENT_RESOURCES
from regen.execution.proof_commits import durable
from regen.execution.node_phase_timing import parse_arch_states
from regen.tools.harness_acceptance_baseline import protected_snapshot, production_hashes
from regen.tools.native_backend_acceptance import Limits
from regen.tools.replay_finish_step import (
    load_inputs, validate_frozen_context, validate_destination, readonly_command, sha,
)


def prefix_inputs(graph, proofs, count=3):
    order = graph.get('accepted_order') or graph['execution_order']
    selected = order[:count]
    if len(selected) != count or len(set(selected)) != count:
        raise ValueError('saved prefix is incomplete')
    result = deepcopy(graph)
    result['nodes'] = [n for n in result['nodes'] if n['id'] in selected]
    result['execution_order'] = selected
    result['accepted_order'] = selected
    if {n['id'] for n in result['nodes']} != set(selected):
        raise ValueError('saved prefix node mismatch')
    return result, {key: proofs[key] for key in selected}


def run(source, output, *, controls=False, arch_context=False, arch_profile=False, reuse_arch=False,
        reuse_audit=False, reuse_controls=False):
    if reuse_controls and not reuse_audit:
        raise ValueError('reuse controls require semantic audit')
    if reuse_audit and not reuse_arch:
        raise ValueError('reuse audit requires reuse comparison')
    if reuse_arch and (controls or arch_context or arch_profile):
        raise ValueError('reuse comparison requires its own study')
    if controls and (arch_context or arch_profile):
        raise ValueError('generic controls and saved Arch profiling are separate studies')
    source, output = validate_destination(source, output)
    inputs = load_inputs(source)
    context, cache = validate_frozen_context(source, inputs)
    config, directory, graph, proofs, ancestors, _, complete, doc = inputs
    graph, proofs = prefix_inputs(graph, proofs)
    modes = ('positive-baseline', 'positive', 'negative', 'async-rejected') if controls else ('baseline', 'phases', 'phases', 'baseline')
    if reuse_arch:
        modes = ('baseline', 'reuse', 'reuse', 'baseline')
        if reuse_audit:
            modes = ('baseline', 'reuse')
    if arch_context or arch_profile:
        if any(n.get('context') != ['context begin interpretation Arch .'] for n in graph['nodes']):
            raise ValueError('saved prefix does not have the exact supported Arch context')
        modes = ('phases', 'arch', 'arch', 'phases')
        if arch_profile:
            modes = ('arch-profile',)
    if controls:
        graph = {'nodes': [{'id': 'control', 'name': 'control', 'context': ['context begin'],
                           'statement': 'lemma control: "True"'}], 'execution_order': ['control'],
                 'accepted_order': ['control']}
        proofs = {'control': 'proof -\n  show True by simp\nqed'}
    if reuse_controls:
        from regen.tools.arch_reuse_controls import fixture
        graph, proofs = fixture()
    output.mkdir(parents=True)
    shutil.copytree(directory/'generated', output/'generated')
    (output/'build-heaps').mkdir()
    before = protected_snapshot(source)
    repo = Path(__file__).resolve().parents[2]
    implementation = production_hashes(repo)
    frozen = [source/'run.json', directory/'batch-requests/000121/input.json',
              directory/'batch-requests/000121/proposal.json', complete, doc, cache,
              directory/'provider-session.json']
    frozen += [p for base in ('heaps', 'generated')
               for p in (directory/base).rglob('*') if p.is_file()]
    hashes = {str(p): sha(p) for p in frozen}
    for name in ('regen/tools/profile_saved_node_phases.py',
                 'regen/tools/arch_locale_profile.py',
                 'regen/tools/arch_reuse_audit.py',
                 'regen/tools/arch_reuse_controls.py',
                 'regen/execution/node_phase_timing.py', 'regen/execution/isabelle_node_checker.py'):
        destination = output/'implementation'/name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo/name, destination)
    durable(output/'manifest.json', {
        'source_run': str(source), 'model_calls': 0, 'experiment_resumed': False,
        'seal_published': False, 'protected_before': before, 'production_sha256': implementation,
        'frozen_input_sha256': hashes, 'selected_nodes': graph['accepted_order'],
        'order': modes, 'generic_controls_only': controls or reuse_controls, 'arch_context': arch_context,
        'arch_profile': arch_profile,
        'reuse_arch': reuse_arch,
        'reuse_audit': reuse_audit,
        'reuse_controls': reuse_controls,
        'native_timeout_seconds': 300, 'native_threads': 4,
        'parallel_proofs': 0,
        'measurement_notice': 'Both baseline and phases disable proof futures for '
                              'disjoint timing. Production settings remain unchanged.',
        'scope': ('Generic controls, not generated target proofs' if controls or reuse_controls else
                  'Timing diagnostics on exact saved first-three-node prefix, not theory acceptance'),
    }, immutable=True)
    durable(output/'saved-prefix.json', {'graph': graph, 'proofs': proofs}, immutable=True)
    checker = native.IsabelleChecker(output, output/'isabelle', timeout=300,
        architecture=context['architecture'], previous_theories=ancestors,
        import_all_previous=True, generated_dir=output/'generated', heaps_dir=directory/'heaps',
        image=config['image'], l4v_dir=Path(config['l4v']), isabelle_dir=Path(config['isabelle_dir']),
        semantic_workspace=Path(config['semantic_workspace']), base_session=config['base_session'],
        base_import=config['base_import'], base_catalog=config['base_catalog'])
    limits = Limits(output, 1320, 2*1024**3)
    resources = NativeResources(slots=1, threads=4, resident_limit=1)
    token = CURRENT_RESOURCES.set(resources)
    original = native.native_run
    result = {'trials': [], 'pass': False, 'model_calls': 0, 'experiment_resumed': False}
    def invoke(command, *, timeout, **kwargs):
        if timeout != 300:
            raise ValueError('unexpected native budget')
        args = readonly_command(command, output)
        at = args.index('process') + 1
        parallel_proofs = 1 if modes[len(result['trials'])] == 'async-rejected' else 0
        args[at:at] = ['-o', 'parallel_proofs='+str(parallel_proofs)]
        if arch_profile:
            from regen.tools.arch_locale_profile import profiling_command
            args = profiling_command(args)
        limits.register(args[args.index('--name')+1])
        durable(output/f'command-{len(result["trials"])+1}.json', args, immutable=True)
        return original(args, timeout=timeout, **kwargs)
    try:
        limits.check(); limits.monitor.start()
        with patch.object(native, 'native_run', side_effect=invoke):
            for index, mode in enumerate(modes, 1):
                label = 'Timing_Scheduler_Prefix'
                selected_graph, selected_proofs = deepcopy(graph), dict(proofs)
                if mode == 'negative':
                    selected_graph['nodes'][0]['statement'] = 'lemma control: "False"'
                    selected_proofs = {'control': 'by simp'}
                source_text = checker._source(selected_graph, selected_proofs, theory_name=label,
                    diagnostic_markers=True, phase_timing=mode not in ('baseline', 'positive-baseline', 'reuse'),
                    arch_timing=mode in ('arch', 'arch-profile'), reuse_arch_context=mode == 'reuse')
                if reuse_audit:
                    from regen.tools.arch_reuse_audit import instrument
                    source_text = instrument(source_text, selected_graph)
                if mode == 'arch-profile':
                    from regen.tools.arch_locale_profile import METADATA
                    source_text = native.append_before_theory_end(source_text, METADATA)
                started = time.time()
                check = checker._check(source_text, label)
                trial = {'mode': mode, 'index': index, 'started_at_unix': started,
                         'finished_at_unix': time.time(), 'check': check}
                if reuse_audit and check['verdict'] == 'green':
                    from regen.tools.arch_reuse_audit import parse, expected_keys
                    trial['context_fingerprints'] = parse(Path(check['log']).read_text())
                    if set(trial['context_fingerprints']) != expected_keys(selected_graph):
                        raise RuntimeError('incomplete semantic fingerprint coverage')
                if mode in ('arch', 'arch-profile'):
                    trial['arch_states'] = parse_arch_states(Path(check['log']).read_text())
                for kind in ('thy', 'log'):
                    shutil.copyfile(checker.work_dir/(label+'.'+kind), output/f'{index}-{mode}.{kind}')
                # Receipts must point at immutable per-trial files, not the next
                # trial's overwritten working source/log.
                check['source'] = str(output/f'{index}-{mode}.thy')
                check['log'] = str(output/f'{index}-{mode}.log')
                check['source_sha256'] = sha(Path(check['source']))
                check['log_sha256'] = sha(Path(check['log']))
                durable(output/f'{index}-{mode}.json', trial, immutable=True)
                result['trials'].append(trial)
                if mode in ('negative', 'async-rejected'):
                    expected = ('Failed to' if mode == 'negative'
                                else 'phase timing requires parallel_proofs=0')
                    if (check['returncode'] in (0, 124, 125, 126, 127)
                            or expected not in Path(check['log']).read_text()):
                        raise RuntimeError('control did not reject for the expected reason')
                    if any(r['complete'] for r in check.get('node_phase_timing', {}).get('nodes', {}).values()):
                        raise RuntimeError('failed control has complete phase coverage')
                    continue
                if check['verdict'] != 'green':
                    raise RuntimeError('native timing trial rejected: '+str(index))
                if set(check['node_wall_ms']) != set(proofs):
                    raise RuntimeError('missing legacy timing coverage')
                if mode in ('phases', 'positive', 'arch', 'arch-profile'):
                    rows = check.get('node_phase_timing', {}).get('nodes', {})
                    if set(rows) != set(proofs) or not all(r['complete'] for r in rows.values()):
                        raise RuntimeError('incomplete phase timing coverage')
                    if mode in ('arch', 'arch-profile') and set(trial['arch_states']) != set(proofs):
                        raise RuntimeError('incomplete Arch state observation coverage')
                if mode == 'arch-profile':
                    from regen.tools.arch_locale_profile import parse_profile
                    profile = parse_profile(Path(check['log']).read_text())
                    durable(output/'runtime-profile.json', profile, immutable=True)
        if reuse_audit:
            left, right = [t['context_fingerprints'] for t in result['trials']]
            result['context_differences'] = {key: {'baseline': left.get(key), 'reuse': right.get(key)}
                for key in sorted(set(left) | set(right)) if left.get(key) != right.get(key)}
            if result['context_differences']:
                raise RuntimeError('Arch reuse changes observable context fingerprints')
        result['pass'] = True
    except Exception as error:
        result['failure'] = {'type': type(error).__name__, 'message': str(error)}
    finally:
        CURRENT_RESOURCES.reset(token)
        resources.close()
        limits.stop.set()
        if limits.monitor.ident is not None:
            limits.monitor.join(timeout=20)
        result['cleanup'] = limits.cleanup()
        result['guard_failure'] = limits.failure
        result['protected_unchanged'] = before == protected_snapshot(source)
        result['frozen_inputs_unchanged'] = all(sha(Path(p)) == value for p, value in hashes.items())
        result['production_unchanged'] = implementation == production_hashes(repo)
        result['pass'] = (result['pass'] and result['guard_failure'] is None
            and result['protected_unchanged'] and result['frozen_inputs_unchanged']
            and result['production_unchanged'] and all(r['confirmed_absent'] for r in result['cleanup']))
        durable(output/'report.json', result, immutable=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--controls-only', action='store_true',
                        help='Generic structured-proof, false-proof and async-mode rejection controls')
    parser.add_argument('--arch-context', action='store_true',
                        help='Split the exact saved Arch context into scope/interpretation phases')
    parser.add_argument('--arch-profile', action='store_true',
                        help='One native CPU profile plus post-measurement locale metadata')
    parser.add_argument('--reuse-arch', action='store_true', help='Experimental shared Arch scope ABBA comparison')
    parser.add_argument('--reuse-audit', action='store_true', help='Separate untimed context equivalence gate')
    parser.add_argument('--reuse-controls', action='store_true', help='Synthetic local state isolation and prefix visibility controls')
    args = parser.parse_args()
    result = run(args.source_run, args.output, controls=args.controls_only,
                 arch_context=args.arch_context, arch_profile=args.arch_profile, reuse_arch=args.reuse_arch,
                 reuse_audit=args.reuse_audit, reuse_controls=args.reuse_controls)
    print({'pass': result['pass'], 'failure': result.get('failure')})
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
