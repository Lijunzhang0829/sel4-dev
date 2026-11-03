"""Synthetic native receipts exercise stop admission; no proof credit is claimed."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import digest, durable
from regen.execution.proof_batches import _render
from regen.planning.batch_file_planner import action_record
from regen.planning.declaration_index import initial_index, accepted_declarations
from regen.planning.theory_document import render, candidate
from regen.planning.theory_outcomes import record_unresolved, current_dispositions
from regen.planning.unresolved_evidence import POLICY, eligibility_view, strict_policy_enabled


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


class UnresolvedEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.package = {'theory':'T', 'file_slot':'T.thy', 'contracts':[
            {'contract_id':key, 'name':key, 'exact_statement':f'lemma {key}: "True"',
             'contract_owned_context':[]} for key in ('a','b','c')]}
        self.state = {'status':'generating', 'graph':accepted_declarations(
            initial_index(self.package, run_id='r--T'), ['a']),
            'proofs':{'a':'by simp'}, 'accepted_order':['a'], 'batch_history':[]}
        self.run = {'run_id':'r', 'base_import':'Main',
                    'contract_outcome_policy':'contract-dispositions-v1',
                    'unresolved_evidence_policy':POLICY}
        self.c = SimpleNamespace(root=self.root, theory='T', run=self.run,
            _state=lambda _: (self.package, self.state))
        self.directory = self.root/'theories/T'
        self.draft = self.directory/'document/draft.thy'
        self.draft.parent.mkdir(parents=True)
        self.draft.write_text(render(self.package, self.state, ['Main']))

    def attempt(self, proof='by fail', *, key='b', feedback_mutate=None, history_mutate=None,
                native_id=None, write_draft=True):
        number = len(self.state['batch_history']) + 1
        draft_state = deepcopy(self.state); draft_state['proofs'][key] = proof
        source = render(self.package, draft_state, ['Main'])
        prospective = candidate(self.package, self.state, source, ['Main'])
        body = prospective['proofs'][key]
        proposal = {'action':'submit_batch', 'proofs':prospective['proofs'], 'document_source':source}
        native_source = _render(prospective['graph'], prospective['proofs'], ['Main'],
                                'Batch_Check_' + digest(['Main'])[:16])
        log = f'REGEN_NODE {key}\nFailed to apply initial proof method\n'
        check = {'verdict':'red', 'returncode':1, 'timed_out':False,
            'diagnostic':'Failed to apply initial proof method',
            'source_content':native_source, 'source_sha256':sha(native_source),
            'log_content':log, 'log_sha256':sha(log),
            'native_receipt':{'schema':'native-check-receipt-v1',
                'request_id':native_id or f'native-{number}', 'check_scope':'cold-full-theory',
                'returncode':1, 'verdict':'red', 'source_sha256':sha(native_source), 'log_sha256':sha(log)}}
        feedback = {'status':'rejected_batch', 'check':check, 'cached_failure':False,
                    'checked_delta_ids':[key], 'node_results':[{'node_id':key,'status':'failed'}]}
        if feedback_mutate: feedback_mutate(feedback)
        history = action_record(proposal, feedback, number, state=self.state)
        if history_mutate: history_mutate(history)
        directory = self.directory/'batch-requests'/f'{number:06d}'
        for name, value in [('proposal',proposal), ('feedback',feedback), ('candidate',prospective)]:
            durable(directory/(name+'.json'), value, immutable=True)
        self.state['batch_history'].append(history)
        if write_draft: self.draft.write_text(source)
        return directory

    def view(self, key='b'):
        return eligibility_view(self.c, key)

    def stop(self, key='b', origin='model'):
        return record_unresolved(self.c, reason='Two changed formal candidates remain unsuccessful',
                                 origin=origin, entry_ids=[key])

    def test_policy_is_explicit_and_requires_contract_isolation(self):
        self.assertFalse(strict_policy_enabled({}))
        self.assertTrue(strict_policy_enabled(self.run))
        for run in ({'unresolved_evidence_policy':POLICY},
                    {**self.run, 'unresolved_evidence_policy':'invented'}):
            with self.assertRaises(ValueError): strict_policy_enabled(run)

    def test_zero_or_one_attempt_cannot_stop_and_sibling_stays_pending(self):
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, 'No disposition was recorded'): self.stop()
            self.assertEqual(current_dispositions(self.c), {})
            self.attempt()
        self.assertFalse(self.view()['eligible'])  # identical retries remain one

    def test_two_revised_failures_bind_auditable_evidence_and_preserve_green(self):
        self.attempt(); self.attempt('by blast')
        before = deepcopy(self.state)
        view = self.view(); self.assertTrue(view['eligible'], view)
        outcome = self.stop()
        self.assertEqual(len(outcome['details']['unresolved_evidence']['attempts']), 2)
        self.assertEqual(outcome['truth_status'], 'undetermined')
        self.assertEqual(set(current_dispositions(self.c)), {'b'})
        self.assertEqual(self.state, before)

    def test_unknown_groups_are_rejected_even_with_evidence(self):
        self.attempt(); self.attempt('by blast')
        for ids in (None, [], ['b','c'], ['b','b']):
            with self.assertRaisesRegex(ValueError, 'exactly one'):
                record_unresolved(self.c, reason='stop', origin='model', entry_ids=ids)

    def test_controller_cannot_bypass_gate(self):
        with self.assertRaisesRegex(ValueError, 'unresolved evidence rejected'): self.stop(origin='controller')
        self.assertEqual(current_dispositions(self.c), {})

    def test_legacy_report_without_evidence_retains_original_behavior(self):
        del self.run['unresolved_evidence_policy']
        result = self.stop()
        self.assertEqual(result['status'], 'unknown')
        self.assertNotIn('unresolved_evidence', result['details'])

    def test_helper_and_already_proved_views_do_not_break_reads(self):
        self.assertEqual(self.view('helper:h')['reason'], 'not_fixed_contract')
        self.assertEqual(self.view('a')['reason'], 'already_proved')

    def test_cached_or_repeated_native_or_cosmetic_candidates_do_not_count(self):
        self.attempt(native_id='one')
        self.attempt('by blast', native_id='one')
        self.attempt('by (* cosmetic *)   fail')
        self.attempt('by auto', feedback_mutate=lambda x:x.update(cached_failure=True))
        view = self.view(); self.assertEqual(view['qualifying_candidates'], 1, view)
        self.assertEqual(view['excluded_attempts']['duplicate_candidate_or_native_request'], 2)

    def test_timeouts_infrastructure_unknown_and_unreached_entries_do_not_count(self):
        changes = [lambda x:x['check'].update(returncode=124, timed_out=True),
                   lambda x:x['check'].update(failure_scope='native_preparation'),
                   lambda x:x['check'].update(verdict='infrastructure_error'),
                   lambda x:x.update(checked_delta_ids=[]),
                   lambda x:x.update(node_results=[{'node_id':'helper:h','status':'failed'},
                                                  {'node_id':'b','status':'not_reached'}]),
                   lambda x:x['check'].update(diagnostic='Worker response lost')]
        for index, change in enumerate(changes): self.attempt(f'by (simp add: fact{index})', feedback_mutate=change)
        self.assertEqual(self.view()['qualifying_candidates'], 0)

    def test_other_target_failure_is_not_evidence_for_this_target(self):
        self.attempt(key='c'); self.attempt('by blast', key='c')
        self.assertEqual(self.view('b')['qualifying_candidates'], 0)
        self.assertTrue(self.view('c')['eligible'])

    def test_native_trace_must_actually_reach_and_fail_at_target(self):
        def wrong_trace(feedback):
            check=feedback['check']; log='REGEN_NODE c\nFailed to apply initial proof method\n'
            check.update(log_content=log,log_sha256=sha(log))
            check['native_receipt']['log_sha256']=sha(log)
        self.attempt(feedback_mutate=wrong_trace)
        self.assertEqual(self.view()['excluded_attempts']['native_failure_not_at_target'],1)

    def test_receipt_and_candidate_tampering_fail_closed(self):
        self.attempt()
        directory = self.attempt('by blast')
        value=json.loads((directory/'candidate.json').read_text());value['proofs']['b']='by auto'
        (directory/'candidate.json').write_text(json.dumps(value))
        self.assertEqual(self.view()['qualifying_candidates'],1)
        value=json.loads((directory/'feedback.json').read_text());value['check']['diagnostic']='tampered'
        (directory/'feedback.json').write_text(json.dumps(value))
        self.assertEqual(self.view()['excluded_attempts']['invalid_or_unavailable_attempt'],1)

    def test_unbound_history_missing_native_receipts_and_hash_mismatch_do_not_count(self):
        self.attempt(history_mutate=lambda x:x.pop('attempt_binding'))
        self.attempt('by blast', feedback_mutate=lambda x:x['check'].pop('native_receipt'))
        self.attempt('by auto', feedback_mutate=lambda x:x['check']['native_receipt'].update(source_sha256='bad'))
        self.attempt('by simp', feedback_mutate=lambda x:x['check']['native_receipt'].update(log_sha256='bad'))
        self.attempt('by force', feedback_mutate=lambda x:x['check']['native_receipt'].update(
            check_scope='conditional-sketch-with-certified-prefix'))
        self.assertEqual(self.view()['qualifying_candidates'],0)

    def test_current_statement_or_owned_context_change_cannot_reuse_old_failures(self):
        self.attempt(); self.attempt('by blast')
        self.package['contracts'][1]['exact_statement']='lemma b: "False"'
        self.state['graph']['nodes'][1]['statement']='lemma b: "False"'
        self.draft.write_text(render(self.package,self.state,['Main']))
        self.assertEqual(self.view()['qualifying_candidates'],0)

    def test_conditionally_failed_sketch_and_uncommitted_success_are_not_formal_failure(self):
        self.attempt(history_mutate=lambda x:x.update(action='check_sketch'))
        self.attempt('by blast',feedback_mutate=lambda x:x.update(status='draft_checked'))
        self.attempt('by auto',feedback_mutate=lambda x:x.update(
            node_results=[{'node_id':'b','status':'completed_uncommitted'},
                          {'node_id':'c','status':'failed'}]))
        self.assertEqual(self.view()['qualifying_candidates'],0)

    def test_context_revision_and_provider_versions_invalidate_evidence(self):
        self.attempt(); self.attempt('by blast')
        self.state['context_revision']=1; self.state['context_imports']=[]
        self.assertFalse(self.view()['eligible'])
        self.state.pop('context_revision'); self.state.pop('context_imports')
        self.state['context_provider_bindings']={'Provider':'new-seal'}
        self.assertFalse(self.view()['eligible'])

    def test_import_change_and_pending_draft_imports_invalidate_evidence(self):
        self.attempt(); self.attempt('by blast')
        self.state['context_imports']=['Provider']
        self.assertFalse(self.view()['eligible'])
        self.state.pop('context_imports')
        self.draft.write_text(self.draft.read_text().replace('"Main"','"Other"'))
        self.assertFalse(self.view()['eligible'])

    def test_prefix_replacement_invalidates_but_later_independent_green_does_not(self):
        self.attempt(); self.attempt('by blast')
        self.state['proofs']['c']='by simp'; self.state['accepted_order'].append('c')
        self.draft.write_text(render(self.package,self.state,['Main']))
        self.assertTrue(self.view()['eligible'], self.view())
        self.state['proofs']['a']='by auto'
        self.draft.write_text(render(self.package,self.state,['Main']))
        self.assertEqual(self.view()['qualifying_candidates'],0)

    def test_reset_failed_target_to_oops_keeps_its_evidence_and_reads_are_readonly(self):
        self.attempt(); self.attempt('by blast')
        self.draft.write_text(render(self.package,self.state,['Main']))
        before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertTrue(self.view()['eligible'],self.view())
        after={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before,after)

    def test_new_available_fact_before_target_requires_fresh_attempts(self):
        self.attempt(); self.attempt('by blast')
        self.state['proofs']['c']='by simp'; self.state['accepted_order'].append('c')
        self.state['graph']['document_order']=['a','c','b']
        self.draft.write_text(render(self.package,self.state,['Main']))
        view=self.view()
        self.assertFalse(view['eligible'])
        self.assertEqual(view['qualifying_candidates'],0)
        self.assertEqual(view['excluded_attempts'].get('stale_prefix'),2)
        self.attempt('by auto'); self.attempt('by fastforce')
        self.assertTrue(self.view()['eligible'], self.view())


class ProductionFailureHistoryTests(unittest.TestCase):
    def test_real_document_controller_history_supports_baseline_and_sketch_reviewed(self):
        from regen.tests.current.test_theory_document import DocumentControllerTests
        from regen.execution.native_check_receipt import native_check_receipt
        from regen.planning.parallel_file_planner import generation_strategy
        for strategy in ('baseline','sketch-reviewed-v1'):
            with self.subTest(strategy=strategy):
                base=DocumentControllerTests();base.setUp()
                try:
                    c=base.controller
                    # This fixture's existing journals remain legacy; exercise
                    # the native history writer first, then its strict reader.
                    c.generation_strategy=generation_strategy(strategy)
                    first=base.read(); checker=base.checkers['Target']
                    def failed(source,label,timeout=None):
                        log='REGEN_NODE target_contract\nFailed to apply initial proof method\n'
                        result={'verdict':'red','returncode':1,'diagnostic':'Failed to apply initial proof method',
                            'source_content':source,'source_sha256':sha(source),
                            'log_content':log,'log_sha256':sha(log)}
                        result['native_receipt']=native_check_receipt(checker,result,source=source,
                            scope='cold-full-theory',log_text=log)
                        return result
                    with patch.object(checker,'_check',side_effect=failed):
                        for proof in ('by fail','by blast'):
                            entry=base.call({'action':'read_entry','entry_id':'target_contract'})
                            saved=base.call({'action':'edit_proof','entry_id':'target_contract',
                                            'expected_hash':entry['expected_hash'],'proof_body':proof})
                            checked=base.call({'action':'check_theory','source_sha256':saved['source_sha256']})
                            self.assertEqual(checked['feedback']['status'],'rejected_batch',checked)
                    c.run={**c.run,'contract_outcome_policy':'contract-dispositions-v1',
                           'unresolved_evidence_policy':POLICY}
                    view=eligibility_view(c,'target_contract')
                    self.assertTrue(view['eligible'],view)
                    self.assertEqual(view['qualifying_candidates'],2)
                    self.assertEqual(c._state('Target')[1]['proofs'],{})
                finally:base.doCleanups()


if __name__ == '__main__':
    unittest.main()
