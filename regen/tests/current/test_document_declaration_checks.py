"""Document checker naming must preserve exact evidence and historical replay."""
from copy import deepcopy
import hashlib
import unittest

from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.proof_batches import check_batch, check_declarations, prepare_batch, validate_batch_commit
from regen.planning.theory_document import candidate, check_document, render, validate_document_commit
from regen.tests.current.test_proof_batches import Checker
from regen.tests.current.test_proof_commits import fixture


class DocumentChecker(Checker):
    _source = IsabelleChecker._source
    _check_declaration_source = IsabelleChecker._check_declaration_source
    check_declarations = IsabelleChecker.check_declarations
    check_graph = IsabelleChecker.check_graph

    def __init__(self):
        self.labels = []
        self.diagnostic = ''

    def _check(self, source, label, **kwargs):
        self.labels.append(label)
        return {**super()._check(source, label, **kwargs), 'diagnostic': self.diagnostic}


class DocumentDeclarationChecksTest(unittest.TestCase):
    def setUp(self):
        self.package, self.old = fixture()
        self.checker = DocumentChecker()
        self.source = render(self.package, self.old, self.checker.imports())
        self.edited = self.source.replace('lemma helper: "True & True"',
                                          'lemma helper: "True & True & True"')

    def test_native_document_names_and_legacy_names_remain_distinct(self):
        current = candidate(self.package, self.old, self.source, self.checker.imports())
        index = current['graph']
        new = self.checker.check_declarations(index)
        self.assertTrue(new['source_content'].startswith('theory Declaration_Check_v2\n'))
        self.assertEqual(new['check_scope'], 'all-document-declarations')
        self.assertFalse(new['proofs_checked'])
        self.assertFalse(new['incremental'])
        self.assertIn('failed_declaration_id', new)
        self.assertNotIn('failed_node_id', new)
        old = self.checker.check_graph(index)
        self.assertTrue(old['source_content'].startswith('theory Graph_Check_v2\n'))
        self.assertEqual(old['check_scope'], 'all-graph-statements')
        self.assertIn('failed_node_id', old)
        self.assertNotIn('failed_declaration_id', old)

    def test_error_attribution_is_the_same_for_both_entry_points(self):
        current = candidate(self.package, self.old, self.source, self.checker.imports())
        index = current['graph']
        source = self.checker._source(index, {}, theory_name='Declaration_Check_v2', statements_only=True)
        line = source[:source.index('lemma target:')].count('\n') + 1
        self.checker.diagnostic = f'Unknown symbol (line {line} of "Declaration_Check_v2.thy")'
        self.assertEqual(self.checker.check_declarations(index)['failed_declaration_id'], 'contract')
        self.assertEqual(self.checker.check_graph(index)['failed_node_id'], 'contract')

    def test_dispatch_prefers_document_api_only_for_new_declaration_index(self):
        current = candidate(self.package, self.old, self.source, self.checker.imports())
        check_declarations(self.checker, current['graph'])
        check_declarations(self.checker, self.old['graph'])
        self.assertEqual(self.checker.labels, ['Declaration_Check_v2', 'Graph_Check_v1'])
        legacy_adapter = Checker()
        fallback = check_declarations(legacy_adapter, current['graph'])
        self.assertTrue(fallback['source_content'].startswith('theory Graph_Check_v2\n'))
        self.assertEqual(legacy_adapter.calls, 1)

    def test_document_declaration_edit_checks_and_binds_neutral_source(self):
        current = candidate(self.package, self.old, self.edited, self.checker.imports())
        evidence = check_document(self.checker, self.old, current, self.edited)
        self.assertEqual(self.checker.labels, ['Declaration_Check_v2'])
        self.assertEqual(evidence['graph_check']['check_scope'], 'all-document-declarations')
        validate_document_commit(self.package, self.old, current, evidence)

    def test_old_document_journal_and_checker_adapter_still_replay(self):
        for legacy in (False, True):
            with self.subTest(legacy_document=legacy):
                current = candidate(self.package, self.old, self.edited,
                                    self.checker.imports(), legacy=legacy)
                evidence = check_document(Checker(), self.old, current, self.edited)
                self.assertTrue(evidence['graph_check']['source_content'].startswith('theory Graph_Check_v2\n'))
                validate_document_commit(self.package, self.old, current, evidence)

    def test_declaration_binding_rejects_unknown_label_changed_statement_and_import(self):
        current = candidate(self.package, self.old, self.edited, self.checker.imports())
        evidence = check_document(self.checker, self.old, current, self.edited)
        text = evidence['graph_check']['source_content']
        for original, replacement in [
                ('Declaration_Check_v2', 'Declaration_Check_v3'),
                ('Declaration_Check_v2', 'Arbitrary_Check_v2'),
                ('True & True & True', 'False'),
                ('Semantic_Base.Semantic_Root', 'Other.Root')]:
            with self.subTest(replacement=replacement):
                tampered = deepcopy(evidence)
                changed = text.replace(original, replacement)
                for field in ('graph_check', 'check'):
                    tampered[field]['source_content'] = changed
                    tampered[field]['source_sha256'] = hashlib.sha256(changed.encode()).hexdigest()
                with self.assertRaisesRegex(ValueError, 'declaration checker source mismatch'):
                    validate_document_commit(self.package, self.old, current, tampered)

    def test_legacy_graph_evidence_cannot_claim_document_label(self):
        current = prepare_batch(self.package, self.old,
                                {'graph': {'delete_node_ids': ['helper']}, 'reason': 'unused'})
        evidence = check_batch(self.checker, self.old, current)
        self.assertEqual(self.checker.labels, ['Graph_Check_v2'])
        validate_batch_commit(self.old, current, evidence)
        for field in ('graph_check', 'check'):
            changed = evidence[field]['source_content'].replace('Graph_Check_v2', 'Declaration_Check_v2')
            evidence[field]['source_content'] = changed
            evidence[field]['source_sha256'] = hashlib.sha256(changed.encode()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'declaration checker source mismatch'):
            validate_batch_commit(self.old, current, evidence)


if __name__ == '__main__':
    unittest.main()
