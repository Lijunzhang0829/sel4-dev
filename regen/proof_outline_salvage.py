#!/usr/bin/env python3
"""Build a local Isar proof outline from failed single-node attempts."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from regen.llm_transcript import allocate_artifact_prefix, call_llm
from regen.module_run_log import write_llm_module_log
from regen.proof_graph import parse_json_response

ALLOWED_KINDS = {"assume", "obtain", "have", "cases", "show", "automation"}
FORBIDDEN_TEXT = re.compile(
    r"^\s*(?:lemma|theorem|corollary|proposition|imports?)\b"
    r"|\b(?:sorry|oops|axiomatization|admit)\b",
    re.I,
)
KERNEL_OBJECT = re.compile(
    r"\bKO(?:Endpoint|Notification|KernelData|UserDataDevice|UserData|TCB|CTE|Arch)\b"
)
QUALIFIED_NAME = re.compile(
    r"(?<![A-Za-z0-9_'])[A-Z][A-Za-z0-9_']*"
    r"(?:\.[A-Za-z0-9_']+)+(?![A-Za-z0-9_'])"
)


def _tail(value: str, limit: int = 10000) -> str:
    return value if len(value) <= limit else value[-limit:]


def failure_class(diagnostic: str) -> str:
    lowered = diagnostic.lower()
    if "undefined fact" in lowered:
        return "undefined_fact"
    if "timeout" in lowered:
        return "timeout"
    if "failed to apply initial proof method" in lowered:
        return "initial_method_failure"
    if "failed to finish proof" in lowered:
        return "unfinished_goal"
    return "isabelle_rejection"


def attempt_evidence(attempt: dict[str, Any]) -> dict[str, Any]:
    proof = str(attempt.get("proof_body", ""))
    diagnostic = str(attempt.get("check", {}).get("diagnostic", ""))
    return {
        "attempt": int(attempt.get("attempt", 0)),
        "proof_body": _tail(proof),
        "diagnostic": _tail(diagnostic),
        "failure_class": failure_class(diagnostic),
        "structured_commands": {
            command: len(re.findall(rf"\b{command}\b", proof))
            for command in ("obtain", "have", "show", "cases")
        },
        "kernel_object_branches": sorted(set(KERNEL_OBJECT.findall(diagnostic))),
        "retrieved_premise_names": list(
            dict.fromkeys(attempt.get("retrieved_premise_names", []))
        )[:64],
    }


def build_salvage_request(
    node: dict[str, Any], attempts: list[dict[str, Any]], *,
    stepwise_residual: str = "", previous_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evidence = [attempt_evidence(item) for item in attempts[-5:]]
    legal_names = sorted({
        name for item in evidence for name in item["retrieved_premise_names"]
    })
    return {
        "node": {
            "id": node["id"], "role": node["role"],
            "statement": node["statement"], "strategy": node.get("strategy", ""),
        },
        "failed_attempts": evidence,
        "stepwise_residual": _tail(stepwise_residual),
        "previous_local_outline": previous_plan,
        "eligible_premise_names": legal_names,
        "constraints": {
            "proof_body_only": True, "fixed_statement": True,
            "may_add_generated_theorems": False, "may_change_graph": False,
            "max_local_steps": 8,
        },
    }


def salvage_prompt(request: dict[str, Any]) -> str:
    return """You are the Local Proof-Outline/Salvage module inside an Isabelle Lemma Prover.

Analyze failed candidates for one fixed theorem. Recover useful local proof
structure and propose a bounded sequence of Isar-local assume/obtain/have/cases/
show steps. This is not theorem-graph planning. Do not introduce a lemma,
helper, import, theorem statement, graph operation, or complete proof body.

Every premise name must come from eligible_premise_names. Preserve a proof
fragment only by copying an exact contiguous fragment from a failed proof_body.
Prefer delaying broad simp/clarsimp until a discriminator has removed irrelevant
datatype branches. When an elimination rule first exposes a generic value,
plan separate steps to (1) obtain that generic value and its guards, (2) prove
the required datatype constructor/projection, and (3) specialize the guards to
that constructor before using downstream premises. Each local_statement is an
intermediate proposition or a short schematic objective, never an outer
declaration.

Return only JSON:
{
  "diagnosis": "...",
  "preserve": [{"attempt": 1, "fragment": "...", "reason": "..."}],
  "avoid": ["..."],
  "local_steps": [{
    "id": "local-1",
    "kind": "assume|obtain|have|cases|show|automation",
    "local_statement": "...",
    "depends_on": [],
    "premise_names": ["Qualified.fact"],
    "method_hint": "..."
  }],
  "repair_directives": ["..."]
}

