#!/usr/bin/env python3
"""Audited session planner for support-only roots in a regen campaign."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from llm_transcript import call_llm, record_replay
from audited_llm import sha256_text


REPO = Path(__file__).resolve().parent.parent
DEFAULT_CAMPAIGN = REPO / "reports" / "regen" / "refine-arm-session"
ROOTS = ("Refine.LevityCatch", "Refine.Move_R")


def read_json(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def fenced_json(text: str):
    match = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.S)
    raw = (match.group(1) if match else text).strip()
    return json.loads(raw)


def compact_input(campaign_dir: Path) -> dict:
    campaign = read_json(campaign_dir / "campaign.json")
    graph = read_json(campaign_dir / "theory_graph.json")
    contracts = read_json(campaign_dir / "contracts.json")["contracts"]
    contract_by_id = {item["id"]: item for item in contracts}
    theories = []
    for theory in graph["theories"]:
        theories.append({
            "name": theory["name"],
            "topological_layer": theory["topological_layer"],
            "target_imports": theory["target_imports"],
            "contract_ids": theory["contracts"],
        })
    roots = []
    by_name = {item["name"]: item for item in graph["theories"]}
    for root in ROOTS:
        item = by_name[root]
        roots.append({
            "theory": root,
            "direct_imports": item["direct_imports"],
            "descendant_theories": item["descendants"],
            "reachable_contract_ids": sorted(
                contract["id"] for contract in contracts
                if contract["theory"] in item["descendants"]
            ),
        })
    return {
        "schema_version": 1,
        "architecture": campaign["architecture"],
        "target_session": campaign["target_session"],
        "planning_roots": roots,
        "theory_dag": theories,
        "fixed_contracts": [
            {
                "id": item["id"],
                "theory": item["theory"],
                "name": item["name"],
                "statement": item["statement"],
            }
            for item in contracts
        ],
        "contract_count": len(contract_by_id),
        "forbidden_information": [
            "all declarations and proof bodies from the human Refine session",
            "human helper names, statements, ordering, and attributes",
        ],
    }


def planner_prompt(payload: dict) -> str:
    return """You are the session-level proof-library planner for an Isabelle/seL4
Refine regeneration experiment. You receive only the target theory import DAG
and fixed corres contract statements. The two roots LevityCatch and Move_R have
no local contracts, so any export from them must be justified by one or more
reachable downstream fixed contracts.

This is architecture planning, not proof synthesis. Propose only concepts whose
early placement is likely to reduce duplication across downstream contracts.
It is valid to propose no exports for a root when the fixed contracts do not
justify one. Do not reconstruct or quote the familiar human Refine files from
memory. Do not use human helper names. Suggested generated names must start
with `regen_`.

For every requirement provide:
- a stable id;
- a concise semantic concept, without asserting an exact Isabelle theorem;
- one or more exact downstream contract IDs from the payload;
- why the root is the correct owner rather than a later theory;
- symbols that a later theory planner should retrieve definitions/premises for;
- risk: low, medium, or high.

Return only JSON with this schema:
{
  "schema_version": 1,
  "root_export_plans": [
    {
      "theory": "Refine.LevityCatch | Refine.Move_R",
      "decision": "exports | empty",
      "requirements": [
        {
          "id": "root:req-name",
          "suggested_name": "regen_name",
          "concept": "...",
          "downstream_contract_ids": ["contract:..."],
          "owner_rationale": "...",
          "retrieval_symbols": ["..."],
          "risk": "low | medium | high"
        }
      ]
    }
  ],
  "global_risks": ["..."]
}

Hard constraints:
- Exactly one plan for each of Refine.LevityCatch and Refine.Move_R.
- Every consumer ID must be copied exactly from the payload and be reachable
  from that root.
- No exact lemma statement yet; that belongs to the per-theory planner.
- No premise or declaration from human Refine is available.
- Do not use tools or inspect the filesystem. The payload is the complete input.

PAYLOAD:
""" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def critique_prompt(payload: dict, draft: dict) -> str:
    return """Audit the following session plan for a strict proof-regeneration
experiment. Remove speculative exports that are not supported by the fixed
contract statements, repair invalid consumer IDs, and merge duplicate concepts.
Prefer an empty root over invented scaffolding. Preserve the exact output schema
and return the complete revised JSON only. Suggested names must start `regen_`.
Do not use tools or inspect the filesystem.

