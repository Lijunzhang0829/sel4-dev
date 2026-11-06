"""The new benchmark must retain context and keep preparation proofs out."""
import json
import copy
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from regen.execution.native_runtime import PROFILE, CALG_BASE, catalogs, configure_command, validate, uses_sel4
from regen.preparation.calg_dataset import (
    PROFILES, benchmark_profile, sanitize, distribution_identity, validate_approval,
)
from regen.planning.workspace_preflight import render
from regen.execution.isabelle_node_checker import automation_context_audit_ml


class CalgAdapterTests(unittest.TestCase):
    @unittest.skipUnless(
        (Path(__file__).resolve().parents[3] / 'c-algorithms/tools/pilot.py').is_file(),
        'optional C benchmark pilot project is not bundled with the regen application')
    def test_pilot_configuration_uses_dataset_count_and_identity(self):
        path = Path(__file__).resolve().parents[3] / 'c-algorithms/tools/pilot.py'
        spec = importlib.util.spec_from_file_location('calg_pilot', path)
        pilot = importlib.util.module_from_spec(spec); spec.loader.exec_module(pilot)
        for count in (6, 8):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); (root / 'dataset').mkdir()
                (root / 'dataset/repository-planning-input.json').write_text(json.dumps(
                    {'contracts': [{}] * count, 'benchmark': {'id': 'fixture-' + str(count)}}))
                pilot.configure(root)
                config = json.loads((root / 'configuration.json').read_text())
                self.assertEqual(config['targets'], count)
                self.assertEqual(config['benchmark_id'], 'fixture-' + str(count))
                self.assertEqual(config['model'], 'gpt-6-astra')
                self.assertEqual(config['reasoning_effort'], 'low')
                self.assertIn(f'all {count} contracts', (root / 'config/planning-policy.json').read_text())

    def binomial_fixture(self, root):
        """Small approved source and evidence closure, independent of native heaps."""
        source = root / 'source'
        for name in ('c', 'theories', 'targets-session', 'evidence', 'satisfiability'):
            (source / name).mkdir(parents=True, exist_ok=True)
        files = {'ROOT': 'session Fixture = HOL +', 'source-lock.json': '{}',
                 'environment-lock.json': '{}', 'c/binomial_heap_input.c': 'int f(void) { return 0; }',
                 'theories/Binomial_Environment.thy': 'theory Binomial_Environment imports Main begin end',
                 'theories/Binomial_Heap_C.thy': 'theory Binomial_Heap_C imports Binomial_Environment begin end',
                 'targets-session/Binomial_Heap_Model.thy': 'theory Binomial_Heap_Model imports Main begin end',
                 'targets-session/Binomial_Heap_Targets.thy': '\n'.join(
                     f'lemma {name}: "True" oops' for name in PROFILES['binomial']['targets'])}
        for name, text in files.items(): (source / name).write_text(text)
        def digest(name): return hashlib.sha256((source / name).read_bytes()).hexdigest()
        def save(name, value): (source / name).write_text(json.dumps(value))
        closure = {name: digest(name) for name in files}
        binding = hashlib.sha256(json.dumps(closure, sort_keys=True).encode()).hexdigest()
        catalog = dict(target_count=8, generation_allowed=True, semantic_binding=binding, source_closure=closure,
            targets=[dict(id=name, proposition='True', isabelle_context=PROFILES['binomial']['locale'])
                     for name in PROFILES['binomial']['targets']])
        save('evidence/readback.json', {'readback': 'fixture'})
        save('evidence/readback-comparison.json', dict(semantic_binding=binding,
            status='comparison_complete_with_correction_preparation_incomplete',
            readback_path='evidence/readback.json', readback_sha256=digest('evidence/readback.json')))
        (source / 'satisfiability/Witness.thy').write_text('lemma must_not_leak: "True" by simp')
        save('evidence/satisfiability.json', dict(semantic_binding=binding,
            status='concrete_environment_satisfiability_passed', target_count=8,
            audit={key: '0' for key in ('oracles','skipped_proofs','hidden_hypotheses','unproved_targets_exported')},
            files={'satisfiability/Witness.thy': digest('satisfiability/Witness.thy')}))
        save('review-approval.json', dict(status='approved_by_user', approved_semantic_binding=binding,
            approved_evidence={name:digest(name) for name in
                ('evidence/readback-comparison.json', 'evidence/satisfiability.json')}))
        return source, catalog

    def test_both_profiles_require_exact_inventory_and_locale(self):
        for kind, profile in PROFILES.items():
            catalog = dict(target_count=len(profile['targets']), targets=[
                dict(id=name, isabelle_context=profile['locale']) for name in profile['targets']])
            self.assertEqual(benchmark_profile(catalog)['kind'], kind)
            changed = copy.deepcopy(catalog); changed['targets'].pop()
            with self.assertRaisesRegex(ValueError, 'inventory'): benchmark_profile(changed)
            changed = copy.deepcopy(catalog); changed['targets'][0]['isabelle_context'] = 'wrong_locale'
            with self.assertRaisesRegex(ValueError, 'locale'): benchmark_profile(changed)

    def test_binomial_approval_rejects_source_statement_and_evidence_drift(self):
        for mutation in ('source', 'statement', 'closure', 'evidence', 'witness', 'approval', 'flag'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                source, catalog = self.binomial_fixture(Path(tmp))
                validate_approval(source, catalog)
                if mutation == 'source': (source / 'c/binomial_heap_input.c').write_text('changed')
                elif mutation == 'statement': catalog['targets'][0]['proposition'] = 'False'
                elif mutation == 'closure': catalog['source_closure'].pop('ROOT')
                elif mutation == 'evidence': (source / 'evidence/satisfiability.json').write_text('{}')
                elif mutation == 'witness': (source / 'satisfiability/Witness.thy').write_text('changed')
                elif mutation == 'approval':
                    path = source / 'review-approval.json'; data = json.loads(path.read_text())
                    data['status'] = 'pending'; path.write_text(json.dumps(data))
                else: catalog['generation_allowed'] = False
                with self.assertRaises(ValueError): validate_approval(source, catalog)

    def test_binomial_sanitization_excludes_target_and_preparation_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, catalog = self.binomial_fixture(root)
            (source / 'targets-session/Binomial_Preparation_Audit.thy').write_text('preparation_secret')
            (source / 'theories/Environment_Sanity.thy').write_text('preparation_secret')
            semantic = root / 'semantic'
            sanitize(source, semantic, benchmark_profile(catalog))
            actual = {str(p.relative_to(semantic)) for p in semantic.rglob('*') if p.is_file()}
            self.assertEqual(actual, {'c/binomial_heap_input.c', 'theories/Binomial_Environment.thy',
                'theories/Binomial_Heap_C.thy', 'model/Binomial_Heap_Model.thy',
                'model/Project_Input.thy', 'ROOT', 'native_process.py', 'native-runtime.json'})
            text = '\n'.join(p.read_text() for p in semantic.rglob('*.thy'))
            self.assertNotIn('must_not_leak', text)
            self.assertNotIn('preparation_secret', text)
            self.assertNotIn('bn_count_exact', text)
            self.assertIn('Calg_Binomial_Foundation', (semantic / 'ROOT').read_text())
            with self.assertRaisesRegex(ValueError, 'empty'): sanitize(source, semantic, benchmark_profile(catalog))

    def test_declaration_cache_tracks_dangling_link_and_later_target(self):
        from regen.execution.declaration_cache import tree_stamp
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            link = root / 'config.h'; link.symlink_to('generated/config.h')
            before = tree_stamp(root)
            self.assertEqual(before, tree_stamp(root))
            (root / 'generated').mkdir()
            (root / 'generated/config.h').write_text('config')
            self.assertNotEqual(before, tree_stamp(root))
            link.unlink(); link.symlink_to('different.h')
            self.assertNotEqual(before, tree_stamp(root))

    def test_repository_checker_preserves_explicit_architecture_and_legacy_default(self):
        from regen.execution.repository_execution import checker_for
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = dict(l4v=tmp, semantic_workspace=tmp, base_session='test',
                       base_import='Main', base_catalog=tmp, image='test', isabelle_dir=tmp)
            with patch('regen.execution.isabelle_node_checker.IsabelleChecker') as constructor:
                checker_for(root, run, root / 'check', 30, [])
                self.assertEqual(constructor.call_args.kwargs['architecture'], 'ARM')
                checker_for(root, {**run, 'architecture': 'X64'}, root / 'check', 30, [])
                self.assertEqual(constructor.call_args.kwargs['architecture'], 'X64')

    def test_distribution_identity_binds_dangling_links_and_rejects_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            link = root / 'config.h'; link.symlink_to('generated/config.h')
            before = distribution_identity(root)
            (root / 'generated').mkdir(); (root / 'generated/config.h').write_text('config')
            self.assertNotEqual(before, distribution_identity(root))
            link.unlink(); link.symlink_to('/etc/passwd')
            with self.assertRaisesRegex(ValueError, 'escapes'): distribution_identity(root)

    def test_unknown_layout_and_wrong_architecture_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'afp').mkdir(); (root / 'afp/ROOTS').touch()
            checker = SimpleNamespace(base_session=CALG_BASE, semantic_workspace=root,
                                      l4v_dir=root, architecture='X64')
            path = root / 'native-runtime.json'
            path.write_text(json.dumps({**PROFILE, 'mount': '/'}))
            with self.assertRaisesRegex(ValueError, 'unsupported'): validate(checker)
            path.write_text(json.dumps(PROFILE)); validate(checker)
            checker.architecture = 'ARM'
            with self.assertRaisesRegex(ValueError, 'X64'): validate(checker)

    def test_runtime_changes_are_scoped_to_calg(self):
        legacy = SimpleNamespace(base_session='Semantic_Base')
        command = ['docker', 'run', '-e', 'L4V_ARCH=ARM', 'image',
                   '/workspace/verification/isabelle/bin/isabelle', 'process']
        self.assertEqual(configure_command(legacy, command.copy()), command)
        self.assertTrue(uses_sel4(legacy))
        self.assertTrue(all(p.startswith('/sel4-project/verification/l4v/') for p in catalogs(legacy)))
        calg = SimpleNamespace(base_session=CALG_BASE, isabelle_dir=Path('/isa'), l4v_dir=Path('/deps'))
        actual = configure_command(calg, command.copy())
        self.assertIn('ISABELLE_HEAPS=/root/.isabelle/heaps', actual)
        self.assertFalse(uses_sel4(calg))
        self.assertTrue(all('/screen/' in p for p in catalogs(calg)))
        self.assertTrue(all(m.endswith(',readonly') for m in actual if m.startswith('type=bind')))

    def test_sanitization_retains_definitions_but_removes_witnesses(self):
        # Tiny fixture, independent of checked-in benchmark files.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'source'
            for directory in ('c', 'theories', 'targets-session'): (source / directory).mkdir(parents=True)
            (source / 'c/binary_heap_input.c').write_text('int f(void) { return 0; }')
            (source / 'theories/Heap_Environment.thy').write_text(
                'theory Heap_Environment imports Main begin\ndefinition kept where "kept = True"\n'
                'lemma malloc_failure_available: "True" by simp\nend\n')
            (source / 'theories/Binary_Heap_C.thy').write_text('theory Binary_Heap_C imports Heap_Environment begin end')
            (source / 'targets-session/Binary_Heap_Model.thy').write_text('theory Binary_Heap_Model imports Main begin end')
            sanitize(source, root / 'semantic')
            environment = (root / 'semantic/theories/Heap_Environment.thy').read_text()
            self.assertIn('definition kept', environment)
            self.assertNotIn('malloc_failure_available', environment)
            self.assertNotIn('by simp', environment)

    def test_native_preflight_preserves_locale_and_has_no_target_facts(self):
        for profile in PROFILES.values():
            with tempfile.TemporaryDirectory() as tmp:
                context = 'context ' + profile['locale'] + ' begin'
                payload = {'contracts': [{'contract_id': 'one', 'exact_statement': 'lemma target: "True"',
                    'contract_owned_context': [context]}]}
                proposal = {'schema_version': 6, 'theories': [{'name': 'One', 'contract_ids': ['one'], 'required_imports': []}]}
                render(payload, proposal, Path(tmp), 'Calg_Project_Base.Project_Input')
                text = (Path(tmp) / 'One.thy').read_text()
                self.assertIn(context, text)
                self.assertIn('lemma target: "True"\n\noops', text)
                self.assertNotIn('sorry', text)

    def test_modern_audit_keeps_simplifier_and_congruence_processes(self):
        old = automation_context_audit_ml(include_wp=False)
        new = automation_context_audit_ml(include_wp=False, modern=True)
        self.assertIn('simps, procs, congs', old)
        self.assertNotIn('Thm_Name.print', old)
        self.assertIn('simprocs = procs, congprocs', new)
        self.assertIn('SIMP_CONG_PROC_LHS', new)
        self.assertIn('Thm_Name.print (Thm.get_name_hint thm)', new)
        self.assertNotIn('WeakestPre', new)


if __name__ == '__main__': unittest.main()
