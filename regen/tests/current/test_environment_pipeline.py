"""Exercise real preparation producers with only external prover calls replaced."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from regen.preparation import environment_pipeline as pipeline
from regen.preparation.environment_dataflow import validate_dataflow
from regen.preparation.environment_gate import sha256_path
from regen.preparation.environment_journal import EnvironmentJournal
from regen.preparation.refine_campaign import read_json


class EnvironmentPipelineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'repo'
        self.repo.mkdir()
        self.root = Path(self.temp.name) / 'environment'
        self.l4v = self.repo / 'verification/l4v'
        arm = self.l4v / 'proof/refine/ARM'
        arm.mkdir(parents=True)
        (self.l4v / 'proof/ROOT').write_text('session Refine in "refine/ARM" = HOL +\n  theories Demo_R\n')
        (arm / 'Demo_R.thy').write_text('''theory Demo_R
imports Main
begin

definition demo where "demo = True"

lemma human_secret_helper: "True"
  by simp

lemma read_corres:
  "corres (=) P Q f g"
  by (rule human_secret_helper)

end
''')
        base = self.l4v / 'proof/refine/base'
        base.mkdir()
        (base / 'Base.thy').write_text('theory Base imports Main begin end\n')
        isabelle = self.repo / 'verification/isabelle'
        isabelle.mkdir()
        (isabelle / 'version').write_text('test-Isabelle')
        (self.repo / 'policy.py').write_text('POLICY = 1\n')
        self.config = self.repo / 'campaign.json'
        self.config.write_text(json.dumps({
            'schema_version': 1, 'campaign_name': 'test-refine', 'architecture': 'ARM',
            'setting': 'Semantic-Base', 'target_stages': [{'stage': 'Refine', 'target_session': 'Refine'}],
            'experimental_claim': 'test', 'trust_boundary': {},
            'contract_selection_policy': {'id': 'operation-corres-v1'}, 'model_backends': [],
            'budgets': {}, 'completion_criteria': {}, 'frozen_input_files': ['policy.py', 'campaign.json'],
            'trusted_source_repositories': [],
            'forbidden_path_policy': {'id': 'deny-human', 'static_forbidden_path_patterns': ['proof/refine/ARM/*.thy']},
        }))
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        subprocess.run(['git', '-C', str(self.repo), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                        'commit', '-qm', 'fixture'], check=True)
        pipeline.initialize(self.root, self.repo, self.config)

    def test_frozen_upstream_cache_seeds_build_and_restores_without_source(self):
        from regen.preparation.upstream_heaps import PLATFORM
        cache = Path(self.temp.name) / 'cache'
        (cache / PLATFORM / 'log').mkdir(parents=True)
        (cache / PLATFORM / 'HOL').write_bytes(b'parent heap')
        (cache / PLATFORM / 'log/HOL.db').write_bytes(b'parent database')
        root = Path(self.temp.name) / 'cached-environment'
        pipeline.initialize(root, self.repo, self.config, upstream_cache=cache)
        def seeded_build(workspace, l4v, heaps, **kwargs):
            self.assertEqual((heaps / PLATFORM / 'HOL').read_bytes(), b'parent heap')
            return self.fake_build(workspace, l4v, heaps, **kwargs)
        with patch.object(pipeline, 'docker_image_identity', return_value={
                'inspect_verdict': 'pass', 'image_id': 'sha256:test'}), \
             patch.object(pipeline, 'run_isabelle_build', side_effect=seeded_build):
            pipeline.run(root, until='build')
        shutil.rmtree(cache)
        shutil.rmtree(root / pipeline.HEAPS)
        pipeline.restore(root)
        self.assertEqual((root / pipeline.HEAPS / PLATFORM / 'HOL').read_bytes(), b'parent heap')

    @staticmethod
    def fake_build(workspace, l4v, heaps, **kwargs):
        heaps.mkdir(parents=True, exist_ok=True)
        (heaps / 'Semantic_Base').write_text('checked heap')
        log = workspace / 'build.log'
        log.write_text('Finished Semantic_Base\n')
        return {'artifact_type': 'semantic-base-isabelle-build', 'verdict': 'pass',
                'session': 'Semantic_Base', 'build_log': {'path': 'build.log', 'sha256': sha256_path(log)},
                'container_image': {'image_id': kwargs['image']}, 'effective_command': ['mock-isabelle'], 'wall_ms': 1}

    @staticmethod
    def fake_export(repo, workspace, l4v, heaps, output, **kwargs):
        raw = output / 'raw-export'
        raw.mkdir(parents=True)
        fields = ['Main.truth', 'Main', 'True', 'Main.truth', 'Main.truth']
        encoded = [item.encode().hex() for item in fields]
        (raw / 'facts.tsv').write_text('FACT\t' + encoded[0] + '\t0\t' + '\t'.join(encoded[1:]) + '\n')
        (raw / 'automation-pools.tsv').write_text('')
        (raw / 'context-metadata.tsv').write_text('META\tnamed_collection\t' + 'wp'.encode().hex() + '\n')
        (output / 'export.log').write_text('exported\n')
        return {'verdict': 'pass', 'facts_sha256': sha256_path(raw / 'facts.tsv'),
                'automation_pools_sha256': sha256_path(raw / 'automation-pools.tsv'),
                'context_metadata_sha256': sha256_path(raw / 'context-metadata.tsv')}

    def execute(self):
        with patch.object(pipeline, 'docker_image_identity', return_value={
            'inspect_verdict': 'pass', 'image_id': 'sha256:test-image'}), \
             patch.object(pipeline, 'run_isabelle_build', side_effect=self.fake_build) as build, \
             patch.object(pipeline.premise_export, 'run_export', side_effect=self.fake_export) as export:
            result = pipeline.run(self.root)
            return result, build.call_count, export.call_count

    def test_complete_pipeline_streams_and_idempotent_resume(self):
        result, builds, exports = self.execute()
        self.assertTrue(result['ready'])
        self.assertEqual((builds, exports), (1, 1))
        index = validate_dataflow(self.root, self.root / pipeline.GATE, self.root / pipeline.INDEX)
        self.assertEqual(index['counts'], {'contracts': 1, 'premises': 1, 'owners': 1})
        public = '\n'.join(p.read_text() for p in (self.root / 'Environment-Dataflow').iterdir())
        self.assertNotIn('human_secret_helper', public)
        self.assertNotIn('source_location', public)
        self.assertNotIn('private/', public)
        contract = json.loads((self.root / 'Environment-Dataflow/contracts.jsonl').read_text())
        self.assertFalse(contract['usable_as_premise'])
        self.assertEqual(contract['proof_status'], 'unproved')
        self.assertFalse(index['policy']['owner_groups_constrain_generated_theories'])
        result, builds, exports = self.execute()
        self.assertTrue(result['ready'])
        self.assertEqual((builds, exports), (0, 0))

    def test_restore_entire_environment_from_journal_and_checkpoints_without_sources(self):
        self.execute()
        identity = read_json(self.root / pipeline.INDEX)['dataflow_id']
        for path in self.root.iterdir():
            if path.name != '.preparation':
                shutil.rmtree(path) if path.is_dir() else path.unlink()
        shutil.rmtree(self.repo)
        (self.root / '.preparation/recipe.json').unlink()
        with patch.object(pipeline, 'run_isabelle_build', side_effect=AssertionError('must not rebuild')):
            recovered = pipeline.restore(self.root)
        self.assertTrue(recovered['ready'])
        self.assertEqual(read_json(self.root / pipeline.INDEX)['dataflow_id'], identity)
        self.assertEqual((self.root / 'Semantic-Base/heaps/Semantic_Base').read_text(), 'checked heap')

    def test_interrupted_build_is_not_reused_and_can_resume(self):
        def interrupted(workspace, l4v, heaps, **kwargs):
            heaps.mkdir(parents=True)
            (workspace / 'build.log').write_text('partial diagnostic')
            raise KeyboardInterrupt('test interrupt')
        with patch.object(pipeline, 'docker_image_identity', return_value={
            'inspect_verdict': 'pass', 'image_id': 'sha256:test-image'}), \
             patch.object(pipeline, 'run_isabelle_build', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                pipeline.run(self.root)
        journal = EnvironmentJournal(self.root)
        self.assertNotIn('build', journal.completed())
        self.assertEqual(journal.events()[-1]['error_type'], 'KeyboardInterrupt')
        self.assertTrue(self.execute()[0]['ready'])
        remnants = list((self.root / '.preparation/incomplete').rglob('build.log'))
        self.assertEqual(len(remnants), 1)
        self.assertEqual(remnants[0].read_text(), 'partial diagnostic')

    def test_drift_is_refused_then_explicit_restore_preserves_changed_file(self):
        self.execute()
        heap = self.root / 'Semantic-Base/heaps/Semantic_Base'
        heap.write_text('changed heap')
        with self.assertRaisesRegex(ValueError, 'artifact drift'):
            pipeline.run(self.root)
        self.assertTrue(pipeline.restore(self.root)['ready'])
        self.assertEqual(heap.read_text(), 'checked heap')
        quarantined = list((self.root / '.preparation/incomplete').rglob('Semantic_Base'))
        self.assertEqual([p.read_text() for p in quarantined], ['changed heap'])

    def test_resume_restores_missing_child_without_rebuild(self):
        self.execute()
        missing = self.root / 'Semantic-Base/heaps/Semantic_Base'
        missing.unlink()
        result, builds, exports = self.execute()
        self.assertTrue(result['ready'])
        self.assertEqual((builds, exports), (0, 0))
        self.assertEqual(missing.read_text(), 'checked heap')
        self.assertTrue(any(e['event'] == 'stage-reused' for e in EnvironmentJournal(self.root).events()))

    def test_corrupt_checkpoint_does_not_restore(self):
        pipeline.run(self.root, until='freeze')
        checkpoint = EnvironmentJournal(self.root).completed()['freeze']['checkpoint']
        backup = self.root / '.preparation' / Path(checkpoint['path']).parent / 'files' / pipeline.MANIFEST
        backup.write_text('{}')
        (self.root / pipeline.MANIFEST).unlink()
        with self.assertRaisesRegex(ValueError, 'checkpoint content drift'):
            pipeline.restore(self.root)
        self.assertFalse((self.root / pipeline.MANIFEST).exists())

    def test_input_drift_stops_resume_and_logs_diagnostic(self):
        pipeline.run(self.root, until='freeze')
        (self.repo / 'policy.py').write_text('POLICY = 2\n')
        with self.assertRaisesRegex(ValueError, 'frozen input drift'):
            pipeline.run(self.root)
        self.assertEqual(EnvironmentJournal(self.root).events()[-1]['event'], 'input-validation-failed')
        self.assertNotIn('inventory', EnvironmentJournal(self.root).completed())

    def test_torn_journal_tail_can_be_recovered_but_hash_change_cannot(self):
        pipeline.run(self.root, until='freeze')
        journal = EnvironmentJournal(self.root)
        with journal.log.open('ab') as stream:
            stream.write(b'{"sequence":')
        with self.assertRaisesRegex(ValueError, 'incomplete journal tail'):
            pipeline.run(self.root, until='freeze')
        self.assertEqual(pipeline.restore(self.root)['next_stage'], 'inventory')
        self.assertTrue(list(journal.private.glob('torn-tail-*.bin')))
        text = journal.log.read_text().replace('stage-completed', 'stage-corrupted', 1)
        journal.log.write_text(text)
        with self.assertRaisesRegex(ValueError, 'identity/sequence mismatch'):
            pipeline.restore(self.root)

    def test_private_recovery_directory_and_exclusive_lock(self):
        journal = EnvironmentJournal(self.root)
        self.assertEqual(journal.private.stat().st_mode & 0o777, 0o700)
        with journal.lock():
            with self.assertRaisesRegex(ValueError, 'holds the lock'):
                pipeline.run(self.root, until='freeze')

    def test_failed_build_does_not_publish_gate_or_dataflow(self):
        def failed(*args, **kwargs):
            result = self.fake_build(*args, **kwargs)
            return {**result, 'verdict': 'fail', 'diagnostic': 'Unknown constant demo'}
        with patch.object(pipeline, 'docker_image_identity', return_value={
            'inspect_verdict': 'pass', 'image_id': 'sha256:test-image'}), \
             patch.object(pipeline, 'run_isabelle_build', side_effect=failed):
            with self.assertRaisesRegex(ValueError, 'Unknown constant demo'):
                pipeline.run(self.root)
        self.assertFalse((self.root / pipeline.GATE).exists())
        self.assertFalse((self.root / pipeline.INDEX).exists())

    def test_public_stream_tampering_is_rejected(self):
        self.execute()
        path = self.root / 'Environment-Dataflow/contracts.jsonl'
        value = json.loads(path.read_text())
        value['usable_as_premise'] = True
        path.write_text(json.dumps(value) + '\n')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            validate_dataflow(self.root, self.root / pipeline.GATE, self.root / pipeline.INDEX)

    def test_symlink_escape_is_rejected_before_snapshot(self):
        journal = EnvironmentJournal(self.root)
        output = self.root / 'link-output'
        output.mkdir()
        (output / 'raw-proof').symlink_to(self.l4v / 'proof/refine/ARM/Demo_R.thy')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            journal.checkpoint('test', ['link-output'])
