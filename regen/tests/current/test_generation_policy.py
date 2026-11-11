"""Policy consolidation must preserve frozen runs and native authority gates."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.planning.generation_policy import (
    CURRENT_STRATEGIES, DEFAULT_STRATEGY, POLICIES,
    generation_strategy, policy_for, resolve_strategy,
)
from regen.planning.theory_document import DocumentStore
from regen.tests.current import test_sketch_workflow as fixtures


class GenerationPolicyTests(unittest.TestCase):
    def test_all_nine_frozen_prompt_hashes_and_capabilities_are_preserved(self):
        expected=json.loads((Path(__file__).parent/'fixtures/generation-policy-bindings.json').read_text())
        self.assertEqual(set(POLICIES)-{'sketch-reviewed-v3'},set(expected))
        for name,record in expected.items():
            with self.subTest(name=name):
                self.assertEqual(generation_strategy(name)['instructions_sha256'],record['instructions_sha256'])
                self.assertEqual(policy_for(name).store_options(),record['capabilities'])

    def test_new_default_and_frozen_resume_are_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            self.assertEqual(CURRENT_STRATEGIES,('baseline','sketch-reviewed-v3'))
            self.assertEqual(resolve_strategy(root),DEFAULT_STRATEGY)
            old=generation_strategy('sketch-reviewed-v1')
            (root/'parallel-workspace-policy.json').write_text(json.dumps({'generation_strategy':old}))
            self.assertEqual(resolve_strategy(root),'sketch-reviewed-v1')
            self.assertEqual(resolve_strategy(root,'sketch-reviewed-v1'),'sketch-reviewed-v1')
            with self.assertRaisesRegex(ValueError,'frozen'):
                resolve_strategy(root,'sketch-reviewed-v2')

    def test_frozen_prompt_tampering_is_not_silently_migrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);value=generation_strategy('sketch-reviewed-v1')
            value['instructions']+='unbound change'
            (root/'parallel-workspace-policy.json').write_text(json.dumps({'generation_strategy':value}))
            with self.assertRaisesRegex(ValueError,'frozen'):resolve_strategy(root)

    def test_version_is_the_only_runtime_document_policy_input(self):
        package,state=fixtures.fixture()
        with tempfile.TemporaryDirectory() as tmp:
            store=DocumentStore(tmp,package,state,[],generation_strategy='sketch-reviewed-v2')
            self.assertTrue(store.require_sketch and store.require_review and store.require_semantic_review)
            self.assertTrue(store.allow_formal_leaf)
            self.assertFalse(store.require_phases or store.require_assessment)
            with self.assertRaisesRegex(ValueError,'overrides'):
                DocumentStore(tmp,package,state,[],generation_strategy='sketch-reviewed-v2',require_review=False)


class SharedLeafGateTests(unittest.TestCase):
    def setUp(self):
        self.base=fixtures.WorkflowTests();self.base.setUp();self.addCleanup(self.base.doCleanups)

    def test_submission_and_replay_call_the_same_leaf_validator(self):
        from regen.planning import sketch_workflow as workflow
        store=self.base.store;row=self.base.row(self.base.probe())
        with patch.object(workflow,'validate_leaf_review',wraps=workflow.validate_leaf_review) as gate:
            self.base.review([row])
            workflow._review(store,*self._nodes_proofs(),'contract')
        self.assertEqual(gate.call_count,2)

    def _nodes_proofs(self):
        from regen.planning.theory_document import parse
        entries,proofs=parse(self.base.store.path.read_text(),self.base.package,self.base.checker.imports())
        return {n['id']:n for n in entries},proofs

    def test_empty_probe_identity_cannot_bypass_a_required_diagnostic(self):
        from regen.planning.sketch_workflow import leaf_review_issue
        nodes,_=self._nodes_proofs()
        for identity in (None,''):
            row=self.base.row(identity)
            self.assertEqual(leaf_review_issue(self.base.store,nodes,row)['code'],'probe_missing')
