#!/usr/bin/env python3
"""Replay an archived model counterexample through the production negative checker.

This is a disclosed validation fixture, not a fresh generation result. It reads
the stopped source experiment, extracts just its final local counterexample,
and changes the terminal positive ``by fail`` to use that counterexample. No
preceding generated target/helper proofs are imported or sent to a model.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')


def protected_inputs(source):
    names = ['proof/run.json', 'proof/experiment.json', 'application-manifest.json',
             'dataset/checker-context.json', 'dataset/premise-audit-context.json',
             'dataset/repository-planning-input.json']
    files = [source / name for name in names]
    for folder in ['proof/theories', 'proof/effective-generation-budget']:
        files.extend(path for path in (source / folder).rglob('*') if path.is_file()
                     and not path.name.endswith('.lock')
                     and ('acceptance' in path.parts or 'effective-generation-budget' in path.parts))
    return {str(p.relative_to(source)): sha(p) for p in sorted(set(files))}


def main(source, root):
    sys.path.insert(0, str(root / 'application'))
    from regen.execution.check_executor import CURRENT_RESOURCES, NativeResources
    from regen.execution.contract_refutation import check_refutation, validate_refutation_certificate
    from regen.execution.isabelle_node_checker import IsabelleChecker
    from regen.execution.shared_proof_workspace import SharedProofWorkspace
    from regen.infrastructure.incremental_log import read_artifact
    from regen.infrastructure.resource_budget import clone_heaps
    from regen.preparation.checker_context import load_context
    from regen.tools.application_release import verify

    started = time.time()
    verify(root / 'application', root / 'application-manifest.json')
    before = protected_inputs(source)
    save(root / 'source-before.json', before)
    archive = source / 'proof/theories/Tree_Execution/batch-requests/000026/check.json'
    row = read_artifact(archive)
    source_text = row['check']['source_content']
    workspace = SharedProofWorkspace(source / 'proof')
    package = workspace._package('Tree_Execution')
    contract, = [c for c in package['contracts'] if c['name'] == 'tree_remove_preserves_wf']
    declaration = contract['exact_statement'] + '\n'
    if source_text.count(declaration) != 1:
        raise ValueError('archived fixed target declaration is ambiguous')
    tail = source_text.split(declaration, 1)[1]
    proof = tail.split('\nML \\<open>writeln ("REGEN_TIMING_END ', 1)[0].strip()
    terminal = '  show ?thesis by fail\nqed'
    if not proof.startswith('proof -\n') or not proof.endswith(terminal) or proof.count(terminal) != 1:
        raise ValueError('archived counterexample layout changed; refuse heuristic extraction')
    proof = proof[:-len(terminal)] + '  show ?thesis using counterexample by simp\nqed'
    (root / 'replayed-proof.isar').write_text(proof + '\n')
    provenance = {'source_experiment': str(source), 'source_artifact': str(archive),
        'source_native_verdict': row['check']['verdict'],
        'source_native_receipt': row['check'].get('native_receipt'),
        'source_content_sha256': hashlib.sha256(source_text.encode()).hexdigest(),
        'contract': contract, 'replayed_proof_sha256': sha(root / 'replayed-proof.isar'),
        'transformation': 'Extract only target proof; replace final show ?thesis by fail with show ?thesis using counterexample by simp.',
        'reused_model_counterexample': True, 'preceding_positive_proofs_imported': False,
        'model_calls': 0, 'fresh_generation_credit': 0}
    save(root / 'provenance.json', provenance)
    context = load_context(source / 'dataset')
    out = root / 'native-check'; out.mkdir(exist_ok=False)
    clone_heaps(context['heaps'], out / 'heaps')
    checker = IsabelleChecker(out, out / 'checks', image=context['image'], timeout=900,
        generated_dir=out / 'generated', heaps_dir=out / 'heaps', l4v_dir=context['l4v'],
        semantic_workspace=context['semantic_workspace'], base_session=context['base_session'],
        base_import=context['base_import'], base_catalog=context['base_catalog'], isabelle_dir=context['isabelle'])
    resources = NativeResources(1, 2, 1, cpus=4, memory_mb=8192, pids_limit=1024,
                               provider_build_timeout=900, build_ml_heap_mb=2048)
    token = CURRENT_RESOURCES.set(resources)
    try:
        result = check_refutation(checker, contract, proof, out / 'negative')
        if result['status'] != 'refuted':
            save(root / 'validation.json', {'status': 'fail', 'result': result,
                 'started_unix': started, 'finished_unix': time.time()})
            raise RuntimeError('production negative check: ' + result['status'])
        certificate = result['certificate']
        validate_refutation_certificate(certificate, contract, checker)
    finally:
        try:
            checker.close_prefix()
        finally:
            CURRENT_RESOURCES.reset(token)
            resources.close()
    after = protected_inputs(source)
    if before != after:
        raise ValueError('source experiment protected inputs changed')
    receipt = {'status': 'pass', 'started_unix': started, 'finished_unix': time.time(),
        'contract_id': contract['contract_id'], 'contract_name': contract['name'],
        'outcome': 'refuted', 'certificate_id': certificate['certificate_id'],
        'certificate': str(out / 'negative/certificate.json'),
        'native_check_receipt': certificate['native_receipt'], 'audit': certificate['audit'],
        'transport': 'production IsabelleChecker Docker cold full theory',
        'imports': certificate['imports'], 'source_unchanged': True,
        'protected_source_files': len(before), 'model_calls': 0,
        'fresh_generation_credit': 0, 'positive_proofs_added': 0,
        'theory_sealed': False, 'published_to_experiment': False,
        'certificate_rebinding_validation': 'pass',
        'limitation': 'Proves negation of the frozen Isabelle model contract; does not establish the same bug in upstream Python.'}
    save(root / 'validation.json', receipt)
    print(json.dumps({k:v for k,v in receipt.items() if k != 'audit'}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    main(args.source.resolve(), args.root.resolve())
