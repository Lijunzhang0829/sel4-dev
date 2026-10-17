"""Transactional same-theory proof batches; candidates are never accepted implicitly."""
from copy import deepcopy
import hashlib
import re

from regen.execution.proof_commits import digest, durable, freeze_evidence
from regen.execution.isabelle_node_checker import IsabelleChecker, declaration
from regen.planning.proof_graph import (
    build_graph, assert_green_nodes_unchanged, graph_payload_sha256,
)

MODE = 'file-planner-batch-v1'
UNSAFE = re.compile(r"\b(?:ML\w*|SML\w*|setup|local_setup|attribute_setup|method_setup|oracle|axiomatization|sorry|oops|admit|tactic|simproc_setup|use|external_file|theory|context|locale|end|lemma|theorem|corollary|proposition|definition|axioms|declare|lemmas|interpretation|syntax|translations|notation|bundle|unbundle)\b")


def _safe_text(text, *, statement=False):
    # Ignore quoted terms: command words inside propositions are harmless. Raw
    # ML/tactic methods remain forbidden, including cartouche payloads.
    out, i, depth, quoted = [], 0, 0, False
    while i < len(text):
        if depth:
            if text.startswith('(*', i): depth += 1; i += 2
            elif text.startswith('*)', i): depth -= 1; i += 2
            else: i += 1
        elif quoted:
            if text[i] == '\\': i += 2
            elif text[i] == '"': quoted = False; out.append('"'); i += 1
            else: i += 1
        elif text.startswith('(*', i):
            depth = 1; out.append(' '); i += 2
        elif text[i] == '"':
            quoted = True; out.append('"'); i += 1
        else:
            out.append(text[i]); i += 1
    if depth or quoted:
        raise ValueError('unclosed proof comment or string')
    scrubbed = ''.join(out)
    if statement:
        scrubbed = re.sub(r'^\s*(?:lemma|theorem|corollary|proposition)\b', '', scrubbed, count=1)
    # Isabelle's `use facts in method` combinator inside method parentheses is
    # not the top-level file-loading command `use`. Legacy certified proofs use
    # this syntax; keep top-level occurrences forbidden.
    chars = list(scrubbed)
    for match in re.finditer(r"\buse\b", scrubbed):
        before = scrubbed[:match.start()]
        if before.count('(') > before.count(')'):
            chars[match.start():match.end()] = ' ' * (match.end()-match.start())
    scrubbed = ''.join(chars)
    if UNSAFE.search(scrubbed):
        raise ValueError('batch contains an unsafe command')



