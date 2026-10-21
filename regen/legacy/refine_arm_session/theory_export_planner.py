#!/usr/bin/env python3
"""Audited export planning for a ready support-only Refine theory."""
from __future__ import annotations

# Allow both python -m and direct script invocation.
if __package__ in {None, ""}:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import argparse
import json
import re
from pathlib import Path

from regen.infrastructure.llm_transcript import call_llm, record_replay
from regen.infrastructure.audited_llm import sha256_text
from regen.legacy.refine_arm_session.theory_trace import TheoryAuditTrail


REPO = Path(__file__).resolve().parents[3]
DEFAULT_CAMPAIGN = REPO / "reports" / "regen" / "refine-arm-session"
DEFAULT_THEORY = "Refine.InvariantUpdates_H"
CONTEXT_LIMIT = 120_000


def read_json(path: Path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def parse_json(text: str):
    match = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.S)
    return json.loads((match.group(1) if match else text).strip())


def planner_prompt(payload: dict) -> str:
    template = """You are planning the exported proof interface of one support-only
Isabelle theory in a strict seL4 Refine regeneration experiment.

The target has no fixed local contract, but generated consumer theories import
it for a reason. Infer possible ownership from the supplied import paths, exact
fixed contract statements and attributes, shared contract symbols, and legal
premises. A contract need not mention the target theory by name.

Hard boundaries:
- The payload is the complete input. Do not use tools, files, memory of the
  public human Refine corpus, or familiar human helper names.
- Human target-session declarations and proofs are unavailable.
- Fixed framework declarations define semantics; they are not proof nodes.
- Retrieved premises are legal candidates, not automatically relevant.
- Cluster every reachable contract exactly once by shared proof structure.
- For each cluster, decide whether legal existing premises already cover it, a
  target-owned export is justified, or the need remains unresolved.
- Do not choose empty merely because contracts do not cite the target by name.
  Empty requires a cluster-complete argument that no target-owned reusable fact
  is justified.
- Every export must cite exact reachable contract IDs, use only supplied legal
  symbols, and include an exact Isabelle statement without a proof.
- Suggested generated names must start with regen_.

Return only JSON:
{
  "schema_version": 2,
  "theory": "__TARGET_THEORY__",
  "decision": "empty | exports",
  "contract_clusters": [{
    "id": "cluster-id",
    "contract_ids": ["contract:..."],
    "shared_symbols": ["..."],
    "coverage": "existing-premises | target-export-candidate | unresolved",
    "rationale": "evidence from statements, paths, and premises"
  }],
  "requirements": [{
    "id": "theory:req-id",
    "suggested_name": "regen_name",
    "concept": "semantic proof concept",
    "statement": "exact Isabelle lemma statement without proof",
    "downstream_contract_ids": ["contract:..."],
    "retrieval_symbols": ["..."],
    "owner_rationale": "why the import path and contract cluster justify this owner",
    "risk": "low | medium | high"
  }],
  "empty_justification": "required for empty; address every cluster",
  "uncertainties": ["..."]
}

PAYLOAD:
"""
    return template.replace("__TARGET_THEORY__", payload["target_theory"]) + \
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def critique_prompt(payload: dict, draft: dict) -> str:
    return """Perform an adversarial false-empty review of this support-theory
plan under the strict regeneration trust boundary. First try to falsify an empty
decision: use consumer import paths, contract attributes, repeated symbols, and
matching legal premises to identify shared proof structure that the draft may
have missed. A consumer contract need not name its imported support theory.
Then remove speculative exports that lack an exact legal statement or ownership
evidence. Ensure the contract clusters cover every supplied contract exactly
once. Keep empty only when the revised plan gives a cluster-complete reason. Do
not use tools, external information, or remembered human Refine facts. Return
the complete schema-version-2 JSON only.

PAYLOAD:
""" + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + \
        "\n\nDRAFT:\n" + json.dumps(draft, ensure_ascii=False, separators=(",", ":"))


def validate(plan: dict, payload: dict) -> None:
    if plan.get("schema_version") != 2 or plan.get("theory") != payload["target_theory"]:
        raise ValueError("incompatible schema or theory")
    if plan.get("decision") not in {"empty", "exports"}:
        raise ValueError("invalid decision")
    requirements = plan.get("requirements", [])
    if (plan["decision"] == "empty") != (not requirements):
        raise ValueError("decision and requirements disagree")
    contract_ids = {item["id"] for item in payload["reachable_fixed_contract_summaries"]}
    covered_contracts: set[str] = set()
    candidate_contracts: set[str] = set()
    for cluster in plan.get("contract_clusters", []):
        members = set(cluster.get("contract_ids", []))
        if not members or not members <= contract_ids:
            raise ValueError("contract cluster contains illegal or empty membership")
        if cluster.get("coverage") not in {
            "existing-premises", "target-export-candidate", "unresolved",
        }:
            raise ValueError("invalid contract cluster coverage")
        if cluster.get("coverage") == "target-export-candidate":
            candidate_contracts |= members
        if covered_contracts & members:
            raise ValueError("contract clusters must not overlap")
        covered_contracts |= members
    if covered_contracts != contract_ids:
        raise ValueError("contract clusters must cover every reachable fixed contract")
    if plan["decision"] == "empty" and not str(plan.get("empty_justification", "")).strip():
        raise ValueError("empty plan requires a cluster-complete justification")
    if plan["decision"] == "empty" and any(
        cluster.get("coverage") == "target-export-candidate"
        for cluster in plan.get("contract_clusters", [])
    ):
        raise ValueError("empty plan cannot discard a target-export candidate")
    requirement_contracts: set[str] = set()
    for requirement in requirements:
        if not str(requirement.get("suggested_name", "")).startswith("regen_"):
            raise ValueError("generated support name must start with regen_")
        consumers = set(requirement.get("downstream_contract_ids", []))
        if not consumers or not consumers <= contract_ids:
            raise ValueError(f"illegal consumers for {requirement.get('id')}")
        requirement_contracts |= consumers
        statement = str(requirement.get("statement", ""))
        if not statement.strip() or re.search(r"\b(sorry|oops|axiomatization|admit)\b", statement):
            raise ValueError("support requirement needs a safe exact Isabelle statement")
        if requirement.get("risk") not in {"low", "medium", "high"}:
            raise ValueError("invalid risk")
    if plan["decision"] == "exports" and not candidate_contracts:
        raise ValueError("exports plan requires a target-export candidate cluster")
    if requirement_contracts != candidate_contracts:
        raise ValueError("requirements must cover exactly the target-export candidates")


