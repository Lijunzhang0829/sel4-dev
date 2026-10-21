"""One ordered same-theory context shared by prompt, checker and commit evidence."""
from regen.execution.proof_commits import digest


def visible_ids(graph, proofs, target):
    if graph.get('context_policy') != 'committed-prefix-v1':
        from regen.execution.isabelle_node_checker import dependency_closure
        return [key for key in graph['execution_order'] if key in dependency_closure(graph, target)]
    order = graph.get('accepted_order', [])
    # Rechecking an already accepted node uses its original prefix only.
    if target in order: order = order[:order.index(target)]
    if set(proofs) - set(graph.get('accepted_order', [])) - {target}:
        raise ValueError('proofs contain facts outside the committed prefix')
    if any(key not in proofs for key in order):
        raise ValueError('committed prefix proof is missing')
    return list(order)


def manifest(graph, proofs, target):
    by_id = {n['id']:n for n in graph['nodes']}
    required = set(by_id[target]['dependencies'])
    facts = [{'id':key, 'name':by_id[key]['name'], 'statement':by_id[key]['statement'],
              'context':by_id[key]['context'], 'proof_sha256':digest(proofs[key]),
              'required':key in required} for key in visible_ids(graph, proofs, target)]
    return {'facts':facts, 'context_id':digest(facts)}
