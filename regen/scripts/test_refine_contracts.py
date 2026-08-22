#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from regen.refine_campaign import sha256_json
from regen.refine_contracts import (
    extract_theory,
    contract_contexts_by_line,
    extraction_audit,
    forbidden_statement_evidence,
    public_package,
    sanitize_contract_statement,
    validate_extraction,
    verify_m1_artifact_seal,
    verify_source_records,
    write_m1,
)


def fake_slice(source: str):
    return [{
        "kind": "lemma",
        "name": "read_corres",
        "attr": "[corres]",
        "start": 3,
        "stmt": 'lemma read_corres [corres]:\n  "corres (=) P P\' f g"',
    }]


class RefineContractsTest(unittest.TestCase):
    def test_extracts_only_enclosing_contract_context_openers(self):
        source = r'''theory Demo
imports Parent
begin

context begin interpretation Arch .

lemma helper: True by simp

lemma read_corres [corres]:
  "corres (=) P P' f g"
  by simp

end
end
'''
        contexts = contract_contexts_by_line(source)
        self.assertEqual(contexts[8], ["context begin interpretation Arch ."])
        self.assertNotIn("helper", str(contexts))

    def test_sanitizes_real_outer_syntax_leak_shapes(self):
        raw = r'''lemma cteSwap_corres [corres]:
  fixes x :: nat
  assumes srcdst: "src' = src" and rel: "cap_relation c c'"
  notes trans_state_update'[symmetric,simp]
  defines "P \<equiv> Q"
  shows "corres dc P P' f g"
  (is "corres _ ?P ?P' _ _") using assms including no_pre
  supply None_upd_eq[simp del]
  apply simp
'''
        statement = sanitize_contract_statement(raw)
        self.assertIn("fixes x :: nat", statement)
        self.assertIn("assumes srcdst:", statement)
        self.assertIn("defines", statement)
        self.assertIn('shows "corres dc P P\' f g"', statement)
        for leaked in ("notes", "trans_state_update", "(is", "using", "including", "supply", "apply"):
            self.assertNotIn(leaked, statement)
        self.assertEqual(forbidden_statement_evidence(statement), [])

    def test_removes_notes_inline_using_and_human_comments(self):
        raw = r'''lemma insertNewCap_corres:
notes if_cong[cong del] if_weak_cong[cong] shows
  "corres dc P P' f g" (* human FIXME *)
  using r c
'''
        statement = sanitize_contract_statement(raw)
        self.assertEqual(statement, '''lemma insertNewCap_corres:
shows
  "corres dc P P' f g"''')

        symbolic = r'''lemma transferCaps_corres:
  shows "corres dc (P and Q \<comment> \<open>human hint\<close> and R) P' f g" by simp'''
        statement = sanitize_contract_statement(symbolic)
        self.assertIn("P and Q", statement)
        self.assertIn("and R", statement)
        self.assertNotIn("comment", statement)
        self.assertNotIn("human hint", statement)
        self.assertNotIn("by simp", statement)

    def test_removes_front_includes_but_keeps_later_proposition(self):
        raw = '''lemma demo_corres:
  includes no_pre
  shows "corres dc P P' f g"
  by simp
'''
        statement = sanitize_contract_statement(raw)
        self.assertEqual(statement, '''lemma demo_corres:
shows "corres dc P P' f g"''')
        self.assertEqual(forbidden_statement_evidence(statement), [])

    def test_public_package_contains_contract_but_no_human_source_metadata(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            path = Path(temp) / "Bits_R.thy"
            path.write_text("theory Bits_R imports Human begin\nby simp\n", encoding="utf-8")
            theory = extract_theory(
                "Refine.Bits_R", path, slice_decls=fake_slice,
                is_contract=lambda _decl: True,
                repo=Path(temp),
            )
            package = public_package("campaign-test", theory)
            self.assertEqual(package["contracts"][0]["name"], "read_corres")
            self.assertEqual(package["contracts"][0]["contract_owned_attributes"], ["corres"])
            self.assertEqual(package["contracts"][0]["contract_owned_context"], [])
            self.assertNotIn("trusted_source_path", package)
            self.assertNotIn("source_location", package["contracts"][0])
            self.assertNotIn("Human", str(package))
            self.assertNotIn("by simp", str(package))
            self.assertEqual(
                package["contract_order_policy"],
                "semantic-contract-id-order-not-source-order",
            )
            audit = extraction_audit("campaign-test", [theory], [package])
            self.assertEqual(audit["verdict"], "pass")

    def test_exact_once_validation_rejects_missing_public_contract(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            path = Path(temp) / "Bits_R.thy"
            path.write_text("fixture", encoding="utf-8")
            theory = extract_theory(
                "Refine.Bits_R", path, slice_decls=fake_slice,
                is_contract=lambda _decl: True,
                repo=Path(temp),
            )
            with self.assertRaisesRegex(ValueError, "exactly once"):
                validate_extraction([theory], [])

    def test_full_source_hash_detects_proof_body_drift(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            repo = Path(temp)
            path = repo / "Bits_R.thy"
            path.write_text("lemma read_corres: \"corres (=) P P' f g\"\nby simp\n", encoding="utf-8")
            first = extract_theory(
                "Refine.Bits_R", path, slice_decls=fake_slice,
                is_contract=lambda _decl: True, repo=repo,
            )
            inventory = [{
                "source_path": "Bits_R.thy",
                "source_sha256": first["trusted_source_sha256"],
            }]
            verify_source_records(repo, inventory)
            path.write_text("lemma read_corres: \"corres (=) P P' f g\"\nby auto\n", encoding="utf-8")
            second = extract_theory(
                "Refine.Bits_R", path, slice_decls=fake_slice,
                is_contract=lambda _decl: True, repo=repo,
            )
            self.assertEqual(
                first["contracts"][0]["exact_statement"],
                second["contracts"][0]["exact_statement"],
            )
            self.assertNotEqual(first["trusted_source_sha256"], second["trusted_source_sha256"])
            with self.assertRaisesRegex(ValueError, "trusted source drift"):
                verify_source_records(repo, inventory)


def minimal_m1_artifacts() -> dict:
    contract = {
        "contract_id": "contract:Refine.Bits_R:read_corres:test",
        "name": "read_corres",
        "owner_file_slot": "Bits_R.thy",
        "exact_statement": "lemma read_corres: \"corres (=) P P' f g\"",
    }
    private_manifest = {
        "campaign_id": "campaign-test",
        "contract_count": 1,
        "contracts": [contract],
        "contracts_sha256": sha256_json([contract]),
    }
    classification = {
        "artifact_type": "proof-support-classification",
        "campaign_id": "campaign-test",
        "proof_theory_count": 1,
        "theories": [{
            "theory": "Refine.Bits_R",
            "file_slot": "Bits_R.thy",
            "classification": "Proof",
            "contract_count": 1,
        }],
    }
    classification["classification_payload_sha256"] = sha256_json(classification)
    package = {
        "artifact_type": "public-proof-file-package",
        "campaign_id": "campaign-test",
        "stage": "Refine",
        "file_slot": "Bits_R.thy",
        "contracts": [contract],
    }
    package["package_payload_sha256"] = sha256_json(package)
    return {
        "private_manifest": private_manifest,
        "classification": classification,
        "support_inventory": {"campaign_id": "campaign-test", "theories": []},
        "forbidden_inventory": {"campaign_id": "campaign-test", "exact_proof_sources": []},
        "packages": [package],
        "audit": {"campaign_id": "campaign-test", "verdict": "pass"},
    }


class M1ArtifactSealTest(unittest.TestCase):
    def test_seal_rejects_missing_public_package(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            campaign = Path(temp) / "campaign"
            artifacts = minimal_m1_artifacts()
            results = write_m1(campaign, artifacts)
            self.assertEqual(len(results), 7)
            verified = verify_m1_artifact_seal(
                campaign / "M1", artifacts["private_manifest"],
            )
            self.assertEqual(verified["payload_artifacts_verified"], 6)
            self.assertEqual(verified["proof_packages_verified"], 1)

            package = campaign / "M1/public/proof-file-packages/Bits_R.thy.json"
            package.unlink()
            with self.assertRaisesRegex(ValueError, "missing or not regular"):
                verify_m1_artifact_seal(campaign / "M1", artifacts["private_manifest"])

    def test_seal_rejects_public_package_content_drift(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            campaign = Path(temp) / "campaign"
            artifacts = minimal_m1_artifacts()
            write_m1(campaign, artifacts)
            package = campaign / "M1/public/proof-file-packages/Bits_R.thy.json"
            package.write_text(package.read_text(encoding="utf-8") + " ", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "artifact drift"):
                verify_m1_artifact_seal(campaign / "M1", artifacts["private_manifest"])


if __name__ == "__main__":
    unittest.main()
