"""Agent-directed repository planning with replayable queries and validation.

Only explicit submission publishes a valid draft. Query and correction turns have
no numerical ceiling; transport failures remain governed by the audited caller.
"""
from collections import Counter
import json
import hashlib
from pathlib import Path

from regen.preparation.planning_input import PlanningInputQueries
from regen.preparation.refine_campaign import write_immutable_json
from regen.planning.planner_io import parse_model_json
from regen.planning.theory_plan import build_bundle, prompt_v4

TOOLS = {
    'inspect_contracts': {'contract_ids': ['ID (at most 32 per call)']},
    'search_premises': {'query': 'names, definitions, or concepts relevant to contracts',
                        'context_profile_ids': ['allowed profile'], 'attributes': [], 'limit': 20},
    'inspect_premises': {'record_ids': ['ID (at most 50 per call)']},
    'resolve_premise_names': {'names': ['public fact name'], 'context_profile_id': 'allowed profile'},
    'validate_plan': {'proposal': 'complete schema-4 proposal object'},
    'submit_plan': {'proposal': 'complete schema-4 proposal object; omit to submit current draft'},
    'read_turn': {'turn': 'earlier integer turn number'},
    'save_notes': {'notes': 'replace persistent working notes with your planning observations'},
    'stop': {'reason': 'why planning should stop without publishing'},
}


def agent_prompt(payload, latest, draft, turn):
    # Full historical evidence remains in the audit and can be recalled. Avoid
    # growing the model context by appending every prior query and failed draft.
    return (
        'You control an iterative repository-planning process. There is no limit on '
        'proposal corrections or query turns. Theory names and helper decision IDs '
        'must match [A-Za-z][A-Za-z0-9_]*: start with an ASCII letter and use only '
        'ASCII letters, digits, and underscores, never hyphens or spaces. Helper '
        'decision IDs must also be unique across the entire proposal. When a '
        'draft fails validation, revise the proposal and pass the complete corrected '
        'object in arguments.proposal; submit_plan with empty arguments revalidates '
        'the unchanged current draft. read_turn only reads history and does not '
        'modify the draft; its turn argument must be an integer, not a string. '
        'Choose what to inspect and when to '
        'validate. validate_plan returns structural errors without publishing; even '
        'a valid draft may be revised. Only submit_plan publishes a valid proposal. '
        'You may stop with a reason. Pagination bounds each call, not the number of '
        'calls. Previously inspected full contracts and premise records persist in '
        'the legal input on every turn; do not repeatedly reread supplied records. '
        'Use save_notes to preserve your own planning conclusions between turns. '
        'Search already established helper lemmas by name/definition/concept; '
        'there is no proof-state query at repository planning time. Exact helper '
        'statements and proof generation belong to later stages. The harness runs '
        'the requested operation and returns its result on your next turn. Do not '
        'invoke shell, file, network, or provider tools. Return one JSON object '
        '{"action":"operation","arguments":{...}} using these operations:\n'
        + json.dumps(TOOLS, ensure_ascii=False, sort_keys=True)
        + '\nTURN: ' + str(turn)
        + '\nLATEST HARNESS RESULT:\n' + json.dumps(latest, ensure_ascii=False, sort_keys=True)
        + '\nPERSISTENT AGENT NOTES:\n' + json.dumps(payload.get('agent_notes', ''), ensure_ascii=False)
        + '\nCURRENT DRAFT (not yet published):\n' + json.dumps(draft, ensure_ascii=False, sort_keys=True)
        + '\nEarlier complete results are available through read_turn; reinspect any '
        'needed contract or premise. Evidence inspected on any prior turn remains '
        'eligible for validation. Never guess unseen propositions. The following '
        'defines the proposal schema, not a requirement to submit immediately:\n'
        + prompt_v4(payload, []).replace(
            'Return JSON only matching this schema (all fields required):',
            'The proposal object inside arguments must match this schema (all fields required):')
        + '\nRespond with exactly one action/arguments operation envelope.'
    )


