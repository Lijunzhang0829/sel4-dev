"""Resumable environment preparation using existing producers and gates."""
from __future__ import annotations

from pathlib import Path
import shutil

from regen.preparation import premise_export, upstream_heaps
from regen.preparation.command_necessity import accept_green_baseline
from regen.preparation.contract_command_inventory import build_inventory, write_inventory
from regen.preparation.environment_dataflow import publish_dataflow, validate_dataflow
from regen.preparation.environment_gate import (
    build_environment_gate, build_semantic_base_seal, validate_environment_gate,
)
from regen.infrastructure.path_identity import source_identity
from regen.preparation.environment_journal import EnvironmentJournal, safe_path
from regen.preparation.premise_catalog import build_snapshot, write_database
from regen.preparation.refine_campaign import (
    build_manifest, read_json, sha256_json, verify_frozen_inputs, verify_manifest, write_immutable_json,
)
from regen.preparation.semantic_base_builder import (
    docker_image_identity, initial_retained_command_ids, materialize_workspace, run_isabelle_build,
)

STAGES = ('freeze', 'inventory', 'runtime', 'toolchain', 'build', 'selection', 'seal', 'export', 'catalog', 'gate', 'dataflow')
MANIFEST = 'Environment-Freeze/campaign-manifest.json'
INVENTORY = 'Contract-Command-Inventory/private/command-inventory.json'
PACKAGES = 'Contract-Command-Inventory/public/contract-packages'
BUILD = 'Semantic-Base/build-result.json'
WORKSPACE = 'Semantic-Base/workspace'
HEAPS = 'Semantic-Base/heaps'
LEDGER = 'Command-Selection/command-necessity-ledger.json'
SEAL = 'Semantic-Base/semantic-base-seal.json'
DISCLOSURE = 'Premise-Export/context-effect-disclosure.json'
SNAPSHOT = 'Premise-Catalog/premise-snapshot.json'
DATABASE = 'Premise-Catalog/premises.sqlite3'
GATE = 'Environment-Preparation/environment-gate.json'
INDEX = 'Environment-Dataflow/environment-index.json'


def initialize(root: Path, repo: Path, config: Path, *, overlay: Path | None = None,
               image: str = 'sel4-public:latest', timeout: int = 2400,
               upstream_cache: Path | None = None) -> dict:
    journal = EnvironmentJournal(root)
    with journal.lock():
        recipe_path = journal.private / 'recipe.json'
        if recipe_path.exists() or journal.events():
            raise ValueError('environment already initialized; use resume')
        if any(path.name != '.preparation' for path in journal.root.iterdir()):
            raise ValueError('prepare requires an empty environment root; existing campaigns are not overwritten')
        repo = repo.resolve()
        manifest = build_manifest(repo, config)
        if manifest['setting'] != 'Semantic-Base' or manifest['architecture'] != 'ARM':
            raise ValueError('current environment pipeline supports Semantic-Base Refine / ARM only')
        source = repo / 'verification/l4v'
        recipe = {'schema_version': 1, 'artifact_type': 'environment-preparation-recipe',
                  'environment_root': str(journal.root), 'repo': str(repo),
                  'manifest': manifest, 'source_l4v': str(source),
                  'source_sha256': source_identity(source),
                  'overlay': str(overlay.resolve()) if overlay else None,
                  'overlay_sha256': source_identity(overlay.resolve()) if overlay else None,
                  'image': image, 'timeout_seconds': timeout,
                  'upstream_cache': str(upstream_cache.resolve()) if upstream_cache else None,
                  'upstream_cache_records': upstream_heaps.inventory(upstream_cache) if upstream_cache else []}
        if timeout <= 0:
            raise ValueError('timeout must be positive')
        journal.append('environment-initialized', recipe=recipe,
                       recipe_sha256=sha256_json(recipe))
        write_immutable_json(recipe_path, recipe)
        return recipe


def load_recipe(journal: EnvironmentJournal) -> dict:
    initialized = [e for e in journal.events() if e['event'] == 'environment-initialized']
    if len(initialized) != 1:
        raise ValueError('journal requires exactly one environment initialization')
    event = initialized[0]
    recipe = event['recipe']
    if sha256_json(recipe) != event['recipe_sha256']:
        raise ValueError('recipe identity mismatch')
    if recipe['environment_root'] != str(journal.root):
        raise ValueError('restore must use the original environment root (absolute build paths are frozen)')
    verify_manifest(recipe['manifest'])
    write_immutable_json(journal.private / 'recipe.json', recipe)
    return recipe


