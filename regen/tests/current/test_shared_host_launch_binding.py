"""Both fresh launchers pass an identical opt-in host-native policy downstream."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.check_executor import shared_host_binding
from regen.execution.vero_checker_adapter import VeroCheckerAdapter
from regen.planning.generic_codex_agent import Controller
from regen.tools import generic_serial_application, vero_serial_application


class SharedHostLaunchBindingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.dataset = self.root / 'dataset'
        self.dataset.mkdir()
        (self.dataset / 'repository-planning-input.json').write_text(json.dumps({'contracts': []}))
        self.lock_dir = self.root / 'shared-native'

    def controller(self, policy):
        with patch.object(Controller, '_planner', return_value=object()):
            value = Controller(self.root / ('codex-' + str(len(list(self.root.glob('codex-*'))))),
                self.dataset, SimpleNamespace(rows=[]), object(), {'threads': 4, **policy})
        self.addCleanup(value.resources.close)
        self.addCleanup(value.executor.close)
        return value

    def adapter(self, policy):
        with patch('regen.execution.vero_checker_adapter.load_context',
                   return_value={'checker_context_id': 'fixture', 'heaps': self.root}), \
             patch('regen.execution.vero_checker_adapter.clone_heaps', return_value={}):
            value = VeroCheckerAdapter(self.dataset,
                self.root / ('vero-' + str(len(list(self.root.glob('vero-*'))))), **policy)
        self.addCleanup(value.close)
        return value

    def test_default_does_not_enable_cross_process_arbitration(self):
        self.assertEqual(shared_host_binding(), {})
        self.assertIsNone(self.adapter({}).resources.shared_host)
        self.assertIsNone(self.controller({}).resources.shared_host)

    def test_vero_and_codex_adapters_use_one_frozen_lock_directory(self):
        policy = shared_host_binding(self.lock_dir, slots=2, cleanup_timeout=45)
        vero = self.adapter(policy)
        codex = self.controller(policy)
        self.assertEqual(vero.resources.policy['shared_host_lock_dir'], str(self.lock_dir))
        self.assertEqual(codex.resources.policy['shared_host_lock_dir'], str(self.lock_dir))
        for owner in (vero, codex):
            self.assertEqual(owner.resources.policy['shared_host_slots'], 2)
            self.assertEqual(owner.resources.policy['shared_host_cleanup_timeout'], 45)
        with self.assertRaisesRegex(ValueError, 'policy mismatch'):
            self.adapter(shared_host_binding(self.lock_dir, slots=1))

    def test_both_cli_parsers_accept_opt_in_and_reject_unbound_slot_count(self):
        commands = (
            (vero_serial_application,
             ['--vero-source', 'source', '--benchmark', 'benchmark', '--dataset', 'dataset',
              '--output', 'output', '--client', 'client', '--auth', 'auth', '--image', 'image',
              '--expected-contracts', '287'],
             {'status': 'complete', 'iterations': []}),
            (generic_serial_application,
             ['--dataset', 'dataset', '--output', 'output', '--model-config', 'model.json',
              '--auth', 'auth', '--model', 'gpt-5.6-sol', '--reasoning', 'medium',
              '--expected-contracts', '287'],
             {'status': 'complete', 'passed': True}),
        )
        for module, base, result in commands:
            with self.subTest(module=module.__name__):
                argv = ['application', *base, '--shared-host-lock-dir', str(self.lock_dir),
                        '--shared-host-slots', '2', '--shared-host-cleanup-timeout', '45']
                with patch.object(sys, 'argv', argv), patch.object(module, 'run', return_value=result) as run, \
                     redirect_stdout(io.StringIO()):
                    with self.assertRaises(SystemExit) as stopped:
                        module.main()
                self.assertFalse(stopped.exception.code)
                self.assertEqual(run.call_args.args[0].shared_host_lock_dir, str(self.lock_dir))
                bad = ['application', *base, '--shared-host-slots', '2']
                with patch.object(sys, 'argv', bad), patch.object(module, 'run') as run, \
                     redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as stopped:
                        module.main()
                self.assertEqual(stopped.exception.code, 2)
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
