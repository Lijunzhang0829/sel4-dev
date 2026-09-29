"""Continuations retain verified frozen provider closure, never projections alone."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.execution import repository_execution as execution
from regen.execution.proof_commits import durable, digest
from regen.execution.fact_context import manifest
from regen.planning.proof_graph import build_graph
from regen.tests.current.test_proof_commits import fixture, check


class ContinuationProvidersTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.source = self.root/'source'
        self.source.mkdir()
        self.run = {'run_id':'source', 'implementation':{}, 'session_schedule':{'source':'schedule'},
                    'environment_run':str(self.root), 'contract_package_dir':str(self.source/'packages')}
        self.packages = {}; self.files = []
        for name in ('Base', 'Provider', 'Target'):
            package, _ = fixture(False)
            package.update(theory=name, file_slot=name+'.thy')
            package['package_payload_sha256'] = digest(package)
            self.packages[name] = package
            self.files.append({'theory':name, 'file_slot':name+'.thy'})
            durable(self.source/'packages'/(name+'.thy.json'), package)
        durable(self.source/'execution-input.json', {'files':self.files, 'package_hashes':{
            p['file_slot']:p['package_payload_sha256'] for p in self.packages.values()}})
        durable(self.source/'run.json', self.run)
        for name, imports in [('Base', []), ('Provider', ['Regen_Base']), ('Target', ['Regen_Provider'])]:
            p = self.packages[name]
            _, base = fixture(False)
            graph = execution.context_graph(build_graph(p, {'generated_nodes':base['graph']['nodes']},
                                                       run_id='source', revision=1), [])
            state = {**base, 'graph':graph, 'batch_requests':319,
                     'batch_notes':'preserve memory', 'batch_facts':[]}
            d = self.source/'theories'/name
            durable(d/'imports.json', imports)
            journal = execution.journal(self.source, self.run, p)
            journal.append('initial', state, {'check':check()})
            green = deepcopy(state)
            green.update(proofs={'helper':'by simp'}, accepted_order=['helper'])
            green['graph'] = execution.context_graph(graph, ['helper'])
            journal.append('green', green, {'node_id':'helper', 'check':check(),
                'sealable_generation':True, 'visibility':manifest(graph, {}, 'helper')})
            if name != 'Target':
                sealed = deepcopy(green); sealed.update(status='sealed', seal_id='seal:'+name)
                journal.append('seal', sealed, {'impact':{'verdict':'pass'},
                    'dependencies':{'verdict':'green'}, 'complete':check(), 'build':{'verdict':'green'},
                    'source_text':'theory '+name, 'source_hash':digest('theory '+name)})
        durable(self.source/'theories/Target/batch-requests/000319/feedback.json', {'status':'available'})

    def initialize(self, destination, *_args):
        import shutil
        destination.mkdir()
        shutil.copytree(self.source/'packages', destination/'packages')
        shutil.copy2(self.source/'execution-input.json', destination/'execution-input.json')
        run = {**self.run, 'run_id':destination.name, 'contract_package_dir':str(destination/'packages')}
        durable(destination/'run.json', run)
        return run

    def fork(self):
        destination = self.root/'continuation'
        with patch.object(execution, 'init_run', side_effect=self.initialize), \
             patch.object(execution, 'progress', return_value={}):
            run = execution.fork_continuation(self.source, destination, 'Target')
        return destination, run

    def test_transitive_seals_and_target_costs_memory_survive(self):
        destination, run = self.fork()
        for name in ('Base', 'Provider', 'Target'):
            state = execution.journal(destination, run, self.packages[name]).replay()
            self.assertEqual(state['proofs'], {'helper':'by simp'})
            self.assertEqual(state['batch_requests'], 319)
            self.assertEqual(state['batch_notes'], 'preserve memory')
            self.assertEqual(state['status'], 'generating' if name == 'Target' else 'sealed')
        reuse = json.loads((destination/'sealed-reuse.json').read_text())
        self.assertEqual([x['theory'] for x in reuse['theories']], ['Base', 'Provider'])
        self.assertTrue((destination/'theories/Target/batch-requests/000319/feedback.json').exists())

    def test_workspace_continuation_carries_all_verified_states(self):
        destination = self.root/'workspace-continuation'
        durable(self.source/'theories/Target/provider-session.json', {'imports':['Regen_Provider']})
        with patch.object(execution, 'init_run', side_effect=self.initialize), \
             patch.object(execution, 'progress', return_value={}):
            run = execution.fork_workspace_continuation(self.source,destination)
        for name in ('Base','Provider','Target'):
            state = execution.journal(destination,run,self.packages[name]).replay()
            self.assertEqual(state['proofs'],{'helper':'by simp'})
            self.assertEqual(state['status'],'generating' if name=='Target' else 'sealed')
        record=json.loads((destination/'workspace-continuation.json').read_text())
        self.assertEqual(record['sealed_theories'],['Base','Provider'])
        self.assertEqual([x['theory'] for x in record['theories']],['Target'])
        self.assertTrue((destination/'theories/Target/batch-requests/000319/feedback.json').exists())
        self.assertFalse((destination/'theories/Target/provider-session.json').exists())

    def test_missing_transitive_seal_rejected_before_new_run(self):
        (self.source/'theories/Base/acceptance/commits/000003.json').unlink()
        with self.assertRaisesRegex(ValueError, 'terminal seal'):
            self.fork()
        self.assertFalse((self.root/'continuation').exists())

    def test_checkpoint_uses_prefix_and_does_not_copy_latest_files(self):
        selection = {'source_run':str(self.source), 'theories':{}}
        for name in ('Base','Provider','Target'):
            paths = sorted((self.source/'theories'/name/'acceptance/commits').glob('*.json'))
            chosen = paths[0] if name == 'Target' else paths[-1]
            selection['theories'][name] = {'commit_id':json.loads(chosen.read_text())['commit_id']}
        durable(self.source/'theories/Target/provider-session.json', {'imports':['Regen_Provider']})
        destination = self.root/'checkpoint'
        with patch.object(execution, 'init_run', side_effect=self.initialize), \
             patch.object(execution, 'progress', return_value={}):
            run = execution.fork_workspace_continuation(self.source,destination,checkpoint=selection)
        state = execution.journal(destination,run,self.packages['Target']).replay()
        self.assertEqual(state['proofs'], {})
        self.assertEqual(state['batch_requests'],319)
        self.assertFalse((destination/'theories/Target/provider-session.json').exists())
        self.assertFalse((destination/'theories/Target/batch-requests').exists())

    def test_checkpoint_rejects_missing_dependency_before_creating_run(self):
        paths = sorted((self.source/'theories/Target/acceptance/commits').glob('*.json'))
        selection = {'source_run':str(self.source), 'theories':{'Target':{
            'commit_id':json.loads(paths[-1].read_text())['commit_id']}}}
        destination = self.root/'checkpoint'
        with self.assertRaisesRegex(ValueError,'frozen dependency'):
            execution.fork_workspace_continuation(self.source,destination,checkpoint=selection)
        self.assertFalse(destination.exists())

    def test_tampered_provider_evidence_rejected_before_new_run(self):
        path = self.source/'theories/Provider/acceptance/commits/000003.json'
        data = json.loads(path.read_text()); data['evidence']['build']['verdict'] = 'red'
        durable(path, data)
        with self.assertRaisesRegex(ValueError, 'source chain'):
            self.fork()
        self.assertFalse((self.root/'continuation').exists())

    def test_unknown_frozen_import_rejected(self):
        with self.assertRaisesRegex(ValueError, 'missing sealed provider'):
            execution._sealed_provider_inputs(self.source, self.run, ['Regen_Unknown'])
