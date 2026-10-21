"""Unit contracts for the isolated driver, not substitutes for native receipts."""
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from regen.execution.batch_checker import body
from regen.tools.native_backend_acceptance import (
    LABEL, binding_mutations, command, fixture, native_window, probe, proof_rejected, raw_native, run, source,
)


class NativeBackendAcceptanceTests(unittest.TestCase):
    def test_source_preserves_body_exactly(self):
        for text in ('', '\nunbundle l4v_word_context\n', fixture(1)['shared']):
            self.assertEqual(body(source('Semantic_Base.Semantic_Root', text)), text)

    def test_fixture_has_same_names_but_distinct_contexts_and_explicit_theft(self):
        first, second = fixture(1), fixture(2)
        for item in (first, second):
            self.assertIn('lemma shared_fact:', item['shared'])
            self.assertIn('by (rule leaked_helper)', item['theft'])
            self.assertIn('lemma doomed: "False"', item['failed'])
        self.assertIn('x = 1', first['shared'])
        self.assertIn('x = 2', second['shared'])
        with self.assertRaises(ValueError):
            fixture(3)

    def test_probe_requires_native_timestamps_not_python_thread_times(self):
        text = 'NATIVE_ACCEPT_START 1 10.0\nNATIVE_ACCEPT_END 1 12.0\n'
        self.assertEqual(native_window(text, 1), {'start': 10., 'end': 12.})
        self.assertIn('OS.Process.sleep (Time.fromSeconds 2)', probe(1))
        with self.assertRaises(ValueError):
            native_window(text, 2)

    def test_every_structural_mutation_is_rejected_even_with_fresh_hash(self):
        root = ET.Element('batch_audit', version='2', worker='native-worker', request='1', theory=LABEL)
        for key, value in [('prefix', 'base'), ('delta', 'suffix')]:
            ET.SubElement(root, key).text = value
        ET.SubElement(ET.SubElement(root, 'imports'), 'import').text = 'Main'
        ET.SubElement(ET.SubElement(root, 'facts'), 'fact').text = 'shared_fact'
        content = ET.tostring(root, encoding='unicode')
        check = {'worker': 'native-worker', 'request_id': 1, 'audit_content': content,
                 'audit_sha256': hashlib.sha256(content.encode()).hexdigest()}
        rows = binding_mutations(check, prefix='base', delta='suffix', imports=['Main'], names=['shared_fact'])
        self.assertEqual(len(rows), 7)
        self.assertTrue(all(row['rejected'] for row in rows))

    def test_parent_heaps_read_only_and_output_separate_with_hard_caps(self):
        ctx = {key: Path('/fixture') / key for key in ('l4v', 'isabelle', 'semantic_workspace', 'heaps')}
        ctx.update(architecture='ARM', image='frozen-image')
        args = command(ctx, Path('/output'), user_heaps=Path('/output/new-heaps'))
        self.assertIn('type=bind,src=/fixture/heaps,dst=/parent-heaps,readonly', args)
        self.assertIn('type=bind,src=/output/new-heaps,dst=/oracle-heaps', args)
        self.assertIn('ISABELLE_HEAPS_SYSTEM=/parent-heaps', args)
        self.assertIn('ISABELLE_HEAPS=/oracle-heaps', args)
        self.assertEqual(args[args.index('--memory') + 1], '8g')
        self.assertEqual(args[args.index('--cpus') + 1], '2')
        self.assertEqual(args[args.index('--network') + 1], 'none')

    def test_oracle_reads_exact_file_without_production_checker(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            directory = output / 'worker-1'
            directory.mkdir()
            text = source('Main', fixture(1)['shared'])
            (directory / (LABEL + '.thy')).write_text(text)
            ctx = {key: output / key for key in ('l4v', 'isabelle', 'semantic_workspace', 'heaps')}
            ctx.update(architecture='ARM', image='frozen-image', base_session='HOL', base_catalog='/semantic-base')
            limits = SimpleNamespace(check=lambda: None, register=lambda name: None, deadline=float('inf'))
            completed = SimpleNamespace(returncode=0, stdout='native output', stderr='')
            with patch('regen.tools.native_backend_acceptance.subprocess.run', return_value=completed) as called:
                result = raw_native(ctx, output, Path('worker-1'), 60, limits)
            self.assertTrue(result['pass'])
            self.assertEqual((directory / (LABEL + '.thy')).read_text(), text)
            self.assertIn('/acceptance/worker-1/' + LABEL, called.call_args_list[0].args[0])

    def test_limits_rejected_before_loading_context(self):
        with patch('regen.tools.native_backend_acceptance.load_context') as load:
            with self.assertRaises(ValueError):
                run('/unused', '/unused', timeout=10000)
            load.assert_not_called()

    def test_negative_requires_proof_diagnostic_not_any_failed_native_process(self):
        self.assertTrue(proof_rejected(1, 'Failed to apply initial proof method'))
        for code, text in [(124, 'Failed to finish proof'), (1, 'Bad theory import'),
                           (1, 'Failed to apply initial proof method; Outer syntax error'),
                           (1, 'Undefined fact: shared_fact'), (0, 'Failed to finish proof')]:
            with self.subTest(code=code, text=text):
                self.assertFalse(proof_rejected(code, text))


if __name__ == '__main__':
    unittest.main()
