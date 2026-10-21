"""Real setup orchestration with mocked builds; never executes Isabelle/model."""
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from regen.execution.repository_execution import ProviderPreparationError, setup


class ProviderSetupFailureReceiptTests(unittest.TestCase):
    def fixture(self, result):
        stack = ExitStack()
        self.addCleanup(stack.close)
        root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        states = [{'theory':'Provider', 'file_slot':'Provider.thy', 'status':'sealed'},
                  {'theory':'Target', 'file_slot':'Target.thy', 'status':'pending'}]
        for name, value in (
                ('run.json', {'implementation':{}, 'execution_input_kind':'refine-dataset'}),
                ('execution-input.json', {'files':states}),
                ('generated-premises.json', {'records':[]})):
            (root/name).write_text(json.dumps(value))
        provider = root/'theories'/'Provider'
        provider.mkdir(parents=True)
        (provider/'imports.json').write_text('[]')
        (root/'theories'/'Target'/'heaps').mkdir(parents=True)
        generated = root/'generated'
        generated.mkdir()
        (generated/'Regen_Provider.thy').write_text('fixture provider; not proof evidence')
        checker_generated = root/'checker-generated'
        checker_generated.mkdir()
        checker = SimpleNamespace(generated_dir=checker_generated,
                                  build_generated_session=Mock(return_value=result))
        package = {'file_slot':'Target.thy', 'repository_projection':{
            'schema_version':5, 'required_imports':[{'theory':'Provider'}]}}
        stack.enter_context(patch('regen.execution.repository_execution.progress', return_value={'files':states}))
        stack.enter_context(patch('regen.execution.repository_execution.package_for', return_value=package))
        stack.enter_context(patch('regen.execution.repository_execution.checker_for', return_value=checker))
        journal = stack.enter_context(patch('regen.execution.repository_execution.journal'))
        return root, checker, journal

    def assert_failure(self, returncode, category):
        result = {'verdict':'red', 'returncode':returncode, 'diagnostic':'synthetic failure'}
        root, checker, journal = self.fixture(result)
        with self.assertRaises(ProviderPreparationError) as caught:
            setup(root, 'Target', 900)
        self.assertEqual(caught.exception.category, category)
        self.assertEqual(caught.exception.result, result)
        checker.build_generated_session.assert_called_once_with(['Regen_Provider'])
        self.assertFalse((root/'theories'/'Target'/'provider-session.json').exists())
        journal.assert_not_called()
        self.assertTrue((root/'theories'/'Target'/'imports.json').exists())

    def test_timeout_has_no_ready_receipt(self):
        self.assert_failure(124, 'timeout')

    def test_sigkill_has_no_ready_receipt_and_does_not_claim_oom(self):
        self.assert_failure(137, 'killed')

    def test_failed_build_has_no_ready_receipt(self):
        self.assert_failure(1, 'build_failed')

    def test_green_publishes_receipt_and_skips_next_build(self):
        root, checker, journal = self.fixture({'verdict':'green', 'returncode':0})
        setup(root, 'Target', 900)
        self.assertEqual(json.loads((root/'theories'/'Target'/'provider-session.json').read_text()),
                         {'imports':['Regen_Provider']})
        setup(root, 'Target', 900)
        checker.build_generated_session.assert_called_once()
        self.assertEqual(journal.call_count, 2)


if __name__ == '__main__':
    unittest.main()
