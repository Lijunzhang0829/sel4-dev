"""Versioned generated imports; the initial journal binding remains immutable.

Draft edits are not context changes. Only a successful native semantic check can
commit a new context, at which point old proofs remain in history/source but no
longer form the current certified prefix.
"""
from copy import deepcopy
from pathlib import Path

from regen.execution.proof_commits import digest, durable
from regen.execution.checker_policy import exports_available, theory_finished


CONTEXT_FIELDS = ('context_imports', 'context_revision', 'context_provider_bindings')
CONTEXT_QUERY_FIELDS = ('batch_facts', 'batch_symbols', 'batch_symbol_observations',
    'observation_queries', 'observation_selection_versions', 'batch_requested_fact_ids')


def revised_state(old, ancestors, revision, summaries):
    """Keep scripts/history in their original authorities, retire current caches."""
    new = deepcopy(old)
    new.update(context_imports=list(ancestors), context_revision=revision,
               context_provider_bindings=deepcopy(summaries), proofs={}, accepted_order=[])
    new['graph']['accepted_order'] = []
    from regen.planning.declaration_index import index_payload_sha256
    new['graph']['graph_payload_sha256'] = index_payload_sha256(new['graph'])
    new.pop('document_source_sha256', None)
    for field in CONTEXT_QUERY_FIELDS:
        new.pop(field, None)
    return new


def effective_imports(state, initial):
    imports = (state or {}).get('context_imports', initial)
    if (not isinstance(imports, list) or any(not isinstance(x, str) or not x for x in imports)
            or len(imports) != len(set(imports))):
        raise ValueError('invalid effective imports')
    return list(imports)


def native_imports(run, ancestors):
    return ['Generated_Refine.' + name for name in ancestors] if ancestors else [run['base_import']]


def checkpoint_imports(run, source_imports):
    if (not isinstance(source_imports, list) or not source_imports
            or any(not isinstance(x, str) or not x for x in source_imports)
            or len(source_imports) != len(set(source_imports))):
        raise ValueError('imports must be a nonempty list of distinct theory names')
    if source_imports == [run['base_import']]:
        return []
    prefix = 'Generated_Refine.'
    if any(not name.startswith(prefix) or not name[len(prefix):] for name in source_imports):
        raise ValueError('use certified Generated_Refine providers, or the base import alone')
    return [name[len(prefix):] for name in source_imports]


def provider_summary(binding):
    terminal = binding['origin']['commits'][-1]
    return {'checkpoint': binding['checkpoint'], 'theory': binding['package']['theory'],
            'seal_id': terminal['state']['seal_id'], 'commit_id': terminal['commit_id']}


def validate_provider_binding(binding, runtime):
    """Replay an embedded provider chain, including partial certified exports."""
    from regen.execution.repository_execution import validator
    from regen.execution.checker_policy import policy_binding, contract_policy_binding
    from regen.execution.proof_session_runner import generated_checkpoint_theory
    if not isinstance(binding, dict) or set(binding) != {'checkpoint', 'package', 'origin'}:
        raise ValueError('invalid import provider binding')
    package, origin = binding['package'], binding['origin']
    if not isinstance(origin, dict) or set(origin) != {'run', 'imports', 'commits'}:
        raise ValueError('invalid import provider origin')
    source = origin['run']
    if source != runtime:
        raise ValueError('import provider belongs to a different frozen run')
    if binding['checkpoint'] != generated_checkpoint_theory(package['file_slot']):
        raise ValueError('import provider checkpoint mismatch')
    body = {key: value for key, value in package.items() if key != 'package_payload_sha256'}
    if digest(body) != package.get('package_payload_sha256'):
        raise ValueError('import provider package digest mismatch')
    expected = {**source['session_schedule'], **policy_binding(source), **contract_policy_binding(source),
        'run_id': source['run_id'], 'implementation': digest(source['implementation']),
        'package_sha256': package['package_payload_sha256'], 'imports': origin['imports']}
    records = origin['commits']
    if not isinstance(records, list) or not records:
        raise ValueError('import provider lacks accepted history')
    previous = None
    state = None
    check = validator(package, source, origin['imports'])
    for number, record in enumerate(records, 1):
        payload = {key: value for key, value in record.items() if key != 'commit_id'}
        if (record['sequence'] != number or record['parent'] != previous
                or record['binding'] != expected or record['commit_id'] != digest(payload)):
            raise ValueError('invalid import provider commit chain')
        check(state, record['state'], record['event'], record['evidence'])
        state = record['state']
        previous = record['commit_id']
    if not exports_available(state['status']) or records[-1]['event'] != 'seal':
        raise ValueError('import provider has no certified exports')
    return effective_imports(state, origin['imports'])


