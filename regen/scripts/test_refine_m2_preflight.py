#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from regen.refine_m2_preflight import (
    audit_public_packages,
    audit_workspace,
    build_arg_parser,
    contract_shell_text,
    m2_gate_exit_code,
    m2_gate_verdict,
    source_access_isolation_not_run,
    support_root_text,
    theory_name_for_shell,
)


class RefineM2PreflightTest(unittest.TestCase):
    def package(self, statement: str):
        return {
            "campaign_id": "campaign-test",
            "file_slot": "Bits_R.thy",
            "contracts": [{
                "contract_id": "contract:Refine.Bits_R:read_corres:abc",
                "name": "read_corres",
                "kind": "lemma",
                "exact_statement": statement,
                "normalized_statement_sha256": __import__("hashlib").sha256(" ".join(statement.split()).encode()).hexdigest(),
                "owner_theory": "Refine.Bits_R",
                "owner_file_slot": "Bits_R.thy",
                "contract_owned_attributes_raw": "[corres]",
                "contract_owned_attributes": ["corres"],
                "contract_owned_context": [],
            }],
        }

    def test_contract_shell_recreates_semantic_context_around_only_its_contract(self):
        package = self.package('lemma read_corres [corres]:\n  "corres (=) P P\' f g"')
        package["contracts"][0]["contract_owned_context"] = [
            "context begin interpretation Arch ."
        ]
        shell = contract_shell_text(package)
        self.assertIn(
            "context begin interpretation Arch .\n\nlemma read_corres", shell,
        )
        self.assertIn("sorry\n\nend", shell)

    def test_contract_shell_groups_identical_contexts_once(self):
        package = self.package('lemma first_corres:\n  "corres (=) P P\' f g"')
        first = package["contracts"][0]
        first["contract_owned_context"] = ["context begin interpretation Arch ."]
        second = dict(first)
        second["contract_id"] = "contract:Refine.Bits_R:second_corres:def"
        second["name"] = "second_corres"
        second["exact_statement"] = 'lemma second_corres:\n  "corres (=) Q Q\' h k"'
        package["contracts"].append(second)
        shell = contract_shell_text(package)
        self.assertEqual(shell.count("context begin interpretation Arch ."), 1)
        self.assertIn("lemma first_corres", shell)
        self.assertIn("lemma second_corres", shell)

    def test_public_package_audit_rejects_proof_context_leak(self):
        package = self.package('lemma read_corres: "corres (=) P P\' f g" using old_helper')
        audit = audit_public_packages("campaign-test", [package])
        self.assertEqual(audit["verdict"], "fail")
        self.assertFalse(audit["checks"]["no_public_statement_leakage"])

    def test_public_package_audit_accepts_sanitized_contract(self):
        package = self.package('lemma read_corres [corres]:\n  "corres (=) P P\' f g"')
        audit = audit_public_packages("campaign-test", [package])
        self.assertEqual(audit["verdict"], "pass")
        shell = contract_shell_text(package)
        self.assertIn(f"theory {theory_name_for_shell('Bits_R.thy')}", shell)
        self.assertIn("sorry", shell)
        self.assertNotIn("using old_helper", shell)
        self.assertIn('imports "M2_Frozen_Support.Corres"', shell)
        self.assertLess(shell.index("unbundle l4v_word_context"), shell.index("lemma read_corres"))

    def test_support_root_is_a_separate_parent_session(self):
        root = support_root_text({
            "eligible_theories": [
                {"file_slot": "LevityCatch.thy"},
                {"file_slot": "Corres.thy"},
            ]
        })
        self.assertIn("session M2_Frozen_Support = BaseRefine +", root)
        self.assertIn("LevityCatch", root)
        self.assertIn("Corres", root)

    def test_workspace_audit_rejects_copied_forbidden_source(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            campaign = Path(temp)
            workspace = campaign / "M2" / "sanitized-workspace"
            forbidden_source = b"lemma human_helper: True by simp\n"
            digest = __import__("hashlib").sha256(forbidden_source).hexdigest()
            inv = {
                "exact_proof_sources": [{
                    "theory": "Refine.Bits_R",
                    "source_path": "verification/l4v/proof/refine/ARM/Bits_R.thy",
                    "source_sha256": digest,
                }]
            }
            inv_path = campaign / "M1" / "private" / "proof-source-forbidden-inventory.json"
            inv_path.parent.mkdir(parents=True)
            inv_path.write_text(json.dumps(inv), encoding="utf-8")
            copied = workspace / "copied-human-source.thy"
            copied.parent.mkdir(parents=True)
            copied.write_bytes(forbidden_source)
            audit = audit_workspace(campaign, workspace)
            self.assertEqual(audit["verdict"], "fail")
            self.assertFalse(audit["checks"]["no_exact_forbidden_source_hash_copied"])

    def test_cli_requires_explicit_campaign_directory(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                build_arg_parser().parse_args([])
        args = build_arg_parser().parse_args(["--campaign-dir", "campaign"])
        self.assertEqual(args.campaign_dir, Path("campaign"))

    def test_content_audit_does_not_claim_source_access_isolation(self):
        result = source_access_isolation_not_run("campaign-test")
        self.assertEqual(result["verdict"], "not_run")
        self.assertIsNone(result["access_policy_identity"])
        self.assertEqual(result["denial_probes"], [])

    def test_m2_gate_requires_all_three_checks_to_pass(self):
        artifacts = {
            "workspace_content_audit": {"verdict": "pass"},
            "source_access_isolation": {"verdict": "pass"},
            "contract_elaboration": {"verdict": "pass"},
        }
        self.assertEqual(m2_gate_verdict(artifacts), "pass")
        self.assertEqual(m2_gate_exit_code("pass"), 0)

        artifacts["contract_elaboration"]["verdict"] = "timeout"
        self.assertEqual(m2_gate_verdict(artifacts), "incomplete")
        self.assertEqual(m2_gate_exit_code("incomplete"), 2)

        artifacts["workspace_content_audit"]["verdict"] = "fail"
        self.assertEqual(m2_gate_verdict(artifacts), "fail")
        self.assertEqual(m2_gate_exit_code("fail"), 1)


if __name__ == "__main__":
    unittest.main()
