#!/usr/bin/env python3
"""Plan a bounded dependency-aware outline for one fixed theorem."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from regen.llm_transcript import allocate_artifact_prefix, call_llm
from regen.module_run_log import write_llm_module_log
from regen.proof_graph import parse_json_response


ALLOWED_PHASES = {"introduce", "compose", "structure", "glue", "finish"}
FORBIDDEN = re.compile(
    r"\b(?:sorry|oops|axiomatization)\b"
    r"|^\s*(?:lemma|theorem|corollary|proposition)\s+[A-Za-z][A-Za-z0-9_']*\s*:",
    re.I,
)


def build_outline_request(
    node: dict[str, Any], dependencies: list[dict[str, Any]],
    legal_premises: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "node": {
            "id": node["id"], "role": node["role"],
            "statement": node["statement"],
            "rough_strategy": node.get("strategy", ""),
        },
        "green_generated_dependencies": dependencies,
        "retrieved_legal_premises": [{
            "canonical_name": item["canonical_name"],
            "exact_statement": item["exact_statement"],
            "owner_theory": item["owner_theory"],
            "attributes": item.get("attributes", []),
        } for item in legal_premises[:24]],
        "constraints": {
            "fixed_statement": True,
            "proof_body_forbidden": True,
            "new_theorem_forbidden": True,
            "graph_change_forbidden": True,
            "external_premises_must_be_retrieved": True,
            "max_steps": 8,
            "preserve_composition_before_unfolding": True,
        },
    }


def outline_prompt(request: dict[str, Any]) -> str:
    return """You are the Local Proof Outline module for one fixed Isabelle theorem.

This is a closed-payload planning call. Do not invoke tools, skills, shell
commands, file reads, repository search, or network search. Use only INPUT.
Answer WHAT PROOF STRUCTURE SHOULD PROBABLY BE ATTEMPTED. Do not write proof
commands or a proof body. Prefer composition through listed GREEN generated
dependencies before unfolding operation semantics. Retrieved premises answer
what exists; mention one only when its role in the structure is clear. Leave
local simp/wp/case tactics to the Automation Advisor and Lemma Prover.

Return JSON only:
{
  "diagnosis": "short structural assessment",
  "local_steps": [{
    "id": "outline-1",
    "phase": "introduce|compose|structure|glue|finish",
    "objective": "semantic milestone, not an Isabelle command",
    "depends_on": [],
    "generated_dependencies": ["exact generated theorem name"],
    "premise_names": ["exact retrieved premise name"],
    "automation_policy": "none|residual-only"
  }],
  "avoid": ["structural search direction to avoid"]
}

