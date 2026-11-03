"""Review findings must survive summary limits, revision and File Planner handoff."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.planning.plan_review import analyze, validate_review
from regen.planning.revision_decisions import (
    build_review_handoff, decisions_valid, prior_dispositions, stable_issue_id,
    unresolved_review_issues)
from regen.tools import reviewed_planning


class ReviewClosureTests(unittest.TestCase):
    def setUp(self):
        self.plan = {'schema_version': 6, 'theories': [
            {'name': 'Provider', 'contract_ids': ['p'], 'required_imports': []},
            {'name': 'Consumer', 'contract_ids': ['c'], 'required_imports': []}]}
        self.contracts = [{'contract_id': c} for c in ['p', 'c']]
        self.issue = {'id': 'I1', 'kind': 'missing_interface',
            'theories': ['Consumer', 'Provider'], 'contract_ids': ['c', 'p'],
            'evidence': 'The selected operation is outside imports',
            'scenario': 'Consumer cannot use the selected provider before seal',
            'suggested_change': 'Select the provider or allocate a local proof',
            'tradeoff': 'Waiting versus independent reconstruction',
            'uncertainty': 'Exact premises require checking'}
        self.review = {'theory_reviews': [
            {'theory': 'Provider', 'assessment': 'self-contained', 'consumed_contract_ids': []},
            {'theory': 'Consumer', 'assessment': 'needs operation p', 'consumed_contract_ids': ['p']}],
            'issues': [self.issue]}
        self.local = {'issue_id': 'I1', 'decision': 'local',
            'reason': 'Prove the consumer operation locally',
            'tradeoff': 'Duplicate operation reasoning, avoid provider wait',
            'remaining_risk': 'Pending local proof',
            'obligations': [{'theory': 'Consumer', 'contract_ids': ['c'],
                'provider_contract_ids': ['p'], 'route': 'local',
                'task': 'Establish the operation correspondence from definitions',
                'completion_condition': 'Native check accepts c with this local correspondence'}]}

    def test_uncapped_issues_cover_more_than_eight_theories(self):
        plan = {'schema_version': 6, 'theories': [
            {'name': f'T{i}', 'contract_ids': [f'c{i}'], 'required_imports': []}
            for i in range(12)]}
        contracts = [{'contract_id': f'c{i}'} for i in range(12)]
        review = {'theory_reviews': [{'theory': f'T{i}', 'assessment': 'local concern',
                    'consumed_contract_ids': []} for i in range(12)],
                  'issues': [{**self.issue, 'id': f'I{i}', 'kind': 'precondition_gap',
                    'theories': [f'T{i}'], 'contract_ids': [f'c{i}']} for i in range(12)]}
        self.assertEqual(len(validate_review(review, plan, contracts,
                                            require_coverage=True)['issues']), 12)

    def test_missing_interface_cannot_hide_only_in_theory_assessment(self):
        review = deepcopy(self.review); review['issues'] = []
        with self.assertRaisesRegex(ValueError, 'Unresolved external interfaces'):
            validate_review(review, self.plan, self.contracts, require_coverage=True)
        validate_review(self.review, self.plan, self.contracts, require_coverage=True)
        # Transitive availability needs no spurious missing-interface issue.
        plan = deepcopy(self.plan)
        plan['theories'][1]['required_imports'] = [{'theory': 'Provider'}]
        validate_review(review, plan, self.contracts, require_coverage=True)

    def test_stable_identity_survives_order_and_rewording(self):
        revised = {**self.issue, 'id': 'I9', 'evidence': 'Rephrased',
                   'theories': list(reversed(self.issue['theories']))}
        self.assertEqual(stable_issue_id(self.issue), stable_issue_id(revised))
        self.assertNotEqual(stable_issue_id(self.issue),
                            stable_issue_id({**revised, 'kind': 'precondition_gap'}))

    def test_local_requires_grounded_work_and_completion_condition(self):
        decisions_valid([self.local], self.review, before=self.plan, after=self.plan, strict=True)
        for change in [dict(obligations=[]), dict(obligations=[{
                **self.local['obligations'][0], 'completion_condition': ''}]),
                dict(obligations=[{**self.local['obligations'][0], 'contract_ids': ['p']}])]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                decisions_valid([{**self.local, **change}], self.review,
                                before=self.plan, after=self.plan, strict=True)

    def test_revise_rejects_prose_only_and_missing_selected_provider(self):
        decision = deepcopy(self.local); decision['decision'] = 'revise'
        after = deepcopy(self.plan); after['theories'][1]['purpose'] = 'better explanation'
        with self.assertRaisesRegex(ValueError, 'without a concrete plan change'):
            decisions_valid([decision], self.review, before=self.plan, after=after, strict=True)
        decision['obligations'][0]['route'] = 'import'
        with self.assertRaisesRegex(ValueError, 'absent from the actual import closure'):
            decisions_valid([decision], self.review, before=self.plan, after=after, strict=True)
        after['theories'][1]['required_imports'] = [{'theory': 'Provider'}]
        decisions_valid([decision], self.review, before=self.plan, after=after, strict=True)

    def test_final_review_must_explicitly_accept_retained_local_work(self):
        prior = prior_dispositions(self.review, [self.local])
        self.assertEqual(unresolved_review_issues(self.review, prior), [self.issue])
        accepted = {**self.review, 'decision_reviews': [{
            'issue_id': stable_issue_id(self.issue), 'status': 'accepted',
            'reason': 'Concrete local coverage is allocated; proof remains pending'}]}
        validate_review(accepted, self.plan, self.contracts,
                        require_coverage=True, prior_dispositions=prior)
        self.assertEqual(unresolved_review_issues(accepted, prior), [])
        rejected = deepcopy(accepted); rejected['decision_reviews'][0]['status'] = 'needs_revision'
        self.assertEqual(unresolved_review_issues(rejected, prior), [self.issue])
        rejected['issues'] = []
        with self.assertRaises(ValueError):
            validate_review(rejected, self.plan, self.contracts, prior_dispositions=prior)

    def test_accepted_disposition_cannot_refer_to_an_old_owner_after_regrouping(self):
        prior = prior_dispositions(self.review, [self.local])
        plan = deepcopy(self.plan); plan['theories'][1]['name'] = 'NewConsumer'
        issue = {**self.issue, 'theories': ['NewConsumer', 'Provider']}
        self.assertEqual(stable_issue_id(issue), stable_issue_id(self.issue))
        review = deepcopy(self.review)
        review['theory_reviews'][1]['theory'] = 'NewConsumer'
        review['issues'] = [issue]
        review['decision_reviews'] = [{'issue_id': stable_issue_id(issue),
            'status': 'accepted', 'reason': 'Old allocation allegedly still sufficient'}]
        with self.assertRaisesRegex(ValueError, 'ungrounded consumer obligations'):
            validate_review(review, plan, self.contracts,
                            require_coverage=True, prior_dispositions=prior)
        review['decision_reviews'][0]['status'] = 'needs_revision'
        validate_review(review, plan, self.contracts,
                        require_coverage=True, prior_dispositions=prior)

    def test_malformed_final_assessment_returns_validation_error(self):
        prior = prior_dispositions(self.review, [self.local])
        for invalid in [{'issue_id': [], 'status': 'accepted', 'reason': 'x'},
                        {'issue_id': stable_issue_id(self.issue), 'status': [], 'reason': 'x'}]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_review({**self.review, 'decision_reviews': [invalid]},
                                self.plan, self.contracts, prior_dispositions=prior)

    def test_legacy_handoff_preserves_unresolved_status_and_plan_binding(self):
        handoff = build_review_handoff(self.plan, self.review, legacy=True)
        self.assertTrue(handoff['legacy'])
        self.assertEqual(handoff['plan_sha256'], reviewed_planning.digest(self.plan))
        entry = handoff['theories']['Consumer']['obligations'][0]
        self.assertEqual(entry['status'], 'unresolved')
        self.assertIsNone(entry['disposition'])
        self.assertEqual(entry['issue_id'], stable_issue_id(self.issue))
        self.assertNotIn('proved', entry)

    def test_accepted_local_duty_does_not_disappear_when_final_issue_is_removed(self):
        final = deepcopy(self.review); final['issues'] = []
        handoff = build_review_handoff(self.plan, final,
            accepted_dispositions=prior_dispositions(self.review, [self.local]))
        entry = handoff['theories']['Consumer']['obligations'][0]
        self.assertEqual(entry['status'], 'local')
        self.assertEqual(entry['disposition']['obligations'], self.local['obligations'])

    def test_revised_import_does_not_drop_its_pending_consumer_duty(self):
        final = deepcopy(self.review); final['issues'] = []
        revised = deepcopy(self.local); revised['decision'] = 'revise'
        revised['obligations'][0]['route'] = 'import'
        plan = deepcopy(self.plan)
        plan['theories'][1]['required_imports'] = [{'theory': 'Provider'}]
        handoff = build_review_handoff(plan, final,
            accepted_dispositions=prior_dispositions(self.review, [revised]))
        entry = handoff['theories']['Consumer']['obligations'][0]
        self.assertEqual(entry['status'], 'revised')
        self.assertEqual(entry['disposition']['obligations'], revised['obligations'])
        self.assertEqual(entry['disposition']['obligations'][0]['completion_condition'],
                         self.local['obligations'][0]['completion_condition'])

    def test_unrelated_direction_change_does_not_satisfy_revise(self):
        plan = deepcopy(self.plan)
        plan['theories'].append({'name': 'Other', 'contract_ids': ['x'], 'required_imports': []})
        after = deepcopy(plan)
        after['proof_direction_suggestions'] = [{'target_contract_id': 'x', 'direction': 'Other task'}]
        with self.assertRaisesRegex(ValueError, 'without a concrete plan change'):
            decisions_valid([{**self.local, 'decision': 'revise'}], self.review,
                            before=plan, after=after, strict=True)

    def exercise_workflow(self, root, *, bound):
        root = Path(root)
        output = root/'run'
        source = root/'input.json'; source.write_text(json.dumps({'contracts': self.contracts}))
        policy = root/'policy.json'; policy.write_text(json.dumps({'model': {}}))
        amended = deepcopy(self.plan)
        amended['theories'][1]['required_imports'] = [{'theory': 'Provider'}]
        revise = deepcopy(self.local); revise['decision'] = 'revise'
        revise['obligations'][0]['route'] = 'import'
        next_issue = {**self.issue, 'id': 'I2', 'kind': 'precondition_gap',
                      'evidence': 'Provider requires an unestablished precondition'}
        local = {**deepcopy(self.local), 'issue_id': 'I2'}
        calls = []
        def save(path, value):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value))
        def run_command(base, stage, args, *, budget=None):
            calls.append(stage); folder = base/stage
            save(base/'status.json', {'state': 'running', 'stage': stage})
            if stage in {'draft', 'revision', 'revision-0002'}:
                proposal = self.plan if stage == 'draft' else amended
                save(folder/'planner-proposal.json', proposal)
                save(folder/'agent-proposal.json', proposal)
                save(folder/'private-contract-map.json', {'p': 'p', 'c': 'c'})
                if stage != 'draft':
                    save(folder/'revision-decisions.json', [revise if stage == 'revision' else local])
                    save(folder/'repository-plan.json', {**proposal, 'plan_id': 'plan', 'revision': 1})
                    save(folder/'file-generation-list.json', {'files': []})
            else:
                review = deepcopy(self.review)
                if stage != 'review':
                    review['issues'] = [next_issue]
                    prior = json.loads(Path(args[args.index('--prior-dispositions')+1]).read_text())
                    review['decision_reviews'] = [{'issue_id': r['issue_id'], 'status': 'accepted',
                        'reason': 'Concrete allocation addresses the review concern'} for r in prior]
                save(folder/'review.json', review)
        argv = ['reviewed_planning', '--input', str(source), '--policy', str(policy),
            '--output', str(output), '--max-revision-rounds', str(bound), '--allow-external-call']
        with patch('sys.argv', argv), patch.object(reviewed_planning, 'run_command', side_effect=run_command):
            reviewed_planning.main()
        return output, calls

    def test_new_final_issue_returns_for_actual_revision_before_freeze(self):
        with tempfile.TemporaryDirectory() as tmp:
            output, calls = self.exercise_workflow(tmp, bound=3)
            self.assertEqual(calls, ['draft', 'review', 'revision', 'final-review',
                                     'revision-0002', 'final-review-0002'])
            receipt = json.loads((output/'frozen-plan.json').read_text())
            self.assertEqual(receipt['review_rounds'], 2)
            self.assertEqual(Path(receipt['plan']).parent.name, 'revision-0002')
            handoff = json.loads(Path(receipt['review_handoff']).read_text())
            entry = handoff['theories']['Consumer']['obligations'][0]
            self.assertEqual(entry['status'], 'local')
            self.assertTrue(entry['disposition']['obligations'])

    def test_revision_bound_never_freezes_an_unhandled_final_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'no plan frozen'):
                self.exercise_workflow(tmp, bound=1)
            output = Path(tmp)/'run'
            self.assertFalse((output/'frozen-plan.json').exists())
            blocked = json.loads((output/'review-blocked.json').read_text())
            self.assertEqual(blocked['reason'], 'unresolved_final_review')
            self.assertEqual(blocked['issues'][0]['id'], 'I2')


if __name__ == '__main__':
    unittest.main()
