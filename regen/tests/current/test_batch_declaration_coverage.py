"""Proof batches may certify declarations; independent unproved edits still need shells."""
from copy import deepcopy
import unittest

from regen.execution.proof_batches import prepare_batch, check_batch, validate_batch_commit, _binding
from regen.execution.proof_commits import digest
from regen.execution.repository_execution import (
    continuation_state, continuation_declaration_coverage, validator,
)
from regen.tests.current import test_continuation
from regen.tests.current.test_proof_batches import Checker
from regen.tests.current.test_proof_commits import fixture, check


class BatchDeclarationCoverageTest(unittest.TestCase):
    def proposal(self, prove=True):
        return {'graph':{'generated_nodes':[{'id':'fresh','name':'fresh','role':'helper',
            'statement':'lemma fresh: "(0::nat) <= 1"','dependencies':[], 'context':[]}]},
            'proofs':{'fresh':'by simp'} if prove else {'contract':'by simp'}}

    def test_new_proved_declaration_uses_one_native_check(self):
        package,old=fixture();candidate=prepare_batch(package,old,self.proposal())
        checker=Checker();evidence=check_batch(checker,old,candidate)
        self.assertEqual(checker.calls,1)
        self.assertIsNone(evidence['graph_check'])
        self.assertEqual(evidence['declaration_coverage']['changed_ids'],['fresh'])
        self.assertNotIn('oops',evidence['check']['source_content'])
        validate_batch_commit(old,candidate,evidence)
        validator(package)(old,candidate,'batch',{**evidence,'sealable_generation':True})

    def test_changed_existing_unproved_declaration_covered_by_its_proof(self):
        package,old=fixture();proposal=self.proposal()
        proposal['graph']['generated_nodes'][0].update(id='helper',name='helper',statement='lemma helper: "(0::nat) <= 1"')
        proposal['proofs']={'helper':'by simp'}
        candidate=prepare_batch(package,old,proposal);checker=Checker()
        evidence=check_batch(checker,old,candidate)
        self.assertEqual(checker.calls,1)
        self.assertEqual(evidence['declaration_coverage']['changed_ids'],['helper'])
        validate_batch_commit(old,candidate,evidence)

    def test_changed_unproved_declaration_keeps_separate_native_check(self):
        package,old=fixture();candidate=prepare_batch(package,old,self.proposal(False))
        checker=Checker();evidence=check_batch(checker,old,candidate)
        self.assertEqual(checker.calls,2)
        self.assertNotIn('declaration_coverage',evidence)
        self.assertIn('lemma fresh:',evidence['graph_check']['source_content'])
        validate_batch_commit(old,candidate,evidence)

    def test_red_does_not_establish_declaration_or_green(self):
        package,old=fixture();candidate=prepare_batch(package,old,self.proposal())
        checker=Checker();checker.verdict='red';evidence=check_batch(checker,old,candidate)
        self.assertEqual(checker.calls,1)
        with self.assertRaises(ValueError):validate_batch_commit(old,candidate,evidence)
        self.assertEqual(old['proofs'],{})
        self.assertNotIn('fresh',{n['id'] for n in old['graph']['nodes']})

    def test_tampered_coverage_or_current_declaration_is_rejected(self):
        package,old=fixture();candidate=prepare_batch(package,old,self.proposal())
        evidence=check_batch(Checker(),old,candidate)
        tampered=deepcopy(evidence);tampered['declaration_coverage']['changed_ids']=[]
        with self.assertRaisesRegex(ValueError,'coverage'):validate_batch_commit(old,candidate,tampered)
        bad=deepcopy(candidate)
        next(n for n in bad['graph']['nodes'] if n['id']=='helper')['statement']='lemma helper: "False"'
        tampered=deepcopy(evidence);tampered['new_binding']=_binding(bad)
        with self.assertRaisesRegex(ValueError,'coverage'):validate_batch_commit(old,bad,tampered)
        tampered=deepcopy(evidence);tampered.pop('declaration_coverage')
        with self.assertRaisesRegex(ValueError,'lack native'):validate_batch_commit(old,candidate,tampered)

    def test_legacy_separate_declaration_evidence_still_replays(self):
        package,old=fixture();candidate=prepare_batch(package,old,self.proposal())
        checker=Checker();evidence=check_batch(checker,old,candidate)
        evidence.pop('declaration_coverage');evidence['graph_check']=checker.check_graph(candidate['graph'])
        validate_batch_commit(old,candidate,evidence)

    def test_continuation_combines_verified_origin_declaration_evidence(self):
        package,origin=test_continuation.ContinuationTest().origin();old=origin['commits'][-1]['state']
        candidate=prepare_batch(package,old,self.proposal())
        evidence=check_batch(Checker(),old,candidate);evidence['sealable_generation']=True
        previous=origin['commits'][-1]
        body={'sequence':len(origin['commits'])+1,'parent':previous['commit_id'],
              'binding':previous['binding'],'event':'batch','state':candidate,'evidence':evidence}
        origin['commits'].append({**body,'commit_id':digest(body)})
        state=continuation_state(package,origin)
        initial={'check':check(),'continuation':origin,
                 'declaration_coverage':continuation_declaration_coverage(origin,state)}
        self.assertNotIn('lemma fresh:',initial['check']['source_content'])
        validator(package,origin['run'])(None,state,'initial',initial)
        tampered=deepcopy(initial);tampered['declaration_coverage']['source_commit_id']='wrong'
        with self.assertRaisesRegex(ValueError,'coverage'):
            validator(package,origin['run'])(None,state,'initial',tampered)
        tampered=deepcopy(initial);tampered['continuation']['commits'][-1]['evidence']['check']['verdict']='red'
        with self.assertRaises(ValueError):validator(package,origin['run'])(None,state,'initial',tampered)
        missing=deepcopy(initial);missing.pop('declaration_coverage')
        with self.assertRaisesRegex(ValueError,'does not cover'):
            validator(package,origin['run'])(None,state,'initial',missing)

    def test_real_fork_journal_after_batch_only_declaration_certificate(self):
        from regen.tests.current.test_continuation_providers import ContinuationProvidersTest
        from regen.execution import repository_execution as execution
        import json
        harness=ContinuationProvidersTest();harness.setUp()
        try:
            package=harness.packages['Target']
            journal=execution.journal(harness.source,harness.run,package)
            old=journal.replay();candidate=prepare_batch(package,old,self.proposal())
            evidence=check_batch(Checker(),old,candidate);evidence['sealable_generation']=True
            journal.append('batch',candidate,evidence)
            destination,run=harness.fork()
            restored=execution.journal(destination,run,package).replay()
            self.assertEqual(restored['proofs'],candidate['proofs'])
            self.assertEqual(restored['graph'],candidate['graph'])
            initial=json.loads((destination/'theories/Target/acceptance/commits/000001.json').read_text())
            self.assertEqual(initial['evidence']['declaration_coverage']['scheme'],
                             'validated-continuation-declarations-v1')
        finally:
            harness.doCleanups()

if __name__=='__main__':unittest.main()
