"""Shared acceptance engine for document versions and legacy graph batches.

All authority stays in the existing acceptance journal. Model calls have no shell
access. The controller can be restarted; requests and feedback are committed
before another call. Each active theory has one serial controller; the coordinator owns run mutation.
"""
from copy import deepcopy
from difflib import SequenceMatcher
import json
import hashlib
import logging
import time
from uuid import uuid4
import re
from pathlib import Path

from regen.execution.proof_commits import digest, durable, freeze_evidence
from regen.infrastructure.llm_transcript import allocate_artifact_prefix, call_llm, validate_call_artifact
from regen.execution.check_tasks import NativePreparationError
from regen.planning.proof_graph import build_graph, parse_json_response
from regen.planning.attempt_memory import compact_target_memory, target_attempts


INSTRUCTIONS = '''You are the continuing File Planner for ONE Isabelle theory. Plan and revise its
local graph, manage helpers, generate proofs, and use Isabelle feedback to continue.
Return one JSON action per call. No shell, arbitrary files or external tools.

Fixed contract statements/contexts and GREEN statements/proofs/accepted order are
immutable. Other auxiliary nodes may be created, replaced or deleted; repair their
consumer dependencies in the same patch. Unproved goals are never premises.
Exploratory candidates and uncertain helper statements are allowed: Isabelle decides
whether they are valid. Missing recalled lemmas or examples do not prohibit trying
a proof or decomposition. Do not seek removed historical target proofs.

Use the available feedback to choose the next useful proof trial. Read or query
when specific missing information would help; no preliminary read is required if
the needed candidate, statement or feedback is already present. Input references
point to an included canonical object. observation_inventory identifies visible
diagnostic ranges; target_memory and recent_actions provide navigation. Incomplete
excerpts are not full proof states. Historical evidence remains available on demand.
Any action may include "notes":"current approach, observations and next step" and
"workspace":{"focus_node_ids":["current, attempted, or same-batch new helper ID"],
"record_ids":["previously recalled IDs"],"summary":"route, obstacle and next check"}.
Selections persist until replaced. Query/route/cost signals and batch opportunities
are advisory. No iteration quota applies. After helper success, use consumer_feedback
to test its consumer or explain the remaining decomposition; planned dependencies
are not actual proof citations.

Proof actions:
{"action":"submit_batch","reason":"what this batch accomplishes",
"graph":{"generated_nodes":[{"id":"new or existing auxiliary ID","name":"regen_name",
"role":"helper|theorem","statement":"exact lemma declaration","context":[],
"dependencies":[],"strategy":"proof approach"}],"delete_node_ids":[],
"contract_dependencies":{"actual contract ID":[]},
"contract_strategies":{"actual contract ID":"approach"}},
"proofs":{"actual node ID":"complete Isabelle proof body"}}
Graph is a PATCH: omitted nodes/maps remain unchanged; omit graph if unchanged.
New/changed auxiliary declarations require the listed fields and an allowed
contract/support context. Do not put fixed contract declarations in graph. Helpers
need an intended consumer or explicit role. Add dependencies for new helpers used
by consumers. Empty proofs permits a graph-only edit, which establishes no theorem.
Prefer coherent batches of related helpers/consumers or simple independent goals.
Proofs run in topological order; later proofs may cite earlier proofs in the same
batch without a separate GREEN round. No sorry/oops/axioms, hidden declarations or
context-changing proof commands. Only an entirely successful batch is committed.
Failed graph/proof edits remain drafts; revise against the CURRENT accepted graph.
node_results.completed_uncommitted is not GREEN and requires separate checking.
To reuse exact historical bodies without retyping, include
"reuse_proofs":[{"request_number":81,"node_ids":["historical node ID"]}]
in submit_batch ("proofs":{} is allowed). The harness expands bound proof bodies
and missing selected declarations, then validates normally. May combine with new
proofs; duplicate or already GREEN selections are rejected. No automatic partial
acceptance. Preserve GREEN work; revise candidates in response to their feedback.
To edit a recorded draft without regenerating its unchanged branches, use
"proof_edits":[{"request_number":81,"node_id":"exact ID","proof_sha256":"hash from next_proof_observation",
"edits":[{"old":"unique exact old text","new":"replacement"}]}] in submit_batch.
Replacements apply sequentially and each old text must occur exactly once. The
expanded FULL proof is validated; unchanged draft text is not automatically GREEN.
Use named Isar have/show subproofs or explicit helper nodes to isolate independent
obligations. Stable helper proofs may be checked and retained separately. Avoid
repeatedly repairing positional apply/defer chains when goal order is unstable.
Same-context deterministic RED candidates return the original failure without a
new native check. An explicit "retry_check":"reason" requests a rerun. Timeouts
are never treated as deterministic RED. repeated_failed_routes shows prior loops;
revise the candidate, decomposition or relevant context instead of cycling them.
next_proof_observation includes available small complete residuals and proof hashes.
Use that feedback directly; batch genuinely missing observations into observe.

Observation actions (all may be used alone or within observe):
{"action":"observe","reads":[{"action":"query","request":{"premise_queries":[
{"query":"fact or constant","mode":"exact","scope":"all"}]}},
{"action":"read_attempts","request_numbers":[39]}]}
Use 1-8 independent read-only items; multiple items of the same kind are allowed.
Items see the same accepted snapshot and cannot depend on an earlier item's output.
Each receives an item_index and separate success/error; successful selections are
retained together. Nested observe and proof execution are not observation items.

{"action":"query","request":{"premise_queries":[{"query":"ONE fact name or constant",
"mode":"exact|definition|search|auto","scope":"all|upstream|sealed","offset":0}],
"context_node_id":"actual graph node ID"}}
Use 1-4 focused premise_queries. exact takes one fact identifier; definition takes
one constant. search matches all words, so query independent constants separately.
Omit context_node_id for global context. Repeated/paged queries are allowed; use a
changed question/scope when earlier retrieval was unhelpful.
{"action":"query","request":{"goal_query":true,"context_node_id":"actual node ID",
"purpose":"helper|proof_example","scope":"all|upstream|sealed"}}
The harness supplies the entry goal's complete statement/context. For an observed
intermediate goal, replace goal_query with
"proof_state_query":{"request_number":94,"goal_index":1,"origin":"diagnostic|before_command"}.
context_node_id must be the submitted node; multi-node attribution must be unambiguous.
No model-supplied proof-state text. helper ranks external facts; proof_example returns
imported sealed generated proofs with contexts. Optional "proof_hint" specifies a
proof-example approach; "use_vectors":true uses the existing helper vector index.
Retrieval similarity and symbol availability do not establish applicability: check
full statements, types and locale assumptions. Upstream proof bodies are not stored.
Current-theory GREEN proofs are workspace material, available through read_proofs.

{"action":"read_facts","record_ids":["previously recalled ID"]}: 1-24 complete facts.
{"action":"read_proofs","node_ids":["GREEN ID"]}: 1-4 accepted declarations/contexts/proofs.
{"action":"read_attempts","request_numbers":[39],"view":"summary"}: 1-4 verified historical summaries, not complete proofs.
Use "view":"full" for exact original candidate/context/feedback. Do not infer missing propositions from summaries.
{"action":"read_queries","query_ids":["query_index ID"]}: 1-4 complete query summaries.
{"action":"read_diagnostic","request_number":39,"path":["check","diagnostic"],
"offset":0,"limit":8000}: exact original character page, maximum 12000; next_offset continues.
{"action":"read_proof_state","request_number":94}: bound residuals and native before-command
state where available; these are distinct observations, never proved premises.
Add "goal_indices":[1,2] to retrieve 1-4 complete diagnostic goals (including printed
hypotheses) in one read, up to 60000 characters; larger states remain pageable.
Use "origin":"before_command" with goal_indices for exact native before-command
goals and their local assumptions, or "origin":"diagnostic" (the default) for
diagnostic residuals. These are different states; unavailable origins never silently
fall back to another state. Small complete states are returned directly with check feedback.
{"action":"resolve_symbols","names":["constant or abbreviation"],"context_node_id":"current node ID"}:
Resolve 1-16 term identifiers in frozen imports and the node's declaration context;
returns term expansions and types, not theorem statements. This does not query the
fact namespace: an unresolved term does not mean a lemma is absent. For lemmas use
query with mode=exact and read_facts for recalled records; catalog misses likewise
do not establish absence from Isabelle. Current-theory GREEN facts use read_proofs.

{"action":"finish"}: request full theory check, dependency audit and seal after all
active nodes are proved. Remove unused unproved auxiliaries when appropriate.
A GREEN batch is not a sealed theory; failed seal feedback requires further work.
{"action":"pause","reason":"concrete unresolved blocker"}: hand control back for an
external missing input/tool failure or stalled mathematical exploration. Identify
the target, actual candidate/checker feedback and decompositions tried or considered.
Weak retrieval or uncertainty alone is insufficient; exploratory candidates are
allowed and no meaningless helper is required. Use actual IDs, not placeholders.
'''


DIAGNOSTIC_CHARS = 12000
PROMPT_CHARS = 800000


def _source_diagnostic_lines(value, source, diagnostic):
    """Map a native virtual filename only to its exact embedded candidate source.

    BatchWorker stores evidence as N.thy while Isabelle reports /candidate/T.thy.
    Never treat an imported theory's error position as a line in this source.
    """
    source_name = Path(value['source']).name if isinstance(value.get('source'), str) else None
    header = re.match(r'\s*theory\s+(?:"([^"\\]+)"|([^\s"\\]+))\s', source)
    theory_name = next((v for v in header.groups() if v), None) if header else None
    virtual = '/candidate/' + theory_name + '.thy' if theory_name else None
    return [int(number) for number, path in re.findall(r'line\s+(\d+)\s+of\s+"([^"]+)"', diagnostic)
            if (source_name is None or Path(path).name == source_name or path == virtual)]


