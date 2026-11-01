#!/usr/bin/env python3
"""Disclosed, model-free v2 controller preflight on machine A.

This is an infrastructure fixture, not a generation experiment. The negative
proof is an archived counterexample adapted only at its final definition unfold;
the positive proof is a handwritten empty-list simplification. Neither earns
fresh generation credit. All 91 current goals are mapped exactly once, but only
two goals are natively checked here. Full declaration preflight is separate.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY))
NEGATIVE = "Preflight_Negative"
POSITIVE = "Preflight_Positive"
HELD_OUT = "Preflight_Unexecuted"
POSITIVE_BODY = "by (simp add: merge_overlaps_def sort_by_lo_def)"
DEFAULT_CERTIFICATE = REPOSITORY / (
    "reports/regen/intervaltree-stability-validation-20261030-001/"
    "native-refutation/native-check/negative/certificate.json"
)


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def sha(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def paths(root):
    output = root / "controller-preflight-v2"
    return output, output / "proof", output / "planning-fixture"


def task_hashes(proof):
    return {str(path.relative_to(proof)): sha(path.read_bytes())
            for path in sorted((proof / "check-tasks").glob("*/*.json"))}


def validate_dataset(root):
    dataset = root / "dataset"
    semantic = dataset / "checker-assets/semantic-base"
    manifest_path = semantic / "input-manifest.json"
    manifest = read(manifest_path)
    prepared = read(dataset / "preparation.json")
    if manifest.get("input_revision") != "expanded-specs-v2" or manifest.get("target_count") != 91:
        raise ValueError("this runner requires expanded-specs-v2 with 91 goals")
    if prepared.get("source_manifest_sha256") != sha(manifest_path.read_bytes()):
        raise ValueError("preparation does not bind the copied source manifest")
    for name, expected in manifest["files"].items():
        # The preparation adapter appends its conservative parent session to ROOT.
        if name != "ROOT" and sha((semantic / name).read_bytes()) != expected:
            raise ValueError("copied source drift: " + name)
    raw = [json.loads(line) for line in (semantic / "contracts.jsonl").read_text().splitlines()]
    payload = read(dataset / "repository-planning-input.json")
    by_name = lambda values: {item["name"]: item["exact_statement"] for item in values}
    if len(raw) != 91 or len(payload["contracts"]) != 91 or by_name(raw) != by_name(payload["contracts"]):
        raise ValueError("prepared statements differ from copied v2 source")
    return {"status": "pass", "input_revision": manifest["input_revision"],
            "manifest_sha256": sha(manifest_path.read_bytes()), "exact_statements_matched": 91}


def controller_for(proof, theory):
    from regen.execution.check_executor import CheckExecutor, NativeResources
    from regen.planning.parallel_file_planner import TheoryController
    resources = NativeResources(1, 2, 1, cpus=4, memory_mb=8192,
        pids_limit=1024, provider_build_timeout=900, build_ml_heap_mb=2048)
    executor = CheckExecutor(proof / "check-tasks", workers=1)
    try:
        controller = TheoryController(proof, theory, resources=resources,
            executor=executor, document_mode=True,
            generation_strategy_name="sketch-reviewed-v1", model="gpt-6-astra",
            call_timeout=900, check_timeout=900, candidate_timeout=300)
    except BaseException:
        executor.close()
        resources.close()
        raise
    return controller, executor, resources


def close_all(controller, executor, resources):
    try:
        controller.close()
    finally:
        try:
            executor.close()
        finally:
            resources.close()


def action(controller, proposal, label):
    return controller.handle("proof_action",
        {"theory": controller.theory, "proposal": proposal},
        {"thread_id": "handwritten-v2-infrastructure-fixture", "turn_id": "fixture",
         "call_id": label, "validation_fixture": True,
         "model_generated_this_run": False, "fresh_generation_credit": 0})


def prepare(root, source_certificate):
    from regen.execution.proof_session_runner import init_run
    from regen.planning.theory_plan import build_bundle
    from regen.planning.plan_revisions import start_plan, append_revision
    from regen.planning.repository_planner import _bind_direct_artifacts
    output, proof, planning = paths(root)
    output.mkdir(exist_ok=False)
    save(output / "v2-input-identity.json", validate_dataset(root))
    payload = read(root / "dataset/repository-planning-input.json")
    contracts = {item["name"]: item for item in payload["contracts"]}
    if len(contracts) != 91:
        raise ValueError("expected all 91 uniquely named frozen v2 contracts")
    names = {NEGATIVE: ["tree_remove_preserves_wf", "refine_empty_state"],
             POSITIVE: ["spec_merge_nil"]}
    selected = {name for values in names.values() for name in values}
    names[HELD_OUT] = [name for name in contracts if name not in selected]
    profiles = [item["context_profile_id"] for item in payload["context_profiles"]]
    proposal = {
        "schema_version": 6,
        "ordering_rationale": "Handwritten infrastructure fixture: replay a false goal, then execute an independent positive theory. No automatic scheduler or model planning is exercised.",
        "contract_relations": [], "unresolved_retrieval_questions": [],
        "collaboration_groups": [], "reuse_suggestions": [],
        "theories": [{"name": theory,
            "purpose": "Native controller validation only; no benchmark generation credit.",
            "grouping_reason": ("Intentionally co-locate a second goal to expose theory-wide stopping."
                if theory == NEGATIVE else "Independent fixture or explicitly unexecuted remainder."),
            "contract_ids": [contracts[name]["contract_id"] for name in values],
            "context_profile_ids": profiles, "helper_decisions": [], "required_imports": []}
            for theory, values in names.items()],
    }
    artifacts = build_bundle(payload, proposal)
    identity = start_plan(planning / "planning", payload["benchmark"]["id"])
    current = append_revision(planning / "planning",
        {**artifacts["repository-plan.json"], "plan_id": identity["plan_id"]})
    for name, value in _bind_direct_artifacts(artifacts, current, planning).items():
        if name != "repository-plan.json":
            save(planning / name, value)
    save(planning / "fixture-proposal.json", proposal)
    certificate = read(source_certificate)
    original = certificate["proof_body"]
    if sha(original) != certificate["proof_body_sha256"]:
        raise ValueError("archived counterexample proof hash mismatch")
    old = "show ?thesis using counterexample by simp"
    new = ("show ?thesis using counterexample\n"
           "    unfolding Interval_Refinement.tree_remove_preserves_wf_def by simp")
    if original.count(old) != 1:
        raise ValueError("expected precisely one mechanical v2 adaptation site")
    adapted = original.replace(old, new)
    (output / "refutation-fixture.isar").write_text(adapted)
    save(output / "fixture-provenance.json", {
        "archived_certificate": str(source_certificate),
        "archived_certificate_sha256": sha(source_certificate.read_bytes()),
        "archived_contract": certificate["contract"],
        "archived_proof_sha256": sha(original), "adapted_proof_sha256": sha(adapted),
        "mechanical_change": {"old": old, "new": new},
        "current_negative_contract": contracts["tree_remove_preserves_wf"],
        "positive_contract": contracts["spec_merge_nil"],
        "positive_proof_body": POSITIVE_BODY,
        "positive_provenance": "Handwritten empty-list definition simplification for infrastructure validation.",
        "model_calls": 0, "fresh_generation_credit": 0,
        "all_contracts_mapped": 91, "native_contracts_selected": 2,
        "same_theory_unproved_control": "refine_empty_state",
        "unexecuted_other_contracts": 89, "manual_plan": True,
        "automatic_scheduling_tested": False, "explicit_stages_tested": False,
    })
    init_run(proof, root / "dataset", run_id=root.name + "-controller-fixture",
        schedule_path=planning / "file-generation-list.json",
        log_storage="regen-merkle-json-v1")
    result = {"status": "pass", "all_contracts_mapped": 91,
        "native_contracts_selected": 2, "model_calls": 0,
        "implementation_binding": "init_run hashes current workspace implementation; no release copy",
        "group_sizes": {theory: len(values) for theory, values in names.items()}}
    save(output / "prepare.json", result)
    return result


def negative(root):
    from regen.execution.proof_session_runner import acquire_run_mutation_lock, release_run_mutation_lock
    from regen.planning.theory_outcomes import current_outcome
    output, proof, _ = paths(root)
    started = time.time()
    provenance = read(output / "fixture-provenance.json")
    body = (output / "refutation-fixture.isar").read_text()
    if sha(body) != provenance["adapted_proof_sha256"]:
        raise ValueError("adapted fixture changed")
    lock = acquire_run_mutation_lock(proof, operation="v2-negative-preflight")
    controller, executor, resources = controller_for(proof, NEGATIVE)
    try:
        package, before = controller._state(NEGATIVE)
        by_name = {item["name"]: item for item in package["contracts"]}
        contract = by_name["tree_remove_preserves_wf"]
        sibling = by_name["refine_empty_state"]
        sibling_entry = action(controller,
            {"action": "read_entry", "entry_id": sibling["contract_id"]}, "read-sibling")
        proposal = {"action": "check_refutation", "entry_id": contract["contract_id"], "proof_body": body}
        result = action(controller, proposal, "native-expanded-negative")
        save(output / "negative-tool-result.json", result)
        outcome = current_outcome(controller)
        _, after = controller._state(NEGATIVE)
        if not outcome or outcome["status"] != "refuted" or not result.get("theory_stop"):
            raise ValueError("negative fixture did not produce a durable native refutation")
        if before["proofs"] != after["proofs"] or after["proofs"] or after["status"] == "sealed":
            raise ValueError("refutation changed positive proof or seal state")
        certificate = outcome["details"]["certificate"]
        if certificate["contract"]["exact_statement"] != contract["exact_statement"]:
            raise ValueError("refutation certificate does not bind the current expanded goal")
        row = executor.get(result["task_id"])
        if row["kind"] != "refutation" or row["status"] != "completed":
            raise ValueError("negative native task did not complete")
        count = len(executor.records)
        repeated = action(controller, proposal, "no-repeat-negative")
        blocked = action(controller, {"action": "edit_proof", "entry_id": sibling["contract_id"],
            "expected_hash": sibling_entry["expected_hash"], "proof_body": "by simp"}, "same-theory-control")
        if not repeated.get("theory_stop") or not blocked.get("theory_stop") or len(executor.records) != count:
            raise ValueError("expected unchanged whole-theory stopping behavior")
        save(output / "negative-certificate.json", certificate)
        save(output / "negative-outcome.json", outcome)
        save(output / "same-theory-blocked.json", blocked)
        summary = {"status": "pass", "seconds": time.time() - started,
            "contract_id": contract["contract_id"], "certificate_id": certificate["certificate_id"],
            "exact_expanded_contract_bound": True, "native_receipt": certificate["native_receipt"],
            "positive_proofs": 0, "theory_sealed": False,
            "repeat_native_checks": 0, "same_theory_sibling_blocked": True,
            "same_theory_sibling_contract_id": sibling["contract_id"],
            "same_theory_sibling_truth_status": "not evaluated", "model_calls": 0}
        save(output / "negative.json", summary)
        return summary
    finally:
        try:
            close_all(controller, executor, resources)
        finally:
            release_run_mutation_lock(lock)


def positive(root):
    from regen.execution.proof_session_runner import acquire_run_mutation_lock, release_run_mutation_lock
    from regen.execution.check_executor import CURRENT_RESOURCES
    from regen.planning.batch_file_planner import seal_theory
    from regen.planning.proof_graph import build_graph
    output, proof, _ = paths(root)
    started = time.time()
    if read(output / "negative.json")["status"] != "pass":
        raise ValueError("negative fixture must finish before independent positive fixture")
    lock = acquire_run_mutation_lock(proof, operation="v2-positive-preflight")
    controller, executor, resources = controller_for(proof, POSITIVE)
    try:
        package, _ = controller._state(POSITIVE)
        contract, = package["contracts"]
        ready = controller.handle("list_workspace", {"assigned_only": True}, {})
        if not ready["ready_for_proof_action"] or ready["unsealed_providers"]:
            raise ValueError("independent positive theory unexpectedly blocked")
        # Production generation commits require a genuine model transcript. This
        # fixture uses the ordinary low-level seal validator without fabricating
        # that provenance or publishing a production proof commit.
        _, initial = controller._state(POSITIVE)
        runtime = controller._runtime()
        checker = runtime[4]
        graph = build_graph(package, {}, run_id=controller.run["run_id"] + "-fixture", revision=0)
        state = {"graph": graph, "proofs": {contract["contract_id"]: POSITIVE_BODY},
                 "accepted_order": [contract["contract_id"]], "status": "generating"}
        native = output / "positive-native"
        native.mkdir(exist_ok=False)

        def execute(_task_directory):
            sealed, evidence = seal_theory(proof, controller.run, package,
                native, checker, [], deepcopy(state))
            save(native / "seal-evidence.json", evidence)
            if sealed is None:
                raise ValueError("native positive seal failed: " + str(evidence.get("stage")))
            save(native / "sealed-state.json", sealed)
            return {"fixture_only": True, "state": sealed, "evidence": evidence}

        marker = CURRENT_RESOURCES.set(resources)
        try:
            row = executor.submit(kind="seal", theory=POSITIVE,
                binding={"run_id": controller.run["run_id"], "fixture_only": True,
                         "contract_id": contract["contract_id"], "exact_statement": contract["exact_statement"]},
                request={"fixture_only": True, "proof_body": POSITIVE_BODY}, execute=execute)
        finally:
            CURRENT_RESOURCES.reset(marker)
        while row["status"] not in {"completed", "error", "cancelled"}:
            row = executor.wait([row["task_id"]], timeout=30)[0]
        if row["status"] != "completed":
            raise ValueError("native positive task failed: " + str(row))
        _, after = controller._state(POSITIVE)
        if after != initial:
            raise ValueError("offline fixture mutated the production proof journal")
        result = {"status": "pass", "seconds": time.time() - started,
            "contract_id": contract["contract_id"], "exact_statement": contract["exact_statement"],
            "native_fixture_proofs": 1, "native_fixture_sealed": True,
            "production_positive_proofs": 0, "production_theory_sealed": False,
            "positive_model_to_commit_e2e": "not_exercised",
            "reason": "No genuine model transcript in this disclosed model-free fixture; production provenance guard is preserved.",
            "independent_theory_ready_after_refutation": True,
            "manually_dispatched": True, "automatic_scheduler_exercised": False,
            "tasks": [{key: row.get(key) for key in ("task_id", "kind", "status")}],
            "model_calls": 0, "fresh_generation_credit": 0}
        save(output / "positive.json", result)
        return result
    finally:
        try:
            close_all(controller, executor, resources)
        finally:
            release_run_mutation_lock(lock)


def recover(root):
    from regen.execution.proof_session_runner import acquire_run_mutation_lock, release_run_mutation_lock
    from regen.planning.theory_outcomes import current_outcome
    from regen.execution.repository_execution import progress
    from regen.execution.seal_evidence import validate_seal
    from regen.planning.parallel_file_planner import run_theory_session
    from unittest.mock import patch
    output, proof, _ = paths(root)
    before = task_hashes(proof)
    lock = acquire_run_mutation_lock(proof, operation="v2-fresh-process-recovery")
    recovered = {}
    try:
        for theory in (NEGATIVE, POSITIVE):
            controller, executor, resources = controller_for(proof, theory)
            try:
                outcome = current_outcome(controller)
                _, state = controller._state(theory)
                if theory == NEGATIVE:
                    expected = read(output / "negative.json")
                    if not outcome or outcome["status"] != "refuted" or state["proofs"] or state["status"] == "sealed":
                        raise ValueError("negative stop was not recovered")
                    if outcome["details"]["certificate"]["certificate_id"] != expected["certificate_id"]:
                        raise ValueError("negative certificate changed after recovery")
                    # Use the production session's retained-outcome guard. A raw
                    # new controller does not latch saved outcomes by itself.
                    with patch("regen.infrastructure.codex_workspace_session.CodexWorkspaceSession",
                               side_effect=AssertionError("preflight must not launch a model")), patch(
                            "regen.planning.parallel_file_planner.tempfile.TemporaryDirectory",
                            side_effect=AssertionError("retained stop must occur before auth preparation")):
                        blocked = run_theory_session(proof, NEGATIVE, resources=resources,
                            executor=executor, settings={}, directory=output / "recovered-session",
                            model="gpt-6-astra", generation_strategy_name="sketch-reviewed-v1")
                    if not blocked.get("retained_from_prior_invocation") or blocked["provider"]["status"] != "not_started_retained_theory_outcome":
                        raise ValueError("production session did not honor the recovered stop")
                    save(output / "recovered-session-result.json", blocked)
                    recovered[theory] = {"outcome": "refuted", "certificate_id": expected["certificate_id"],
                        "positive_proofs": 0, "production_session_not_started": True,
                        "raw_controller_stop_latch_tested": False}
                else:
                    if outcome or state["status"] == "sealed" or state["proofs"]:
                        raise ValueError("offline positive fixture polluted production proof state")
                    sealed = read(output / "positive-native/sealed-state.json")
                    evidence = read(output / "positive-native/seal-evidence.json")
                    validate_seal(sealed, evidence, expected_imports=[controller.run["base_import"]],
                                  base_session=controller.run["base_session"], runtime=controller.run)
                    recovered[theory] = {"fixture_seal_replay": "pass", "production_positive_proofs": 0,
                        "production_theory_sealed": False, "fixture_positive_proofs": len(sealed["proofs"])}
            finally:
                close_all(controller, executor, resources)
        save(output / "recovered-progress.json", progress(proof))
    finally:
        release_run_mutation_lock(lock)
    if before != task_hashes(proof):
        raise ValueError("fresh-process recovery changed native task evidence")
    result = {"status": "pass", "pid": os.getpid(), "theories": recovered,
        "native_checks_added": 0, "task_evidence_unchanged": True, "model_calls": 0}
    save(output / "recovery.json", result)
    return result


def finish(root):
    output, proof, _ = paths(root)
    save(output / "v2-input-identity.json", validate_dataset(root))
    command = [sys.executable, str(Path(__file__).resolve()), "--root", str(root), "--phase", "recover"]
    child = subprocess.run(command, capture_output=True, text=True, timeout=60)
    (output / "recovery-process.log").write_text(child.stdout + child.stderr)
    if child.returncode:
        raise RuntimeError("fresh-process recovery failed; inspect recovery-process.log")
    recovery = read(output / "recovery.json")
    if recovery["pid"] == os.getpid():
        raise ValueError("recovery did not use a new process")
    result = {"status": "pass", "infrastructure_preflight": "pass", "full_campaign_ready": False,
        "pid": os.getpid(), "recovery_pid": recovery["pid"],
        "prepare": read(output / "prepare.json"), "negative": read(output / "negative.json"),
        "positive": read(output / "positive.json"), "recovery": recovery,
        "positive_model_to_commit_e2e": "not_exercised",
        "model_calls": 0, "fresh_generation_credit": 0,
        "limitations": ["Whole-theory stop still blocks the unproved sibling.",
            "Independent theory dispatched manually; automatic scheduling and explicit stage barriers were not tested.",
            "Positive native seal fixture is not a production generation commit; no model provenance was fabricated.",
            "No full-spec proof campaign or LLM generation was performed."]}
    save(output / "validation.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--certificate", type=Path, default=DEFAULT_CERTIFICATE)
    parser.add_argument("--phase", choices=("prepare", "negative", "positive", "recover", "finish", "all"), default="all")
    args = parser.parse_args()
    root = args.root.resolve()
    validate_dataset(root)
    if args.phase == "prepare":
        result = prepare(root, args.certificate.resolve())
    elif args.phase == "all":
        prepare(root, args.certificate.resolve())
        negative(root)
        positive(root)
        result = finish(root)
    else:
        result = globals()[args.phase](root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
