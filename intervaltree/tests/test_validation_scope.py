"""Reject partial-session receipts and unaudited expanded contract inputs."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, PROJECT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


build = load("build")
prepare = load("prepare_benchmark")
with patch.dict(sys.modules, {"build": build, "prepare_benchmark": prepare}):
    validation = load("record_validation")


class ValidationScopeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / "sanitized"
        shutil.copytree(PROJECT / "sanitized", self.directory)
        self.registry = json.loads((PROJECT / "targets.json").read_text())
        self.manifest = json.loads((self.directory / "input-manifest.json").read_text())

    def receipt(self, suffix):
        path = Path(self.temporary.name) / "build-result.json"
        path.write_text(json.dumps({
            "status": "passed", "returncode": 0,
            "sources_sha256": build.source_hashes(self.directory),
            "sources_unchanged_during_build": True,
            "command": ["isabelle", "build", *suffix],
        }))
        return path

    def check_expansion(self):
        return validation.checked_expansion(self.registry, self.manifest, self.directory)

    def test_definitions_only_receipt_is_rejected_despite_complete_source_hashes(self):
        receipt = self.receipt(["-d", str(self.directory), "Intervaltree_Definitions"])
        with self.assertRaisesRegex(ValueError, "complete session directory"):
            validation.checked_build(receipt, self.directory)

    def test_complete_directory_receipt_and_current_audit_are_accepted(self):
        receipt = self.receipt(["-D", str(self.directory)])
        self.assertEqual(validation.checked_build(receipt, self.directory)["status"], "passed")
        evidence = self.check_expansion()
        self.assertEqual(evidence["checked_targets"], 91)
        self.assertEqual(evidence["goals_sha256"], validation.sha(self.directory / "goals/Interval_Goals.thy"))

    def test_missing_goal_session_is_rejected(self):
        root = self.directory / "ROOT"
        root.write_text(root.read_text().split("session Intervaltree_Goals")[0])
        with self.assertRaisesRegex(ValueError, "Goals session"):
            self.check_expansion()

    def test_missing_native_audit_is_rejected(self):
        goals = self.directory / "goals/Interval_Goals.thy"
        goals.write_text(goals.read_text().replace(prepare.expansion_audit(self.registry["targets"]), ""))
        with self.assertRaisesRegex(ValueError, "Native spec expansion audit"):
            self.check_expansion()

    def test_public_statement_drift_is_rejected(self):
        contracts = self.directory / "contracts.jsonl"
        records = [json.loads(line) for line in contracts.read_text().splitlines()]
        records[0]["exact_statement"] = 'lemma spec_overlaps_symm: "True"'
        contracts.write_text("".join(json.dumps(record) + "\n" for record in records))
        with self.assertRaisesRegex(ValueError, "Public contracts differ"):
            self.check_expansion()

    def test_duplicate_target_ids_are_rejected(self):
        self.registry["targets"][1]["id"] = self.registry["targets"][0]["id"]
        with self.assertRaisesRegex(ValueError, "target coverage"):
            self.check_expansion()


if __name__ == "__main__":
    unittest.main()
