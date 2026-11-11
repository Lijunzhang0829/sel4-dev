from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.planning.independent_document_checks import run_checks
from regen.planning.theory_document import render, candidate, block
from regen.tests.current.test_proof_commits import fixture
from regen.tests.current.test_proof_batches import Checker
from regen.execution.repository_execution import validator


class IndependentChecksTests(unittest.TestCase):
    def run_case(self, first_red=False, cancel_second=False):
        package, old = fixture()
        checker = Checker()
        source = render(package, old, checker.imports()).replace('oops', 'by simp')
        commits, sources = [], []
        from regen.planning.theory_document import check_document as original
        def check(checker, current, new, isolated):
            sources.append(isolated)
            if cancel_second and len(sources) == 2:
                raise RuntimeError('interrupted')
            checker.verdict = 'red' if first_red and len(sources) == 1 else 'green'
            return original(checker,current,new,isolated)
        def commit(new, evidence):
            prior = commits[-1] if commits else old
            validator(package)(prior,new,'document',evidence)
            commits.append(deepcopy(new))
            return new
        with tempfile.TemporaryDirectory() as temp, patch(
                'regen.planning.independent_document_checks.check_document', side_effect=check):
            if cancel_second:
                with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                    run_checks(package,old,source,checker,temp,commit=commit,
                               evidence_origin={'sealable_generation':True})
                result = None
            else:
                result = run_checks(package,old,source,checker,temp,commit=commit,
                                    evidence_origin={'sealable_generation':True})
        return old, commits, sources, result

    def test_red_does_not_skip_later_proof_or_admit_failed_helper(self):
        old, commits, sources, result = self.run_case(first_red=True)
        self.assertEqual(result['new_green'], ['contract'])
        self.assertEqual([x['verdict'] for x in result['node_results']], ['red','green'])
        self.assertIn('oops', sources[1])
        self.assertNotIn('sorry', sources[1])
        self.assertEqual(commits[-1]['proofs'], {'contract':'by simp'})
        self.assertEqual(old['proofs'], {})

    def test_each_green_has_valid_commit_and_next_can_use_it(self):
        _, commits, sources, result = self.run_case()
        self.assertEqual(len(commits), 2)
        self.assertEqual(result['new_green'], ['helper','contract'])
        self.assertIn('lemma helper', sources[1])
        self.assertEqual(commits[-1]['accepted_order'], ['helper','contract'])

    def test_interruption_retains_first_validated_commit(self):
        _, commits, _, _ = self.run_case(cancel_second=True)
        self.assertEqual(len(commits), 1)
        self.assertEqual(list(commits[0]['proofs']), ['helper'])


class SwitchTargetTests(unittest.TestCase):
    def setUp(self):
        from regen.tests.current.test_theory_document import DocumentControllerTests
        self.base = DocumentControllerTests();self.base.setUp();self.addCleanup(self.base.doCleanups)

    def test_contract_switch_preserves_draft_and_acceptance(self):
        before = self.base.read()['document']
        state = deepcopy(self.base.controller._state('Target')[1])
        result = self.base.call({'action':'switch_target','entry_id':'target_contract',
                                 'reason':'Try an independent pending obligation','notes':'Keep earlier route pending'})
        self.assertEqual(result['status'],'target_switched')
        self.assertEqual(self.base.read()['document']['source'], before['source'])
        self.assertEqual(self.base.controller._state('Target')[1]['proofs'],state['proofs'])
        self.assertTrue((self.base.fixture.root/'theories/Target/document/active-target.json').exists())

    def test_invalid_switch_does_not_dispose_or_change_proofs(self):
        for proposal in [dict(entry_id='missing'),dict(theory='Target'),
                         dict(theory='unselected'),dict(entry_id='target_contract',theory='Target')]:
            with self.subTest(proposal=proposal),self.assertRaises(ValueError):
                self.base.call({'action':'switch_target','reason':'retain all work',**proposal})
        self.assertEqual(self.base.controller._state('Target')[1]['proofs'],{})

