"""Public operation contract for the generic agent's repository planning tool.

These schemas describe existing mechanical inputs, not a planning algorithm.
The common planner remains the authority for coverage, context and native checks.
"""
from copy import deepcopy
import json

from jsonschema import Draft202012Validator


TEXT = {'type': 'string', 'minLength': 1}
STRINGS = {'type': 'array', 'items': TEXT}
LIMIT = {'type': 'integer', 'minimum': 1, 'maximum': 50}
OFFSET = {'type': 'integer', 'minimum': 0, 'maximum': 2**63 - 1}


def obj(properties, required=None, **annotations):
    return {'type': 'object', 'properties': deepcopy(properties),
            'required': list(properties if required is None else required),
            'additionalProperties': False, **annotations}


def array(items, **constraints):
    return {'type': 'array', 'items': items, **constraints}


def proposal_schema(version=6):
    """Structural vocabulary; semantic acceptance still runs the common validator."""
    imports = 'suggested_predecessors' if version == 4 else 'required_imports'
    theory = obj({
        'name': TEXT, 'purpose': TEXT, 'grouping_reason': TEXT,
        'contract_ids': STRINGS,
        'context_profile_ids': {**STRINGS, 'minItems': 1},
        'helper_decisions': array(obj({
            'id': TEXT,
            'kind': {'enum': ['reuse_existing_fact', 'propose_auxiliary_intent']},
            'intent': TEXT, 'for_contracts': {**STRINGS, 'minItems': 1},
            'premise_ids': STRINGS,
        })),
        imports: array(obj({'theory': TEXT, 'reason': TEXT})),
    })
    fields = {
        'schema_version': {'const': version}, 'ordering_rationale': TEXT,
        'contract_relations': array(obj({
            'left_contract_id': TEXT, 'right_contract_id': TEXT,
            'relation': TEXT, 'evidence_ids': {**STRINGS, 'minItems': 1},
        })),
        'theories': array(theory, minItems=1),
        'unresolved_retrieval_questions': {**STRINGS, 'maxItems': 50},
    }
    if version == 6:
        fields.update(
            collaboration_groups=array(obj({
                'id': TEXT, 'contract_ids': {**STRINGS, 'minItems': 2}, 'reason': TEXT,
            })),
            reuse_suggestions=array(obj({'provider': TEXT, 'consumer': TEXT, 'reason': TEXT})),
        )
    return obj(fields)


def example_proposal():
    # Syntax example only; IDs and coverage must come from this run's public input.
    return {
        'schema_version': 6, 'ordering_rationale': 'Example order rationale.',
        'contract_relations': [], 'theories': [{
            'name': 'Generated_Example', 'purpose': 'Example purpose.',
            'grouping_reason': 'Example grouping rationale.',
            'contract_ids': ['C0001'], 'context_profile_ids': ['returned-context-profile-id'],
            'helper_decisions': [], 'required_imports': [],
        }],
        'unresolved_retrieval_questions': [], 'collaboration_groups': [], 'reuse_suggestions': [],
    }


def operations(*, allow_revision=True):
    def operation(action, fields, required, description, example):
        return obj({'action': {'const': action}, **fields}, ['action', *required],
                   description=description, examples=[{'action': action, **example}])
    result = {
        'schema': operation('schema', {}, [],
            'Read operation schemas, examples, plan format, public aliases and context profiles.', {}),
        'contracts': operation('contracts', {'values': STRINGS}, [],
            'Omit values or use [] for the catalog. Supply public contract IDs for exact declarations and inspection receipts.',
            {'values': ['C0001']}),
        'search': operation('search', {'values': {**STRINGS, 'minItems': 1}, 'limit': LIMIT}, ['values'],
            'Search legal parent facts. values are joined into a query; inspect returned record IDs for complete facts.',
            {'values': ['fact_name_or_concept'], 'limit': 20}),
        'definitions': operation('definitions', {'values': {**STRINGS, 'minItems': 1}, 'limit': LIMIT, 'offset': OFFSET}, ['values'],
            'Retrieve complete legal definition records by identifiers. Continue with returned next_offset.',
            {'values': ['definition_name'], 'limit': 20, 'offset': 0}),
        'inspect': operation('inspect', {'values': {**STRINGS, 'minItems': 1, 'maxItems': 50}}, ['values'],
            'Read complete parent facts using record IDs returned by search/definitions, and record inspection receipts.',
            {'values': ['returned-premise-record-id']}),
        'related': operation('related', {'values': {**STRINGS, 'minItems': 1}, 'limit': LIMIT, 'offset': OFFSET}, ['values'],
            'Read relation candidates for public contract IDs; candidates do not establish dependencies.',
            {'values': ['C0001'], 'limit': 20, 'offset': 0}),
        'read_prior': operation('read_prior', {'epoch': {'type': 'integer', 'minimum': 1}, 'theory': TEXT}, ['epoch', 'theory'],
            'Read an accepted theory-state snapshot (not an unaccepted draft) from an earlier accepted epoch of this experiment, as reference only. Get epochs and theory names from experiment_status.prior_plans.',
            {'epoch': 1, 'theory': 'Generated_Example'}),
    }
    if allow_revision:
        result['begin_revision'] = operation('begin_revision', {'reason': TEXT}, ['reason'],
            'Start a replacement plan after an accepted plan. Accepting it invalidates current proof authority; prior results remain reference-only.',
            {'reason': 'Describe the requested plan change.'})
    for action, description in (
        ('validate', 'Validate plan structure, coverage and inspected evidence; does not prove or publish.'),
        ('preflight', 'Validate plan and load declarations/imports in Isabelle; does not prove or publish.'),
        ('submit', 'Validate, preflight and publish the plan; publication of a replacement invalidates current proof authority.'),
    ):
        if not allow_revision and action == 'submit':
            description = 'Validate, preflight and publish the immutable initial plan.'
        result[action] = operation(action, {
            'proposal': {'type': 'object', 'description':
                'Complete repository proposal. Obtain proposal_schema and schema_help with action=schema. '
                'Versions 4, 5 and 6 retain common-harness validation and detailed semantic errors.'},
        }, ['proposal'], description if allow_revision else
            description + ' The initial accepted allocation and imports are immutable for this run.',
            {'proposal': example_proposal()})
    return result


