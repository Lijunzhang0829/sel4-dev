"""Validate revision decisions before the planner publishes its submission."""


def decisions_valid(decisions, review):
    expected = {i['id'] for i in review['issues']}
    if not isinstance(decisions, list) or len(decisions) != len(expected):
        raise ValueError('One decision required per review issue')
    seen = set()
    fields = {'issue_id', 'decision', 'reason', 'tradeoff', 'remaining_risk'}
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
