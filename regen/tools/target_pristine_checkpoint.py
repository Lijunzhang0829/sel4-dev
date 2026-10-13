"""Create a proof-oracle-free target checkpoint from a sealed repository run.

Historical evidence may select the target, but this builder never reads the
target theory's acceptance journal, draft, requests, or provider transcript.
It verifies and reuses only the target's sealed transitive providers, creates a
new run identity, and initializes the target theory from its fixed contract
package with zero accepted proofs.  The fixed scaffold is checked by the normal
native declaration checker before the checkpoint is published.

No model is called by this module.  The native scaffold/provider checks are
part of checkpoint preparation and must not be charged as proving time in a
derived A/B arm.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
from uuid import uuid4

from regen.execution.proof_commits import digest, durable, freeze_evidence
from regen.execution.proof_session_runner import generated_checkpoint_theory
from regen.execution.repository_execution import (
    CONTINUATION_ENV_FIELDS,
    _reuse_sealed_theories,
    _sealed_provider_inputs,
    init_run,
    journal,
    log_policy_arguments,
    package_for,
    policy_binding,
    progress,
    read,
    setup,
)
from regen.planning.import_contract import required_imports


SCHEMA = 'target-pristine-checkpoint-v1'
# incremental_log.enable permits this coordination directory on a new root;
# a marker at the root would make every merkle-logged real run fail startup.
_OWNERSHIP_MARKER = '.runner-locks/target-pristine-preparation.json'


def _sha256(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def _regular_file(path, label):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(label + ' must be a regular file: ' + str(path))
    return path


def _source_layout(source_dir, source):
    """Reject relocated package paths and symlinked frozen metadata."""
    _regular_file(source_dir/'run.json', 'source run')
    _regular_file(source_dir/'execution-input.json', 'source execution input')
    if source.get('execution_mode') != 'repository-projection-v1':
        raise ValueError('source is not a repository-projection run')
    if source.get('execution_input_kind') != 'refine-dataset':
        raise ValueError('target-pristine checkpoints require refine-dataset input')
    packages = Path(source.get('contract_package_dir', ''))
    if (not packages.is_absolute() or packages.is_symlink()
            or not packages.is_dir()
            or packages.resolve() != (source_dir/'packages').resolve()):
        raise ValueError('source contract packages must be the frozen in-run packages')


def _execution_index(data):
    files = data.get('files') if isinstance(data, dict) else None
    if not isinstance(files, list) or not files:
        raise ValueError('source execution input has no theory files')
    by_theory = {}
    by_checkpoint = {}
    slots = set()
    for row in files:
        if not isinstance(row, dict):
            raise ValueError('invalid source execution file entry')
        theory, slot = row.get('theory'), row.get('file_slot')
        path = Path(slot) if isinstance(slot, str) else None
        if (not isinstance(theory, str) or not theory
                or path is None or path.name != slot or path.suffix != '.thy'
                or not path.stem):
            raise ValueError('unsafe source theory/file-slot identity')
        checkpoint = generated_checkpoint_theory(slot)
        if theory in by_theory or slot in slots or checkpoint in by_checkpoint:
            raise ValueError('duplicate source theory/file-slot identity')
        by_theory[theory] = row
        by_checkpoint[checkpoint] = row
        slots.add(slot)
    return by_theory, by_checkpoint


def _package(source_dir, source, entry):
    path = source_dir/'packages'/(entry['file_slot']+'.json')
    _regular_file(path, 'source contract package')
    package = package_for(source, entry['file_slot'])
    if (package.get('theory') != entry['theory']
            or package.get('file_slot') != entry['file_slot']):
        raise ValueError('source package identity differs from execution input')
    return package


def _target_entry(source_dir, source, theory, data):
    matches = [row for row in data['files'] if row['theory'] == theory]
    if len(matches) != 1:
        raise ValueError('target theory is absent or ambiguous: ' + theory)
    entry = matches[0]
    package = _package(source_dir, source, entry)
    return entry, package, data


def _frozen_imports(source_dir, package, data):
    """Derive imports from the fixed repository projection, not target history."""
    by_theory, _ = _execution_index(data)
    declared = required_imports(package['repository_projection'])
    if (not isinstance(declared, list)
            or not all(isinstance(row, dict) and isinstance(row.get('theory'), str)
                       for row in declared)):
        raise ValueError('target projection has invalid required imports')
    providers = [row['theory'] for row in declared]
    if len(set(providers)) != len(providers):
        raise ValueError('target projection contains duplicate providers')
    missing = [name for name in providers if name not in by_theory]
    if missing:
        raise ValueError('target provider is absent from source run: '
                         + ', '.join(sorted(missing)))
    imports = [generated_checkpoint_theory(by_theory[name]['file_slot'])
               for name in providers]
    return imports


def _projection_provider_closure(source_dir, source, package, data):
    """Resolve the provider DAG without opening any source theory history."""
    _, by_checkpoint = _execution_index(data)
    target = generated_checkpoint_theory(package['file_slot'])
    direct = _frozen_imports(source_dir, package, data)
    active, visited, order, expected_imports = set(), set(), [], {}

    def visit(name):
        # This guard precedes every imports.json/commit read.  A malformed
        # self/cycle through the target therefore cannot become an oracle.
        if name == target:
            raise ValueError('target theory cannot appear in its provider closure')
        if name in active:
            raise ValueError('cyclic frozen provider projection')
        if name in visited:
            return
        entry = by_checkpoint.get(name)
        if entry is None:
            raise ValueError('target provider is absent from source run: ' + name)
        provider = _package(source_dir, source, entry)
        imports = _frozen_imports(source_dir, provider, data)
        active.add(name)
        for imported in imports:
            visit(imported)
        active.remove(name)
        visited.add(name)
        expected_imports[name] = imports
        order.append(name)

    for name in direct:
        visit(name)
    return direct, order, expected_imports


def _provider_history_paths(source_dir, data, checkpoints):
    """Verify that the only histories about to be read are in-tree providers."""
    _, by_checkpoint = _execution_index(data)
    for name in checkpoints:
        entry = by_checkpoint[name]
        directory = source_dir/'theories'/Path(entry['file_slot']).stem
        if (directory.is_symlink() or not directory.is_dir()
                or directory.resolve() != directory):
            raise ValueError('sealed provider directory escapes source run: ' + name)
        _regular_file(directory/'imports.json', 'sealed provider imports')
        commits = directory/'acceptance'/'commits'
        if commits.is_symlink() or not commits.is_dir():
            raise ValueError('sealed provider commit directory is invalid: ' + name)
        paths = sorted(commits.glob('*.json'))
        if not paths:
            raise ValueError('sealed provider has no accepted commits: ' + name)
        for path in paths:
            _regular_file(path, 'sealed provider commit')


def _verified_provider_inputs(source_dir, source, package, data):
    direct, closure, expected_imports = _projection_provider_closure(
        source_dir, source, package, data)
    _provider_history_paths(source_dir, data, closure)
    prepared = _sealed_provider_inputs(source_dir, source, direct)
    actual = []
    for item in prepared:
        if not isinstance(item, tuple) or len(item) != 3:
            raise ValueError('invalid verified provider input')
        provider, origin, state = item
        name = generated_checkpoint_theory(provider['file_slot'])
        actual.append(name)
        if name not in expected_imports or origin.get('imports') != expected_imports[name]:
            raise ValueError('sealed provider imports differ from frozen projection: ' + name)
        commits = origin.get('commits')
        if (not isinstance(commits, list) or not commits
                or commits[-1].get('event') != 'seal'
                or commits[-1].get('state', {}).get('status') != 'sealed'
                or state.get('proofs') is None):
            raise ValueError('sealed provider lacks verified terminal state: ' + name)
    if actual != closure:
        raise ValueError('verified provider closure differs from frozen projection')
    return direct, prepared


def _claim_destination(destination):
    destination.mkdir(parents=True, exist_ok=False)
    token = uuid4().hex
    durable(destination/_OWNERSHIP_MARKER,
            {'schema': SCHEMA, 'token': token}, immutable=True)
    return token


def _cleanup_destination(destination, token):
    """Remove only the fresh directory claimed by this invocation."""
    marker = destination/_OWNERSHIP_MARKER
    if (destination.is_symlink() or not destination.is_dir()
            or marker.is_symlink() or not marker.is_file()
            or read(marker) != {'schema': SCHEMA, 'token': token}):
        raise RuntimeError('refusing to clean an unowned checkpoint destination')
    if not shutil.rmtree.avoids_symlink_attacks:
        raise RuntimeError('descriptor-relative symlink-safe rmtree is required')
    shutil.rmtree(destination)


def _model_binding(model, reasoning_effort):
    from regen.infrastructure.audited_llm import model_settings
    settings = model_settings()
    if (not isinstance(model, str) or not model.strip()
            or model != model.strip()):
        raise ValueError('model must be a non-empty normalized identity')
    if (not isinstance(reasoning_effort, str) or not reasoning_effort.strip()
            or settings.get('reasoning_effort') != reasoning_effort):
        raise ValueError('requested reasoning effort differs from frozen model configuration')
    roles = settings.get('role_models')
    if not isinstance(roles, dict) or not roles:
        raise ValueError('frozen model configuration has no execution roles')
    return {role: model for role in roles}


def verify(run_dir, expected_provenance_sha256, *, prepared_relocation=None):
    """Replay the semantic checkpoint authority without a model/native call.

    Prepared-checkpoint inventory is a separate copy authority.  Its manifest
    should pin both ``expected_provenance_sha256`` and every byte under this
    run; it must not replace these journal/package/import checks.
    """
    run_dir = Path(run_dir).resolve()
    provenance_path = _regular_file(
        run_dir/'target-pristine.json', 'target-pristine provenance')
    if (not isinstance(expected_provenance_sha256, str)
            or len(expected_provenance_sha256) != 64
            or _sha256(provenance_path) != expected_provenance_sha256):
        raise ValueError('target-pristine provenance hash mismatch')
    provenance = read(provenance_path)
    if provenance.get('schema') != SCHEMA:
        raise ValueError('unsupported target-pristine provenance')
    run = read(run_dir/'run.json')
    if prepared_relocation is None:
        if provenance.get('destination_run') != str(run_dir):
            raise ValueError('target-pristine destination identity mismatch')
        if _sha256(run_dir/'run.json') != provenance.get('destination_run_sha256'):
            raise ValueError('target-pristine run identity mismatch')
    else:
        required = {'source_run', 'source_run_sha256', 'run_binding'}
        if (not isinstance(prepared_relocation, dict)
                or set(prepared_relocation) != required
                or provenance.get('destination_run')
                != prepared_relocation['source_run']
                or provenance.get('destination_run_sha256')
                != prepared_relocation['source_run_sha256']
                or {key: value for key, value in run.items()
                    if key not in {'contract_package_dir',
                                   'session_progress_ledger'}}
                != prepared_relocation['run_binding']):
            raise ValueError('invalid prepared target-pristine relocation binding')
    if (run.get('execution_models') != provenance.get('execution_models')
            or set(run.get('execution_models', {}).values()) != {provenance.get('model')}
            or not provenance.get('reasoning_effort')):
        raise ValueError('target-pristine model binding mismatch')
    data = read(run_dir/'execution-input.json')
    _execution_index(data)
    entry, package, _ = _target_entry(
        run_dir, run, provenance.get('theory'), data)
    if (package.get('package_payload_sha256')
            != provenance.get('target_package_sha256')):
        raise ValueError('target-pristine package binding mismatch')
    if provenance.get('target_contract_id') not in {
            row['contract_id'] for row in package['contracts']}:
        raise ValueError('target-pristine target is absent from fixed package')
    direct, closure, _ = _projection_provider_closure(
        run_dir, run, package, data)
    if direct != provenance.get('derived_imports'):
        raise ValueError('target-pristine import binding mismatch')
    receipts = provenance.get('reused_sealed_providers')
    if (not isinstance(receipts, list)
            or len(receipts) != provenance.get('reused_sealed_provider_count')
            or [row.get('checkpoint_theory') for row in receipts] != closure):
        raise ValueError('target-pristine provider receipt closure mismatch')

    by_theory, _ = _execution_index(data)
    for receipt in receipts:
        provider_entry = by_theory.get(receipt.get('theory'))
        if (provider_entry is None
                or generated_checkpoint_theory(provider_entry['file_slot'])
                != receipt.get('checkpoint_theory')):
            raise ValueError('target-pristine provider identity mismatch')
        provider = _package(run_dir, run, provider_entry)
        if provider['package_payload_sha256'] != receipt.get('package_sha256'):
            raise ValueError('target-pristine provider package mismatch')
        provider_log = journal(run_dir, run, provider)
        provider_snapshot = provider_log.replay_snapshot()
        if (provider_snapshot.get('state', {}).get('status') != 'sealed'
                or len(provider_snapshot.get('records', ())) != 2
                or provider_snapshot['records'][0].get('event') != 'initial'
                or provider_snapshot['records'][1].get('event') != 'seal'):
            raise ValueError('target-pristine provider is not a replayed sealed reuse')
        inherited = read(provider_log.directory/'commits'/'000001.json')
        origin = inherited.get('evidence', {}).get('sealed_reuse', {})
        commits = origin.get('commits', [])
        if (not commits
                or commits[-1].get('commit_id') != receipt.get('source_commit_id')
                or commits[-1].get('state', {}).get('seal_id')
                != receipt.get('source_seal_id')):
            raise ValueError('target-pristine provider source receipt mismatch')

    target_dir = run_dir/'theories'/Path(entry['file_slot']).stem
    imports_path = _regular_file(target_dir/'imports.json', 'target frozen imports')
    if read(imports_path) != direct:
        raise ValueError('target-pristine target imports differ from projection')
    target_log = journal(run_dir, run, package)
    snapshot = target_log.replay_snapshot()
    state = snapshot.get('state', {})
    if (snapshot.get('commit_id') != provenance.get('initial_commit_id')
            or len(snapshot.get('records', ())) != 1
            or snapshot['records'][0].get('event') != 'initial'
            or digest(state) != provenance.get('initial_state_sha256')
            or state.get('status') != 'planned'
            or state.get('proofs') != {}
            or state.get('accepted_order') != []):
        raise ValueError('target-pristine target journal is not the pinned empty initial state')
    record = read(target_log.directory/'commits'/'000001.json')
    if set(record.get('evidence', {})) != {'check'}:
        raise ValueError('target-pristine target journal contains inherited history')
    forbidden = {'batch-requests', 'llm', 'session-transcript.jsonl'}
    paths = list(target_dir.rglob('*'))
    if (any(path.is_symlink() for path in paths)
            or any(forbidden & set(path.parts) for path in paths)):
        raise ValueError('target-pristine target contains request/transcript history')
    if provenance.get('accepted_proofs') != 0 or provenance.get('model_calls') != 0:
        raise ValueError('target-pristine zero-work counters changed')
    return provenance


def prepare(source_dir, destination, *, theory, target_contract_id,
            check_timeout=900, model='gpt-6-astra', reasoning_effort='medium'):
    """Build one new, native-checked target-pristine repository run."""
    source_dir = Path(source_dir).resolve()
    destination = Path(destination).resolve()
    if (not source_dir.is_dir() or not (source_dir/'run.json').is_file()):
        raise ValueError('source repository run is missing')
    if destination.exists() or destination.is_symlink():
        raise ValueError('destination already exists')
    if (destination == source_dir or destination.is_relative_to(source_dir)
            or source_dir.is_relative_to(destination)):
        raise ValueError('destination must be independent of the source run')
    if (not isinstance(check_timeout, int) or isinstance(check_timeout, bool)
            or check_timeout <= 0):
        raise ValueError('check_timeout must be a positive integer')
    overrides = _model_binding(model, reasoning_effort)
    source_run_sha256 = _sha256(source_dir/'run.json')
    source_input_sha256 = _sha256(source_dir/'execution-input.json')
    source = read(source_dir/'run.json')
    _source_layout(source_dir, source)
    data = read(source_dir/'execution-input.json')
    _execution_index(data)
    entry, package, data = _target_entry(
        source_dir, source, theory, data)
    fixed_ids = {row['contract_id'] for row in package['contracts']}
    if target_contract_id not in fixed_ids:
        raise ValueError('target is not a fixed contract of ' + theory)

    # This is the central non-leakage boundary: imports come from the immutable
    # projection.  Only those providers' verified terminal journals are read.
    imports, providers = _verified_provider_inputs(
        source_dir, source, package, data)
    input_root = Path(source.get('checker_context_root')
                      or source['environment_run'])
    ownership = None
    try:
        ownership = _claim_destination(destination)
        run = init_run(
            destination, input_root, destination.name,
            Path(source['session_schedule']['source']),
            **policy_binding(source), **log_policy_arguments(source),
            execution_models=overrides,
        )
        if any(run.get(key) != source.get(key)
               for key in CONTINUATION_ENV_FIELDS):
            raise ValueError('target-pristine environment differs from source')
        if run.get('execution_models') != overrides:
            raise ValueError('target-pristine execution models differ from requested binding')
        _reuse_sealed_theories(destination, run, source_dir, providers)
        progress(destination)

        checker = None
        active_error = None
        try:
            actual_run, actual_package, directory, journal, checker, ancestors = setup(
                destination, theory, check_timeout)
            if actual_run != run or actual_package != package:
                raise ValueError('initialized target package differs from source package')
            if ancestors != imports:
                raise ValueError('initialized target imports differ from projection')
            if journal.replay() is not None:
                raise ValueError('target theory unexpectedly has accepted history')

            from regen.execution.proof_batches import check_declarations
            from regen.planning.declaration_index import accepted_declarations, initial_index

            graph = initial_index(package,
                run_id=run['run_id'] + '--' + Path(package['file_slot']).stem)
            graph.update(initial_graph_owner='File Planner',
                runtime_graph_owner='File Planner',
                execution_mode='file-planner-batch-v1')
            graph = accepted_declarations(graph, [])
            check = freeze_evidence(check_declarations(checker, graph))
            if check.get('verdict') != 'green':
                raise RuntimeError('fixed-contract scaffold failed native elaboration: '
                                   + check.get('diagnostic', ''))
            state = {'graph': graph, 'proofs': {}, 'accepted_order': [],
                     'rounds': {}, 'revisions': {}, 'status': 'planned',
                     'seal_id': None}
            state = journal.append('initial', state, {'check': check})
            snapshot = journal.replay_snapshot()
        except BaseException as error:
            active_error = error
            raise
        finally:
            if checker is not None:
                try:
                    checker.close_prefix()
                except BaseException as cleanup_error:
                    if active_error is not None:
                        active_error.add_note('checker cleanup also failed: '
                            + type(cleanup_error).__name__ + ': ' + str(cleanup_error))
                    else:
                        raise

        if (state['proofs'] or state['accepted_order']
                or snapshot.get('state') != state
                or snapshot.get('commit_id') is None
                or len(snapshot.get('records', ())) != 1
                or snapshot['records'][0].get('event') != 'initial'):
            raise ValueError('target-pristine journal is not one empty initial commit')
        commits = sorted((journal.directory/'commits').glob('*.json'))
        if len(commits) != 1 or commits[0].name != '000001.json':
            raise ValueError('target-pristine journal contains unexpected history')
        initial_record = read(commits[0])
        if (initial_record.get('event') != 'initial'
                or set(initial_record.get('evidence', {})) != {'check'}
                or initial_record.get('state') != state):
            raise ValueError('target-pristine initial commit carries inherited history')

        result = progress(destination)
        target_rows = [row for row in result['files'] if row['theory'] == theory]
        if (len(target_rows) != 1 or target_rows[0].get('status') != 'planned'
                or target_rows[0].get('checked_contracts') != 0):
            raise ValueError('target-pristine progress contains accepted contracts')
        target_dir = destination/'theories'/Path(entry['file_slot']).stem
        target_files = list(target_dir.rglob('*'))
        forbidden = {'batch-requests', 'llm', 'session-transcript.jsonl'}
        if any(forbidden & set(path.parts) for path in target_files):
            raise ValueError('target-pristine output contains model/request history')
        if any(path.is_symlink() for path in target_files):
            raise ValueError('target-pristine output contains a symlink')
        published = destination/'generated'/(
            generated_checkpoint_theory(entry['file_slot'])+'.thy')
        if published.exists() or published.is_symlink():
            raise ValueError('target-pristine output published a target proof theory')
        if (_sha256(source_dir/'run.json') != source_run_sha256
                or _sha256(source_dir/'execution-input.json') != source_input_sha256):
            raise ValueError('source run changed during checkpoint preparation')

        provider_receipts = [{
            'theory': provider['theory'],
            'checkpoint_theory': generated_checkpoint_theory(provider['file_slot']),
            'package_sha256': provider['package_payload_sha256'],
            'source_commit_id': origin['commits'][-1]['commit_id'],
            'source_seal_id': origin['commits'][-1]['state']['seal_id'],
        } for provider, origin, _state in providers]
        provenance = {
            'schema': SCHEMA,
            'source_run': str(source_dir),
            'source_run_sha256': source_run_sha256,
            'source_execution_input_sha256': source_input_sha256,
            'destination_run': str(destination),
            'destination_run_sha256': _sha256(destination/'run.json'),
            'theory': theory,
            'target_contract_id': target_contract_id,
            'target_package_sha256': package['package_payload_sha256'],
            'scaffold_scope': 'full-fixed-theory',
            'fixed_contract_count': len(package['contracts']),
            'target_contract_is_selection_label_only': True,
            'derived_imports': imports,
            'reused_sealed_providers': provider_receipts,
            'reused_sealed_provider_count': len(providers),
            'initial_commit_id': snapshot['commit_id'],
            'initial_commit_sequence': len(snapshot['records']),
            'initial_state_sha256': digest(state),
            'model': model,
            'reasoning_effort': reasoning_effort,
            'execution_models': overrides,
            'model_calls': 0,
            'native_scaffold_checked': True,
            'accepted_proofs': 0,
            'target_history_read': False,
            'target_history_policy': 'source target theory directory is not an input',
            'historical_selection_only': True,
            'proving_clock_includes_preparation': False,
        }
        durable(destination/'target-pristine.json', provenance, immutable=True)
        (destination/_OWNERSHIP_MARKER).unlink()
        return provenance
    except BaseException as error:
        if ownership is not None:
            try:
                _cleanup_destination(destination, ownership)
            except BaseException as cleanup_error:
                error.add_note('checkpoint destination cleanup failed: '
                    + type(cleanup_error).__name__ + ': ' + str(cleanup_error))
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--theory', required=True)
    parser.add_argument('--target-contract-id', required=True)
    parser.add_argument('--check-timeout', type=int, default=900)
    parser.add_argument('--model', default='gpt-6-astra')
    parser.add_argument('--reasoning-effort', default='medium')
    args = parser.parse_args(argv)
    value = prepare(
        args.source, args.destination, theory=args.theory,
        target_contract_id=args.target_contract_id,
        check_timeout=args.check_timeout, model=args.model,
        reasoning_effort=args.reasoning_effort)
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
