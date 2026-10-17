"""A process-local VERO GREEN cache never substitutes for a native seal."""
import hashlib
import tempfile
import unittest
from collections import OrderedDict
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
from unittest.mock import Mock, patch

from regen.execution.vero_checker_adapter import VeroCheckerAdapter


class ExactGreenReuseTests(unittest.TestCase):
    def adapter(self, root, *, frozen=True, observer=True, imports=None):
        imports = {'T':[]} if imports is None else imports
        source = root / 'rendered'
        source.mkdir()
        (source / 'T.thy').write_text('theory T\nimports Vero_Support '
            + ' '.join(imports['T']) + '\nbegin\nend\n')
        (source / 'Vero_Support.thy').write_text('theory Vero_Support\nimports Main\nbegin\nend\n')
        for theory in imports:
            if theory != 'T':
                (source / (theory + '.thy')).write_text(
                    'theory ' + theory + '\nimports Vero_Support\nbegin\nend\n')
        checker = object.__new__(VeroCheckerAdapter)
        checker.root = root / 'adapter'
        checker.context = dict(image='test-image', l4v=root, semantic_workspace=root,
            isabelle=root, heaps=root, base_session='HOL', base_import='Main',
            base_catalog='/base', checker_context_id='frozen-context')
        checker.timeout = 900
        checker.resources = SimpleNamespace(policy={'slots':1,'threads':4})
        checker.lock = RLock()
        checker.closed = False
        checker.frozen_plan = None
        checker._exact_green_checks = OrderedDict()
        if frozen:
            checker.set_frozen_plan({'imports':imports,
                'plan_sha256':'p'*64, 'allocation_sha256':'a'*64,
                'imports_sha256':'i'*64})
        if observer:
            stop = SimpleNamespace(event=SimpleNamespace(is_set=lambda:False),
                check=lambda:None, begin_exclusion=lambda reason:reason,
                end_exclusion=lambda handle:None, mark_sealed=lambda theory:None)
            checker.observer = SimpleNamespace(stop=stop, ledger=Mock(),
                bench={'contracts':[{'id':'c0001','module':'T'}]},
                checked=Mock())
        else:
            checker.observer = None
        return checker, source

    def native_class(self, *, returns=(0,), logs=None):
        class Native:
            checks = 0
            seals = 0
            def __init__(self, run_dir, work_dir, **kwargs):
                self.work_dir = work_dir
            def _command(self, source, *, session):
                return []
            def _check(self, source, theory, *, timeout):
                index = type(self).checks
                type(self).checks += 1
                rc = returns[min(index, len(returns)-1)]
                path = self.work_dir / (theory + '.thy')
                path.write_text(source)
                log = self.work_dir / (theory + '.log')
                messages=logs if logs is not None else ('VERO_RESULT c0001 prove clean\n',)
                log.write_text(messages[min(index,len(messages)-1)])
                return {'returncode':rc, 'wall_ms':1000,
                    'source':str(path), 'log':str(log)}
            def build_generated_session(self, theories):
                type(self).seals += 1
                log = self.work_dir / 'seal.log'
                log.write_text('build complete\n')
                return {'returncode':0, 'wall_ms':200, 'log':str(log)}
            def close_prefix(self):
                pass
        return Native

    def test_exact_green_reused_but_seal_still_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapter, source = self.adapter(root)
            Native = self.native_class()
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker', Native):
                first = adapter.check(source, 'T')
                second = adapter.check(source, 'T')
            self.assertEqual(Native.checks, 1)
            self.assertEqual(Native.seals, 2)
            self.assertFalse(first['reused_exact_check'])
            self.assertTrue(second['reused_exact_check'])
            self.assertEqual(second['native_elapsed_seconds'], 0)
            self.assertEqual(second['seal_elapsed_seconds'], .2)
            self.assertEqual(second['elapsed_seconds'], .2)
            self.assertEqual(second['audited'], ['c0001:prove'])
            self.assertEqual(second['native_evidence'], first['native_evidence'])
            self.assertEqual(second['reuse_receipt']['original_native_elapsed_seconds'], 1)
            self.assertEqual(second['reuse_receipt']['original_source_sha256'],
                hashlib.sha256((source / 'T.thy').read_bytes()).hexdigest())
            self.assertEqual(adapter.observer.ledger.append.call_args_list[-1].args[0],
                'sealed')
            self.assertEqual(sum(call.args[0]=='reused_exact_check'
                for call in adapter.observer.ledger.append.call_args_list), 1)

    def test_source_import_support_and_timeout_changes_force_cold_check(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapter, source = self.adapter(root)
            Native = self.native_class()
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker', Native):
                adapter.check(source, 'T')
                changed_timeout = adapter.check(source, 'T', timeout=10)
                (source / 'T.thy').write_text(
                    'theory T\nimports Vero_Support\nbegin\nlemma x: True by simp\nend\n')
                changed_theory = adapter.check(source, 'T')
                (source / 'Vero_Support.thy').write_text(
                    'theory Vero_Support\nimports Main\nbegin\nlemma y: True by simp\nend\n')
                changed_support = adapter.check(source, 'T')
            self.assertEqual(Native.checks, 4)
            self.assertTrue(all(not result['reused_exact_check'] for result in
                (changed_timeout, changed_theory, changed_support)))

    def test_changed_import_closure_forces_cold_check(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapter, source = self.adapter(root, imports={'A':[],'T':['A']})
            Native = self.native_class()
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker', Native):
                self.assertFalse(adapter.check(source, 'T')['reused_exact_check'])
                self.assertTrue(adapter.check(source, 'T')['reused_exact_check'])
                (source / 'A.thy').write_text(
                    'theory A\nimports Vero_Support\nbegin\nlemma x: True by simp\nend\n')
                self.assertFalse(adapter.check(source, 'T')['reused_exact_check'])
            self.assertEqual(Native.checks, 2)

    def test_red_timeout_and_corrupt_original_log_are_never_reused(self):
        for failure in (1, 124):
            with self.subTest(returncode=failure), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                adapter, source = self.adapter(root)
                Native = self.native_class(returns=(failure, 0))
                with patch('regen.execution.vero_checker_adapter.IsabelleChecker', Native):
                    first = adapter.check(source, 'T')
                    second = adapter.check(source, 'T')
                    self.assertEqual(Native.checks, 2)
                    self.assertFalse(first['reused_exact_check'])
                    self.assertFalse(second['reused_exact_check'])
                    Path(second['native_evidence']['log']).write_text('tampered\n')
                    third = adapter.check(source, 'T')
                    self.assertEqual(Native.checks, 3)
                    self.assertFalse(third['reused_exact_check'])

    def test_successful_native_exit_without_required_audit_marker_is_not_cached(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            adapter,source=self.adapter(root)
            Native=self.native_class(logs=('Isabelle finished successfully\n',
                'VERO_RESULT c0001 prove clean\n'))
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',Native):
                first=adapter.check(source,'T')
                self.assertEqual(first['returncode'],0)
                self.assertEqual(first['audited'],[])
                self.assertEqual(first['seal_status'],'not_attempted')
                self.assertEqual(len(adapter._exact_green_checks),0)
                second=adapter.check(source,'T')
                third=adapter.check(source,'T')
            self.assertEqual(Native.checks,2)
            self.assertEqual(Native.seals,2)
            self.assertFalse(second['reused_exact_check'])
            self.assertTrue(third['reused_exact_check'])
            self.assertEqual(third['audited'],['c0001:prove'])

    def test_missing_frozen_plan_or_support_disables_reuse(self):
        for no_plan in (True, False):
            with self.subTest(no_plan=no_plan), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                adapter, source = self.adapter(root, frozen=not no_plan)
                if not no_plan:
                    (source / 'Vero_Support.thy').unlink()
                Native = self.native_class()
                with patch('regen.execution.vero_checker_adapter.IsabelleChecker', Native):
                    self.assertFalse(adapter.check(source, 'T')['reused_exact_check'])
                    self.assertFalse(adapter.check(source, 'T')['reused_exact_check'])
                self.assertEqual(Native.checks, 2)


if __name__ == '__main__':
    unittest.main()
