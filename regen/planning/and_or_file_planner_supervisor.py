"""Opt-in stepwise AND/OR supervisor for the real document File Planner.

The default TheoryController and run_theory_session paths are unchanged.  This
subclass is intended for a separately frozen B arm.  CodexWorkspaceSession is
already serial at the dynamic-tool callback boundary: handle() finishes, the
tool result is sent, and only then can the next queued callback execute.  This
controller uses that boundary to refresh the directive after every tool result.
"""
from copy import deepcopy
from pathlib import Path
import threading

from regen.execution.proof_commits import digest, durable
from regen.infrastructure.incremental_log import read_artifact
from regen.planning.bounded_prefix import STRATEGY as BOUNDED_PREFIX
from regen.planning.online_policy_composition import (
    ARM_ATTESTATION_NAME,
    AND_OR_SEARCH,
    acceptance_payload_from_task,
    apply_online_event,
    arm_policy,
    bind_event,
    make_execution_binding,
    native_step_binding,
    next_online_directive,
    replay_callback_protocol,
    validate_authorized_candidate,
    validate_online_problem,
    verify_artifact_locator,
)
from regen.planning.parallel_file_planner import TheoryController
from regen.planning.theory_document import baseline, candidate, parse, source_hash


SUPERVISOR_SCHEMA = 'contract-and-or-file-planner-supervisor-v1'
SUPERVISOR_MANIFEST_SCHEMA = 'contract-and-or-supervisor-manifest-v1'
INTENT_SCHEMA = 'contract-and-or-tool-intent-v1'
STEP_RECEIPT_SCHEMA = 'contract-and-or-tool-step-receipt-v1'
IDENTITY_FIELDS = {
    'plan_id', 'arm_id', 'case_name', 'arm_instance_id',
    'input_binding_sha256', 'checkpoint_sha256',
    'pristine_provenance_sha256', 'prepared_manifest_sha256',
    'application_manifest_sha256',
}


def _sha(value, field):
    if (not isinstance(value, str) or len(value) != 64
            or any(character not in '0123456789abcdef'
                   for character in value)):
        raise ValueError(field + ' must be a SHA-256 identity')


def _preview_edit(store, proposal):
    """Validate and preview DocumentStore.edit without mutating its draft."""
    if (not isinstance(proposal, dict)
            or set(proposal)
            - {'action', 'base_sha256', 'source', 'edits', 'notes'}):
        raise ValueError(
            'edit_theory accepts base_sha256 and either source or edits')
    current = store.read()
    if proposal.get('base_sha256') != current['source_sha256']:
        raise ValueError(
            'stale document; read_theory before editing')
    if ('source' in proposal) == ('edits' in proposal):
        raise ValueError('provide source OR edits')
    source = proposal.get('source', current['source'])
    if 'edits' in proposal:
        edits = proposal['edits']
        if not isinstance(edits, list) or not edits:
            raise ValueError('edits must be a nonempty list')
        for edit in edits:
            if (not isinstance(edit, dict)
                    or set(edit) != {'old', 'new'}
                    or not all(isinstance(value, str)
                               for value in edit.values())
                    or not edit['old']
                    or source.count(edit['old']) != 1):
                raise ValueError(
                    'each edit needs one unique nonempty old string')
            source = source.replace(edit['old'], edit['new'], 1)
    parse(source, store.package, store.imports)
    return source