def recorded_call(args, prompt: str, prefix: Path, response: Path | None) -> str:
    stage = prefix.name
    artifact_prefix = args.audit_trail.run_dir / "llm" / f"{len(args.audit_trail.events()) + 1:04d}-{stage}"
    if args.provider in {"codex", "claude"}:
        if not args.allow_external_payload:
            raise SystemExit("external provider requires --allow-external-payload")
        return call_llm(
            args.provider, prompt, artifact_prefix, model=args.model, timeout=args.timeout,
            theory=args.theory, stage=stage, transcript_sink=args.audit_trail,
        )
    if response is None:
        raise SystemExit("current-session provider requires recorded response files")
    return record_replay(
        prompt,
        response.read_text(encoding="utf-8"),
        artifact_prefix,
        model=args.model, theory=args.theory, stage=prefix.name,
        transcript_sink=args.audit_trail,
    )


def run(args) -> dict:
    campaign_dir = args.campaign_dir.resolve()
    basename = args.theory.split(".", 1)[1]
    args.audit_trail = TheoryAuditTrail(
        campaign_dir, args.theory, generation_id=args.generation_id,
    )
    args.audit_trail.record_harness("planning-start", details={
        "provider": args.provider, "model": args.model,
    })
    preflight = read_json(campaign_dir / "preflight" / basename / "report.json")
    if preflight["verdict"] != "pass":
        raise SystemExit("theory preflight is not green")
    payload = read_json(campaign_dir / "preflight" / basename / "planner-input.json")
    args.audit_trail.record_harness("preflight", artifacts=[
        campaign_dir / "preflight" / basename / "report.json",
        campaign_dir / "preflight" / basename / "planner-input.json",
    ], details={"verdict": preflight["verdict"]})
    planner_dir = campaign_dir / "planner" / basename

    first_prompt = planner_prompt(payload)
    if len(first_prompt) > CONTEXT_LIMIT:
        raise SystemExit(f"planner prompt exceeds context limit: {len(first_prompt)}")
    draft = parse_json(recorded_call(
        args, first_prompt, planner_dir / "01-export-plan", args.response,
    ))
    validate(draft, payload)
    write_json(planner_dir / "01-export-plan.parsed.json", draft)

    second_prompt = critique_prompt(payload, draft)
    if len(second_prompt) > CONTEXT_LIMIT:
        raise SystemExit(f"critique prompt exceeds context limit: {len(second_prompt)}")
    revised = parse_json(recorded_call(
        args, second_prompt, planner_dir / "02-export-critique", args.critique_response,
    ))
    validate(revised, payload)
    if revised["decision"] == "empty":
        revised["empty_interface_status"] = (
            "provisional_until_direct_consumer_green"
            if payload["reachable_fixed_contract_summaries"]
            else "terminal_no_reachable_contracts"
        )
    else:
        revised["empty_interface_status"] = "not_applicable"
    revised["planner_input_sha256"] = preflight["planner_input_sha256"]
    revised["draft_sha256"] = sha256_text(json.dumps(draft, sort_keys=True))
    revised["context_chars"] = {
        "draft": len(first_prompt),
        "critique": len(second_prompt),
        "limit": CONTEXT_LIMIT,
    }
    write_json(planner_dir / "export-plan.json", revised)
    args.audit_trail.record_harness("planning-complete", artifacts=[
        planner_dir / "01-export-plan.parsed.json",
        planner_dir / "export-plan.json",
    ])
    return revised


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN)
    parser.add_argument("--theory", default=DEFAULT_THEORY)
    parser.add_argument("--provider", choices=("codex", "claude", "current-session"), default="codex")
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--allow-external-payload", action="store_true")
    parser.add_argument("--response", type=Path)
    parser.add_argument("--critique-response", type=Path)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--non-production-replay", action="store_true")
    args = parser.parse_args()
    if args.provider == "current-session" and not args.non_production_replay:
        raise SystemExit("current-session requires --non-production-replay and cannot seal")
    print(json.dumps(run(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
