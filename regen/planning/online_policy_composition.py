"""Fail-closed composition of online proof search and proof acceptance.

generation_strategy historically selected both prompt/search behaviour and
bounded-prefix acceptance. This module keeps those axes independent and, for
the experimental AND/OR arm, makes every state transition replayable against a
specific frozen arm instance and proof journal.

It is intentionally not a File Planner runner. A focus prompt is not a
scheduling boundary. A caller must stop at every directive, execute only that
directive, and feed an authority-bound event back before continuing.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import re

from regen.execution.proof_commits import ProofCommits, digest, durable
from regen.infrastructure.incremental_log import read_artifact
from regen.planning.bounded_prefix import STRATEGY as BOUNDED_PREFIX
from regen.planning.bounded_prefix import run_prefix
from regen.planning.contract_and_or import (
    SCHEMA as AND_OR_SEARCH,
    apply_graph_revision,
    apply_node_results,
    apply_parent_probe,
    next_decision,
    validate_problem,
)
from regen.planning.theory_document import candidate, source_hash


SCHEMA = 'online-proof-policy-composition-v2'
PLAN_SCHEMA = 'contract-and-or-controlled-plan-v1'
BINDING_SCHEMA = 'online-proof-arm-execution-binding-v1'
RECEIPT_SCHEMA = 'online-proof-step-receipt-v1'
CANDIDATE_SCHEMA = 'online-proof-candidate-binding-v1'
PROBE_ARTIFACT_SCHEMA = 'and-or-native-parent-probe-v1'
NATIVE_STEP_BINDING_SCHEMA = 'and-or-native-step-binding-v1'
ARM_ATTESTATION_SCHEMA = 'contract-and-or-arm-instance-v1'
ARM_ATTESTATION_NAME = 'and-or-arm-attestation.json'
EXISTING_SEARCH = 'existing-file-planner-v1'
SEARCH_POLICIES = frozenset({EXISTING_SEARCH, AND_OR_SEARCH})
ACCEPTANCE_POLICIES = frozenset({BOUNDED_PREFIX})
SHA256 = re.compile(r'^[0-9a-f]{64}$')


def _sha(value, field):
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise ValueError(field + ' must be a SHA-256 identity')
    return value


def _nonempty(value, field):
    if not isinstance(value, str) or not value:
        raise ValueError(field + ' must be a non-empty string')
    return value


def validate_online_problem(problem):
    """Require every schedulable ID to name one frozen declaration."""
    validate_problem(problem)
    for goal in problem['goals']:
        required = {'id', 'role', 'name', 'statement', 'context'}
        if not required <= set(goal):
            raise ValueError(
                'online goal lacks frozen declaration identity: ' + goal['id'])
        if (goal['role'] not in {
                'contract', 'helper', 'theorem', 'residual'}
                or not isinstance(goal['name'], str) or not goal['name']
                or not isinstance(goal['statement'], str)
                or not goal['statement']
                or not isinstance(goal['context'], list)
                or any(not isinstance(item, str)
                       for item in goal['context'])):
            raise ValueError(
                'invalid frozen online declaration: ' + goal['id'])
    return deepcopy(problem)


def _declaration(node):
    return {key: deepcopy(node.get(key))
            for key in ('id', 'name', 'role', 'statement', 'context')}


def _document_order(state):
    graph = state['graph']
    order = graph.get('document_order')
    if not isinstance(order, list):
        accepted = list(state.get('accepted_order', []))
        order = accepted + [key for key in graph['execution_order']
                            if key not in set(accepted)]
    return order


def validate_authorized_candidate(
        problem, document, projected, goal_id, *, require_proof):
    """Reject mathematical work outside one prescriptive directive."""
    validate_online_problem(problem)
    goals = {goal['id']: goal for goal in problem['goals']}
    if goal_id not in goals:
        raise ValueError('directive selects an unknown frozen goal')
    old_nodes = document.get('graph', {}).get('nodes', [])
    new_nodes = projected.get('graph', {}).get('nodes', [])
    before = {node['id']: _declaration(node) for node in old_nodes}
    after = {node['id']: _declaration(node) for node in new_nodes}
    if (set(before) - {goal_id} != set(after) - {goal_id}
            or any(before[key] != after[key]
                   for key in set(before) - {goal_id})):
        raise ValueError('draft changes an off-route declaration')
    old_other_order = [key for key in _document_order(document)
                       if key != goal_id]
    new_other_order = [key for key in _document_order(projected)
                       if key != goal_id]
    if old_other_order != new_other_order:
        raise ValueError('draft reorders off-route declarations')
    expected = {key: deepcopy(goals[goal_id][key])
                for key in ('id', 'name', 'role', 'statement', 'context')}
    if after.get(goal_id) != expected:
        raise ValueError('directive goal differs from frozen declaration')
    if goal_id in before:
        if before[goal_id] != expected:
            raise ValueError(
                'authoritative goal differs from frozen declaration')
        if _document_order(document) != _document_order(projected):
            raise ValueError('draft reorders the directive declaration')
    old_proofs = document.get('proofs', {})
    new_proofs = projected.get('proofs', {})
    if any(new_proofs.get(key) != body
           for key, body in old_proofs.items()):
        raise ValueError('draft changes an accepted proof')
    additions = set(new_proofs) - set(old_proofs)
    if not additions <= {goal_id}:
        raise ValueError('draft contains an off-route proof body')
    if require_proof and additions != {goal_id}:
        raise ValueError(
            'checked draft must add exactly the directive goal proof')
    return projected


def arm_policy(label):
    """Return one arm; only the search policy differs between A and B."""
    if label not in {'A', 'B'}:
        raise ValueError('arm label must be A or B')
    return {
        'schema': SCHEMA,
        'arm': label,
        'search_policy': EXISTING_SEARCH if label == 'A' else AND_OR_SEARCH,
        'acceptance_policy': BOUNDED_PREFIX,
        'runner_generation_strategy': BOUNDED_PREFIX,
    }


def comparison_arms():
    return [arm_policy('A'), arm_policy('B')]


def validate_arm(policy):
    if not isinstance(policy, dict) or policy.get('schema') != SCHEMA:
        raise ValueError('invalid online policy composition')
    expected = arm_policy(policy.get('arm'))
    if policy != expected:
        raise ValueError('arm changes more than the frozen search-policy axis')
    if policy['search_policy'] not in SEARCH_POLICIES:
        raise ValueError('unknown search policy')
    if policy['acceptance_policy'] not in ACCEPTANCE_POLICIES:
        raise ValueError('unknown acceptance policy')
    return deepcopy(policy)


def _snapshot(journal):
    if not isinstance(journal, ProofCommits):
        raise ValueError('online acceptance requires the ProofCommits authority')
    value = journal.replay_snapshot()
    if value is None:
        raise ValueError('online acceptance requires an initialized journal')
    return value


def _arm_run_root(journal):
    directory = Path(journal.directory).resolve()
    if (directory.name != 'acceptance'
            or directory.parent.parent.name != 'theories'):
        raise ValueError(
            'AND/OR journal must be inside run/theories/<theory>/acceptance')
    return directory.parents[2]


def _attestation(path):
    path = Path(path)
    if (not path.is_absolute() or str(path.resolve()) != str(path)
            or not path.is_file() or path.is_symlink()
            or path.name != ARM_ATTESTATION_NAME):
        raise ValueError('invalid AND/OR arm attestation path')
    value = read_artifact(path)
    fields = {
        'schema', 'plan_id', 'arm_id', 'arm_instance_id', 'case_id',
        'arm_label', 'search_policy', 'input_binding_sha256',
        'application_manifest_sha256', 'checkpoint_sha256',
        'pristine_provenance_sha256', 'prepared_manifest_sha256',
        'problem_sha256', 'package_sha256', 'journal_binding_sha256',
        'run_directory', 'theory', 'target_contract_id',
        'initial_journal_commit_id', 'initial_document_state_sha256',
        'attestation_id',
    }
    if (not isinstance(value, dict) or set(value) != fields
            or value.get('schema') != ARM_ATTESTATION_SCHEMA):
        raise ValueError('invalid AND/OR arm attestation')
    for field in (
            'plan_id', 'arm_instance_id', 'input_binding_sha256',
            'application_manifest_sha256', 'checkpoint_sha256',
            'pristine_provenance_sha256', 'prepared_manifest_sha256',
            'problem_sha256', 'package_sha256', 'journal_binding_sha256',
            'initial_journal_commit_id', 'initial_document_state_sha256'):
        _sha(value.get(field), 'attestation.' + field)
    for field in (
            'arm_id', 'case_id', 'arm_label', 'search_policy',
            'run_directory', 'theory', 'target_contract_id'):
        _nonempty(value.get(field), 'attestation.' + field)
    if value['attestation_id'] != digest({
            key: item for key, item in value.items()
            if key != 'attestation_id'}):
        raise ValueError('AND/OR arm attestation identity mismatch')
    if value['checkpoint_sha256'] != value['prepared_manifest_sha256']:
        raise ValueError(
            'checkpoint_sha256 must equal prepared_manifest_sha256')
    return value


def make_arm_attestation(*, problem, package, journal, policy,
                         plan_id, arm_id, case_name, arm_instance_id,
                         input_binding_sha256, checkpoint_sha256,
                         pristine_provenance_sha256,
                         prepared_manifest_sha256,
                         application_manifest_sha256):
    """Derive one arm attestation from its current journal checkpoint."""
    policy = validate_arm(policy)
    validate_online_problem(problem)
    _sha(plan_id, 'plan_id')
    _nonempty(arm_id, 'arm_id')
    _nonempty(case_name, 'case_name')
    _sha(arm_instance_id, 'arm_instance_id')
    _sha(input_binding_sha256, 'input_binding_sha256')
    _sha(checkpoint_sha256, 'checkpoint_sha256')
    _sha(pristine_provenance_sha256, 'pristine_provenance_sha256')
    _sha(prepared_manifest_sha256, 'prepared_manifest_sha256')
    _sha(application_manifest_sha256, 'application_manifest_sha256')
    if checkpoint_sha256 != prepared_manifest_sha256:
        raise ValueError(
            'checkpoint_sha256 must equal prepared_manifest_sha256')
    if not isinstance(package, dict):
        raise ValueError('package must be an object')
    snapshot = _snapshot(journal)
    run_root = _arm_run_root(journal)
    value = {
        'schema': ARM_ATTESTATION_SCHEMA,
        'plan_id': plan_id,
        'arm_id': arm_id,
        'arm_instance_id': arm_instance_id,
        'case_id': case_name,
        'arm_label': policy['arm'],
        'search_policy': policy['search_policy'],
        'input_binding_sha256': input_binding_sha256,
        'application_manifest_sha256': application_manifest_sha256,
        'checkpoint_sha256': checkpoint_sha256,
        'pristine_provenance_sha256': pristine_provenance_sha256,
        'prepared_manifest_sha256': prepared_manifest_sha256,
        'problem_sha256': digest(problem),
        'package_sha256': digest(package),
        'journal_binding_sha256': digest(journal.binding),
        'run_directory': str(run_root),
        'theory': package.get('theory'),
        'target_contract_id': problem.get('root_goal'),
        'initial_journal_commit_id': snapshot['commit_id'],
        'initial_document_state_sha256': digest(snapshot['state']),
    }
    value['attestation_id'] = digest(value)
    # Run the schema validator before any immutable file is created.
    for field in ('theory', 'target_contract_id'):
        _nonempty(value[field], 'attestation.' + field)
    return deepcopy(value)


def write_arm_attestation(**arguments):
    """Create and immediately verify the canonical immutable arm sidecar."""
    journal = arguments.get('journal')
    value = make_arm_attestation(**arguments)
    path = _arm_run_root(journal)/ARM_ATTESTATION_NAME
    durable(path, value, immutable=True)
    locator = verify_arm_attestation(path, **arguments)
    return {
        'path': str(path),
        'attestation': value,
        'locator': locator,
    }


def verify_arm_attestation(path, *, problem, package, journal, policy,
                           plan_id, arm_id, case_name, arm_instance_id,
                           input_binding_sha256,
                           checkpoint_sha256, pristine_provenance_sha256,
                           prepared_manifest_sha256,
                           application_manifest_sha256):
    """Bind an isolated arm journal to one frozen experiment instance."""
    policy = validate_arm(policy)
    run_root = _arm_run_root(journal)
    expected_path = run_root/ARM_ATTESTATION_NAME
    if Path(path).resolve() != expected_path:
        raise ValueError(
            'arm attestation must be at the journal-derived run root')
    value = _attestation(expected_path)
    expected = {
        'plan_id': plan_id,
        'arm_id': arm_id,
        'arm_instance_id': arm_instance_id,
        'case_id': case_name,
        'arm_label': policy['arm'],
        'search_policy': policy['search_policy'],
        'input_binding_sha256': input_binding_sha256,
        'application_manifest_sha256': application_manifest_sha256,
        'checkpoint_sha256': checkpoint_sha256,
        'pristine_provenance_sha256': pristine_provenance_sha256,
        'prepared_manifest_sha256': prepared_manifest_sha256,
        'problem_sha256': digest(problem),
        'package_sha256': digest(package),
        'theory': package.get('theory'),
        'target_contract_id': problem.get('root_goal'),
        'journal_binding_sha256': digest(journal.binding),
        'run_directory': str(run_root),
    }
    if any(value.get(key) != item for key, item in expected.items()):
        raise ValueError(
            'arm attestation differs from the frozen execution instance')
    initial = journal.replay_snapshot(
        commit_id=value['initial_journal_commit_id'])
    if (digest(initial['state'])
            != value['initial_document_state_sha256']):
        raise ValueError(
            'arm attestation initial journal checkpoint differs')
    return {
        'path': str(expected_path),
        'raw_sha256': hashlib.sha256(
            expected_path.read_bytes()).hexdigest(),
        'content_sha256': digest(value),
        'attestation_id': value['attestation_id'],
    }


def make_execution_binding(*, plan_id, arm_id, case_name, arm_instance_id,
                           arm_label, input_binding_sha256,
                           checkpoint_sha256, pristine_provenance_sha256,
                           prepared_manifest_sha256,
                           application_manifest_sha256,
                           problem, package, journal, document_state,
                           artifact_root, arm_attestation_path):
    """Bind one arm instance to its exact application, journal and document."""
    _sha(plan_id, 'plan_id')
    _nonempty(arm_id, 'arm_id')
    _sha(arm_instance_id, 'arm_instance_id')
    _sha(input_binding_sha256, 'input_binding_sha256')
    _sha(checkpoint_sha256, 'checkpoint_sha256')
    _sha(pristine_provenance_sha256, 'pristine_provenance_sha256')
    _sha(prepared_manifest_sha256, 'prepared_manifest_sha256')
    if checkpoint_sha256 != prepared_manifest_sha256:
        raise ValueError(
            'checkpoint_sha256 must equal prepared_manifest_sha256')
    _sha(application_manifest_sha256, 'application_manifest_sha256')
    _nonempty(case_name, 'case_name')
    if arm_label not in {'A', 'B'}:
        raise ValueError('arm_label must be A or B')
    validate_online_problem(problem)
    if not isinstance(package, dict) or not isinstance(document_state, dict):
        raise ValueError('package and document_state must be objects')
    snap = _snapshot(journal)
    if snap['state'] != document_state:
        raise ValueError('document_state is not the authoritative journal head')
    source_identity = document_state.get('document_source_sha256')
    if source_identity is not None:
        _sha(source_identity, 'document_source_sha256')
    policy = arm_policy(arm_label)
    attestation = verify_arm_attestation(
        arm_attestation_path, problem=problem, package=package,
        journal=journal, policy=policy, plan_id=plan_id, arm_id=arm_id,
        case_name=case_name, arm_instance_id=arm_instance_id,
        input_binding_sha256=input_binding_sha256,
        checkpoint_sha256=checkpoint_sha256,
        pristine_provenance_sha256=pristine_provenance_sha256,
        prepared_manifest_sha256=prepared_manifest_sha256,
        application_manifest_sha256=application_manifest_sha256)
    value = {
        'schema': BINDING_SCHEMA,
        'plan_id': plan_id,
        'arm_id': arm_id,
        'case_name': case_name,
        'arm_instance_id': arm_instance_id,
        'arm_label': arm_label,
        'input_binding_sha256': input_binding_sha256,
        'checkpoint_sha256': checkpoint_sha256,
        'pristine_provenance_sha256': pristine_provenance_sha256,
        'prepared_manifest_sha256': prepared_manifest_sha256,
        'application_manifest_sha256': application_manifest_sha256,
        'problem_sha256': digest(problem),
        'package_sha256': digest(package),
        'theory': package.get('theory'),
        'target_contract_id': problem.get('root_goal'),
        'journal_directory': str(Path(journal.directory).resolve()),
        'journal_binding_sha256': digest(journal.binding),
        'journal_head_commit_id': snap['commit_id'],
        'journal_head_sequence': len(snap['records']),
        'document_state_sha256': digest(document_state),
        'document_source_sha256': source_identity,
        'accepted_order_sha256': digest(document_state.get('accepted_order')),
        'proofs_sha256': digest(document_state.get('proofs')),
        'artifact_root': str(Path(artifact_root).resolve()),
        'arm_attestation': attestation,
    }
    validate_execution_binding(
        value, policy, package=package, journal=journal,
        document_state=document_state)
    return value


_BINDING_FIELDS = {
    'schema', 'plan_id', 'arm_id', 'case_name', 'arm_instance_id', 'arm_label',
    'input_binding_sha256',
    'checkpoint_sha256', 'application_manifest_sha256', 'package_sha256',
    'pristine_provenance_sha256', 'prepared_manifest_sha256',
    'problem_sha256', 'theory', 'target_contract_id', 'arm_attestation',
    'journal_directory', 'journal_binding_sha256', 'journal_head_commit_id',
    'journal_head_sequence', 'document_state_sha256',
    'document_source_sha256', 'accepted_order_sha256', 'proofs_sha256',
    'artifact_root',
}


def validate_execution_binding(binding, policy, *, package=None, journal=None,
                               document_state=None, require_current=True):
    policy = validate_arm(policy)
    if (not isinstance(binding, dict) or set(binding) != _BINDING_FIELDS
            or binding.get('schema') != BINDING_SCHEMA):
        raise ValueError('invalid arm execution binding')
    for field in (
            'plan_id', 'arm_instance_id', 'input_binding_sha256',
            'checkpoint_sha256',
            'pristine_provenance_sha256', 'prepared_manifest_sha256',
            'application_manifest_sha256', 'package_sha256',
            'problem_sha256',
            'journal_binding_sha256', 'journal_head_commit_id',
            'document_state_sha256', 'accepted_order_sha256', 'proofs_sha256'):
        _sha(binding.get(field), field)
    if binding['document_source_sha256'] is not None:
        _sha(binding['document_source_sha256'], 'document_source_sha256')
    _nonempty(binding.get('case_name'), 'case_name')
    _nonempty(binding.get('arm_id'), 'arm_id')
    _nonempty(binding.get('theory'), 'theory')
    _nonempty(binding.get('target_contract_id'), 'target_contract_id')
    if binding.get('arm_label') != policy['arm']:
        raise ValueError('execution binding belongs to another arm')
    if (type(binding.get('journal_head_sequence')) is not int
            or binding['journal_head_sequence'] < 1):
        raise ValueError('journal_head_sequence must be a positive integer')
    for field in ('journal_directory', 'artifact_root'):
        path = Path(_nonempty(binding.get(field), field))
        if not path.is_absolute() or str(path.resolve()) != str(path):
            raise ValueError(field + ' must be an absolute normalized path')
    attestation = binding.get('arm_attestation')
    if (not isinstance(attestation, dict)
            or set(attestation) != {
                'path', 'raw_sha256', 'content_sha256', 'attestation_id'}):
        raise ValueError('invalid bound arm attestation locator')
    for field in ('raw_sha256', 'content_sha256', 'attestation_id'):
        _sha(attestation.get(field), 'arm_attestation.' + field)
    value = _attestation(attestation.get('path'))
    path = Path(attestation['path'])
    journal_directory = Path(binding['journal_directory'])
    if (journal_directory.name != 'acceptance'
            or journal_directory.parent.parent.name != 'theories'
            or path != journal_directory.parents[2]/ARM_ATTESTATION_NAME):
        raise ValueError(
            'bound arm attestation is outside the journal-derived run root')
    if (hashlib.sha256(path.read_bytes()).hexdigest()
            != attestation['raw_sha256']
            or digest(value) != attestation['content_sha256']
            or value['attestation_id'] != attestation['attestation_id']):
        raise ValueError('bound arm attestation content changed')
    expected_attestation = {
        'plan_id': binding['plan_id'],
        'arm_id': binding['arm_id'],
        'arm_instance_id': binding['arm_instance_id'],
        'case_id': binding['case_name'],
        'arm_label': binding['arm_label'],
        'search_policy': policy['search_policy'],
        'input_binding_sha256': binding['input_binding_sha256'],
        'application_manifest_sha256':
            binding['application_manifest_sha256'],
        'checkpoint_sha256': binding['checkpoint_sha256'],
        'pristine_provenance_sha256':
            binding['pristine_provenance_sha256'],
        'prepared_manifest_sha256':
            binding['prepared_manifest_sha256'],
        'problem_sha256': binding['problem_sha256'],
        'package_sha256': binding['package_sha256'],
        'journal_binding_sha256': binding['journal_binding_sha256'],
        'run_directory': str(journal_directory.parents[2]),
        'theory': binding['theory'],
        'target_contract_id': binding['target_contract_id'],
    }
    if any(value.get(key) != item
           for key, item in expected_attestation.items()):
        raise ValueError('bound arm attestation differs from execution binding')
    if package is not None and digest(package) != binding['package_sha256']:
        raise ValueError('package differs from frozen arm binding')
    if document_state is not None:
        expected = {
            'document_state_sha256': digest(document_state),
            'document_source_sha256':
                document_state.get('document_source_sha256'),
            'accepted_order_sha256':
                digest(document_state.get('accepted_order')),
            'proofs_sha256': digest(document_state.get('proofs')),
        }
        if any(binding[key] != value for key, value in expected.items()):
            raise ValueError('authoritative document identity drift')
    if journal is not None:
        if not isinstance(journal, ProofCommits):
            raise ValueError(
                'online acceptance requires the ProofCommits authority')
        if (str(Path(journal.directory).resolve())
                != binding['journal_directory']
                or digest(journal.binding) != binding['journal_binding_sha256']):
            raise ValueError('journal authority differs from arm binding')
        if require_current:
            snap = _snapshot(journal)
            if (snap['commit_id'] != binding['journal_head_commit_id']
                    or len(snap['records'])
                    != binding['journal_head_sequence']):
                raise ValueError('journal head advanced outside this directive')
            if document_state is not None and snap['state'] != document_state:
                raise ValueError('journal head and document_state differ')
    return deepcopy(binding)


def _candidate_binding(value, *, goal_id=None, route_id=None):
    fields = {
        'schema', 'goal_id', 'route_id', 'checked_source_sha256',
        'proof_body_sha256', 'native_evidence_sha256',
        'base_document_state_sha256',
    }
    if (not isinstance(value, dict) or set(value) != fields
            or value.get('schema') != CANDIDATE_SCHEMA):
        raise ValueError('invalid frozen proof candidate binding')
    if goal_id is not None and value.get('goal_id') != goal_id:
        raise ValueError('candidate belongs to another goal')
    if route_id is not None and value.get('route_id') != route_id:
        raise ValueError('candidate belongs to another route')
    for field in fields - {'schema', 'goal_id', 'route_id'}:
        _sha(value.get(field), 'candidate.' + field)
    _nonempty(value.get('goal_id'), 'candidate.goal_id')
    _nonempty(value.get('route_id'), 'candidate.route_id')
    return deepcopy(value)


def _decision_execution(decision, state):
    action = decision['action']
    if action == 'PROVE':
        return 'planner_proof', [decision['goal_id']]
    if action == 'RECHECK_PREFIX':
        goal, route = decision['goal_ids'][0], decision['route_ids'][0]
        frozen = _candidate_binding(
            state.get('candidate_artifacts', {}).get(goal),
            goal_id=goal, route_id=route)
        decision['scheduled_goal_id'] = goal
        decision['scheduled_route_id'] = route
        decision['candidate_binding'] = frozen
        return 'native_recheck', [goal]
    if action == 'PROBE_PARENT':
        return 'native_parent_probe', []
    if action == 'REVISE_GRAPH':
        return 'supervisor_graph_revision', []
    if action in {'SOLVED', 'BLOCKED'}:
        return 'terminal', []
    raise ValueError('unsupported AND/OR decision: ' + str(action))


def next_online_directive(problem, state, policy, execution_binding):
    """Bind one action to a unique arm, checkpoint, journal head and overlay."""
    validate_online_problem(problem)
    policy = validate_arm(policy)
    binding = validate_execution_binding(execution_binding, policy)
    if (binding['problem_sha256'] != digest(problem)
            or binding['target_contract_id'] != problem['root_goal']):
        raise ValueError('problem differs from frozen arm binding')
    if policy['search_policy'] == EXISTING_SEARCH:
        decision = {
            'action': 'PLANNER_TURN',
            'notice': 'The existing File Planner chooses its next proof action.',
        }
        execution, eligible = 'planner_turn', None
    else:
        decision = deepcopy(next_decision(problem, state))
        execution, eligible = _decision_execution(decision, state)
    value = {
        'schema': SCHEMA,
        'arm': policy['arm'],
        'search_policy': policy['search_policy'],
        'acceptance_policy': policy['acceptance_policy'],
        'runner_generation_strategy': policy['runner_generation_strategy'],
        'execution_binding': binding,
        'problem_sha256': digest(problem),
        'state_before_sha256': digest(state),
        'execution': execution,
        'eligible_goal_ids': eligible,
        'decision': decision,
    }
    value['directive_id'] = digest(value)
    return value


def validate_fresh_directive(problem, state, policy, execution_binding,
                             directive):
    expected = next_online_directive(
        problem, state, policy, execution_binding)
    if directive != expected:
        raise ValueError('stale, cross-arm, or altered online search directive')
    return expected


def _proof_delta(before, after):
    old, new = before.get('proofs'), after.get('proofs')
    old_order = before.get('accepted_order')
    new_order = after.get('accepted_order')
    if (not isinstance(old, dict) or not isinstance(new, dict)
            or not isinstance(old_order, list) or not isinstance(new_order, list)
            or new_order[:len(old_order)] != old_order
            or any(new.get(key) != body for key, body in old.items())):
        raise ValueError('commit changed the authoritative accepted prefix')
    delta = [key for key in new_order if key not in old]
    if set(new) - set(old) != set(delta) or len(delta) != 1:
        raise ValueError('one online commit must add exactly one proof')
    return delta[0]


def proof_document_identity(state):
    """Identity of accepted mathematics, excluding request/feedback counters."""
    if not isinstance(state, dict):
        raise ValueError('proof document state must be an object')
    graph = state.get('graph')
    nodes = graph.get('nodes') if isinstance(graph, dict) else None
    if not isinstance(nodes, list):
        raise ValueError('proof document state lacks declaration nodes')
    declarations = [{
        key: deepcopy(node.get(key))
        for key in ('id', 'name', 'role', 'statement', 'context')
    } for node in nodes]
    return digest({
        'declarations': declarations,
        'proofs': state.get('proofs'),
        'accepted_order': state.get('accepted_order'),
        'document_source_sha256': state.get('document_source_sha256'),
    })


def _commit_record(journal, sequence):
    path = Path(journal.directory)/'commits'/f'{sequence:06d}.json'
    record = read_artifact(path)
    body = {key: value for key, value in record.items()
            if key != 'commit_id'}
    if record.get('commit_id') != digest(body):
        raise ValueError('journal commit content identity mismatch')
    return record


def _journal_range(journal, binding):
    """Replay every record after a directive, including control-only commits."""
    validate_execution_binding(
        binding, arm_policy(binding['arm_label']), journal=journal,
        require_current=False)
    current = _snapshot(journal)
    start = binding['journal_head_sequence']
    if len(current['records']) < start:
        raise ValueError('journal history predates the directive head')
    prior = journal.replay_snapshot(
        commit_id=binding['journal_head_commit_id'])
    if (len(prior['records']) != start
            or digest(prior['state']) != binding['document_state_sha256']):
        raise ValueError('journal predecessor differs from directive document')
    records = [
        _commit_record(journal, sequence)
        for sequence in range(start + 1, len(current['records']) + 1)
    ]
    return prior, current, records


def native_step_binding(directive):
    binding = directive['execution_binding']
    return {
        'schema': NATIVE_STEP_BINDING_SCHEMA,
        'directive_id': directive['directive_id'],
        'plan_id': binding['plan_id'],
        'arm_id': binding['arm_id'],
        'arm_instance_id': binding['arm_instance_id'],
        'old_journal_head_commit_id':
            binding['journal_head_commit_id'],
        'eligible_goal_ids': deepcopy(
            directive['eligible_goal_ids']),
    }


def replay_callback_protocol(directive, journal, *, require_feedback=True):
    """Replay one File Planner callback after the directive head."""
    prior, current, records = _journal_range(
        journal, directive['execution_binding'])
    previous = prior['state']
    index = 0
    resume_seen = False
    while index < len(records):
        record = records[index]
        event, evidence = record.get('event'), record.get('evidence', {})
        allowed_resume = event == 'resume' and not resume_seen
        allowed_merge = (event == 'planner-feedback'
            and evidence.get('reason')
            == 'merge verified read-only task receipts; no proof authority')
        if not (allowed_resume or allowed_merge):
            break
        if proof_document_identity(record['state']) \
                != proof_document_identity(previous):
            raise ValueError('callback control event changed accepted proofs')
        resume_seen = resume_seen or allowed_resume
        previous = record['state']
        index += 1
    if index >= len(records) or records[index].get('event') != 'planner-request':
        raise ValueError('callback has no unique new planner request')
    request_record = records[index]
    request = request_record.get('evidence', {}).get('request_number')
    if (type(request) is not int or request < 1
            or request_record['state'].get('batch_requests') != request
            or request != previous.get('batch_requests', 0) + 1):
        raise ValueError('callback planner request identity mismatch')
    if proof_document_identity(request_record['state']) \
            != proof_document_identity(previous):
        raise ValueError('planner request changed accepted proofs')
    previous = request_record['state']
    index += 1
    document = None
    if index < len(records) and records[index].get('event') == 'document':
        document = records[index]
        evidence = document.get('evidence', {})
        if (evidence.get('parent_request_number') != request
                or evidence.get('online_control')
                != native_step_binding(directive)):
            raise ValueError(
                'document commit is not bound to this callback directive')
        previous = document['state']
        index += 1
    feedback = None
    if index < len(records) and records[index].get('event') == 'planner-feedback':
        feedback = records[index]
        if feedback.get('evidence', {}).get('request_number') != request:
            raise ValueError('planner feedback request identity mismatch')
        if proof_document_identity(feedback['state']) \
                != proof_document_identity(previous):
            raise ValueError('planner feedback changed accepted proofs')
        previous = feedback['state']
        index += 1
    if index != len(records):
        raise ValueError('callback journal contains extra or reordered events')
    if require_feedback and feedback is None:
        raise ValueError('completed callback lacks planner feedback')
    return {
        'prior': prior, 'current': current, 'records': records,
        'request_number': request, 'document': document,
        'feedback': feedback,
    }


def _verify_no_proof_change(directive, journal):
    protocol = replay_callback_protocol(directive, journal)
    prior, current, records = (
        protocol['prior'], protocol['current'], protocol['records'])
    identity = proof_document_identity(prior['state'])
    for record in records:
        if proof_document_identity(record['state']) != identity:
            raise ValueError(
                'uncommitted tool result changed authoritative proofs')
    return prior, current, records


def _artifact_locator(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    if not path.is_file() or path.is_symlink() or not path.is_relative_to(root):
        raise ValueError('native artifact is outside the frozen arm root')
    return {
        'path': str(path),
        'raw_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'content_sha256': digest(read_artifact(path)),
    }


def verify_artifact_locator(locator, binding):
    """Dereference an immutable artifact inside the bound theory root."""
    if (not isinstance(locator, dict)
            or set(locator) != {
                'path', 'raw_sha256', 'content_sha256'}):
        raise ValueError('invalid artifact locator')
    path = Path(locator['path'])
    root = Path(binding['artifact_root'])
    if (not path.is_absolute() or str(path.resolve()) != str(path)
            or not path.is_file() or path.is_symlink()
            or not path.is_relative_to(root)):
        raise ValueError('artifact locator escapes the frozen arm root')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != locator['raw_sha256']:
        raise ValueError('artifact raw bytes changed')
    value = read_artifact(path)
    if digest(value) != locator['content_sha256']:
        raise ValueError('artifact decoded content changed')
    return value


def acceptance_payload_from_task(directive, journal, check_path):
    """Derive, never guess, one event payload from task and journal artifacts."""
    binding = directive['execution_binding']
    goal, _ = _directive_route(directive)
    protocol = replay_callback_protocol(directive, journal)
    request = protocol['request_number']
    check_path = Path(check_path).resolve()
    request_root = (Path(binding['artifact_root'])/'batch-requests'
                    / f'{request:06d}'/'bounded-prefix').resolve()
    if (not check_path.is_relative_to(request_root)
            or check_path.name != 'check.json'
            or check_path.parent.parent != request_root):
        raise ValueError(
            'task check artifact is outside this callback request')
    locator = _artifact_locator(check_path, binding['artifact_root'])
    evidence = read_artifact(check_path)
    bounded = evidence.get('bounded_prefix', {})
    if (bounded.get('entry_id') != goal
            or bounded.get('search_scope_enforced') is not True
            or bounded.get('eligible_ids') != [goal]
            or evidence.get('parent_request_number') != request
            or evidence.get('online_control')
            != native_step_binding(directive)):
        raise ValueError('task check artifact belongs to another directive')
    previous = protocol['prior']['state']
    changes = []
    for record in protocol['records']:
        if (proof_document_identity(previous)
                != proof_document_identity(record['state'])):
            if record.get('event') != 'document':
                raise ValueError(
                    'non-document task event changed accepted proofs')
            changes.append((
                record, _proof_delta(previous, record['state'])))
        previous = record['state']
    verdict = evidence.get('check', {}).get('verdict')
    if verdict == 'green' and len(changes) == 1:
        record, changed_goal = changes[0]
        if changed_goal != goal or record.get('evidence') != evidence:
            raise ValueError(
                'task proof commit differs from its native evidence')
        outcome, commit_id = 'committed_green', record['commit_id']
    elif verdict == 'green' and not changes:
        outcome, commit_id = 'completed_uncommitted', None
    elif verdict == 'red' and not changes:
        outcome, commit_id = 'failed', None
    elif (evidence.get('check', {}).get('failure_scope')
          == 'native_preparation') and not changes:
        outcome, commit_id = 'infrastructure_failure', None
    else:
        raise ValueError(
            'task artifact and authoritative journal have no valid outcome')
    return {
        'goal_id': goal,
        'outcome': outcome,
        'native_artifact': locator,
        'journal_commit_id': commit_id,
    }


def run_bounded_prefix_for_directive(
        problem, state, policy, execution_binding, directive,
        package, document_state, source, checker, directory, *,
        journal, evidence_origin):
    """Run one authority-bound use of the common acceptance policy."""
    directive = validate_fresh_directive(
        problem, state, policy, execution_binding, directive)
    binding = directive['execution_binding']
    validate_execution_binding(
        binding, policy, package=package, journal=journal,
        document_state=document_state)
    if directive['execution'] not in {
            'planner_turn', 'planner_proof', 'native_recheck'}:
        raise ValueError(
            'current search directive does not authorize proof acceptance')
    eligible = directive['eligible_goal_ids']
    if directive['search_policy'] == AND_OR_SEARCH:
        if not isinstance(document_state.get('proofs'), dict):
            raise ValueError(
                'AND/OR acceptance requires a document proof state')
        goal_ids = {goal['id'] for goal in problem['goals']}
        document_certified = set(document_state['proofs']) & goal_ids
        if document_certified != set(state.get('certified', [])):
            raise ValueError(
                'AND/OR overlay and authoritative document are out of sync')
        if not isinstance(eligible, list) or len(eligible) != 1:
            raise ValueError('an AND/OR proof step must authorize one goal')
        projected = candidate(
            package, document_state, source, checker.imports())
        validate_authorized_candidate(
            problem, document_state, projected, eligible[0],
            require_proof=True)
        if directive['execution'] == 'native_recheck':
            frozen = directive['decision']['candidate_binding']
            if source_hash(source) != frozen['checked_source_sha256']:
                raise ValueError('recheck source differs from frozen candidate')
            goal = eligible[0]
            if (goal not in projected['proofs']
                    or digest(projected['proofs'][goal])
                    != frozen['proof_body_sha256']):
                raise ValueError(
                    'recheck proof body differs from frozen candidate')

    request_number = document_state.get('batch_requests', 0) + 1
    request_state = deepcopy(document_state)
    request_state['batch_requests'] = request_number
    expected_state = journal.append(
        'planner-request', request_state,
        {'request_number': request_number})
    commit_receipts = []

    def commit(new, evidence):
        nonlocal expected_state
        before = _snapshot(journal)
        if before['state'] != expected_state:
            raise ValueError('journal changed between online checks')
        goal = _proof_delta(before['state'], new)
        if eligible is not None and goal not in set(eligible):
            raise ValueError('off-route proof reached commit boundary')
        result = journal.append('document', new, evidence)
        after = _snapshot(journal)
        if (after['state'] != new or result != new
                or len(after['records']) != len(before['records']) + 1):
            raise ValueError('journal append did not publish the exact candidate')
        record = _commit_record(journal, len(after['records']))
        if (record.get('parent') != before['commit_id']
                or record.get('event') != 'document'
                or record.get('state') != new
                or record.get('evidence') != evidence):
            raise ValueError('journal commit differs from checked transition')
        if _proof_delta(before['state'], after['state']) != goal:
            raise ValueError('authoritative journal delta differs from proposal')
        commit_receipts.append({
            'goal_id': goal,
            'parent_commit_id': before['commit_id'],
            'commit_id': after['commit_id'],
            'sequence': len(after['records']),
            'old_document_state_sha256': digest(before['state']),
            'new_document_state_sha256': digest(after['state']),
            'evidence_sha256': digest(evidence),
        })
        expected_state = new
        return result

    if ('online_control' in evidence_origin
            or 'parent_request_number' in evidence_origin):
        raise ValueError('caller cannot override online evidence binding')
    evidence_origin = {
        **deepcopy(evidence_origin),
        'parent_request_number': request_number,
        'online_control': native_step_binding(directive),
    }
    feedback = run_prefix(
        package, request_state, source, checker, directory,
        commit=commit, evidence_origin=evidence_origin,
        eligible_ids=eligible)
    journal.append(
        'planner-feedback', expected_state,
        {'request_number': request_number, 'feedback': feedback})
    if feedback is None:
        return None
    checks = feedback.get('bounded_prefix', {}).get('checks', [])
    payloads = []
    receipt_by_goal = {row['goal_id']: row for row in commit_receipts}
    for row in checks:
        goal = row['entry_id']
        native = _artifact_locator(
            row['evidence_path'], binding['artifact_root'])
        committed = receipt_by_goal.get(goal)
        verdict = row['verdict']
        if committed is not None:
            outcome, commit_id = 'committed_green', committed['commit_id']
        elif verdict == 'green':
            outcome, commit_id = 'completed_uncommitted', None
        elif verdict == 'red':
            outcome, commit_id = 'failed', None
        else:
            outcome, commit_id = 'infrastructure_failure', None
        payloads.append({
            'goal_id': goal,
            'outcome': outcome,
            'native_artifact': native,
            'journal_commit_id': commit_id,
        })
    control = {
        'schema': SCHEMA,
        'directive_id': directive['directive_id'],
        'old_journal_head_commit_id': binding['journal_head_commit_id'],
        'acceptance_payloads': payloads,
        'commit_receipts': commit_receipts,
    }
    durable(Path(directory)/'online-control.json', control, immutable=True)
    feedback['online_control'] = control
    return feedback


def bind_event(directive, kind, payload):
    """Create a content-addressed transition event for one directive."""
    if kind not in {'acceptance', 'parent_probe', 'graph_revision'}:
        raise ValueError('unknown online transition kind')
    if not isinstance(payload, dict):
        raise ValueError('online transition payload must be an object')
    value = {
        'schema': SCHEMA,
        'kind': kind,
        'directive_id': directive['directive_id'],
        'execution_binding': deepcopy(directive['execution_binding']),
        'problem_sha256': directive['problem_sha256'],
        'state_before_sha256': directive['state_before_sha256'],
        'search_policy': directive['search_policy'],
        'acceptance_policy': directive['acceptance_policy'],
        'payload': deepcopy(payload),
    }
    value['event_id'] = digest(value)
    return value


def _validate_event(directive, event, kind):
    if not isinstance(event, dict) or event.get('schema') != SCHEMA:
        raise ValueError('invalid online transition event')
    if event.get('kind') != kind:
        raise ValueError('online transition kind does not match action')
    expected = {key: directive[key] for key in (
        'directive_id', 'execution_binding', 'problem_sha256',
        'state_before_sha256', 'search_policy', 'acceptance_policy')}
    if any(event.get(key) != value for key, value in expected.items()):
        raise ValueError(
            'online transition is not bound to the current arm directive')
    if event.get('event_id') != digest({
            key: value for key, value in event.items()
            if key != 'event_id'}):
        raise ValueError('online transition identity mismatch')
    return event['payload']


def _seen_event(state, event_id):
    for observation in state.get('observations', []):
        evidence = observation.get('evidence')
        if isinstance(evidence, dict) and evidence.get('event_id') == event_id:
            return True
    return False


def _directive_route(directive):
    decision, goals = directive['decision'], directive['eligible_goal_ids']
    if not isinstance(goals, list) or len(goals) != 1:
        raise ValueError('current directive does not select one proof goal')
    goal = goals[0]
    if decision['action'] == 'PROVE':
        route = decision['route_id']
    elif decision['action'] == 'RECHECK_PREFIX':
        route = decision['scheduled_route_id']
    else:
        raise ValueError('current directive does not accept a proof result')
    return goal, route


def _verified_native(
        locator, verifier, *, directive, binding, goal, outcome):
    if verifier is None:
        raise ValueError('native result requires an artifact verifier')
    evidence = verifier(deepcopy(locator), deepcopy(binding))
    if not isinstance(evidence, dict):
        raise ValueError('native artifact verifier rejected the receipt')
    check = evidence.get('check')
    if not isinstance(check, dict):
        raise ValueError('native artifact lacks a checker result')
    for field in ('source', 'log'):
        content = check.get(field + '_content')
        identity = check.get(field + '_sha256')
        if (not isinstance(content, str)
                or hashlib.sha256(content.encode()).hexdigest() != identity):
            raise ValueError('native artifact embedded bytes mismatch')
    bounded = evidence.get('bounded_prefix')
    if (not isinstance(bounded, dict)
            or bounded.get('entry_id') != goal
            or bounded.get('search_scope_enforced') is not True
            or bounded.get('eligible_ids') != [goal]
            or evidence.get('online_control')
            != native_step_binding(directive)):
        raise ValueError('native artifact belongs to another proof step')
    expected = {
        'committed_green': 'green',
        'completed_uncommitted': 'green',
        'failed': 'red',
    }
    if outcome in expected and check.get('verdict') != expected[outcome]:
        raise ValueError('native verdict and declared outcome disagree')
    if outcome == 'infrastructure_failure' and (
            check.get('failure_scope') != 'native_preparation'
            and check.get('verdict') in {'green', 'red'}):
        raise ValueError(
            'infrastructure failure lacks native failure evidence')
    if source_hash(evidence.get('document_source', '')) != evidence.get(
            'document_source_sha256'):
        raise ValueError('native artifact document source mismatch')
    return evidence


def _verify_committed_goal(
        problem, journal, directive, goal, commit_id, evidence):
    protocol = replay_callback_protocol(directive, journal)
    prior, current, records = (
        protocol['prior'], protocol['current'], protocol['records'])
    previous = prior['state']
    proof_changes = []
    for record in records:
        changed = (
            proof_document_identity(previous)
            != proof_document_identity(record['state']))
        if changed:
            if record.get('event') != 'document':
                raise ValueError(
                    'non-document journal event changed accepted proofs')
            changed_goal = _proof_delta(previous, record['state'])
            proof_changes.append((record, previous, changed_goal))
        previous = record['state']
    if len(proof_changes) != 1:
        raise ValueError(
            'one online step must contain exactly one proof commit')
    record, immediate_prior, changed_goal = proof_changes[0]
    if (record.get('commit_id') != commit_id
            or record.get('evidence') != evidence):
        raise ValueError(
            'event does not replay to the authoritative proof commit')
    if changed_goal != goal:
        raise ValueError('journal accepted an off-route proof delta')
    validate_authorized_candidate(
        problem, immediate_prior, record['state'], goal,
        require_proof=True)
    if (proof_document_identity(current['state'])
            != proof_document_identity(record['state'])):
        raise ValueError('proofs changed again after the authorized commit')
    return record, immediate_prior, current


def apply_acceptance_event(
        problem, state, policy, execution_binding, directive, event, *,
        package, journal, native_artifact_verifier):
    """Advance B only after replaying the native artifact and proof journal."""
    directive = validate_fresh_directive(
        problem, state, policy, execution_binding, directive)
    if directive['search_policy'] != AND_OR_SEARCH:
        raise ValueError(
            'the existing-scheduler arm has no prescriptive AND/OR state')
    payload = _validate_event(directive, event, 'acceptance')
    if _seen_event(state, event['event_id']):
        raise ValueError('online transition was already applied')
    if set(payload) != {
            'goal_id', 'outcome', 'native_artifact',
            'journal_commit_id'}:
        raise ValueError(
            'acceptance payload has unexpected or missing fields')
    goal, route = _directive_route(directive)
    if payload['goal_id'] != goal:
        raise ValueError('native result escapes the search directive')
    outcome = payload['outcome']
    if outcome not in {
            'committed_green', 'completed_uncommitted', 'failed',
            'infrastructure_failure'}:
        raise ValueError('unknown online proof outcome')
    evidence = _verified_native(
        payload['native_artifact'], native_artifact_verifier,
        directive=directive, binding=directive['execution_binding'],
        goal=goal, outcome=outcome)
    evidence_identity = digest(evidence)
    commit_id = payload['journal_commit_id']
    journal_evidence = None
    if outcome == 'committed_green':
        _sha(commit_id, 'journal_commit_id')
        record, prior, current = _verify_committed_goal(
            problem, journal, directive, goal, commit_id,
            evidence)
        status = 'accepted_green'
        journal_evidence = {
            'parent_commit_id': record['parent'],
            'commit_id': record['commit_id'],
            'sequence': record['sequence'],
            'old_document_state_sha256': digest(prior),
            'new_document_state_sha256': digest(record['state']),
            'result_journal_head_commit_id': current['commit_id'],
            'result_journal_head_sequence': len(current['records']),
        }
    else:
        if commit_id is not None:
            raise ValueError(
                'uncommitted outcome cannot cite a journal commit')
        validate_execution_binding(
            directive['execution_binding'], policy, package=package,
            journal=journal, require_current=False)
        _verify_no_proof_change(directive, journal)
        status = {
            'completed_uncommitted': 'completed_uncommitted',
            'failed': 'failed',
            'infrastructure_failure': 'infrastructure_failure',
        }[outcome]
    result = apply_node_results(
        problem, state, [{
            'goal_id': goal, 'route_id': route, 'status': status}],
        evidence={
            'event_id': event['event_id'],
            'directive_id': directive['directive_id'],
            'native_artifact': deepcopy(payload['native_artifact']),
            'native_evidence_sha256': evidence_identity,
            'journal': journal_evidence,
        })
    candidates = result.setdefault('candidate_artifacts', {})
    if outcome == 'completed_uncommitted':
        base = journal.replay_snapshot(
            commit_id=directive['execution_binding'][
                'journal_head_commit_id'])['state']
        projected = candidate(
            package, base, evidence['document_source'],
            evidence['imports'])
        if goal not in projected['proofs']:
            raise ValueError(
                'native candidate does not contain scheduled goal')
        candidates[goal] = {
            'schema': CANDIDATE_SCHEMA,
            'goal_id': goal,
            'route_id': route,
            'checked_source_sha256':
                evidence['document_source_sha256'],
            'proof_body_sha256': digest(projected['proofs'][goal]),
            'native_evidence_sha256': evidence_identity,
            'base_document_state_sha256': digest(base),
        }
    else:
        candidates.pop(goal, None)
    return result


def _child_commit_chain(state, decision):
    wanted = set(decision['child_goals'])
    rows = []
    for observation in state.get('observations', []):
        if observation.get('kind') != 'node_results':
            continue
        accepted = {
            row['goal_id'] for row in observation.get('results', [])
            if row.get('status') == 'accepted_green'
            and row.get('goal_id') in wanted
        }
        journal = observation.get('evidence', {}).get('journal')
        if accepted and isinstance(journal, dict):
            rows.append({
                'goal_ids': sorted(accepted),
                'parent_commit_id': journal.get('parent_commit_id'),
                'commit_id': journal.get('commit_id'),
                'sequence': journal.get('sequence'),
            })
    covered = {goal for row in rows for goal in row['goal_ids']}
    if covered != wanted:
        raise ValueError(
            'parent probe lacks accepted child commit receipts')
    rows.sort(key=lambda row: row['sequence'])
    return rows


def _verified_probe_artifact(problem, state, directive, locator, verifier):
    if verifier is None:
        raise ValueError(
            'parent probe requires a native probe adapter/verifier')
    artifact = verifier(
        deepcopy(locator), deepcopy(directive['execution_binding']))
    decision = directive['decision']
    fields = {
        'schema', 'directive_id', 'plan_id', 'arm_instance_id',
        'target_goal', 'selected_route', 'child_goals',
        'parent_declaration_sha256', 'checkpoint_sha256',
        'application_manifest_sha256', 'before_journal_head_commit_id',
        'after_journal_head_commit_id', 'child_commit_ids',
        'normalization', 'before_residual', 'before_residual_sha256',
        'after_residual', 'after_residual_sha256',
        'native_receipt_sha256',
    }
    if (not isinstance(artifact, dict) or set(artifact) != fields
            or artifact.get('schema') != PROBE_ARTIFACT_SCHEMA):
        raise ValueError('invalid native parent-probe artifact')
    binding = directive['execution_binding']
    expected = {
        'directive_id': directive['directive_id'],
        'plan_id': binding['plan_id'],
        'arm_instance_id': binding['arm_instance_id'],
        'target_goal': decision['target_goal'],
        'selected_route': decision['selected_route'],
        'child_goals': sorted(decision['child_goals']),
        'checkpoint_sha256': binding['checkpoint_sha256'],
        'application_manifest_sha256':
            binding['application_manifest_sha256'],
        'after_journal_head_commit_id':
            binding['journal_head_commit_id'],
    }
    goal = next(
        row for row in problem['goals']
        if row['id'] == decision['target_goal'])
    expected['parent_declaration_sha256'] = digest({
        key: goal.get(key) for key in ('id', 'statement', 'context')})
    if any(
            artifact.get(key) != value for key, value in expected.items()):
        raise ValueError('parent-probe artifact differs from directive')
    chain = _child_commit_chain(state, decision)
    if (artifact.get('child_commit_ids')
            != [row['commit_id'] for row in chain]
            or artifact.get('before_journal_head_commit_id')
            != chain[0]['parent_commit_id']):
        raise ValueError(
            'parent probe is not bound to accepted child commits')
    if artifact.get(
            'normalization') != 'isabelle-residual-canonical-v1':
        raise ValueError('unknown residual normalization')
    for side in ('before', 'after'):
        residual = artifact.get(side + '_residual')
        if (not isinstance(residual, str) or not residual
                or digest(residual)
                != artifact.get(side + '_residual_sha256')):
            raise ValueError('parent probe residual identity mismatch')
    _sha(
        artifact.get('native_receipt_sha256'),
        'native_receipt_sha256')
    return artifact


def apply_parent_probe_event(
        problem, state, policy, execution_binding, directive, event, *,
        probe_artifact_verifier):
    directive = validate_fresh_directive(
        problem, state, policy, execution_binding, directive)
    if directive['decision']['action'] != 'PROBE_PARENT':
        raise ValueError('current directive is not a parent probe')
    payload = _validate_event(directive, event, 'parent_probe')
    if _seen_event(state, event['event_id']):
        raise ValueError('online transition was already applied')
    if set(payload) != {'probe_artifact'}:
        raise ValueError(
            'parent probe event must contain one artifact locator')
    artifact = _verified_probe_artifact(
        problem, state, directive, payload['probe_artifact'],
        probe_artifact_verifier)
    before = artifact['before_residual_sha256']
    after = artifact['after_residual_sha256']
    verdict = 'no_progress' if before == after else 'progress'
    decision = directive['decision']
    return apply_parent_probe(
        problem, state, target_goal=decision['target_goal'],
        verdict=verdict, before_fingerprint=before,
        after_fingerprint=after,
        evidence={
            'event_id': event['event_id'],
            'directive_id': directive['directive_id'],
            'probe_artifact': deepcopy(payload['probe_artifact']),
            'native_receipt_sha256':
                artifact['native_receipt_sha256'],
        })


def apply_graph_revision_event(
        problem, state, policy, execution_binding, directive, event):
    directive = validate_fresh_directive(
        problem, state, policy, execution_binding, directive)
    decision = directive['decision']
    if decision['action'] != 'REVISE_GRAPH':
        raise ValueError('current directive is not a graph revision')
    payload = _validate_event(directive, event, 'graph_revision')
    if _seen_event(state, event['event_id']):
        raise ValueError('online transition was already applied')
    if set(payload) != {
            'target_goal', 'selected_route', 'resolution'}:
        raise ValueError('invalid graph revision payload')
    if (payload['target_goal'] != decision['target_goal']
            or payload['selected_route'] != decision['selected_route']):
        raise ValueError(
            'graph revision target differs from directive')
    return apply_graph_revision(
        problem, state, target_goal=decision['target_goal'],
        resolution=payload['resolution'],
        evidence={
            'event_id': event['event_id'],
            'directive_id': directive['directive_id'],
        })


def transition_receipt(directive, event, new_state):
    """Record old state -> decision -> action/authority -> new state."""
    payload = event['payload']
    authority = {
        'event_id': event['event_id'],
        'event_kind': event['kind'],
    }
    if event['kind'] == 'acceptance':
        authority.update(
            native_artifact=deepcopy(payload['native_artifact']),
            journal_commit_id=payload['journal_commit_id'])
    elif event['kind'] == 'parent_probe':
        authority['probe_artifact'] = deepcopy(
            payload['probe_artifact'])
    value = {
        'schema': RECEIPT_SCHEMA,
        'plan_id': directive['execution_binding']['plan_id'],
        'arm_instance_id':
            directive['execution_binding']['arm_instance_id'],
        'old_state_sha256': directive['state_before_sha256'],
        'directive_id': directive['directive_id'],
        'decision': deepcopy(directive['decision']),
        'execution': directive['execution'],
        'authority': authority,
        'new_state_sha256': digest(new_state),
        'search_policy': directive['search_policy'],
        'acceptance_policy': directive['acceptance_policy'],
    }
    value['receipt_id'] = digest(value)
    return value


def apply_online_event(
        problem, state, policy, execution_binding, directive, event, *,
        package=None, journal=None, native_artifact_verifier=None,
        probe_artifact_verifier=None):
    kind = event.get('kind') if isinstance(event, dict) else None
    if kind == 'acceptance':
        new = apply_acceptance_event(
            problem, state, policy, execution_binding, directive, event,
            package=package, journal=journal,
            native_artifact_verifier=native_artifact_verifier)
    elif kind == 'parent_probe':
        new = apply_parent_probe_event(
            problem, state, policy, execution_binding, directive, event,
            probe_artifact_verifier=probe_artifact_verifier)
    elif kind == 'graph_revision':
        new = apply_graph_revision_event(
            problem, state, policy, execution_binding, directive, event)
    else:
        raise ValueError('unknown online transition event kind')
    return {
        'state': new,
        'receipt': transition_receipt(directive, event, new),
    }


def comparison_plan(cases, *, application_manifest_sha256,
                    model='gpt-6-astra', reasoning_effort='medium',
                    order=('A', 'B', 'B', 'A'),
                    effective_seconds=3600, wall_seconds=10800):
    """Freeze a two-case ABBA design without authorizing online execution."""
    _sha(application_manifest_sha256, 'application_manifest_sha256')
    if (not isinstance(cases, list) or len(cases) != 2
            or len({
                case.get('name') for case in cases
                if isinstance(case, dict)}) != 2):
        raise ValueError(
            'comparison requires exactly two uniquely named cases')
    if tuple(order) != ('A', 'B', 'B', 'A'):
        raise ValueError('comparison order must be exactly ABBA')
    if (type(effective_seconds) is not int or effective_seconds < 1
            or type(wall_seconds) is not int
            or wall_seconds < effective_seconds):
        raise ValueError('invalid controlled arm budgets')
    frozen_cases = []
    for case in cases:
        required = {
            'name', 'theory', 'target_contract_id',
            'checkpoint_sha256', 'problem',
        }
        if not isinstance(case, dict) or set(case) != required:
            raise ValueError('case has unexpected or missing fields')
        if (not isinstance(case['name'], str)
                or re.fullmatch(
                    r'[a-z0-9][a-z0-9_-]*', case['name']) is None
                or not isinstance(case['theory'], str)
                or re.fullmatch(
                    r'[A-Za-z0-9_]+', case['theory']) is None
                or not isinstance(case['target_contract_id'], str)
                or not case['target_contract_id'].startswith('contract:')):
            raise ValueError(
                'case name, theory or contract identity is invalid')
        validate_problem(case['problem'])
        if case['problem']['root_goal'] != case['target_contract_id']:
            raise ValueError(
                'case target must equal the AND/OR root goal')
        _sha(case['checkpoint_sha256'], 'checkpoint_sha256')
        frozen_cases.append({
            **{
                key: deepcopy(case[key])
                for key in required - {'problem'}},
            'problem': deepcopy(case['problem']),
            'problem_sha256': digest(case['problem']),
        })
    arms = []
    for case in frozen_cases:
        for position, label in enumerate(order, 1):
            instance = digest({
                'application_manifest_sha256':
                    application_manifest_sha256,
                'case_name': case['name'],
                'checkpoint_sha256': case['checkpoint_sha256'],
                'position': position,
                'label': label,
            })
            arms.append({
                'name': f"{case['name']}--{position:02d}--{label}",
                'arm_instance_id': instance,
                'case_name': case['name'],
                'position': position,
                'policy': arm_policy(label),
                'effective_seconds': effective_seconds,
                'wall_seconds': wall_seconds,
            })
    value = {
        'schema': PLAN_SCHEMA,
        'application_manifest_sha256': application_manifest_sha256,
        'model': model,
        'reasoning_effort': reasoning_effort,
        'cases': frozen_cases,
        'order_per_case': list(order),
        'arms': arms,
        'acceptance_policy': BOUNDED_PREFIX,
        'intervention_axis': 'search_policy_only',
        'automatic_replacements': 0,
        'automatic_budget_expansion': False,
        'online_authorized': False,
        'runtime_adapter_required': 'stepwise-policy-supervisor-v1',
        'raw_parallel_workspace_is_and_or_arm': False,
        'runtime_blockers': [
            'The free-running File Planner has no enforced per-decision step boundary.',
            'focus_contract_id is prompt text and cannot authorize an AND/OR arm.',
            'No native parent-probe artifact producer is integrated yet.',
        ],
        'limitations': [
            'Freezing this plan does not authorize provider or native execution.',
            'Two B replicates per case are case-bound engineering evidence, not a general reliability estimate.',
        ],
    }
    value['plan_id'] = digest(value)
    return value
