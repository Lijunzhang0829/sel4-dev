"""Document declaration validation, independent of proof-DAG planning.

The journal/checker wire format still calls this index `graph` and entries `nodes`.
Empty edge fields are a compatibility encoding, never a scheduling relation.
"""
from copy import deepcopy
import re

DECLARATION = re.compile(r"^\s*(?:lemma|theorem|corollary|proposition)\s+([A-Za-z0-9_'.]+)")


def index_payload_sha256(index):
    from regen.execution.proof_commits import digest
    return digest({k:v for k,v in index.items() if k != 'graph_payload_sha256'})


def accepted_declarations(index, order):
    """Bind ordered accepted entries to the shared checker/journal encoding."""
    result = deepcopy(index)
    result['context_policy'] = 'committed-prefix-v1'
    result['accepted_order'] = list(order)
    result['graph_payload_sha256'] = index_payload_sha256(result)
    return result


def document_order(index):
    nodes = index['nodes']
    order = index['document_order']
    if (not isinstance(order, list) or not all(isinstance(k, str) for k in order)
            or len(order) != len(nodes) or set(order) != {n['id'] for n in nodes}
            or any(n.get('dependencies') or n.get('consumers') for n in nodes)):
        raise ValueError('document order must cover all declarations once, without planned proof dependencies or consumers')
    return list(order)


def validate_index(index, package):
    from regen.execution.proof_batches import _safe_text
    nodes = index.get('nodes')
    if not isinstance(nodes, list):
        raise ValueError('document declarations must be a list')
    ids = [n.get('id') for n in nodes]
    names = [n.get('name') for n in nodes]
    for values in (ids, names):
        if any(not isinstance(v, str) or not v for v in values) or len(set(values)) != len(values):
            raise ValueError('document declaration IDs and names must be nonempty and unique')
    fixed = {c['contract_id']: c for c in package['contracts']}
    actual = {n['id']: n for n in nodes if n.get('role') == 'contract'}
    if set(actual) != set(fixed):
        raise ValueError('document must contain exactly the fixed contracts')
    contexts = {tuple(c.get('contract_owned_context', [])) for c in
                [*package['contracts'], *package.get('support_contracts', [])]} | {()}
    for node in nodes:
        if node.get('role') not in {'contract', 'helper', 'theorem'}:
            raise ValueError('invalid declaration role')
        statement = node.get('statement')
        match = DECLARATION.match(statement) if isinstance(statement, str) else None
        if not match or match[1] != node['name']:
            raise ValueError('declaration statement/name mismatch')
        if tuple(node.get('context', [])) not in contexts:
            raise ValueError('unapproved declaration context')
        if any(k in node for k in ('proof', 'proof_body', 'premises')):
            raise ValueError('proofs belong to the source version, not the declaration index')
        _safe_text(statement, statement=True)
    for key, contract in fixed.items():
        node = actual[key]
        if (node['name'] != contract['name'] or node['statement'] != contract['exact_statement']
                or node.get('context', []) != contract.get('contract_owned_context', [])):
            raise ValueError('fixed contract changed: ' + key)
    if index.get('contract_routes'):
        raise ValueError('document declarations do not carry contract routes')
    order = document_order(index)
    if 'execution_order' in index and index['execution_order'] != order:
        raise ValueError('document execution order differs from source order')
    index['statement_certification'] = {'policy': 'native-document-check'}


def build_index(package, declarations, *, run_id, revision):
    nodes = [dict(deepcopy(n), dependencies=[], consumers=[], strategy='') for n in declarations]
    index = {'schema_version': 1, 'run_id': run_id, 'theory': package['theory'],
             'file_slot': package['file_slot'], 'revision': revision, 'nodes': nodes,
             'document_order': [n['id'] for n in nodes], 'declaration_index_version': 1}
    validate_index(index, package)
    index['execution_order'] = document_order(index)
    return index


def initial_index(package, *, run_id, revision=1):
    return build_index(package, [dict(id=c['contract_id'], name=c['name'], role='contract',
        statement=c['exact_statement'], context=c.get('contract_owned_context', []))
        for c in package['contracts']], run_id=run_id, revision=revision)


# Compatibility import for clients of the first source-workspace implementation.
from regen.planning.document_interface import public_view