def request_schema(*, allow_revision=True):
    return {'oneOf': list(operations(allow_revision=allow_revision).values()),
            'description': 'repository_plan request. Each action accepts only its documented fields.'}


def contracts(*, allow_revision=True):
    result = {
        'tool': 'repository_plan', 'version': 1,
        'operations': operations(allow_revision=allow_revision),
        'call_shape': {'request': {'action': 'contracts'}},
        'field_conventions': {
            'values': 'A JSON list of strings, never query/name/ids. Use public contract IDs or returned premise IDs as the selected action requires.',
            'limit': 'Default 20, maximum 50; supported by search, definitions and related.',
            'offset': 'Default 0; supported by definitions and related; use returned next_offset.',
            'proposal': 'Complete plan object. schema_help and proposal_schema describe version 6; common validation also supports legacy plan formats.',
        },
        'result_semantics': {
            'valid_false': 'The operation was rejected. Read errors/error and correct the request; no plan was accepted.',
            'records_empty': 'A valid retrieval with no visible matches; not an input-format error.',
            'submitted': 'Common planning preflight passed; no proof has been established.',
            'accepted': 'Plan publication completed, with epoch/run/allocation identity.',
        },
        'examples_are_syntax_only': True,
    }
    if not allow_revision:
        result['plan_policy'] = 'initial-plan-immutable-after-acceptance'
    return result


def validate_request(request, *, allow_revision=True):
    choices = operations(allow_revision=allow_revision)
    action = request.get('action') if isinstance(request, dict) else None
    if not isinstance(action, str) or action not in choices:
        raise ValueError('$.request.action must be one of: ' + ', '.join(choices) +
                         '. Example: {"request":{"action":"contracts"}}')
    errors = list(Draft202012Validator(choices[action]).iter_errors(request))
    if errors:
        messages = []
        for error in errors[:5]:
            path = '$.request' + ''.join('.' + str(p) for p in error.absolute_path)
            messages.append(path + ': ' + error.message)
        example = json.dumps({'request': choices[action]['examples'][0]}, ensure_ascii=False)
        raise ValueError('; '.join(messages) + '. Example: ' + example)



def schema_help(*, allow_revision=True):
    """Mechanical planning contract, without prescribing a grouping strategy."""
    return (
        'Submit a complete proposal using the supplied proposal_schema. Assign every fixed contract exactly once. '
        'Preserve the exact statements and contexts. Choose theory count, grouping, order and helpers yourself. '
        'required_imports form an acyclic graph; imported generated providers must be sealed before use, '
        'and imports freeze when a theory starts. Theory array order is ready priority, not a proof dependency. '
        'context_profile_ids must refer to the supplied legal context profiles. '
        'contract_relations require returned evidence IDs; similarity alone does not certify applicability. '
        'helper_decisions, collaboration_groups and reuse_suggestions are optional intentions/advice; '
        'empty lists are valid and unproved material never becomes a premise. '
        'validate checks the proposal, preflight elaborates its declarations, and submit publishes the plan. '
        'Inspect returned valid/errors/submitted/accepted fields; a tool response does not imply acceptance. '
        'No proof has been established by plan acceptance. '
        + ('Use begin_revision before replacing an accepted plan; a replacement invalidates existing proof authority '
           'until rechecked in the new context.' if allow_revision else
           'This interface accepts one initial plan only; after acceptance its theories, contract allocation, '
           'dependencies and imports are immutable for the run.')
    )
