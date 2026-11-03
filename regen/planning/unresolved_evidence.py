"""Versioned, evidence-bound permission to stop one unsuccessful fixed goal.

This is a minimum exploration gate, not evidence of unprovability. Only failed
formal candidates bound to the accepted history count; model narratives and
conditional/helper checks do not. Reads never start Isabelle or create artifacts.
"""
from collections import Counter
import hashlib
from pathlib import Path
import re

from regen.execution.proof_commits import digest


POLICY = 'native-attempts-v1'
MINIMUM_CANDIDATES = 2
INSTRUCTIONS = '''UNRESOLVED EVIDENCE POLICY: native-attempts-v1.
report_unresolved selects exactly one pending fixed contract. Before stopping it,
submit an original formal proof attempt and a different repaired candidate, and
obtain native failures actually attributed to that contract in the current
context. The harness verifies persisted evidence automatically; prose, repeated
or cached proofs, helper failures, unexecuted batch entries, conditional sketches,
timeouts and infrastructure/transport failures do not satisfy this gate. Inspect
unresolved_eligibility to see missing evidence. A rejection preserves every goal:
repair the candidate or continue another pending contract. UNKNOWN never means
false or unprovable. Do not discard other goals because this one is difficult.
'''


def strict_policy_enabled(run):
    from regen.execution.checker_policy import validate_unresolved_evidence_policy
    return validate_unresolved_evidence_policy(run) == POLICY


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _candidate_identity(proof):
    # Remove cosmetic whitespace/comments while preserving quoted Isabelle
    # strings. Token changes are a different candidate, not certified progress.
    from regen.preparation.contract_extractor import strip_nested_comments
    clean = strip_nested_comments(proof)
    return digest(re.findall(r'"(?:\\.|[^"\\])*"|\\<[^>]+>|[A-Za-z0-9_\']+|[^\s]', clean))


def _node_identity(node):
    return {key:node.get(key) for key in ('id', 'name', 'role', 'statement', 'context')}


def _prefix(nodes, proofs, target):
    result = []
    for node in nodes:
        if node['id'] == target:
            return result
        if node['id'] in proofs:
            result.append({'entry':_node_identity(node), 'proof_sha256':digest(proofs[node['id']])})
    raise ValueError('fixed contract absent from document')


def _current_document(controller, package, state):
    from regen.execution.import_revisions import effective_imports, native_imports
    from regen.execution.repository_execution import read
    from regen.planning.theory_document import parse, render
    directory = Path(controller.root)/'theories'/Path(package['file_slot']).stem
    initial = read(directory/'imports.json') if (directory/'imports.json').exists() else []
    imports = native_imports(controller.run, effective_imports(state, initial))
    path = directory/'document'/'draft.thy'
    source = path.read_text() if path.exists() else render(package, state, imports)
    nodes, proofs = parse(source, package, imports)
    return directory, imports, nodes, proofs


def _attempt(controller, package, state, directory, imports, nodes, proofs, target, history):
    from regen.infrastructure.incremental_log import read_artifact
    from regen.planning.attempt_views import failed_entries
    from regen.planning.planner_efficiency import deterministic_red
    from regen.planning.planner_workspace import attempt_evidence
    from regen.planning.retrieval_usage import target_identity
    from regen.execution.proof_batches import _render
    number = history['request_number']
    proposal, feedback = attempt_evidence(directory, state, number)
    if proposal.get('action') != 'submit_batch' or feedback.get('status') != 'rejected_batch':
        return None, 'not_formal_failure'
    if feedback.get('cached_failure') or history.get('cached_failure'):
        return None, 'cached_failure'
    if target not in failed_entries(feedback.get('node_results', [])):
        return None, 'target_not_failed'
    if target not in feedback.get('checked_delta_ids', []):
        return None, 'target_not_executed'
    check = feedback.get('check', {})
    if (check.get('failure_scope') or check.get('timed_out')
            or check.get('returncode') == 124 or not deterministic_red(check)):
        return None, 'not_native_proof_failure'
    node = next(n for n in nodes if n['id'] == target)
    binding = history.get('attempt_binding', {})
    if (binding.get('context_revision') != state.get('context_revision', 0)
            or binding.get('context_imports') != state.get('context_imports')
            or binding.get('provider_bindings_sha256') != digest(state.get('context_provider_bindings'))
            or binding.get('target_sha256', {}).get(target) != target_identity(node)):
        return None, 'stale_statement_or_context'
    receipt = check.get('native_receipt', {})
    if (receipt.get('schema') != 'native-check-receipt-v1'
            or not receipt.get('request_id')
            or receipt.get('check_scope') not in {'cold-full-theory', 'committed-prefix-and-incremental-batch'}
            or receipt.get('returncode') != 1 or receipt.get('verdict') != 'red'):
        return None, 'missing_native_receipt'
    source, log = check.get('source_content'), check.get('log_content')
    if (not isinstance(source, str) or not isinstance(log, str)
            or receipt.get('source_sha256') != _sha(source)
            or receipt.get('log_sha256') != _sha(log)
            or check.get('source_sha256') != _sha(source)
            or check.get('log_sha256') != _sha(log)):
        return None, 'native_receipt_binding_mismatch'
    started = re.findall(r'^REGEN_NODE (.+)$', log, re.M)
    ended = set(re.findall(r'^REGEN_TIMING_END (\S+) ', log, re.M))
    if not started or started[-1] != target or target in ended:
        return None, 'native_failure_not_at_target'
    # candidate.json is not trusted by itself: reconstructing the actual native
    # input binds its declarations/proofs/order to the journal-bound feedback.
    candidate = read_artifact(directory/'batch-requests'/f'{number:06d}'/'candidate.json')
    rendered = _render(candidate['graph'], candidate['proofs'], imports,
                       'Batch_Check_' + digest(imports)[:16])
    if rendered != source:
        return None, 'native_candidate_binding_mismatch'
    candidate_nodes = {n['id']:n for n in candidate['graph']['nodes']}
    if (_node_identity(candidate_nodes.get(target, {})) != _node_identity(node)
            or candidate['graph'].get('run_id') != state['graph'].get('run_id')):
        return None, 'candidate_target_binding_mismatch'
    proof = candidate['proofs'].get(target)
    if not isinstance(proof, str) or proposal.get('proofs', {}).get(target) != proof:
        return None, 'candidate_proof_binding_mismatch'
    ordered = [candidate_nodes[key] for key in candidate['accepted_order']]
    previous_prefix = _prefix(ordered, candidate['proofs'], target)
    current_prefix = _prefix(nodes, proofs, target)
    # New facts before the target change its available proof context too.
    # Work after the target does not affect either prefix and remains harmless.
    if current_prefix != previous_prefix:
        return None, 'stale_prefix'
    return {'request_number':number, 'proposal_sha256':history['proposal_sha256'],
            'feedback_sha256':history['feedback_sha256'], 'proof_sha256':digest(proof),
            'candidate_identity':_candidate_identity(proof),
            'native_request_id':receipt['request_id'], 'native_source_sha256':_sha(source),
            'statement_context_sha256':target_identity(node),
            'prefix_sha256':digest(previous_prefix)}, None


