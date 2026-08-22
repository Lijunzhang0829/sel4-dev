#!/usr/bin/env python3
"""Contract-surface regression tests."""
from __future__ import annotations

import unittest

from regen_extract import is_contract, slice_decls


class ContractPolicyTest(unittest.TestCase):
    def classify(self, source: str) -> bool:
        return is_contract(slice_decls(source)[0])

    def test_operation_corres_is_contract(self):
        self.assertTrue(self.classify(
            'lemma getCurThread_corres [corres]:\n'
            '  "corres (=) \\<top> \\<top> (gets cur_thread) getCurThread"\n'
            '  by simp\n'
        ))

    def test_generic_corres_rule_is_not_contract(self):
        self.assertFalse(self.classify(
            'lemma corres_cap_fault:\n'
            '  "corres r P P\' f g \\<Longrightarrow> corres r Q Q\' f g"\n'
            '  by simp\n'
        ))

    def test_non_corres_suffix_is_not_contract(self):
        self.assertFalse(self.classify(
            'lemma helper:\n'
            '  "corres r P P\' f g"\n'
            '  by simp\n'
        ))

    def test_names_quoted_definition_and_record(self):
        declarations = slice_decls(
            'definition\n  "ps_clear p n s \\<equiv> True"\n\n'
            'definition\n  "invs\' s \\<equiv> True"\n\n'
            "definition\n  obj_at' :: \"nat => bool\"\nwhere\n  \"obj_at' x = True\"\n\n"
            "record itcb' =\n  field :: nat\n"
        )
        self.assertEqual(
            [item["name"] for item in declarations],
            ["ps_clear", "invs'", "obj_at'", "itcb'"],
        )

    def test_inductive_is_a_named_semantic_declaration(self):
        declarations = slice_decls(
            'inductive\n'
            '  subtree :: "nat => nat => bool"\n'
            'where\n'
            '  direct: "subtree x x"\n'
        )
        self.assertEqual(declarations[0]["kind"], "inductive")
        self.assertEqual(declarations[0]["name"], "subtree")


if __name__ == "__main__":
    unittest.main()
