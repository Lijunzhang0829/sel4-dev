"""Workspace forks keep one verified origin alive, not the whole repository."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import weakref

from regen.execution import repository_execution as execution
from regen.execution.proof_commits import durable
from regen.tests.current import test_continuation_providers as fixtures


class TrackedRecord(dict):
    pass


class ContinuationMemoryTests(unittest.TestCase):
    setUp = fixtures.ContinuationProvidersTest.setUp
    initialize = fixtures.ContinuationProvidersTest.initialize

    def fork(self, source=None, destination=None, *, checkpoint=None, initialize=None):
        source = source or self.source
        destination = destination or self.root/'bounded-continuation'
        with patch.object(execution, 'init_run', side_effect=initialize or self.initialize), \
             patch.object(execution, 'progress', return_value={}):
            run = execution.fork_workspace_continuation(source, destination, checkpoint=checkpoint)
        return destination, run

    def test_each_origin_is_released_before_loading_the_next_theory(self):
        real_read = execution.read
        observed = {}
        previous = None
        source_reads = []

        def watched_read(path):
            nonlocal previous
            path = Path(path)
            if path.parent.name != 'commits' or not path.is_relative_to(self.source):
                return real_read(path)
            owner = path.parents[2].name
            if previous is not None and owner != previous:
                self.assertTrue(all(ref() is None for ref in observed[previous]),
                                'previous theory origin survived into the next read')
            previous = owner
            record = TrackedRecord(real_read(path))
            observed.setdefault(owner, []).append(weakref.ref(record))
            source_reads.append((owner, path.name))
            return record

        def initialize(*args):
            self.assertTrue(all(ref() is None for refs in observed.values() for ref in refs),
                            'preflight retained raw origin records')
            return self.initialize(*args)

        with patch.object(execution, 'read', new=watched_read):
            self.fork(initialize=initialize)
        self.assertTrue(all(ref() is None for refs in observed.values() for ref in refs))
        # Neither phase promotes the descriptor to authority: both read the full
        # selected source chain, and journal.append independently checks it again.
        for owner in self.packages:
            paths = list((self.source/'theories'/owner/'acceptance/commits').glob('*.json'))
            for path in paths:
                self.assertEqual(source_reads.count((owner, path.name)), 2)

    def test_nested_workspace_forks_preserve_proofs_costs_seals_and_memory(self):
        source = self.source
        expected = {name:deepcopy(execution.journal(source, self.run, package).replay())
                    for name, package in self.packages.items()}
        for depth in range(3):
            source, run = self.fork(source, self.root/('nested-'+str(depth)))
            for name, package in self.packages.items():
                state = execution.journal(source, run, package).replay()
                for field in ('graph', 'proofs', 'accepted_order', 'rounds', 'revisions',
                              'batch_requests', 'batch_notes', 'batch_facts'):
                    self.assertEqual(state[field], expected[name][field])
                self.assertEqual(state['status'], 'generating' if name == 'Target' else 'sealed')
                if name != 'Target':
                    self.assertEqual(state['seal_id'], expected[name]['seal_id'])
            self.assertTrue((source/'theories/Target/batch-requests/000319/feedback.json').exists())
            reuse = execution.read(source/'sealed-reuse.json')
            self.assertEqual([r['theory'] for r in reuse['theories']], ['Base', 'Provider'])

    def test_changed_commit_between_preflight_and_copy_is_rejected(self):
        path = self.source/'theories/Base/acceptance/commits/000003.json'

        def initialize(*args):
            run = self.initialize(*args)
            record = execution.read(path)
            record['evidence']['build']['verdict'] = 'red'
            durable(path, record)
            return run

        with self.assertRaisesRegex(ValueError, 'changed after preflight'):
            self.fork(initialize=initialize)
        self.assertFalse((self.root/'bounded-continuation/theories/Base/acceptance/commits').exists())

    def test_changed_imports_between_phases_are_rejected(self):
        def initialize(*args):
            run = self.initialize(*args)
            durable(self.source/'theories/Base/imports.json', ['Regen_Unknown'])
            return run

        with self.assertRaises(ValueError):
            self.fork(initialize=initialize)
        self.assertFalse((self.root/'bounded-continuation/theories/Base/acceptance/commits').exists())

    def test_bad_last_theory_is_rejected_before_creating_destination(self):
        path = self.source/'theories/Target/acceptance/commits/000002.json'
        record = execution.read(path)
        record['state']['batch_requests'] = 0
        durable(path, record)
        with self.assertRaisesRegex(ValueError, 'source chain'):
            self.fork()
        self.assertFalse((self.root/'bounded-continuation').exists())

    def test_checkpoint_does_not_decode_records_after_selected_prefix(self):
        checkpoint = {'source_run':str(self.source), 'theories':{}}
        excluded = self.source/'theories/Target/acceptance/commits/000002.json'
        for name in self.packages:
            paths = sorted((self.source/'theories'/name/'acceptance/commits').glob('*.json'))
            selected = paths[0] if name == 'Target' else paths[-1]
            checkpoint['theories'][name] = {'commit_id':execution.read(selected)['commit_id']}
        real_read = execution.read

        def selected_read(path):
            self.assertNotEqual(Path(path), excluded, 'checkpoint decoded a future record')
            return real_read(path)

        with patch.object(execution, 'read', new=selected_read):
            destination, run = self.fork(checkpoint=checkpoint)
        state = execution.journal(destination, run, self.packages['Target']).replay()
        self.assertEqual(state['proofs'], {})
        self.assertEqual(state['batch_requests'], 319)
        self.assertFalse((destination/'theories/Target/batch-requests').exists())

    def test_missing_checkpoint_dependency_is_rejected_before_new_run(self):
        path = self.source/'theories/Provider/acceptance/commits/000003.json'
        checkpoint = {'source_run':str(self.source), 'theories':{'Provider':{
            'commit_id':json.loads(path.read_text())['commit_id']}}}
        with self.assertRaisesRegex(ValueError, 'sealed dependency'):
            self.fork(checkpoint=checkpoint)
        self.assertFalse((self.root/'bounded-continuation').exists())


if __name__ == '__main__':
    unittest.main()