PAYLOAD:
""" + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + \
        "\n\nDRAFT:\n" + json.dumps(draft, ensure_ascii=False, separators=(",", ":"))


def validate(plan: dict, payload: dict) -> None:
    if plan.get("schema_version") != 1:
        raise ValueError("unsupported planner schema")
    roots = {item["theory"]: item for item in payload["planning_roots"]}
    plans = plan.get("root_export_plans", [])
    if {item.get("theory") for item in plans} != set(ROOTS) or len(plans) != 2:
        raise ValueError("planner must return exactly the two requested roots")
    contract_ids = {item["id"] for item in payload["fixed_contracts"]}
    for root_plan in plans:
        theory = root_plan["theory"]
        reachable = set(roots[theory]["reachable_contract_ids"])
        requirements = root_plan.get("requirements", [])
        if root_plan.get("decision") == "empty" and requirements:
            raise ValueError(f"empty root {theory} contains requirements")
        if root_plan.get("decision") == "exports" and not requirements:
            raise ValueError(f"exports root {theory} has no requirements")
        for requirement in requirements:
            if not str(requirement.get("suggested_name", "")).startswith("regen_"):
                raise ValueError("generated helper name must start with regen_")
            consumers = set(requirement.get("downstream_contract_ids", []))
            if not consumers or not consumers <= contract_ids or not consumers <= reachable:
                raise ValueError(f"illegal consumers for {requirement.get('id')}")
            if requirement.get("risk") not in {"low", "medium", "high"}:
                raise ValueError(f"invalid risk for {requirement.get('id')}")


def run(args) -> None:
    campaign_dir = args.campaign_dir.resolve()
    planner_dir = campaign_dir / "planner"
    payload = compact_input(campaign_dir)
    write_json(planner_dir / "session-input.json", payload)
    prompt = planner_prompt(payload)
    if args.provider in {"codex", "claude"}:
        if not args.allow_external_payload:
            raise SystemExit(
                "external provider call requires --allow-external-payload; "
                "the prompt contains repository-derived contract statements"
            )
        draft_text = call_llm(
            args.provider, prompt, planner_dir / "01-session-plan",
            model=args.model, timeout=args.timeout, theory="Refine-session",
            stage="session-plan",
        )
    else:
        if not args.draft_response:
            raise SystemExit("--draft-response is required for current-session provider")
        draft_text = record_replay(
            prompt, args.draft_response.read_text(encoding="utf-8"),
            planner_dir / "01-session-plan", model=args.model,
        )
    draft = fenced_json(draft_text)
    validate(draft, payload)
    write_json(planner_dir / "01-session-plan.parsed.json", draft)

    critique = critique_prompt(payload, draft)
    if args.provider in {"codex", "claude"}:
        revised_text = call_llm(
            args.provider,
            critique, planner_dir / "02-session-critique",
            model=args.model, timeout=args.timeout, theory="Refine-session",
            stage="session-critique",
        )
    else:
        if not args.critique_response:
            raise SystemExit("--critique-response is required for current-session provider")
        revised_text = record_replay(
            critique, args.critique_response.read_text(encoding="utf-8"),
            planner_dir / "02-session-critique", model=args.model,
        )
    revised = fenced_json(revised_text)
    validate(revised, payload)
    revised["input_sha256"] = sha256_text(json.dumps(payload, sort_keys=True))
    revised["draft_sha256"] = sha256_text(json.dumps(draft, sort_keys=True))
    write_json(planner_dir / "session-plan.json", revised)
    print(json.dumps({
        "plan": str(planner_dir / "session-plan.json"),
        "roots": {
            item["theory"]: {
                "decision": item["decision"],
                "requirements": len(item.get("requirements", [])),
            }
            for item in revised["root_export_plans"]
        },
    }, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN)
    ap.add_argument("--provider", choices=("codex", "claude", "current-session"), default="codex")
    ap.add_argument("--model", default="gpt-5.5")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--allow-external-payload", action="store_true")
    ap.add_argument("--draft-response", type=Path)
    ap.add_argument("--critique-response", type=Path)
    ap.add_argument("--non-production-replay", action="store_true")
    args = ap.parse_args()
    if args.provider == "current-session" and not args.non_production_replay:
        raise SystemExit("current-session requires --non-production-replay and cannot seal")
    run(args)


if __name__ == "__main__":
    main()
