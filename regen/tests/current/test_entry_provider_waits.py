"""Optional reuse waits must not hold unrelated fixed contracts hostage."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import digest
from regen.infrastructure.experiment_ledger import Ledger
from regen.planning.provider_waits import (record_wait, current_wait, claim_ready_wait,
    blocked_wait_contract_ids, runnable_contract_ids)
from regen.planning.review_handoff import review_context, record_review_decision, require_review_decisions
from regen.tests.current import test_provider_waits as fixtures


class EntryProviderWaitTests(unittest.TestCase):
    setUp = fixtures.ProviderWaitTests.setUp
    controller = fixtures.ProviderWaitTests.controller
    seal = fixtures.ProviderWaitTests.seal
    handoff = fixtures.ProviderWaitTests.handoff
    install = fixtures.ProviderWaitTests.install

    def add_sibling(self):
        self.packages['A']['contracts'].append({'contract_id':'a2','name':'a2'})

    def wait_for(self, entries, providers):
        return record_wait(self.c, {'action':'wait_for_providers', 'entry_ids':entries,
            'providers':providers, 'reason':'Reuse these exports if available'}, {})

    def test_subset_wait_survives_restart_and_sibling_green_without_changing_proofs(self):
        self.add_sibling()
        before=deepcopy(self.states)
        result=self.wait_for(['a'],['B'])
        self.assertFalse(result['theory_wait'])
        self.assertEqual(result['runnable_entry_ids'],['a2'])
        self.assertIsNone(self.c.provider_wait)
        self.assertEqual(before,self.states)
        self.states['A']['proofs']['a2']='by simp'
        self.states['A']['accepted_order'].append('a2')
        fresh=self.controller('A')
        wait=current_wait(fresh)
        self.assertEqual(wait['receipt_id'],result['provider_wait']['receipt_id'])
        self.assertTrue(wait['theory_wait'])
        self.assertEqual(blocked_wait_contract_ids(fresh),['a'])
        self.assertEqual(runnable_contract_ids(fresh),[])
        self.assertEqual(self.states['A']['proofs'],{'a2':'by simp'})

    def test_disjoint_waits_resume_independently_after_matching_provider_seals(self):
        self.add_sibling()
        first=self.wait_for(['a'],['B'])
        second=self.wait_for(['a2'],['C'])
        self.assertTrue(second['theory_wait'])
        self.assertEqual(len(current_wait(self.c)['waits']),2)
        self.seal('B')
        wait=current_wait(self.c)
        self.assertFalse(wait['theory_wait'])
        self.assertFalse(wait['ready'])
        self.assertEqual(wait['blocked_entry_ids'],['a2'])
        self.assertEqual(wait['runnable_entry_ids'],['a'])
        resumed=claim_ready_wait(self.c)
        self.assertEqual(resumed['receipt_id'],first['provider_wait']['receipt_id'])
        remaining=current_wait(self.c)
        self.assertEqual(remaining['entry_ids'],['a2'])
        with self.assertRaisesRegex(ValueError,'not ready'):
            claim_ready_wait(self.c)
        self.seal('C');claim_ready_wait(self.c)
        self.assertIsNone(current_wait(self.c))

    def test_terminal_provider_releases_entry_without_claiming_exports_or_imports(self):
        before=deepcopy(self.states['A'])
        original=self.wait_for(['a'],['B'])
        self.states['B']['status']='retired'
        wait=current_wait(self.controller('A'))
        self.assertFalse(wait['ready'])
        self.assertTrue(wait['invalidated'])
        self.assertTrue(wait['requires_route_reselection'])
        self.assertFalse(wait['theory_wait'])
        self.assertEqual(wait['runnable_entry_ids'],['a'])
        self.assertFalse(wait['failed_providers'][0]['exports_available'])
        claimed=claim_ready_wait(self.c)
        self.assertEqual(claimed['resumed_waits'],[])
        self.assertEqual(claimed['invalidated_waits'][0]['receipt_id'],
                         original['provider_wait']['receipt_id'])
        self.assertIsNone(current_wait(self.controller('A')))
        row=Ledger(self.root/'provider-waits'/digest('A'),'fixture').rows[-1]
        self.assertEqual(row['kind'],'provider_wait_invalidated')
        self.assertEqual(row['data']['imports_added'],0)
        self.assertEqual(self.states['A'],before)

    def test_terminal_and_ready_waits_claim_independently_of_still_waiting_siblings(self):
        self.add_sibling()
        self.wait_for(['a'],['B']);self.wait_for(['a2'],['C'])
        self.states['B']['status']='retired'
        wait=current_wait(self.c)
        self.assertEqual(wait['blocked_entry_ids'],['a2'])
        self.assertEqual(wait['runnable_entry_ids'],['a'])
        claimed=claim_ready_wait(self.c)
        self.assertEqual(len(claimed['invalidated_waits']),1)
        self.assertEqual(current_wait(self.c)['entry_ids'],['a2'])
        self.seal('C')
        claimed=claim_ready_wait(self.c)
        self.assertEqual(len(claimed['resumed_waits']),1)
        self.assertEqual(claimed['invalidated_waits'],[])

    def test_invalid_wait_requires_new_review_decision_before_and_after_claim(self):
        self.install()
        record_wait(self.c,{'action':'wait_for_providers','entry_ids':['a'],
            'providers':['B'],'reason':'Need B export','review_issue_ids':['stable-issue']},{})
        require_review_decisions(self.c)
        self.states['B']['status']='retired'
        issue=review_context(self.c)['obligations'][0]
        self.assertTrue(issue['requires_generation_decision'])
        self.assertEqual(issue['decision_invalid_reason'],'selected_provider_no_longer_available')
        with self.assertRaisesRegex(ValueError,'record_review_decision'):
            require_review_decisions(self.c)
        claim_ready_wait(self.c)
        with self.assertRaisesRegex(ValueError,'record_review_decision'):
            require_review_decisions(self.c)
        record_review_decision(self.c,{'action':'record_review_decision','issue_id':'stable-issue',
            'entry_ids':['a'],'decision':'local','reason':'Provider cannot export the needed fact',
            'obligations':['Prove the local replacement in this context']},{})
        require_review_decisions(self.c)

    def test_terminal_wait_does_not_relax_actual_header_import(self):
        from regen.planning.proof_workspace_agent import WorkspaceController
        self.c.selected=['A','B','C']
        self.packages['A']['repository_projection']['required_imports']=['B']
        self.wait_for(['a'],['B'])
        self.states['B']['status']='retired'
        readiness=WorkspaceController._theory_readiness(self.c,'A')
        self.assertEqual(readiness['runnable_contract_ids'],['a'])
        self.assertFalse(readiness['ready_for_proof_action'])
        self.assertEqual(readiness['unsealed_providers'],['B'])

    def test_legacy_refutation_releases_wait_but_does_not_publish_positive_fact(self):
        self.wait_for(['a'],['B'])
        with patch('regen.planning.theory_outcomes.current_outcome',
                   side_effect=lambda controller,theory=None:
                       {'status':'refuted'} if theory=='B' else None):
            wait=current_wait(self.c)
            self.assertTrue(wait['invalidated'])
            self.assertFalse(wait['ready'])
            self.assertEqual(wait['failed_providers'][0]['terminal_reason'],'refuted_provider')

    def test_retired_provider_rejects_new_wait_but_partial_export_is_ready(self):
        self.states['B']['status']='retired'
        with self.assertRaisesRegex(ValueError,'without positive exports'):
            self.wait_for(['a'],['B'])
        self.states['B']['status']='generating'
        self.wait_for(['a'],['B'])
        self.states['B']['status']='exported_partial'
        wait=current_wait(self.c)
        self.assertTrue(wait['ready'])
        self.assertFalse(wait['invalidated'])

    def test_invalidated_wait_allows_a_different_provider_without_claiming_old_export(self):
        self.wait_for(['a'],['B'])
        self.states['B']['status']='retired'
        self.wait_for(['a'],['C'])
        self.assertEqual(current_wait(self.c)['blocked_entry_ids'],['a'])
        claim_ready_wait(self.c)
        wait=current_wait(self.c)
        self.assertEqual([p['theory'] for p in wait['providers']],['C'])
        self.assertFalse(wait['ready'])

    def test_entry_wait_never_relaxes_actual_header_imports(self):
        from regen.planning.proof_workspace_agent import WorkspaceController
        self.add_sibling()
        self.c.selected=['A','B','C']
        self.packages['A']['repository_projection']['required_imports']=['C']
        self.wait_for(['a'],['B'])
        readiness=WorkspaceController._theory_readiness(self.c,'A')
        self.assertFalse(readiness['ready_for_proof_action'])
        self.assertEqual(readiness['unsealed_providers'],['C'])
        self.assertEqual(readiness['runnable_contract_ids'],['a2'])
        self.seal('C')
        self.assertTrue(WorkspaceController._theory_readiness(self.c,'A')['ready_for_proof_action'])

    def test_context_revision_invalidates_old_optional_wait(self):
        self.add_sibling();self.wait_for(['a'],['B'])
        self.states['A'].update(context_revision=1,context_imports=[])
        self.assertIsNone(current_wait(self.c))
        self.assertEqual(runnable_contract_ids(self.c),['a','a2'])

    def test_duplicate_target_wait_and_optional_cycle_are_rejected(self):
        self.add_sibling();self.wait_for(['a'],['B'])
        with self.assertRaisesRegex(ValueError,'active provider wait'):
            self.wait_for(['a'],['C'])
        with self.assertRaisesRegex(ValueError,'cycle'):
            record_wait(self.controller('B'),{'action':'wait_for_providers','entry_ids':['b'],
                'providers':['A'],'reason':'Would form an optional cycle'}, {})

    def test_completed_contract_drops_wait_without_publishing_provider_or_import(self):
        self.wait_for(['a'],['B'])
        self.states['A']['proofs']['a']='by simp'
        self.states['A']['accepted_order'].append('a')
        self.assertIsNone(current_wait(self.c))
        self.assertFalse(self.states['B']['seal_id'])
        self.assertEqual(self.packages['A']['repository_projection']['required_imports'],[])

    def test_legacy_wait_keeps_whole_state_binding_semantics(self):
        from regen.planning.theory_outcomes import _snapshot
        from regen.planning.provider_waits import _provider
        ledger=Ledger(self.root/'provider-waits'/digest('A'),'fixture')
        ledger.append('provider_wait_started',schema='provider-wait-v1',
            binding=_snapshot(self.c)[2],providers=[_provider(self.c,'B')],entry_ids=['a'],
            reason='Old frozen receipt',review_issue_ids=[],metadata={},positive_proofs_added=0,imports_added=0)
        self.assertTrue(current_wait(self.c)['theory_wait'])
        self.states['A']['graph']['nodes'].append({'id':'new-helper'})
        self.assertIsNone(current_wait(self.c))

    def test_completed_green_review_no_longer_requests_impossible_pending_decision(self):
        self.install()
        self.states['A']['proofs']['a']='by simp'
        issue=review_context(self.c)['obligations'][0]
        self.assertFalse(issue['requires_generation_decision'])
        self.assertEqual(issue['pending_contract_ids'],[])
        self.assertEqual(issue['completed_contract_ids'],['a'])
        require_review_decisions(self.c)
        result=record_review_decision(self.c,{'action':'record_review_decision','issue_id':'stable-issue',
            'entry_ids':['a'],'decision':'local','reason':'This was already checked',
            'obligations':['None remain']},{})
        self.assertEqual(result['status'],'not_required')
        self.assertEqual(Ledger(self.root/'review-decisions'/digest('A'),'fixture').rows,[])

    def test_review_wait_cannot_borrow_another_entrys_provider_registration(self):
        self.add_sibling();self.install()
        self.wait_for(['a'],['B']);self.wait_for(['a2'],['C'])
        with self.assertRaisesRegex(ValueError,'registered provider wait'):
            record_review_decision(self.c,{'action':'record_review_decision','issue_id':'stable-issue',
                'entry_ids':['a'],'decision':'wait','providers':['C'],'reason':'Wrong entry provider',
                'obligations':['Wait']},{})

    def test_disposed_contract_requires_no_new_review_decision_or_provider_wait(self):
        from regen.planning.theory_outcomes import CONTRACT_POLICY, record_unresolved
        self.run['contract_outcome_policy']=CONTRACT_POLICY
        self.packages['A']['contracts'][0].update(exact_statement='lemma a: "False"',
                                                contract_owned_context=[])
        self.install()
        record_unresolved(self.c,reason='Legacy synthetic search disposition',origin='model',entry_ids=['a'])
        issue=review_context(self.c)['obligations'][0]
        self.assertFalse(issue['requires_generation_decision'])
        self.assertEqual(issue['completed_contract_ids'],[])
        self.assertEqual(issue['disposed_contract_ids'],['a'])
        require_review_decisions(self.c)


if __name__=='__main__':
    unittest.main()