def compact_feedback(value):
    if isinstance(value, dict):
        # An explicit, hash-bound full history read must round-trip exactly even
        # through controller receipts. Default reads use the summary projection.
        if value.get('view') == 'full' and 'bindings' in value and 'content_id' in value:
            return deepcopy(value)
        result = {k:compact_feedback(v) for k,v in value.items()
                  if k not in {'source_content','log_content','audit_content','native_proof_state_full'}
                  and not (k == 'command' and not isinstance(v, str))}
        diagnostic = value.get('diagnostic')
        if isinstance(diagnostic, str) and len(diagnostic) > DIAGNOSTIC_CHARS:
            result['diagnostic'] = diagnostic[:DIAGNOSTIC_CHARS]
            result['diagnostic_excerpt'] = {
                'incomplete': True, 'offset': 0, 'total_chars': len(diagnostic),
                'sha256': digest(diagnostic),
                'notice': 'Excerpt only; read_diagnostic retrieves exact pages from the original request evidence.'}
        source = value.get('source_content')
        diagnostic = value.get('diagnostic', '')
        if isinstance(source, str) and isinstance(diagnostic, str):
            lines = _source_diagnostic_lines(value, source, diagnostic)
            if lines:
                number = lines[-1]
                source_lines = source.splitlines()
                if 1 <= number <= len(source_lines):
                    result['source_error_line'] = number
                    result['source_excerpt'] = [
                        {'line':i+1, 'text':source_lines[i]}
                        for i in range(max(0, number-9), min(len(source_lines), number+8))]
        return result
    if isinstance(value, list):
        return [compact_feedback(v) for v in value]
    return deepcopy(value)


def action_record(proposal, feedback, number):
    proposal = proposal if isinstance(proposal, dict) else {}
    record = {'request_number':number, 'action':proposal.get('action'),
              'status':feedback.get('status'), 'proposal_sha256':digest(proposal),
              'feedback_sha256':digest(feedback)}
    for field in ('request','node_ids','reason','notes'):
        if field in proposal:
            record[field] = deepcopy(proposal[field])
    if feedback.get('query_identity'):
        record['query_identity'] = feedback['query_identity']
        record['query_feedback_sha256'] = digest(feedback)
    if feedback.get('kind') == 'fact_selection':
        record['query_results'] = compact_feedback(feedback.get('queries', []))
        record['returned_record_ids'] = [r['record_id'] for r in feedback.get('records', [])]
    if proposal.get('action') == 'observe':
        record['observations'] = [dict(action_record(item['request'], item['result'], number),
            item_index=i, parent_feedback_sha256=record['feedback_sha256'])
            for i, item in enumerate(feedback.get('observations', []), 1)]
    if proposal.get('action') == 'submit_batch':
        proofs = proposal.get('proofs', {})
        record['proof_node_ids'] = list(proofs) if isinstance(proofs, dict) else []
        record['candidate_signature'] = digest({'proofs':proofs,'graph':proposal.get('graph')})
        check = feedback.get('check',{})
        if check.get('verdict') == 'red':
            diagnostic = re.sub(r'line \d+ of "[^"]+"','source location',check.get('diagnostic',''))
            record['failure_signature'] = digest(diagnostic)
        for key in ('failure_identity','cached_failure','reused_request'):
            if key in feedback: record[key] = feedback[key]
        record['node_results'] = deepcopy(feedback.get('node_results',[]))
        if 'cost_seconds' in feedback:
            record['cost_seconds'] = deepcopy(feedback['cost_seconds'])
    return record


def apply_feedback(state, proposal, feedback, number, *, observation_order=None):
    """Apply feedback with one selection order for live actions and recovery.

    Legacy feedback without a task order cannot supersede task-ordered memory.
    Proof acceptance and mathematical request accounting remain unchanged.
    """
    from regen.planning.observation_memory import SELECTIONS, memory_fields, memory_value
    versions = memory_value(state,1,{})
    clean = deepcopy(proposal)
    def stale(key):
        return key in versions and (observation_order is None or observation_order < versions[key])
    if isinstance(clean, dict):
        for field in ('workspace', 'notes'):
            if stale('batch_' + field):
                clean.pop(field, None)
    new = _apply_feedback(state, clean, feedback, number)
    # History records the original request, even when its selection is obsolete.
    for item in new.get('batch_history', []):
        if item.get('request_number', item.get('request')) == number:
            item.update(action_record(proposal or {}, feedback, number))
    def touched(request, result):
        if not isinstance(request, dict):return set()
        keys = set()
        if result.get('status') == 'available':
            keys.update(SELECTIONS.get(request.get('action'), ()))
            for item in result.get('observations', []):
                keys.update(touched(item['request'], item['result']))
        if isinstance(request.get('notes'), str):keys.add('batch_notes')
        if isinstance(request.get('workspace'), dict) and result.get('status') not in {'rejected_request','tool_failed'}:
            keys.add('batch_workspace')
        return keys
    selected = touched(proposal, feedback)
    for key in set(versions) | selected:
        if stale(key):
            if key in state:new[key] = deepcopy(state[key])
            else:new.pop(key, None)
        elif observation_order is not None and key in selected and key in new:
            new.setdefault(memory_fields(new)[1], deepcopy(memory_value(new,1,{})))[key] = deepcopy(observation_order)
    if observation_order is not None:
        new.setdefault('batch_observation_orders', {})[str(number)] = deepcopy(observation_order)
    # Historical AND/OR attempts remain replayable. Ordinary document callbacks
    # have no bound route attempt and need no scheduler/cost-table work.
    if new.get('helper_route_attempt', {}).get('request_number') == number:
        from regen.planning.helper_selection import record_attempt
        record_attempt(new, feedback, number)
    return new


def _apply_feedback(state, proposal, feedback, number):
    """Idempotent observation reducer shared by execution and recovery.

    This never changes graph/proof authority. Acceptance must already have been
    established by the caller's validated batch commit.
    """
    new = deepcopy(state)
    proposal = proposal if isinstance(proposal, dict) else {}
    if proposal.get('action') == 'observe' and feedback.get('status') == 'available':
        successful = []
        for item in feedback.get('observations', []):
            if item['result'].get('status') == 'available':
                successful.append(item)
                new = _apply_feedback(new, item['request'], item['result'], number)
        # Same-kind reads in ONE decision form one selection, not replacements.
        for action, key, field, identity in (
                ('read_attempts', 'batch_requested_attempts', 'attempts', 'request_number'),
                ('read_queries', 'batch_requested_queries', 'queries', 'query_id')):
            selected = {v[identity]:v for item in successful if item['request'].get('action') == action
                        for v in item['result'][field]}
            if selected and number >= state.get(key + '_request_number', 0):
                new[key] = deepcopy(list(selected.values()))
                new[key + '_request_number'] = number
        for action, key, field in (
                ('read_diagnostic', 'batch_diagnostic_pages', 'diagnostic_page'),
                ('read_proof_state', 'batch_requested_proof_states', 'proof_state'),
                ('resolve_symbols', 'batch_symbol_observations', 'symbols')):
            items = [item for item in successful if item['request'].get('action') == action]
            if items and number >= state.get(key + '_request_number', 0):
                new[key] = [deepcopy(item['result'][field]) for item in items]
                new[key + '_request_number'] = number
        facts = [f['record_id'] for item in successful if item['request'].get('action') == 'read_facts'
                 for f in item['result'].get('records', [])]
        if facts and number >= state.get('batch_requested_fact_ids_request_number', 0):
            new['batch_requested_fact_ids'] = list(dict.fromkeys(facts))
            new['batch_requested_fact_ids_request_number'] = number
    if proposal.get('action') == 'read_proof_state' and feedback.get('status') == 'available':
        if number >= new.get('batch_proof_state_request', 0):
            new['batch_requested_proof_state'] = deepcopy(feedback['proof_state'])
            new['batch_proof_state_request'] = number
            new['batch_requested_proof_states'] = [deepcopy(feedback['proof_state'])]
            new['batch_requested_proof_states_request_number'] = number
    if proposal.get('action') == 'resolve_symbols' and feedback.get('status') == 'available':
        if number >= new.get('batch_symbol_observations_request_number', 0):
            new['batch_symbols'] = deepcopy(feedback['symbols'])
            new['batch_symbol_observations'] = [deepcopy(feedback['symbols'])]
            new['batch_symbol_observations_request_number'] = number
    history = {item.get('request_number', item.get('request')): item
               for item in new.get('batch_history', [])}
    latest = max((n for n in history if isinstance(n, int)), default=0)
    history[number] = action_record(proposal, feedback, number)
    new['batch_history'] = [history[n] for n in sorted(n for n in history if isinstance(n, int))]
    if number >= latest:
        new['batch_feedback'] = deepcopy(feedback)
        if isinstance(proposal.get('notes'), str):
            new['batch_notes'] = proposal['notes']
        if feedback.get('status') == 'paused':
            new.update(status='paused', reason=feedback['reason'])
        if proposal.get('action') == 'submit_batch':
            new['batch_candidate'] = None if feedback.get('status') == 'accepted' else deepcopy(proposal)
    if (proposal.get('action') == 'submit_batch' and isinstance(proposal.get('proofs'), dict)
            and proposal['proofs']):
        attempt = {'request_number':number, 'candidate':deepcopy(proposal),
                   'feedback':compact_feedback(feedback)}
        if number >= new.get('batch_proof_feedback', {}).get('request_number', 0):
            new['batch_proof_feedback'] = attempt
        targets = dict(target_attempts(new))
        new['batch_target_memory'] = targets
        for node_id in proposal['proofs']:
            if number >= targets.get(node_id, {}).get('request_number', 0):
                targets[node_id] = attempt
        compact_target_memory(new)
    if isinstance(proposal.get('workspace'), dict) and feedback.get('status') not in {'rejected_request','tool_failed'}:
        from regen.planning.planner_workspace import validate_workspace
        if number >= new.get('batch_workspace_request', 0):
            candidates = proposal.get('graph', {}).get('generated_nodes', []) if proposal.get('action') == 'submit_batch' else ()
            new['batch_workspace'] = validate_workspace(new, proposal['workspace'], candidate_nodes=candidates)
            new['batch_workspace_request'] = number
    if feedback.get('kind') == 'fact_selection' and feedback.get('status') == 'available':
        facts = {f['record_id']:f for f in new.get('batch_facts', [])}
        facts.update({f['record_id']:deepcopy(f) for f in feedback.get('records', [])})
        new['batch_facts'] = list(facts.values())
    # Keep bounded, explicitly selected details across complementary tool reads.
    # Otherwise read_attempts -> read_facts drops the diagnostic before a proof
    # can use both. A newer successful read replaces, rather than accumulates,
    # this working set. Request numbers make observation replay idempotent.
    for action, key, field in (
            ('read_attempts', 'batch_requested_attempts', 'attempts'),
            ('read_queries', 'batch_requested_queries', 'queries')):
        if (proposal.get('action') == action and feedback.get('status') == 'available'
                and number >= new.get(key + '_request_number', 0)):
            new[key] = deepcopy(feedback[field])
            new[key + '_request_number'] = number
    if proposal.get('action') == 'read_diagnostic' and feedback.get('status') == 'available':
        if number >= new.get('batch_diagnostic_pages_request_number', 0):
            new['batch_diagnostic_page'] = deepcopy(feedback['diagnostic_page'])
            new['batch_diagnostic_pages'] = [deepcopy(feedback['diagnostic_page'])]
            new['batch_diagnostic_pages_request_number'] = number
    if proposal.get('action') == 'read_facts' and feedback.get('status') == 'available':
        if number >= new.get('batch_requested_fact_ids_request_number', 0):
            new['batch_requested_fact_ids'] = [f['record_id'] for f in feedback.get('records', [])]
            new['batch_requested_fact_ids_request_number'] = number
    if proposal.get('action') == 'read_proofs' and feedback.get('status') == 'available':
        examples = new.setdefault('batch_examples', {})
        examples.update({n['id']:deepcopy(n) for n in feedback.get('proofs', [])})
    return new



