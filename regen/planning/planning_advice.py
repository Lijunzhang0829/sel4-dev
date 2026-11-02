"""Read-only planning intentions, separate from positive proof authority.

Frozen suggestions are not dependencies, accepted facts, or import requests.
Runtime views consult only relevant fixed packages and existing certified journals;
they never initialize another theory's checker or reconcile mutable state.
"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace


ADVICE_INSTRUCTIONS = (
    'planning_advice contains optional, unverified proof directions. You may adopt, '
    'adjust, or abandon them. In document mode, read_entry for a fixed target expands '
    'its candidate declarations and current authority/visibility. Pending contracts '
    'are not facts; you may prove them earlier and then use them, including in one '
    'checked batch. Cross-theory facts require a certified export and effective '
    'imports. can_use_as_current_premise means certified visibility only, never '
    'mathematical applicability; native checking still decides. An unknown, refuted, '
    'or failing candidate must not force waiting, weaken a fixed target, or prevent '
    'choosing another route. No separate advice-review action is required.'
)

_INTENTS = ('purpose', 'grouping_reason', 'helper_decisions', 'auxiliary_intents',
            'reuse_suggestions', 'collaboration_groups')
_CONTRACT_DETAIL = ('name', 'exact_statement', 'contract_owned_context', 'attributes',
                    'contract_owned_attributes', 'contract_owned_attributes_raw',
                    'kind', 'context_profile_id')


def _premise_id(record):
    # Planning inspected database records use record_id; some runtime catalogs
    # expose the same identity as premise_id. Neither spelling confers authority.
    return record.get('record_id') or record.get('premise_id')


def static_advice(package, entry_id=None):
    """Return frozen advisory intentions, without claiming current availability."""
    fixed = {c['contract_id'] for c in package['contracts']}
    if entry_id is not None and entry_id not in fixed:
        return {}
    projection = package.get('repository_projection', {})
    suggestions = [deepcopy(item) for item in projection.get('proof_direction_suggestions', [])
                   if item['target_contract_id'] in fixed
                   and (entry_id is None or item['target_contract_id'] == entry_id)]
    intents = {key: deepcopy(projection[key]) for key in _INTENTS if projection.get(key)}
    if entry_id is not None:
        for key in ('helper_decisions', 'auxiliary_intents'):
            if key in intents:
                intents[key] = [item for item in intents[key]
                                if not item.get('for_contracts') or entry_id in item['for_contracts']]
                if not intents[key]:
                    del intents[key]
    if not suggestions and not intents:
        return {}
    result = {'advisory_only': True, 'mandatory': False, 'directions_verified': False,
              'availability_checked': False, 'native_check_required': True,
              'notice': ADVICE_INSTRUCTIONS, 'intents': intents,
              'proof_direction_suggestions': suggestions}
    if entry_id is not None:
        contract_ids = {key for item in suggestions for key in item['candidate_contract_ids']}
        premise_ids = {key for item in suggestions for key in item['candidate_premise_ids']}
        result['candidate_contract_references'] = [deepcopy(item) for item in
            projection.get('proof_direction_contracts', []) if item['contract_id'] in contract_ids]
        result['candidate_premise_references'] = [deepcopy(item) for item in
            projection.get('proof_direction_premises', []) if _premise_id(item) in premise_ids]
    return result


def _draft_prefix(store, state, source=None):
    from regen.planning.theory_document import baseline, candidate, source_imports
    source = store.path.read_text() if source is None else source
    try:
        imports = source_imports(source, store.package['theory'])
        proposed = candidate(store.package, state, source, imports)
        retained = baseline(state, proposed)[0]['proofs'] if imports == store.checked_imports else {}
        return {'retained': set(retained),
                'order': proposed['graph']['document_order'],
                'imports_pending': imports != store.checked_imports}
    except ValueError as error:
        return {'retained': set(), 'order': [], 'imports_pending': None,
                'draft_error': str(error)}


def _dispositions(controller, package, state):
    # These saved readers validate existing receipts without recovery or writes.
    from regen.planning.theory_outcomes import (
        _saved_dispositions, _saved_outcome, contract_dispositions_enabled)
    reader = SimpleNamespace(root=controller.root, run=controller.run,
                             theory=package['theory'], _state=lambda _: (package, state))
    if contract_dispositions_enabled(controller.run):
        saved = _saved_dispositions(reader, package['theory'])
        if 'context_revision' in state:
            from regen.execution.import_revisions import native_imports
            from regen.planning.theory_outcomes import validate_dispositions
            validate_dispositions(package, state, saved,
                expected_imports=native_imports(controller.run, state['context_imports']))
        return saved
    outcome = _saved_outcome(reader, package['theory'])
    return {key: outcome for key in outcome['entry_ids']} if outcome else {}


class _References:
    """Per-read caches, bounded to candidates and their checked import paths."""
    def __init__(self, controller, store, state):
        self.controller, self.store, self.state = controller, store, state
        self.shared = controller.shared
        self.package = store.package
        self.theory = self.package['theory']
        self.packages = {self.theory: self.package}
        self.snapshots, self.dispositions = {}, {}
        self.registry = self.shared.entries
        self.fixed = set(self.shared.fixed_contract_ids)
        self.owners = {c['contract_id']: self.theory for c in self.package['contracts']}
        for theory, entry in self.registry.items():
            for key in entry.get('contract_ids', []):
                self.owners[key] = theory
        self.hints = {item['contract_id']: item.get('provider_theory') for item in
                      self.package.get('repository_projection', {}).get('proof_direction_contracts', [])}
        self.visible = None
        self.draft = None
        from regen.planning.theory_document import source_imports
        self.source = store.path.read_text()
        self.draft_error = None
        try:
            self.imports_pending = source_imports(self.source, self.theory) != store.checked_imports
        except ValueError as error:
            self.imports_pending = None
            self.draft_error = str(error)

    def package_for(self, theory):
        if theory not in self.packages:
            self.packages[theory] = self.shared._package(theory)
        return self.packages[theory]

    def resolve(self, key):
        if key not in self.fixed:
            return None, None
        owner = self.owners.get(key) or self.hints.get(key)
        if owner not in self.registry:
            return None, None
        package = self.package_for(owner)
        contract = next((c for c in package['contracts'] if c['contract_id'] == key), None)
        return (owner, contract) if contract is not None else (None, None)

    def snapshot(self, owner):
        if owner not in self.snapshots:
            self.snapshots[owner] = self.shared._snapshot(owner)
        return self.snapshots[owner]

    def stopped(self, owner, state):
        if owner not in self.dispositions:
            self.dispositions[owner] = _dispositions(
                self.controller, self.package_for(owner), state)
        return self.dispositions[owner]

    def imported_owners(self):
        if self.visible is not None:
            return self.visible
        from regen.execution.checker_policy import exports_available
        from regen.execution.import_revisions import effective_imports
        from regen.execution.proof_session_runner import generated_checkpoint_theory
        by_checkpoint = {generated_checkpoint_theory(row['file_slot']): name
                         for name, row in self.registry.items()}
        pending = [name[len('Generated_Refine.'):] for name in self.store.checked_imports
                   if name.startswith('Generated_Refine.')]
        seen, self.visible = set(), set()
        while pending:
            checkpoint = pending.pop()
            if checkpoint in seen:
                continue
            seen.add(checkpoint)
            owner = by_checkpoint.get(checkpoint)
            if owner is None or owner == self.theory:
                continue
            snapshot = self.snapshot(owner)
            if snapshot is None or not exports_available(snapshot['state']['status']):
                continue
            self.visible.add(owner)
            state = snapshot['state']
            initial = ([] if 'context_imports' in state else
                       self.shared._json(Path(snapshot['directory']) / 'imports.json'))
            pending.extend(effective_imports(state, initial))
        return self.visible

    def contract_view(self, key, target, *, full):
        from regen.execution.checker_policy import exports_available
        from regen.execution.proof_session_runner import generated_checkpoint_theory
        row = {'contract_id': key, 'owner_theory': None,
               'authority_status': 'unresolved_reference', 'visibility_status': 'unavailable',
               'can_use_as_current_premise': False, 'applicability_verified': False,
               'native_check_required': True}
        owner, contract = self.resolve(key)
        if contract is None:
            row['reason'] = 'Candidate could not be located in the current run fixed contracts.'
            return row
        row.update(owner_theory=owner, name=contract['name'])
        if full:
            row.update({field: deepcopy(contract[field]) for field in _CONTRACT_DETAIL if field in contract})
        if owner == self.theory:
            state, snapshot = self.state, None
        else:
            snapshot = self.snapshot(owner)
            state = snapshot['state'] if snapshot else None
            row['import_name'] = 'Generated_Refine.' + generated_checkpoint_theory(self.package_for(owner)['file_slot'])
            row['canonical_name'] = generated_checkpoint_theory(self.package_for(owner)['file_slot']) + '.' + contract['name']
            if snapshot:
                row['commit_id'] = snapshot['commit_id']
                row['seal_id'] = state.get('seal_id')
        proved = state is not None and key in state.get('proofs', {})
        disposition = self.stopped(owner, state).get(key) if state is not None and not proved else None
        if disposition:
            row.update(authority_status=disposition['status'], visibility_status='unavailable',
                       reason=disposition['reason'])
        elif not proved:
            row.update(authority_status='pending', visibility_status='not_proved',
                       reason='Not an accepted positive fact. Prove it first, then use it; another route remains allowed.')
        elif owner == self.theory:
            row['authority_status'] = 'local_green'
            if self.draft is None:
                self.draft = _draft_prefix(self.store, self.state, self.source)
            order = self.draft['order']
            if key == target:
                row.update(visibility_status='self_target', reason='A target cannot justify itself.')
            elif key not in self.draft['retained'] or target not in order:
                row.update(visibility_status='local_recheck_required',
                           reason='The current draft/imports no longer retain this certified prefix; recheck is required.')
            elif order.index(key) >= order.index(target):
                row.update(visibility_status='local_after_target',
                           reason='The accepted declaration must precede the target in the checked document.')
            else:
                row.update(visibility_status='local_current_prefix', can_use_as_current_premise=True,
                           reason='Accepted in the unchanged current prefix before the target; applicability remains unchecked.')
        elif not exports_available(state['status']):
            row.update(authority_status='unsealed_green', visibility_status='unsealed_reference',
                       reason='Accepted in another theory but not certified for export; reference only.')
        elif owner not in self.imported_owners():
            row.update(authority_status='sealed_export', visibility_status='sealed_not_imported',
                       reason='Certified export exists, but current checked imports do not expose it.')
        else:
            row.update(authority_status='sealed_export', visibility_status='checked_import_visible',
                       can_use_as_current_premise=True,
                       reason='Certified export visible through checked imports; applicability remains unchecked.')
            if self.imports_pending is not False:
                row.update(visibility_status='checked_import_visible_draft_pending',
                           can_use_as_current_premise=False,
                           reason='Visible in the checked context, but draft import changes require native rechecking first.')
        return row


def planning_advice(controller, store, state, entry_id=None):
    """Attach current read-only authority to optional repository suggestions.

    The theory-wide view stays compact. A fixed target's entry view additionally
    expands declarations from the authoritative current-run packages, not model
    text or potentially stale reference copies.
    """
    result = static_advice(store.package, entry_id)
    if not result:
        return {}
    result.pop('candidate_contract_references', None)
    result.pop('candidate_premise_references', None)
    directions = result['proof_direction_suggestions']
    if not directions:
        return result
    references = _References(controller, store, state)
    projection = store.package.get('repository_projection', {})
    premises = {_premise_id(item): item for item in [
        *projection.get('legal_premises', []), *projection.get('proof_direction_premises', [])]}
    for direction in directions:
        direction['candidate_contracts'] = [references.contract_view(
            key, direction['target_contract_id'], full=entry_id is not None)
            for key in direction['candidate_contract_ids']]
        candidates = []
        for key in direction['candidate_premise_ids']:
            record = premises.get(key)
            row = {'premise_id': key, 'authority_status': 'catalog_candidate' if record else 'unresolved_reference',
                   'visibility_status': 'requires_context_check', 'can_use_as_current_premise': False,
                   'applicability_verified': False, 'native_check_required': True,
                   'reason': 'A planning-time inspected record is a reference; check current availability and applicability.'}
            if record:
                fields = record if entry_id is not None else {
                    field: record[field] for field in ('canonical_name', 'source_theory', 'source_class') if field in record}
                row['record'] = deepcopy(fields)
            candidates.append(row)
        direction['candidate_premises'] = candidates
    result.update(availability_checked=True, checked_imports=list(store.checked_imports),
                  context_revision=state.get('context_revision', 0),
                  imports_pending=references.imports_pending)
    if references.draft_error:
        result['draft_error'] = references.draft_error
    if references.draft is not None:
        result['imports_pending'] = references.draft['imports_pending']
        if 'draft_error' in references.draft:
            result['draft_error'] = references.draft['draft_error']
    return result
