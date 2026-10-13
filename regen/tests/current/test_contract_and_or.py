"""Contract-rooted AND/OR search tests; no model or native worker is started."""
from pathlib import Path
import unittest

from regen.planning.contract_and_or import (
    PlanFrontierLimitExceeded, SCHEMA, apply_graph_revision, apply_node_results,
    apply_parent_probe, best_plan, initial_state, next_decision, validate_problem,
)
from regen.tools.contract_and_or_replay import (
    DELETE_ROOT, REPOSITORY, RUN, SCHEDULE_ROOT, delete_objects_problem, report,
    replay_delete_objects, replay_schedule, schedule_problem,
)


def route(key, requires=(), cost=1, **extra):
    return {'id': key, 'requires': list(requires), 'estimate_seconds': cost,
            'recheck_seconds': cost, **extra}


def goal(key, order, routes, role='helper'):
    return {'id': key, 'order': order, 'role': role, 'routes': routes}


class ContractAndOrTests(unittest.TestCase):
    def problem(self, goals, root='root'):
        return {'schema_version': 1, 'method': SCHEMA,
                'root_goal': root, 'goals': goals}

    def test_theorem_role_is_a_frozen_planner_declaration(self):
        problem = self.problem([
            goal('root', 1, [route('root/r')], 'theorem')])
        validate_problem(problem)

    def test_shared_and_subgraph_cost_is_counted_once(self):
        problem = self.problem([
            goal('shared', 1, [route('shared/r', cost=5)]),
            goal('left', 2, [route('left/r', ('shared',), cost=2)]),
            goal('right', 3, [route('right/r', ('shared',), cost=3)]),
            goal('root', 4, [route('root/r', ('left', 'right'), cost=1)], 'contract'),
        ])
        plan = best_plan(problem, initial_state(problem))
        self.assertEqual(plan['estimated_seconds'], 11)
        self.assertEqual(set(plan['goal_cost_seconds']), {'shared', 'left', 'right', 'root'})

    def test_shared_or_choice_is_globally_optimal(self):
        problem = self.problem([
            goal('shared', 1, [route('shared/r', cost=6)]),
            goal('a', 2, [
                route('a/private', cost=5),
                route('a/shared', ('shared',), cost=0),
            ]),
            goal('b', 3, [route('b/shared', ('shared',), cost=0)]),
            goal('root', 4, [route('root/r', ('a', 'b'), cost=0)], 'contract'),
        ])
        plan = best_plan(problem, initial_state(problem))
        self.assertEqual(plan['estimated_seconds'], 6)
        self.assertEqual(plan['selected_routes']['a'], 'a/shared')
        self.assertEqual(plan['selected_routes']['shared'], 'shared/r')

    def test_exact_frontier_limit_fails_closed(self):
        problem = self.problem([
            goal('root', 1, [
                route('root/a', cost=1),
                route('root/b', cost=2),
            ], 'contract'),
        ])
        problem['max_frontier_plans'] = 1
        with self.assertRaises(PlanFrontierLimitExceeded):
            best_plan(problem, initial_state(problem))

    def test_failure_penalty_prevents_unlimited_or_route_starvation(self):
        problem = self.problem([
            goal('root', 1, [
                route('root/cheap', cost=1, failure_penalty_seconds=5),
                route('root/alternative', cost=4),
            ], 'contract'),
        ])
        state = initial_state(problem)
        self.assertEqual(best_plan(problem, state)['selected_routes']['root'],
                         'root/cheap')
        state = apply_node_results(problem, state, [{
            'goal_id': 'root', 'route_id': 'root/cheap', 'status': 'failed'}])
        plan = best_plan(problem, state)
        self.assertEqual(plan['selected_routes']['root'], 'root/alternative')
        self.assertEqual(next_decision(problem, state)['route_id'],
                         'root/alternative')

    def test_default_failure_penalty_eventually_rotates_or_route(self):
        problem = self.problem([
            goal('root', 1, [
                route('root/cheap', cost=1),
                route('root/alternative', cost=4),
            ], 'contract'),
        ])
        state = initial_state(problem)
        for _ in range(4):
            state = apply_node_results(problem, state, [{
                'goal_id': 'root', 'route_id': 'root/cheap', 'status': 'failed'}])
        self.assertEqual(best_plan(problem, state)['selected_routes']['root'],
                         'root/alternative')

    def test_probe_cost_is_explicit_in_plan_estimate(self):
        problem = self.problem([
            goal('helper', 1, [route('helper/r', cost=2)]),
            goal('root', 2, [
                route('root/r', ('helper',), cost=1, probe_seconds=7),
            ], 'contract'),
        ])
        plan = best_plan(problem, initial_state(problem))
        self.assertEqual(plan['estimated_proof_seconds'], 3)
        self.assertEqual(plan['estimated_probe_seconds'], 7)
        self.assertEqual(plan['estimated_seconds'], 10)
        self.assertEqual(plan['probe_cost_seconds'], {'root': 7.0})

    def test_each_child_probe_policy_budgets_every_direct_child(self):
        problem = self.problem([
            goal('left', 1, [route('left/r', cost=0)]),
            goal('right', 2, [route('right/r', cost=0)]),
            goal('root', 3, [
                route('root/r', ('left', 'right'), cost=0, probe_seconds=7),
            ], 'contract'),
        ])
        plan = best_plan(problem, initial_state(problem))
        self.assertEqual(plan['estimated_probe_seconds'], 7)
        problem['goals'][-1]['routes'][0]['probe_policy'] = 'each_accepted_child'
        plan = best_plan(problem, initial_state(problem))
        self.assertEqual(plan['estimated_probe_seconds'], 14)

    def test_none_probe_policy_is_conservative_and_proves_parent(self):
        problem = self.problem([
            goal('helper', 1, [route('helper/r', cost=2)]),
            goal('root', 2, [route(
                'root/r', ('helper',), cost=1, probe_seconds=7,
                probe_policy='none')], 'contract'),
        ])
        plan = best_plan(problem, initial_state(problem))
        self.assertEqual(plan['estimated_probe_seconds'], 0)
        state = apply_node_results(problem, initial_state(problem), [{
            'goal_id': 'helper', 'route_id': 'helper/r',
            'status': 'accepted_green',
        }])
        self.assertEqual(state['pending_parent_probes'], [])
        decision = next_decision(problem, state)
        self.assertEqual(decision['action'], 'PROVE')
        self.assertEqual(decision['goal_id'], 'root')

    def test_shared_helper_creates_probe_for_every_nearest_consumer(self):
        problem = self.problem([
            goal('shared', 1, [route('shared/r')]),
            goal('left', 2, [route('left/r', ('shared',))]),
            goal('right', 3, [route('right/r', ('shared',))]),
            goal('root', 4, [route('root/r', ('left', 'right'))], 'contract'),
        ])
        state = apply_node_results(problem, initial_state(problem), [{
            'goal_id': 'shared', 'route_id': 'shared/r',
            'status': 'accepted_green',
        }])
        self.assertEqual(
            [item['target_goal'] for item in state['pending_parent_probes']],
            ['left', 'right'])
        self.assertTrue(all(item['child_goals'] == ['shared']
                            for item in state['pending_parent_probes']))

    def test_default_and_waits_for_every_required_child(self):
        problem = self.problem([
            goal('left', 1, [route('left/r')]),
            goal('right', 2, [route('right/r')]),
            goal('root', 3, [route('root/r', ('left', 'right'))], 'contract'),
        ])
        state = apply_node_results(problem, initial_state(problem), [{
            'goal_id': 'left', 'route_id': 'left/r', 'status': 'accepted_green',
        }])
        self.assertEqual(state['pending_parent_probes'], [])
        self.assertEqual(next_decision(problem, state)['goal_id'], 'right')
        state = apply_node_results(problem, state, [{
            'goal_id': 'right', 'route_id': 'right/r', 'status': 'accepted_green',
        }])
        self.assertEqual(len(state['pending_parent_probes']), 1)
        self.assertEqual(
            state['pending_parent_probes'][0]['child_goals'],
            ['left', 'right'])
        self.assertEqual(next_decision(problem, state)['action'], 'PROBE_PARENT')

    def test_incomplete_each_child_no_progress_does_not_disable_route(self):
        problem = self.problem([
            goal('left', 1, [route('left/r')]),
            goal('right', 2, [route('right/r')]),
            goal('root', 3, [
                route('root/r', ('left', 'right'), probe_seconds=7,
                      probe_policy='each_accepted_child'),
            ], 'contract'),
        ])
        state = apply_node_results(problem, initial_state(problem), [{
            'goal_id': 'left', 'route_id': 'left/r', 'status': 'accepted_green',
        }])
        state = apply_parent_probe(
            problem, state, target_goal='root', verdict='no_progress',
            before_fingerprint='same', after_fingerprint='same')
        self.assertFalse(state['required_graph_revisions'])
        self.assertFalse(state['disabled_routes'])
        self.assertEqual(state['no_progress_parent_edges'][0]['probe_scope'],
                         'child_hypothesis')
        self.assertEqual(best_plan(problem, state)['estimated_probe_seconds'], 7)
        self.assertEqual(next_decision(problem, state)['goal_id'], 'right')

        state = apply_node_results(problem, state, [{
            'goal_id': 'right', 'route_id': 'right/r', 'status': 'accepted_green',
        }])
        state = apply_parent_probe(
            problem, state, target_goal='root', verdict='no_progress',
            before_fingerprint='same', after_fingerprint='same')
        self.assertEqual(next_decision(problem, state)['action'], 'REVISE_GRAPH')
        self.assertEqual(state['no_progress_parent_edges'][-1]['probe_scope'],
                         'child_hypothesis')
        self.assertEqual(
            state['required_graph_revisions'][0]['child_goals'],
            ['left', 'right'])

    def test_prior_child_progress_prevents_route_level_pruning(self):
        problem = self.problem([
            goal('left', 1, [route('left/r')]),
            goal('right', 2, [route('right/r')]),
            goal('root', 3, [
                route('root/r', ('left', 'right'),
                      probe_policy='each_accepted_child'),
            ], 'contract'),
        ])
        state = apply_node_results(problem, initial_state(problem), [{
            'goal_id': 'left', 'route_id': 'left/r', 'status': 'accepted_green',
        }])
        state = apply_parent_probe(
            problem, state, target_goal='root', verdict='progress',
            before_fingerprint='A', after_fingerprint='B')
        self.assertEqual(next_decision(problem, state)['goal_id'], 'right')

        state = apply_node_results(problem, state, [{
            'goal_id': 'right', 'route_id': 'right/r', 'status': 'accepted_green',
        }])
        state = apply_parent_probe(
            problem, state, target_goal='root', verdict='no_progress',
            before_fingerprint='B', after_fingerprint='B')
        self.assertFalse(state['required_graph_revisions'])
        self.assertFalse(state['disabled_routes'])
        decision = next_decision(problem, state)
        self.assertEqual(decision['action'], 'PROVE')
        self.assertEqual(decision['goal_id'], 'root')

    def test_no_progress_requires_identical_nonempty_fingerprints(self):
        problem = self.problem([
            goal('helper', 1, [route('helper/r')]),
            goal('root', 2, [route('root/r', ('helper',))], 'contract'),
        ])
        state = apply_node_results(problem, initial_state(problem), [{
            'goal_id': 'helper', 'route_id': 'helper/r',
            'status': 'accepted_green',
        }])
        with self.assertRaisesRegex(ValueError, 'non-empty'):
            apply_parent_probe(
                problem, state, target_goal='root', verdict='no_progress')
        with self.assertRaisesRegex(ValueError, 'identical'):
            apply_parent_probe(
                problem, state, target_goal='root', verdict='no_progress',
                before_fingerprint='before', after_fingerprint='after')
        self.assertEqual(next_decision(problem, state)['action'], 'PROBE_PARENT')

    def test_no_progress_requires_explicit_graph_revision_and_replans(self):
        problem = self.problem([
            goal('helper', 1, [route('helper/r')]),
            goal('root', 2, [
                route('root/via-helper', ('helper',), cost=0),
                route('root/direct', cost=10),
            ], 'contract'),
        ])
        state = apply_node_results(problem, initial_state(problem), [{
            'goal_id': 'helper', 'route_id': 'helper/r',
            'status': 'accepted_green',
        }])
        state = apply_parent_probe(
            problem, state, target_goal='root', verdict='no_progress',
            before_fingerprint='same', after_fingerprint='same')
        decision = next_decision(problem, state)
        self.assertEqual(decision['action'], 'REVISE_GRAPH')
        self.assertEqual(decision['selected_route'], 'root/via-helper')
        self.assertNotIn('root/via-helper', state['disabled_routes'])

        state = apply_graph_revision(
            problem, state, target_goal='root',
            resolution='disable_selected_route')
        self.assertEqual(state['disabled_routes'], ['root/via-helper'])
        self.assertFalse(state['required_graph_revisions'])
        decision = next_decision(problem, state)
        self.assertEqual(decision['action'], 'PROVE')
        self.assertEqual(decision['route_id'], 'root/direct')

    def test_graph_revision_blocks_when_no_alternative_route_remains(self):
        problem = self.problem([
            goal('helper', 1, [route('helper/r')]),
            goal('root', 2, [
                route('root/only', ('helper',), cost=0),
            ], 'contract'),
        ])
        state = apply_node_results(problem, initial_state(problem), [{
            'goal_id': 'helper', 'route_id': 'helper/r',
            'status': 'accepted_green',
        }])
        state = apply_parent_probe(
            problem, state, target_goal='root', verdict='no_progress',
            before_fingerprint='same', after_fingerprint='same')
        state = apply_graph_revision(
            problem, state, target_goal='root',
            resolution='disable_selected_route')
        self.assertEqual(next_decision(problem, state)['action'], 'BLOCKED')

    def test_repeated_accepted_green_is_idempotent(self):
        problem = self.problem([
            goal('helper', 1, [route('helper/r')]),
            goal('root', 2, [route('root/r', ('helper',))], 'contract'),
        ])
        result = {'goal_id': 'helper', 'route_id': 'helper/r',
                  'status': 'accepted_green'}
        state = apply_node_results(problem, initial_state(problem), [result])
        first_pending = list(state['pending_parent_probes'])
        state = apply_node_results(problem, state, [result])
        self.assertEqual(state['certified'], ['helper'])
        self.assertEqual(state['pending_parent_probes'], first_pending)


    def test_exhausted_direct_or_route_selects_helper_and_route(self):
        problem = delete_objects_problem()
        state = initial_state(problem)
        state = apply_node_results(problem, state, [{
            'goal_id': DELETE_ROOT, 'route_id': 'delete/root-direct', 'status': 'failed'}])
        decision = next_decision(problem, state)
        self.assertEqual(decision['action'], 'PROVE')
        self.assertEqual(decision['goal_id'], 'helper:ul_aligned_range_card')
        self.assertEqual(best_plan(problem, state)['selected_routes'][DELETE_ROOT],
                         'delete/root-architecture')

    def test_red_is_a_route_attempt_not_a_false_goal(self):
        problem = self.problem([
            goal('root', 1, [route('root/unlimited')], 'contract'),
        ])
        state = apply_node_results(problem, initial_state(problem), [{
            'goal_id': 'root', 'route_id': 'root/unlimited', 'status': 'failed'}])
        self.assertNotIn('root', state['certified'])
        self.assertEqual(next_decision(problem, state)['action'], 'PROVE')

    def test_completed_uncommitted_rechecks_maximal_root_prefix_only(self):
        problem = self.problem([
            goal('leaf', 1, [route('leaf/r')]),
            goal('middle', 2, [route('middle/r', ('leaf',))]),
            goal('root', 3, [route('root/r', ('middle',))], 'contract'),
            goal('off-root', 4, [route('off/r')]),
        ])
        state = initial_state(problem)
        state = apply_node_results(problem, state, [
            {'goal_id': 'leaf', 'route_id': 'leaf/r', 'status': 'completed_uncommitted'},
            {'goal_id': 'middle', 'route_id': 'middle/r', 'status': 'completed_uncommitted'},
            {'goal_id': 'root', 'route_id': 'root/r', 'status': 'completed_uncommitted'},
            {'goal_id': 'off-root', 'route_id': 'off/r', 'status': 'completed_uncommitted'},
        ])
        decision = next_decision(problem, state)
        self.assertEqual(decision['action'], 'RECHECK_PREFIX')
        self.assertEqual(decision['goal_ids'], ['leaf', 'middle', 'root'])
        self.assertNotIn('off-root', decision['goal_ids'])
        self.assertEqual(state['certified'], [])

    def test_only_journal_acceptance_certifies(self):
        problem = self.problem([goal('root', 1, [route('root/r')], 'contract')])
        for status in ('completed_uncommitted', 'audited_green'):
            with self.subTest(status=status):
                state = apply_node_results(problem, initial_state(problem), [{
                    'goal_id': 'root', 'route_id': 'root/r', 'status': status}])
                self.assertEqual(state['certified'], [])
                self.assertEqual(next_decision(problem, state)['action'], 'RECHECK_PREFIX')
        accepted = apply_node_results(problem, initial_state(problem), [{
            'goal_id': 'root', 'route_id': 'root/r', 'status': 'accepted_green'}])
        self.assertEqual(next_decision(problem, accepted)['action'], 'SOLVED')

    def test_infrastructure_failure_does_not_change_math_state(self):
        problem = self.problem([goal('root', 1, [route('root/r')], 'contract')])
        before = apply_node_results(problem, initial_state(problem), [{
            'goal_id': 'root', 'route_id': 'root/r', 'status': 'completed_uncommitted'}])
        after = apply_node_results(problem, before, [{
            'goal_id': 'root', 'route_id': 'root/r', 'status': 'infrastructure_failure'}])
        self.assertEqual(after['certified'], before['certified'])
        self.assertEqual(after['candidate_routes'], before['candidate_routes'])
        self.assertEqual(after['route_attempts'], before['route_attempts'])

    def test_accepted_helper_requires_checker_observed_parent_probe(self):
        problem = self.problem([
            goal('helper', 1, [route('helper/r')]),
            goal('root', 2, [route('root/r', ('helper',))], 'contract'),
        ])
        state = apply_node_results(problem, initial_state(problem), [{
            'goal_id': 'helper', 'route_id': 'helper/r', 'status': 'accepted_green'}])
        decision = next_decision(problem, state)
        self.assertEqual(decision['action'], 'PROBE_PARENT')
        self.assertEqual(decision['target_goal'], 'root')
        interrupted = apply_parent_probe(problem, state, target_goal='root',
            verdict='infrastructure_failure', evidence={'kind': 'network'})
        self.assertEqual(next_decision(problem, interrupted)['action'], 'PROBE_PARENT')
        progressed = apply_parent_probe(problem, interrupted, target_goal='root',
            verdict='progress', before_fingerprint='residual-a',
            after_fingerprint='residual-b')
        self.assertFalse(progressed['pending_parent_probes'])
        self.assertEqual(progressed['validated_parent_edges'][0]['child_goal'], 'helper')
        self.assertEqual(next_decision(problem, progressed)['action'], 'PROVE')

    def test_schedule_hypothesis_requires_probe_before_more_helpers(self):
        problem = schedule_problem()
        routes = {goal['id']: goal['routes'][0]['id'] for goal in problem['goals']}
        state = initial_state(problem)
        state = apply_node_results(problem, state, [
            {'goal_id': 'helper:dispatch_state_gets_commute',
             'route_id': routes['helper:dispatch_state_gets_commute'],
             'status': 'completed_uncommitted'},
            {'goal_id': SCHEDULE_ROOT, 'route_id': 'schedule/root-direct', 'status': 'failed'},
        ])
        self.assertEqual(next_decision(problem, state)['action'], 'RECHECK_PREFIX')
        state = apply_node_results(problem, state, [
            {'goal_id': 'helper:dispatch_state_gets_commute',
             'route_id': routes['helper:dispatch_state_gets_commute'],
             'status': 'accepted_green'},
            {'goal_id': 'helper:dispatch_runnable_bind',
             'route_id': routes['helper:dispatch_runnable_bind'],
             'status': 'accepted_green'},
            {'goal_id': 'helper:dispatch_resume', 'route_id': 'schedule/resume',
             'status': 'accepted_green'},
        ])
        decision = next_decision(problem, state)
        self.assertEqual(decision['action'], 'PROBE_PARENT')
        self.assertEqual(decision['target_goal'], SCHEDULE_ROOT)

    def test_rejects_unknown_probe_policy(self):
        problem = self.problem([
            goal('root', 1, [
                route('root/r', probe_policy='unsafe'),
            ], 'contract'),
        ])
        with self.assertRaisesRegex(ValueError, 'probe_policy'):
            validate_problem(problem)

    def test_rejects_cycles_even_when_an_alternative_route_is_acyclic(self):
        problem = self.problem([
            goal('child', 1, [route('child/cycle', ('root',)), route('child/leaf')]),
            goal('root', 2, [route('root/r', ('child',))], 'contract'),
        ])
        with self.assertRaisesRegex(ValueError, 'cyclic'):
            validate_problem(problem)

    def test_rejects_cycle_outside_root_reachable_subgraph(self):
        problem = self.problem([
            goal('root', 1, [route('root/r')], 'contract'),
            goal('off-a', 2, [route('off-a/r', ('off-b',))]),
            goal('off-b', 3, [route('off-b/r', ('off-a',))]),
        ])
        with self.assertRaisesRegex(ValueError, 'cyclic'):
            validate_problem(problem)