def prepare_batch(package, state, proposal):
    """Apply a graph PATCH and proof map to an isolated prospective state.

    graph.generated_nodes upserts noncontracts; delete_node_ids removes them.
    contract_dependencies/strategies/routes are partial mappings. No mutation of
    state occurs and this function alone confers no GREEN status.
    """
    if not isinstance(proposal, dict):
        raise ValueError('batch proposal must be an object')
    if not isinstance(proposal.get('reason', ''), str):
        raise ValueError('batch reason must be a string')
    if state['status'] in {'sealed', 'retired'}:
        raise ValueError('terminal theory is immutable')
    if set(proposal) - {'graph', 'proofs', 'reason'}:
        raise ValueError('unknown batch proposal fields')
    patch = proposal.get('graph', {})
    if not isinstance(patch, dict):
        raise ValueError('graph patch must be an object')
    for field in ('contract_dependencies', 'contract_strategies', 'contract_routes'):
        if not isinstance(patch.get(field, {}), dict):
            raise ValueError(field + ' must be an object')
    if not isinstance(patch.get('delete_node_ids', []), list) or not all(
            isinstance(key, str) for key in patch.get('delete_node_ids', [])):
        raise ValueError('delete_node_ids must be a list of IDs')
    if not isinstance(patch.get('generated_nodes', []), list) or not all(
            isinstance(node, dict) for node in patch.get('generated_nodes', [])):
        raise ValueError('generated_nodes must be a list of objects')
    if set(patch) - {'generated_nodes', 'delete_node_ids', 'contract_dependencies', 'contract_strategies', 'contract_routes'}:
        raise ValueError('unknown graph patch fields')
    old = state['graph']
    generated = {n['id']: deepcopy(n) for n in old['nodes'] if n['role'] != 'contract'}
    removed = patch.get('delete_node_ids', [])
    if removed and not proposal.get('reason', '').strip():
        raise ValueError('deletion requires a reason')
    for key in removed:
        if key not in generated:
            raise ValueError('only existing generated nodes may be deleted')
        del generated[key]
    updates = patch.get('generated_nodes', [])
    if len({n['id'] for n in updates}) != len(updates):
        raise ValueError('duplicate generated node updates')
    for node in updates:
        if node['role'] == 'contract' or node['id'] in removed:
            raise ValueError('invalid generated node update')
        if not isinstance(node['statement'], str):
            raise ValueError('generated statement must be a string')
        _safe_text(node['statement'], statement=True)
        generated[node['id']] = deepcopy(node)
    contracts = {n['id']: n for n in old['nodes'] if n['role'] == 'contract'}
    plan = {'generated_nodes': list(generated.values())}
    for field, value in [('contract_dependencies', 'dependencies'), ('contract_strategies', 'strategy')]:
        if set(patch.get(field, {})) - contracts.keys():
            raise ValueError('unknown fixed contract in graph patch')
        plan[field] = {key: deepcopy(n.get(value, [] if value == 'dependencies' else '')) for key, n in contracts.items()}
        plan[field].update(deepcopy(patch.get(field, {})))
    plan['contract_routes'] = {**old.get('contract_routes', {}), **patch.get('contract_routes', {})}
    graph = build_graph(package, plan, run_id=old['run_id'], revision=old['revision'] + 1)
    assert_green_nodes_unchanged(old, graph, set(state['proofs']))
    incoming = proposal.get('proofs', {})
    nodes = {n['id']: n for n in graph['nodes']}
    if not isinstance(incoming, dict) or set(incoming) - nodes.keys():
        raise ValueError('proof map contains unknown nodes')
    if set(incoming) & state['proofs'].keys():
        raise ValueError('accepted proofs may not be replaced')
    proofs = deepcopy(state['proofs'])
    order = list(state['accepted_order'])
    for key in graph['execution_order']:
        if key not in incoming:
            continue
        if not isinstance(incoming[key], str) or not incoming[key].strip():
            raise ValueError('empty/non-string proof')
        _safe_text(incoming[key])
        declaration(nodes[key], incoming[key])  # reject admission/command injection
        if not set(nodes[key]['dependencies']) <= proofs.keys():
            raise ValueError(f'unproved batch prerequisite: {key}')
        proofs[key] = incoming[key]
        order.append(key)
    graph.update(context_policy='committed-prefix-v1', accepted_order=order,
                 initial_graph_owner='File Planner', runtime_graph_owner='File Planner',
                 execution_mode=MODE)
    graph['graph_payload_sha256'] = graph_payload_sha256(graph)
    candidate = deepcopy(state)
    candidate.update(graph=graph, proofs=proofs, accepted_order=order)
    return candidate


def _binding(state):
    return digest({k: state[k] for k in ('graph', 'proofs', 'accepted_order')})


def _render(graph, proofs, imports, label):
    # Reuse the authoritative declaration/context renderer without subprocesses.
    renderer = object.__new__(IsabelleChecker)
    renderer.imports = lambda: imports
    return renderer._source(graph, proofs, theory_name=label, diagnostic_markers=True)


def _declarations(state):
    return {n['id']: (n['name'], n['statement'], n.get('context', [])) for n in state['graph']['nodes']}


def _batch_declaration_coverage(old, new):
    """Native proof source can certify changed declarations only when it proves them."""
    before, after = _declarations(old), _declarations(new)
    changed = {key for key, value in after.items() if before.get(key) != value}
    additions = set(new['proofs']) - set(old['proofs'])
    if before == after or not additions or not changed <= additions:
        return None
    return {'scheme':'proof-batch-declarations-v1', 'changed_ids':sorted(changed),
            'declarations_sha256':digest(after)}


from regen.execution.check_executor import native_operation


def check_declarations(checker, index):
    """Use the document checker API, keeping old checker adapters replayable."""
    method = getattr(checker, 'check_declarations', None)
    if index.get('declaration_index_version') == 1 and callable(method):
        return method(index)
    return checker.check_graph(index)


