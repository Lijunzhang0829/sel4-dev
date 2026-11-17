#!/usr/bin/env python3
"""Stage entry points only: all checking/planning/generation uses regen."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def preflight(root, resume_directory=None):
    from regen.preparation.checker_context import load_context
    from regen.planning.workspace_preflight import native_checker
    from regen.execution.isabelle_node_checker import IsabelleChecker
    from regen.infrastructure.resource_budget import clone_heaps
    from regen.planning.proof_graph import build_graph
    from regen.planning.batch_file_planner import seal_theory
    c = load_context(root / 'dataset')
    payload = json.loads((root / 'dataset/repository-planning-input.json').read_text())
    contexts = [x['contract_owned_context'] for x in payload['contracts']]
    if not contexts or not contexts[0] or any(x != contexts[0] for x in contexts):
        raise ValueError('heap preflight requires one shared, explicit contract context')
    directory = root / 'adapter-preflight' if resume_directory is None else (root / resume_directory).resolve()
    if resume_directory is None:
        if directory.exists(): directory = root / ('adapter-preflight-' + str(time.time_ns()))
        directory.mkdir(exist_ok=False)
    elif not directory.is_relative_to(root) or not directory.is_dir():
        raise ValueError('resume directory must be an existing preflight under this pilot')
    proposal = {'schema_version': 6, 'theories': [{'name': 'Adapter_Contracts',
        'contract_ids': [x['contract_id'] for x in payload['contracts']], 'required_imports': []}]}
    if (directory / 'contracts.json').exists():
        from regen.preparation.premise_database import digest
        result = json.loads((directory / 'contracts.json').read_text())
        if result['checker_context_id'] != c['checker_context_id']:
            raise ValueError('preflight context changed')
        for name, expected in result['source_sha256'].items():
            if digest(directory / 'contracts/source' / name) != expected:
                raise ValueError('preflight declaration source changed')
    else:
        result = native_checker(root / 'dataset', 300, threads=2)(payload, proposal, directory / 'contracts')
        save(directory / 'contracts.json', result)
    if not result['valid']: raise RuntimeError('fixed contract preflight failed: ' + result.get('diagnostic', ''))
    if not (directory / 'heaps').exists(): clone_heaps(c['heaps'], directory / 'heaps')
    checker = IsabelleChecker(directory, directory / 'smoke', image=c['image'], architecture=c['architecture'],
        timeout=300, generated_dir=directory / 'generated', heaps_dir=directory / 'heaps', l4v_dir=c['l4v'],
        semantic_workspace=c['semantic_workspace'], base_session=c['base_session'], base_import=c['base_import'],
        base_catalog=c['base_catalog'], isabelle_dir=c['isabelle'])
    false_contract = {'contract_id': 'infra-false', 'name': 'infra_false',
        'exact_statement': 'lemma infra_false: "False"', 'contract_owned_context': []}
    negative_graph = build_graph({'theory': 'Infra_False', 'file_slot': 'Infra_False.thy',
        'contracts': [false_contract]}, {}, run_id='calg-negative-control', revision=0)
    if (directory / 'false-positive-control.json').exists():
        from regen.preparation.premise_database import digest
        rejected = json.loads((directory / 'false-positive-control.json').read_text())
        receipt = rejected['native_receipt']
        source = Path(rejected['source'])
        expected = checker._source(negative_graph, {'infra-false': 'by simp'},
            theory_name='Node_Check_infra_false', target='infra-false')
        if (source.read_text() != expected
                or receipt['source_files_sha256'] != {'candidate/' + source.name: digest(source)}
                or digest(Path(rejected['log'])) != receipt['log_sha256']):
            raise ValueError('negative control artifacts changed')
    else:
        rejected = checker.check_node(negative_graph, {'infra-false': 'by simp'}, 'infra-false')
        save(directory / 'false-positive-control.json', rejected)
    if rejected['verdict'] != 'red': raise RuntimeError('false positive control was not rejected')
    from regen.execution.contract_refutation import check_refutation, validate_refutation_certificate
    if (directory / 'refutation-smoke/certificate.json').exists():
        certificate = json.loads((directory / 'refutation-smoke/certificate.json').read_text())
        validate_refutation_certificate(certificate, false_contract, checker)
        refuted = {'status': 'refuted'}
    else:
        refuted = check_refutation(checker, false_contract, 'by simp', directory / ('refutation-smoke-' + str(time.time_ns())))
    if refuted['status'] != 'refuted': raise RuntimeError('native refutation control failed: ' + refuted.get('diagnostic', ''))
    from regen.execution.batch_checker import BatchWorker
    prefix = 'theory Worker_Smoke\nimports "' + c['base_import'] + '"\nbegin\n\nunbundle l4v_word_context\n\nend\n'
    candidate = prefix.rsplit('\nend\n', 1)[0] + '\nlemma worker_smoke: "True" by simp\n\nend\n'
    if (directory / 'worker-smoke.json').exists():
        from regen.execution import batch_checker
        from regen.execution.native_runtime import worker_source
        batch = json.loads((directory / 'worker-smoke.json').read_text())
        server = checker.work_dir / batch['worker'] / 'server.ML'
        if (server.read_text() != worker_source(checker, Path(batch_checker.__file__).with_suffix('.ML').read_text())
                or Path(batch['source']).read_text() != candidate):
            raise ValueError('persistent control inputs changed')
    else:
        worker = BatchWorker(checker, prefix, [], checker.imports())
        try:
            batch = worker.check_batch(candidate, ['worker_smoke'])
            save(directory / 'worker-smoke.json', batch)
        finally:
            worker.close()
    if batch['verdict'] != 'green': raise RuntimeError('persistent batch smoke failed')
    # Test locale-bound export and global export, without a benchmark proof.
    package = {'theory': 'Infra_Smoke', 'file_slot': 'Infra_Smoke.thy', 'contracts': [
        {'contract_id': 'infra-global', 'name': 'infra_global', 'exact_statement': 'lemma infra_global: "True"',
         'contract_owned_context': []},
        {'contract_id': 'infra-locale', 'name': 'infra_locale', 'exact_statement': 'lemma infra_locale: "Suc (0::nat) = 1"',
         'contract_owned_context': contexts[0]}]}
    graph = build_graph(package, {}, run_id='calg-adapter-smoke', revision=0)
    proofs = {c['contract_id']: 'by simp' for c in package['contracts']}
    state = {'graph': graph, 'proofs': proofs, 'accepted_order': list(proofs), 'status': 'generating'}
    sealed, evidence = seal_theory(directory, {'premise_snapshot': str(c['premise_audit_context_path'])},
        package, directory, checker, [], state)
    save(directory / 'seal-evidence.json', evidence)
    if sealed is None: raise RuntimeError('seal preflight failed: ' + str(evidence.get('diagnostic')))
    save(directory / 'sealed-state.json', sealed)
    save(root / 'adapter-preflight.json', {'status': 'pass', 'contracts_checked': len(payload['contracts']),
        'benchmark_id': payload['benchmark']['id'], 'contract_context': contexts[0],
        'evidence_directory': str(directory.relative_to(root)),
        'fixed_targets_proved': 0, 'generic_global_and_locale_smoke': True,
        'false_positive_rejected': True, 'false_target_refutation_checked': True,
        'persistent_batch_checked': True,
        'native_audit_and_seal': True, 'seal_certificate_replay': True})


def configure(root):
    payload = json.loads((root / 'dataset/repository-planning-input.json').read_text())
    target_count = len(payload['contracts'])
    config = root / 'config'; config.mkdir(exist_ok=False)
    models = json.loads((PROJECT / 'regen/config/proof-models.json').read_text())
    models['reasoning_effort'] = 'low'
    models['role_models'] = {role: 'gpt-6-astra' for role in models['role_models']}
    models['comparison_models'] = ['gpt-6-astra']
    models['codex_runtime_root'] = str(root / 'runtime')
    models.pop('codex_historical_paths', None)
    save(config / 'models.json', models)
    policy = json.loads((PROJECT / 'regen/config/repository-planning-crefine-owner-blind-sol-xhigh-local.json').read_text())
    policy = json.loads(json.dumps(policy).replace('CRefine', 'Calg').replace('309', str(target_count)))
    policy['model'].update(model='gpt-6-astra', reasoning_effort='low', timeout_seconds=600)
    policy['preflight_seconds'] = 300
    save(config / 'planning-policy.json', policy)
    save(config / 'execution-policy.json', {'stages': [], 'session_stall_retries': 0, 'provider_feedback_retries': 1})
    save(root / 'configuration.json', {'model': 'gpt-6-astra', 'reasoning_effort': 'low', 'targets': target_count,
        'benchmark_id': payload['benchmark']['id'],
        'purpose': 'adapter and model-readiness pilot, not a comparative benchmark',
        'generation_wall_seconds': 900, 'generation_effective_seconds': 900,
        'repository_planning_excluded_and_reported_separately': True,
        'max_parallel_theories': 1, 'native_slots': 1, 'native_memory_mb': 8192,
        'contract_outcome_policy': 'contract-dispositions-v1', 'unresolved_evidence_policy': 'proof-or-refutation-v1',
        'target_specific_supervisor_proofs_or_hints': 0})


def export_scan(root):
    from regen.preparation.checker_context import load_context
    from regen.execution.isabelle_node_checker import IsabelleChecker
    from regen.execution.contract_refutation import check_export_scan
    c = load_context(root / 'dataset')
    directory = root / 'target-export-scan'
    checker = IsabelleChecker(directory, directory / 'source', image=c['image'], architecture=c['architecture'],
        timeout=300, generated_dir=directory / 'generated', heaps_dir=c['heaps'], l4v_dir=c['l4v'],
        semantic_workspace=c['semantic_workspace'], base_session=c['base_session'], base_import=c['base_import'],
        base_catalog=c['base_catalog'], isabelle_dir=c['isabelle'])
    contracts = json.loads((root / 'dataset/repository-planning-input.json').read_text())['contracts']
    result = check_export_scan(checker, contracts, directory / 'evidence')
    save(root / 'target-export-scan.json', result)
    if not result['passed']: raise RuntimeError('fixed contextual targets did not export')


def execution_preflight(root, proof, run):
    """Exercise the actual controller path before spending provider budget."""
    from regen.execution.check_executor import CheckExecutor, NativeResources
    from regen.planning.parallel_file_planner import TheoryController
    from regen.execution.repository_execution import checker_for
    from regen.planning.proof_graph import build_graph
    directory = root / 'execution-preflight'
    executor = CheckExecutor(directory / 'tasks', workers=1)
    resources = NativeResources(1, 2, 1)
    names = [p.stem for p in (proof / 'packages').glob('*.json')]
    read = []
    try:
        for name in names:
            # Packages use the .thy.json convention in some adapters.
            theory = name.removesuffix('.thy')
            controller = TheoryController(proof, theory, resources=resources, executor=executor,
                document_mode=True, generation_strategy_name='sketch-reviewed-v1')
            try:
                result = controller.handle('read_theory', {'theory': theory}, {})
                save(directory / (theory + '.json'), result)
                if not isinstance(result.get('document', {}).get('source'), str):
                    raise RuntimeError('controller failed to read fixed targets: ' + theory)
                read.append(theory)
            finally:
                controller.close()
        checker = checker_for(proof, run, directory / 'native', 300, [], heaps_dir=proof / 'heaps')
        graph = build_graph({'theory': 'Execution_Control', 'file_slot': 'Execution_Control.thy',
            'contracts': [{'contract_id': 'control', 'name': 'control',
                'exact_statement': 'lemma control: "True"', 'contract_owned_context': []}]},
            {}, run_id='execution-preflight', revision=0)
        try:
            declared = checker.check_declarations(graph)
            save(directory / 'native-declarations.json', declared)
            if declared['verdict'] != 'green': raise RuntimeError('execution declaration control failed')
            checked = checker.check_node(graph, {'control': 'by simp'}, 'control')
        finally:
            checker.close_prefix()
        save(directory / 'native-control.json', checked)
        if checked['verdict'] != 'green': raise RuntimeError('execution path native control failed')
        save(root / 'execution-preflight.json', {'status': 'pass', 'theories_read': read,
            'architecture': run['architecture'], 'fixed_targets_proved': 0,
            'native_generic_control': True})
    finally:
        executor.close()


def controller_smoke(root):
    """Offline fixture only: exercise projection/initialization/document reads.

    This one-theory test allocation is not a model plan and must never be reused
    as an experiment plan. No target proofs or helper statements are supplied.
    """
    from regen.planning.theory_plan import build_bundle
    from regen.planning.plan_revisions import start_plan, append_revision
    from regen.planning.repository_planner import _bind_direct_artifacts
    from regen.execution.proof_session_runner import init_run
    from regen.execution.repository_execution import progress
    fixture = root / 'controller-smoke'
    fixture.mkdir(exist_ok=False)
    payload = json.loads((root / 'dataset/repository-planning-input.json').read_text())
    proposal = {'schema_version': 6, 'ordering_rationale': 'Offline interface test; not an experiment plan.',
        'contract_relations': [], 'collaboration_groups': [], 'reuse_suggestions': [],
        'unresolved_retrieval_questions': [], 'theories': [{
            'name': 'Adapter_Contracts', 'purpose': 'Read every fixed declaration through the controller.',
            'grouping_reason': 'Test fixture only; no proposed proof decomposition.',
            'contract_ids': [x['contract_id'] for x in payload['contracts']],
            'context_profile_ids': [x['context_profile_id'] for x in payload['context_profiles']],
            'helper_decisions': [], 'required_imports': []}]}
    artifacts = build_bundle(payload, proposal)
    start_plan(fixture / 'planning', payload['benchmark']['id'])
    plan = append_revision(fixture / 'planning', artifacts['repository-plan.json'])
    for name, value in _bind_direct_artifacts(artifacts, plan, fixture).items():
        path = fixture / name; path.parent.mkdir(parents=True, exist_ok=True); save(path, value)
    save(fixture / 'fixture.json', {'fixture_only': True, 'model_calls': 0,
        'not_for_experiment_planning': True, 'proposal': proposal})
    os.environ['REGEN_MODEL_CONFIG'] = str(root / 'config/models.json')
    run = init_run(fixture / 'proof', root / 'dataset', run_id=root.name + '-controller-smoke',
        schedule_path=fixture / 'file-generation-list.json', log_storage='regen-merkle-json-v1',
        contract_outcome_policy='contract-dispositions-v1', unresolved_evidence_policy='proof-or-refutation-v1')
    execution_preflight(root, fixture / 'proof', run)
    result = progress(fixture / 'proof')
    if result['contracts'] != len(payload['contracts']) or result['proved_contracts'] or result['refuted_contracts']:
        raise RuntimeError('controller fixture must retain all unproved targets')
    save(fixture / 'progress.json', result)


def launch(root, reuse_planning=None):
    os.environ['REGEN_MODEL_CONFIG'] = str(root / 'config/models.json')
    from regen.tools.application_release import verify
    from regen.execution.proof_session_runner import init_run
    verify(root / 'application', root / 'release/manifest.json')
    if json.loads((root / 'adapter-preflight.json').read_text())['status'] != 'pass':
        raise ValueError('native preflight required')
    if not json.loads((root / 'target-export-scan.json').read_text())['passed']:
        raise ValueError('native contextual target export scan required')
    planning = root / 'planning'
    started = time.time()
    save(root / 'phase-status.json', {'stage': 'repository_planning', 'started_unix': started})
    command = [sys.executable, '-u', '-m', 'regen.tools.reviewed_planning',
        '--input', str(root / 'dataset/repository-planning-input.json'),
        '--policy', str(root / 'config/planning-policy.json'), '--output', str(planning),
        '--model', 'gpt-6-astra', '--reasoning-effort', 'low', '--allow-external-call']
    if reuse_planning is None:
        with (root / 'planning.log').open('w') as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, cwd=root / 'application')
        save(root / 'planning-timing.json', {'elapsed_seconds': time.time() - started,
            'returncode': result.returncode, 'included_in_generation_budget': False})
        if result.returncode: raise RuntimeError('repository planning failed; no generation started')
    else:
        import hashlib
        previous = reuse_planning.resolve()
        frozen = json.loads((previous / 'planning/frozen-plan.json').read_text())
        for relative, expected in frozen['artifacts'].items():
            path = previous / 'planning' / relative
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError('reused planning artifact changed: ' + relative)
        if (root / 'dataset/repository-planning-input.json').read_bytes() != (previous / 'dataset/repository-planning-input.json').read_bytes():
            raise ValueError('reused planning input differs')
        planning.symlink_to(previous / 'planning', target_is_directory=True)
        save(root / 'planning-timing.json', {'elapsed_seconds': 0, 'returncode': 0,
            'included_in_generation_budget': False, 'reused_from': str(previous),
            'original': json.loads((previous / 'planning-timing.json').read_text())})
    frozen = json.loads((planning / 'frozen-plan.json').read_text())
    if frozen['state'] != 'frozen': raise ValueError('plan not frozen')
    proof = root / 'proof'
    run = init_run(proof, root / 'dataset', run_id=root.name, schedule_path=Path(frozen['file_generation_list']),
        log_storage='regen-merkle-json-v1', contract_outcome_policy='contract-dispositions-v1',
        unresolved_evidence_policy='proof-or-refutation-v1')
    execution_preflight(root, proof, run)
    command = [sys.executable, '-u', '-m', 'regen.execution.proof_session_runner', 'parallel-workspace',
        '--run-dir', str(proof), '--planner-model', 'gpt-6-astra', '--file-planner-interface', 'document',
        '--max-parallel-theories', '1', '--native-slots', '1', '--resident-limit', '1',
        '--native-threads', '2', '--native-cpus', '4', '--native-memory-mb', '8192',
        '--native-pids-limit', '1024', '--build-ml-heap-mb', '4096',
        '--provider-build-timeout', '600', '--call-timeout', '600', '--check-timeout', '300',
        '--candidate-timeout', '180', '--generation-strategy', 'sketch-reviewed-v1',
        '--effective-seconds', '900', '--wall-seconds', '900',
        '--execution-policy', str(root / 'config/execution-policy.json'), '--allow-external-call']
    save(root / 'generation-invocation.json', {'command': command, 'cwd': str(root / 'application')})
    started = time.time()
    save(root / 'phase-status.json', {'stage': 'generation', 'started_unix': started})
    with (root / 'generation.log').open('w') as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, cwd=root / 'application')
    save(root / 'generation-timing.json', {'elapsed_seconds': time.time() - started, 'returncode': result.returncode})
    save(root / 'phase-status.json', {'stage': 'pilot_finished', 'ended_unix': time.time(), 'returncode': result.returncode})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['preflight', 'configure', 'export_scan', 'controller_smoke', 'launch'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--resume-directory')
    parser.add_argument('--reuse-planning', type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    # Launch from the frozen release; never let concurrent worktree edits affect a run.
    if args.action == 'launch':
        sys.path.insert(0, str(root / 'application'))
    if args.reuse_planning:
        if args.action != 'launch': parser.error('--reuse-planning only applies to launch')
        launch(root, args.reuse_planning)
    elif args.resume_directory:
        if args.action != 'preflight': parser.error('--resume-directory only applies to preflight')
        preflight(root, args.resume_directory)
    else:
        globals()[args.action](root)
