"""Repository-wide discovery from sealed projections, verified against journals.

The catalog generates candidates only. Selected candidates are checked against
the current certified provider before being returned. Discovery never imports a
fact or makes an unproved contract a premise.
"""
from pathlib import Path

from regen.execution.proof_commits import digest, durable

POLICY = 'repository-sealed-discovery-v1'


def _source(run, request):
    if (not any(key in request for key in ('goal_query', 'proof_state_query'))
            or request.get('scope', 'all') == 'upstream'
            or run.get('execution_mode') != 'repository-projection-v1'):
        return None
    packages = Path(run['contract_package_dir']).resolve()
    root = packages.parent
    from regen.infrastructure.incremental_log import read_artifact
    if packages != root/'packages' or read_artifact(root/'run.json') != run:
        raise ValueError('generated discovery differs from frozen repository run')
    path = root/'generated-premises.json'
    value = read_artifact(path)
    if not isinstance(value, dict) or not isinstance(value.get('records'), list):
        raise ValueError('invalid generated discovery projection')
    return root, value


def discovery_binding(run, request):
    source = _source(run, request)
    if source is None:
        return None
    return {'policy':POLICY, 'catalog_sha256':digest(source[1])}


class GeneratedDiscovery:
    def __init__(self, root, value, package, directory):
        from regen.execution.shared_proof_workspace import SharedProofWorkspace
        from regen.planning.premise_visibility import GeneratedVisibility
        self.workspace = SharedProofWorkspace(root)
        self.visibility = GeneratedVisibility(self.workspace, package['theory'])
        self.binding = {'policy':POLICY, 'catalog_sha256':digest(value)}
        # A concurrent seal may replace the mutable projection after this read.
        # Query identity, candidate indexing and ranking use this exact version.
        self.catalog = root/'generated-discovery-catalogs'/(
            self.binding['catalog_sha256']+'.json')
        durable(self.catalog, value, immutable=True)

    def resolve(self, record):
        from regen.execution.checker_policy import exports_available
        from regen.execution.proof_session_runner import generated_checkpoint_theory
        owner = record.get('owner_theory')
        if owner not in self.workspace.entries:
            return None
        snapshot = self.visibility.snapshot(owner)
        if snapshot is None:
            return None
        state = snapshot['state']
        if (not exports_available(state['status']) or not state.get('seal_id')
                or record.get('theory_seal_id') != state['seal_id']):
            return None
        checkpoint = generated_checkpoint_theory(self.workspace.entries[owner]['file_slot'])
        node = next((n for n in state['graph']['nodes']
                     if record.get('canonical_name') == checkpoint+'.'+n['name']), None)
        if (node is None or node['id'] not in state['proofs']
                or record.get('record_id') != f"generated:{owner}:{node['name']}"
                or record.get('exact_statement') != node['statement']
                or record.get('context', []) != node.get('context', [])):
            return None
        if 'proof_body' in record and (record['proof_body'] != state['proofs'][node['id']]
                or record.get('proof_sha256') != digest(record['proof_body'])):
            return None
        source_ref = self.workspace._ref(snapshot, node)
        visibility = self.visibility.describe(source_ref)
        visible = visibility['status'] in {
            'imported_provider_version_verified', 'imported_provider_version_unbound'}
        # Own current-prefix proofs are already available through read_evidence;
        # this external discovery path must not suggest importing the target.
        if owner == self.visibility.target_theory:
            return None
        return {**record, 'source_ref':source_ref, 'visibility':visibility,
                'provider_theory':owner, 'provider_commit_id':snapshot['commit_id'],
                'provider_seal_id':state['seal_id'], 'import_name':visibility['import_name'],
                'requires_import':visibility['needs_import'],
                'can_use_as_current_premise':visible,
                'availability':('checked_import_candidate_requires_native_check' if visible else
                                'sealed_requires_import' if visibility['needs_import'] else
                                visibility['status']),
                'is_proof_authority':False, 'applicability_verified':False}


def prepare_discovery(run, package, directory, request):
    source = _source(run, request)
    return None if source is None else GeneratedDiscovery(*source, package, directory)
