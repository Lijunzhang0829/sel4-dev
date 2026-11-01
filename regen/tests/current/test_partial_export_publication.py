"""Real journal/publication tests with explicitly synthetic native evidence.

No Isabelle process or model is invoked here. Mathematical export isolation is
covered separately by native_partial_export_smoke; these tests exercise durable
acceptance, publication, statistics, readiness and restart using seal_fixture.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from regen.execution import repository_execution as execution
from regen.execution.contract_refutation import check_refutation
from regen.execution.fact_context import manifest
from regen.execution.partial_theory_export import POLICY, build_export_scope, export_graph
from regen.execution.proof_commits import digest, durable
from regen.execution.seal_evidence import state_binding
from regen.planning.declaration_index import accepted_declarations, initial_index
from regen.planning.proof_workspace_agent import WorkspaceController
from regen.planning.theory_outcomes import _publish, current_dispositions, record_unresolved
from regen.tests.current.seal_fixture import evidence as synthetic_seal_evidence, native
from regen.tests.current.test_theory_outcome_continuation import SyntheticNativeChecker


class PartialExportPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "run"
        self.root.mkdir()
        (self.root / "generated").mkdir()
        self.packages = {}
        for theory, claims, providers in [
                ("Provider", [("a", "True"), ("b", "False"), ("c", "True"), ("u", "True")], []),
                ("Consumer", [("consumer", "True")], ["Provider"])]:
            package = {"theory": theory, "file_slot": theory + ".thy",
                "contracts": [{"contract_id": key, "name": key,
                    "exact_statement": f'lemma {key}: "{claim}"', "contract_owned_context": []}
                    for key, claim in claims],
                "repository_projection": {"schema_version": 6,
                    "required_imports": [{"theory": p, "why_import": "synthetic publication test"}
                                         for p in providers]}}
            package["package_payload_sha256"] = digest(package)
            self.packages[theory] = package
            durable(self.root / "packages" / (theory + ".thy.json"), package)
        files = [{"theory": p["theory"], "file_slot": p["file_slot"], "status": "not_started",
                  "contract_ids": [c["contract_id"] for c in p["contracts"]]}
                 for p in self.packages.values()]
        listing = {"files": [{k: row[k] for k in ("theory", "file_slot", "contract_ids")}
                              for row in files]}
        self.run = {"run_id": "partial-publication-unit", "implementation": {},
            "contract_outcome_policy": POLICY, "execution_mode": "repository-projection-v1",
            "execution_models": {"File Planner": "synthetic-model-not-called"},
            "contract_package_dir": str(self.root / "packages"),
            "session_schedule": {"file_list_sha256": digest(listing)},
            "session_progress_ledger": str(self.root / "session-progress-ledger.json"),
            "base_session": "Semantic_Base", "base_import": "Semantic_Base.Semantic_Root"}
        durable(self.root / "run.json", self.run)
        durable(self.root / "execution-input.json", {"files": files, "file_list": listing,
            "package_hashes": {p["file_slot"]: p["package_payload_sha256"] for p in self.packages.values()}})
        durable(self.root / "theories/Provider/imports.json", [])
        self.package = self.packages["Provider"]
        index = initial_index(self.package, run_id=self.run["run_id"])
        index.update(initial_graph_owner="File Planner", runtime_graph_owner="File Planner",
                     execution_mode="file-planner-batch-v1")
        state = {"graph": accepted_declarations(index, []), "proofs": {}, "accepted_order": [],
                 "rounds": {"b": 3, "u": 2}, "revisions": {"b": 1}, "batch_requests": 7,
                 "status": "planned", "seal_id": None}
        self.journal().append("initial", state, {"check": self.native_check()})

    def journal(self):
        return execution.journal(self.root, self.run, self.package)

    def state(self):
        return self.journal().replay(repair=False)

    def native_check(self):
        # Deliberately synthetic: no claim that Isabelle accepted these bytes.
        return native("\n".join(c["exact_statement"] + "\noops" for c in self.package["contracts"])
                      + "\n(* synthetic unit checker *)\nby simp\n")

    def controller(self):
        return SimpleNamespace(root=self.root, run=self.run, theory="Provider",
            _state=lambda _: (deepcopy(self.package), self.state()))

    def green(self, key):
        before = self.state()
        after = deepcopy(before)
        after["proofs"][key] = "by simp"
        after["accepted_order"].append(key)
        after["graph"] = accepted_declarations(before["graph"], after["accepted_order"])
        after["status"] = "generating"
        self.journal().append("green", after, {"node_id": key, "check": self.native_check(),
            "sealable_generation": True, "visibility": manifest(before["graph"], before["proofs"], key),
            "fixture_only": True})
        return after

    def refute_b(self):
        checker = SyntheticNativeChecker(self.root, "b")
        checker.imports = lambda: [self.run["base_import"]]
        contract = next(c for c in self.package["contracts"] if c["contract_id"] == "b")
        result = check_refutation(checker, contract, "by simp", self.root / "synthetic-refutation")
        return _publish(self.controller(), status="refuted", reason="Explicit synthetic native transport fixture",
            origin="native_checker", details={"certificate": result["certificate"]}, entry_ids=["b"],
            metadata={"fixture_only": True})

    def resolve_provider(self):
        # B is disposed between two accepted GREENs. Its receipt must survive C.
        self.green("a")
        refuted = self.refute_b()
        self.green("c")
        record_unresolved(self.controller(), reason="Synthetic bounded search stopped", origin="controller",
                          entry_ids=["u"], classification="proof_search_exhausted")
        return refuted

    def export_provider(self):
        self.resolve_provider()
        return self.seal_resolved_provider()

    def seal_resolved_provider(self):
        before = self.state()
        dispositions = current_dispositions(self.controller())
        scope = build_export_scope(self.package, before, dispositions, runtime=self.run,
                                   expected_imports=[self.run["base_import"]])
        view = deepcopy(before)
        view["graph"] = export_graph(before)
        evidence = synthetic_seal_evidence(view)
        evidence.update(seal_evidence_version=4, state_binding=state_binding(before), export_scope=scope)
        after = deepcopy(before)
        after.update(status="exported_partial", contract_dispositions=dispositions,
                     seal_id="theory-seal:" + digest(evidence)[:20])
        self.journal().append("seal", after, evidence)
        return after

    def immutable_hashes(self):
        paths = list((self.root / "theories/Provider/acceptance/commits").glob("*.json"))
        paths += list((self.root / "contract-dispositions").glob("*/events/*.json"))
        return {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}

    def assert_counts(self, progress):
        self.assertEqual(progress["contracts"], 5)
        for key in ("proved_contracts", "checked_contracts", "exported_contracts"):
            self.assertEqual(progress[key], 2, key)
        self.assertEqual(progress["refuted_contracts"], 1)
        self.assertEqual(progress["unknown_contracts"], 1)
        self.assertEqual(progress["sealed_contracts"], 0)
        self.assertFalse(progress["all_contracts_proved"])
        self.assertFalse(progress["repository_verified"])
        self.assertEqual(progress["session_status"], "partial")
        provider = next(row for row in progress["files"] if row["theory"] == "Provider")
        self.assertEqual(provider["contract_statuses"], {"a": "proved", "b": "refuted", "c": "proved", "u": "unknown"})
        self.assertTrue(provider["exports_available"])

    def test_real_journal_publishes_only_positive_subset_and_preserves_full_targets(self):
        sealed = self.export_provider()
        progress = execution.progress(self.root)
        self.assert_counts(progress)
        catalog = json.loads((self.root / "generated-premises.json").read_text())["records"]
        self.assertEqual({row["record_id"] for row in catalog}, {"generated:Provider:a", "generated:Provider:c"})
        self.assertTrue(all(row["proof_commit_id"] and row["theory_seal_id"] == sealed["seal_id"] for row in catalog))
        self.assertEqual({n["id"] for n in self.state()["graph"]["nodes"]}, {"a", "b", "c", "u"})
        source = (self.root / "generated/Regen_Provider.thy").read_text()
        self.assertIn("lemma a:", source)
        self.assertIn("lemma c:", source)
        self.assertNotIn("lemma b:", source)
        self.assertNotIn("lemma u:", source)
        self.assertNotIn("sorry", source)
        workspace = WorkspaceController(self.root, theories=["Provider"])
        try:
            view = workspace.list_workspace()
            self.assertTrue(view["all_selected_exported"])
            self.assertFalse(view["all_selected_sealed"])
        finally:
            workspace.close()

    def test_deleted_projection_and_forged_catalog_recover_from_same_journal(self):
        self.export_provider()
        execution.progress(self.root)
        original = self.immutable_hashes()
        for name in ["generated/Regen_Provider.thy", "session-progress-ledger.json",
                     "theories/Provider/progress.json", "theories/Provider/declaration-index.json",
                     "theories/Provider/acceptance/HEAD.json"]:
            (self.root / name).unlink()
        (self.root / "generated-premises.json").write_text('{"records":[{"record_id":"forged-b"}]}')
        self.assert_counts(execution.progress(self.root))
        self.assertEqual(self.immutable_hashes(), original)
        self.assertFalse((self.root / "theories/Provider/acceptance/HEAD.json").exists())
        self.assertTrue((self.root / "generated/Regen_Provider.thy").exists())
        catalog = json.loads((self.root / "generated-premises.json").read_text())["records"]
        self.assertEqual({r["canonical_name"] for r in catalog}, {"Regen_Provider.a", "Regen_Provider.c"})

    def test_workspace_consumer_becomes_ready_only_after_partial_export(self):
        workspace = WorkspaceController(self.root, theories=["Consumer"])
        try:
            rows = {r["theory"]: r for r in workspace.list_workspace()["theories"]}
            self.assertFalse(rows["Consumer"]["ready_for_proof_action"])
            self.assertEqual(rows["Consumer"]["unsealed_providers"], ["Provider"])
            self.resolve_provider()
            rows = {r["theory"]: r for r in workspace.list_workspace()["theories"]}
            self.assertFalse(rows["Consumer"]["ready_for_proof_action"])
        finally:
            workspace.close()
        self.seal_resolved_provider()
        fresh = WorkspaceController(self.root, theories=["Consumer"])
        try:
            rows = {r["theory"]: r for r in fresh.list_workspace()["theories"]}
            self.assertTrue(rows["Consumer"]["ready_for_proof_action"])
            self.assertEqual(rows["Consumer"]["unsealed_providers"], [])
            self.assertEqual(rows["Provider"]["pending_contract_ids"], [])
            self.assertEqual(set(rows["Provider"]["contract_dispositions"]), {"b", "u"})
            self.assertEqual(fresh._state("Provider")[1]["proofs"], {"a": "by simp", "c": "by simp"})
        finally:
            fresh.close()

    def test_disposed_contract_cannot_acquire_green_and_costs_are_not_reset(self):
        self.resolve_provider()
        before = self.state(); original = self.immutable_hashes()
        with self.assertRaisesRegex(ValueError, "positive proof.*disposition"):
            self.green("b")
        self.assertEqual(self.immutable_hashes(), original)
        after = self.state()
        for field in ("proofs", "accepted_order", "rounds", "revisions", "batch_requests"):
            self.assertEqual(after[field], before[field], field)
        self.assertEqual(after["rounds"], {"b": 3, "u": 2})

    def test_cross_run_fork_refuses_dispositions_without_losing_source(self):
        self.export_provider()
        original = self.immutable_hashes(); before = self.state()
        destination = self.root.parent / "continuation"
        with self.assertRaisesRegex(ValueError, "cross-run continuation with contract dispositions"):
            execution.fork_workspace_continuation(self.root, destination)
        self.assertFalse(destination.exists())
        self.assertEqual(self.immutable_hashes(), original)
        self.assertEqual(self.state(), before)
        self.assert_counts(execution.progress(self.root))


if __name__ == "__main__":
    unittest.main()
