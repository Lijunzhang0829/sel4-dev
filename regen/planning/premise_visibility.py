"""Read-only visibility descriptions from verified generated-proof journals.

Discovery is broader than the target's imports.  This module annotates those
references; it never imports a theory, promotes a pending declaration, or claims
that native fact lookup/application has occurred.
"""
from copy import deepcopy
from pathlib import Path

from regen.execution.checker_policy import exports_available
from regen.execution.import_revisions import effective_imports
from regen.execution.proof_commits import digest
from regen.execution.proof_session_runner import generated_checkpoint_theory


class GeneratedVisibility:
    """One response's immutable journal snapshots, not a persistent fact cache."""

    def __init__(self, workspace, target_theory=None):
        self.workspace = workspace
        self.target_theory = target_theory
        self.snapshots = {}
        self.providers = {}
        self.issues = []
        self.checkpoints = {generated_checkpoint_theory(entry['file_slot']): name
                            for name, entry in workspace.entries.items()}
        self.target = self.snapshot(target_theory) if target_theory is not None else None
        direct = self.imports(self.target) if self.target else []
        for checkpoint in direct:
            self._visit(checkpoint, self.target, (), 'verified')
        state = self.target['state'] if self.target else {}
        binding = {
            'target_theory': target_theory,
            'target_commit_id': self.target['commit_id'] if self.target else None,
            'context_revision': state.get('context_revision', 0) if self.target else None,
            'committed_imports': direct,
            'provider_versions': deepcopy(self.providers),
        }
        nodes = {node['id']: node for node in state.get('graph', {}).get('nodes', [])}
        prefix = [{'entry_id': key, 'node_sha256': digest(nodes[key]),
                   'proof_sha256': digest(state['proofs'][key])}
                  for key in state.get('accepted_order', []) if key in nodes and key in state.get('proofs', {})]
        environment = {key: value for key, value in binding.items() if key != 'target_commit_id'}
        environment['accepted_prefix'] = prefix
        self.context = {**binding, 'environment_id': digest(environment),
            'initialized': self.target is not None, 'issues': list(self.issues),
            'imports_source': 'accepted_journal', 'draft_imports_considered': False,
            'notice': 'This describes the accepted context, never pending draft imports. '
                      'The environment identity binds imports, provider versions and accepted prefix; '
                      'unrelated feedback commits do not change it. '
                      'No native fact availability or applicability check was run.'}

    def snapshot(self, theory):
        if theory not in self.snapshots:
            self.snapshots[theory] = self.workspace._snapshot(theory)
        return self.snapshots[theory]

    def imports(self, snapshot):
        path = Path(snapshot['directory']) / 'imports.json'
        initial = self.workspace._json(path) if path.exists() else []
        return effective_imports(snapshot['state'], initial)

    def _visit(self, checkpoint, consumer, active, inherited_status):
        if checkpoint in active:
            self.issues.append('cyclic_import:' + checkpoint)
            return
        owner = self.checkpoints.get(checkpoint)
        if owner is None:
            self.issues.append('unknown_import:' + checkpoint)
            return
        provider = self.snapshot(owner)
        if provider is None:
            self.providers[owner] = {'checkpoint': checkpoint, 'status': 'missing_provider',
                                     'commit_id': None, 'seal_id': None}
            return
        state = provider['state']
        expected = next((item for item in consumer['state'].get('context_provider_bindings', [])
                         if item.get('checkpoint') == checkpoint), None)
        status = 'verified' if expected is not None else 'unversioned'
        if not exports_available(state['status']) or not state.get('seal_id'):
            status = 'unsealed_provider'
        elif expected is not None and any(expected.get(key) != value for key, value in (
                ('theory', owner), ('commit_id', provider['commit_id']), ('seal_id', state['seal_id']))):
            status = 'provider_version_mismatch'
        elif inherited_status != 'verified':
            status = inherited_status
        summary = {'checkpoint': checkpoint, 'status': status,
                   'commit_id': provider['commit_id'], 'seal_id': state.get('seal_id'),
                   'expected_binding': deepcopy(expected)}
        previous = self.providers.get(owner)
        # Never upgrade an inconsistent path merely because another path matches.
        if previous and previous['status'] not in {'verified', 'unversioned'}:
            return
        self.providers[owner] = summary
        if status not in {'verified', 'unversioned'}:
            return
        for imported in self.imports(provider):
            self._visit(imported, provider, (*active, checkpoint), status)

    def describe(self, source_ref):
        """The source ref was validated by SharedProofWorkspace before this call."""
        owner = source_ref['theory']
        latest = self.snapshot(owner)
        state = latest['state'] if latest else {}
        nodes = {node['id']: node for node in state.get('graph', {}).get('nodes', [])}
        node = nodes.get(source_ref['node_id'])
        proof = state.get('proofs', {}).get(source_ref['node_id'])
        content_current = (node is not None and proof is not None
                           and digest(node) == source_ref['node_sha256']
                           and digest(proof) == source_ref['proof_sha256'])
        sealed = exports_available(state.get('status')) and bool(state.get('seal_id'))
        provider = self.providers.get(owner)
        if self.target_theory is None:
            status = 'target_not_bound'
        elif self.target is None:
            status = 'target_not_initialized'
        elif not content_current:
            status = 'historical_source_requires_recheck'
        elif owner == self.target_theory:
            status = 'current_theory_accepted_reference'
        elif provider is not None:
            status = {'verified': 'imported_provider_version_verified',
                      'unversioned': 'imported_provider_version_unbound'}.get(
                          provider['status'], provider['status'])
        elif sealed:
            status = 'sealed_requires_import'
        else:
            status = 'accepted_source_unsealed'
        return {
            'status': status, 'reference_only': True, 'is_proof_authority': False,
            'native_availability_checked': False, 'applicability_checked': False,
            'target_theory': self.target_theory, 'environment_id': self.context['environment_id'],
            'context_revision': self.context['context_revision'],
            'in_committed_import_closure': provider is not None,
            'needs_import': (owner != self.target_theory and provider is None)
                            if self.target is not None else None,
            'provider_exports_available': sealed,
            'provider_commit_id': latest['commit_id'] if latest else None,
            'provider_seal_id': state.get('seal_id'),
            'source_reference_commit_id': source_ref['commit_id'],
            'source_content_matches_current': content_current,
            'provider_binding': deepcopy(provider),
            'import_name': 'Generated_Refine.' + generated_checkpoint_theory(
                self.workspace.entries[owner]['file_slot']),
            'notice': 'Import membership and source certification are navigation evidence, '
                      'not a certificate that this fact resolves or applies in the target. '
                      'Unsealed material can be adapted only through fresh target checking.',
        }


def annotate_generated_result(workspace, result, target_theory=None):
    """Annotate a fresh response; do not mutate journal-derived index entries."""
    view = GeneratedVisibility(workspace, target_theory)
    output = deepcopy(result)
    output['target_context'] = deepcopy(view.context)
    for record in output.get('records', []):
        record['visibility'] = view.describe(record['source_ref'])
        record['reference_only'] = True
    return output
