from copy import deepcopy
import hashlib
import unittest
from regen.execution.proof_batches import prepare_batch, check_batch, validate_batch_commit
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.tests.current.test_proof_commits import fixture


class Checker:
    timeout = 1800
    imports = lambda self: ['Semantic_Base.Semantic_Root']
    verdict = 'green'
    calls = 0
    def _check(self, source, label):
        self.calls += 1
        return {'verdict':self.verdict, 'returncode':0 if self.verdict == 'green' else 1,
                'source_content':source, 'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
                'log_content':'', 'log_sha256':hashlib.sha256(b'').hexdigest()}
    def check_graph(self, graph):
        renderer = object.__new__(IsabelleChecker)
        renderer.imports = self.imports
        return self._check(renderer._source(graph, {}, theory_name=f"Graph_Check_v{graph['revision']}", statements_only=True), 'graph')


class ProofBatchesTest(unittest.TestCase):
    def test_batch_dependency_and_single_native_proof_check(self):
        package, old = fixture()
        candidate = prepare_batch(package, old, {'graph':{'contract_dependencies':{'contract':['helper']}}, 'proofs':{'contract':'using helper by simp', 'helper':'by simp'}})
        self.assertEqual(candidate['accepted_order'], ['helper','contract'])
        self.assertEqual(old['proofs'], {})
        checker=Checker(); evidence=check_batch(checker,old,candidate)
        self.assertEqual(checker.calls,1)
        self.assertNotIn('oops',evidence['check']['source_content'])
        validate_batch_commit(old,candidate,evidence)
        bad=deepcopy(evidence);bad['check']['source_content'] += '\n'
        with self.assertRaises(ValueError):validate_batch_commit(old,candidate,bad)
        bad=deepcopy(candidate);bad['proofs']['contract']='by blast'
        with self.assertRaises(ValueError):validate_batch_commit(old,bad,evidence)

    def test_failed_batch_never_accepts(self):
        package,old=fixture(); candidate=prepare_batch(package,old,{'proofs':{'helper':'by simp'}})
        checker=Checker();checker.verdict='red'; evidence=check_batch(checker,old,candidate)
        with self.assertRaises(ValueError):validate_batch_commit(old,candidate,evidence)
        self.assertEqual(old['proofs'],{})

    def test_graph_patch_delete_and_fixed_contract_protection(self):
        package,old=fixture()
        candidate=prepare_batch(package,old,{'graph':{'delete_node_ids':['helper']},'reason':'unused'})
        evidence=check_batch(Checker(),old,candidate);validate_batch_commit(old,candidate,evidence)
        self.assertEqual(len(candidate['graph']['nodes']),1)
        with self.assertRaises(ValueError):prepare_batch(package,old,{'graph':{'delete_node_ids':['contract']},'reason':'remove'})
        with self.assertRaises(ValueError):prepare_batch(package,old,{'graph':{'delete_node_ids':['helper']}})

    def test_green_immutable_and_unproved_prerequisite(self):
        package,old=fixture()
        with self.assertRaises(ValueError):prepare_batch(package,old,{'graph':{'contract_dependencies':{'contract':['helper']}},'proofs':{'contract':'by simp'}})
        green=prepare_batch(package,old,{'proofs':{'helper':'by simp'}})
        with self.assertRaises(ValueError):prepare_batch(package,green,{'graph':{'delete_node_ids':['helper']},'reason':'remove'})
        with self.assertRaises(ValueError):prepare_batch(package,green,{'proofs':{'helper':'by simp'}})

    def test_malformed_field_types_are_validation_errors(self):
        package,old=fixture()
        for proposal in [None, {'graph':[]}, {'reason':1}, {'graph':{'delete_node_ids':1}},
                         {'graph':{'generated_nodes':[1]}}, {'graph':{'contract_dependencies':[]}}]:
            with self.subTest(proposal=proposal), self.assertRaises(ValueError):
                prepare_batch(package,old,proposal)

    def test_injection_and_missing_graph_evidence(self):
        package,old=fixture()
        for proof in ['sorry','by simp\nML \\<open>bad\\<close>', 'by simp\nlemma extra: "True" by simp', 'by (tactic \\<open>bad\\<close>)']:
            with self.assertRaises(ValueError):prepare_batch(package,old,{'proofs':{'helper':proof}})
        candidate=prepare_batch(package,old,{'graph':{'delete_node_ids':['helper']},'reason':'unused'})
        evidence=check_batch(Checker(),old,candidate);evidence['graph_check']=None
        with self.assertRaises(ValueError):validate_batch_commit(old,candidate,evidence)

if __name__ == '__main__':unittest.main()
