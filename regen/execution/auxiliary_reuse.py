"""On-demand examples from the checker's accepted same-theory prefix only."""
from regen.execution.fact_context import manifest, visible_ids
from regen.execution.proof_commits import digest


def auxiliary_page(graph, proofs, target, request):
    ids = request.get('node_ids') if isinstance(request, dict) else None
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 4
            or not all(isinstance(key, str) for key in ids)):
        raise ValueError('auxiliary_query requires 1 to 4 node_ids')
    visible = set(visible_ids(graph, proofs, target)) & set(proofs)
    if not set(ids) <= visible:
        raise ValueError('auxiliary proof is outside the accepted visible prefix')
    facts = {fact['id']: fact for fact in manifest(graph, proofs, target)['facts']}
    return {'kind': 'accepted_auxiliaries', 'status': 'available', 'nodes': [
        {**facts[key], 'status': 'green', 'proof_body': proofs[key],
         'proof_sha256': digest(proofs[key])} for key in dict.fromkeys(ids)],
        'notice': 'Accepted examples with their original contexts. Reuse requires '
                  'applicable assumptions; a new proof must pass the checker.'}