@native_operation
def check_batch(checker, old, candidate):
    """Check every declaration, then all accepted and proposed proofs in one run.

    Declaration checks are not proof evidence. Failed batches return evidence
    for feedback but validate_batch_commit will reject them.
    """
    coverage = _batch_declaration_coverage(old, candidate)
    graph_check = (freeze_evidence(check_declarations(checker, candidate['graph']))
                   if _declarations(old) != _declarations(candidate) and coverage is None else None)
    evidence = {'schema_version': 1, 'old_binding': _binding(old),
                'new_binding': _binding(candidate), 'graph_check': graph_check,
                'imports': checker.imports(), 'label': 'Batch_Check_' + digest(checker.imports())[:16]}
    if coverage is not None:
        evidence['declaration_coverage'] = coverage
    additions = set(candidate['proofs']) - set(old['proofs'])
    if graph_check is not None and graph_check.get('verdict') != 'green':
        evidence['check'] = graph_check
        from regen.planning.batch_routes import node_results
        evidence['node_results'] = node_results(old, candidate, evidence)
        return evidence
    if additions:
        import time
        batch_started = time.monotonic()
        candidate_timeout = getattr(checker, 'batch_candidate_timeout', 120)
        source = _render(candidate['graph'], candidate['proofs'], evidence['imports'], evidence['label'])
        result = None
        if getattr(checker, 'batch_incremental', False):
            from regen.execution.batch_checker import BatchWorker, body
            import time
            started = time.monotonic()
            # Input validation is not an infrastructure failure and must never
            # be hidden by a worker recovery classification.
            for node in candidate['graph']['nodes']:
                if node['id'] in candidate['proofs']:
                    _safe_text(node['statement'], statement=True)
                    _safe_text(candidate['proofs'][node['id']])
            prefix_source = _render(old['graph'], old['proofs'], evidence['imports'], evidence['label'])
            failure_stage = 'prefix-startup-or-alignment'
            try:
                worker = getattr(checker, '_batch_worker', None)
                if (worker is None or worker.closed or worker.process.poll() is not None
                        or worker.imports != evidence['imports']
                        or not worker.can_align(body(prefix_source))):
                    if worker is not None:
                        worker.close()
                    names = {n['id']: n['name'] for n in old['graph']['nodes']}
                    checker._batch_worker = None
                    worker = BatchWorker(checker, prefix_source,
                        [names[k] for k in old['accepted_order']], evidence['imports'])
                    checker._batch_worker = worker
                worker.align(body(prefix_source))
                prepare_ms = round((time.monotonic()-started)*1000)
                names = {n['id']:n['name'] for n in candidate['graph']['nodes']}
                failure_stage = 'candidate-execution-or-audit-transport'
                candidate_started = time.monotonic()
                result = worker.check_batch(source,
                    [names[k] for k in candidate['accepted_order'] if k in additions])
                result['candidate_execution_ms'] = round((time.monotonic()-candidate_started)*1000)
                result['prefix_prepare_ms'] = prepare_ms
                result['execution_wall_ms'] = round((time.monotonic()-started)*1000)
                receipt = result.get('native_receipt')
                if receipt is not None:
                    receipt['prefix_prepare_ms'] = prepare_ms
                    receipt['candidate_execution_ms'] = result['candidate_execution_ms']
                    receipt['wall_ms'] = result['execution_wall_ms']
            except TimeoutError as error:
                worker = getattr(checker, '_batch_worker', None)
                if worker is not None:
                    worker.close()
                checker._batch_worker = None
                path = checker.work_dir / (evidence['label'] + '_startup_timeout.thy')
                path.write_text(source)
                log = path.with_suffix('.log')
                log.write_text(getattr(error, 'worker_log', '') + '\n' + str(error))
                scope = getattr(error, 'timeout_scope', 'prefix-startup')
                result = {'verdict':'infrastructure_error' if scope == 'prefix-startup' else 'red',
                    'failure_scope':'native_preparation' if scope == 'prefix-startup' else 'candidate',
                    'returncode':124, 'timed_out':True,
                    'diagnostic':str(error), 'source':str(path), 'log':str(log),
                    'timeout_scope':getattr(error, 'timeout_scope', 'prefix-startup'),
                    'timeout_seconds':getattr(error, 'timeout_seconds', checker.timeout),
                    'wall_ms':round((time.monotonic()-started)*1000),
                    'incremental':'audited-batch-worker-v1',
                    'check_scope':'committed-prefix-and-incremental-batch',
                    'prefix_sha256':hashlib.sha256(body(prefix_source).encode()).hexdigest(),
                    'delta_sha256':hashlib.sha256(body(source)[len(body(prefix_source)):].encode()).hexdigest()}
            except (RuntimeError, OSError, ValueError) as error:
                # A failed incremental transport is not a mathematical RED.
                # Running the full prefix cold under the delta's candidate
                # deadline changes the check contract and can fabricate a
                # proof timeout. Preserve evidence and let the existing
                # NativePreparationError boundary pause for infrastructure.
                from uuid import uuid4
                from regen.execution.check_tasks import NativePreparationError
                worker = getattr(checker, '_batch_worker', None)
                native_log = getattr(error, 'worker_log', '')
                worker_root = getattr(worker, 'root', None)
                log_error = None
                if not native_log and worker_root is not None:
                    try:
                        native_log = (worker_root/'worker.log').read_text()
                    except OSError as read_error:
                        log_error = str(read_error)
                # Cleanup must run even when disk exhaustion prevents saving a
                # receipt. Failed cleanup retains the owned worker for retry.
                cleanup_error = None
                if worker is not None:
                    worker.pending = None
                    try:
                        worker.close()
                    except Exception as close_error:
                        cleanup_error = close_error
                    else:
                        checker._batch_worker = None
                else:
                    checker._batch_worker = None
                directory = checker.work_dir / ('incremental-failure-' + uuid4().hex)
                path, log = directory/'candidate.thy', directory/'native.log'
                result = {'verdict':'infrastructure_error', 'failure_scope':'native_preparation',
                    'native_failure_stage':failure_stage, 'error_type':type(error).__name__,
                    'returncode':1, 'timed_out':False, 'diagnostic':str(error),
                    'source':str(path), 'log':str(log), 'native_log_read_error':log_error,
                    'wall_ms':round((time.monotonic()-started)*1000),
                    'incremental':'audited-batch-worker-v1',
                    'check_scope':'committed-prefix-and-incremental-batch',
                    'prefix_sha256':hashlib.sha256(body(prefix_source).encode()).hexdigest(),
                    'delta_sha256':hashlib.sha256(body(source)[len(body(prefix_source)):].encode()).hexdigest()}
                try:
                    directory.mkdir()
                    path.write_text(source)
                    log.write_text(native_log + '\n' + type(error).__name__ + ': ' + str(error))
                    durable(directory/'failure.json', freeze_evidence(result), immutable=True)
                    if cleanup_error is not None:
                        durable(directory/'cleanup-failure.json', {
                            'error_type':type(cleanup_error).__name__, 'message':str(cleanup_error)}, immutable=True)
                except OSError as receipt_error:
                    failure = NativePreparationError(str(error) + '; cannot save infrastructure evidence: ' + str(receipt_error))
                    if cleanup_error is not None:
                        failure.add_note('worker cleanup failed: ' + str(cleanup_error))
                    raise failure from receipt_error
                if cleanup_error is not None:
                    failure = NativePreparationError(str(error) + '; worker cleanup failed: ' + str(cleanup_error))
                    failure.add_note('incremental failure evidence: ' + str(directory/'failure.json'))
                    raise failure from cleanup_error
        if result is None:
            result = checker._check(source, evidence['label'], timeout=candidate_timeout)
            result['timed_out'] = result.get('returncode') == 124
            result['timeout_scope'] = 'cold-candidate-including-prefix' if result['timed_out'] else None
            result['timeout_seconds'] = candidate_timeout
            result['check_scope'] = 'accepted-prefix-and-batch'
            result['incremental'] = False
        if result.get('native_receipt') is None:
            # Prefix startup and transport failures have no successful worker
            # transaction, but their saved diagnostic still needs a bound
            # receipt. This metadata cannot authorize GREEN acceptance.
            from pathlib import Path
            from regen.execution.native_check_receipt import native_check_receipt
            log_path = Path(result['log']) if result.get('log') else None
            if log_path is not None and log_path.is_file():
                result['native_receipt'] = native_check_receipt(
                    checker, result, source=source,
                    scope='committed-prefix-and-incremental-batch'
                        if result.get('incremental') else 'cold-full-theory',
                    log_text=log_path.read_bytes(),
                    prefix_sha256=result.get('prefix_sha256'),
                    delta_sha256=result.get('delta_sha256'),
                    prefix_prepare_ms=result.get('prefix_prepare_ms'),
                    candidate_execution_ms=result.get('candidate_execution_ms'))
        result['execution_wall_ms'] = round((time.monotonic()-batch_started)*1000)
        if result.get('native_receipt') is not None:
            result['native_receipt']['timeout_scope'] = result.get('timeout_scope')
            result['native_receipt']['timeout_seconds'] = result.get('timeout_seconds')
        evidence['check'] = freeze_evidence(result)
    else:
        # Declaration-only changes still need concrete native evidence for commit.
        if graph_check is None:
            graph_check = freeze_evidence(check_declarations(checker, candidate['graph']))
            evidence['graph_check'] = graph_check
        evidence['check'] = graph_check
    from regen.planning.batch_routes import node_results
    evidence['node_results'] = node_results(old, candidate, evidence)
    return evidence


