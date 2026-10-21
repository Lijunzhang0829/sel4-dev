"""Crash recovery and immutable-proof checks for the isolated experiment journal."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from regen.tools.proof_lab_journal import replay_lab, state_digest


class LabJournalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.lab = Path(self.temp.name)
        (self.lab / 'acceptance').mkdir()
        node = {'id': 'c', 'name': 'c', 'role': 'contract',
                'statement': 'lemma c: True', 'context': [], 'dependencies': []}
        self.initial = {'graph': {'nodes': [node], 'accepted_order': []}, 'proofs': {}}
        self.old = {**copy.deepcopy(self.initial), 'accepted': []}
        self.new = copy.deepcopy(self.old)
        self.new['proofs']['c'] = 'by simp'
        self.new['graph']['accepted_order'] = ['c']
        self.new['accepted'] = ['001.json']
        self.record = {'previous_state_sha256': state_digest(self.old),
                       'state': self.new,
                       'result': {'request': {'node_id': 'c', 'proof_body': 'by simp'},
                                  'check': {'verdict': 'green', 'speculative': False}}}
        self.write('initial.json', self.initial)
        self.write('acceptance/001.json', self.record)
        self.write('state.json', self.new)

    def write(self, name, data):
        (self.lab / name).write_text(json.dumps(data))

    def test_missing_or_stale_pointer_recovers_from_commits(self):
        for missing in (False, True):
            if missing:
                (self.lab / 'state.json').unlink()
            else:
                self.write('state.json', self.old)
            self.assertEqual(replay_lab(self.lab, check_pointer=False), self.new)
        self.write('state.json', self.old)
        with self.assertRaisesRegex(ValueError, 'pointer'):
            replay_lab(self.lab)

    def test_uncommitted_pointer_not_authoritative(self):
        (self.lab / 'acceptance/001.json').unlink()
        self.assertEqual(replay_lab(self.lab, check_pointer=False), self.old)

    def test_speculative_green_and_changed_contract_rejected(self):
        for mutation in ('speculative', 'statement'):
            record = copy.deepcopy(self.record)
            if mutation == 'speculative':
                record['result']['check']['speculative'] = True
            else:
                record['state']['graph']['nodes'][0]['statement'] = 'lemma c: False'
            self.write('acceptance/001.json', record)
            with self.assertRaises(ValueError):
                replay_lab(self.lab, check_pointer=False)

    def test_fork_rejected(self):
        self.write('acceptance/002.json', self.record)
        with self.assertRaisesRegex(ValueError, 'forked'):
            replay_lab(self.lab)

    def test_replayed_proof_matches_checked_candidate(self):
        self.record['state']['proofs']['c'] = 'by blast'
        self.write('acceptance/001.json', self.record)
        with self.assertRaisesRegex(ValueError, 'candidate'):
            replay_lab(self.lab, check_pointer=False)


if __name__ == '__main__':
    unittest.main()