@unittest.skipUnless((REPOSITORY / RUN).is_dir(), 'historical replay artifacts unavailable')
class ContractAndOrHistoricalReplayTests(unittest.TestCase):
    def test_delete_objects_real_trace(self):
        result = replay_delete_objects(REPOSITORY)
        self.assertEqual(result['final_decision']['action'], 'SOLVED')
        self.assertEqual(result['measurements']['repeated_completed_node_executions'], 7)
        self.assertEqual(result['policy_decisions'][0]['decision']['goal_id'],
                         'helper:ul_aligned_range_card')
        self.assertEqual(result['policy_decisions'][2]['decision']['action'],
                         'RECHECK_PREFIX')
        self.assertEqual(result['counterfactual_after_first_prefix_acceptance']['action'],
                         'PROVE')
        self.assertEqual(result['counterfactual_after_first_prefix_acceptance']['goal_id'],
                         'helper:ul_arch_cut_bits')
        self.assertEqual(result['edge_basis']['kind'],
                         'posthoc_evaluator_reconstruction')
        by_request = {item['request_number']: item for item in result['evidence']}
        self.assertTrue(all(by_request[number]['acceptance_journal'] is None
                            for number in range(61, 67)))
        journal = by_request[67]['acceptance_journal']
        self.assertEqual(journal['sequence'], 55)
        self.assertTrue(journal['commit_path'].endswith(
            '/Untyped_Object_Lifecycle/acceptance/commits/000055.json'))
        self.assertEqual(len(journal['raw_manifest_sha256']), 64)
        self.assertEqual(len(journal['commit_id']), 64)


    def test_schedule_real_trace_exposes_helper_drift_and_cancelled_check(self):
        result = replay_schedule(REPOSITORY)
        audit = result['historical_drift_audit']
        self.assertEqual(result['root_direct_attempt']['residual_count'], 9)
        self.assertEqual(audit['accepted_helper_count'], 24)
        self.assertEqual(audit['root_recheck_requests_67_97'], [])
        self.assertEqual(result['policy_decisions'][1]['decision']['action'], 'PROBE_PARENT')
        self.assertEqual(result['deep_frontier']['request_95_residual_count'], 4)
        self.assertEqual(result['deep_frontier']['request_96_residual_count'], 2)
        self.assertEqual(result['deep_frontier']['request_97_outcome'], 'CheckCancelled')
        self.assertFalse(result['posthoc_dependency_oracle'])
        self.assertEqual(result['edge_basis']['kind'], 'mixed')
        semantic_routes = {
            item['route_id'] for item in result['edge_basis']['semantic_hypothesis']
        }
        self.assertEqual(semantic_routes,
                         {'schedule/enqueue', 'schedule/root-actions'})
        self.assertEqual(audit['accepted_helper_batch_requests'],
                         [68, 76, 85, 90, 94])
        bound = audit['journal_bound_helper_batches']
        self.assertEqual([item['request_number'] for item in bound],
                         [68, 76, 85, 90, 94])
        self.assertEqual([item['acceptance_journal']['sequence'] for item in bound],
                         [50, 69, 89, 99, 108])
        for item in bound:
            journal = item['acceptance_journal']
            self.assertTrue(journal['commit_path'])
            self.assertEqual(len(journal['raw_manifest_sha256']), 64)
            self.assertEqual(len(journal['commit_id']), 64)

    def test_report_binds_frozen_source_run(self):
        payload = report(REPOSITORY)
        self.assertEqual(payload['edge_basis'], 'mixed')
        artifacts = payload['source_run_binding']['artifacts']
        self.assertEqual([Path(item['path']).name for item in artifacts], [
            'run.json', 'execution-input.json', 'experiment.json',
            'log-storage.json',
        ])
        self.assertTrue(all(Path(item['path']).parent.name == 'proof'
                            for item in artifacts))
        self.assertTrue(all(len(item['raw_sha256']) == 64
                            for item in artifacts))



if __name__ == '__main__':
    unittest.main()