INPUT:
""" + json.dumps(request, ensure_ascii=False, indent=2)


def validate_salvage_plan(
    plan: dict[str, Any], request: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(plan.get("diagnosis"), str) or not plan["diagnosis"].strip():
        raise ValueError("salvage plan requires a diagnosis")
    if "proof_body" in plan or "graph_operation" in plan:
        raise ValueError("salvage plan crossed its proof-outline authority")
    steps = plan.get("local_steps")
    if not isinstance(steps, list) or not steps or len(steps) > 8:
        raise ValueError("salvage plan requires 1..8 local steps")
    legal = set(request["eligible_premise_names"])
    known: set[str] = set()
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("local step must be an object")
        step_id = step.get("id")
        if not isinstance(step_id, str) or not re.fullmatch(r"local-[1-9][0-9]*", step_id):
            raise ValueError("local step IDs must use local-N")
        if step_id in known:
            raise ValueError("duplicate local step ID")
        if step.get("kind") not in ALLOWED_KINDS:
            raise ValueError("invalid local step kind")
        statement = step.get("local_statement")
        if not isinstance(statement, str) or not statement.strip():
            raise ValueError("local step requires a statement/objective")
        if FORBIDDEN_TEXT.search(statement):
            raise ValueError("local step contains an outer-theory command")
        dependencies = step.get("depends_on", [])
        if not isinstance(dependencies, list) or any(item not in known for item in dependencies):
            raise ValueError("local step dependency must point backward")
        names = step.get("premise_names", [])
        if not isinstance(names, list) or any(name not in legal for name in names):
            raise ValueError("local step cites an ineligible premise")
        known.add(step_id)
    mentioned = set(QUALIFIED_NAME.findall(
        json.dumps(plan, ensure_ascii=False)
    ))
    node_identity = str(request["node"]["id"])
    ineligible = {
        name for name in mentioned
        if name not in legal
        and name not in node_identity
        and not any(item.startswith(name + "(") for item in legal)
    }
    if ineligible:
        raise ValueError(
            "salvage plan mentions an ineligible qualified premise: "
            + ", ".join(sorted(ineligible))
        )
    proofs = {
        int(item["attempt"]): item["proof_body"]
        for item in request["failed_attempts"]
    }
    preserve = plan.get("preserve", [])
    if not isinstance(preserve, list):
        raise ValueError("preserve must be a list")
    for item in preserve:
        attempt = item.get("attempt")
        fragment = item.get("fragment")
        if attempt not in proofs or not isinstance(fragment, str) or fragment not in proofs[attempt]:
            raise ValueError("preserved fragment is not copied from its attempt")
    for field in ("avoid", "repair_directives"):
        values = plan.get(field, [])
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise ValueError(f"{field} must be a string list")
    return plan


def deterministic_fallback(request: dict[str, Any], error: str = "") -> dict[str, Any]:
    node = request["node"]
    failed = request["failed_attempts"][-1]
    failure = failed["failure_class"].replace("_", " ")
    diagnostic = failed.get("diagnostic", "")
    type_diagnostics = [
        item.get("diagnostic", "") for item in request["failed_attempts"]
        if "Introduced fixed type variable" in item.get("diagnostic", "")
        or "incompatible operand type" in item.get("diagnostic", "")
    ]
    if type_diagnostics:
        diagnosis = (
            f"Node {node['id']} has an underconstrained Isar-local variable. "
            "A prior candidate reached a typed operator with an incompatible "
            "operand type; this proof-body typing error remains authoritative "
            "even if a later candidate fails earlier."
        )
        if error:
            diagnosis += f" LLM outline unavailable: {error}"
        return {
            "diagnosis": diagnosis,
            "preserve": [],
            "avoid": [
                "Do not rely on a polymorphic getter occurrence to infer the local result type.",
                "Do not change the fixed theorem statement or add a helper for this local typing error.",
            ],
            "local_steps": [{
                "id": "local-1",
                "kind": "assume",
                "local_statement": "Declare the concrete result variable with the exact operand type required by the relation in the goal.",
                "depends_on": [],
                "premise_names": [],
                "method_hint": "Add an explicit Isabelle type annotation in the fix or obtain command before replaying the accepted semantic prefix.",
            }],
            "repair_directives": [
                "Preserve the corres introduction and definition-expansion structure from the furthest candidate.",
                "Repair the local variable type first, then address only the next Isabelle residual.",
            ],
            "source": "deterministic-fallback",
        }
    if "Failed to refine any pending goal" in diagnostic:
        diagnosis = (
            f"Node {node['id']} reached a closed proof state before the "
            "rejected command. This is a local Isar control-flow surplus, not "
            "missing theorem structure or premises."
        )
        if error:
            diagnosis += f" LLM outline unavailable: {error}"
        return {
            "diagnosis": diagnosis,
            "preserve": [],
            "avoid": [
                "Do not add a helper, premise, case split, or broader automation.",
                "Do not replay commands after Isabelle first reports that no goal is pending.",
            ],
            "local_steps": [{
                "id": "local-1",
                "kind": "automation",
                "local_statement": "End the proof at the last accepted command before the rejected no-pending-goal command.",
                "depends_on": [],
                "premise_names": [],
                "method_hint": "Keep the accepted prefix and remove the first surplus command and everything after it; retain only the structural proof terminator required by Isar.",
            }],
            "repair_directives": [
                "Treat no-pending-goal as evidence that the proof prefix already closed the target.",
                "Shorten the newest candidate; do not strengthen the tactic or change the theorem graph.",
            ],
            "source": "deterministic-fallback",
        }
    diagnosis = (
        f"Node {node['id']} remains red with {failure}. The deterministic "
        "fallback has no authority to infer theorem-specific semantics; keep the "
        "fixed statement and repair the first rejected local block shown by the "
        "latest Isabelle diagnostic."
    )
    if error:
        diagnosis += f" LLM outline unavailable: {error}"
    return {
        "diagnosis": diagnosis,
        "preserve": [],
        "avoid": [
            "Do not introduce facts or semantic side conditions absent from the current statement and residual.",
            "Do not restart from an older theorem-specific outline.",
            "Do not use broad automation before the failing branch has been isolated.",
        ],
        "local_steps": [
            {
                "id": "local-1", "kind": "assume",
                "local_statement": "Name the assumptions of the current fixed statement and retain their exact types.",
                "depends_on": [], "premise_names": [],
                "method_hint": "Use only assumptions that occur in the current node statement.",
            },
            {
                "id": "local-2", "kind": "cases",
                "local_statement": "Re-enter the latest rejected branch using explicitly named and typed case variables.",
                "depends_on": ["local-1"],
                "premise_names": [],
                "method_hint": "Repair the first command named by Isabelle; avoid anonymous generated case names.",
            },
            {
                "id": "local-3", "kind": "have",
                "local_statement": "Prove only the local fact needed to discharge that rejected branch.",
                "depends_on": ["local-2"],
                "premise_names": [],
                "method_hint": "Prefer direct definition unfolding and a narrow simp set derived from the latest residual.",
            },
            {
                "id": "local-4", "kind": "show",
                "local_statement": "Resume the surrounding proof from the repaired branch and close the fixed theorem conclusion.",
                "depends_on": ["local-3"],
                "premise_names": [],
                "method_hint": "Reuse the accepted prefix of the newest candidate and change only the failing local block.",
            },
        ],
        "repair_directives": [
            "Treat the latest Isabelle residual as authoritative for this node.",
            "Retain the furthest-passing candidate and repair only its first rejected local block.",
            "Request graph revision only after bounded local repairs show a structural obstruction.",
        ],
        "source": "deterministic-fallback",
    }


def plan_local_outline(
    node: dict[str, Any], attempts: list[dict[str, Any]], *,
    output_prefix: Path, provider: str, model: str, timeout: int,
    theory: str, stepwise_residual: str = "",
    previous_plan: dict[str, Any] | None = None,
    module_log_path: Path | None = None,
) -> dict[str, Any]:
    output_prefix = allocate_artifact_prefix(output_prefix)
    request = build_salvage_request(
        node, attempts, stepwise_residual=stepwise_residual,
        previous_plan=previous_plan,
    )
    request_path = Path(str(output_prefix) + ".request.json")
    plan_path = Path(str(output_prefix) + ".outline.json")
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(
        json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if provider == "deterministic":
        plan = deterministic_fallback(request)
        plan["provider"] = "deterministic"
        plan["sealable_generation"] = True
    else:
        try:
            response = call_llm(
                provider, salvage_prompt(request), output_prefix,
                model=model, timeout=timeout, theory=theory,
                stage=f"local-proof-outline:{node['id']}",
            )
            plan = validate_salvage_plan(parse_json_response(response), request)
            plan["source"] = "llm"
            meta_path = Path(str(output_prefix) + ".meta.json")
            transcript_meta = (
                json.loads(meta_path.read_text(encoding="utf-8"))
                if meta_path.is_file() else {}
            )
            plan["provider"] = transcript_meta.get("provider")
            plan["sealable_generation"] = bool(
                transcript_meta.get("sealable_generation")
            )
        except Exception as error:
            plan = deterministic_fallback(request, str(error))
            plan["provider"] = provider
            plan["sealable_generation"] = False
    plan["request_path"] = str(request_path)
    plan_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if module_log_path is not None and Path(str(output_prefix) + ".meta.json").is_file():
        write_llm_module_log(
            output_prefix, module_log_path,
            module="Local Proof Outline Salvage",
            run_name=f"{theory}--{node['id']}",
            context={"node_id": node["id"], "attempt_count": len(attempts)},
            outputs={"request": request_path, "outline": plan_path},
        )
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--attempts", type=Path, required=True)
    parser.add_argument("--output-prefix", type=Path, required=True)
    parser.add_argument("--provider", default="codex")
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    graph = json.loads(args.graph.read_text())
    node = next(item for item in graph["nodes"] if item["id"] == args.node_id)
    attempts = json.loads(args.attempts.read_text())
    plan = plan_local_outline(
        node, attempts, output_prefix=args.output_prefix,
        provider=args.provider, model=args.model, timeout=args.timeout,
        theory=graph["theory"],
    )
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
