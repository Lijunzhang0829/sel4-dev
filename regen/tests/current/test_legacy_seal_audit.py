"""Exact historical v3 replay does not authorize weak or newly bound old audits."""
from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.legacy_seal_audit import legacy_complete_audit_source
from regen.execution.proof_commits import digest
from regen.execution.seal_evidence import AUDIT_POLICY, bind_seal, validate_seal
from regen.tests.current.seal_fixture import bound, evidence
from regen.tests.current.test_proof_commits import fixture


class LegacySealAuditTests(unittest.TestCase):
    def state(self):
        package, state = fixture()
        state['proofs'] = {'helper':'by simp', 'contract':'by simp'}
        state['accepted_order'] = ['helper','contract']
        state['graph']['accepted_order'] = state['accepted_order']
        return package, state

    def bind_id(self, state, record):
        state.update(status='sealed', seal_id='theory-seal:'+digest(record)[:20])

    def legacy(self, policy='isolated-v1'):
        package, state = self.state()
        record = evidence(state, policy=policy)
        record.pop('audit_policy')
        renderer = object.__new__(IsabelleChecker)
        renderer.imports = lambda: record['imports']
        renderer.complete_context_policy = policy
        source = legacy_complete_audit_source(renderer, state['graph'], state['proofs'])
        record['dependencies'].update(bound('source', source))
        self.bind_id(state, record)
        return package, state, record

    def test_exact_legacy_v3_replays_with_original_identity(self):
        for policy in ('isolated-v1','arch-reuse-v1'):
            with self.subTest(policy=policy):
                _, state, record = self.legacy(policy)
                before = deepcopy((state, record))
                validate_seal(state, record, expected_imports=record['imports'],
                    base_session=record['base_session'], runtime={'complete_context_policy':policy})
                self.assertEqual((state, record), before)

    def test_current_policy_cannot_claim_old_source(self):
        _, state, record = self.legacy()
        record['audit_policy'] = AUDIT_POLICY
        self.bind_id(state, record)
        with self.assertRaisesRegex(ValueError, 'dependency audit source mismatch'):
            validate_seal(state, record)

    def test_missing_policy_cannot_claim_new_source(self):
        _, state = self.state()
        record = evidence(state)
        record.pop('audit_policy')
        self.bind_id(state, record)
        with self.assertRaisesRegex(ValueError, 'dependency audit source mismatch'):
            validate_seal(state, record)

    def test_unknown_or_null_policy_cannot_select_legacy(self):
        for policy in (None, '', 'legacy', 'declaration-export-audit-v2'):
            with self.subTest(policy=policy):
                _, state, record = self.legacy()
                record['audit_policy'] = policy
                self.bind_id(state, record)
                with self.assertRaisesRegex(ValueError, 'unsupported seal audit policy'):
                    validate_seal(state, record)

    def test_rehashed_legacy_source_changes_are_rejected(self):
        for mutate in (lambda s:s+'\n',
                       lambda s:s.replace('Thm.consolidate targets;', 'Thm.consolidate [hd targets];'),
                       lambda s:s.replace('if null targets then error "Audit target has no theorems" else ();', '();')):
            _, state, record = self.legacy()
            record['dependencies'].update(bound('source', mutate(record['dependencies']['source_content'])))
            self.bind_id(state, record)
            with self.assertRaisesRegex(ValueError, 'dependency audit source mismatch'):
                validate_seal(state, record)

    def test_all_native_and_state_bindings_still_apply_to_legacy(self):
        changes = [
            ('failed_native',lambda e:e['dependencies'].update(returncode=1)),
            ('unbound_log',lambda e:e['dependencies'].update(log_content='changed')),
            ('missing_receipt',lambda e:e['dependency_receipts'].pop('helper')),
            ('admitted_receipt',lambda e:e['dependency_receipts']['helper'].update(bound('raw','HAS_SKIP_PROOF\t'+b'true'.hex()+'\n'))),
            ('wrong_imports',lambda e:e.update(imports=['Wrong.Theory'])),
            ('wrong_build',lambda e:e['build'].update(session='Wrong')),
            ('weak_version',lambda e:e.update(seal_evidence_version=2)),
        ]
        for name, mutate in changes:
            with self.subTest(name=name):
                _, state, record = self.legacy()
                mutate(record)
                self.bind_id(state, record)
                with self.assertRaises(ValueError):
                    validate_seal(state, record)
        _, state, record = self.legacy()
        state['proofs']['helper'] = 'by blast'
        with self.assertRaisesRegex(ValueError, 'state binding'):
            validate_seal(state, record)

    def test_new_binding_always_tags_current_audit_and_rejects_old_native_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for legacy in (False, True):
                _, state = self.state()
                record = self.legacy()[2] if legacy else evidence(state)
                locations = {}
                for key, receipt in record['dependency_receipts'].items():
                    path = root/(key+'.tsv')
                    path.write_text(receipt['raw_content'])
                    locations[key] = {'raw':str(path)}
                record['dependencies']['complete_theory_audit']['proof_dependency_paths'] = locations
                checker = SimpleNamespace(base_session=record['base_session'],
                    imports=lambda:record['imports'], complete_context_policy='isolated-v1')
                new = bind_seal(state, record, checker)
                self.assertEqual(new['audit_policy'], AUDIT_POLICY)
                self.bind_id(state, new)
                if legacy:
                    with self.assertRaisesRegex(ValueError, 'dependency audit source mismatch'):
                        validate_seal(state, new)
                else:
                    validate_seal(state, new)

    def test_sealed_reuse_preserves_and_validates_historical_evidence(self):
        from regen.execution.repository_execution import _reuse_sealed_theory
        package, sealed, record = self.legacy()
        original = deepcopy(record)
        initial = deepcopy(sealed)
        initial.update(status='planned', seal_id=None)
        origin = {'imports':record['imports'], 'commits':[{
            'state':sealed, 'evidence':record, 'commit_id':'historical-seal'}]}
        appended = []
        class Journal:
            def append(self, event, state, evidence):
                if event == 'seal':
                    validate_seal(state, evidence)
                appended.append((event,deepcopy(state),deepcopy(evidence)))
        with tempfile.TemporaryDirectory() as temporary, \
                patch('regen.execution.repository_execution.journal', return_value=Journal()):
            result = _reuse_sealed_theory(Path(temporary), {}, package, origin, initial)
        self.assertEqual([r[0] for r in appended], ['initial','seal'])
        self.assertEqual(appended[-1][2], original)
        self.assertEqual(result['seal_id'], sealed['seal_id'])
        self.assertNotIn('audit_policy', appended[-1][2])


if __name__ == '__main__':
    unittest.main()
