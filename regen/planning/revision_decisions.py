"""Validate explicit review dispositions and bind their handoff to a plan.

Legacy decisions remain readable. New planning opts into the strict policy; its
structured obligations are advice and pending work, never proof certificates.
"""
from copy import deepcopy
import hashlib
import json


REVIEW_POLICY = 'review-obligations-v1'


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def stable_issue_id(issue):
    """Stable across review reordering and wording changes, not arbitrary IDs."""
    return 'review:' + _digest({key: sorted(issue[key]) if key != 'kind' else issue[key]
                              for key in ('kind', 'contract_ids')})[:24]


def _owner(plan):
    return {c: t['name'] for t in plan['theories'] for c in t.get('contract_ids',
            [c['contract_id'] for c in t.get('contracts', [])])}


def _structure(plan, affected):
    rows = []
    for theory in plan['theories']:
        if theory['name'] in affected:
            rows.append({key: theory.get(key) for key in (
                'name', 'contract_ids', 'context_profile_ids', 'helper_decisions')} | {
                'required_imports': sorted(x['theory'] if isinstance(x, dict) else x
                                           for x in theory.get('required_imports', []))})
    affected_ids = {c for t in plan['theories'] if t['name'] in affected
                    for c in t.get('contract_ids', [])}
    return rows, [d for d in plan.get('proof_direction_suggestions', [])
                  if d.get('target_contract_id') in affected_ids]


def decisions_valid(decisions, review, *, before=None, after=None, strict=False):
    expected = {i['id'] for i in review['issues']}
    if not isinstance(decisions, list) or len(decisions) != len(expected):
        raise ValueError('One decision required per review issue')
    seen = set()
    fields = {'issue_id', 'decision', 'reason', 'tradeoff', 'remaining_risk'}
    if strict:
        fields.add('obligations')
    issues = {i['id']: i for i in review['issues']}
    for index, row in enumerate(decisions):
        if not isinstance(row, dict) or set(row) != fields:
            raise ValueError(f'Decision {index} requires exactly {sorted(fields)}')
        identity = row['issue_id']
        if not isinstance(identity, str) or identity not in expected or identity in seen:
            raise ValueError(f'Decision {index} has an unknown or duplicate issue_id')
        seen.add(identity)
        if not isinstance(row['decision'], str) or row['decision'] not in {'revise', 'local', 'retain'}:
            raise ValueError(f'Decision {index} has an invalid decision')
        for key in ('reason', 'tradeoff', 'remaining_risk'):
            if not isinstance(row[key], str) or not row[key].strip():
                raise ValueError(f'Decision {index} requires nonempty {key}')
        if not strict:
            continue
        if before is None or after is None:
            raise ValueError('Strict review decisions require the actual before/after plans')
        issue = issues[identity]
        owner = _owner(after)
        obligations = row['obligations']
        if not isinstance(obligations, list) or not obligations:
            raise ValueError(f'Decision {identity} needs concrete obligations')
        for obligation in obligations:
            required = {'theory', 'contract_ids', 'provider_contract_ids', 'route',
                        'task', 'completion_condition'}
            if not isinstance(obligation, dict) or set(obligation) != required:
                raise ValueError(f'Decision {identity} has an invalid obligation schema')
            theory, ids = obligation['theory'], obligation['contract_ids']
            providers = obligation['provider_contract_ids']
            if (not isinstance(theory, str) or theory not in owner.values()
                    or not isinstance(ids, list) or not ids
                    or any(not isinstance(c, str) or owner.get(c) != theory for c in ids)
                    or not set(ids) & set(issue['contract_ids'])):
                raise ValueError(f'Decision {identity} has ungrounded consumer obligations')
            if (not isinstance(providers, list)
                    or any(not isinstance(c, str) or c not in owner or owner[c] == theory
                           for c in providers)):
                raise ValueError(f'Decision {identity} has invalid provider references')
            route = obligation['route']
            if not isinstance(route, str) or route not in {'import', 'local', 'retain'}:
                raise ValueError(f'Decision {identity} has an invalid obligation route')
            if row['decision'] == 'local' and route != 'local':
                raise ValueError('A local decision must allocate local obligations')
            for key in ('task', 'completion_condition'):
                if not isinstance(obligation[key], str) or not obligation[key].strip():
                    raise ValueError(f'Decision {identity} needs an explicit {key}')
            if route == 'import':
                if not providers:
                    raise ValueError('Import obligations require concrete provider contracts')
                deps = {t['name']: [v['theory'] if isinstance(v, dict) else v
                                  for v in t.get('required_imports', [])]
                        for t in after['theories']}
                reachable, pending = set(), list(deps[theory])
                while pending:
                    name = pending.pop()
                    if name in reachable:
                        continue
                    if name not in deps:
                        raise ValueError('Unknown review obligation provider')
                    reachable.add(name); pending.extend(deps[name])
                if any(owner[c] not in reachable for c in providers):
                    raise ValueError('Selected review provider is absent from the actual import closure')
        if row['decision'] == 'revise':
            affected = set(issue['theories']) | {o['theory'] for o in obligations}
            if _structure(before, affected) == _structure(after, affected):
                raise ValueError(f'Decision {identity} claims revise without a concrete plan change')
    return decisions


