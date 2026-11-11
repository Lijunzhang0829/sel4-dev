"""Versioned semantic-risk reviews; judgments never certify helper truth.

Coverage statements are model-authored claims bound by the enclosing review.
Critical route assumptions must obtain ordinary GREEN before sibling proof work.
"""
from regen.planning.sketch_check import load_groups

CRITICAL_ROLES = frozenset({'classification', 'bridge'})


class WorkflowBlocked(ValueError):
    workflow_blocked = True
    def __init__(self, code, diagnostic, *, details=None, next_actions=None):
        super().__init__(diagnostic)
        self.code = code
        self.details = details or {}
        self.next_actions = next_actions or []


def formal_leaf_mode(store, row):
    return (getattr(store, 'allow_formal_leaf', False)
            and row.get('proof_mode') == 'formal'
            and isinstance(row.get('proof_plan'), str) and bool(row['proof_plan'].strip()))


def validate_coverage(store, row):
    if not getattr(store, 'require_semantic_review', False):
        return
    if row.get('helper_role') not in {'classification', 'bridge', 'local'}:
        raise WorkflowBlocked('semantic_review_missing', 'semantic review requires helper_role')
    coverage = row.get('coverage')
    if not isinstance(coverage, dict) or set(coverage) != {
            'definition_refs', 'branches', 'premise_sources', 'unknowns'}:
        raise WorkflowBlocked('semantic_review_missing', 'semantic review requires coverage evidence')
    for field in ('definition_refs', 'premise_sources', 'unknowns'):
        values = coverage[field]
        if (not isinstance(values, list) or any(not isinstance(x, str) or not x.strip() for x in values)
                or (field == 'definition_refs' and not values)):
            raise WorkflowBlocked('semantic_review_missing', 'semantic coverage needs '+field)
    branches = coverage['branches']
    if not isinstance(branches, list) or not branches:
        raise WorkflowBlocked('semantic_review_missing', 'semantic coverage requires explicit branches')
    for branch in branches:
        if (not isinstance(branch, dict) or set(branch) != {'case', 'status', 'evidence'}
                or branch.get('status') not in {'handled', 'excluded', 'unknown'}
                or any(not isinstance(branch.get(k), str) or not branch[k].strip()
                       for k in ('case', 'evidence'))):
            raise WorkflowBlocked('semantic_review_missing', 'invalid semantic branch evidence')
    if any(b['status']=='unknown' for b in branches) and not coverage['unknowns']:
        raise WorkflowBlocked('semantic_review_missing', 'unknown branches need explicit unresolved coverage')
    if row.get('proof_mode') not in {'diagnostic', 'formal'}:
        raise WorkflowBlocked('proof_mode_missing', 'semantic review requires diagnostic or formal proof mode')
    if row['proof_mode']=='formal' and not formal_leaf_mode(store, row):
        raise WorkflowBlocked('formal_plan_missing', 'formal proof mode requires an explicit proof_plan')


def feasibility(store, row):
    if row['helper_id'] in store.state['proofs']:
        return 'proved'
    if row.get('probe_id'):
        from regen.planning.sketch_workflow import _read
        if _read(store, 'probes', row['probe_id'])['status']=='proved_in_probe':
            return 'proved_in_probe'
    return 'unestablished'


def _descendants(groups, key):
    found=set();todo=[key]
    while todo:
        target=todo.pop()
        for group in groups:
            if group['target_entry_id']==target:
                for child in group['helper_ids']:
                    if child not in found:found.add(child);todo.append(child)
    return found


def require_critical_predecessors(store, nodes, proofs, group, review, needed):
    if not getattr(store, 'require_semantic_review', False):
        return
    critical={r['helper_id'] for r in review['reviews']
              if r.get('helper_role') in CRITICAL_ROLES
              and r['helper_id'] not in store.state['proofs']}
    groups=load_groups(store)
    for key in set(group['helper_ids']) & set(needed):
        if key in critical:
            continue
        missing=sorted(c for c in critical if key not in _descendants(groups,c))
        if missing:
            from regen.planning.theory_document import source_hash
            current=source_hash(store.path.read_text())
            raise WorkflowBlocked('critical_helper_unproved',
                'semantic-critical helper must be proved first: '+', '.join(missing),
                details={'entry_id':key,'critical_entry_ids':missing},
                next_actions=[{'request':{'action':'check_theory','source_sha256':current,
                                         'entry_ids':missing},'requires_agent_fields':[]}])