class IndependentControllerTests(unittest.TestCase):
    def test_one_request_returns_all_results_and_replays_individual_acceptance(self):
        from regen.tests.current.test_theory_document import DocumentControllerTests
        from regen.planning.theory_document import block
        base=DocumentControllerTests();base.setUp();self.addCleanup(base.doCleanups)
        draft=base.read()['document']
        state=base.controller._state('Target')[1]
        helper={'id':'bad_helper','name':'bad_helper','role':'helper',
                'statement':'lemma bad_helper: "False"','context':[],'dependencies':[],'strategy':''}
        source=draft['source'].replace('\nend\n',block(helper,'by fail')+'\nend\n')
        source=source.replace('oops','by simp')
        saved=base.call({'action':'edit_theory','base_sha256':draft['source_sha256'],'source':source})
        checker=base.checkers['Target']
        original=checker._check
        def check(source,label,**kwargs):
            checker.verdict='red' if 'by fail' in source else 'green'
            return original(source,label,**kwargs)
        with patch.object(checker,'_check',side_effect=check):
            result=base.call({'action':'check_theory','source_sha256':saved['source_sha256']})
        feedback=result['feedback']
        self.assertEqual(feedback['new_green'],['target_contract'])
        self.assertEqual([x['verdict'] for x in feedback['entry_results']],['green','red'])
        self.assertEqual(base.read()['document']['source'],source)
        self.assertEqual(list(base.controller._state('Target')[1]['proofs']),['target_contract'])

class YieldSchedulingTests(unittest.TestCase):
    def test_serial_yield_starts_requested_ready_theory_then_resumes_original(self):
        import hashlib
        import threading
        from regen.planning.parallel_file_planner import run_parallel_workspace
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); binary=root/'codex';binary.write_text('fixture')
            states={name:'pending' for name in ['A','B','C']}; started=[]
            class Overview:
                selected=list(states)
                run={'run_id':'yield-test','execution_input_kind':'refine-dataset',
                     'checker_context_root':str(root),'checker_context_id':'fixture'}
                options={'model':'test','call_timeout':10,'check_timeout':10,'candidate_timeout':10}
                def __init__(self,*args,**kwargs):self.root=root
                def list_workspace(self):
                    return {'theories':[{'theory':n,'status':s,'selected':True,'contracts':[n],
                                         'ready_for_proof_action':s=='pending'} for n,s in states.items()]}
            def session(root,theory,**kwargs):
                started.append(theory)
                if theory=='A' and started.count('A')==1:
                    return {'theory':'A','status':'yielded','switch':{'from_theory':'A','to_theory':'C'}}
                states[theory]='sealed'
                return {'theory':theory,'status':'sealed'}
            settings={'codex_bin':str(binary),'codex_runtime_root':str(root/'runtime'),
                      'codex_sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}
            with patch('regen.planning.parallel_file_planner.WorkspaceController',Overview),patch(
                    'regen.planning.parallel_file_planner.run_theory_session',side_effect=session),patch(
                    'regen.infrastructure.audited_llm.model_settings',return_value=settings),patch(
                    'regen.preparation.checker_context.load_context',return_value={'checker_context_id':'fixture'}):
                result=run_parallel_workspace(root, budget_mode="effective-time",allow_external_call=True,integrate=False,
                                              max_parallel_theories=1,resident_limit=1)
            self.assertEqual(started,['A','C','B','A'])
            self.assertEqual(result['status'],'selected_theories_sealed')

class SwitchPersistenceTests(unittest.TestCase):
    def test_theory_switch_latches_tools_and_retains_the_source_receipt(self):
        from regen.tests.current.test_theory_document import DocumentControllerTests
        from regen.execution.proof_commits import durable
        base=DocumentControllerTests();base.setUp();self.addCleanup(base.doCleanups)
        controller=base.controller
        base.read()
        original=controller._state
        controller.switchable_theories=['Target','Other']
        def state(theory):
            if theory=='Other':return {},{'status':'generating','proofs':{}}
            return original(theory)
        with patch.object(controller,'_state',side_effect=state),patch.object(
                controller,'_effective_providers',return_value=[]):
            result=base.call({'action':'switch_target','theory':'Other','reason':'Continue independent ready work'})
        self.assertTrue(result['theory_yield'])
        self.assertEqual(result['switch']['to_theory'],'Other')
        later=base.call({'action':'check_theory','source_sha256':'stale'})
        self.assertTrue(later['theory_yield'])
        self.assertEqual(len(list((base.fixture.root/'theory-switches').glob('*.json'))),1)
        self.assertEqual(controller._state('Target')[1]['proofs'],{})

    def test_local_switch_reuses_live_native_runtime(self):
        from regen.tests.current.test_theory_document import DocumentControllerTests
        base=DocumentControllerTests();base.setUp();self.addCleanup(base.doCleanups)
        base.read();checker=base.checkers['Target']
        runtime=base.controller._runtime()
        base.call({'action':'switch_target','entry_id':'target_contract','reason':'Change local focus'})
        self.assertIs(base.controller._runtime(),runtime)
        checker.close_prefix.assert_not_called()