def validate_sources(recipe: dict):
    verify_frozen_inputs(Path(recipe['repo']), recipe['manifest'])
    if source_identity(Path(recipe['source_l4v'])) != recipe['source_sha256']:
        raise ValueError('trusted l4v input bytes drifted since initialization')
    if recipe['overlay'] and source_identity(Path(recipe['overlay'])) != recipe['overlay_sha256']:
        raise ValueError('generated overlay bytes drifted since initialization')


def run(root: Path, *, until: str = 'dataflow') -> dict:
    if until not in STAGES:
        raise ValueError(f'unknown environment stage: {until}')
    journal = EnvironmentJournal(root)
    with journal.lock():
        recipe = load_recipe(journal)
        root = journal.root
        repo = Path(recipe['repo'])
        manifest = recipe['manifest']
        timeout = recipe['timeout_seconds']
        def write(relative, payload):
            write_immutable_json(root / relative, payload)
        def freeze():
            if recipe.get('upstream_cache'):
                upstream_heaps.copy_cache(Path(recipe['upstream_cache']),
                    root / 'Environment-Freeze/upstream-heaps', recipe['upstream_cache_records'])
            write(MANIFEST, manifest)
            write('Environment-Freeze/config.json', read_json(repo / manifest['campaign_config']['path']))
            for record in manifest['frozen_inputs']:
                destination = safe_path(root / 'Environment-Freeze/frozen-inputs', record['path'])
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(repo / record['path'], destination)
        def inventory():
            write_inventory(root / 'Contract-Command-Inventory', build_inventory(
                repo, manifest['campaign_id'], architecture=manifest['architecture']))
        def runtime():
            from regen.preparation.environment_preparation import materialize_sanitized_l4v
            materialize_sanitized_l4v(Path(recipe['source_l4v']), root / 'Runtime/l4v',
                                      root / 'Runtime/sanitized-l4v-runtime.json',
                                      Path(recipe['overlay']) if recipe['overlay'] else None)
        def toolchain():
            identity = docker_image_identity(recipe['image'])
            if identity.get('inspect_verdict') != 'pass' or not identity.get('image_id'):
                raise ValueError(f'cannot freeze Docker image: {identity}')
            write('Toolchain/identity.json', {'image': identity,
                  'isabelle_sha256': source_identity(repo / 'verification/isabelle', allow_links=True)})
        def check_toolchain():
            identity = read_json(root / 'Toolchain/identity.json')
            if source_identity(repo / 'verification/isabelle', allow_links=True) != identity['isabelle_sha256']:
                raise ValueError('Isabelle installation drifted after toolchain freeze')
            return identity['image']['image_id']
        def build():
            if recipe.get('upstream_cache'):
                upstream_heaps.copy_cache(root / 'Environment-Freeze/upstream-heaps',
                                         root / HEAPS, recipe['upstream_cache_records'])
            inv = read_json(root / INVENTORY)
            workspace = materialize_workspace(inv, root / WORKSPACE, initial_retained_command_ids(inv))
            result = run_isabelle_build(root / WORKSPACE, root / 'Runtime/l4v', root / HEAPS,
                                       timeout_seconds=timeout, image=check_toolchain(),
                                       isabelle=repo / 'verification/isabelle')
            write(BUILD, {'workspace': workspace, 'isabelle': result})
            if result['verdict'] != 'pass':
                raise ValueError(f"Semantic Base build {result['verdict']}: {result.get('diagnostic', '')}")
            check_toolchain()
        def selection():
            write(LEDGER, accept_green_baseline(read_json(root / INVENTORY), root / BUILD))
        def seal():
            write(SEAL, build_semantic_base_seal(root, root / MANIFEST, root / INVENTORY,
                  root / LEDGER, root / BUILD, root / WORKSPACE, root / HEAPS,
                  root / 'Runtime/l4v', root / 'Runtime/sanitized-l4v-runtime.json'))
        def export():
            result = premise_export.run_export(repo, root / WORKSPACE, root / 'Runtime/l4v',
                      root / HEAPS, root / 'Premise-Export', image=check_toolchain(), timeout_seconds=timeout)
            write('Premise-Export/fact-export-result.json', result)
            if result['verdict'] != 'pass':
                raise ValueError(f"premise export {result['verdict']}: {result.get('diagnostic', '')}")
            seal_value = read_json(root / SEAL)
            raw = root / 'Premise-Export/raw-export'
            records = premise_export.parse_fact_records(raw / 'facts.tsv', raw / 'automation-pools.tsv',
                      campaign_id=manifest['campaign_id'], seal_id=seal_value['seal_id'],
                      semantic_theories=premise_export.semantic_theory_names(read_json(root / BUILD)))
            write('Premise-Export/premise-records.json', {'records': records})
            write(DISCLOSURE, premise_export.build_context_effect_disclosure(
                  seal_value, result, records, raw / 'automation-pools.tsv', raw / 'context-metadata.tsv'))
            check_toolchain()
        def catalog():
            snapshot = build_snapshot(read_json(root / SEAL),
                       read_json(root / 'Premise-Export/premise-records.json')['records'])
            write(SNAPSHOT, snapshot)
            write_database(root / DATABASE, snapshot['records'])
        def gate():
            write(GATE, build_environment_gate(root, root / SEAL, root / DISCLOSURE,
                                              root / PACKAGES, root / SNAPSHOT, root / DATABASE))
        steps = [
            ('freeze', ['Environment-Freeze'], freeze),
            ('inventory', ['Contract-Command-Inventory'], inventory),
            ('runtime', ['Runtime'], runtime),
            ('toolchain', ['Toolchain'], toolchain),
            ('build', [WORKSPACE, HEAPS, BUILD], build),
            ('selection', ['Command-Selection'], selection),
            ('seal', [SEAL], seal),
            ('export', ['Premise-Export'], export),
            ('catalog', ['Premise-Catalog'], catalog),
            ('gate', [GATE], gate),
            ('dataflow', ['Environment-Dataflow'], lambda: publish_dataflow(root, root / GATE, root / 'Environment-Dataflow')),
        ]
        limit = STAGES.index(until)
        pending = any(name not in journal.completed() for name in STAGES[:limit + 1])
        if pending:
            try:
                validate_sources(recipe)
            except Exception as error:
                journal.append('input-validation-failed', error_type=type(error).__name__, diagnostic=str(error))
                raise
        for name, outputs, action in steps[:limit + 1]:
            def checked_action(action=action, name=name):
                action()
                if name in {'freeze', 'inventory', 'runtime'}:
                    validate_sources(recipe)
            journal.run_stage(name, outputs, checked_action)
        if until == 'dataflow':
            validate_dataflow(root, root / GATE, root / INDEX)
        elif until == 'gate':
            validate_environment_gate(root, root / GATE)
        return {'campaign_id': manifest['campaign_id'], 'completed_through': until,
                'ready': until == 'dataflow', 'environment_root': str(root),
                'journal': str(journal.log), 'dataflow': INDEX if until == 'dataflow' else None}


def restore(root: Path) -> dict:
    journal = EnvironmentJournal(root)
    with journal.lock():
        journal.trim_incomplete_tail()
        recipe = load_recipe(journal)
        completed = journal.completed()
        unknown = set(completed) - set(STAGES)
        if unknown:
            raise ValueError(f'unknown completed stages: {sorted(unknown)}')
        names = [name for name in STAGES if name in completed]
        if names != list(STAGES[:len(names)]):
            raise ValueError('non-contiguous completed stages in journal')
        for name in names:
            journal.restore_stage(completed[name], repair=True)
        if 'dataflow' in completed:
            validate_dataflow(journal.root, journal.root / GATE, journal.root / INDEX)
        elif 'gate' in completed:
            validate_environment_gate(journal.root, journal.root / GATE)
        journal.append('environment-restored', completed_stages=names)
        return {'campaign_id': recipe['manifest']['campaign_id'], 'restored_stages': names,
                'ready': 'dataflow' in completed, 'next_stage': STAGES[len(names)] if len(names) < len(STAGES) else None}
