"""Validation-tool guards only; these tests make no native-success claim."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import digest
from regen.tools.document_acceptance_validation import (
    MODEL, THEORY, ScriptedProvenance, create_fixture, oracle_fact_audit_ml, readonly_heaps, run,
)


class DocumentAcceptanceValidationTests(unittest.TestCase):
    def test_oracle_uses_process_draft_namespace_and_kernel_checks(self):
        ml = oracle_fact_audit_ml()
        self.assertIn('Thy_Info.get_theory "Draft.' + THEORY + '"', ml)
        for operation in ('Thm.consolidate', 'Thm_Deps.has_skip_proof', 'Thm_Deps.all_oracles'):
            self.assertIn(operation, ml)

    def test_existing_output_is_refused_before_loading_native_context(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
                'regen.tools.document_acceptance_validation.load_context') as load:
            with self.assertRaises(FileExistsError):
                run(Path(tmp)/'unused', Path(tmp))
            load.assert_not_called()

    def test_only_parent_heap_mount_becomes_readonly(self):
        command = ['docker', '--mount', 'type=bind,src=/test/heaps,dst=/root/.isabelle/heaps',
                   '--mount', 'type=bind,src=/test/output,dst=/worker']
        actual = readonly_heaps(command)
        self.assertTrue(actual[2].endswith(',readonly'))
        self.assertEqual(actual[4], command[4])
        self.assertFalse(command[2].endswith(',readonly'))

    def test_fixture_starts_without_synthetic_journal_or_green_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config, package = create_fixture(root, {'checker_context_id': 'fixture'})
            self.assertTrue(config['validation_only'])
            self.assertEqual(config['model_calls'], 0)
            self.assertEqual(len(package['contracts']), 1)
            self.assertFalse((root/'theories').exists())
            self.assertEqual(list((root/'generated').iterdir()), [])
            self.assertTrue(config['implementation'])

    def test_scripted_provenance_never_claims_a_model_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = ScriptedProvenance(Path(tmp))
            prefix = Path(tmp)/'call'
            proposal = {'action': 'check_theory', 'source_sha256': 'source'}
            response = bridge.record(prefix, 'context', proposal, {'scripted': True},
                                     theory=THEORY, model=MODEL)
            self.assertEqual(json.loads(response), proposal)
            verified = bridge.validate(prefix)
            self.assertFalse(verified['real_model'])
            self.assertFalse(verified['provider_network_call'])
            self.assertIn('test-only', verified['eligibility_override'])
            value = json.loads(bridge.path(prefix).read_text())
            value['proposal']['source_sha256'] = 'different-source'
            value['receipt_sha256'] = digest({k: v for k, v in value.items() if k != 'receipt_sha256'})
            bridge.path(prefix).write_text(json.dumps(value))
            with self.assertRaisesRegex(AssertionError, 'changed'):
                bridge.validate(prefix)


if __name__ == '__main__':
    unittest.main()
