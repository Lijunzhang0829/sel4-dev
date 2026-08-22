#!/usr/bin/env python3
"""Lemma Prover: retrieve premises and generate/repair one proof body."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from regen.llm_transcript import allocate_artifact_prefix, call_llm
from regen.module_run_log import attach_module_outputs, write_llm_module_log
from regen.premise_retrieval import (
    retrieve_proof_premises, semantic_bridge_guidance,
)
from regen.proof_outline_salvage import plan_local_outline
from regen.proof_graph import parse_json_response


def timeout_repair_guidance(previous: dict[str, Any] | None) -> str:
    if not previous:
        return ""
    diagnostic = str(previous.get("check", {}).get("diagnostic", ""))
    if "timeout" not in diagnostic.lower():
        return ""
    proof = str(previous.get("proof_body", ""))
    broad_methods = sorted(set(re.findall(
        r"\b(?:simp|clarsimp|auto|fastforce)\b[^\n]*", proof,
    )))
    repeated = "\n".join(f"- {method}" for method in broad_methods[-4:])
    return """The previous Isabelle check timed out. Treat this as proof-search
explosion, not as evidence that the theorem is false. Do not repeat broad
`simp`, `clarsimp`, `auto`, or `fastforce` over the same large definition.
Replace the timed-out closing block with bounded construction: use named
intermediate facts, `simp only` with an explicit small rule set, or introduce
the conjunction fields one at a time. A merely cosmetic tactic substitution is
not a repair. Timed-out method lines from the previous candidate:
""" + (repeated or "- unavailable")


def retrieval_repair_guidance(retrieval_hint: str) -> str:
    if "ps_clear_def" not in retrieval_hint.lower():
        return ""
    return """The focused retrieval request identified ps_clear_def2. Preserve
the ps_clear fact obtained from obj_at' instead of unfolding it into a set
difference. Instantiate ps_clear_def2 directly. First derive `ptr <= ptr + 1`
from the local alignment fact with `Aligned.is_aligned_no_wrap'` at offset 1
(the remaining `1 < 2 ^ cteSizeBits` side condition is numeric). Then use
`iffD1 (Invariants_H.ps_clear_def2[OF that_bound])` to obtain exactly
`{ptr + 1..ptr + 2 ^ cteSizeBits - 1} inter dom (ksPSpace s') = {}`.
Do not prove the interval-set equality element by element."""



def constructor_case_residual_guidance(residual: str) -> str:
    """Translate a stuck constructor case into a local tactic-order repair."""
    if (
        'At command "apply"' not in residual
        or "case " not in residual
        or "ASIDPool" not in residual
    ):
        return ""
    return """The latest residual is a constructor case left by the current
failing apply, not a missing theorem. Add the abstract architecture datatype
split to that same failing simp method; do not put the split in a later
command, because Isabelle stops at an apply that makes no progress. For an
arch_kernel_obj.ASIDPool residual use
split: Arch_Structs_A.ARM_A.arch_kernel_obj.splits (or the unqualified
ARM_A.arch_kernel_obj.splits if that is the visible name). Avoid inserting
the concrete arch_kernel_object.case(3) rewrite before this split."""


def projectko_type_erasure_guidance(node: dict[str, Any], residual: str) -> str:
    """Keep the projectKO result type visible across fst/snd projections."""
    if (
        "updateObject_default_checks_no_fail" not in str(node.get("id", ""))
        or "snd (projectKO ko s)" not in residual
    ):
        return ""
    return r"""The current residual is a projectKO type-erasure failure, not a