class ReadIdError(ValueError):
    """A rejected exact-ID selection with explicit, non-authoritative suggestions."""
    def __init__(self, diagnostic, **details):
        super().__init__(diagnostic)
        self.details = details


def validate_read_ids(ids, available, *, action, field, maximum, index):
    """Validate exact IDs; suggestions never select or return a different item."""
    if not isinstance(ids, list):
        raise ReadIdError(f'{action}.{field} must be a list of exact string IDs from {index}; '
                          f'received {type(ids).__name__}', field=field)
    if not 1 <= len(ids) <= maximum:
        raise ReadIdError(f'{action}.{field} requires 1-{maximum} IDs; received {len(ids)}',
                          field=field, received_count=len(ids))
    invalid_types = [{'index':i, 'value':value} for i, value in enumerate(ids) if not isinstance(value, str)]
    if invalid_types:
        raise ReadIdError(f'{action}.{field} contains non-string IDs at positions '
                          f'{[item["index"] for item in invalid_types]}',
                          field=field, invalid_id_items=invalid_types)
    known = set(available)
    invalid = list(dict.fromkeys(value for value in ids if value not in known))
    if not invalid:
        return ids
    suggestions = {}
    for value in invalid:
        # Very short prefixes are not helpful evidence of an intended exact ID.
        if len(value) < 8:
            continue
        prefixes = sorted(key for key in known if key.startswith(value))
        if len(prefixes) == 1:
            suggestions[value] = prefixes
        elif not prefixes:
            ranked = sorted(((SequenceMatcher(None, value, key, autojunk=False).ratio(), key)
                             for key in known), reverse=True)
            if (ranked and ranked[0][0] >= .94
                    and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= .02)):
                suggestions[value] = [ranked[0][1]]
    raise ReadIdError(f'{action}: unknown exact IDs {json.dumps(invalid, ensure_ascii=False)}. '
                      f'Copy IDs exactly from {index}; suggestions require an explicit new request.',
                      field=field, invalid_ids=invalid, suggested_exact_ids=suggestions)


def read_bound_json(path, expected=None):
    from regen.infrastructure.incremental_log import read_artifact
    value = read_artifact(path)
    if expected is not None and digest(value) != expected:
        raise ValueError('observation artifact integrity mismatch: '+str(path))
    return value


def committed_batch_results(directory):
    """Extract batch/document acceptance, never derived action history.

    The controller calls this only after journal.replay validated this chain and
    any embedded continuation origins. Artifact summaries are not proof authority.
    """
    from regen.execution import proof_commits
    results = {}
    def visit(record):
        if record.get('event') == 'initial':
            origin = record.get('evidence', {}).get('continuation', {})
            for source in origin.get('commits', []):
                visit(source)
        if record.get('event') in {'batch', 'document'}:
            state = record['state']
            number = state.get('batch_requests')
            if isinstance(number, int):
                results[number] = deepcopy(state.get('batch_feedback', {'status':'accepted'}))
    acceptance = directory/'acceptance'
    # Match replay's journal -> cold-memory lock order. Recovery traverses the
    # same potentially giant embedded origins and must share its memory gate.
    with proof_commits.journal_lock(acceptance):
        for path in sorted((acceptance/'commits').glob('*.json')):
            fingerprint = proof_commits._fingerprint(path)
            with proof_commits._COLD_REPLAY_LOCK:
                record = None
                try:
                    record = proof_commits.read_artifact(path)
                    if proof_commits._fingerprint(path) != fingerprint:
                        raise ValueError('proof commit changed during batch recovery')
                    visit(record)
                except BaseException as error:
                    proof_commits._clear_validation_tracebacks(error)
                    raise
                finally:
                    # Future exceptions must not retain a decoded origin, and
                    # the next recovery/replay must not overlap its lifetime.
                    del record
    return results


def restore_batch_memory(directory, state, *, accepted_results=None):
    """Recover charged observations without inferring acceptance from proof text."""
    restored = deepcopy(state)
    accepted_results = committed_batch_results(directory) if accepted_results is None else accepted_results
    # Correct legacy derived-history pollution even if its artifacts are missing.
    for item in restored.get('batch_history', []):
        number = item.get('request_number', item.get('request'))
        if item.get('status') == 'accepted' and number not in accepted_results:
            item['status'] = 'checked_not_committed'
    # Repair derived acceptance status before computing immutable attempt IDs.
    if 'batch_target_memory' in restored:
        restored['batch_target_memory'] = {k:deepcopy(v) for k,v in target_attempts(restored)}
    restored.pop('batch_attempt_objects', None)
    for attempt in [*restored.get('batch_target_memory', {}).values(),
                    restored.get('batch_proof_feedback', {})]:
        if (attempt.get('feedback', {}).get('status') == 'accepted'
                and attempt.get('request_number') not in accepted_results):
            attempt['feedback']['status'] = 'checked_not_committed'
    committed = {item.get('request_number', item.get('request')): item
                 for item in restored.get('batch_history', [])}
    for request_dir in sorted((directory/'batch-requests').glob('*')):
        if not request_dir.name.isdigit() or int(request_dir.name) > state.get('batch_requests', 0):
            continue
        number = int(request_dir.name)
        proposal_path = request_dir/'proposal.json'
        proposal = read_bound_json(proposal_path, committed.get(number, {}).get('proposal_sha256')) if proposal_path.exists() else None
        proposal = proposal if isinstance(proposal, dict) else None
        feedback_path = request_dir/'feedback.json'
        check_path = request_dir/'check.json'
        if feedback_path.exists():
            feedback = read_bound_json(feedback_path, committed.get(number, {}).get('feedback_sha256')
                                       or committed.get(number, {}).get('query_feedback_sha256'))
        elif check_path.exists():
            check = read_bound_json(check_path)['check']
            accepted = number in accepted_results and check.get('verdict') == 'green'
            feedback = {'status':'accepted' if accepted else
                        'rejected_batch' if check.get('verdict') != 'green' else 'checked_not_committed',
                        'check':compact_feedback(check)}
            if accepted:
                feedback = deepcopy(accepted_results[number])
        elif number in committed:
            continue  # inherited committed observation has no local artifact
        else:
            feedback = {'status':'interrupted_action',
                        'notice':'No action result is available; acceptance is never inferred.'}
        # A feedback file alone cannot establish proof acceptance.
        if feedback.get('status') == 'accepted' and number not in accepted_results:
            feedback = {**feedback, 'status':'checked_not_committed'}
        restored = apply_feedback(restored, proposal, feedback, number,
            observation_order=state.get('batch_observation_orders', {}).get(str(number)))
    return compact_target_memory(restored)


