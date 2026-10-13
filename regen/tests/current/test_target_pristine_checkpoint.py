"""Pure audit tests for target-pristine checkpoint construction."""

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import digest, durable
from regen.execution import repository_execution as execution
from regen.infrastructure.incremental_log import enable
from regen.tools import target_pristine_checkpoint as checkpoint
from regen.tools import prepared_checkpoint
from regen.tools.prepared_checkpoint import inventory


ROLES = {
    'File Planner': 'gpt-6-astra',
    'Graph Revision Agent': 'gpt-6-astra',
    'Lemma Prover': 'gpt-6-astra',
}


def native_declaration_check(index):
    source = '\n'.join(node['statement'] + '\n  oops'
                       for node in index['nodes'])
    log = 'synthetic declaration-only unit-test evidence'
    return {
        'verdict': 'green', 'returncode': 0, 'diagnostic': '',
        'source_content': source,
        'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
        'log_content': log,
        'log_sha256': hashlib.sha256(log.encode()).hexdigest(),
    }


class Checker:
    def __init__(self):
        self.closed = False

    def check_declarations(self, index):
        return native_declaration_check(index)

    def close_prefix(self):
        self.closed = True


class TargetPristineCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root/'source'
        self.source.mkdir()

    def package(self, theory, imports=()):
        body = {
            'theory': theory,
            'file_slot': theory + '.thy',
            'repository_projection': {
                'schema_version': 5,
                'required_imports': [{'theory': name} for name in imports],
            },
            'contracts': [{
                'contract_id': 'contract:' + theory,
                'name': 'target_' + theory,
                'exact_statement': 'lemma target_' + theory + ': "True"',
                'contract_owned_context': [],
            }],
        }
        return {**body, 'package_payload_sha256': digest(body)}

    def source_run(self, packages):
        package_dir = self.source/'packages'
        rows = [{'theory': p['theory'], 'file_slot': p['file_slot']}
                for p in packages]
        inputs = {
            'files': rows,
            'package_hashes': {p['file_slot']: p['package_payload_sha256']
                               for p in packages},
        }
        run = {
            'schema_version': 2,
            'execution_mode': 'repository-projection-v1',
            'execution_input_kind': 'refine-dataset',
            'run_id': 'source',
            'checker_context_root': str(self.root/'context'),
            'checker_context_id': 'context:test',
            'premise_database': str(self.root/'premises.sqlite'),
            'premise_snapshot': str(self.root/'premises.json'),
            'semantic_workspace': str(self.root/'semantic'),
            'l4v': str(self.root/'l4v'),
            'base_session': 'Semantic_Base',
            'base_import': 'Semantic_Base.Semantic_Root',
            'base_catalog': '/semantic-base',
            'isabelle_dir': str(self.root/'isabelle'),
            'image': 'sha256:test',
            'isabelle_sha256': 'isabelle:test',
            'implementation': {},
            'execution_models': dict(ROLES),
            'contract_package_dir': str(package_dir),
            'session_progress_ledger': str(self.source/'progress.json'),
            'session_schedule': {
                'source': str(self.root/'schedule.json'),
                'file_list_sha256': 'listing:test',
                'plan_id': 'plan:test',
                'plan_revision': 1,
            },
        }
        durable(self.source/'run.json', run)
        durable(self.source/'execution-input.json', inputs)
        for package in packages:
            durable(package_dir/(package['file_slot']+'.json'), package)
        return run, inputs

    def model_patch(self):
        return patch('regen.infrastructure.audited_llm.model_settings',
                     return_value={'reasoning_effort': 'medium',
                                   'role_models': dict(ROLES)})

    def test_full_prepare_replays_one_empty_initial_and_ignores_target_history(self):
        package = self.package('Target')
        source_run, inputs = self.source_run([package])
        # If copied or scanned as target input this sentinel would violate the
        # checkpoint's own no-LLM-history invariant.
        durable(self.source/'theories/Target/llm/historical-proof.json',
                {'proof_body': 'forbidden oracle'})
        destination = self.root/'checkpoint'
        holder = {}

        def initialize(path, _input, run_id, _schedule, **options):
            self.assertTrue(path.is_dir())
            shutil.copytree(self.source/'packages', path/'packages')
            durable(path/'execution-input.json', inputs)
            run = {**source_run, 'run_id': run_id,
                   'contract_package_dir': str(path/'packages'),
                   'session_progress_ledger': str(path/'progress.json'),
                   'execution_models': options['execution_models']}
            durable(path/'run.json', run)
            (path/'generated').mkdir()
            holder['run'] = run
            return run

        checker = Checker()

        def setup(path, theory, _timeout):
            run = holder['run']
            directory = path/'theories'/theory
            durable(directory/'imports.json', [], immutable=True)
            journal = execution.journal(path, run, package)
            return run, package, directory, journal, checker, []

        def progress(path):
            commits = path/'theories/Target/acceptance/commits'
            status = 'planned' if commits.is_dir() and any(commits.iterdir()) else 'pending'
            return {'files': [{'theory': 'Target', 'status': status,
                               'checked_contracts': 0}]}

        with self.model_patch(), \
             patch.object(checkpoint, 'init_run', side_effect=initialize), \
             patch.object(checkpoint, '_reuse_sealed_theories'), \
             patch.object(checkpoint, 'setup', side_effect=setup), \
             patch.object(checkpoint, 'progress', side_effect=progress):
            result = checkpoint.prepare(
                self.source, destination, theory='Target',
                target_contract_id='contract:Target')

        self.assertTrue(checker.closed)
        self.assertEqual(result['accepted_proofs'], 0)
        self.assertEqual(result['scaffold_scope'], 'full-fixed-theory')
        self.assertEqual(result['execution_models'], ROLES)
        self.assertFalse((destination/checkpoint._OWNERSHIP_MARKER).exists())
        self.assertFalse((destination/'llm').exists())
        commits = sorted((destination/'theories/Target/acceptance/commits').glob('*.json'))
        self.assertEqual([p.name for p in commits], ['000001.json'])
        record = json.loads(commits[0].read_text())
        self.assertEqual(record['event'], 'initial')
        self.assertEqual(record['state']['proofs'], {})
        self.assertEqual(record['state']['accepted_order'], [])
        self.assertEqual(set(record['evidence']), {'check'})
        provenance_pin = checkpoint._sha256(destination/'target-pristine.json')
        self.assertEqual(checkpoint.verify(destination, provenance_pin), result)
        files = inventory(destination)
        self.assertEqual(
            files['target-pristine.json']['sha256'], provenance_pin)

        fixture = self.root/'fixture'
        arm = self.root/'arm'
        with patch.object(prepared_checkpoint, 'application_binding',
                          return_value={'regen/application.py': 'a'*64}), \
             patch.object(prepared_checkpoint, 'external_binding',
                          return_value={'files': {}, 'source_trees': {}}):
            frozen = prepared_checkpoint.prepare_existing_pristine(
                destination, provenance_pin, fixture)
            manifest = prepared_checkpoint.verify(
                fixture, frozen['manifest_sha256'])
            instance = prepared_checkpoint.derive(
                fixture, arm,
                expected_manifest_sha256=frozen['manifest_sha256'])
        self.assertEqual(
            manifest['pristine_provenance_sha256'], provenance_pin)
        self.assertEqual(
            manifest['pristine_semantic_receipt']['initial_commit_id'],
            result['initial_commit_id'])
        self.assertEqual(instance['fixture_manifest_sha256'],
                         frozen['manifest_sha256'])
        self.assertNotIn('pristine_provenance_sha256', instance)
        arm_record = json.loads(
            (arm/'theories/Target/acceptance/commits/000001.json').read_text())
        self.assertEqual(arm_record['state']['proofs'], {})

    def test_target_in_provider_projection_is_rejected_before_history_reader(self):
        package = self.package('Target', imports=('Target',))
        self.source_run([package])
        destination = self.root/'checkpoint'
        with self.model_patch(), \
             patch.object(checkpoint, '_sealed_provider_inputs') as history:
            with self.assertRaisesRegex(ValueError, 'target theory cannot appear'):
                checkpoint.prepare(self.source, destination, theory='Target',
                                   target_contract_id='contract:Target')
        history.assert_not_called()
        self.assertFalse(destination.exists())

    def test_provider_import_history_must_equal_frozen_projection(self):
        provider = self.package('Provider')
        target = self.package('Target', imports=('Provider',))
        run, data = self.source_run([provider, target])
        origin = {'imports': ['Regen_Unexpected'], 'commits': [{
            'event': 'seal', 'commit_id': 'provider-commit',
            'state': {'status': 'sealed', 'seal_id': 'provider-seal'},
        }]}
        prepared = [(provider, origin, {'proofs': {}})]
        with patch.object(checkpoint, '_provider_history_paths'), \
             patch.object(checkpoint, '_sealed_provider_inputs',
                          return_value=prepared):
            with self.assertRaisesRegex(ValueError, 'differ from frozen projection'):
                checkpoint._verified_provider_inputs(
                    self.source, run, target, data)

    def test_partial_initialization_is_removed_only_from_owned_destination(self):
        package = self.package('Target')
        self.source_run([package])
        destination = self.root/'checkpoint'

        def fail(path, *_args, **_kwargs):
            durable(path/'partial.json', {'allocated': True})
            raise RuntimeError('synthetic initialization failure')

        with self.model_patch(), patch.object(checkpoint, 'init_run', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'synthetic initialization failure'):
                checkpoint.prepare(self.source, destination, theory='Target',
                                   target_contract_id='contract:Target')
        self.assertFalse(destination.exists())

    def test_reasoning_effort_is_frozen_before_destination_creation(self):
        package = self.package('Target')
        self.source_run([package])
        destination = self.root/'checkpoint'
        with self.model_patch():
            with self.assertRaisesRegex(ValueError, 'reasoning effort differs'):
                checkpoint.prepare(self.source, destination, theory='Target',
                    target_contract_id='contract:Target', reasoning_effort='high')
        self.assertFalse(destination.exists())

    def test_owned_destination_is_compatible_with_merkle_log_initialization(self):
        destination = self.root/'checkpoint'
        token = checkpoint._claim_destination(destination)
        enable(destination)
        self.assertTrue((destination/'log-storage.json').is_file())
        checkpoint._cleanup_destination(destination, token)
        self.assertFalse(destination.exists())


if __name__ == '__main__':
    unittest.main()