missing graph node. In every local no-fail fact, annotate the complete value as
`(projectKO ko s :: (('a \<times> kernel_state) set \<times> bool))`; an
untyped `snd (projectKO ko s)` may instantiate projectKO at an unrelated result
type. Do not apply `regen_setObject_other_projectKO_typed_no_fail` with a bare
`rule`, because that has repeatedly lost the intended type instantiation.
Instead prove the explicitly typed local fact directly from `pko` with
`simp add: PSpaceStorable_H.pre_storable_class.projectKO_def
Nondet_Monad.return_def`. When simplifying updateObject_default, retain this
typed fact. For an `unless (ptr && mask ... = 0)` residual, do not unfold
`alignCheck_def` or `is_aligned_def`; rewrite with
`LevityCatch.alignCheck_assert` so the existing `is_aligned` assumption removes
the check. Likewise prefer `LevityCatch.magnitudeCheck_assert` for the optional
successor bound. Use only narrow bind/no-fail composition rules, and do not
drop the type annotation in a later intermediate fact."""


def projectko_type_erasure_precheck(node: dict[str, Any], proof: str) -> str:
    if "updateObject_default_checks_no_fail" not in str(node.get("id", "")):
        return ""
    if re.search(
        r"using\s+pko\s+by\s+\(rule\s+"
        r"regen_setObject_other_projectKO_typed_no_fail\)", proof,
    ):
        return (
            "Candidate rejected before Isabelle: a bare rule application of "
            "regen_setObject_other_projectKO_typed_no_fail repeats the known "
            "snd/projectKO type-erasure failure. Prove an explicitly typed "
            "pko_nf directly from pko with projectKO_def and return_def."
        )
    if (
        "alignCheck_def" in proof
        and "alignCheck_assert" not in proof
    ):
        return (
            "Candidate rejected before Isabelle: unfolding alignCheck_def "
            "repeats the known unless/mask residual. Rewrite with "
            "LevityCatch.alignCheck_assert and preserve the is_aligned fact."
        )
    typed_pko_nf = re.search(
        r"have\s+pko_nf\s*:\s*\"[^\"]*snd\s*\(projectKO\s+ko\s+s\s*::",
        proof,
    )
    if "have pko_nf" in proof and not typed_pko_nf:
        return (
            "Candidate rejected before Isabelle: pko_nf dropped the explicit "
            "projectKO result type. Annotate the value before applying snd."
        )
    if (
        typed_pko_nf
        and "pre_storable_class.projectKO_def" in proof
        and "return_def" not in proof
    ):
        return (
            "Candidate rejected before Isabelle: direct projectKO no-fail "
            "simplification also requires Nondet_Monad.return_def."
        )
    return ""


def explosive_state_relation_closer(node: dict[str, Any], proof: str) -> str:
    if "regen_setObject_other_pspace_relation_update" in proof:
        wrapped_goal = re.search(
            r"pspace_relation\s+\(kheap\s+\?[A-Za-z0-9_']+\)"
            r"\s+\(ksPSpace\s+\?[A-Za-z0-9_']+\)",
            proof,
        )
        if wrapped_goal:
            return (
                "Candidate rejected before Isabelle: normalize the pspace "
                "component goal before invoking the higher-order update rule. "
                "State the goal directly over `(kheap s)(ptr \\<mapsto> ob)` and "
                "`(ksPSpace s')(ptr \\<mapsto> injectKO ob')`; do not wrap those "
                "maps as `kheap ?s` and `ksPSpace ?s'` behind local record lets."
            )
        unsafe_of = re.search(
            r"regen_setObject_other_pspace_relation_update\s*\[[^\]]*\bOF\b",
            proof,
        )
        unsafe_apply = (
            "apply (rule regen_setObject_other_pspace_relation_update" in proof
        )
        if unsafe_of or unsafe_apply:
            return (
                "Candidate rejected before Isabelle: this higher-order helper "
                "must not be invoked with OF or an apply-chain. Both forms have "
                "already produced multiple-unifier or shifted-subgoal failures. "
                "Use `proof (rule regen_setObject_other_pspace_relation_update"
                "[where P=P and ptr=ptr and ob=ob and ob'=ob' and s=s and s'=s'])` "
                "and discharge every certified premise with a separate `show` "
                "block, including the higher-order z/b/e premises."
            )
    if "pspace_relation" in str(node.get("statement", "")):
        broad = re.search(r"\b(?:fastforce|blast)\b", proof, flags=re.I)
        if broad:
            return (
                "Candidate rejected before Isabelle: broad " + broad.group(0) +
                " in pspace_relation assembly repeats a known timeout pattern. "
                "Instantiate the two bounded universal premises explicitly with "
                "drule or bspec for x and the relation cut, then simplify only "
                "the resulting case expression."
            )
    if "state_relation" not in str(node.get("statement", "")):
        return ""
    if (
        str(node.get("id", "")).endswith("regen_setObject_other_state_relation_update")
        and "ready_queues_relation_def" in proof
    ):
        return (
            "Candidate rejected before Isabelle: keep the exact "
            "`ready_queues_relation_2` conclusion returned by "
            "regen_setObject_other_ready_queues_update and submit that rq2 fact "
            "directly during state_relation_def assembly; do not expand it into "
            "ready_queues_relation with `simp add: ready_queues_relation_def`."
        )
    if re.search(
        r"unfolding\s+(?:StateRelation\.)?state_relation_def[\s\S]*?(?:by|apply)\s+simp\b",
        proof,
        flags=re.I,
    ):
        return (
            "Candidate rejected before Isabelle: a default `simp` closer after "
            "unfolding state_relation_def is a known multi-minute search. "
            "Assemble the relation with `rule CollectI`, "
            "`simp only: split_conv`, repeated `rule conjI`, and one explicit "
            "`rule` for each of all 17 already-proved relation components; "
            "do not leave a trailing default `simp` or `apply simp`."
        )
    show_match = re.search(r"show\s+\?thesis([\s\S]*)", proof, flags=re.I)
    if not show_match:
        return ""
    closer_block = show_match.group(1)
    if "state_relation_def" not in closer_block:
        return ""
    broad = re.search(
        r"\b(?:simp|clarsimp|auto|fastforce)\b(?!\s+only\b)",
        closer_block, flags=re.I,
    )
    if not broad:
        return ""
    closer = " ".join(("show ?thesis" + closer_block).split())[:800]
    return (
        "Candidate rejected before Isabelle: known proof-search explosion "
        "pattern in final state_relation assembly. Replace this closing block "
        "with bounded simp only or explicit conjunction construction: " + closer
    )


def trim_redundant_proof_terminator(
    proof: str, diagnostic: str, source: str | None = None,
) -> tuple[str, str] | None:
    """Remove one complete trailing command after Isabelle closed the goal."""
    no_pending = "Failed to refine any pending goal" in diagnostic
    illegal_prove = (
        'Illegal application of proof command in "prove" mode' in diagnostic
    )
    if not (no_pending or illegal_prove):
        return None

    # Prefer Isabelle's exact source location. A nested Isar proof may close
    # an outer goal early, so deleting the final qed can remove the wrong one.
    if no_pending and source:
        match = re.search(
            r'At command "qed" \(line (\d+) of "[^"]+"\)', diagnostic,
        )
        source_path = Path(source)
        if match and source_path.is_file():
            source_lines = source_path.read_text(encoding="utf-8").splitlines()
            source_index = int(match.group(1)) - 1
            if (0 <= source_index < len(source_lines)
                    and source_lines[source_index].strip() == "qed"):
                rejected = source_lines[source_index]
                proof_lines = proof.rstrip().splitlines()
                # The checker embeds the proof body verbatim after the node
                # statement. Locate that contiguous body once, then translate
                # Isabelle's absolute source line to an unambiguous proof line.
                prefix_size = min(4, len(proof_lines))
                starts = [
                    start for start in range(len(source_lines) - prefix_size + 1)
                    if source_lines[start:start + prefix_size]
                    == proof_lines[:prefix_size]
                ]
                if len(starts) == 1:
                    index = source_index - starts[0]
                    if (0 <= index < len(proof_lines)
                            and proof_lines[index].strip() == "qed"):
                        repaired = "\n".join(
                            proof_lines[:index] + proof_lines[index + 1:]
                        ).rstrip()
                        if repaired:
                            return repaired, rejected

    lines = proof.rstrip().splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return None

    def trailing_from_show_start(end: int) -> int | None:
        """Find a bounded trailing from/using-show-by command."""
        if end <= 0:
            return None
        last_index = end - 1
        last = lines[last_index].strip()
        if re.fullmatch(r"(?:from|using|with)\b.*\bshow\b.*\bby\b.*", last):
            return last_index
        if not re.match(r"(?:by|done)\b", last):
            return None
        for start in range(last_index - 1, max(-1, last_index - 8), -1):
            text = " ".join(line.strip() for line in lines[start:end])
            if re.match(r"(?:from|using|with)\b", lines[start].strip()):
                return start if re.search(r"\bshow\b", text) else None
        return None

    last = lines[-1].strip()
    if no_pending and last == "qed":
        repaired = "\n".join(lines[:-1]).rstrip()
        if repaired:
            return repaired, "qed"
    if illegal_prove and last == "qed":
        command_start = trailing_from_show_start(len(lines) - 1)
        if command_start is not None:
            removed = "\n".join(lines[command_start:-1])
            repaired_lines = lines[:command_start] + [lines[-1]]
            return "\n".join(repaired_lines).rstrip(), removed
        repaired = "\n".join(lines[:-1]).rstrip()
        if repaired:
            return repaired, "qed"

    if no_pending:
        command_start = trailing_from_show_start(len(lines))
        if command_start is not None:
            removed = "\n".join(lines[command_start:])
            repaired = "\n".join(lines[:command_start]).rstrip()
            if repaired:
                return repaired, removed
        if re.fullmatch(r"by(?:\s+.*)?", last):
            repaired = "\n".join(lines[:-1]).rstrip()
            if repaired:
                return repaired, last
    return None


def replace_redundant_by_with_fact(
    proof: str, diagnostic: str, source: str | None,
) -> tuple[str, str] | None:
    """Remove the exact by-method Isabelle reports after goal closure."""
    if "Failed to refine any pending goal" not in diagnostic or not source:
        return None
    match = re.search(
        r'At command "by" \(line (\d+) of "[^"]+"\)', diagnostic,
    )
    if not match:
        return None
    source_path = Path(source)
    if not source_path.is_file():
        return None
    source_lines = source_path.read_text(encoding="utf-8").splitlines()
    source_index = int(match.group(1)) - 1
    if not (0 <= source_index < len(source_lines)):
        return None
    rejected = source_lines[source_index]
    if not re.match(r"\s*by\b", rejected):
        return None
    proof_lines = proof.rstrip().splitlines()
    candidates = [
        index for index, line in enumerate(proof_lines)
        if line.strip() == rejected.strip()
    ]
    if len(candidates) != 1:
        return None
    index = candidates[0]
    indentation = proof_lines[index][:-len(proof_lines[index].lstrip())]
    proof_lines[index] = indentation + "."
    return "\n".join(proof_lines).rstrip(), rejected


def trim_redundant_local_apply(
    proof: str, diagnostic: str, source: str | None,
) -> tuple[str, str] | None:
    """Remove the exact local apply command that Isabelle reports as surplus."""
    if "No subgoals!" not in diagnostic or not source:
        return None
    match = re.search(
        r'At command "apply" \(line (\d+) of "[^"]+"\)', diagnostic,
    )
    if not match:
        return None
    source_path = Path(source)
    if not source_path.is_file():
        return None
    source_lines = source_path.read_text(encoding="utf-8").splitlines()
    source_index = int(match.group(1)) - 1
    if not (0 <= source_index < len(source_lines)):
        return None
    rejected = source_lines[source_index].strip()
    if not re.match(r"apply\b", rejected):
        return None

    proof_lines = proof.rstrip().splitlines()
    candidates = [
        index for index, line in enumerate(proof_lines)
        if line.strip() == rejected
    ]
    if len(candidates) != 1:
        return None
    index = candidates[0]
    remove_end = index + 1
    if remove_end < len(proof_lines) and proof_lines[remove_end].strip() == "done":
        remove_end += 1
    removed = "\n".join(proof_lines[index:remove_end])
    repaired = "\n".join(proof_lines[:index] + proof_lines[remove_end:]).rstrip()
    if not repaired:
        return None
    return repaired, removed


def normalize_reserved_local_fact_names(proof: str) -> tuple[str, list[dict[str, str]]]:
    """Rename local fact labels that collide with Isabelle command keywords."""
    repairs: list[dict[str, str]] = []
    normalized = proof
    for reserved in ("typ",):
        if re.search(rf"\b(?:have|show)\s+{reserved}\s*:", normalized):
            replacement = f"{reserved}_fact"
            normalized = re.sub(rf"\b{reserved}\b", replacement, normalized)
            repairs.append({"kind": "rename-reserved-local-fact",
                            "before": reserved, "after": replacement})
    return normalized, repairs


def proof_prompt(
    node: dict[str, Any], dependencies: list[dict[str, Any]],
    premises: list[dict[str, Any]], previous: dict[str, Any] | None,
    guidance: str = "", forbidden_facts: set[str] | None = None,
    previous_round_feedback: dict[str, Any] | None = None,
    local_proof_outline: dict[str, Any] | None = None,
    latest_attempt: dict[str, Any] | None = None,
    attempt_history: list[dict[str, Any]] | None = None,
) -> str:
    payload = {
        "node": {
            "id": node["id"], "role": node["role"],
            "statement": node["statement"], "strategy": node["strategy"],
        },
        "green_generated_dependencies": dependencies,
        "retrieved_legal_premises": premises,
        "best_previous_attempt": previous,
        "latest_attempt": latest_attempt,
        "attempt_history": attempt_history or [],
        "previous_round_feedback": previous_round_feedback,
        "local_proof_outline": local_proof_outline,
        "proof_guidance": guidance,
        "forbidden_facts": sorted(forbidden_facts or set()),
    }
    return """You are the Lemma Prover. Prove exactly one fixed Isabelle theorem.

