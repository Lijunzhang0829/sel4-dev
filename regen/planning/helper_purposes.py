"""Advisory helper purpose notes; never proof authority or scheduling edges."""
from copy import deepcopy

from regen.execution.proof_commits import digest


FIELDS = ('contract_id', 'gap', 'expected_use', 'remaining_gap')


def identity(entry):
    return digest({key: entry[key] for key in ('id', 'statement', 'context')})


def merge(records, updates, entries, proofs, source_sha256):
    """Bind model-authored notes to declarations, not merely reusable helper IDs."""
    nodes = {entry['id']: entry for entry in entries}
    result = {key: deepcopy(row) for key, row in records.items()
              if key in nodes and row['declaration_sha256'] == identity(nodes[key])}
    if not isinstance(updates, dict):
        raise ValueError('helper_purposes must be an object keyed by helper ID')
    for key, update in updates.items():
        if key not in nodes or nodes[key]['role'] != 'helper':
            raise ValueError('purpose must identify a current helper')
        if not isinstance(update, dict) or set(update) - {*FIELDS, 'used_in_target'}:
            raise ValueError('invalid helper purpose fields; status is derived, not writable')
        if any(not isinstance(update.get(field), str) or not update[field].strip() for field in FIELDS):
            raise ValueError('helper purpose requires contract_id, gap, expected_use, remaining_gap')
        target = update['contract_id']
        if target not in nodes or nodes[target]['role'] != 'contract':
            raise ValueError('purpose target must be a fixed contract in this theory')
        row = {field: update[field] for field in FIELDS}
        row['declaration_sha256'] = identity(nodes[key])
        use = update.get('used_in_target')
        if use is not None:
            if not isinstance(use, str) or not use.strip() or target not in proofs:
                raise ValueError('reported target use requires a location and a non-oops target body')
            row['reported_use'] = {'location': use, 'source_sha256': source_sha256,
                                   'target_proof_sha256': digest(proofs[target])}
        result[key] = row
    return result


def view(records, entries, proofs, state):
    accepted = {entry['id']: entry for entry in state['graph']['nodes']}
    rows, missing = {}, []
    for entry in entries:
        if entry['role'] != 'helper':
            continue
        key = entry['id']
        row = records.get(key)
        if not row or row['declaration_sha256'] != identity(entry):
            missing.append(key)
            continue
        row = deepcopy(row)
        green = (key in state['proofs'] and key in accepted
                 and identity(accepted[key]) == identity(entry))
        row['status'] = 'accepted' if green else 'pending'
        use = row.get('reported_use')
        if (green and use and row['contract_id'] in proofs
                and digest(proofs[row['contract_id']]) == use['target_proof_sha256']):
            row['status'] = 'used_in_target'
        row['target_green'] = row['contract_id'] in state['proofs']
        rows[key] = row
    return {'records': rows, 'missing_helper_ids': missing,
            'notice': 'Purpose/use are planner notes, not native dependency evidence. '
                      'Missing notes do not block checks. Target GREEN comes only from acceptance.'}
