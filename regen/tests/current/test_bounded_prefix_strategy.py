"""Synthetic checker tests; no native workers, providers or historical proofs."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import ProofCommits
from regen.execution.repository_execution import validator
from regen.planning.bounded_prefix import STRATEGY, one_declaration, run_prefix
from regen.planning.theory_document import DocumentStore, block, candidate, header, render
from regen.tests.current.test_proof_batches import Checker
from regen.tests.current.test_proof_commits import fixture, check


class SequenceChecker(Checker):
    def __init__(self, verdicts):
        self.verdicts = iter(verdicts)
        self.calls = 0

    def _check(self, source, label, *, timeout=None):
        self.verdict = next(self.verdicts)
        return super()._check(source, label, timeout=timeout)


class BoundedPrefixStrategyTests(unittest.TestCase):
    def source(self, package, old):
        return render(package, old, Checker().imports()).replace('oops', 'by simp')

    def journal(self, root, package, old):
        journal = ProofCommits(root/'acceptance', {'run':'bounded-test'}, validator(package))
        journal.append('initial', old, {'check':check()})
        return journal

    def execute(self, package, old, source, checker, root, journal, *, commit=None):
        return run_prefix(package, old, source, checker, root/'checks',
            evidence_origin={'sealable_generation':True, 'transcript_prefix':'synthetic-test-only'},
            commit=commit or (lambda new, evidence:journal.append('document', new, evidence)))

    def test_independent_green_commits_and_contract_stop_preserve_full_draft(self):
        package, old = fixture()
        old['batch_requests'] = 7
        old['rounds'] = {'helper':3}
        source = self.source(package, old)
        later = {'id':'later', 'role':'helper', 'context':[], 'statement':'lemma later: "True"'}
        source = source[:-4]+block(later, 'by simp')+'end\n'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = DocumentStore(root/'document', package, old, Checker().imports())
            saved = store.edit({'action':'edit_theory', 'base_sha256':store.read()['source_sha256'],
                                'source':source}, {'origin':'synthetic-model'})
            journal = self.journal(root, package, old)
            checker = SequenceChecker(['green', 'green'])
            result = self.execute(package, old, source, checker, root, journal)
            new = journal.replay()
            self.assertEqual(new['accepted_order'], ['helper', 'contract'])
            self.assertNotIn('later', new['proofs'])
            self.assertEqual(new['batch_requests'], 7)
            self.assertEqual(new['rounds'], {'helper':3})
            self.assertEqual(result['bounded_prefix']['stop_reason'], 'first_accepted_contract')
            self.assertEqual(result['bounded_prefix']['pending_ids'], ['later'])
            self.assertEqual(checker.calls, 2)
            self.assertEqual(DocumentStore(root/'document', package, new, checker.imports()).read()['source'], source)
            self.assertEqual(store.read()['source_sha256'], saved['source_sha256'])
            self.assertEqual(len(list((root/'acceptance/commits').glob('*.json'))), 3)
            self.assertTrue((root/'checks/000001/check.json').is_file())
            self.assertTrue((root/'checks/000002/check.json').is_file())

    def test_first_red_retains_prior_green_and_original_obligations(self):
        package, old = fixture()
        source = self.source(package, old)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal = self.journal(root, package, old)
            checker = SequenceChecker(['green','red'])
            feedback = self.execute(package, old, source, checker, root, journal)
            new = journal.replay()
            self.assertEqual(new['accepted_order'], ['helper'])
            self.assertEqual({n['id'] for n in new['graph']['nodes']}, {'helper','contract'})
            self.assertEqual(feedback['status'], 'rejected_batch')
            self.assertEqual(feedback['bounded_prefix']['stop_reason'], 'first_red')
            self.assertEqual(feedback['bounded_prefix']['pending_ids'], ['contract'])
            self.assertEqual(checker.calls, 2)

    def test_original_unproved_helpers_not_deleted_when_absent_from_new_draft(self):
        package, old = fixture()
        # The model proposes a new helper and leaves its contract open. The
        # pre-existing helper remains an obligation in every accepted segment.
        fresh = {'id':'fresh', 'role':'helper', 'context':[], 'statement':'lemma fresh: "True"'}
        contract = next(n for n in old['graph']['nodes'] if n['role']=='contract')
        source = header(package['theory'], Checker().imports())+block(fresh,'by simp')+block(contract)+'end\n'
        proposed = candidate(package, old, source, Checker().imports())
        new, _ = one_declaration(package, old, proposed, 'fresh', Checker().imports())
        self.assertEqual({n['id'] for n in new['graph']['nodes']}, {'helper','fresh','contract'})
        self.assertEqual(new['proofs'], {'fresh':'by simp'})

    def test_interruption_after_first_commit_keeps_full_draft_and_resumes_remaining(self):
        package, old = fixture()
        source = self.source(package, old)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = DocumentStore(root/'document', package, old, Checker().imports())
            store.edit({'action':'edit_theory', 'base_sha256':store.read()['source_sha256'], 'source':source}, {})
            journal = self.journal(root, package, old)
            def interrupted_commit(new, evidence):
                journal.append('document', new, evidence)
                raise KeyboardInterrupt('simulated interruption after durable GREEN')
            with self.assertRaises(KeyboardInterrupt):
                self.execute(package, old, source, Checker(), root, journal, commit=interrupted_commit)
            restored = journal.replay()
            self.assertEqual(restored['accepted_order'], ['helper'])
            self.assertEqual(store.read()['source'], source)
            result = run_prefix(package, restored, store.read()['source'], Checker(), root/'resumed',
                evidence_origin={'sealable_generation':True},
                commit=lambda new, evidence:journal.append('document', new, evidence))
            self.assertEqual(result['new_green'], ['contract'])
            self.assertEqual(journal.replay()['accepted_order'], ['helper','contract'])

    def test_nonappend_rewrite_falls_back_without_any_check_or_commit(self):
        package, old = fixture()
        source = self.source(package, old)
        accepted = candidate(package, old, source, Checker().imports())
        rewritten = source.replace('by simp','by blast',1)
        with tempfile.TemporaryDirectory() as tmp:
            checker = Checker()
            result = run_prefix(package, accepted, rewritten, checker, Path(tmp),
                evidence_origin={}, commit=lambda *args:self.fail('unexpected commit'))
            self.assertIsNone(result)
            self.assertEqual(checker.calls, 0)
            from regen.infrastructure.incremental_log import read_artifact
            policy = read_artifact(Path(tmp)/'policy.json')
            self.assertEqual(policy['reason'], 'non_append_rewrite')

    def test_strategy_is_explicit_and_not_baseline_prompt_only(self):
        from regen.planning.parallel_file_planner import generation_strategy
        self.assertEqual(generation_strategy(STRATEGY)['name'], STRATEGY)
        self.assertEqual(generation_strategy('baseline')['instructions'], '')

    def test_online_controller_keeps_original_rpc_and_charges_one_model_request(self):
        from regen.tests.current.test_proof_workspace_agent import ProofWorkspaceAgentTest
        from regen.execution.check_executor import CheckExecutor, NativeResources
        from regen.planning.parallel_file_planner import TheoryController
        from regen.infrastructure.incremental_log import read_artifact
        for strategy in ('baseline', STRATEGY):
            with self.subTest(strategy=strategy):
                fixture_owner = ProofWorkspaceAgentTest()
                fixture_owner.setUp()
                self.addCleanup(fixture_owner.doCleanups)
                resources = NativeResources(1,1,1)
                executor = CheckExecutor(fixture_owner.root/'tasks', workers=1)
                self.addCleanup(resources.close)
                self.addCleanup(executor.close)
                controller = TheoryController(fixture_owner.root, 'Target', resources=resources,
                    executor=executor, document_mode=True, generation_strategy_name=strategy)
                self.addCleanup(controller.close)
                checkers, _ = fixture_owner.local_runtime(controller)
                draft = controller.handle('read_theory', {'theory':'Target'}, {})['document']
                package, old = controller._state('Target')
                helper = {'id':'fresh', 'role':'helper', 'context':[], 'statement':'lemma fresh: "True"'}
                later = {'id':'later', 'role':'helper', 'context':[], 'statement':'lemma later: "True"'}
                contract = next(n for n in old['graph']['nodes'] if n['role']=='contract')
                source = header('Target',checkers['Target'].imports())+block(helper,'by simp')+block(
                    contract,'by simp')+block(later,'by simp')+'end\n'
                metadata = {'thread_id':'test-thread','turn_id':'test-turn',
                            'call_id':'edit','transcript_path':'synthetic-only'}
                saved = controller.handle('proof_action', {'theory':'Target','proposal':{
                    'action':'edit_theory','base_sha256':draft['source_sha256'],'source':source}}, metadata)
                original = {'action':'check_theory','source_sha256':saved['source_sha256'], 'reason':'model proposal'}
                result = controller.handle('proof_action', {'theory':'Target','proposal':original},
                    {**metadata,'call_id':'check'})
                self.assertEqual(result['feedback']['status'],
                                 'accepted_prefix' if strategy==STRATEGY else 'accepted',result)
                self.assertEqual(result['request_number'],1)
                self.assertEqual(fixture_owner.record.call_count,2)  # Model edit and original check only.
                self.assertEqual(fixture_owner.record.call_args.args[2],original)
                self.assertEqual(controller.handle('read_theory',{'theory':'Target'}, {})['document']['source'], source)
                request = fixture_owner.root/'theories/Target/batch-requests/000001'
                self.assertEqual(read_artifact(request/'model-proposal.json'),original)
                state = controller._state('Target')[1]
                if strategy == STRATEGY:
                    self.assertIsNotNone(state['batch_candidate'])
                    self.assertEqual(state['accepted_order'],['fresh','target_contract'])
                    evidence = read_artifact(request/'bounded-prefix/000001/check.json')
                    self.assertEqual(evidence['parent_request_number'],1)
                    self.assertEqual(evidence['bounded_prefix']['original_source_sha256'],saved['source_sha256'])
                    self.assertEqual(checkers['Target'].calls,2)
                else:
                    self.assertEqual(state['accepted_order'],['fresh','target_contract','later'])
                    self.assertFalse((request/'bounded-prefix').exists())
                    self.assertEqual(checkers['Target'].calls,1)
                fixture_owner.llm.assert_not_called()

    def test_online_partial_green_survives_runtime_native_and_controller_cancel(self):
        from regen.tests.current.test_proof_workspace_agent import ProofWorkspaceAgentTest
        from regen.execution.check_executor import CheckExecutor, NativeResources
        from regen.execution.check_tasks import NativePreparationError
        from regen.planning.parallel_file_planner import TheoryController
        for mode in ('runtime', 'native', 'cancel'):
            with self.subTest(mode=mode):
                owner = ProofWorkspaceAgentTest()
                owner.setUp()
                self.addCleanup(owner.doCleanups)
                resources = NativeResources(1,1,1)
                executor = CheckExecutor(owner.root/'tasks', workers=1)
                self.addCleanup(resources.close)
                self.addCleanup(executor.close)
                controller = TheoryController(owner.root, 'Target', resources=resources,
                    executor=executor, document_mode=True, generation_strategy_name=STRATEGY,
                    focus_contract_id='target_contract')
                self.addCleanup(controller.close)
                checkers, _ = owner.local_runtime(controller)
                draft = controller.handle('read_theory', {'theory':'Target'}, {})['document']
                _, old = controller._state('Target')
                helper = {'id':'fresh', 'role':'helper', 'context':[], 'statement':'lemma fresh: "True"'}
                contract = next(n for n in old['graph']['nodes'] if n['role']=='contract')
                source = header('Target',checkers['Target'].imports())+block(helper,'by simp')+block(contract,'by simp')+'end\n'
                metadata = {'thread_id':'t','turn_id':'u','call_id':'edit','transcript_path':'synthetic'}
                saved = controller.handle('proof_action', {'theory':'Target','proposal':{
                    'action':'edit_theory', 'base_sha256':draft['source_sha256'], 'source':source}}, metadata)
                checker = checkers['Target']
                original_check = checker._check
                def fail_second(source, label, *, timeout=None):
                    if checker.calls == 1:
                        if mode == 'runtime':
                            raise RuntimeError('second prefix tooling failed')
                        if mode == 'native':
                            raise NativePreparationError('second prefix startup failed')
                        controller.abort_pending()
                    return original_check(source, label, timeout=timeout)
                with patch.object(checker, '_check', side_effect=fail_second):
                    if mode == 'native':
                        with self.assertRaises(NativePreparationError):
                            controller.handle('proof_action', {'theory':'Target','proposal':{
                                'action':'check_theory','source_sha256':saved['source_sha256']}},
                                {**metadata,'call_id':'check'})
                    else:
                        result = controller.handle('proof_action', {'theory':'Target','proposal':{
                            'action':'check_theory','source_sha256':saved['source_sha256']}},
                            {**metadata,'call_id':'check'})
                        if mode == 'runtime':
                            self.assertEqual(result['feedback']['status'],'tool_failed')
                            self.assertEqual(result['feedback']['bounded_prefix']['accepted_ids'],['fresh'])
                            self.assertEqual(result['feedback']['bounded_prefix']['pending_ids'],['target_contract'])
                            self.assertNotIn('No graph or proof was accepted',result['feedback']['notice'])
                        else:
                            self.assertEqual(result['status'],'cancelled')
                state = controller._state('Target')[1]
                self.assertEqual(state['accepted_order'],['fresh'])
                self.assertEqual(state['batch_requests'],1)
                self.assertEqual(controller._document()[0].read()['source'],source)
                self.assertEqual(state['batch_candidate']['document_source'],source)
                owner.llm.assert_not_called()

    def test_focus_rejects_unknown_contract_without_model_or_native(self):
        from regen.tests.current.test_proof_workspace_agent import ProofWorkspaceAgentTest
        from regen.execution.check_executor import CheckExecutor, NativeResources
        from regen.planning.parallel_file_planner import TheoryController
        owner = ProofWorkspaceAgentTest()
        owner.setUp()
        self.addCleanup(owner.doCleanups)
        resources = NativeResources(1,1,1)
        executor = CheckExecutor(owner.root/'tasks',workers=1)
        self.addCleanup(resources.close)
        self.addCleanup(executor.close)
        with self.assertRaisesRegex(ValueError,'unproved fixed contract'):
            TheoryController(owner.root,'Target',resources=resources,executor=executor,
                             document_mode=True,focus_contract_id='foreign-contract')


if __name__=='__main__':
    unittest.main()
