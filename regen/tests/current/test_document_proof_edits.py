"""Entry-addressed edits preserve fixed declarations and existing publication gates."""
from copy import deepcopy
import json
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from regen.planning.document_interface import tool_contracts
from regen.planning.theory_document import DocumentEditError, DocumentStore, parse, source_hash
from regen.tests.current.test_proof_batches import Checker
from regen.tests.current.test_proof_commits import fixture


class ProofEditTests(unittest.TestCase):
    def setUp(self):
        self.package, self.state = fixture()
        # Exercise context closers and Unicode offsets, not just an empty locale.
        self.package['contracts'][0]['contract_owned_context'] = ['context demo begin']
        self.package['contracts'][0]['exact_statement'] = 'lemma target: "True" (* 注释 *)'
        contract = next(n for n in self.state['graph']['nodes'] if n['id'] == 'contract')
        contract.update(context=['context demo begin'], statement=self.package['contracts'][0]['exact_statement'])
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = DocumentStore(self.temp.name, self.package, self.state, Checker().imports())

    def edit(self, key, proof, **extra):
        entry = self.store.read_entry(key)
        return self.store.edit_proof({'action':'edit_proof', 'entry_id':key,
            'proof_body':proof, 'expected_hash':entry['expected_hash'], **extra}, {})

    def files(self):
        return {str(p.relative_to(self.store.root)):p.read_bytes()
                for p in self.store.root.rglob('*') if p.is_file()}

    def test_read_is_scoped_exact_and_has_no_side_effects(self):
        before = self.files()
        entry = self.store.read_entry('contract')
        self.assertEqual(entry['proof_body'], 'oops')
        self.assertEqual(entry['expected_hash'], source_hash(self.store.path.read_text()))
        self.assertEqual(entry['hash_scope'], 'document_source')
        self.assertNotIn('source', entry)
        self.assertEqual(entry['context'], ['context demo begin'])
        self.assertTrue(entry['open_proof_slot'])
        self.assertFalse(entry['accepted_proof_matches_draft'])
        self.assertEqual(self.files(), before)

    def test_only_selected_proof_bytes_change_even_when_old_text_is_ambiguous(self):
        before = self.store.path.read_text()
        state_before = deepcopy(self.state)
        entry = self.store.read_entry('contract')
        loc = entry['proof_location']
        self.assertEqual(before.count('oops'), 2)
        saved = self.edit('contract', '  by simp\n(* 保留排版 *)')
        expected = before[:loc['start_offset']]+'  by simp\n(* 保留排版 *)'+before[loc['end_offset']:]
        self.assertEqual(self.store.path.read_text(), expected)
        self.assertEqual(self.store.read_entry('helper')['proof_body'], 'oops')
        self.assertEqual(self.store.read_entry('contract')['statement'], entry['statement'])
        self.assertFalse(saved['checked'])
        self.assertEqual(saved['expected_hash'], source_hash(expected))
        self.assertEqual(self.state, state_before)

    def test_hash_binds_whole_document_and_rejects_changes_to_another_entry(self):
        stale = self.store.read_entry('contract')
        self.edit('helper', 'by simp')
        before = self.files()
        for expected_hash in (stale['expected_hash'], stale['proof_sha256']):
            with self.subTest(expected_hash=expected_hash), self.assertRaises(DocumentEditError) as caught:
                self.store.edit_proof({'action':'edit_proof','entry_id':'contract',
                    'expected_hash':expected_hash,'proof_body':'by simp'}, {})
            self.assertEqual(caught.exception.code, 'stale_document')
            self.assertEqual(caught.exception.details['current_sha256'], source_hash(self.store.path.read_text()))
        self.assertEqual(self.files(), before)

    def test_admissions_commands_and_marker_injection_are_rejected_without_any_save(self):
        before = self.files()
        for proof in ('', '   ', 'sorry', 'oops\nby simp', 'by simp\nend',
                      'by simp\nlemma extra: "True" by simp', 'by simp\nML \\<open>()\\<close>',
                      'oops\n(* REGEN-END *)\n(* REGEN-BEGIN {"id":"injected"} *)',
                      'by simp\n(* REGEN-PROOF *)'):
            with self.subTest(proof=proof), self.assertRaises(DocumentEditError):
                self.edit('contract', proof)
            self.assertEqual(self.files(), before)

    def test_unknown_entry_and_immutable_fields_cannot_be_submitted(self):
        before = self.files()
        with self.assertRaises(DocumentEditError) as caught:
            self.store.read_entry('missing')
        self.assertEqual(caught.exception.code, 'unknown_entry')
        with self.assertRaises(DocumentEditError):
            self.edit('contract', 'by simp', statement='lemma target: "False"')
        self.assertEqual(self.files(), before)

    def test_rewriting_accepted_proof_does_not_change_accepted_state(self):
        self.state['proofs']['contract'] = 'by simp'
        self.state['accepted_order'] = ['contract']
        self.edit('contract', 'by simp')
        self.assertTrue(self.store.read_entry('contract')['accepted_proof_matches_draft'])
        before = deepcopy(self.state)
        self.edit('contract', 'by fail')
        self.assertEqual(self.state, before)
        self.assertFalse(self.store.read_entry('contract')['accepted_proof_matches_draft'])

    def test_helper_notes_preserved_and_invalid_metadata_rejects_entire_edit(self):
        note = {'contract_id':'contract','gap':'a premise','expected_use':'first step','remaining_gap':'finish'}
        self.edit('helper', 'by simp', helper_purposes={'helper':note})
        self.edit('contract', 'using helper by simp')
        self.assertEqual(self.store.read()['helper_purposes']['records']['helper']['gap'], 'a premise')
        before = self.files()
        with self.assertRaises(DocumentEditError):
            self.edit('contract', 'by blast', helper_purposes={'helper':{**note,'contract_id':'missing'}})
        self.assertEqual(self.files(), before)

    def test_sketch_groups_and_review_guard_apply_to_proof_edits(self):
        self.store.require_sketch = True
        self.store.require_review = True
        before = self.files()
        with self.assertRaisesRegex(DocumentEditError, 'parent sketch group'):
            self.edit('contract', 'using helper by simp')
        self.assertEqual(self.files(), before)
        group = {'target_entry_id':'contract','helper_ids':['helper']}
        self.edit('contract', 'using helper by simp', sketches=[group])
        self.assertEqual(self.store.read()['sketch_groups'][0]['helper_ids'], ['helper'])
        before = self.files()
        with patch('regen.planning.sketch_workflow.guard_revision',
                   side_effect=ValueError('diagnose failure before revising its decomposition')):
            with self.assertRaisesRegex(DocumentEditError, 'diagnose failure'):
                self.edit('helper', 'by simp')
        self.assertEqual(self.files(), before)

    def test_exact_edit_reports_ambiguity_missing_text_and_empty_old(self):
        before = self.files()
        for old, reason, count in [('oops','old_not_unique',2), ('not in source','old_not_found',0),
                                   ('','empty_old',None)]:
            with self.subTest(old=old), self.assertRaises(DocumentEditError) as caught:
                self.store.edit({'action':'edit_theory','base_sha256':source_hash(self.store.path.read_text()),
                                 'edits':[{'old':old,'new':'by simp'}]}, {})
            details = caught.exception.details
            self.assertEqual((details['reason'],details['match_count'],details['edit_index']), (reason,count,0))
            json.dumps(details)
            if count:
                self.assertEqual({k for m in details['matches'] for k in m['entry_ids']}, {'helper','contract'})
                self.assertTrue(all(m['start_line'] > 1 for m in details['matches']))
            self.assertEqual(self.files(), before)

    def test_failed_later_exact_edit_does_not_save_prior_edits(self):
        before = self.files()
        source = self.store.path.read_text()
        entry = self.store.read_entry('helper')
        helper_text = source[entry['entry_location']['start_offset']:entry['entry_location']['end_offset']]
        with self.assertRaises(DocumentEditError) as caught:
            self.store.edit({'action':'edit_theory','base_sha256':source_hash(source),
                'edits':[{'old':helper_text,'new':helper_text.replace('oops','by simp')},
                         {'old':'missing second edit','new':'x'}]}, {})
        self.assertEqual(caught.exception.details['edit_index'], 1)
        self.assertEqual(caught.exception.details['current_sha256'], source_hash(source))
        self.assertNotEqual(caught.exception.details['working_source_sha256'], source_hash(source))
        self.assertEqual(self.files(), before)

    def test_proof_edit_keeps_fixed_metadata_under_the_original_parser(self):
        before, _ = parse(self.store.path.read_text(), self.package, self.store.imports)
        self.edit('contract', 'by simp')
        after, proofs = parse(self.store.path.read_text(), self.package, self.store.imports)
        self.assertEqual(after, before)
        self.assertEqual(proofs, {'contract':'by simp'})


class ProofEditSchemaTests(unittest.TestCase):
    def test_new_actions_reject_metadata_edits_and_require_document_hash(self):
        schemas = tool_contracts()['source_action_schemas']
        reader = Draft202012Validator(schemas['read_entry'])
        editor = Draft202012Validator(schemas['edit_proof'])
        self.assertTrue(reader.is_valid({'action':'read_entry','entry_id':'goal'}))
        good = {'action':'edit_proof','entry_id':'goal','expected_hash':'document hash','proof_body':'by simp'}
        self.assertTrue(editor.is_valid(good))
        for change in ({'statement':'False'}, {'context':[]}, {'source':'replacement file'}):
            self.assertFalse(editor.is_valid({**good,**change}))
        self.assertFalse(editor.is_valid({k:v for k,v in good.items() if k != 'expected_hash'}))
        self.assertFalse(editor.is_valid({**good,'proof_body':''}))
