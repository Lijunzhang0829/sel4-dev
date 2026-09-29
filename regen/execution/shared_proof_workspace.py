"""Read checked source from this run; copied helpers are ordinary new candidates.

No source read imports a theorem, runs a prover, repairs a journal pointer, or
writes a source file. Hashes identify evidence, rather than conferring GREEN.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import threading

from regen.execution.proof_commits import digest, _fingerprint, commit_paths, journal_lock
from regen.planning.proof_graph import DECLARATION, normalized_declaration_body

SCHEMA = 'shared-proof-workspace-v1'
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_']*\Z")
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_'.]*")


def _outside_strings(text, transform):
    """Rewrite Isabelle identifiers without changing terms or comments."""
    pieces, start, i, depth, quoted = [], 0, 0, 0, False
    while i < len(text):
        if depth:
            if text.startswith('(*', i): depth += 1; i += 2
            elif text.startswith('*)', i):
                depth -= 1; i += 2
                if not depth: pieces.append(text[start:i]); start = i
            else: i += 1
        elif quoted:
            if text[i] == '\\': i += 2
            elif text[i] == '"':
                quoted = False; i += 1; pieces.append(text[start:i]); start = i
            else: i += 1
        elif text.startswith('(*', i):
            pieces.append(_TOKEN.sub(lambda m: transform(m.group()), text[start:i]))
            start = i; depth = 1; i += 2
        elif text[i] == '"':
            pieces.append(_TOKEN.sub(lambda m: transform(m.group()), text[start:i]))
            start = i; quoted = True; i += 1
        else: i += 1
    if depth or quoted:
        raise ValueError('unclosed source proof comment or string')
    pieces.append(_TOKEN.sub(lambda m: transform(m.group()), text[start:]))
    return ''.join(pieces)


def _actual_dependencies(evidence, graph):
    """Read only dependency records embedded in a validated acceptance record."""
    result = {}
    check = evidence.get('dependencies', {})
    audit = check.get('complete_theory_audit', {})
    native = check.get('source_content', '')
    label = re.search(r'^\s*theory\s+([A-Za-z0-9_]+)', native)
    if not label or check.get('verdict') != 'green':
        return result
    names = {label[1] + '.' + n['name']: n['id'] for n in graph['nodes']}
    for key, row in audit.get('proof_dependencies', {}).items():
        result[key] = sorted({names[d['canonical_name']] for d in row.get('dependencies', [])
                              if d.get('canonical_name') in names and names[d['canonical_name']] != key})
    return result


class SharedProofWorkspace:
    def __init__(self, run_dir):
        self.root = Path(run_dir).resolve()
        self.run = self._json(self.root / 'run.json')
        if self.run.get('execution_mode') != 'repository-projection-v1':
            raise ValueError('shared workspace requires a repository projection run')
        if Path(self.run['contract_package_dir']).resolve() != self.root / 'packages':
            raise ValueError('shared workspace packages must belong to this run')
        self.inputs = self._json(self.root / 'execution-input.json')
        if digest(self.inputs['file_list']) != self.run['session_schedule']['file_list_sha256']:
            raise ValueError('shared workspace file list binding mismatch')
        self.entries = {e['theory']: e for e in self.inputs['files']}
        if len(self.entries) != len(self.inputs['files']):
            raise ValueError('duplicate workspace theory')
        frozen = self.inputs['file_list']['files']
        actual = self.inputs['files']
        if [(r['theory'],r['file_slot']) for r in actual] != [(r['theory'],r['file_slot']) for r in frozen]:
            raise ValueError('derived files differ from frozen file list')
        self.fixed_contract_ids = []
        for reference,row in zip(frozen,actual):
            package = self._package(reference['theory'])
            contracts = [c['contract_id'] for c in package['contracts']]
            expected = reference.get('contract_ids',contracts)
            if (len(set(expected)) != len(expected) or set(expected) != set(contracts)
                    or ('contract_ids' in reference and 'contract_ids' not in row)
                    or set(row.get('contract_ids',contracts)) != set(expected)
                    or len(row.get('contract_ids',contracts)) != len(expected)):
                raise ValueError('derived contract coverage differs from frozen targets')
            self.fixed_contract_ids.extend(expected)
        if len(set(self.fixed_contract_ids)) != len(self.fixed_contract_ids):
            raise ValueError('duplicate frozen contract target')
        self._lock = threading.RLock()
        self._cache = {}
        self._index_cache = {}
        self._observed_sequences = {}

    def _path(self, path):
        path = Path(path)
        if not path.resolve().is_relative_to(self.root):
            raise ValueError('workspace source path escapes this run')
        return path

    def _json(self, path):
        return json.loads(self._path(path).read_text())

    def _package(self, theory):
        if not isinstance(theory, str) or theory not in self.entries:
            raise ValueError('unknown theory in this run')
        slot = self.entries[theory]['file_slot']
        if Path(slot).name != slot:
            raise ValueError('invalid workspace file slot')
        package = self._json(self.root / 'packages' / (slot + '.json'))
        expected = self.inputs['package_hashes'][slot]
        if (package.get('theory') != theory or package.get('file_slot') != slot
                or package.get('package_payload_sha256') != expected
                or digest({k: v for k, v in package.items() if k != 'package_payload_sha256'}) != expected):
            raise ValueError('workspace source package binding mismatch')
        return package

    def _snapshot(self, theory, commit_id=None):
        with self._lock:
            package = self._package(theory)
            directory = self._path(self.root / 'theories' / Path(package['file_slot']).stem)
            # durable(..., immutable=True) links a complete temporary inode to
            # the commit name and then removes the temporary link. The latter
            # legitimately changes ctime without changing commit bytes. Share
            # the writer's transaction lock so fingerprints never observe that
            # short in-flight link-count transition.
            with journal_lock(directory / 'acceptance'):
                return self._read_snapshot(
                    theory, commit_id, package=package, directory=directory)

    def _read_snapshot(self, theory, commit_id=None, *, package, directory):
        """Validate the same chain/evidence as ProofCommits, without HEAD repair.

        Cached snapshots are bound to every immutable prefix file's fingerprint.
        A requested historical commit is still in this run and is validated from
        the beginning; it does not follow historical-run paths or catalog rows.
        """
        from regen.execution.repository_execution import journal
        # journal() constructs only a validator and binding; replay() is not used
        # because normal replay may repair its mutable HEAD.json projection.
        self._path(directory / 'imports.json')
        log = journal(self.root, self.run, package)
        self._path(directory / 'acceptance' / 'HEAD.json')
        paths, _ = commit_paths(self._path(directory / 'acceptance'))
        fingerprints = tuple(_fingerprint(self._path(p)) for p in paths)
        if len(paths) < self._observed_sequences.get(theory, 0):
            raise ValueError('workspace committed history is missing from an observed prefix')
        self._observed_sequences[theory] = len(paths)
        cache_key = (theory, commit_id, digest(log.binding))
        cached = self._cache.get(cache_key)
        if cached and cached[0] == fingerprints:
            return deepcopy(cached[1])
        state = previous = None
        inherited_run_id = None
        actual = {}
        result = None
        for sequence, path in enumerate(paths, 1):
            record = self._json(path)
            if _fingerprint(path) != fingerprints[sequence - 1]:
                raise ValueError('workspace source changed during journal validation')
            body = {k: v for k, v in record.items() if k != 'commit_id'}
            if (path.name != f'{sequence:06d}.json' or record.get('sequence') != sequence
                    or record.get('parent') != previous or record.get('binding') != log.binding
                    or record.get('commit_id') != digest(body)):
                raise ValueError('invalid workspace proof commit chain/binding')
            log.validate(state, record['state'], record['event'], record['evidence'])
            state, previous = record['state'], record['commit_id']
            # The validator above replays the complete embedded origin and binds
            # this initial state exactly. Retain its index identity without
            # trusting arbitrary foreign run IDs in later records.
            if sequence == 1 and record['event'] == 'initial' and any(
                    key in record['evidence'] for key in ('sealed_reuse','continuation')):
                inherited_run_id = state['graph']['run_id']
            run_ids = {self.run['run_id'], self.run['run_id'] + '--' + theory,
                       self.run['run_id'] + '--' + Path(package['file_slot']).stem}
            if inherited_run_id is not None:
                run_ids.add(inherited_run_id)
            if state['graph']['run_id'] not in run_ids or state['graph']['theory'] != theory:
                raise ValueError('workspace graph belongs to a different run/theory')
            if record['event'] == 'seal':
                actual.update(_actual_dependencies(record['evidence'], state['graph']))
            if commit_id is not None and previous == commit_id:
                result = {'state': state, 'commit_id': previous, 'actual_dependencies': actual,
                          'directory': str(directory), 'package': package}
                break
        if commit_id is not None and result is None:
            raise ValueError('unknown immutable source commit in this theory')
        if commit_id is None:
            result = None if state is None else {
                'state': state, 'commit_id': previous, 'actual_dependencies': actual,
                'directory': str(directory), 'package': package}
        # Retain a small read cache, never source journal records or raw logs.
        if len(self._cache) >= 8: self._cache.pop(next(iter(self._cache)))
        self._cache[cache_key] = (fingerprints, deepcopy(result))
        if result is not None and commit_id is None:
            self._cache[(theory, result['commit_id'], digest(log.binding))] = self._cache[cache_key]
        return deepcopy(result)

    def _ref(self, snapshot, node):
        return {'run_id': self.run['run_id'], 'theory': snapshot['state']['graph']['theory'],
                'node_id': node['id'], 'commit_id': snapshot['commit_id'],
                'node_sha256': digest(node),
                'proof_sha256': digest(snapshot['state']['proofs'][node['id']])}

    def _resolve(self, ref):
        fields = {'run_id', 'theory', 'node_id', 'commit_id', 'node_sha256', 'proof_sha256'}
        if (not isinstance(ref, dict) or set(ref) != fields
                or any(not isinstance(v, str) or not v for v in ref.values())
                or ref['run_id'] != self.run['run_id']):
            raise ValueError('invalid source reference for this run')
        snapshot = self._snapshot(ref['theory'], ref['commit_id'])
        nodes = {n['id']: n for n in snapshot['state']['graph']['nodes']}
        node = nodes.get(ref['node_id'])
        if node is None or node['id'] not in snapshot['state']['proofs']:
            raise ValueError('source reference is not an accepted proof')
        if self._ref(snapshot, node) != ref:
            raise ValueError('source node/proof hash mismatch')
        return snapshot, node

    def index(self, *, theory=None, query='', offset=0, limit=20):
        if (not isinstance(query, str) or type(offset) is not int or offset < 0
                or type(limit) is not int or not 1 <= limit <= 100):
            raise ValueError('workspace index requires text query and a valid page')
        theories = [theory] if theory is not None else list(self.entries)
        rows, summaries = [], []
        for owner in theories:
            snapshot = self._snapshot(owner)
            if snapshot is None:
                summaries.append({'theory': owner, 'status': 'not_started', 'accepted': 0})
                continue
            # Validate the source snapshot first, then reuse derived rows for its
            # immutable commit. Retain only the latest index per theory.
            with self._lock:
                cached = self._index_cache.get(owner)
                if cached is None or cached['commit_id'] != snapshot['commit_id']:
                    state = snapshot['state']
                    failures = [{'request_number': h.get('request_number'), 'status': h.get('status')}
                                for h in state.get('batch_history', [])
                                if h.get('status') in {'rejected_batch', 'rejected_request', 'checked_not_committed'}]
                    summary = {'theory': owner, 'status': state['status'],
                               'accepted': len(state['proofs']), 'failed_attempt_count': len(failures),
                               'failed_attempts': failures[-8:]}
                    records = []
                    for node in state['graph']['nodes']:
                        if node['id'] in state['proofs']:
                            records.append(((node['name']+' '+node['statement']).lower(),
                                {'source_ref': self._ref(snapshot,node), 'name':node['name'],
                                 'role':node['role'], 'status':'source_green_reference',
                                 'statement_preview':node['statement'][:240]}))
                    cached = {'commit_id':snapshot['commit_id'],'summary':summary,'records':records}
                    self._index_cache[owner] = cached
                summaries.append(deepcopy(cached['summary']))
                rows.extend(deepcopy(row) for text,row in cached['records'] if query.lower() in text)
        return {'kind': SCHEMA, 'status': 'available', 'theories': summaries,
                'total': len(rows), 'offset': offset, 'records': rows[offset:offset + limit],
                'notice': 'Source examples only; no theorem is imported or accepted in the target.'}

    def read(self, refs):
        if not isinstance(refs, list) or not 1 <= len(refs) <= 8:
            raise ValueError('read_workspace requires 1-8 exact source references')
        records = []
        for ref in refs:
            snapshot, node = self._resolve(ref)
            records.append({'source_ref': deepcopy(ref), **deepcopy(node),
                            'proof_body': snapshot['state']['proofs'][node['id']],
                            'status': 'source_green_reference', 'reference_only': True})
        return {'status': 'available', 'records': records,
                'notice': 'Original contexts are retained. Copying requires a new target-native check.'}

    def read_attempt(self, theory, request_number):
        if type(request_number) is not int or request_number < 1:
            raise ValueError('candidate reference requires a positive request number')
        snapshot = self._snapshot(theory)
        if snapshot is None:
            raise ValueError('theory has no committed attempts')
        history = {h['request_number']: h for h in snapshot['state'].get('batch_history', [])}
        entry = history.get(request_number)
        if not entry or entry.get('status') not in {'rejected_batch', 'rejected_request', 'checked_not_committed'}:
            raise ValueError('request is not a committed failed candidate reference')
        directory = Path(snapshot['directory']) / 'batch-requests' / f'{request_number:06d}'
        candidate = self._json(directory / 'proposal.json')
        feedback = self._json(directory / 'feedback.json')
        if (not entry.get('proposal_sha256') or digest(candidate) != entry['proposal_sha256']
                or not entry.get('feedback_sha256') or digest(feedback) != entry['feedback_sha256']):
            raise ValueError('candidate/feedback is not bound to the verified source commit')
        return {'status': 'candidate_reference', 'reference_only': True,
                'theory': theory, 'request_number': request_number,
                'source_commit_id': snapshot['commit_id'], 'candidate': candidate,
                'feedback': feedback, 'notice': 'Failed source; never a GREEN premise or accepted reuse bundle.'}

    def _closure(self, snapshot, root):
        state = snapshot['state']
        nodes = {n['id']: n for n in state['graph']['nodes']}
        aliases = {}
        prefixes = [state['graph']['theory'], 'Regen_' + Path(state['graph']['file_slot']).stem]
        for node in nodes.values():
            for name in [node['name'], *(p + '.' + node['name'] for p in prefixes)]:
                aliases[name] = node['id']
        deps, visiting = {}, set()
        def visit(key):
            if key in visiting: raise ValueError('cyclic source helper dependency')
            if key in deps: return
            if key not in nodes or key not in state['proofs']:
                raise ValueError('reuse dependency has no accepted source proof: ' + key)
            node = nodes[key]
            if node['role'] == 'contract':
                raise ValueError('fixed contracts cannot be copied as helpers; source dependency: ' + key)
            visiting.add(key)
            used = set()
            _outside_strings(state['proofs'][key], lambda token: used.add(aliases[token]) or token
                             if token in aliases else token)
            wanted = set(node['dependencies']) | set(snapshot['actual_dependencies'].get(key, [])) | used
            wanted.discard(key)
            for dep in sorted(wanted): visit(dep)
            visiting.remove(key)
            deps[key] = sorted(wanted)
        visit(root['id'])
        return deps, aliases

    def prepare_reuse(self, source_ref, *, target_state, target_node_id, mapping=None):
        snapshot, source = self._resolve(source_ref)
        graph = target_state['graph']
        target_package = self._package(graph['theory'])
        run_ids = {self.run['run_id'], self.run['run_id'] + '--' + graph['theory'],
                   self.run['run_id'] + '--' + Path(target_package['file_slot']).stem}
        if graph['run_id'] not in run_ids or graph['file_slot'] != target_package['file_slot']:
            raise ValueError('reuse target belongs to a different run')
        if source_ref['theory'] == graph['theory']:
            raise ValueError('same-theory proofs already have a read_proofs/reuse_proofs route')
        target_nodes = {n['id']: n for n in graph['nodes']}
        target = target_nodes.get(target_node_id)
        if target is None or target_node_id in target_state['proofs']:
            raise ValueError('reuse requires an existing unproved target consumer')
        if target_state.get('status') in {'sealed', 'retired'}:
            raise ValueError('reuse target theory is immutable')
        dependencies, aliases = self._closure(snapshot, source)
        nodes = {n['id']: n for n in snapshot['state']['graph']['nodes']}
        if mapping is None:
            mapping = {}
            for key in dependencies:
                name = 'reuse_' + re.sub(r'[^A-Za-z0-9_]', '_', source_ref['theory'] + '_' + nodes[key]['name'])
                name += '_' + digest({'ref': source_ref, 'node': key})[:8]
                mapping[key] = {'id': 'node:' + graph['theory'] + ':' + name, 'name': name}
        if not isinstance(mapping, dict) or set(mapping) != set(dependencies):
            raise ValueError('reuse mapping must include the complete source helper dependency closure')
        ids, names = set(target_nodes), {n['name'] for n in target_nodes.values()}
        for key, binding in mapping.items():
            if (not isinstance(binding, dict) or set(binding) != {'id', 'name'}
                    or not isinstance(binding['id'], str) or not binding['id']
                    or not isinstance(binding['name'], str) or not _NAME.fullmatch(binding['name'])):
                raise ValueError('invalid copied helper ID/name binding')
            if binding['id'].startswith('contract:') or binding['id'] in ids or binding['name'] in names:
                raise ValueError('copied helper ID/name conflicts with an existing target node')
            ids.add(binding['id']); names.add(binding['name'])
            if nodes[key].get('context', []) != target.get('context', []):
                raise ValueError('source helper context is incompatible with the selected target consumer')
            if any(normalized_declaration_body(nodes[key]['statement']) == normalized_declaration_body(n['statement'])
                   for n in target_nodes.values() if n['role'] == 'contract'):
                raise ValueError('a copied helper may not impersonate a fixed target contract')
        rename = {alias: mapping[key]['name'] for alias, key in aliases.items() if key in mapping}
        helpers = []
        for key, required in dependencies.items():
            node, binding = nodes[key], mapping[key]
            match = DECLARATION.match(node['statement'])
            if not match or match[1] != node['name']:
                raise ValueError('source helper statement/name mismatch')
            statement = node['statement'][:match.start(1)] + binding['name'] + node['statement'][match.end(1):]
            proof = _outside_strings(snapshot['state']['proofs'][key], lambda token: rename.get(token, token))
            helpers.append({'source_ref': self._ref(snapshot, node),
                'node': {'id': binding['id'], 'name': binding['name'], 'role': node['role'],
                         'statement': statement, 'context': deepcopy(node.get('context', [])),
                         'dependencies': [mapping[d]['id'] for d in required],
                         'strategy': 'Recheck copied source helper in this target context.'},
                'proof_body': proof, 'proof_sha256': digest(proof),
                'source_dependencies': required})
        bundle = {'schema': SCHEMA, 'source_ref': deepcopy(source_ref),
                  'target_theory': graph['theory'], 'target_node_id': target_node_id,
                  'target_context_sha256': digest(target.get('context', [])),
                  'mapping': deepcopy(mapping), 'helpers': helpers,
                  'root_helper_id': mapping[source['id']]['id'],
                  'dependency_basis': 'planned + embedded kernel audit when available + explicit source references',
                  'notice': 'Candidate source only. Incomplete dependency recovery is resolved by the target-native checker.'}
        bundle_hash = digest(bundle)
        receipt = {'source_ref': deepcopy(source_ref), 'target_node_id': target_node_id,
                   'mapping': deepcopy(mapping), 'bundle_sha256': bundle_hash}
        return {**bundle, 'bundle_sha256': bundle_hash, 'reuse_request': receipt}


def expand_reuse_helpers(proposal, state, workspace):
    """Expand source-bound candidates, leaving acceptance to prepare/check_batch."""
    if not isinstance(proposal, dict) or proposal.get('action') != 'submit_batch':
        raise ValueError('helper reuse requires a submit_batch action')
    requests = proposal.get('reuse_helpers')
    if not isinstance(requests, list) or not 1 <= len(requests) <= 8:
        raise ValueError('reuse_helpers requires 1-8 prepared bundles')
    out = deepcopy(proposal)
    out.pop('reuse_helpers')
    if 'workspace_reuse_origin' in out:
        raise ValueError('reuse provenance is generated by the harness')
    patch, proofs = out.setdefault('graph', {}), out.setdefault('proofs', {})
    if not isinstance(patch, dict) or not isinstance(proofs, dict):
        raise ValueError('reuse requires object graph/proofs fields')
    updates = patch.setdefault('generated_nodes', [])
    if (not isinstance(updates, list) or not all(isinstance(n, dict)
            and isinstance(n.get('id'), str) and isinstance(n.get('name'), str) for n in updates)):
        raise ValueError('reuse requires generated node objects with string IDs/names')
    occupied_ids = {n['id'] for n in state['graph']['nodes']} | {n.get('id') for n in updates}
    occupied_names = {n['name'] for n in state['graph']['nodes']} | {n.get('name') for n in updates}
    deleted = patch.get('delete_node_ids', [])
    if not isinstance(deleted, list) or not all(isinstance(key, str) for key in deleted):
        raise ValueError('reuse requires a list of deleted node IDs')
    removed = set(deleted)
    by_id = {n['id']: n for n in state['graph']['nodes']}
    origins = []
    for request in requests:
        if not isinstance(request, dict):
            raise ValueError('reuse_helpers entries require a prepared bundle or receipt')
        compact = set(request) == {'source_ref', 'target_node_id', 'mapping', 'bundle_sha256'}
        if not compact and (set(request) != {'bundle'} or not isinstance(request['bundle'], dict)):
            raise ValueError('reuse_helpers entries require a prepared bundle or receipt')
        received = request if compact else request['bundle']
        try:
            expected = workspace.prepare_reuse(received['source_ref'], target_state=state,
                target_node_id=received['target_node_id'], mapping=received['mapping'])
        except KeyError as error:
            raise ValueError('incomplete prepared reuse bundle') from error
        if received != (expected['reuse_request'] if compact else expected):
            raise ValueError('reuse bundle hash/source/context/dependency mismatch')
        consumer = by_id[expected['target_node_id']]
        if consumer['id'] in removed:
            raise ValueError('reuse consumer cannot be deleted in the same batch')
        for helper in expected['helpers']:
            node = helper['node']
            if node['id'] in occupied_ids or node['name'] in occupied_names or node['id'] in proofs or node['id'] in removed:
                raise ValueError('copied helper conflicts with this batch graph/proofs')
            occupied_ids.add(node['id']); occupied_names.add(node['name'])
            updates.append(deepcopy(node)); proofs[node['id']] = helper['proof_body']
        incoming_consumer = next((n for n in updates if n['id'] == consumer['id']), consumer)
        if incoming_consumer.get('context', []) != consumer.get('context', []):
            raise ValueError('reuse consumer context changed in the same batch')
        root_id = expected['root_helper_id']
        if consumer['role'] == 'contract':
            contract_deps = patch.setdefault('contract_dependencies', {})
            if not isinstance(contract_deps, dict): raise ValueError('invalid contract dependency patch')
            dependencies = contract_deps.setdefault(consumer['id'], list(consumer['dependencies']))
            if not isinstance(dependencies, list): raise ValueError('invalid contract dependencies')
            if root_id not in dependencies: dependencies.append(root_id)
        else:
            existing = next((n for n in updates if n['id'] == consumer['id']), None)
            if existing is None:
                existing = {k: deepcopy(v) for k, v in consumer.items() if k != 'consumers'}
                updates.append(existing)
            if root_id not in existing['dependencies']: existing['dependencies'].append(root_id)
        origins.append({'bundle_sha256': expected['bundle_sha256'], 'source_ref': expected['source_ref'],
                        'target_node_id': consumer['id'], 'mapping': expected['mapping'],
                        'source_refs': [h['source_ref'] for h in expected['helpers']],
                        'policy': 'copied_source_requires_target_native_check'})
    out['workspace_reuse_origin'] = origins
    return out