INPUT:
""" + json.dumps(request, ensure_ascii=False, indent=2)


def validate_outline(plan: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(plan.get("diagnosis"), str) or not plan["diagnosis"].strip():
        raise ValueError("outline requires a diagnosis")
    if "proof_body" in plan:
        raise ValueError("outline cannot contain a proof body")
    steps = plan.get("local_steps")
    if not isinstance(steps, list) or not steps or len(steps) > 8:
        raise ValueError("outline requires 1..8 steps")
    legal_dependencies = {
        item["name"] for item in request["green_generated_dependencies"]
    }
    legal_premises = {
        item["canonical_name"] for item in request["retrieved_legal_premises"]
    }
    known: set[str] = set()
    for step in steps:
        step_id = step.get("id")
        if not isinstance(step_id, str) or not re.fullmatch(r"outline-[1-9][0-9]*", step_id):
            raise ValueError("outline step IDs must use outline-N")
        if step_id in known:
            raise ValueError("duplicate outline step ID")
        if step.get("phase") not in ALLOWED_PHASES:
            raise ValueError("invalid outline phase")
        objective = step.get("objective")
        if not isinstance(objective, str) or not objective.strip() or FORBIDDEN.search(objective):
            raise ValueError("outline objective must describe a milestone, not proof code")
        predecessors = step.get("depends_on", [])
        if not isinstance(predecessors, list) or any(item not in known for item in predecessors):
            raise ValueError("outline dependencies must point backward")
        generated = step.get("generated_dependencies", [])
        premises = step.get("premise_names", [])
        if not isinstance(generated, list) or not set(generated) <= legal_dependencies:
            raise ValueError("outline cites an unavailable generated dependency")
        if not isinstance(premises, list) or not set(premises) <= legal_premises:
            raise ValueError("outline cites an unretrieved premise")
        if step.get("automation_policy", "none") not in {"none", "residual-only"}:
            raise ValueError("invalid automation policy")
        known.add(step_id)
    avoid = plan.get("avoid", [])
    if not isinstance(avoid, list) or any(not isinstance(item, str) for item in avoid):
        raise ValueError("outline avoid field must be a string list")
    return plan


def deterministic_outline(request: dict[str, Any], error: str = "") -> dict[str, Any]:
    node = request["node"]
    dependencies = request["green_generated_dependencies"]
    steps = []
    prior = []
    if node["role"] == "contract" or "corres" in node["statement"]:
        intro = next((item for item in request["retrieved_legal_premises"]
                      if item["canonical_name"].endswith("corres_underlyingI")), None)
        steps.append({
            "id": "outline-1", "phase": "introduce",
            "objective": "Enter the correspondence introduction structure while preserving both programs.",
            "depends_on": [], "generated_dependencies": [],
            "premise_names": [intro["canonical_name"]] if intro else [],
            "automation_policy": "none",
        })
        prior = ["outline-1"]
    for dependency in dependencies:
        step_id = f"outline-{len(steps) + 1}"
        steps.append({
            "id": step_id, "phase": "compose",
            "objective": f"Establish the sub-obligation supplied by generated dependency {dependency['name']}.",
            "depends_on": prior, "generated_dependencies": [dependency["name"]],
            "premise_names": [], "automation_policy": "none",
        })
        prior = [step_id]
    steps.append({
        "id": f"outline-{len(steps) + 1}", "phase": "glue",
        "objective": "Close only the residual definitional and structural glue after dependency composition.",
        "depends_on": prior, "generated_dependencies": [],
        "premise_names": [], "automation_policy": "residual-only",
    })
    return {
        "diagnosis": (
            "Use the certified dependency order before local automation."
            + (f" Planner fallback reason: {error}" if error else "")
        ),
        "local_steps": steps[:8],
        "avoid": [
            "Do not unfold the complete operation semantics before trying generated dependencies.",
            "Do not use broad automation to discover the dependency composition order.",
        ],
        "source": "deterministic-fallback",
    }


def plan_initial_outline(
    node: dict[str, Any], dependencies: list[dict[str, Any]],
    legal_premises: list[dict[str, Any]], *, output_prefix: Path,
    provider: str, model: str, timeout: int, theory: str,
    module_log_path: Path | None = None,
) -> dict[str, Any]:
    prefix = allocate_artifact_prefix(output_prefix)
    request = build_outline_request(node, dependencies, legal_premises)
    request_path = Path(str(prefix) + ".request.json")
    outline_path = Path(str(prefix) + ".outline.json")
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n")
    try:
        response = call_llm(
            provider, outline_prompt(request), prefix, model=model,
            timeout=timeout, theory=theory,
            stage=f"initial-local-proof-outline:{node['id']}",
            clean_runtime=True,
        )
        plan = validate_outline(parse_json_response(response), request)
        plan["source"] = "llm"
        meta_path = Path(str(prefix) + ".meta.json")
        if meta_path.is_file():
            meta = json.loads(meta_path.read_text())
            plan["provider"] = meta.get("provider")
            plan["model"] = meta.get("model")
            plan["sealable_generation"] = bool(meta.get("sealable_generation"))
    except Exception as error:
        plan = deterministic_outline(request, str(error))
    plan["request_path"] = str(request_path)
    outline_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    if module_log_path is not None and Path(str(prefix) + ".meta.json").is_file():
        write_llm_module_log(
            prefix, module_log_path, module="Local Proof Outline",
            run_name=f"{theory}--{node['id']}",
            context={"node_id": node["id"], "role": node["role"]},
            outputs={"request": request_path, "outline": outline_path},
        )
    return plan


def outline_prompt_block(outline: dict[str, Any] | None) -> str:
    if not outline:
        return ""
    return (
        "\nLocal Proof Outline (dependency-level guidance, not proof commands):\n"
        + json.dumps(outline, ensure_ascii=False, indent=2)
        + "\nRespect its composition order when applicable. Isabelle remains the "
          "only authority on each next command.\n"
    )
