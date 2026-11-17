"""Build a C-free HOL parent for the existing numbered-dataset adapter.

This is preparation, not a generation runner. The bootstrap contains no goals
and no premise index. calg_shuffled_dataset.prepare must export the actual
premises and elaborate the contracts before it becomes a generation dataset.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import time
import uuid

from regen.execution import calg_process
from regen.execution.native_runtime import AFP_CACHE, PROFILE
from regen.preparation import calg_dataset as calg
from regen.preparation.calg_shuffled_dataset import inventory
from regen.preparation.checker_context import load_context
from regen.preparation.intervaltree_dataset import save
from regen.preparation.premise_database import SCHEMA, digest, verify_database
from regen.preparation.refine_campaign import sha256_json


def validate_fixed_input(source):
    """Reject a resealed package that still needs or ships C frontend inputs."""
    source = Path(source)
    manifest, _, _ = inventory(source)
    if (manifest.get('model_representation') != 'fixed-hol-source'
            or manifest.get('c_source_required_at_generation') is not False
            or manifest.get('c_translation_required_at_generation') is not False):
        raise ValueError('requires a frozen, C-free HOL-source dataset')
    for path in source.rglob('*'):
        if path.is_symlink():
            raise ValueError('model source must not contain links')
        if path.is_file() and (path.suffix.lower() in {'.c', '.h'} or
                (path.suffix in {'.thy', '.ML'} and re.search(
                    r'(?m)^\s*(?:install_C_file|autocorres|init-autocorres)\b',
                    path.read_text()))):
            raise ValueError('C input or translation command in fixed model: '+str(path))
    return manifest


def bootstrap(source, output, isabelle, afp, upstream_heaps):
    source, output, isabelle, afp, upstream_heaps = (
        Path(p).resolve() for p in (source, output, isabelle, afp, upstream_heaps))
    manifest = validate_fixed_input(source)
    original = source/'parent'
    output.mkdir(parents=True, exist_ok=False)
    assets = output/'checker-assets'; assets.mkdir()
    semantic = assets/'semantic-base'; shutil.copytree(original, semantic)
    shutil.copyfile(calg_process.__file__, semantic/'native_process.py')
    save(semantic/'native-runtime.json', PROFILE)
    (assets/'isabelle').symlink_to(isabelle, target_is_directory=True)
    l4v = assets/'l4v'; l4v.mkdir()
    (l4v/'afp').symlink_to(afp, target_is_directory=True)
    heaps = assets/'heaps'; heaps.mkdir()
    copied = []
    for path in upstream_heaps.rglob('*'):
        # A program-specific heap is never accepted as an upstream input.
        if path.is_file() and (path.name in {'Simpl', 'AutoCorres2'} or
                path.name.startswith(('Simpl.', 'AutoCorres2.'))):
            dest = heaps/path.relative_to(upstream_heaps)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest); copied.append(str(dest.relative_to(heaps)))
    if not any(Path(p).name == 'AutoCorres2' for p in copied):
        raise ValueError('pinned upstream AutoCorres2 heap is missing')
    evidence = output/'native-evidence'; evidence.mkdir()
    environment = json.loads((source/'environment.json').read_text())
    image = environment['container_image_id']
    name = 'calg-hol-bootstrap-'+uuid.uuid4().hex
    executable = '/workspace/verification/isabelle/bin/isabelle'
    command = ['docker', 'run', '--rm', '--name', name, '--network', 'none',
        '--cpus', '2', '--memory', '8g', '--memory-swap', '8g', '--pids-limit', '1024',
        '--mount', f'type=bind,src={isabelle},dst={executable.rsplit("/bin/",1)[0]},readonly',
        '--mount', f'type=bind,src={afp},dst={AFP_CACHE},readonly',
        '--mount', f'type=bind,src={semantic},dst=/semantic-base,readonly',
        '--mount', f'type=bind,src={heaps},dst=/parent-heaps',
        '-e', 'L4V_ARCH=X64', '--entrypoint', executable, image, 'env',
        'ISABELLE_HEAPS=/parent-heaps',
        'ML_OPTIONS=-H 256 --maxheap 4096 --stackspace 64 --gcthreads 2',
        'ISABELLE_TOOL_JAVA_OPTIONS=-Djava.awt.headless=true -Xms128m -Xmx1g -Xss8m',
        executable, 'build', '-b', '-j', '1', '-o', 'threads=2',
        '-o', 'document=false', '-d', AFP_CACHE, '-d', '/semantic-base', calg.BASE]
    save(evidence/'build-command.json', command)
    started = time.monotonic()
    try:
        with (evidence/'build.log').open('w') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                check=True, timeout=900)
    finally:
        from regen.execution.native_container import remove_owned
        remove_owned(name)
        subprocess.run(['docker', 'run', '--rm', '--network', 'none',
            '--mount', f'type=bind,src={heaps},dst=/heaps', image,
            'chown', '-R', f'{os.getuid()}:{os.getgid()}', '/heaps'], check=True)
    inventory(source)
    for path in original.rglob('*'):
        if path.is_file() and digest(path) != digest(semantic/path.relative_to(original)):
            raise ValueError('model changed during native bootstrap')
    # The consuming adapter creates the full native premise index. An empty
    # index here deliberately supplies no proof or advertised fact.
    database = output/'parent-premises.sqlite'
    with sqlite3.connect(database) as db:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO premise_search(premise_search) VALUES ('rebuild')")
    upstream = sorted(calg.UPSTREAM | {'Calg_Binomial_Foundation'})
    payload = dict(schema_version=1, artifact_type='repository-planning-input',
        benchmark=dict(id='calg-hol-bootstrap', target_session='Calg', architecture='X64',
            independent_experiment=True, cross_experiment_inputs=[]),
        contracts=[], contract_cards=[], contract_visibility='owner-blind-v1',
        owner_groups={}, session_graph={}, theory_import_graph={},
        upstream_session_closure=upstream, contract_relation_candidates=[],
        context_profiles=[], planner_interfaces={},
        parent_premises=dict(database='parent-premises.sqlite',
            manifest='parent-premises-manifest.json', record_count=0,
            database_sha256=digest(database), record_ids_sha256=sha256_json([]),
            query_interface='proof-state-recall-v2'))
    payload['planning_input_id'] = 'calg-hol-bootstrap:'+sha256_json(payload)[:20]
    save(output/'repository-planning-input.json', payload)
    save(output/'parent-premises-manifest.json', dict(schema_version=2,
        database_sha256=digest(database), record_count=0,
        source_policy='bootstrap-only-no-premises-advertised'))
    verify_database(database)
    audit = dict(schema_version=1, artifact_type='calg-parent-premise-audit-context',
        campaign_id='bootstrap:'+manifest['dataset_binding'],
        semantic_base_seal_id='native-model:'+digest(evidence/'build.log'), records=[])
    save(output/'premise-audit-context.json', audit)
    core = dict(schema_version=1, artifact_type='calg-checker-context',
        target_session='Calg', architecture='X64', paths=dict(l4v='checker-assets/l4v',
            semantic_workspace='checker-assets/semantic-base', heaps='checker-assets/heaps',
            isabelle='checker-assets/isabelle'), premise_database='parent-premises.sqlite',
        premise_audit_context='premise-audit-context.json',
        premise_audit_context_sha256=digest(output/'premise-audit-context.json'),
        premise_database_sha256=digest(database), base_session=calg.BASE,
        base_import=calg.IMPORT, base_catalog='/semantic-base', image=image,
        isabelle_sha256=calg.distribution_identity(isabelle),
        isabelle_identity_kind='installed-distribution-files-and-links-v1',
        checker_policy='fixed-prefix-cold-green-v1',
        budgets=dict(graph_revisions_per_generation=16, model_call_timeout_seconds=600,
            whole_proof_attempts_per_logical_round=5))
    core['checker_context_id'] = 'checker-context:'+sha256_json(core)[:20]
    save(output/'checker-context.json', core)
    load_context(output)
    result = dict(status='native-hol-parent-bootstrap', not_a_generation_dataset=True,
        dataset_binding=manifest['dataset_binding'], target_proofs_added=0, model_calls=0,
        seconds=time.monotonic()-started,
        semantic_files={str(p.relative_to(semantic)):digest(p) for p in semantic.rglob('*') if p.is_file()},
        next_step='regen.preparation.calg_shuffled_dataset.prepare')
    save(output/'bootstrap.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('source', 'output', 'isabelle', 'afp', 'upstream-heaps'):
        parser.add_argument('--'+key, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(bootstrap(args.source, args.output, args.isabelle, args.afp, args.upstream_heaps)))
