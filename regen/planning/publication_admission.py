"""Bind publication to certified entries rather than incidental draft ordering."""
from contextlib import contextmanager
from regen.planning.theory_document import candidate, source_hash


def accepted_document_binding(package, state, source, imports):
    """Return a receipt only for unchanged declarations, imports and proof bodies.

    The published order remains the certified order. This does not certify the
    requested draft order, and never replaces complete native verification.
    """
    proposed = candidate(package, state, source, imports)
    before = {n['id']:n for n in state['graph']['nodes']}
    after = {n['id']:n for n in proposed['graph']['nodes']}
    if (proposed['proofs'] != state['proofs'] or set(before) != set(after)
            or proposed.get('document_imports', imports) != imports
            or state.get('document_imports', imports) != imports):
        return None
    fields = ('id', 'name', 'role', 'statement', 'context')
    if any(any(before[k].get(f) != after[k].get(f) for f in fields) for k in before):
        return None
    return {'policy':'publish-certified-entry-order-v1',
            'requested_source_sha256':source_hash(source),
            'accepted_source_sha256':state.get('document_source_sha256'),
            'requested_order':proposed['accepted_order'],
            'published_order':list(state['accepted_order']),
            'proof_bodies_unchanged':True, 'complete_verification_required':True}


@contextmanager
def complete_document_budget(checker):
    """Use the existing whole-check budget; never leave it on a warm worker."""
    missing = object()
    previous = getattr(checker, 'batch_candidate_timeout', missing)
    checker.batch_candidate_timeout = checker.timeout
    try:
        yield
    finally:
        if previous is missing:
            del checker.batch_candidate_timeout
        else:
            checker.batch_candidate_timeout = previous


def check_complete_document(checker, state, proposed, source):
    from regen.planning.theory_document import check_document
    with complete_document_budget(checker):
        evidence = check_document(checker, state, proposed, source)
    evidence['publication_check_budget'] = {'scope':'complete-document',
                                          'seconds':checker.timeout}
    return evidence