def history_actions(state):
    """Tool observations retain the enclosing request's artifact binding."""
    for item in state.get('batch_history', []):
        yield item
        if item.get('action') == 'observe':
            yield from item.get('observations', [])


def observation_summary(state):
    """Small persistent-history view: repeated queries and factual progress signals."""
    queries = {}; seen = set(); last_fact = last_submit = last_green = 0
    for item in history_actions(state):
        number = item.get('request_number', item.get('request', 0))
        if item.get('action') == 'query':
            request = item.get('request', {})
            if not isinstance(request, dict):
                request = {'invalid_request':request}
            signature = item.get('query_identity') or 'legacy:' + digest(request)
            entry = queries.setdefault(signature, {'query_id':signature, 'request':request, 'requests':[],
                'identity_bound':bool(item.get('query_identity')), 'returned_record_ids':[]})
            entry['requests'].append(number)
            ids = item.get('returned_record_ids', [])
            entry['returned_record_ids'] = ids
            entry['last_status'] = item.get('status')
            if set(ids) - seen:
                last_fact = number
            seen.update(ids)
        if item.get('action') == 'submit_batch' and item.get('proof_node_ids'):
            last_submit = number
            if item.get('status') == 'accepted':
                last_green = number
    count = state.get('batch_requests', 0)
    from regen.planning.observation_memory import memory_value
    for query in memory_value(state,0,[]):
        queries[query['query_id']] = {**queries.get(query['query_id'],{}),
            **{key:value for key,value in query.items() if key not in {'cache','observation_order'}}}
    return list(queries.values()), {
        'requests_since_new_record_id':count-last_fact,
        'requests_since_proof_submission':count-last_submit,
        'requests_since_accepted_proof_batch':count-last_green,
        'notice':'No new record IDs does not imply no new information. These signals impose no action limit.'}


def planner_input(package, state):
    graph = state['graph']
    queries, signals = observation_summary(state)
    # Complete propositions remain in the journal; select recent working facts,
    # with a lightweight index so the planner can read any older fact explicitly.
    facts = state.get('batch_facts', [])
    workspace = state.get('batch_workspace', {})
    requested = set(state.get('batch_requested_fact_ids', [])) | set(workspace.get('record_ids', []))
    recent_ids = []
    focus = set(workspace.get('focus_node_ids', []))
    for item in reversed(list(history_actions(state))):
        if focus and item.get('request', {}).get('context_node_id') not in focus:
            continue
        for record_id in item.get('returned_record_ids', []):
            if record_id not in recent_ids:
                recent_ids.append(record_id)
        if len(recent_ids) >= 24:
            break
    active_ids = requested | set(recent_ids[:24])
    # Legacy states without an observation history still expose a usable page.
    if not active_ids:
        active_ids = {f['record_id'] for f in facts[-24:]}
    working_facts = [f for f in facts if f['record_id'] in active_ids]
    relevant_queries = [q for q in queries if not focus or q['request'].get('context_node_id') in focus]
    expanded_queries = {q['query_id'] for q in relevant_queries[-8:]}
    targets = {}
    for node_id, attempt in target_attempts(state):
        feedback = attempt.get('feedback', {})
        diagnostic = str(feedback.get('check', {}).get('diagnostic', feedback.get('diagnostic', '')))
        targets[node_id] = {'request_number':attempt['request_number'],
            'status':feedback.get('status'), 'reason':attempt.get('candidate', {}).get('reason', ''),
            'notes':attempt.get('candidate', {}).get('notes', ''),
            'diagnostic_excerpt':diagnostic[:1800],
            'diagnostic_excerpt_incomplete':len(diagnostic) > 1800,
            'read_action':{'action':'read_attempts', 'request_numbers':[attempt['request_number']]}}
    payload = {
        'theory': package['theory'], 'graph': graph,
        'green_node_ids': list(state['accepted_order']),
        'unproved_node_ids': [n['id'] for n in graph['nodes'] if n['id'] not in state['proofs']],
        'support_contracts': package.get('support_contracts', []),
        'recalled_facts': working_facts,
        'recalled_fact_index': [{k:f[k] for k in ('record_id', 'canonical_name', 'source_class', 'theory') if k in f}
                                for f in facts],
        'fact_loading_notice':'Only a recent working set is expanded; use read_facts for complete indexed facts.',
        'requested_accepted_proofs': state.get('batch_examples', {}),
        'last_feedback': compact_feedback(state.get('batch_feedback')),
        'last_proof_feedback': compact_feedback(state.get('batch_proof_feedback')),
        'planner_notes': state.get('batch_notes', ''),
        'last_candidate': state.get('batch_candidate'),
        'recent_actions': state.get('batch_history', [])[-12:],
        'target_memory': targets,
        'requested_attempts': compact_feedback(state.get('batch_requested_attempts', [])),
        'query_memory': [q for q in queries if q['query_id'] in expanded_queries],
        'query_index': [{'query_id':q['query_id'], 'context_node_id':q['request'].get('context_node_id'),
                         'last_request':q['requests'][-1] if q['requests'] else None,
                         'task_ids':q.get('task_ids',[]), 'count':len(q['requests'])+len(q.get('task_ids',[]))} for q in queries],
        'requested_queries': state.get('batch_requested_queries', []),
        'progress_signals': signals,
        'requests_used': state.get('batch_requests', 0),
    }


    # A rejected submit already appears inside the target-bound proof feedback.
    proof = payload['last_proof_feedback']
    if isinstance(proof, dict) and payload['last_feedback'] == proof.get('feedback'):
        payload['last_feedback'] = {'reference': 'last_proof_feedback.feedback',
                                    'request_number': proof.get('request_number')}
    payload['feedback_request_number'] = max(
        (h['request_number'] for h in state.get('batch_history', [])), default=None)
    payload['diagnostic_page'] = state.get('batch_diagnostic_page')
    payload['diagnostic_pages'] = state.get('batch_diagnostic_pages', [])
    from regen.planning.planner_workspace import consumer_feedback
    payload['workspace'] = workspace
    if 'document_order' not in graph:
        payload['consumer_feedback'] = consumer_feedback(state)
    from regen.planning.batch_routes import route_signals
    if 'document_order' not in graph:
        payload['batch_opportunities'] = route_signals(state)
    from regen.planning.planner_efficiency import next_proof_observation, failed_routes
    payload['next_proof_observation'] = next_proof_observation(state)
    payload['repeated_failed_routes'] = failed_routes(state)
    payload['requested_proof_state'] = state.get('batch_requested_proof_state')
    payload['resolved_symbols'] = state.get('batch_symbols')
    payload['requested_proof_states'] = state.get('batch_requested_proof_states', [])
    payload['symbol_observations'] = state.get('batch_symbol_observations', [])
    return payload


def prepare_prompt(payload):
    """Never silently cut contract statements, facts or proof bodies."""
    payload = compact_feedback(payload)
    proof = payload.get('last_proof_feedback')
    if isinstance(proof, dict) and payload.get('last_feedback') == proof.get('feedback'):
        payload['last_feedback'] = {'reference':'last_proof_feedback.feedback',
                                    'request_number':proof.get('request_number')}
    from regen.planning.planner_context import project_context
    from regen.planning.planner_workspace import observation_inventory
    payload = project_context(payload)
    payload['observation_inventory'] = observation_inventory(payload)
    prompt = INSTRUCTIONS+'\nINPUT:\n'+json.dumps(payload, ensure_ascii=False, indent=2)
    if len(prompt) > PROMPT_CHARS:
        raise ValueError('input_budget_exceeded: bounded observations still exceed local prompt budget; '
                         'mandatory statements/proofs were not truncated')
    return payload, prompt


def workspace_action_input(run, package, state, request_number):
    """Small audit binding for an already-received dynamic tool action.

    The actual model inputs/results are recorded in the workspace transcript.
    Rebuilding its entire historical planner prompt here neither sends useful
    information to the model nor adds proof authority. The journal and immutable
    package retain the exact data addressed by these bindings.
    """
    from regen.execution.proof_batches import _binding
    return {'schema': 'workspace-action-input-v2', 'run_id': run['run_id'],
            'theory': package['theory'], 'request_number': request_number,
            'package_sha256': digest(package), 'accepted_binding': _binding(state),
            'accepted_source_sha256': state.get('document_source_sha256'),
            'provider_called': False,
            'input_origin': 'audited workspace transcript; not a new model prompt'}


def read_diagnostic(directory, state, proposal):
    number = proposal.get('request_number')
    history = {h['request_number']:h for h in state.get('batch_history', [])}
    offset, limit = proposal.get('offset', 0), proposal.get('limit', 8000)
    path = proposal.get('path', ['check', 'diagnostic'])
    if (type(number) is not int or number not in history or type(offset) is not int
            or offset < 0 or type(limit) is not int or not 1 <= limit <= DIAGNOSTIC_CHARS
            or not isinstance(path, list) or not path or path[-1] != 'diagnostic'
            or not all(isinstance(k, str) for k in path)):
        raise ValueError('read_diagnostic needs a historical request_number, diagnostic key path, '
                         'nonnegative offset and limit between 1 and 12000')
    artifact = directory/'batch-requests'/f'{number:06d}'/'feedback.json'
    expected = history[number].get('feedback_sha256') or history[number].get('query_feedback_sha256')
    if not expected:
        raise ValueError('diagnostic read requires bound original feedback')
    value = read_bound_json(artifact, expected)
    for key in path:
        if not isinstance(value, dict) or key not in value:
            raise ValueError('diagnostic path does not exist in original feedback')
        value = value[key]
    if not isinstance(value, str) or offset > len(value):
        raise ValueError('invalid diagnostic text or offset')
    end = min(offset + limit, len(value))
    return {'status':'available', 'diagnostic_page':{
        'request_number':number, 'path':path, 'offset':offset, 'end':end,
        'total_chars':len(value), 'sha256':digest(value), 'text':value[offset:end],
        'incomplete':offset != 0 or end != len(value),
        'next_offset':end if end < len(value) else None}}