def validate_draft(payload, proposal, evidence):
    try:
        artifacts = build_bundle({**payload, 'planner_evidence': evidence}, proposal)
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        result = {'valid': False, 'errors': [str(error)]}
        theories = proposal.get('theories') if isinstance(proposal, dict) else None
        if isinstance(theories, list) and all(isinstance(t, dict) and isinstance(t.get('contract_ids'), list)
                                             for t in theories):
            assigned = [cid for t in theories for cid in t['contract_ids']]
            if all(isinstance(cid, str) for cid in assigned):
                expected = {c['contract_id'] for c in payload['contracts']}
                result['contract_coverage'] = {
                    'missing': sorted(expected - set(assigned)),
                    'unknown': sorted(set(assigned) - expected),
                    'duplicates': sorted(cid for cid, count in Counter(assigned).items() if count > 1),
                }
        return result, None
    plan = artifacts['repository-plan.json']
    return {'valid': True, 'errors': [], 'theories': len(plan['theories']),
            'contracts': len(payload['contracts']), 'published': False}, artifacts


def _fields(arguments, required, optional=()):
    if not isinstance(arguments, dict) or not set(required) <= set(arguments) or set(arguments) - set(required) - set(optional):
        raise ValueError('invalid operation arguments')


def run_agent(payload, planning_input_path, policy, output, call):
    """Replay completed turns through the audited caller, then continue as needed."""
    queries = PlanningInputQueries(Path(planning_input_path))
    output = Path(output)
    view = None
    if policy.get('contract_visibility') == 'owner-blind-v1':
        from regen.planning.owner_blind import OwnerBlindView
        view = OwnerBlindView(payload)
        queries = view.queries(queries)
        payload = view.payload
        write_immutable_json(output / 'agent-input.json', payload)
        write_immutable_json(output / 'private-contract-map.json', view.to_original)
        write_immutable_json(output / 'planner-validation-input.json', view.canonical_payload)
    contracts, premises = {}, {}
    evidence = {'inspected_contracts': [], 'inspected_parent_premises': [], 'premise_searches': []}
    latest = {'status': 'ready', 'notice': 'Choose an inspection, search, draft validation, or submission.'}
    draft = None
    notes = ''
    turn = 0
    while True:
        turn += 1
        text = agent_prompt({**payload, 'planner_evidence': evidence, 'agent_notes': notes}, latest, draft, turn)
        # Preserve exact prompts for committed turns when resuming after an
        # audited infrastructure correction. The caller validates and reuses
        # their original completed transcripts instead of sending them again.
        prior_path = output / 'turns' / f'{turn:06d}.json'
        if prior_path.is_file():
            from regen.infrastructure.llm_transcript import artifact_paths
            prior = json.loads(prior_path.read_text())
            original_prompt = artifact_paths(output / prior['transcript_prefix'])['prompt']
            if original_prompt.is_file():
                text = original_prompt.read_text()
        if view:
            # Check harness-supplied evidence, not names independently invented
            # by the agent in its own draft/notes or rejected requests.
            view.assert_no_owner_disclosure(json.dumps(evidence, ensure_ascii=False))
        final, meta, prefix = call(
            text, output / 'transcript' / f'repository-agent-{turn:06d}',
            model=policy['model']['model'], timeout=int(policy['model'].get('timeout_seconds', 1800)),
            allow_external_call=policy.get('_allow_external_call', False),
            stage='Repository-Planning', reasoning_effort=policy['model'].get('reasoning_effort'),
        )
        action = None
        arguments = None
        artifacts = None
        terminal = None
        try:
            request = parse_model_json(final)
            if set(request) != {'action', 'arguments'}:
                raise ValueError('return action and arguments; put the proposal inside validate_plan or submit_plan')
            action, arguments = request['action'], request['arguments']
            if action == 'inspect_contracts':
                _fields(arguments, ('contract_ids',))
                records = queries.inspect_contracts(arguments['contract_ids'])
                contracts.update((r['contract_id'], r) for r in records)
                latest = {'records': records}
            elif action == 'search_premises':
                _fields(arguments, ('query', 'context_profile_ids'), ('attributes', 'limit'))
                hits = queries.search_parent_premises(**arguments)
                latest = {'records': hits, 'notice': 'Inspect premise IDs for complete statements before reusing them.'}
            elif action == 'inspect_premises':
                _fields(arguments, ('record_ids',))
                records = queries.inspect_parent_premises(arguments['record_ids'])
                premises.update((r['record_id'], r) for r in records)
                latest = {'records': records}
            elif action == 'resolve_premise_names':
                _fields(arguments, ('names', 'context_profile_id'))
                latest = {'records': queries.resolve_symbols(**arguments)}
            elif action in {'validate_plan', 'submit_plan'}:
                _fields(arguments, ('proposal',) if action == 'validate_plan' else (), ('proposal',))
                if 'proposal' in arguments:
                    draft = arguments['proposal']
                latest, artifacts = validate_draft(payload, draft, evidence)
                if action == 'submit_plan' and latest['valid']:
                    terminal = 'submitted'
            elif action == 'read_turn':
                _fields(arguments, ('turn',))
                number = arguments['turn']
                if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number < turn:
                    raise ValueError('read_turn requires an earlier positive turn number')
                record = json.loads((output / 'turns' / f'{number:06d}.json').read_text())
                if record['action'] == 'read_turn':
                    # Avoid recursively copying previous history-read responses.
                    latest = {'turn': number, 'action': record['action'], 'arguments': record['arguments'],
                              'notice': 'Read the referenced original turn to obtain its complete result.'}
                else:
                    latest = {'turn': number, 'action': record['action'], 'arguments': record['arguments'],
                              'result': record['result']}
            elif action == 'save_notes':
                _fields(arguments, ('notes',))
                if not isinstance(arguments['notes'], str):
                    raise ValueError('notes must be text')
                notes = arguments['notes']
                latest = {'status': 'notes_saved'}
            elif action == 'stop':
                _fields(arguments, ('reason',))
                if not isinstance(arguments['reason'], str) or not arguments['reason'].strip():
                    raise ValueError('stop requires a nonempty reason')
                latest = {'status': 'stopped', 'reason': arguments['reason']}
                terminal = 'stopped'
            else:
                raise ValueError('unknown planner operation; use one of ' + ', '.join(TOOLS))
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            latest = {'status': 'request_error', 'errors': [str(error)], 'rejected_response': final,
                      'notice': 'Correct the request and continue; no proposal was published.'}
        evidence['inspected_contracts'] = list(contracts.values())
        evidence['inspected_parent_premises'] = list(premises.values())
        latest = {'operation': action, **latest}
        record = {'turn': turn, 'action': action, 'arguments': arguments, 'result': latest,
                  'draft': draft, 'response_sha256': hashlib.sha256(final.encode()).hexdigest(),
                  'transcript_prefix': str(prefix.relative_to(output))}
        if notes:
            record['notes'] = notes
        write_immutable_json(output / 'turns' / f'{turn:06d}.json', record)
        if terminal:
            write_immutable_json(output / 'agent-evidence.json', evidence)
            published_evidence = view.canonical_evidence(evidence) if view else evidence
            write_immutable_json(output / 'planner-evidence.json', published_evidence)
            if terminal == 'stopped':
                write_immutable_json(output / 'planner-stop.json', {'turn': turn, **latest})
                return {'status': 'stopped', 'turns': turn, 'reason': latest['reason']}
            if view:
                write_immutable_json(output / 'agent-proposal.json', draft)
                draft = view.decode(draft)
                artifacts = build_bundle({**view.canonical_payload, 'planner_evidence': published_evidence}, draft)
            return {'status': 'submitted', 'turns': turn, 'proposal': draft, 'artifacts': artifacts,
                    'plan_prefix': prefix, 'evidence': evidence}