This is a closed-payload proof call. Do not invoke tools, skills, shell
commands, file reads, repository search, or network search. Use only INPUT and
return the required JSON directly.

You may change only its proof body. Do not change the statement, introduce a
new lemma, or request a graph change. Use only the listed generated dependencies
and legal premises. Isabelle's previous diagnostic is authoritative.
Never cite a fact listed in forbidden_facts; Isabelle already reported that it
does not exist in this context. This prohibition applies to citing the name as
a theorem, not to an object-language constant with the same spelling in the
fixed statement.
Treat Isabelle identifiers as case-sensitive. In particular, never replace a
lower-case abstract operation with a similarly named capitalized concrete
operation. Generated dependencies are certified facts selected by the theorem
graph: use the relevant dependency instead of reproving or bypassing its result.
Use best_previous_attempt as the proof-body baseline and latest_attempt as the
current repair target. Preserve every prefix that Isabelle had already passed;
do not reintroduce a residual that an earlier attempt eliminated.
Do not use Isar command keywords such as `next`, `then`, or `show` as
case-variable names. Use neutral names such as `nxt`.

Return only JSON:
{
  "proof_body": "by ... | proof ... qed",
  "used_premise_ids": ["..."]
}

INPUT:
""" + json.dumps(payload, ensure_ascii=False, indent=2)


def proof_progress_line(attempt: dict[str, Any]) -> int:
    """Approximate how far Isabelle parsed a failed proof candidate."""
    diagnostic = str(attempt.get("check", {}).get("diagnostic", ""))
    lines = [int(value) for value in re.findall(r"line (\d+)", diagnostic)]
    return max(lines, default=0)


def attempt_summary(attempt: dict[str, Any]) -> dict[str, Any]:
    check = attempt.get("check", {})
    return {
        "attempt": attempt.get("attempt"),
        "isabelle_progress_line": proof_progress_line(attempt),
        "verdict": check.get("verdict"),
        "returncode": check.get("returncode"),
        "wall_ms": check.get("wall_ms"),
        "diagnostic": str(check.get("diagnostic", ""))[-4000:],
    }


def prove_node(
    graph: dict[str, Any], node_id: str, proofs: dict[str, str], *,
    checker: Any, premise_database: Path, output_dir: Path,
    generated_records: Path | None = None, provider: str = "codex",
    model: str = "gpt-5.5", timeout: int = 1800, max_attempts: int = 5,
    backend: str = "whole-proof", round_id: int = 1,
    retrieval_hint: str = "",
    previous_round_feedback: dict[str, Any] | None = None,
    stepwise_residual: str = "",
    salvage_provider: str | None = None,
    think_timeout: int | None = None,
) -> dict[str, Any]:
    feedback_text = ""
    feedback_diagnostics = ""
    if previous_round_feedback:
        feedback_text = (
            "Previous complete proof round feedback:\n"
            + json.dumps(previous_round_feedback, ensure_ascii=False)
        )
        feedback_diagnostics = "\n".join([
            str(previous_round_feedback.get("residual", "")),
            *(str(item.get("diagnostic", "")) for item in
              previous_round_feedback.get("proof_candidates", [])),
        ])
    if backend == "stepwise":
        from regen.stepwise_lemma_backend import (
            prove_node_stepwise, stepwise_search_budget,
        )
        search_budget = stepwise_search_budget(max_attempts)
        stepwise = prove_node_stepwise(
            graph, node_id, proofs, checker=checker,
            premise_database=premise_database, output_dir=output_dir,
            generated_records=generated_records, model=model, timeout=timeout,
            max_attempts=search_budget["max_attempts"],
            selected_states=search_budget["selected_states"],
            max_depth=search_budget["max_depth"], round_id=round_id,
            retrieval_hint=retrieval_hint,
            previous_round_feedback=previous_round_feedback,
            think_timeout=think_timeout,
        )
        if stepwise.get("status") == "green":
            return stepwise
        fallback_hint = "\n".join([
            retrieval_hint,
            "Stepwise search did not close the node. Preserve useful proof-state "
            "progress, avoid broad automation that timed out, and return a "
            "structured Isar proof with named intermediate facts.",
            stepwise.get("residual", "")[-12000:],
        ])
        fallback = prove_node(
            graph, node_id, proofs, checker=checker,
            premise_database=premise_database, output_dir=output_dir,
            generated_records=generated_records, provider=provider,
            model=model, timeout=timeout, max_attempts=max_attempts,
            backend="whole-proof", round_id=round_id,
            retrieval_hint=fallback_hint,
            previous_round_feedback=previous_round_feedback,
            stepwise_residual=stepwise.get("residual", ""),
            salvage_provider=salvage_provider,
            think_timeout=think_timeout,
        )
        fallback["stepwise_result"] = stepwise
        fallback["backend"] = "stepwise-then-structured-whole-proof"
        return fallback
    if backend != "whole-proof":
        raise ValueError(f"unknown lemma backend: {backend}")
    by_id = {node["id"]: node for node in graph["nodes"]}
    node = by_id[node_id]
    dependencies = [{
        "id": dep,
        "name": by_id[dep]["name"],
        "statement": by_id[dep]["statement"],
    } for dep in node["dependencies"]]
    attempts = []
    previous = None
    prior_candidates = []
    if previous_round_feedback:
        for item in previous_round_feedback.get("proof_candidates", []):
            prior_candidates.append({
                "attempt": item.get("attempt"),
                "proof_body": item.get("proof_body", ""),
                "used_premise_ids": item.get("used_premise_ids", []),
                "check": {"diagnostic": item.get("diagnostic", "")},
            })
    best_previous = (
        max(prior_candidates, key=proof_progress_line)
        if prior_candidates else None
    )
    local_outline = None
    salvage_plans = []
    forbidden_facts: set[str] = set(re.findall(
        r'Undefined fact:\s*["`]?([A-Za-z][A-Za-z0-9_\'.]*)',
        "\n".join(filter(None, [
            retrieval_hint, feedback_text, feedback_diagnostics,
        ])),
        flags=re.I,
    ))
    node_dir = (
        output_dir / "nodes" / node_id.replace("/", "_").replace(":", "_")
        / f"round-{round_id:02d}"
    )
    node_dir.mkdir(parents=True, exist_ok=True)
    module_log_path = (
        output_dir.parent.parent / "module-logs/lemma-prover"
        / (
            f"{graph['file_slot'].removesuffix('.thy')}--"
            f"{node_id.replace('/', '_').replace(':', '_')}"
            f"--round-{round_id:02d}.json"
        )
    )

    for number in range(1, max_attempts + 1):
        residual = "" if previous is None else previous["check"]["diagnostic"]
        prior_round_residual = (
            previous_round_feedback.get("residual", "")
            if previous_round_feedback else ""
        )
        query = " ".join([
            node["statement"], node["strategy"], retrieval_hint,
            prior_round_residual, residual,
            json.dumps(local_outline, ensure_ascii=False) if local_outline else "",
        ])
        premises = retrieve_proof_premises(
            premise_database, query, limit=32,
            generated_records=generated_records,
            retrieval_hint=retrieval_hint,
            residual="\n".join(filter(None, [prior_round_residual, residual])),
        )
        prompt = proof_prompt(
            node, dependencies, premises, best_previous,
            guidance=(
                retrieval_hint
                + "\n" + retrieval_repair_guidance(retrieval_hint)
                + ("\n" + "\n".join(semantic_bridge_guidance(
                    "\n".join(filter(None, [
                        node["statement"], prior_round_residual, residual,
                    ]))
                )) if semantic_bridge_guidance("\n".join(filter(None, [
                    node["statement"], prior_round_residual, residual,
                ]))) else "")
                + "\nPrefer explicit Isar `have` steps when the proof requires "
                  "several semantic projections."
                + "\nFor a state_relation update assembled from GREEN component "
                  "facts, do not finish with broad simp, clarsimp, auto, or "
                  "fastforce over state_relation_def. Use bounded simp only "
                  "or introduce its conjunction fields explicitly."
                + "\nFor pspace_relation, instantiate its nested bounded universals "
                  "with explicit drule/bspec steps; do not use blast or "
                  "fastforce to discover x and the relation cut."
                + "\nWhen applying a generated dependency, match each supplied "
                  "fact against the dependency's certified premise type and "
                  "order. In particular, "
                  "regen_setObject_other_relation_cut_preserved_at_ptr requires "
                  "the original obj_at fact (usually ao), not its weaker typ_at "
                  "consequence (typ_fact). For this pspace_relation node, "
                  "do not create a separately named rels fact. At theorem "
                  "top level use apply-style: `unfolding pspace_relation_def; "
                  "apply (rule conjI)`, close the first subgoal with dom_upd, "
                  "then `apply (intro ballI); apply clarify` before the existing "
                  "x = ptr / y = ptr case proof. This removes the ambiguous "
                  "nested rels/qed scope entirely."
                + "\n" + timeout_repair_guidance(previous)
                + "\n" + constructor_case_residual_guidance(
                    "\n".join(filter(None, [
                        prior_round_residual, residual,
                    ]))
                )
                + "\n" + projectko_type_erasure_guidance(
                    node,
                    "\n".join(filter(None, [
                        prior_round_residual, residual,
                    ])),
                )
            ),
            forbidden_facts=forbidden_facts,
            previous_round_feedback=previous_round_feedback,
            local_proof_outline=local_outline,
            latest_attempt=previous,
            attempt_history=[
                attempt_summary(item) for item in prior_candidates + attempts
            ],
        )
        transcript_prefix = allocate_artifact_prefix(
            node_dir / "llm" / f"attempt-{number:03d}"
        )
        provider_error: Exception | None = None
        try:
            response = call_llm(
                provider, prompt, transcript_prefix,
                model=model,
                timeout=min(timeout, think_timeout or timeout),
                theory=graph["theory"],
                stage=f"lemma-prover:{node_id}",
            )
        except Exception as error:
            provider_error = error
        finally:
            if transcript_prefix.with_suffix(".meta.json").is_file():
                write_llm_module_log(
                    transcript_prefix, module_log_path, module="Lemma Prover",
                    run_name=(
                        f"{graph['file_slot'].removesuffix('.thy')}--{node_id}"
                        f"--round-{round_id:02d}"
                    ),
                    context={
                        "theory": graph["theory"], "node_id": node_id,
                        "round_id": round_id, "attempt": number,
                        "backend": "whole-proof",
                    },
                )
        if provider_error is not None:
            diagnostic = (
                "LLM provider failure; no proof candidate was checked by "
                f"Isabelle: {type(provider_error).__name__}: {provider_error}"
            )
            attempt = {
                "attempt": number,
                "proof_body": "",
                "used_premise_ids": [],
                "retrieved_premises": [
                    item["record_id"] for item in premises
                ],
                "retrieved_premise_names": [
                    item["canonical_name"] for item in premises
                ],
                "forbidden_facts": sorted(forbidden_facts),
                "failure_class": "llm_provider_error",
                "check": {
                    "verdict": "red", "returncode": None,
                    "diagnostic": diagnostic,
                    "source": None, "log": None, "wall_ms": 0,
                },
            }
            attempts.append(attempt)
            (node_dir / "attempts.json").write_text(
                json.dumps(attempts, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            previous = attempt
            if (
                best_previous is None
                or proof_progress_line(attempt) > proof_progress_line(best_previous)
            ):
                best_previous = attempt
            continue
        candidate = parse_json_response(response)
        proof = candidate.get("proof_body", "").strip()
        if not proof:
            raise ValueError("Lemma Prover returned an empty proof body")
        proof, syntax_normalizations = normalize_reserved_local_fact_names(proof)
        candidate_proofs = {**proofs, node_id: proof}
        explosive = (
            projectko_type_erasure_precheck(node, proof)
            or explosive_state_relation_closer(node, proof)
        )
        if explosive:
            check = {
                "verdict": "red", "returncode": None,
                "diagnostic": explosive, "source": None, "log": None,
                "wall_ms": 0, "precheck": "explosive-state-relation-closer",
            }
        else:
            check = checker.check_node(graph, candidate_proofs, node_id)
        deterministic_repairs = []
        original_proof = proof
        original_check = check
        trial_proof = proof
        trial_check = check
        for repair_number in range(1, 7):
            repair = replace_redundant_by_with_fact(
                trial_proof, str(trial_check.get("diagnostic", "")),
                trial_check.get("source"),
            )
            repair_kind = "remove-redundant-by-method"
            if repair is None:
                repair = trim_redundant_local_apply(
                    trial_proof, str(trial_check.get("diagnostic", "")),
                    trial_check.get("source"),
                )
                repair_kind = "trim-redundant-local-apply"
            if repair is None:
                repair = trim_redundant_proof_terminator(
                    trial_proof, str(trial_check.get("diagnostic", "")),
                    trial_check.get("source"),
                )
                repair_kind = "trim-redundant-proof-terminator"
            if repair is None:
                break
            repaired_proof, removed_command = repair
            repaired_check = checker.check_node(
                graph, {**proofs, node_id: repaired_proof}, node_id,
            )
            deterministic_repairs.append({
                "repair": repair_number,
                "kind": repair_kind,
                "removed_command": removed_command,
                "proof_body_before": trial_proof,
                "proof_body_after": repaired_proof,
                "trigger_diagnostic": trial_check.get("diagnostic", ""),
                "check": repaired_check,
            })
            trial_proof = repaired_proof
            trial_check = repaired_check
            if repaired_check.get("verdict") == "green":
                proof = repaired_proof
                check = repaired_check
                break
        else:
            proof = original_proof
            check = original_check
        if trial_check.get("verdict") != "green":
            proof = original_proof
            check = original_check
        attempt = {
            "attempt": number,
            "proof_body": proof,
            "used_premise_ids": candidate.get("used_premise_ids", []),
            "retrieved_premises": [item["record_id"] for item in premises],
            "retrieved_premise_names": [
                item["canonical_name"] for item in premises
            ],
            "forbidden_facts": sorted(forbidden_facts),
            "syntax_normalizations": syntax_normalizations,
            "deterministic_repairs": deterministic_repairs,
            "check": check,
        }
        attempts.append(attempt)
        (node_dir / "attempts.json").write_text(
            json.dumps(attempts, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if check["verdict"] == "green":
            result = {
                "status": "green", "node_id": node_id, "proof_body": proof,
                "attempts": attempts, "salvage_plans": salvage_plans,
                "sealable_generation": all(
                    item["outline"].get("sealable_generation", False)
                    for item in salvage_plans
                ),
            }
            result_path = node_dir / "result.json"
            result_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            attach_module_outputs(module_log_path, {"proof_result": result_path})
            return result
        forbidden_facts.update(re.findall(
            r'Undefined fact:\s*["`]?([A-Za-z][A-Za-z0-9_\'.]*)',
            check["diagnostic"], flags=re.I,
        ))
        previous = attempt
        if (
            best_previous is None
            or proof_progress_line(attempt) > proof_progress_line(best_previous)
        ):
            best_previous = attempt
        if number < max_attempts:
            salvage_prefix = (
                node_dir / "salvage" / f"after-attempt-{number:03d}"
            )
            salvage_log = (
                output_dir.parent.parent / "module-logs/proof-outline-salvage"
                / (
                    f"{graph['file_slot'].removesuffix('.thy')}--"
                    f"{node_id.replace('/', '_').replace(':', '_')}"
                    f"--round-{round_id:02d}.json"
                )
            )
            local_outline = plan_local_outline(
                node, attempts, output_prefix=salvage_prefix,
                provider=salvage_provider or provider,
                model=model, timeout=min(timeout, think_timeout or timeout),
                theory=graph["theory"], stepwise_residual=stepwise_residual,
                previous_plan=local_outline, module_log_path=salvage_log,
            )
            salvage_plans.append({
                "after_attempt": number, "outline": local_outline,
            })

    result = {
        "status": "red", "node_id": node_id, "attempts": attempts,
        "salvage_plans": salvage_plans,
        "sealable_generation": all(
            item["outline"].get("sealable_generation", False)
            for item in salvage_plans
        ),
        "residual": attempts[-1]["check"]["diagnostic"],
        "retrieved_legal_premises": [{
            "record_id": item["record_id"],
            "canonical_name": item["canonical_name"],
            "exact_statement": item["exact_statement"],
            "owner_theory": item["owner_theory"],
            "score": item["score"],
        } for item in premises[:16]],
    }
    result_path = node_dir / "result.json"
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if module_log_path.is_file():
        attach_module_outputs(module_log_path, {"proof_result": result_path})
    return result
