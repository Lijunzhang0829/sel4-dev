"""Reconstruct this experiment's append-only acceptance history, not its pointer."""
import hashlib
import json


def state_digest(state):
    data = (json.dumps(state, sort_keys=True, ensure_ascii=False, indent=2) + '\n').encode()
    return hashlib.sha256(data).hexdigest()


def replay_lab(lab, *, check_pointer=True):
    initial = json.loads((lab / 'initial.json').read_text())
    state = {'graph': initial['graph'], 'proofs': initial['proofs'], 'accepted': []}
    pending = [(p, json.loads(p.read_text())) for p in (lab / 'acceptance').glob('*.json')]
    while pending:
        matches = [(p, r) for p, r in pending
                   if r['previous_state_sha256'] == state_digest(state)]
        if len(matches) != 1:
            raise ValueError('broken or forked experimental acceptance history')
        path, record = matches[0]
        new = record['state']
        request, check = record['result']['request'], record['result']['check']
        key = request['node_id']
        if check['verdict'] != 'green' or check.get('speculative'):
            raise ValueError('acceptance lacks independent cold GREEN')
        if key in state['proofs'] or set(new['proofs']) != set(state['proofs']) | {key}:
            raise ValueError('acceptance changes existing proof responsibility')
        if any(new['proofs'][k] != v for k, v in state['proofs'].items()):
            raise ValueError('accepted proof was changed')
        if new['proofs'][key] != request['proof_body']:
            raise ValueError('accepted proof differs from checked candidate')
        old_nodes = {n['id']: n for n in state['graph']['nodes']}
        new_nodes = {n['id']: n for n in new['graph']['nodes']}
        if set(new_nodes) != set(old_nodes) | {key}:
            raise ValueError('nodes were silently added or removed')
        for k, old in old_nodes.items():
            for field in ('name', 'role', 'statement', 'context', 'dependencies'):
                if old.get(field) != new_nodes[k].get(field):
                    raise ValueError('existing node interface or dependency changed')
        if key not in old_nodes:
            candidate = request.get('new_node', {})
            if candidate.get('role') != 'helper':
                raise ValueError('new node must be an explicitly proposed helper')
            for field in ('id', 'name', 'role', 'statement', 'context', 'dependencies'):
                if candidate.get(field) != new_nodes[key].get(field):
                    raise ValueError('helper differs from proposed interface')
        if new['graph']['accepted_order'] != state['graph']['accepted_order'] + [key]:
            raise ValueError('accepted declaration order changed')
        if new['accepted'] != state['accepted'] + [path.name]:
            raise ValueError('acceptance sequence changed')
        pending.remove((path, record))
        state = new
    if check_pointer and json.loads((lab / 'state.json').read_text()) != state:
        raise ValueError('state pointer differs from replayed acceptance history')
    return state
