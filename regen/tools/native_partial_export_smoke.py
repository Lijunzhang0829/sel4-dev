"""Native partial-export regression with disclosed synthetic HOL fixtures.

No model is called, no benchmark target is solved, and no production generation
journal is created. A false synthetic contract receives an actual native
refutation; the production disposition and seal validators then certify only
the independently proved positive subset. Consumers import that exact source.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import time
from types import SimpleNamespace

from regen.execution.check_executor import CheckExecutor, CURRENT_RESOURCES, NativeResources
from regen.execution.contract_refutation import check_refutation
from regen.execution.checker_policy import PROOF_OR_REFUTATION_POLICY
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.proof_commits import digest
from regen.execution.proof_session_runner import generated_checkpoint_theory
from regen.execution.seal_evidence import validate_seal
from regen.infrastructure.resource_budget import clone_heaps
from regen.planning.batch_file_planner import seal_theory
from regen.planning.proof_graph import build_graph
from regen.planning.theory_outcomes import _publish, current_dispositions, record_unresolved
from regen.preparation.checker_context import load_context


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def contract(key, proposition):
    return {"contract_id": key, "name": key, "exact_statement": f'lemma {key}: "{proposition}"',
            "contract_owned_context": []}


def package(theory, contracts):
    return {"theory": theory, "file_slot": theory + ".thy", "contracts": contracts}


def fixture_state(pkg, proofs, run_id):
    return {"graph": build_graph(pkg, {}, run_id=run_id, revision=0),
            "proofs": dict(proofs), "accepted_order": list(proofs), "status": "generating"}


def run(dataset, output):
    dataset, output = Path(dataset).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    ctx = load_context(dataset)
    clone_heaps(ctx["heaps"], output / "heaps")
    resources = NativeResources(1, 2, 1, cpus=4, memory_mb=8192,
        pids_limit=1024, provider_build_timeout=600, build_ml_heap_mb=2048)
    executor = CheckExecutor(output / "check-tasks", workers=1)
    marker = CURRENT_RESOURCES.set(resources)
    started = time.monotonic()
    report = {"status": "running", "fixture_only": True, "model_calls": 0,
              "benchmark_contracts_proved": 0, "production_generation_commits": 0,
              "dataset": str(dataset), "checker_context_id": ctx["checker_context_id"],
              "cases": []}
    checkers = []
    runtime = {"run_id": output.name + "-synthetic", "contract_outcome_policy": "contract-dispositions-v1",
               "unresolved_evidence_policy": PROOF_OR_REFUTATION_POLICY,
               "premise_snapshot": str(ctx["premise_audit_context_path"]),
               "base_session": ctx["base_session"], "base_import": ctx["base_import"]}
    save(output / "runtime.json", runtime)

    def checker(label, previous=()):
        work = output / label
        c = IsabelleChecker(work, work / "isabelle", image=ctx["image"],
            architecture=ctx["architecture"], timeout=600,
            previous_theories=list(previous), import_all_previous=True,
            heaps_dir=output / "heaps", l4v_dir=ctx["l4v"],
            semantic_workspace=ctx["semantic_workspace"], base_session=ctx["base_session"],
            base_import=ctx["base_import"], base_catalog=ctx["base_catalog"],
            isabelle_dir=ctx["isabelle"], generated_dir=work / "generated")
        checkers.append(c)
        return c

    def task(label, kind, execute):
        row = executor.submit(kind=kind, theory=label,
            binding={"run_id": runtime["run_id"], "fixture_only": True},
            request={"fixture_case": label, "model_generated": False}, execute=lambda _: execute())
        while row["status"] not in {"completed", "cancelled", "error", "interrupted"}:
            row = executor.wait([row["task_id"]], timeout=30)[0]
        if row["status"] != "completed":
            raise RuntimeError(label + ": " + row["status"] + ": " + row.get("diagnostic", ""))
        return row["result"]

    def record(name, passed, **details):
        report["cases"].append({"name": name, "passed": bool(passed), **details})
        save(output / "report.json", report)
        print(json.dumps({"case": name, "passed": bool(passed)}), flush=True)
        if not passed:
            raise AssertionError("native partial export smoke failed: " + name)

    def refute_and_dispose(label, c, pkg, state, key):
        selected = next(item for item in pkg["contracts"] if item["contract_id"] == key)
        controller = SimpleNamespace(root=output / label / "disposition-fixture",
            theory=pkg["theory"], run=runtime, _state=lambda _: (pkg, state))

        def execute():
            result = check_refutation(c, selected, "by simp", output / label / "negative")
            if result["status"] != "refuted":
                raise AssertionError("synthetic false contract was not refuted")
            _publish(controller, status="refuted", reason="Native synthetic false-contract fixture",
                origin="native_checker", details={"certificate": result["certificate"]},
                entry_ids=[key], metadata={"fixture_only": True, "model_generated": False})
            return result

        result = task(label + "_Refutation", "refutation", execute)
        outcomes = current_dispositions(controller)
        record(label + "_native_refutation", set(outcomes) == {key} and
               outcomes[key]["status"] == "refuted", certificate_id=result["certificate"]["certificate_id"])
        save(output / label / "dispositions.json", outcomes)
        return outcomes

    def seal(label, c, pkg, state, outcomes):
        work = output / label

        def execute():
            sealed, evidence = seal_theory(output, runtime, pkg, work, c,
                list(c.previous_theories), deepcopy(state), dispositions=outcomes)
            save(work / "seal-evidence.json", evidence)
            if sealed is None:
                raise AssertionError("partial seal failed: " + str(evidence.get("stage")))
            save(work / "sealed-state.json", sealed)
            return {"state": sealed, "evidence": evidence}

        result = task(label + "_Seal", "seal", execute)
        sealed, evidence = result["state"], result["evidence"]
        validate_seal(sealed, evidence, expected_imports=c.imports(),
            base_session=ctx["base_session"], runtime=runtime, expected_package=pkg)
        certificates = evidence["dependencies"]["complete_theory_audit"]["proof_dependencies"]
        record(label + "_partial_seal", sealed["status"] == "exported_partial" and
               evidence["seal_evidence_version"] == 4 and
               set(certificates) == set(state["proofs"]) and
               all(not value["has_skip_proof"] for value in certificates.values()),
               exported_ids=sorted(state["proofs"]), omitted_ids=sorted(outcomes),
               seal_id=sealed["seal_id"], all_exported_proofs_no_skip=True)
        return sealed, evidence

    def consumer_check(c, name, statement, proof, expected, diagnostic=None):
        pkg = package(name, [contract("consumer", statement)])
        state = fixture_state(pkg, {"consumer": proof}, runtime["run_id"])
        result = task(name, "proof_batch", lambda: c.check_complete(state["graph"], state["proofs"]))
        record(name, result["verdict"] == expected and
               (diagnostic is None or diagnostic in result.get("diagnostic", "")),
               verdict=result["verdict"], expected=expected, log=result["log"],
               diagnostic_required=diagnostic)

    def rejects(name, sealed, evidence, pkg, c, change):
        altered_state, altered_evidence = deepcopy(sealed), deepcopy(evidence)
        change(altered_state, altered_evidence)
        # Rebind this outer checksum so semantic checks, not just the envelope,
        # must reject the altered evidence.
        altered_state["seal_id"] = "theory-seal:" + digest(altered_evidence)[:20]
        try:
            validate_seal(altered_state, altered_evidence, expected_imports=c.imports(),
                base_session=ctx["base_session"], runtime=runtime, expected_package=pkg)
        except ValueError as error:
            record(name, True, rejection=str(error), outer_checksum_rebound=True)
        else:
            record(name, False)

    try:
        pkg = package("Partial_Synthetic_Provider", [contract("smoke_a", "(0::nat) = 0"),
            contract("smoke_b", "(0::nat) = 1"), contract("smoke_c", "(2::nat) = 2")])
        state = fixture_state(pkg, {"smoke_a": "by simp", "smoke_c": "by simp"}, runtime["run_id"])
        c = checker("provider")
        controller = SimpleNamespace(root=output, run=runtime, theory=pkg['theory'],
            _state=lambda _: (pkg, state))
        try:
            record_unresolved(controller, reason='Synthetic ordinary failure', origin='model',
                              entry_ids=['smoke_b'])
        except ValueError as error:
            record('ordinary_failure_cannot_publish_unknown', 'disables report_unresolved' in str(error))
        else:
            record('ordinary_failure_cannot_publish_unknown', False)
        outcomes = refute_and_dispose("provider", c, pkg, state, "smoke_b")
        from regen.execution.partial_theory_export import build_export_scope
        pending = deepcopy(state)
        pending['proofs'].pop('smoke_c')
        pending['accepted_order'].remove('smoke_c')
        try:
            build_export_scope(pkg, pending, outcomes, runtime=runtime)
        except ValueError as error:
            record('refuted_sibling_cannot_omit_pending_contract',
                   'every unproved fixed contract' in str(error))
        else:
            record('refuted_sibling_cannot_omit_pending_contract', False)
        sealed, evidence = seal("provider", c, pkg, state, outcomes)
        source = evidence["source_text"]
        record("positive_source_excludes_refuted_fact", "lemma smoke_b" not in source and
               "sorry" not in source and set(node["id"] for node in sealed["graph"]["nodes"]) ==
               {"smoke_a", "smoke_b", "smoke_c"}, original_contracts_retained=3)

        provider_name = generated_checkpoint_theory(pkg["file_slot"])
        downstream = checker("consumer", [provider_name])
        shutil.copyfile(c.generated_dir / (provider_name + ".thy"),
                        downstream.generated_dir / (provider_name + ".thy"))
        built = task("Consumer_Provider_Build", "seal", lambda: downstream.build_generated_session([provider_name]))
        record("consumer_provider_build", built["verdict"] == "green", log=built["log"])
        consumer_check(downstream, "Consumer_Uses_A_C", "(0::nat) = 0 & (2::nat) = 2",
            f"using {provider_name}.smoke_a {provider_name}.smoke_c by blast", "green")
        consumer_check(downstream, "Consumer_Cannot_Use_B", "(0::nat) = 1",
            f"by (rule {provider_name}.smoke_b)", "red", "Undefined fact")
        consumer_check(downstream, "Consumer_No_Implicit_False_Fact", "False", "by simp", "red")

        rejects("partial_cannot_claim_fully_proved", sealed, evidence, pkg, c,
                lambda s, e: s.update(status="sealed"))
        rejects("cannot_omit_fixed_contract_from_scope", sealed, evidence, pkg, c,
                lambda s, e: e["export_scope"]["package"]["contracts"].pop(1))
        rejects("cannot_export_refuted_contract", sealed, evidence, pkg, c,
                lambda s, e: e["export_scope"]["exported_node_ids"].append("smoke_b"))
        rejects("cannot_drop_negative_disposition", sealed, evidence, pkg, c,
                lambda s, e: e["export_scope"]["dispositions"].clear())
        rejects("cannot_accept_skip_proof_receipt", sealed, evidence, pkg, c,
                lambda s, e: e["dependencies"]["complete_theory_audit"]["proof_dependencies"]["smoke_a"].update(has_skip_proof=True))

        empty_pkg = package("Empty_Synthetic_Provider", [contract("empty_false", "(0::nat) = 1")])
        empty_state = fixture_state(empty_pkg, {}, runtime["run_id"])
        empty_checker = checker("empty-provider")
        empty_outcomes = refute_and_dispose("empty-provider", empty_checker, empty_pkg, empty_state, "empty_false")
        empty_sealed, empty_evidence = seal("empty-provider", empty_checker, empty_pkg, empty_state, empty_outcomes)
        record("empty_positive_export_is_not_repository_success", not empty_sealed["proofs"] and
               empty_sealed["status"] == "exported_partial" and
               not empty_evidence["export_scope"]["exported_node_ids"])
        empty_name = generated_checkpoint_theory(empty_pkg["file_slot"])
        empty_consumer = checker("empty-consumer", [empty_name])
        shutil.copyfile(empty_checker.generated_dir / (empty_name + ".thy"),
                        empty_consumer.generated_dir / (empty_name + ".thy"))
        built = task("Empty_Consumer_Provider_Build", "seal", lambda: empty_consumer.build_generated_session([empty_name]))
        record("empty_consumer_provider_build", built["verdict"] == "green", log=built["log"])
        consumer_check(empty_consumer, "Consumer_After_Empty_Provider", "True", "by simp", "green")
        report["status"] = "pass"
    except BaseException as error:
        report.update(status="fail", error_type=type(error).__name__, diagnostic=str(error))
        raise
    finally:
        try:
            for c in checkers:
                c.close_prefix()
            executor.close()
        finally:
            resources.close()
            CURRENT_RESOURCES.reset(marker)
            report.update(seconds=time.monotonic() - started,
                tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
            save(output / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.dataset, args.output)
    print(json.dumps({"status": result["status"], "cases": len(result["cases"]),
                      "seconds": result["seconds"], "model_calls": 0}), flush=True)


if __name__ == "__main__":
    main()
