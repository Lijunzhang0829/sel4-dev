"""Authority-replay checks for orthogonal search/acceptance composition."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import tempfile
import unittest

from regen.execution.proof_commits import ProofCommits, digest, durable
from regen.execution.repository_execution import validator
from regen.infrastructure.incremental_log import read_artifact
from regen.planning.bounded_prefix import one_declaration
from regen.planning.contract_and_or import initial_state
from regen.planning.online_policy_composition import (
    AND_OR_SEARCH,
    ARM_ATTESTATION_NAME,
    ARM_ATTESTATION_SCHEMA,
    BOUNDED_PREFIX,
    EXISTING_SEARCH,
    RECEIPT_SCHEMA,
    apply_acceptance_event,
    apply_online_event,
    arm_policy,
    bind_event,
    comparison_arms,
    comparison_plan,
    make_execution_binding,
    native_step_binding,
    next_online_directive,
    run_bounded_prefix_for_directive,
    write_arm_attestation,
)
from regen.planning.theory_document import candidate, check_document, render
from regen.tests.current.test_proof_batches import Checker
from regen.tests.current.test_proof_commits import check, fixture
from regen.tools.contract_and_or_online_control import (
    CONFIG_SCHEMA,
    freeze_plan,
    verify_artifact_locator,
)


def route(key, requires=()):
    return {
        'id': key,
        'requires': list(requires),
        'estimate_seconds': 1,
        'recheck_seconds': 1,
        'failure_penalty_seconds': 1,
        'probe_seconds': 1,
    }


def problem(root='contract'):
    return {
        'schema_version': 1,
        'method': AND_OR_SEARCH,
        'root_goal': root,
        'goals': [
            {'id': 'helper', 'order': 1, 'role': 'helper',
             'name': 'helper',
             'statement': 'lemma helper: "True & True"', 'context': [],
             'routes': [route('helper/r')]},
            {'id': root, 'order': 2, 'role': 'contract',
             'name': 'target',
             'statement': 'lemma target: "True"', 'context': [],
             'routes': [route('contract/r', ('helper',))]},
        ],
    }


class OnlinePolicyCompositionTests(unittest.TestCase):
    def _journal(self, root, package, old):
        journal = ProofCommits(
            root/'theories'/'Demo'/'acceptance',
            {'run': 'online-policy-test'},
            validator(package))
        journal.append('initial', old, {'check': check()})
        return journal

    def _binding(self, root, package, journal, document, label='B',
                 instance='2', graph=None):
        graph = graph or problem()
        arm_id = 'synthetic-' + label + '-' + instance
        input_binding = digest({'case': 'synthetic', 'problem': graph})
        if not (root/ARM_ATTESTATION_NAME).exists():
            written = write_arm_attestation(
                plan_id='1' * 64,
                arm_id=arm_id,
                case_name='synthetic',
                arm_instance_id=instance * 64,
                policy=arm_policy(label),
                input_binding_sha256=input_binding,
                checkpoint_sha256='3' * 64,
                pristine_provenance_sha256='6' * 64,
                prepared_manifest_sha256='3' * 64,
                application_manifest_sha256='4' * 64,
                problem=graph, package=package, journal=journal)
            self.assertEqual(
                written['attestation']['schema'], ARM_ATTESTATION_SCHEMA)
        return make_execution_binding(
            plan_id='1' * 64,
            arm_id=arm_id,
            case_name='synthetic',
            arm_instance_id=instance * 64,
            arm_label=label,
            input_binding_sha256=input_binding,
            checkpoint_sha256='3' * 64,
            pristine_provenance_sha256='6' * 64,
            prepared_manifest_sha256='3' * 64,
            application_manifest_sha256='4' * 64,
            problem=graph,
            package=package,
            journal=journal,
            document_state=document,
            artifact_root=root,
            arm_attestation_path=root/ARM_ATTESTATION_NAME,
        )

    def _run(self, root, label='B'):
        package, old = fixture()
        source = render(package, old, Checker().imports())
        source = source.replace(
            'oops', 'by simp', 1 if label == 'B' else source.count('oops'))
        graph = problem()
        overlay = initial_state(graph)
        journal = self._journal(root, package, old)
        policy = arm_policy(label)
        binding = self._binding(
            root, package, journal, old, label=label,
            instance='2' if label == 'B' else '5', graph=graph)
        directive = next_online_directive(
            graph, overlay, policy, binding)
        feedback = run_bounded_prefix_for_directive(
            graph, overlay, policy, binding, directive,
            package, old, source, Checker(), root/'checks',
            journal=journal,
            evidence_origin={
                'sealable_generation': True,
                'transcript_prefix': 'synthetic-test-only',
            })
        return (
            package, old, graph, overlay, journal, policy, binding,
            directive, feedback)

    def test_comparison_changes_only_search_axis(self):
        control, variant = comparison_arms()
        self.assertEqual(control['search_policy'], EXISTING_SEARCH)
        self.assertEqual(variant['search_policy'], AND_OR_SEARCH)
        self.assertEqual(control['acceptance_policy'], BOUNDED_PREFIX)
        self.assertEqual(
            control['acceptance_policy'], variant['acceptance_policy'])
        self.assertEqual(
            control['runner_generation_strategy'],
            variant['runner_generation_strategy'])

    def test_directive_is_bound_to_unique_replicate_and_full_document(self):
        package, old = fixture()
        graph, state = problem(), initial_state(problem())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_root, second_root = root/'one', root/'two'
            first_journal = self._journal(first_root, package, old)
            second_journal = self._journal(second_root, package, old)
            first = self._binding(
                first_root, package, first_journal, old,
                instance='2', graph=graph)
            second = self._binding(
                second_root, package, second_journal, old,
                instance='3', graph=graph)
            one = next_online_directive(
                graph, state, arm_policy('B'), first)
            two = next_online_directive(
                graph, state, arm_policy('B'), second)
        self.assertNotEqual(one['directive_id'], two['directive_id'])
        self.assertNotEqual(
            one['execution_binding']['arm_instance_id'],
            two['execution_binding']['arm_instance_id'])
        self.assertEqual(
            one['execution_binding']['document_state_sha256'],
            digest(old))
        self.assertEqual(
            one['execution_binding']['proofs_sha256'],
            digest(old['proofs']))

    def test_both_arms_use_same_acceptance_but_b_precommit_scope_is_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            control = self._run(root/'A', 'A')
            variant = self._run(root/'B', 'B')
            control_order = control[4].replay()['accepted_order']
            variant_order = variant[4].replay()['accepted_order']
            control_feedback, variant_feedback = control[-1], variant[-1]
        self.assertEqual(control_order, ['helper', 'contract'])
        self.assertEqual(variant_order, ['helper'])
        self.assertEqual(
            control_feedback['bounded_prefix']['strategy'],
            variant_feedback['bounded_prefix']['strategy'])
        self.assertFalse(
            control_feedback['bounded_prefix']['search_scope_enforced'])
        self.assertTrue(
            variant_feedback['bounded_prefix']['search_scope_enforced'])
        self.assertEqual(
            variant_feedback['bounded_prefix']['unselected_ids'],
            [])
        self.assertEqual(
            variant_feedback['online_control']['commit_receipts'][0][
                'goal_id'],
            'helper')

    def test_authoritative_commit_advances_overlay_and_emits_transition_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            values = self._run(Path(tmp), 'B')
            (package, _, graph, state, journal, policy, binding,
             directive, feedback) = values
            payload = feedback['online_control'][
                'acceptance_payloads'][0]
            event = bind_event(directive, 'acceptance', payload)
            result = apply_online_event(
                graph, state, policy, binding, directive, event,
                package=package, journal=journal,
                native_artifact_verifier=verify_artifact_locator)
        self.assertEqual(result['state']['certified'], ['helper'])
        self.assertEqual(result['receipt']['schema'], RECEIPT_SCHEMA)
        self.assertEqual(
            result['receipt']['old_state_sha256'], digest(state))
        self.assertEqual(
            result['receipt']['new_state_sha256'],
            digest(result['state']))
        self.assertEqual(
            result['receipt']['authority']['journal_commit_id'],
            payload['journal_commit_id'])
        self.assertEqual(
            result['receipt']['receipt_id'],
            digest({
                key: value for key, value in result['receipt'].items()
                if key != 'receipt_id'}))

    def test_fabricated_or_cross_replicate_event_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            values = self._run(root, 'B')
            (package, old, graph, state, journal, policy, binding,
             directive, feedback) = values
            payload = feedback['online_control'][
                'acceptance_payloads'][0]
            event = bind_event(directive, 'acceptance', payload)
            bad = json.loads(json.dumps(event))
            bad['payload']['native_artifact']['raw_sha256'] = 'f' * 64
            bad['event_id'] = digest({
                key: value for key, value in bad.items()
                if key != 'event_id'})
            with self.assertRaisesRegex(ValueError, 'raw bytes'):
                apply_acceptance_event(
                    graph, state, policy, binding, directive, bad,
                    package=package, journal=journal,
                    native_artifact_verifier=verify_artifact_locator)

            other_binding = dict(binding)
            other_binding['arm_instance_id'] = '9' * 64
            with self.assertRaisesRegex(ValueError, 'attestation'):
                next_online_directive(
                    graph, state, policy, other_binding)

    def test_unverified_journal_or_arbitrary_commit_callback_is_not_accepted(self):
        package, old = fixture()
        graph, state = problem(), initial_state(problem())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal = self._journal(root, package, old)
            binding = self._binding(root, package, journal, old)
            directive = next_online_directive(
                graph, state, arm_policy('B'), binding)
            source = render(
                package, old, Checker().imports()).replace(
                    'oops', 'by simp')
            with self.assertRaisesRegex(ValueError, 'ProofCommits authority'):
                run_bounded_prefix_for_directive(
                    graph, state, arm_policy('B'), binding, directive,
                    package, old, source, Checker(), root/'checks',
                    journal=lambda *args: None,
                    evidence_origin={'sealable_generation': True})

    def test_completed_candidate_binds_recheck_source_and_body(self):
        package, old = fixture()
        graph, state = problem(), initial_state(problem())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal = self._journal(root, package, old)
            binding = self._binding(root, package, journal, old)
            policy = arm_policy('B')
            directive = next_online_directive(
                graph, state, policy, binding)
            full_source = render(
                package, old, Checker().imports()).replace(
                    'oops', 'by simp')
            projected = candidate(
                package, old, full_source, Checker().imports())
            _, checked_source = one_declaration(
                package, old, projected, 'helper', Checker().imports())
            checked = candidate(
                package, old, checked_source, Checker().imports())
            evidence = check_document(
                Checker(), old, checked, checked_source)
            evidence.update(
                sealable_generation=True,
                parent_request_number=1,
                online_control=native_step_binding(directive),
                bounded_prefix={
                    'entry_id': 'helper',
                    'search_scope_enforced': True,
                    'eligible_ids': ['helper'],
                    'original_source_sha256':
                        hashlib.sha256(
                            checked_source.encode()).hexdigest(),
                })
            request_state = deepcopy(old)
            request_state['batch_requests'] = 1
            journal.append(
                'planner-request', request_state,
                {'request_number': 1})
            journal.append(
                'planner-feedback', request_state,
                {'request_number': 1, 'feedback': {
                    'status': 'candidate-only'}})
            artifact = root/'candidate-check.json'
            durable(artifact, evidence, immutable=True)
            locator = {
                'path': str(artifact.resolve()),
                'raw_sha256': hashlib.sha256(
                    artifact.read_bytes()).hexdigest(),
                'content_sha256': digest(read_artifact(artifact)),
            }
            event = bind_event(directive, 'acceptance', {
                'goal_id': 'helper',
                'outcome': 'completed_uncommitted',
                'native_artifact': locator,
                'journal_commit_id': None,
            })
            candidate_state = apply_acceptance_event(
                graph, state, policy, binding, directive, event,
                package=package, journal=journal,
                native_artifact_verifier=verify_artifact_locator)
            current = journal.replay()
            advanced = self._binding(
                root, package, journal, current, graph=graph)
            recheck = next_online_directive(
                graph, candidate_state, policy, advanced)
            self.assertEqual(recheck['execution'], 'native_recheck')
            self.assertEqual(recheck['eligible_goal_ids'], ['helper'])
            changed = checked_source.replace('by simp', 'by blast', 1)
            checker = Checker()
            with self.assertRaisesRegex(
                    ValueError, 'frozen candidate'):
                run_bounded_prefix_for_directive(
                    graph, candidate_state, policy, advanced, recheck,
                    package, current, changed, checker, root/'recheck',
                    journal=journal,
                    evidence_origin={'sealable_generation': True})
            self.assertEqual(checker.calls, 0)

    def test_parent_probe_is_fail_closed_without_native_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            values = self._run(root, 'B')
            (package, _, graph, state, journal, policy, binding,
             directive, feedback) = values
            event = bind_event(
                directive, 'acceptance',
                feedback['online_control']['acceptance_payloads'][0])
            state = apply_acceptance_event(
                graph, state, policy, binding, directive, event,
                package=package, journal=journal,
                native_artifact_verifier=verify_artifact_locator)
            document = journal.replay()
            advanced = self._binding(
                root, package, journal, document)
            probe = next_online_directive(
                graph, state, policy, advanced)
            self.assertEqual(probe['decision']['action'], 'PROBE_PARENT')
            fake = bind_event(
                probe, 'parent_probe', {'probe_artifact': {
                    'path': str(root/'fake.json'),
                    'raw_sha256': 'a' * 64,
                    'content_sha256': 'b' * 64,
                }})
            with self.assertRaisesRegex(
                    ValueError, 'native probe adapter'):
                apply_online_event(
                    graph, state, policy, advanced, probe, fake,
                    package=package, journal=journal,
                    native_artifact_verifier=verify_artifact_locator)

    def test_comparison_plan_is_exact_abba_and_exposes_runtime_blockers(self):
        graph = problem('contract:root')
        cases = [
            {'name': 'one', 'theory': 'T1',
             'target_contract_id': 'contract:root',
             'checkpoint_sha256': '1' * 64, 'problem': graph},
            {'name': 'two', 'theory': 'T2',
             'target_contract_id': 'contract:root',
             'checkpoint_sha256': '2' * 64, 'problem': graph},
        ]
        plan = comparison_plan(
            cases, application_manifest_sha256='3' * 64)
        self.assertEqual(plan['order_per_case'], ['A', 'B', 'B', 'A'])
        self.assertEqual(len(plan['arms']), 8)
        self.assertEqual(
            len({arm['arm_instance_id'] for arm in plan['arms']}), 8)
        self.assertFalse(plan['online_authorized'])
        self.assertFalse(plan['raw_parallel_workspace_is_and_or_arm'])
        self.assertIn(
            'focus_contract_id',
            ' '.join(plan['runtime_blockers']))
        with self.assertRaisesRegex(ValueError, 'exactly ABBA'):
            comparison_plan(
                cases, application_manifest_sha256='3' * 64,
                order=('A', 'A', 'B', 'B'))

    def test_independent_driver_freezes_new_non_authorizing_plan(self):
        graph = problem('contract:root')
        config = {
            'schema': CONFIG_SCHEMA,
            'application_manifest_sha256': '3' * 64,
            'model': 'gpt-6-astra',
            'reasoning_effort': 'medium',
            'cases': [
                {'name': 'one', 'theory': 'T1',
                 'target_contract_id': 'contract:root',
                 'checkpoint_sha256': '1' * 64, 'problem': graph},
                {'name': 'two', 'theory': 'T2',
                 'target_contract_id': 'contract:root',
                 'checkpoint_sha256': '2' * 64, 'problem': graph},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path, output = root/'config.json', root/'plan.json'
            config_path.write_text(json.dumps(config))
            value = freeze_plan(config_path, output)
            stored = read_artifact(output)
        self.assertEqual(stored, value)
        self.assertEqual(
            stored['plan_id'],
            digest({
                key: item for key, item in stored.items()
                if key != 'plan_id'}))
        self.assertFalse(stored['online_authorized'])
        self.assertEqual(
            stored['source_config_raw_sha256'],
            hashlib.sha256(
                json.dumps(config).encode()).hexdigest())


if __name__ == '__main__':
    unittest.main()
