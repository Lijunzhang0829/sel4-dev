#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from regen.preparation.contract_extractor import (
    contract_contexts_by_line,
    extract_theory,
    forbidden_statement_evidence,
    public_package,
    sanitize_contract_statement,
)


def fake_slice(source: str):
    return [{
        "kind": "lemma",
        "name": "read_corres",
        "attr": "[corres]",
        "start": 3,
        "stmt": 'lemma read_corres [corres]:\n  "corres (=) P P\' f g"',
    }]


class ContractExtractorTest(unittest.TestCase):
    def test_extracts_only_enclosing_contract_context_openers(self):
        source = r"""theory Demo
imports Parent
begin

context begin interpretation Arch .

lemma helper: True by simp

lemma read_corres [corres]:
  "corres (=) P P' f g"
  by simp

end
end
"""
        contexts = contract_contexts_by_line(source)
        self.assertEqual(contexts[8], ["context begin interpretation Arch ."])
        self.assertNotIn("helper", str(contexts))

    def test_sanitizes_proof_context_and_body(self):
        raw = r"""lemma cteSwap_corres [corres]:
  fixes x :: nat
  assumes srcdst: "src' = src" and rel: "cap_relation c c'"
  notes trans_state_update'[symmetric,simp]
  defines "P \<equiv> Q"
  shows "corres dc P P' f g"
  (is "corres _ ?P ?P' _ _") using assms including no_pre
  supply None_upd_eq[simp del]
  apply simp
"""
        statement = sanitize_contract_statement(raw)
        self.assertIn("fixes x :: nat", statement)
        self.assertIn("assumes srcdst:", statement)
        self.assertIn("defines", statement)
        self.assertIn('shows "corres dc P P\' f g"', statement)
        for leaked in (
            "notes", "trans_state_update", "(is", "using", "including",
            "supply", "apply",
        ):
            self.assertNotIn(leaked, statement)
        self.assertEqual(forbidden_statement_evidence(statement), [])

    def test_removes_front_includes_but_keeps_proposition(self):
        raw = """lemma demo_corres:
  includes no_pre
  shows "corres dc P P' f g"
  by simp
"""
        statement = sanitize_contract_statement(raw)
        self.assertEqual(
            statement,
            """lemma demo_corres:
shows "corres dc P P' f g" """.rstrip(),
        )

    def test_public_package_has_only_contract_semantic_identity(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            path = Path(temp) / "Bits_R.thy"
            path.write_text(
                "theory Bits_R imports Human begin\nby simp\n", encoding="utf-8",
            )
            theory = extract_theory(
                "Refine.Bits_R", path, slice_decls=fake_slice,
                is_contract=lambda _decl: True, repo=Path(temp),
            )
            package = public_package("campaign-test", theory)
            self.assertEqual(package["artifact_type"], "contract-package")
            self.assertEqual(package["contracts"][0]["name"], "read_corres")
            self.assertEqual(
                package["contracts"][0]["contract_owned_attributes"], ["corres"],
            )
            self.assertEqual(
                package["semantic_context_policy"],
                "minimal-enclosing-context-openers-only",
            )
            self.assertNotIn("trusted_source_path", package)
            self.assertNotIn("source_location", package["contracts"][0])
            self.assertNotIn("Human", str(package))
            self.assertNotIn("by simp", str(package))


if __name__ == "__main__":
    unittest.main()
