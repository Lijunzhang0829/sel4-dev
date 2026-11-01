"""Bind a completed contract disposition set to a positive-only export.

The journal keeps every original declaration.  Only this native-check projection
omits unproved declarations; neither refutations nor UNKNOWN reports are facts.
"""
from copy import deepcopy

from regen.execution.proof_commits import digest

POLICY = 'contract-dispositions-v1'
STATUS = 'exported_partial'


def export_graph(state):
    """Deterministic native/publication view of the accepted positive proofs."""
    graph, proofs = state['graph'], state['proofs']
    nodes = graph['nodes']
    by_id = {node['id']: node for node in nodes}
    if len(by_id) != len(nodes) or not set(proofs) <= set(by_id):
        raise ValueError('partial export contains foreign or duplicate proof IDs')
    accepted = state['accepted_order']
    if (len(accepted) != len(proofs) or set(accepted) != set(proofs)
            or len(set(accepted)) != len(accepted)):
        raise ValueError('partial export accepted order must cover every positive proof')
    order = graph['execution_order']
    if len(order) != len(nodes) or set(order) != set(by_id):
        raise ValueError('partial export source order must cover the full declaration set')
    for key in proofs:
        if not set(by_id[key].get('dependencies', [])) <= set(proofs):
            raise ValueError('accepted proof retains an unproved prerequisite: ' + key)
    projected = deepcopy(graph)
    projected['nodes'] = [deepcopy(node) for node in nodes if node['id'] in proofs]
    for node in projected['nodes']:
        node['consumers'] = [key for key in node.get('consumers', []) if key in proofs]
    projected['execution_order'] = [key for key in order if key in proofs]
    if 'document_order' in projected:
        projected['document_order'] = [key for key in graph['document_order'] if key in proofs]
    projected['accepted_order'] = list(accepted)
    if 'contract_routes' in projected:
        projected['contract_routes'] = {key: value for key, value in projected['contract_routes'].items()
                                         if key in proofs}
    # An empty checked provider is useful when every fixed target was refuted or
    # left UNKNOWN.  This marker permits its empty native dependency audit only.
    projected['partial_export_projection'] = POLICY
    from regen.planning.declaration_index import index_payload_sha256
    projected['graph_payload_sha256'] = index_payload_sha256(projected)
    return projected


def _fixed_contracts(package, state):
    contracts = package.get('contracts')
    if not isinstance(contracts, list):
        raise ValueError('partial export must retain the full fixed contract list')
    fixed = {c['contract_id']: c for c in contracts}
    nodes = {node['id']: node for node in state['graph']['nodes'] if node.get('role') == 'contract'}
    if len(fixed) != len(contracts) or set(fixed) != set(nodes):
        raise ValueError('partial export fixed contract coverage mismatch')
    for key, contract in fixed.items():
        node = nodes[key]
        if (node.get('statement') != contract.get('exact_statement')
                or node.get('context', []) != contract.get('contract_owned_context', [])
                or node.get('name') != contract.get('name')):
            raise ValueError('partial export changed a fixed contract: ' + key)
    return fixed


def build_export_scope(package, state, dispositions, *, runtime=None, expected_imports=None):
    if runtime is not None and runtime.get('contract_outcome_policy') != POLICY:
        raise ValueError('partial export requires the frozen contract disposition policy')
    fixed = _fixed_contracts(package, state)
    if package.get('theory') != state['graph']['theory'] or package.get('file_slot') != state['graph']['file_slot']:
        raise ValueError('partial export package differs from the full theory state')
    if not isinstance(dispositions, dict) or set(dispositions) != set(fixed) - set(state['proofs']):
        raise ValueError('partial export requires a disposition for every unproved fixed contract')
    if not dispositions:
        raise ValueError('partial export needs at least one unproved fixed contract')
    expected_binding = None
    if runtime is not None and 'run_id' in runtime:
        expected_binding = {'run_id': runtime['run_id'], 'run_sha256': digest(runtime),
                            'theory': package['theory'], 'package_sha256': digest(package)}
    from regen.planning.theory_outcomes import validate_dispositions
    validate_dispositions(package, state, dispositions,
                          expected_imports=expected_imports, expected_binding=expected_binding)
    projected = export_graph(state)
    return {'policy': POLICY, 'package': deepcopy(package),
            'dispositions': deepcopy(dispositions),
            'exported_node_ids': sorted(state['proofs']),
            'omitted_node_ids': sorted(set(n['id'] for n in state['graph']['nodes']) - set(state['proofs'])),
            'export_graph_sha256': digest(projected)}


def validate_export_scope(state, scope, *, runtime=None, expected_imports=None, expected_package=None):
    if not isinstance(scope, dict) or not isinstance(scope.get('package'), dict):
        raise ValueError('partial export lacks its original package')
    if expected_package is not None and scope['package'] != expected_package:
        raise ValueError('partial export package differs from frozen journal package')
    expected = build_export_scope(scope['package'], state, scope.get('dispositions'),
                                  runtime=runtime, expected_imports=expected_imports)
    if scope != expected:
        raise ValueError('partial export scope does not bind the full target set and exported proofs')
    if state.get('contract_dispositions') != scope['dispositions']:
        raise ValueError('partial export state dispositions differ from its certificate')
    return export_graph(state)