def prior_dispositions(review, decisions):
    by_id = {d['issue_id']: d for d in decisions}
    return [{'issue_id': stable_issue_id(i), 'issue': deepcopy(i),
             'disposition': deepcopy(by_id[i['id']])} for i in review['issues']]


def unresolved_review_issues(review, prior):
    """Only an explicit final review can accept an unchanged local/retain route."""
    previous = {v['issue_id']: v for v in prior}
    assessed = {v['issue_id']: v for v in review.get('decision_reviews', [])}
    unresolved = []
    for issue in review['issues']:
        identity = stable_issue_id(issue)
        old, assessment = previous.get(identity), assessed.get(identity)
        if (not old or not assessment or assessment['status'] != 'accepted'
                or old['disposition']['decision'] not in {'local', 'retain'}):
            unresolved.append(issue)
    return unresolved


def build_review_handoff(plan, review, decisions=(), *, legacy=False,
                         accepted_dispositions=()):
    """Upgrade a legacy review without claiming its unresolved risks were fixed.

    The plan digest covers the exact published JSON. Consumers must verify it
    before attaching this sidecar to immutable packages or a continuation ledger.
    """
    direct = {d['issue_id']: d for d in decisions}
    previous = {v['issue_id']: v for v in accepted_dispositions}
    names = {t['name'] for t in plan['theories']}
    rows = {name: {'assessment': '', 'obligations': []} for name in sorted(names)}
    owners = _owner(plan)
    def affected(issue):
        return set(issue['theories']) & names | {owners[c] for c in issue['contract_ids'] if c in owners}
    for row in review['theory_reviews']:
        if row['theory'] in rows:
            rows[row['theory']]['assessment'] = row['assessment']
    for issue in review['issues']:
        identity = stable_issue_id(issue)
        disposition = direct.get(issue['id'])
        if disposition is None and identity in previous:
            disposition = previous[identity]['disposition']
        entry = {'issue_id': identity, 'review_issue_id': issue['id'],
                 'issue': deepcopy(issue), 'disposition': deepcopy(disposition),
                 'status': ('unresolved' if disposition is None else
                            'revised' if disposition['decision'] == 'revise' else disposition['decision'])}
        for name in affected(issue):
            rows[name]['obligations'].append(deepcopy(entry))
    # Duties may disappear from final issues after the reviewer accepts their
    # allocation. A revised import is not a completed consumer proof; every
    # concrete obligation must still reach the File Planner as pending work.
    present = {stable_issue_id(i) for i in review['issues']}
    for item in accepted_dispositions:
        if item['issue_id'] in present:
            continue
        disposition = item['disposition']
        entry = {'issue_id': item['issue_id'], 'review_issue_id': item['issue']['id'],
                 'issue': deepcopy(item['issue']), 'disposition': deepcopy(disposition),
                 'status': 'revised' if disposition['decision'] == 'revise' else disposition['decision']}
        for name in affected(item['issue']):
            rows[name]['obligations'].append(deepcopy(entry))
    return {'schema_version': 1, 'policy': REVIEW_POLICY,
            'plan_sha256': _digest(plan), 'review_sha256': _digest(review),
            'legacy': bool(legacy), 'theories': rows}