def eligibility_view(controller, entry_id=None):
    enabled = strict_policy_enabled(controller.run)
    package, state = controller._state(controller.theory)
    fixed = {c['contract_id'] for c in package['contracts']}
    if entry_id is None:
        return {'policy':POLICY if enabled else None, 'minimum_distinct_candidates':MINIMUM_CANDIDATES,
                'notice':'Read one fixed entry to inspect its bound failure evidence.'}
    base = {'policy':POLICY if enabled else None, 'entry_id':entry_id, 'eligible':False,
            'minimum_distinct_candidates':MINIMUM_CANDIDATES, 'qualifying_candidates':0,
            'missing_candidates':MINIMUM_CANDIDATES, 'attempts':[],
            'notice':'Minimum exploration evidence only, never proof of unprovability.'}
    if not enabled:
        return {**base, 'reason':'legacy_policy'}
    if entry_id not in fixed:
        return {**base, 'reason':'not_fixed_contract'}
    if entry_id in state.get('proofs', {}):
        return {**base, 'reason':'already_proved'}
    from regen.planning.theory_outcomes import current_dispositions
    if entry_id in current_dispositions(controller):
        return {**base, 'reason':'already_disposed'}
    try:
        directory, imports, nodes, proofs = _current_document(controller, package, state)
    except (OSError, ValueError, KeyError) as error:
        return {**base, 'reason':'current_context_unavailable', 'detail':str(error)}
    rejected, attempts, candidates, native_ids = Counter(), [], set(), set()
    for history in state.get('batch_history', []):
        if history.get('action') != 'submit_batch' or history.get('status') != 'rejected_batch':
            continue
        if entry_id not in history.get('proof_node_ids', []):
            continue
        try:
            attempt, reason = _attempt(controller, package, state, directory, imports,
                                       nodes, proofs, entry_id, history)
        except (OSError, ValueError, KeyError, TypeError):
            rejected['invalid_or_unavailable_attempt'] += 1
            continue
        if reason:
            rejected[reason] += 1
            continue
        if attempt['candidate_identity'] in candidates or attempt['native_request_id'] in native_ids:
            rejected['duplicate_candidate_or_native_request'] += 1
            continue
        candidates.add(attempt['candidate_identity']); native_ids.add(attempt['native_request_id'])
        attempts.append(attempt)
    count = len(attempts)
    return {**base, 'eligible':count >= MINIMUM_CANDIDATES,
            'reason':'eligible' if count >= MINIMUM_CANDIDATES else 'insufficient_native_attempts',
            'qualifying_candidates':count, 'missing_candidates':max(0, MINIMUM_CANDIDATES-count),
            'attempts':attempts, 'excluded_attempts':dict(rejected),
            'context_binding':{'run_id':controller.run['run_id'], 'theory':controller.theory,
                'context_revision':state.get('context_revision', 0), 'native_imports':imports,
                'provider_bindings_sha256':digest(state.get('context_provider_bindings'))}}


def require_unresolved_evidence(controller, entry_ids):
    if (not isinstance(entry_ids, list) or len(entry_ids) != 1
            or not isinstance(entry_ids[0], str)):
        raise ValueError('native-attempts-v1: select exactly one pending fixed contract; '
                         'other contracts must remain available for independent work')
    view = eligibility_view(controller, entry_ids[0])
    if not view['eligible']:
        raise ValueError('native-attempts-v1: unresolved evidence rejected for ' + entry_ids[0]
            + f" ({view['reason']}; {view['qualifying_candidates']}/2 distinct native failures). "
            'Read this entry\'s unresolved_eligibility and its native diagnostics; submit an original '
            'formal candidate and a different repaired candidate for this target. Cached results, '
            'helper failures, unreached entries, stale contexts and timeouts do not count. '
            'You may continue another pending contract. No disposition was recorded.')
    return {'policy':POLICY, 'minimum_distinct_candidates':MINIMUM_CANDIDATES,
            'entry_id':entry_ids[0], 'context_binding':view['context_binding'],
            'attempts':view['attempts'], 'truth_status':'undetermined'}