def _green(check):
    if check.get('verdict') != 'green' or check.get('returncode') != 0:
        raise ValueError('batch checker did not succeed')
    for key in ('source', 'log'):
        content = check.get(key + '_content')
        if not isinstance(content, str) or hashlib.sha256(content.encode()).hexdigest() != check.get(key + '_sha256'):
            raise ValueError('batch checker evidence bytes mismatch')
    if not check['source_content']:
        raise ValueError('empty checker source')


def validate_batch_commit(old, new, evidence):
    """Replay-time binding check; declaration/state invariants remain caller-owned."""
    if old is None or evidence.get('old_binding') != _binding(old) or evidence.get('new_binding') != _binding(new):
        raise ValueError('batch state binding mismatch')
    assert_green_nodes_unchanged(old['graph'], new['graph'], set(old['proofs']))
    if new['accepted_order'][:len(old['accepted_order'])] != old['accepted_order'] or any(new['proofs'].get(k) != v for k, v in old['proofs'].items()):
        raise ValueError('batch changed accepted prefix')
    graph_check = evidence.get('graph_check')
    coverage = evidence.get('declaration_coverage')
    if coverage is not None and (graph_check is not None
            or coverage != _batch_declaration_coverage(old, new)):
        raise ValueError('invalid proof-batch declaration coverage')
    if graph_check is not None:
        _green(graph_check)
        renderer = object.__new__(IsabelleChecker)
        renderer.imports = lambda: evidence['imports']
        # Old document journals and checker adapters used Graph_Check labels.
        # Accept only exact renderings of the candidate for the allowed names;
        # this does not relax statement, context, imports or byte binding.
        index = new['graph']
        labels = [f"Graph_Check_v{index['revision']}"]
        if index.get('declaration_index_version') == 1:
            labels.append(f"Declaration_Check_v{index['revision']}")
        expected_sources = [renderer._source(index, {}, theory_name=label,
                            statements_only=True) for label in labels]
        if graph_check['source_content'] not in expected_sources:
            raise ValueError('declaration checker source mismatch')
    elif _declarations(old) != _declarations(new) and coverage is None:
        raise ValueError('changed declarations lack native evidence')
    _green(evidence['check'])
    additions = set(new['proofs']) - set(old['proofs'])
    if additions:
        source = _render(new['graph'], new['proofs'], evidence['imports'], evidence['label'])
        if evidence['check']['source_content'] != source:
            raise ValueError('batch checker source does not exactly match candidate')
        from regen.execution.batch_audit import PROTOCOL, validate_native_audit
        protocol = evidence['check'].get('incremental')
        if protocol not in (None, False, 'audited-batch-worker-v1', PROTOCOL):
            raise ValueError('unsupported incremental batch evidence protocol')
        if protocol in ('audited-batch-worker-v1', PROTOCOL):
            from regen.execution.batch_checker import body
            prefix = body(_render(old['graph'], old['proofs'], evidence['imports'], evidence['label']))
            full = body(source)
            check = evidence['check']
            names = {n['id']:n['name'] for n in new['graph']['nodes']}
            expected = [names[k] for k in new['accepted_order'] if k in additions]
            if (not full.startswith(prefix)
                    or check.get('prefix_sha256') != hashlib.sha256(prefix.encode()).hexdigest()
                    or check.get('delta_sha256') != hashlib.sha256(full[len(prefix):].encode()).hexdigest()
                    or check.get('audited_facts') != expected):
                raise ValueError('incremental batch source/audit binding mismatch')
            if protocol == PROTOCOL:
                validate_native_audit(check, prefix=prefix, delta=full[len(prefix):],
                    names=expected, imports=evidence['imports'], label=evidence['label'])
            elif any('REGEN_BATCH_CLEAN '+name not in check['log_content'].splitlines() for name in expected):
                # Preserve historical v1 journal validation without promoting its logs to v2.
                raise ValueError('legacy incremental batch lacks audit markers')
    elif new['proofs'] != old['proofs']:
        raise ValueError('declaration-only batch changed proofs')
    seen = set()
    nodes = {n['id']: n for n in new['graph']['nodes']}
    for key in new['accepted_order']:
        if not set(nodes[key]['dependencies']) <= seen:
            raise ValueError('batch proof order violates prerequisites')
        seen.add(key)