def query_identity(run, state, directory, request):
    def file_identity(path):
        path = Path(path)
        if not path.is_file():
            return {'path':str(path), 'missing':True}
        st = path.stat()
        # Equal-size rewrites can share mtime/ctime on coarse host filesystems.
        # Observations must bind the actual catalog/WAL bytes, not only stat().
        with path.open('rb') as stream:
            content_sha256=hashlib.file_digest(stream,'sha256').hexdigest()
        return {'path':str(path.resolve()), 'device':st.st_dev, 'inode':st.st_ino,
                'size':st.st_size, 'mtime_ns':st.st_mtime_ns, 'ctime_ns':st.st_ctime_ns,
                'content_sha256':content_sha256}
    node = next((n for n in state['graph']['nodes'] if n['id'] == request.get('context_node_id')), None)
    observation = None
    if 'proof_state_query' in request:
        from regen.planning.planner_workspace import intermediate_target
        node, observation = intermediate_target(directory.parent.parent, state, request)
    return digest({'request':request, 'observation':observation, 'run_id':run.get('run_id'),
        'database':file_identity(run.get('premise_database', '')),
        'database_wal':file_identity(str(run.get('premise_database', ''))+'-wal'),
        'vectors':[file_identity(Path(run.get('premise_database', '')).parent/name)
                   for name in ('premise-vectors.sqlite', 'premise-vectors.sqlite.sha256', 'premise-vectors.sqlite-wal')]
                  if request.get('use_vectors') else None,
        'sealed':digest((directory.parent.parent/'sealed-premises.json').read_text())
            if (directory.parent.parent/'sealed-premises.json').is_file() else None,
        'context':{k:node.get(k) for k in ('id', 'statement', 'context')} if node else None,
        'accepted_prefix':[(n, digest(state['proofs'][n])) for n in state['accepted_order']]})


def cached_query_feedback(state, directory, identity):
    """Reuse only committed observations whose complete artifact still matches."""
    for item in reversed(list(history_actions(state))):
        if item.get('query_identity') != identity or item.get('status') != 'available':
            continue
        number = item['request_number']
        path = directory.parent/f'{number:06d}'/'feedback.json'
        if not path.is_file():
            continue
        try:
            feedback = read_bound_json(path)
            if 'item_index' in item:
                if digest(feedback) != item.get('parent_feedback_sha256'):
                    continue
                observation = feedback['observations'][item['item_index'] - 1]
                if digest(observation['request']) != item.get('proposal_sha256'):
                    continue
                feedback = observation['result']
        except (ValueError, OSError, KeyError, IndexError, TypeError):
            continue
        if (digest(feedback) == item.get('query_feedback_sha256')
                and feedback.get('kind') == 'fact_selection'):
            return feedback, number
    return None, None


def execute_query(run, package, state, checker, directory, request, item_index=None, *, audit_directory=None):
    """One audited retrieval path for standalone and grouped tool decisions."""
    if not isinstance(request, dict):
        raise ValueError('query request must be an object')
    audit_path = (audit_directory or directory)/('fact-selection.json' if item_index is None
                            else f'fact-selection-{item_index:02d}.json')
    identity = query_identity(run, state, directory, request)
    page, reused_request = cached_query_feedback(state, directory, identity)
    reused_task = None
    if page is None:
        from regen.planning.observation_memory import cached_query
        page,reused_task = cached_query(state,identity)
    if page is None:
        page = batch_query(run, package, state, checker, directory, request, audit_path=audit_path)
    else:
        durable(audit_path, {'request':request, 'reused_request':reused_request,
            'reused_task_id':reused_task, 'source_feedback_sha256':digest(page), 'result':page}, immutable=True)
    previous = [h['request_number'] for h in history_actions(state)
                if h.get('query_identity') == identity]
    known = {f['record_id'] for f in state.get('batch_facts', [])}
    return {**page, 'status':'available', 'query_identity':identity,
        'query_progress':{'previous_identical_requests':previous,
            'reused_request':reused_request,'reused_task_id':reused_task,
            'new_record_ids':[f['record_id'] for f in page.get('records', []) if f['record_id'] not in known]}}


def batch_query(run, package, state, checker, directory, request, *, audit_path=None):
    from regen.execution.round_recall import select_round_facts, is_fact_request
    audit_path = audit_path if audit_path is not None else directory/'fact-selection.json'
    if not isinstance(request, dict):
        raise ValueError('query request must be an object')
    nodes = {n['id']: n for n in state['graph']['nodes']}
    if 'proof_state_query' in request:
        from regen.planning.planner_workspace import intermediate_target
        from regen.execution.file_planner_recall import recall_for_node
        node, observation = intermediate_target(directory.parent.parent, state, request)
        clean = {k:v for k,v in request.items() if k != 'proof_state_query'}
        clean['goal_query'] = True
        return recall_for_node(run['premise_database'], node, clean,
            generated_records=directory.parent.parent/'sealed-premises.json',
            audit_path=audit_path, observation=observation)
    context_id = request.get('context_node_id')
    if context_id is not None and (not isinstance(context_id, str) or context_id not in nodes):
        raise ValueError('context_node_id must be an actual current graph ID or omitted')
    if 'goal_query' in request:
        if context_id is None:
            raise ValueError('goal_query requires an actual context_node_id')
        from regen.execution.file_planner_recall import recall_for_node
        return recall_for_node(run['premise_database'], nodes[context_id], request,
            generated_records=directory.parent.parent/'sealed-premises.json',
            audit_path=audit_path)
    if not is_fact_request(request) or 'proof_state_query' in request:
        raise ValueError('use explicit premise_queries or goal_query with context_node_id')
    queries = request.get('premise_queries', [request])
    if isinstance(queries, list) and any(isinstance(q, dict) and q.get('scope') == 'local' for q in queries):
        raise ValueError('current-theory GREEN nodes are in your workspace; use read_proofs')
    context = nodes[context_id].get('context', []) if context_id else []
    return select_round_facts(Path(run['premise_database']), request,
        generated_records=directory.parent.parent/'sealed-premises.json', local_facts=(),
        audit_path=audit_path,
        symbol_resolver=lambda names: checker.resolve_symbols(names, context))


def seal_theory(run_dir, run, package, directory, checker, ancestors, state):
    from regen.analysis.retained_semantic_impact import audit_theory
    from regen.execution.proof_session_runner import generated_checkpoint_theory
    graph, proofs = state['graph'], state['proofs']
    if set(proofs) != {n['id'] for n in graph['nodes']}:
        raise ValueError('finish requires every active node proved; delete unnecessary unproved auxiliaries explicitly')
    complete = freeze_evidence(checker.check_complete(graph, proofs))
    if complete['verdict'] != 'green':
        return None, {'stage': 'complete', 'check': complete}
    try:
        impact = audit_theory(checker=checker, graph=graph, proofs=proofs,
            premise_snapshot_path=Path(run['premise_snapshot']), output_path=directory/'retained-semantic-impact.json')
    except ValueError as error:
        return None, {'stage':'semantic-audit', 'diagnostic':str(error),
                      'check':freeze_evidence(getattr(checker, 'last_complete_audit', {}))}
    name = generated_checkpoint_theory(package['file_slot'])
    source = checker.complete_source(graph, proofs, theory_name=name)
    path = checker.generated_dir/(name+'.thy'); path.write_text(source)
    build = freeze_evidence(getattr(checker, 'build_sealed_session', checker.build_generated_session)([*ancestors, name]))
    deps = checker.last_complete_audit
    evidence = freeze_evidence({'complete':complete, 'impact':impact, 'build':build,
        'dependencies':deps, 'source':str(path), 'source_text':source, 'source_hash':digest(source)})
    if build['verdict'] != 'green' or impact.get('verdict') != 'pass' or deps.get('verdict') != 'green':
        return None, {'stage':'seal', 'evidence':evidence}
    from regen.execution.seal_evidence import bind_seal, validate_seal
    evidence = bind_seal(state, evidence, checker)
    new = deepcopy(state); new.update(status='sealed', reason='', seal_id='theory-seal:'+digest(evidence)[:20])
    validate_seal(new, evidence, expected_imports=checker.imports(),
                  base_session=checker.base_session, runtime=run)
    return new, evidence



