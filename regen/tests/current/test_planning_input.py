from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.preparation.planning_input import (
    PlanningInputQueries, build_refine_input, prepare,
)
from regen.preparation.checker_context import create_context, load_context
from regen.planning import theory_plan
from regen.planning import repository_planner
from regen.execution import repository_execution


class PlanningInputTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.l4v = self.root / "l4v"
        proof = self.l4v / "proof"
        proof.mkdir(parents=True)
        (proof / "ROOT").write_text(
            '''session BaseRefine in "base" = HOL +
  theories Base
session Lib in "lib" = HOL +
  theories Lib
session Refine in "refine/$L4V_ARCH" = BaseRefine +
  sessions Lib
  theories Demo_R
session CRefine in "crefine/$L4V_ARCH" = Refine +
  theories Demo_C
''',
            encoding="utf-8",
        )
        refine = proof / "refine/ARM"
        refine.mkdir(parents=True)
        (refine / "Demo_R.thy").write_text(
            'theory Demo_R imports BaseRefine.Base Lib.Lib begin\nend\n',
            encoding="utf-8",
        )
        self.contract = {
            "contract_id": "contract:Refine.Demo_R:demo:1",
            "name": "demo",
            "kind": "lemma",
            "exact_statement": 'lemma demo: "P x \\<Longrightarrow> Q x"',
            "owner_theory": "Refine.Demo_R",
            "owner_file_slot": "Demo_R.thy",
            "contract_owned_context": [],
            "contract_owned_attributes": [],
        }
        self.premise = {
            "record_id": "premise:base",
            "canonical_name": "Base.base_rule",
            "exact_statement": '"P x \\<Longrightarrow> Q x"',
            "owner_theory": "BaseRefine.Base",
            "owner_session": "BaseRefine",
            "source_class": "frozen_parent",
            "attributes": ["intro"],
            "provenance": {"export": "test"},
        }

    def test_refine_input_and_read_only_queries(self):
        value, rows = build_refine_input(
            self.l4v, [self.contract], [self.premise],
        )
        self.assertEqual(value["benchmark"]["target_session"], "Refine")
        self.assertTrue(value["benchmark"]["independent_experiment"])
        self.assertEqual(value["benchmark"]["cross_experiment_inputs"], [])
        self.assertIn("BaseRefine", value["upstream_session_closure"])
        self.assertNotIn("CRefine", json.dumps(value))
        self.assertEqual(rows[0]["context_profile_ids"], ["context:Refine:parents"])

        contracts = self.root / "contracts.json"
        premises = self.root / "premises.json"
        output = self.root / "prepared"
        contracts.write_text(json.dumps({"contracts": [self.contract]}))
        premises.write_text(json.dumps({"records": [self.premise]}))
        result = prepare(self.l4v, contracts, premises, output)
        self.assertEqual(result["verdict"], "pass")
        query = PlanningInputQueries(output / "repository-planning-input.json")
        self.assertEqual(query.inspect_contracts([self.contract["contract_id"]])[0]["name"], "demo")
        hits = query.search_parent_premises(
            "Base base_rule P Q", context_profile_ids=["context:Refine:parents"],
        )
        self.assertEqual(hits[0]["record_id"], self.premise["record_id"])
        exact = query.inspect_parent_premises([self.premise["record_id"]])
        self.assertEqual(exact[0]["attributes"], ["intro"])
        resolved = query.resolve_symbols(["Base.base_rule"], "context:Refine:parents")
        self.assertEqual(resolved[0]["status"], "resolved")

    def test_target_and_cross_experiment_facts_are_rejected(self):
        for session, theory in [
            ("Refine", "Refine.Demo_R"),
            ("CRefine", "CRefine.Demo_C"),
        ]:
            bad = {**self.premise, "owner_session": session, "owner_theory": theory}
            with self.subTest(session=session), self.assertRaisesRegex(
                ValueError, "target-session fact"
            ):
                build_refine_input(self.l4v, [self.contract], [bad])

    def test_out_of_closure_fact_and_non_refine_contract_are_rejected(self):
        bad = {**self.premise, "owner_session": "CBaseRefine"}
        with self.assertRaisesRegex(ValueError, "outside the Refine parent closure"):
            build_refine_input(self.l4v, [self.contract], [bad])
        contract = {**self.contract, "owner_theory": "CRefine.Demo_C"}
        with self.assertRaisesRegex(ValueError, "non-Refine contract"):
            build_refine_input(self.l4v, [contract], [self.premise])

    def test_schema_four_plan_keeps_helpers_at_intent_level(self):
        payload, rows = build_refine_input(
            self.l4v, [self.contract], [self.premise],
        )
        self.assertNotIn(self.contract["exact_statement"], theory_plan.prompt(payload, []))
        payload["planner_evidence"] = {
            "inspected_contracts": payload["contracts"],
            "inspected_parent_premises": rows,
        }
        proposal = {
            "schema_version": 4,
            "ordering_rationale": "One ready theory for the single contract.",
            "contract_relations": [],
            "theories": [{
                "name": "Generated_Demo",
                "purpose": "Prove the demo contract.",
                "grouping_reason": "The benchmark has one target.",
                "contract_ids": [self.contract["contract_id"]],
                "context_profile_ids": ["context:Refine:parents"],
                "helper_decisions": [{
                    "id": "reuse_base_rule",
                    "kind": "reuse_existing_fact",
                    "intent": "Use the visible parent rule before inventing a helper.",
                    "for_contracts": [self.contract["contract_id"]],
                    "premise_ids": [self.premise["record_id"]],
                }, {
                    "id": "possible_demo_bridge",
                    "kind": "propose_auxiliary_intent",
                    "intent": "Close any remaining semantic bridge locally.",
                    "for_contracts": [self.contract["contract_id"]],
                    "premise_ids": [],
                }],
                "suggested_predecessors": [],
            }],
            "unresolved_retrieval_questions": [],
        }
        artifacts = theory_plan.build_bundle(payload, proposal)
        plan = artifacts["repository-plan.json"]
        projection = artifacts["files/Generated_Demo.json"]
        self.assertNotIn("nodes", plan)
        self.assertEqual(
            repository_planner.summary(
                {**plan, "plan_id": "repository-plan:test"}, "created",
            )["auxiliary_intents"],
            1,
        )
        self.assertEqual(
            projection["reused_parent_premise_ids"],
            [self.premise["record_id"]],
        )
        self.assertNotIn("statement", projection["auxiliary_intents"][0])
        self.assertEqual(
            artifacts["file-generation-list.json"]["provider_theories"]
            if "provider_theories" in artifacts["file-generation-list.json"]
            else projection["suggested_predecessors"],
            [],
        )

    def test_direct_repository_planner_creates_revision_bound_projections(self):
        contracts = self.root / "contracts.json"
        premises = self.root / "premises.json"
        prepared = self.root / "prepared"
        output = self.root / "planning-output"
        contracts.write_text(json.dumps({"contracts": [self.contract]}))
        premises.write_text(json.dumps({"records": [self.premise]}))
        prepare(self.l4v, contracts, premises, prepared)
        request = {
            "schema_version": 1,
            "contract_batches": [[self.contract["contract_id"]]],
            "premise_searches": [],
        }
        proposal = {
            "schema_version": 4,
            "ordering_rationale": "The only theory is immediately ready.",
            "contract_relations": [],
            "theories": [{
                "name": "Generated_Demo",
                "purpose": "Prove the fixed demo contract.",
                "grouping_reason": "There is one contract.",
                "contract_ids": [self.contract["contract_id"]],
                "context_profile_ids": ["context:Refine:parents"],
                "helper_decisions": [],
                "suggested_predecessors": [],
            }],
            "unresolved_retrieval_questions": [],
        }

        def fake_call(_text, prefix, **kwargs):
            value = request if kwargs["stage"].endswith("Inspection") else proposal
            return json.dumps(value), {}, prefix

        policy = {
            "schema_version": 3,
            "artifact_type": "repository-planning-policy",
            "mode": "repository-proof-planning",
            "stage": "Refine",
            "model": {"model": "test-model", "max_attempts": 1},
        }
        with patch.object(repository_planner, "_direct_call", side_effect=fake_call):
            result = repository_planner.run_prepared(
                prepared / "repository-planning-input.json", policy, output,
                allow_external_call=True,
            )
        plan = json.loads((output / "repository-plan.json").read_text())
        listing = json.loads((output / "file-generation-list.json").read_text())
        projection = json.loads((output / "files/Generated_Demo.json").read_text())
        lineage = output / "planning" / plan["plan_id"]
        self.assertEqual(result["contracts"], 1)
        self.assertTrue((lineage / "revisions/0001.json").is_file())
        self.assertTrue((lineage / "HEAD.json").is_file())
        self.assertEqual(listing["plan_revision_id"], plan["revision_id"])
        self.assertEqual(projection["plan_revision_id"], plan["revision_id"])
        self.assertNotIn("environment_gate_id", json.dumps(plan))

        assets = self.root / "checker-source"
        sanitized_l4v = assets / "l4v/proof/refine/base"
        semantic = assets / "semantic-base"
        heaps = assets / "heaps"
        isabelle = assets / "isabelle"
        for directory in (sanitized_l4v, semantic, heaps, isabelle):
            directory.mkdir(parents=True)
        (sanitized_l4v / "Include.thy").write_text(
            "theory Include imports Main begin end\n"
        )
        context = create_context(
            prepared, l4v=assets / "l4v", semantic_workspace=semantic,
            heaps=heaps, isabelle=isabelle, image="test-image",
        )
        self.assertEqual(load_context(prepared)["checker_context_id"],
                         context["checker_context_id"])
        run = repository_execution.init_run(
            self.root / "proof-run", prepared, "direct-run",
            output / "file-generation-list.json",
        )
        self.assertEqual(run["execution_input_kind"], "refine-dataset")
        self.assertEqual(run["checker_context_id"], context["checker_context_id"])
        self.assertNotIn("environment_run", run)


if __name__ == "__main__":
    unittest.main()
