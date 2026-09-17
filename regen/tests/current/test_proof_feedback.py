import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from regen.execution.proof_feedback import semantic_goal, checked_history
from regen.execution.retry_policy import failure_signature
from regen.execution.graph_revision_agent import historical_node_residuals, augment_decision_context
from regen.planning.premise_retrieval import retrieve_proof_premises


class FeedbackTest(unittest.TestCase):
    def test_cold_warm_same_goal_and_not_model_explanation(self):
        warm = 'RED\nFailed to finish proof:\ngoal (1 subgoal):\n 1. x = y\nAt command "by" (line 7)'
        cold = '\n'.join('*** ' + x for x in warm.splitlines()[1:])
        self.assertEqual(semantic_goal(warm), semantic_goal(cold))
        self.assertEqual(failure_signature({'check': {'diagnostic': warm}}), failure_signature({'check': {'diagnostic': cold}}))
        self.assertIsNone(failure_signature({'check': {'diagnostic': warm, 'isabelle_called': False}}))
        self.assertIsNone(semantic_goal('Timeout after 60s'))

    def test_history_uses_full_contract_id(self):
        key = 'contract:Demo:c:abcdef'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path = root/'nodes'/key.replace(':', '_')/'round-01/attempts.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps([{'attempt': 1, 'check': {'diagnostic': 'RED\ngoal (1 subgoal):\n 1. P'}}]))
            self.assertEqual(len(historical_node_residuals(root, 'c', node_id=key)), 1)
            graph = {'nodes': [{'id': key, 'name': 'c', 'role': 'contract', 'dependencies': [], 'consumers': [], 'statement': 'lemma c: P'}]}
            old = {'node_id': key, 'decision_id': '000001', 'decision': 'RETRY_PROOF', 'semantic_residual_fingerprints': [], 'retry_directive_sha256': 'd', 'retry_directive': {'retrieval_query': 'query'}}
            c = augment_decision_context(root, graph, {'node_id': key}, {'accepted_decision_history': [old], 'proof_round': 2})
            self.assertEqual(c['consecutive_retry_proof_decisions'], 1)
            self.assertEqual(len(c['historical_isabelle_semantic_residuals']), 1)

    def test_inherited_residual_cannot_match_itself_as_new_stagnation(self):
        key = 'contract:Demo:c:abcdef'
        def attempt(goal):
            return {'attempt': 1, 'check': {'diagnostic': 'RED\ngoal (1 subgoal):\n 1. '+goal}}
        old, fresh = attempt('P'), attempt('Q')
        graph = {'nodes': [{'id': key, 'name': 'c', 'role': 'contract',
            'dependencies': [], 'consumers': [], 'statement': 'lemma c: P'}]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root/'nodes'/key.replace(':', '_')/'round-01/attempts.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps([old]))
            policy = {'proof_round': 2, 'proof_rounds_used': 2, 'proof_retry_limit': 5,
                      'accepted_decision_history': []}
            for attempts in ([fresh], []):
                with self.subTest(attempts=attempts):
                    context = augment_decision_context(root, graph,
                        {'node_id': key, 'checked_history': [old], 'attempts': attempts}, policy)
                    self.assertFalse(context['stable_historical_residual'])
                    self.assertTrue(context['retry_proof_allowed'])
                    self.assertEqual(len(context['historical_isabelle_semantic_residuals']), 1)
            repeated = augment_decision_context(root, graph,
                {'node_id': key, 'checked_history': [old], 'attempts': [old]}, policy)
            self.assertTrue(repeated['stable_historical_residual'])
            self.assertFalse(repeated['retry_proof_allowed'])
            exhausted = augment_decision_context(root, graph,
                {'node_id': key, 'checked_history': [old], 'attempts': [fresh]},
                {**policy, 'proof_round': 5, 'proof_rounds_used': 5})
            self.assertFalse(exhausted['retry_proof_allowed'])

    def test_empty_round_does_not_erase_checked_history(self):
        checked = {'proof_body': 'by simp', 'check': {'diagnostic': 'RED\ngoal: P'}}
        invalid = {'proof_body': '', 'check': {'diagnostic': 'empty', 'isabelle_called': False}}
        self.assertEqual(checked_history({'checked_history': [checked], 'attempts': [invalid]}), [checked])
        self.assertEqual(checked_history({'attempts': [checked]}), [checked])

    def test_prover_empty_first_response_keeps_previous_checker_goal(self):
        from unittest.mock import patch
        from regen.execution.lemma_prover import prove_node
        old = {'proof_body': 'by simp', 'check': {'diagnostic': 'RED\ngoal: P'}}
        graph = {'theory': 'Demo', 'file_slot': 'Demo.thy', 'nodes': [{'id': 'c', 'role': 'contract', 'name': 'c', 'statement': 'lemma c: P', 'context': [], 'strategy': 'direct', 'dependencies': []}]}
        captured = []
        def call(provider, prompt, prefix, **kwargs):
            captured.append(json.loads(prompt.split('INPUT:\n', 1)[1]))
            prefix.parent.mkdir(parents=True, exist_ok=True)
            prefix.with_suffix('.meta.json').write_text(json.dumps({'sealable_generation': True}))
            return '{"proof_body": null}'
        with tempfile.TemporaryDirectory() as tmp, patch('regen.execution.lemma_prover.call_llm', side_effect=call), patch('regen.execution.lemma_prover.retrieve_proof_premises', return_value=[]), patch('regen.execution.lemma_prover.write_llm_module_log'):
            result = prove_node(graph, 'c', {}, checker=object(), premise_database=Path(tmp)/'db', output_dir=Path(tmp)/'theories/Demo', previous_round_feedback={'attempts': [old]}, round_id=2)
        self.assertEqual(result['residual'], old['check']['diagnostic'])
        self.assertIn('validation', result['last_model_error'])
        self.assertEqual(captured[0]['repair_baseline']['proof_body'], 'by simp')
        self.assertEqual(len(result['checked_history']), 1)

    def test_requested_and_residual_definitions_are_exactly_retrieved(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp)/'db'
            with sqlite3.connect(db) as c:
                c.execute('CREATE TABLE premises (record_id TEXT, canonical_name TEXT, exact_statement TEXT, owner_theory TEXT, owner_session TEXT, source_class TEXT, attributes_json TEXT)')
                for term in ('targetOp', 'residualOp', 'requestedOp'):
                    c.execute('INSERT INTO premises VALUES (?,?,?,?,?,?,?)', (term, 'Demo.'+term+'_def', term+' x = x', 'Demo', 'Base', 'frozen_parent', '[]'))
            audit = {}
            records = retrieve_proof_premises(db, 'targetOp', goal_statement='targetOp x = x', residual='goal: residualOp x = x', retrieval_hint='requestedOp', retrieval_audit=audit)
            self.assertEqual({r['canonical_name'] for r in records}, {'Demo.'+t+'_def' for t in ('targetOp', 'residualOp', 'requestedOp')})
            self.assertEqual(set(audit['selected']), {r['canonical_name'] for r in records})

if __name__ == '__main__':
    unittest.main()