class _PhaseTimer:
    """Contiguous, mutually exclusive wall intervals; native timings are subsets.

    Publication of this telemetry artifact is excluded from its own total. A
    telemetry write failure must never replace a controller result or exception.
    """
    def __init__(self, path, phases, **metadata):
        self.started = self.last = time.perf_counter()
        self.path = path
        self.phase = phases[0]
        self.seconds = dict.fromkeys(phases, 0.0)
        self.metadata = {'started_at_unix':time.time(), **metadata}

    def __enter__(self):
        return self

    def mark(self, phase):
        if phase not in self.seconds:
            raise ValueError('unknown timing phase: '+phase)
        now = time.perf_counter()
        self.seconds[self.phase] += now - self.last
        self.last, self.phase = now, phase
        self.publish_lifecycle('running')

    def publish_lifecycle(self, status):
        if self.path is None or self.metadata.get('scope') != 'request':
            return
        try:
            from regen.infrastructure.progress_events import emit_event
            transition = (self.phase, status, self.metadata.get('model_attempts'), self.metadata.get('action'))
            if transition != getattr(self, '_published_transition', None):
                emit_event('file_planner_phase', theory=self.metadata.get('theory'),
                    request_number=self.metadata.get('request_number'), phase=self.phase,
                    status=status, model_attempts=self.metadata.get('model_attempts'),
                    action=self.metadata.get('action'))
                self._published_transition = transition
            durable(self.path.parent/'lifecycle.json', {
                **self.metadata, 'status':status, 'phase':self.phase,
                'action_status':self.metadata.get('status'),
                'updated_at_unix':time.time(),
                'accounting_notice':'Request registration and backend attempts are not provider billing. '
                    'audited_turn_completed counts only returned, validated transcripts; failed dispatch is unknown.'})
        except Exception:
            logging.getLogger(__name__).warning('Unable to publish request lifecycle', exc_info=True)

    def __exit__(self, exception_type, exception, traceback):
        self.mark(self.phase)
        self.publish_lifecycle('failed' if exception_type else 'completed')
        if self.path is not None:
            result = {**self.metadata, 'schema_version':1,
                      'phase_seconds':self.seconds, 'total_wall_seconds':self.last-self.started,
                      'outcome':'exception' if exception_type else 'completed',
                      'exception_type':exception_type.__name__ if exception_type else None,
                      'measurement_notice':'Phases are disjoint wall intervals. Native and LLM event durations are subsets; do not add them again. Timing artifact publication is excluded.'}
            try:
                durable(self.path, result)
                if self.metadata.get('scope') == 'initialization_latest_invocation':
                    archive = self.path.parent/'initialization-timings'/(uuid4().hex+'.json')
                    durable(archive, result, immutable=True)
            except Exception:
                logging.getLogger(__name__).warning('Unable to publish controller timing: %s', self.path, exc_info=True)
        return False



def _record_termination(run_dir, theory, directory, journal, state, error):
    from regen.execution.repository_execution import progress
    # Read the authority again: an exception may occur after a successful commit.
    # Failure publication must not mask the original error, even on a full disk.
    from regen.infrastructure.provider_recovery import ProviderUnavailable
    terminal = {'status':'paused' if isinstance(error, ProviderUnavailable) else 'interrupted' if isinstance(error, (KeyboardInterrupt, SystemExit))
                else 'infrastructure_error', 'exception_type':type(error).__name__,
                'reason':str(error), 'theory':theory, 'stopped_at_unix':time.time(),
                'request_number':(state or {}).get('batch_requests', 0)}
    if isinstance(error, ProviderUnavailable):
        terminal.update(failure_scope='provider_transport', recoverable=True,
            resume_notice='Resume the same frozen run after connectivity recovers; preserve charged calls and GREEN proofs.')
    try:
        latest = journal.replay() if journal is not None else None
        if latest is not None:
            terminal['request_number'] = latest.get('batch_requests', 0)
        if latest is not None and latest.get('status') not in {'sealed','retired'}:
            new = deepcopy(latest)
            new.update(status=terminal['status'], reason=terminal['reason'], termination=terminal)
            journal.append('failed', new, terminal)
            progress(run_dir)
    except Exception:
        logging.getLogger(__name__).exception('Unable to commit controller termination')
    try:
        stop_path = directory/'execution-stop.json' if directory is not None else Path(run_dir)/'startup-failure.json'
        durable(stop_path, terminal)
        from regen.infrastructure.progress_events import emit_event
        emit_event('file_planner_stopped', **terminal)
    except Exception:
        logging.getLogger(__name__).exception('Unable to publish controller termination')