def validate_import_event(package, runtime, initial, old, new, evidence):
    if old is None or runtime is None or theory_finished(old['status']):
        raise ValueError('imports may change only in an active theory')
    if old['graph'].get('declaration_index_version') != 1:
        raise ValueError('imports revisions require the document interface')
    before = effective_imports(old, initial)
    after = effective_imports(new, initial)
    revision = old.get('context_revision', 0) + 1
    if before == after or type(new.get('context_revision')) is not int or new.get('context_revision') != revision:
        raise ValueError('imports revision must change the context exactly once')
    if (evidence.get('previous_imports') != before or evidence.get('context_imports') != after
            or evidence.get('context_revision') != revision
            or evidence.get('imports') != native_imports(runtime, after)):
        raise ValueError('imports event context binding mismatch')
    providers = evidence.get('provider_bindings')
    if not isinstance(providers, list) or [p.get('checkpoint') for p in providers] != after:
        raise ValueError('imports event provider coverage mismatch')
    from regen.execution.proof_session_runner import generated_checkpoint_theory
    consumer = generated_checkpoint_theory(package['file_slot'])
    if consumer in after:
        raise ValueError('theory cannot import itself')
    for provider in providers:
        ancestors = validate_provider_binding(provider, runtime)
        # A newly importing consumer is unpublished; no accepted provider may
        # have already imported it, even via its certified ancestor closure.
        def contains_target(item):
            if item['checkpoint'] == consumer:
                return True
            for record in item['origin']['commits']:
                if consumer in effective_imports(record['state'], item['origin']['imports']):
                    return True
            return False
        if consumer in ancestors or contains_target(provider):
            raise ValueError('cyclic imports revision')
    summaries = [provider_summary(p) for p in providers]
    if new.get('context_provider_bindings') != summaries:
        raise ValueError('imports event provider versions mismatch')
    from regen.execution.import_semantics import validate_import_semantics
    semantic = evidence.get('semantic_check')
    validate_import_semantics(semantic, package, native_imports(runtime, before), native_imports(runtime, after))
    if evidence.get('check') != semantic['after']['check']:
        raise ValueError('imports check differs from semantic receipt')
    expected = revised_state(old, after, revision, summaries)
    if new != expected:
        raise ValueError('imports revision changed proofs, declarations or charged history')


def _provider_bindings(root, run, ancestors):
    from regen.execution.repository_execution import read, package_for, journal
    from regen.execution.proof_session_runner import generated_checkpoint_theory
    entries = {generated_checkpoint_theory(e['file_slot']): e
               for e in read(root / 'execution-input.json')['files']}
    bindings = []
    for name in ancestors:
        if name not in entries:
            raise ValueError('unknown generated import provider: ' + name)
        entry = entries[name]
        package = package_for(run, entry['file_slot'])
        state = journal(root, run, package).replay(repair=False)
        if state is None or not exports_available(state['status']):
            raise ValueError('import provider has no certified exports: ' + entry['theory'])
        directory = root / 'theories' / Path(package['file_slot']).stem
        origin = {'run': run, 'imports': read(directory / 'imports.json'),
                  'commits': [read(path) for path in sorted((directory / 'acceptance/commits').glob('*.json'))]}
        binding = {'checkpoint': name, 'package': package, 'origin': origin}
        validate_provider_binding(binding, run)
        bindings.append(binding)
    return bindings


def _guard_refuted_context(root, run, package, state):
    from types import SimpleNamespace
    from regen.planning.theory_outcomes import current_dispositions, current_outcome
    view = SimpleNamespace(root=root, run=run, theory=package['theory'],
                           _state=lambda _: (package, state))
    dispositions = current_dispositions(view)
    outcome = current_outcome(view)
    if (any(row['status'] in {'refuted', 'context_refuted'} for row in dispositions.values())
            or outcome is not None and outcome['status'] == 'refuted'):
        raise ValueError('imports cannot change after an accepted native refutation; '
                         'retain its certified environment (refutation recertification is not supported)')


def prepare_import_change(run_dir, theory, source_imports, timeout):
    from regen.execution.repository_execution import read, package_for, journal, setup
    from regen.execution.proof_session_runner import generated_checkpoint_theory
    root = Path(run_dir)
    run = read(root / 'run.json')
    entry = next(e for e in read(root / 'execution-input.json')['files'] if e['theory'] == theory)
    package = package_for(run, entry['file_slot'])
    log = journal(root, run, package)
    old = log.replay(repair=False)
    if old is None or theory_finished(old['status']):
        raise ValueError('imports revisions require an active initialized theory')
    if old['graph'].get('declaration_index_version') != 1:
        raise ValueError('imports revisions require the document interface')
    ancestors = checkpoint_imports(run, source_imports)
    if generated_checkpoint_theory(entry['file_slot']) in ancestors:
        raise ValueError('theory cannot import itself')
    before = effective_imports(old, log.binding['imports'])
    if ancestors != before:
        _guard_refuted_context(root, run, package, old)
    providers = _provider_bindings(root, run, ancestors)
    runtime = setup(root, theory, timeout, context_imports=ancestors)
    return {'runtime': runtime, 'provider_bindings': providers,
            'old_context_imports': before, 'old_context_revision': old.get('context_revision', 0),
            'changed': ancestors != before}


def commit_import_change(runtime, old, native_evidence, provider_bindings):
    run, package, directory, log, checker, ancestors = runtime
    if log.replay(repair=False) != old:
        raise ValueError('theory changed while preparing imports')
    before = effective_imports(old, log.binding['imports'])
    if ancestors != before:
        _guard_refuted_context(directory.parent.parent, run, package, old)
    revision = old.get('context_revision', 0) + 1
    new = revised_state(old, ancestors, revision, [provider_summary(p) for p in provider_bindings])
    evidence = {**native_evidence, 'previous_imports': before, 'context_imports': list(ancestors),
                'context_revision': revision, 'imports': native_imports(run, ancestors),
                'provider_bindings': provider_bindings}
    accepted = log.append('imports', new, evidence)
    # Publish the rebuildable recall projection only after context acceptance.
    # setup() repairs this projection on recovery if this write is interrupted.
    from regen.execution.repository_execution import read
    prepared = directory/'contexts'/digest({'imports': list(ancestors)})/'sealed-premises.json'
    durable(directory/'sealed-premises.json', read(prepared))
    return accepted