class AndOrTheoryController(TheoryController):
    """A real TheoryController with fail-closed per-callback B-arm policy."""

    def __init__(self, run_dir, theory, *, and_or_problem, and_or_state,
                 execution_identity, supervisor_directory=None,
                 arm_attestation_path=None, **options):
        if set(execution_identity) != IDENTITY_FIELDS:
            raise ValueError(
                'execution_identity must bind plan/case/arm/checkpoint/application')
        for field in IDENTITY_FIELDS - {'case_name', 'arm_id'}:
            _sha(execution_identity[field], field)
        for field in ('case_name', 'arm_id'):
            if (not isinstance(execution_identity[field], str)
                    or not execution_identity[field]):
                raise ValueError(field + ' must be a non-empty string')
        if (execution_identity['checkpoint_sha256']
                != execution_identity['prepared_manifest_sha256']):
            raise ValueError(
                'checkpoint_sha256 must equal prepared_manifest_sha256')
        validate_online_problem(and_or_problem)
        if (not isinstance(and_or_state, dict)
                or and_or_state.get('method') != AND_OR_SEARCH):
            raise ValueError('invalid initial AND/OR overlay state')
        if options.get('document_mode', True) is not True:
            raise ValueError('AND/OR supervisor requires document mode')
        strategy = options.get(
            'generation_strategy_name', BOUNDED_PREFIX)
        if strategy != BOUNDED_PREFIX:
            raise ValueError(
                'AND/OR supervisor requires bounded-prefix acceptance')
        options['document_mode'] = True
        options['generation_strategy_name'] = BOUNDED_PREFIX
        super().__init__(run_dir, theory, **options)
        self.and_or_problem = deepcopy(and_or_problem)
        self.and_or_state = deepcopy(and_or_state)
        self.execution_identity = deepcopy(execution_identity)
        self.arm_attestation_path = Path(
            arm_attestation_path
            or Path(run_dir).resolve()/ARM_ATTESTATION_NAME).resolve()
        self.and_or_policy = arm_policy('B')
        self._and_or_lock = threading.RLock()
        runtime = self._runtime()
        default = (
            runtime[2]/'and-or-supervisor'
            / execution_identity['arm_instance_id'])
        self.supervisor_directory = Path(
            supervisor_directory or default).resolve()
        theory_root = Path(runtime[2]).resolve()
        if not self.supervisor_directory.is_relative_to(theory_root):
            raise ValueError(
                'supervisor_directory must be inside the theory artifact root')
        self.supervisor_directory.mkdir(parents=True, exist_ok=True)
        package, journal, document, _ = self._authority()
        manifest = {
            'schema': SUPERVISOR_MANIFEST_SCHEMA,
            'theory': self.theory,
            'problem_sha256': digest(self.and_or_problem),
            'initial_overlay_sha256': digest(and_or_state),
            'execution_identity': deepcopy(self.execution_identity),
            'package_sha256': digest(package),
            'journal_directory': str(Path(journal.directory).resolve()),
            'journal_binding_sha256': digest(journal.binding),
            'arm_attestation_path': str(self.arm_attestation_path),
        }
        manifest['manifest_id'] = digest(manifest)
        manifest_path = self.supervisor_directory/'manifest.json'
        if manifest_path.exists():
            if read_artifact(manifest_path) != manifest:
                raise ValueError(
                    'supervisor manifest differs from the frozen arm')
        else:
            durable(manifest_path, manifest, immutable=True)
        state_path = self.supervisor_directory/'state.json'
        if state_path.exists():
            stored = read_artifact(state_path)
            if (self.and_or_state != stored
                    and digest(self.and_or_state)
                    != manifest['initial_overlay_sha256']):
                raise ValueError(
                    'supplied AND/OR state is neither initial nor durable state')
            self.and_or_state = stored
        else:
            durable(state_path, self.and_or_state)
        numbers = [int(path.stem) for directory in ('intents', 'steps')
                   for path in (self.supervisor_directory/directory).glob(
                       '*.json') if path.stem.isdigit()]
        self._step_number = max(numbers, default=0)
        self._recover_pending()
        self._refresh_directive()

    def _authority(self):
        runtime = self._runtime()
        package, journal = runtime[1], runtime[3]
        document = journal.replay()
        if document is None:
            raise ValueError(
                'AND/OR supervision requires an initialized theory journal')
        return package, journal, document, Path(runtime[2]).resolve()

    def _refresh_directive(self):
        package, journal, document, theory_root = self._authority()
        known = {goal['id'] for goal in self.and_or_problem['goals']}
        certified = set(document.get('proofs', {})) & known
        if certified != set(self.and_or_state.get('certified', [])):
            raise ValueError(
                'AND/OR overlay differs from authoritative accepted proofs')
        identity = self.execution_identity
        self.and_or_binding = make_execution_binding(
            plan_id=identity['plan_id'],
            arm_id=identity['arm_id'],
            case_name=identity['case_name'],
            arm_instance_id=identity['arm_instance_id'],
            arm_label='B',
            input_binding_sha256=identity['input_binding_sha256'],
            checkpoint_sha256=identity['checkpoint_sha256'],
            pristine_provenance_sha256=
                identity['pristine_provenance_sha256'],
            prepared_manifest_sha256=
                identity['prepared_manifest_sha256'],
            application_manifest_sha256=
                identity['application_manifest_sha256'],
            problem=self.and_or_problem,
            package=package,
            journal=journal,
            document_state=document,
            artifact_root=theory_root,
            arm_attestation_path=self.arm_attestation_path,
        )
        self.and_or_directive = next_online_directive(
            self.and_or_problem, self.and_or_state,
            self.and_or_policy, self.and_or_binding)
        return self.and_or_directive

    def directive_view(self):
        directive = self.and_or_directive
        eligible = set(directive['eligible_goal_ids'] or [])
        declarations = [{
            key: deepcopy(goal[key])
            for key in ('id', 'role', 'name', 'statement', 'context')
        } for goal in self.and_or_problem['goals'] if goal['id'] in eligible]
        decision = directive['decision']
        route_id = (decision.get('route_id')
                    or decision.get('scheduled_route_id')
                    or decision.get('selected_route'))
        route = next((deepcopy(route)
                      for goal in self.and_or_problem['goals']
                      for route in goal['routes']
                      if route['id'] == route_id), None)
        return {
            'schema': SUPERVISOR_SCHEMA,
            'problem_sha256': directive['problem_sha256'],
            'directive_id': directive['directive_id'],
            'execution': directive['execution'],
            'eligible_goal_ids': deepcopy(
                directive['eligible_goal_ids']),
            'eligible_goal_declarations': declarations,
            'decision': deepcopy(decision),
            'planning_guidance': ({
                'selected_route': route,
                'notice': decision.get('notice'),
            } if route is not None else {
                'selected_route': None,
                'notice': decision.get('notice'),
            }),
            'search_policy': directive['search_policy'],
            'acceptance_policy': directive['acceptance_policy'],
            'journal_head_commit_id':
                directive['execution_binding'][
                    'journal_head_commit_id'],
            'native_parent_probe_available': False,
        }

    def _goal(self, goal_id):
        return next(
            goal for goal in self.and_or_problem['goals']
            if goal['id'] == goal_id)

    def _validate_source(self, source, *, require_proof):
        directive = self.and_or_directive
        eligible = directive['eligible_goal_ids']
        if (directive['execution']
                not in {'planner_proof', 'native_recheck'}
                or not isinstance(eligible, list) or len(eligible) != 1):
            if directive['execution'] == 'native_parent_probe':
                raise ValueError(
                    'native parent-probe producer is not integrated; '
                    'source mutation is blocked')
            raise ValueError(
                'current AND/OR directive does not authorize source mutation')
        package, _, document, _ = self._authority()
        runtime = self._runtime()
        projected = candidate(
            package, document, source, runtime[4].imports())
        if baseline(document, projected)[1] != 'append':
            raise ValueError(
                'AND/OR source action must preserve the accepted prefix')
        goal_id = eligible[0]
        validate_authorized_candidate(
            self.and_or_problem, document, projected, goal_id,
            require_proof=require_proof)
        if directive['execution'] == 'native_recheck':
            frozen = directive['decision']['candidate_binding']
            if source_hash(source) != frozen['checked_source_sha256']:
                raise ValueError(
                    'recheck source differs from frozen candidate')
            if digest(projected['proofs'].get(goal_id)) != frozen[
                    'proof_body_sha256']:
                raise ValueError(
                    'recheck proof body differs from frozen candidate')
        return projected

    def _gate(self, name, arguments):
        if name != 'proof_action':
            return None
        if arguments.get('theory') != self.theory:
            raise ValueError('only the assigned theory may be edited')
        proposal = arguments.get('proposal')
        if not isinstance(proposal, dict):
            raise ValueError('proposal must be an object')
        action = proposal.get('action')
        if action == 'edit_theory':
            store, _ = self._document()
            source = _preview_edit(store, proposal)
            self._validate_source(source, require_proof=False)
            return source
        if action == 'check_theory':
            store, _ = self._document()
            source = store.checked_source(
                proposal.get('source_sha256'))
            self._validate_source(source, require_proof=True)
            return source
        if action == 'finish':
            if self.and_or_directive['decision']['action'] != 'SOLVED':
                raise ValueError(
                    'finish is blocked until the root contract is journal-accepted')
        return None

    def _check_artifact(self, result):
        request = (
            result.get('request_number')
            if isinstance(result, dict) else None)
        if type(request) is not int or request < 1:
            return None
        directory = (
            self._runtime()[2]/'batch-requests'
            / f'{request:06d}'/'bounded-prefix')
        paths = sorted(directory.glob('*/check.json'))
        if len(paths) > 1:
            raise ValueError(
                'one AND/OR callback produced multiple native checks')
        return paths[0] if paths else None

    def _consume_check(self, result, old_directive, old_binding, step):
        check_path = self._check_artifact(result)
        if check_path is None:
            return None
        _, journal, _, _ = self._authority()
        payload = acceptance_payload_from_task(
            old_directive, journal, check_path)
        event = bind_event(
            old_directive, 'acceptance', payload)
        bundle = apply_online_event(
            self.and_or_problem, self.and_or_state,
            self.and_or_policy, old_binding, old_directive, event,
            package=self._runtime()[1], journal=journal,
            native_artifact_verifier=verify_artifact_locator)
        durable(
            self.supervisor_directory/'transitions'/f'{step:06d}.json',
            bundle, immutable=True)
        self.and_or_state = bundle['state']
        durable(
            self.supervisor_directory/'state.json',
            self.and_or_state)
        return bundle['receipt']

    def _intent(self, step, *, name, arguments, metadata, old_state,
                old_directive, old_binding):
        action = arguments.get(
            'proposal', {}).get('action') if isinstance(
                arguments, dict) else None
        value = {
            'schema': INTENT_SCHEMA,
            'step': step,
            'old_state': deepcopy(old_state),
            'old_state_sha256': digest(old_state),
            'directive': deepcopy(old_directive),
            'execution_binding': deepcopy(old_binding),
            'action': {
                'tool': name,
                'proposal_action': action,
                'arguments_sha256': digest(arguments),
                'thread_id': metadata.get('thread_id'),
                'turn_id': metadata.get('turn_id'),
                'call_id': metadata.get('call_id'),
            },
        }
        value['intent_id'] = digest(value)
        durable(
            self.supervisor_directory/'intents'/f'{step:06d}.json',
            value, immutable=True)
        return value

    def _authority_synchronized(self):
        _, _, document, _ = self._authority()
        known = {goal['id'] for goal in self.and_or_problem['goals']}
        certified = set(document.get('proofs', {})) & known
        projection = self.supervisor_directory/'state.json'
        return (certified == set(self.and_or_state.get('certified', []))
                and projection.is_file()
                and read_artifact(projection) == self.and_or_state)

    def _record_step(self, intent, *, result=None, error=None,
                     transition=None, recovered=False,
                     authority_sync='synchronized'):
        _, journal, _, _ = self._authority()
        current = journal.replay_snapshot()
        value = {
            'schema': STEP_RECEIPT_SCHEMA,
            'step': intent['step'],
            'intent_id': intent['intent_id'],
            'old_state_sha256': intent['old_state_sha256'],
            'decision': deepcopy(intent['directive']['decision']),
            'execution': intent['directive']['execution'],
            'eligible_goal_ids': deepcopy(
                intent['directive']['eligible_goal_ids']),
            'directive_id': intent['directive']['directive_id'],
            'action': deepcopy(intent['action']),
            'journal': {
                'old_head_commit_id':
                    intent['execution_binding'][
                        'journal_head_commit_id'],
                'old_head_sequence':
                    intent['execution_binding'][
                        'journal_head_sequence'],
                'new_head_commit_id': current['commit_id'],
                'new_head_sequence': len(current['records']),
            },
            'transition_receipt_id': (
                transition.get('receipt_id')
                if isinstance(transition, dict) else None),
            'result_sha256': (
                digest(result) if error is None else None),
            'error': (
                None if error is None else {
                    'type': type(error).__name__,
                    'message': str(error),
                }),
            'authority_sync': authority_sync,
            'recovered': recovered,
            'observed_overlay_state_sha256': digest(self.and_or_state),
            'new_state_sha256': (
                digest(self.and_or_state)
                if authority_sync == 'synchronized' else None),
        }
        value['receipt_id'] = digest(value)
        directory = ('steps' if authority_sync == 'synchronized'
                     else 'pending-recovery')
        durable(self.supervisor_directory/directory
                / f'{intent["step"]:06d}.json', value, immutable=True)
        return value

    def _artifact_for_request(self, request):
        directory = (self._runtime()[2]/'batch-requests'
                     / f'{request:06d}'/'bounded-prefix')
        paths = sorted(directory.glob('*/check.json'))
        if len(paths) > 1:
            raise ValueError(
                'one AND/OR callback produced multiple native checks')
        return paths[0] if paths else None

    def _recover_pending(self):
        """Reconcile journal authority from immutable pre-dispatch intents."""
        for path in sorted((self.supervisor_directory/'intents').glob(
                '*.json')):
            step = int(path.stem)
            if (self.supervisor_directory/'steps'/f'{step:06d}.json').exists():
                continue
            intent = read_artifact(path)
            if (intent.get('schema') != INTENT_SCHEMA
                    or intent.get('intent_id') != digest({
                        key: value for key, value in intent.items()
                        if key != 'intent_id'})):
                raise ValueError('invalid supervisor write-ahead intent')
            if self.and_or_state != intent['old_state']:
                transition_path = (self.supervisor_directory/'transitions'
                                   / f'{step:06d}.json')
                if not transition_path.exists():
                    raise ValueError(
                        'durable overlay diverges before pending intent')
            transition_path = (self.supervisor_directory/'transitions'
                               / f'{step:06d}.json')
            transition = None
            if transition_path.exists():
                bundle = read_artifact(transition_path)
                if (bundle.get('receipt', {}).get('old_state_sha256')
                        != intent['old_state_sha256']
                        or bundle.get('receipt', {}).get('directive_id')
                        != intent['directive']['directive_id']):
                    raise ValueError(
                        'durable transition differs from pending intent')
                self.and_or_state = bundle['state']
                transition = bundle['receipt']
                durable(
                    self.supervisor_directory/'state.json',
                    self.and_or_state)
            else:
                _, journal, _, _ = self._authority()
                current = journal.replay_snapshot()
                old_head = intent['execution_binding'][
                    'journal_head_commit_id']
                if current['commit_id'] != old_head:
                    try:
                        protocol = replay_callback_protocol(
                            intent['directive'], journal,
                            require_feedback=False)
                    except ValueError as error:
                        if ('no unique new planner request' not in str(error)
                                or any(key in current['state'].get(
                                    'proofs', {})
                                    for key in {goal['id'] for goal in
                                                self.and_or_problem['goals']}
                                    - set(intent['old_state'].get(
                                        'certified', [])))):
                            raise
                        protocol = None
                    if protocol is not None:
                        if protocol['feedback'] is None:
                            journal.append(
                                'planner-feedback', current['state'], {
                                    'request_number':
                                        protocol['request_number'],
                                    'feedback': {
                                        'status': 'interrupted-recovery'}})
                            protocol = replay_callback_protocol(
                                intent['directive'], journal)
                        artifact = self._artifact_for_request(
                            protocol['request_number'])
                        if artifact is not None:
                            payload = acceptance_payload_from_task(
                                intent['directive'], journal, artifact)
                            event = bind_event(
                                intent['directive'], 'acceptance', payload)
                            bundle = apply_online_event(
                                self.and_or_problem, intent['old_state'],
                                self.and_or_policy,
                                intent['execution_binding'],
                                intent['directive'], event,
                                package=self._runtime()[1], journal=journal,
                                native_artifact_verifier=
                                    verify_artifact_locator)
                            durable(transition_path, bundle, immutable=True)
                            self.and_or_state = bundle['state']
                            transition = bundle['receipt']
                            durable(
                                self.supervisor_directory/'state.json',
                                self.and_or_state)
            if not self._authority_synchronized():
                raise ValueError(
                    'pending supervisor intent could not be reconciled')
            self._record_step(
                intent, transition=transition, recovered=True)

    def handle(self, name, arguments, metadata):
        """Gate one actual dynamic-tool callback, then refresh its directive."""
        with self._and_or_lock:
            # Never dispatch a new tool until a prior write-ahead intent has
            # been reconciled against the authoritative journal.
            self._recover_pending()
            self._refresh_directive()
            old_state = deepcopy(self.and_or_state)
            old_directive = deepcopy(self.and_or_directive)
            old_binding = deepcopy(self.and_or_binding)
            self._step_number += 1
            intent = self._intent(
                self._step_number, name=name, arguments=arguments,
                metadata=(metadata or {}), old_state=old_state,
                old_directive=old_directive, old_binding=old_binding)
            is_check = (
                name == 'proof_action'
                and isinstance(arguments, dict)
                and arguments.get('proposal', {}).get('action')
                == 'check_theory')
            checker = self._runtime()[4]
            prior_scope = getattr(
                checker, 'bounded_prefix_eligible_ids', None)
            had_scope = hasattr(
                checker, 'bounded_prefix_eligible_ids')
            prior_origin = getattr(
                checker, 'bounded_prefix_evidence_origin', None)
            had_origin = hasattr(
                checker, 'bounded_prefix_evidence_origin')
            try:
                self._gate(name, arguments)
                if is_check:
                    checker.bounded_prefix_eligible_ids = list(
                        old_directive['eligible_goal_ids'])
                    checker.bounded_prefix_evidence_origin = \
                        native_step_binding(old_directive)
                result = super().handle(name, arguments, metadata)
                transition = (
                    self._consume_check(
                        result, old_directive, old_binding,
                        self._step_number)
                    if is_check else None)
                self._refresh_directive()
                receipt = self._record_step(
                    intent,
                    result=result, transition=transition)
                if not isinstance(result, dict):
                    result = {'result': result}
                return {
                    **result,
                    'and_or_control': self.directive_view(),
                    'and_or_step_receipt_id': receipt['receipt_id'],
                }
            except Exception as error:
                try:
                    synchronized = self._authority_synchronized()
                except Exception:
                    synchronized = False
                self._record_step(
                    intent, error=error,
                    authority_sync=(
                        'synchronized' if synchronized
                        else 'pending_recovery'))
                raise
            finally:
                if had_scope:
                    checker.bounded_prefix_eligible_ids = prior_scope
                elif hasattr(
                        checker, 'bounded_prefix_eligible_ids'):
                    del checker.bounded_prefix_eligible_ids
                if had_origin:
                    checker.bounded_prefix_evidence_origin = prior_origin
                elif hasattr(
                        checker, 'bounded_prefix_evidence_origin'):
                    del checker.bounded_prefix_evidence_origin