def _run_batch_file(run_dir, theory, *, provider='codex', model=None,
                    call_timeout=600, check_timeout=900, candidate_timeout=120, max_requests=0,
                    decision_source=None, runtime_cache=None, observation_source=None, observation_order=None,
                    state_sink=None, incremental_progress=False, helper_attempt=None):
    from regen.execution.repository_execution import setup, progress
    from regen.planning.declaration_index import accepted_declarations
    from regen.execution.proof_session_runner import frozen_role_model
    from regen.execution.proof_batches import prepare_batch, check_batch, check_declarations
    if call_timeout <= 0 or check_timeout <= 0 or candidate_timeout <= 0 or max_requests < 0:
        raise ValueError('invalid batch controller limits')
    checker = journal = directory = None
    monitor_started = False
    state = {}
    if incremental_progress:
        from regen.execution.repository_execution import publish_theory_progress
        full_progress = progress
        def progress(path):
            return (publish_theory_progress(path, theory, state)
                    if state else full_progress(path))
    try:
        with _PhaseTimer(None, ('setup', 'initial_state', 'restore_and_resume', 'progress'),
                         theory=theory, scope='initialization_latest_invocation') as initialization:
            cache_key = (str(Path(run_dir).resolve()), theory, check_timeout)
            cached = runtime_cache.get(cache_key) if runtime_cache is not None else None
            cached_runtime = cached is not None
            if cached is None:
                cached = setup(run_dir, theory, check_timeout)
                if runtime_cache is not None:
                    runtime_cache[cache_key] = cached
            run, package, directory, journal, checker, ancestors = cached
            from regen.infrastructure.progress_events import configure_event_monitor, emit_event
            configure_event_monitor(Path(run_dir), run_id=run['run_id'], theory=theory)
            monitor_started = True
            emit_event('file_planner_started', theory=theory, stage='initialization',
                       mode='file-planner-workspace-v1' if decision_source else 'file-planner-batch-v1')
            checker.batch_candidate_timeout = candidate_timeout
            initialization.path = directory/'batch-initialization-timing.json'
            initialization.mark('initial_state')
            model = model or frozen_role_model(run, 'File Planner')
            if model != frozen_role_model(run, 'File Planner'):
                raise ValueError('File Planner model differs from frozen identity')
            policy = {'mode':'file-planner-workspace-v1' if decision_source else 'file-planner-batch-v1', 'provider':provider, 'model':model,
                      'call_timeout':call_timeout, 'check_timeout':check_timeout,
                      'candidate_timeout':candidate_timeout, 'workers':1}
            if getattr(checker, 'generation_strategy_name', 'baseline') == 'bounded-audited-prefix-v1':
                policy['generation_strategy'] = 'bounded-audited-prefix-v1'
            durable(directory/'batch-policy.json', policy, immutable=True)
            state = journal.replay()
            if state is None:
                if getattr(checker, 'document_mode', False) is True:
                    from regen.planning.declaration_index import initial_index
                    graph = initial_index(package, run_id=run['run_id']+'--'+theory)
                else:
                    graph = build_graph(package, {'generated_nodes':[], 'contract_dependencies':{}},
                                        run_id=run['run_id']+'--'+theory, revision=1)
                graph.update(initial_graph_owner='File Planner', runtime_graph_owner='File Planner',
                             execution_mode='file-planner-batch-v1')
                graph = accepted_declarations(graph, [])
                check = freeze_evidence(check_declarations(checker, graph))
                if check['verdict'] != 'green':
                    raise RuntimeError('fixed-contract scaffold failed native elaboration: '+check.get('diagnostic',''))
                state = journal.append('initial', {'graph':graph, 'proofs':{}, 'accepted_order':[],
                    'rounds':{}, 'revisions':{}, 'status':'planned', 'seal_id':None}, {'check':check})
            if state['status'] == 'sealed':
                initialization.mark('progress')
                return progress(run_dir)
            if state.get('runtime_policy') and state['runtime_policy'].get('mode') != policy['mode']:
                raise ValueError('use an explicit continuation to change frozen execution policy')
            initialization.mark('restore_and_resume')
            if not cached_runtime or state.get('status') != 'generating':
                new = restore_batch_memory(directory, state)
                new.update(status='generating', reason='', runtime_policy=policy)
                new.pop('termination', None)
                state = journal.append('resume', new, {'reason':'authorized batch controller; preserve graph, proofs and historical costs'})
                initialization.mark('progress')
                progress(run_dir)
            # A cached workspace action has not changed accepted state here.
            # Its result below publishes progress once, after the actual commit.
        if observation_source is not None:
            observed = observation_source(state)
            if observed != state:
                state = journal.append('planner-feedback',observed,{'reason':'merge verified read-only task receipts; no proof authority'})
        executed = 0
        source_phase = 'tool_source_audit' if decision_source else 'model'
        while not max_requests or executed < max_requests:
            with _PhaseTimer(None, ('charge', 'input_and_prompt', source_phase, 'action', 'commit', 'progress'),
                             theory=theory, scope='request') as timing:
                count = state.get('batch_requests', 0)+1
                request_dir = directory/'batch-requests'/f'{count:06d}'
                timing.path = request_dir/'timing.json'
                timing.metadata['request_number'] = count
                request_dir.mkdir(parents=True, exist_ok=True)
                new = deepcopy(state); new['batch_requests'] = count
                if helper_attempt is not None:
                    new['helper_route_attempt'] = {**helper_attempt, 'request_number': count}
                state = journal.append('planner-request', new, {'request_number':count})
                timing.mark('input_and_prompt')
                timing.metadata['model_attempts'] = 0
                if decision_source is None:
                    payload, prompt = prepare_prompt(planner_input(package, state))
                else:
                    payload = workspace_action_input(run, package, state, count)
                    prompt = json.dumps({'kind':'workspace-action-context-reference',
                        'theory':theory,'request_number':count,'input_sha256':digest(payload)}, sort_keys=True)
                durable(request_dir/'input.json', payload, immutable=True)
                durable(request_dir/'input-preflight.json', {
                    'status':'ready', 'prompt_chars':len(prompt), 'limit_chars':PROMPT_CHARS if decision_source is None else None,
                    'prompt_sha256':digest(prompt)}, immutable=True)
                timing.mark(source_phase)
                timing.metadata['model_attempts'] = 0
                response = None
                timing.metadata['calls'] = []
                for attempt in range(1 if decision_source else 2):
                    timing.metadata['model_attempts'] += int(decision_source is None)
                    timing.metadata['dispatch_kind'] = 'dynamic_tool_call' if decision_source else 'model_request'
                    prefix = allocate_artifact_prefix(request_dir/'call')
                    call_record = {'artifact_prefix':str(prefix), 'state':'backend_attempted',
                                   'audited_turn_completed':False, 'provider_called':None}
                    timing.metadata['calls'].append(call_record)
                    timing.publish_lifecycle('running')
                    try:
                        if decision_source is None:
                            response = call_llm(provider, prompt, prefix, model=model,
                                timeout=call_timeout, theory=theory, stage='file-planner-batch', clean_runtime=True)
                        else:
                            response = decision_source(prompt, payload, prefix, theory=theory, model=model)
                        call_record['state'] = 'backend_returned'
                        meta = validate_call_artifact(prefix, require_real=True)
                        call_record.update(state='audited', audited_turn_completed=decision_source is None,
                                           provider_called=decision_source is None,
                                           audited_tool_call=decision_source is not None)
                        break
                    except RuntimeError as error:
                        if decision_source is not None:
                            raise  # A received tool action must never be dispatched twice.
                        from regen.infrastructure.provider_recovery import transient_failure, ProviderUnavailable
                        transient = transient_failure(prefix)
                        if attempt:
                            if transient:
                                raise ProviderUnavailable(str(error)) from error
                            raise  # durable request remains charged; no accepted proof lost
                        if not transient:
                            raise  # Unknown/permanent errors need diagnosis, not another paid dispatch.
                        from regen.infrastructure.progress_events import emit_event
                        emit_event('provider_retry_backoff', seconds=10, request_number=count, theory=theory)
                        time.sleep(10)
                    finally:
                        timing.publish_lifecycle('running')
                timing.mark('action')
                executed += 1
                proposal = None
                bounded_start_order = None
                try:
                    document_check_requested = False
                    proposal = parse_json_response(response)
                    if not isinstance(proposal, dict):
                        proposal = None
                        raise ValueError('expected one JSON action object')
                    if proposal.get('action') == 'check_theory':
                        document_check_requested = True
                        from regen.planning.theory_document import DocumentStore, candidate as document_candidate, selected_source
                        if set(proposal)-{'action','source_sha256','reason','notes','retry_check','entry_ids'}:
                            raise ValueError('check_theory accepts source_sha256, entry_ids, reason, notes and optional retry_check')
                        durable(request_dir/'model-proposal.json',proposal,immutable=True)
                        from regen.planning.sketch_check import strategy_options
                        store=DocumentStore(directory/'document',package,state,checker.imports(),
                            **strategy_options(getattr(checker,'generation_strategy_name','baseline')))
                        source=store.checked_source(proposal.get('source_sha256'))
                        from regen.planning.sketch_check import authorize
                        document_sketch_authorizations=authorize(store,source,proposal.get('entry_ids'))
                        source=selected_source(package,state,source,checker.imports(),proposal.get('entry_ids'))
                        projected=document_candidate(package,state,source,checker.imports())
                        proposal={**{k:v for k,v in proposal.items() if k in {'reason','notes','retry_check'}},
                            'action':'submit_batch','document_source':source,
                            'proofs':projected['proofs'],
                            'graph':{'generated_nodes':[n for n in projected['graph']['nodes'] if n['role']!='contract']}}
                    if proposal.get('action') == 'submit_batch' and any(
                            key in proposal for key in ('proof_edits','reuse_proofs','reuse_helpers')):
                        durable(request_dir/'model-proposal.json', proposal, immutable=True)
                    if proposal.get('action') == 'submit_batch' and 'proof_edits' in proposal:
                        from regen.planning.planner_efficiency import expand_edits
                        proposal = expand_edits(directory, state, proposal)
                    if proposal.get('action') == 'submit_batch' and 'reuse_proofs' in proposal:
                        from regen.planning.batch_routes import expand_reuse
                        proposal = expand_reuse(directory,state,proposal)
                    if proposal.get('action') == 'submit_batch' and 'reuse_helpers' in proposal:
                        from regen.execution.shared_proof_workspace import SharedProofWorkspace, expand_reuse_helpers
                        proposal = expand_reuse_helpers(proposal, state, SharedProofWorkspace(Path(run_dir)))
                    durable(request_dir/'proposal.json', proposal, immutable=True)
                    if 'notes' in proposal and not isinstance(proposal['notes'], str):
                        raise ValueError('notes must be a string')
                    action = proposal.get('action')
                    timing.metadata['action'] = action
                    if 'workspace' in proposal and action != 'submit_batch':
                        from regen.planning.planner_workspace import validate_workspace
                        validate_workspace(state, proposal['workspace'])
                    bounded_feedback = None
                    bounded_fallback = None
                    if (document_check_requested and decision_source is not None
                            and getattr(checker, 'generation_strategy_name', 'baseline') == 'bounded-audited-prefix-v1'):
                        from regen.planning.bounded_prefix import run_prefix, STRATEGY
                        durable(request_dir/'document-source.json', {'source':proposal['document_source']}, immutable=True)
                        durable(request_dir/'candidate.json', projected, immutable=True)
                        def commit_prefix(new, evidence):
                            nonlocal state
                            new = apply_feedback(new, proposal, new['batch_feedback'], count,
                                                 observation_order=observation_order)
                            state = journal.append('document', new, evidence)
                            return state
                        bounded_start_order = list(state['accepted_order'])
                        bounded_feedback = run_prefix(package, state, proposal['document_source'], checker,
                            request_dir/'bounded-prefix', commit=commit_prefix,
                            evidence_origin={'sealable_generation':bool(meta.get('sealable_generation')),
                                'transcript_prefix':str(prefix), 'reason':proposal.get('reason',''),
                                'parent_request_number':count,
                                'parent_model_proposal_path':str(request_dir/'model-proposal.json'),
                                **deepcopy(getattr(
                                    checker,
                                    'bounded_prefix_evidence_origin', {}))},
                            eligible_ids=getattr(
                                checker, 'bounded_prefix_eligible_ids', None))
                        if bounded_feedback is None:
                            bounded_fallback = {'strategy':STRATEGY, 'status':'whole_check_fallback',
                                'policy_path':str(request_dir/'bounded-prefix/policy.json')}
                    if bounded_feedback is not None:
                        feedback = bounded_feedback
                    elif action == 'observe':
                        from regen.planning.planner_workspace import observe
                        feedback = observe(directory, state, proposal, checker,
                            query_handler=lambda request, index: execute_query(
                                run, package, state, checker, request_dir, request, index))
                    elif action in {'read_proof_state','resolve_symbols'}:
                        from regen.planning.planner_workspace import read_observation
                        feedback = read_observation(directory,state,proposal,checker)
                    elif action == 'query':
                        feedback = execute_query(run, package, state, checker, request_dir,
                                                 proposal.get('request'))
                    elif action == 'read_attempts':
                        from regen.planning.planner_workspace import read_observation
                        feedback = read_observation(directory, state, proposal)
                    elif action == 'read_diagnostic':
                        feedback = read_diagnostic(directory, state, proposal)
                    elif action == 'read_queries':
                        ids = proposal.get('query_ids')
                        queries = {q['query_id']:q for q in observation_summary(state)[0]}
                        validate_read_ids(ids, queries, action=action, field='query_ids', maximum=4, index='query_index')
                        feedback = {'status':'available','queries':[queries[k] for k in ids]}
                    elif action == 'read_facts':
                        ids = proposal.get('record_ids')
                        facts = {f['record_id']:f for f in state.get('batch_facts', [])}
                        validate_read_ids(ids, facts, action=action, field='record_ids', maximum=24, index='recalled_fact_index')
                        feedback = {'status':'available', 'records':[deepcopy(facts[k]) for k in ids]}
                    elif action == 'read_proofs':
                        ids = proposal.get('node_ids')
                        validate_read_ids(ids, state['proofs'], action=action, field='node_ids', maximum=4, index='green_node_ids')
                        nodes = {n['id']:n for n in state['graph']['nodes']}
                        feedback = {'status':'available', 'proofs':[
                            {**nodes[k], 'proof_body':state['proofs'][k], 'proof_sha256':digest(state['proofs'][k])} for k in ids]}
                    elif action == 'submit_batch':
                        document_source = proposal.get('document_source')
                        if document_source is not None:
                            from regen.planning.theory_document import candidate as document_candidate
                            # check_theory was projected above from this exact
                            # hash-bound source and unchanged state. Reuse it;
                            # commit validation still independently reconstructs
                            # and verifies the candidate at the authority boundary.
                            candidate=(projected if document_check_requested else
                                document_candidate(package,state,document_source,checker.imports()))
                            if proposal.get('proofs')!=candidate['proofs']:
                                raise ValueError('document projection differs from source proofs')
                            durable(request_dir/'document-source.json',{'source':document_source},immutable=True)
                        else:
                            candidate = prepare_batch(package, state, {k: v for k, v in proposal.items() if k not in {'action', 'notes', 'workspace', 'reuse_origin', 'edit_origin', 'retry_check', 'workspace_reuse_origin'}})
                        if 'workspace' in proposal:
                            from regen.planning.planner_workspace import validate_workspace
                            validate_workspace(candidate, proposal['workspace'])
                        durable(request_dir/'candidate.json', candidate, immutable=True)
                        from regen.planning.planner_efficiency import failure_identity, reused_failure
                        identity = failure_identity(run, checker, state, candidate)
                        cached = reused_failure(directory, state, identity, retry_reason=proposal.get('retry_check'))
                        from regen.planning.theory_document import check_document
                        evidence = ({'check':cached['check'], 'node_results':cached.get('node_results',[]),
                                     'cached_failure':True, 'failure_identity':identity,
                                     'reused_request':cached['reused_request']} if cached else
                                    (check_document(checker,state,candidate,document_source) if document_source is not None
                                     else check_batch(checker, state, candidate)))
                        evidence.update(sealable_generation=bool(meta.get('sealable_generation')),
                                        transcript_prefix=str(prefix), reason=proposal.get('reason',''))
                        if document_source is not None:
                            evidence['document_publication_policy'] = 'preserve-accepted-ids-v1'
                            if document_check_requested:
                                evidence['sketch_authorizations'] = document_sketch_authorizations
                        if bounded_fallback is not None:
                            evidence['bounded_prefix'] = bounded_fallback
                        if 'workspace_reuse_origin' in proposal:
                            evidence['workspace_reuse_origin'] = deepcopy(proposal['workspace_reuse_origin'])
                        durable(request_dir/'check.json', evidence, immutable=True)
                        if evidence['check'].get('failure_scope') == 'native_preparation':
                            raise NativePreparationError(evidence['check'].get('diagnostic','native prefix preparation failed'))
                        if document_check_requested and store.require_review and not cached:
                            from regen.planning.sketch_workflow import record_formal_result
                            record_formal_result(store, document_source, evidence)
                        from regen.planning.theory_document import execution_scope
                        scope = execution_scope(state, candidate, evidence)
                        missing_accepted = (sorted(set(state['proofs']) - set(candidate['proofs']))
                                            if document_source is not None else [])
                        if evidence['check']['verdict'] == 'green' and not missing_accepted:
                            timing.mark('commit')
                            candidate.update(status='generating', reason='', batch_feedback={'status':'accepted',
                                'new_green':sorted(set(candidate['proofs'])-set(state['proofs'])),
                                'graph_revision':candidate['graph']['revision'],
                                'node_results':evidence.get('node_results',[]),
                                **scope,
                                'cost_seconds':{k:timing.seconds[k] for k in (source_phase,'action')}}, batch_candidate=None)
                            candidate.pop('revision_rejection', None)
                            candidate = apply_feedback(candidate, proposal, candidate['batch_feedback'], count, observation_order=observation_order)
                            if document_source is not None:
                                candidate['batch_feedback'].update(source_sha256=candidate['document_source_sha256'],
                                    check_mode=evidence['document_check_mode'],
                                    rechecked_ids=scope['checked_delta_ids'],
                                    requested_source_sha256=store.read()['source_sha256'])
                            if bounded_fallback is not None:
                                candidate['batch_feedback']['bounded_prefix'] = bounded_fallback
                            state = journal.append('document' if document_source is not None else 'batch', candidate, evidence)
                            timing.metadata.update(status='accepted', action_completed=True, check_verdict='green')
                            timing.mark('progress')
                            projection = progress(run_dir)
                            if max_requests and executed >= max_requests:
                                return {'status':'checkpoint', **projection}
                            continue
                        feedback = {'status':'rejected_batch', 'check':evidence['check'],
                            'node_results':evidence.get('node_results',[]), 'failure_identity':identity,
                            **scope}
                        if evidence['check']['verdict'] == 'green' and missing_accepted:
                            # A diagnostic draft may intentionally contain oops.
                            # Keep its native evidence, but never publish a smaller
                            # accepted set or merge proofs from different contexts.
                            feedback.update(status='draft_checked', accepted=False,
                                publication_blocked='missing_accepted_proofs',
                                missing_accepted_ids=missing_accepted, new_green=[],
                                source_sha256=candidate['document_source_sha256'],
                                accepted_source_sha256=state.get('document_source_sha256'),
                                notice='Diagnostic draft is GREEN, not accepted. Restore all missing accepted entries and check the complete draft before publication; read_proofs retrieves accepted bodies.')
                        if bounded_fallback is not None:
                            feedback['bounded_prefix'] = bounded_fallback
                        if cached:
                            feedback.update(cached_failure=True, reused_request=cached['reused_request'], notice=cached['notice'])
                    elif action == 'finish':
                        new, evidence = seal_theory(run_dir, run, package, directory, checker, ancestors, state)
                        durable(request_dir/'seal.json', evidence, immutable=True)
                        if new is not None:
                            timing.mark('commit')
                            state = journal.append('seal', new, evidence)
                            timing.metadata.update(status='sealed', action_completed=True)
                            timing.mark('progress')
                            return progress(run_dir)
                        feedback = {'status':'seal_failed', **evidence}
                    elif action == 'pause':
                        if not isinstance(proposal.get('reason'),str) or not proposal['reason'].strip():
                            raise ValueError('pause requires a concrete reason')
                        feedback = {'status':'paused', 'reason':proposal['reason']}
                    else:
                        raise ValueError('unknown action; use query/read_queries/read_attempts/read_diagnostic/read_facts/read_proofs/submit_batch/finish/pause')
                except (ValueError, KeyError, TypeError) as error:
                    feedback = {'status':'rejected_request', 'diagnostic':str(error)}
                    if isinstance(error, ReadIdError):
                        feedback.update(error.details)
                except NativePreparationError:
                    raise
                except RuntimeError as error:
                    feedback = {'status':'tool_failed', 'failure_scope':'tool',
                                'diagnostic':str(error),
                                'notice':'Inspect the tool failure; only durable checker-validated commits remain accepted.'}
                if bounded_start_order is not None and 'bounded_prefix' not in feedback:
                    accepted = [key for key in state['accepted_order'] if key not in bounded_start_order]
                    feedback.update(new_green=accepted, bounded_prefix={
                        'strategy':'bounded-audited-prefix-v1', 'accepted_ids':accepted,
                        'pending_ids':[key for key in projected['accepted_order'] if key not in state['proofs']],
                        'original_source_sha256':projected['document_source_sha256'],
                        'draft_preserved':True, 'stop_reason':'tool_error'})
                timing.metadata['action_completed'] = True
                timing.metadata['check_verdict'] = feedback.get('check', {}).get('verdict')
                timing.mark('commit')
                if proposal and proposal.get('action') in {'submit_batch','check_theory'}:
                    feedback['cost_seconds'] = {k:timing.seconds[k] for k in (source_phase,'action')}
                timing.metadata['status'] = feedback['status']
                durable(request_dir/'feedback.json', feedback, immutable=True)
                new = apply_feedback(state, proposal, feedback, count, observation_order=observation_order)
                state = journal.append('planner-feedback', new, {'request_number':count, 'feedback':feedback})
                timing.mark('progress')
                projection = progress(run_dir)
                if feedback['status'] == 'paused':
                    return {'status':'paused', 'reason':feedback['reason'], **projection}
                if max_requests and executed >= max_requests:
                    return {'status':'checkpoint', **projection}
        return {'status':'checkpoint', **progress(run_dir)}
    except BaseException as error:
        from regen.execution.check_tasks import CheckCancelled
        if isinstance(error, CheckCancelled):
            # Task cancellation has its own durable state, never a mathematical failure.
            if checker is not None:
                checker.close_prefix()
            raise
        _record_termination(run_dir, theory, directory, journal, state, error)
        from regen.infrastructure.provider_recovery import ProviderUnavailable
        if isinstance(error, ProviderUnavailable):
            return {**progress(run_dir), 'status':'paused', 'failure_scope':'provider_transport',
                    'recoverable':True, 'reason':str(error)}
        raise
    finally:
        import sys
        active_error = sys.exc_info()[0] is not None
        if not active_error and state_sink is not None and state:
            # The caller owns the serial theory action. Reuse the exact state
            # returned by the journal instead of replaying it for the response.
            state_sink(state)
        monitor_reason = 'failed' if active_error else 'controller-returned'
        try:
            if checker is not None and runtime_cache is None:
                checker.close_prefix()
        except Exception as cleanup_error:
            monitor_reason = 'failed'
            if not active_error:
                _record_termination(run_dir, theory, directory, journal, state, cleanup_error)
                raise
            logging.getLogger(__name__).exception('Worker cleanup failed after controller failure')
        finally:
            if monitor_started and runtime_cache is None:
                from regen.infrastructure.progress_events import close_event_monitor
                try:
                    close_event_monitor(monitor_reason)
                except Exception:
                    logging.getLogger(__name__).exception('Unable to close progress monitor')


def run_batch_file(run_dir, theory, **options):
    from regen.execution.proof_session_runner import acquire_run_mutation_lock, release_run_mutation_lock
    token = acquire_run_mutation_lock(run_dir, operation='batch-file', theory=theory)
    try:
        return _run_batch_file(run_dir, theory, **options)
    finally:
        release_run_mutation_lock(token)
