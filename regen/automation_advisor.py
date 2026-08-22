#!/usr/bin/env python3
"""State-local Isabelle automation advice joined with legal premise records."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any


def _decode(value: str) -> str:
    try:
        return bytes.fromhex(value).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError("invalid hex field in automation context") from error


def parse_automation_context(raw: str) -> dict[str, Any]:
    counts: dict[str, int] | None = None
    records: list[dict[str, str]] = []
    for line_number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split("\t")
        kind = parts[0]
        fields = [_decode(item) for item in parts[1:]]
        if kind == "COUNTS" and len(fields) == 4:
            counts = {
                "active_simp_rules": int(fields[0]),
                "active_wp_rules": int(fields[1]),
                "relevant_simp_rules": int(fields[2]),
                "relevant_wp_rules": int(fields[3]),
            }
        elif kind in {"SIMP_RULE", "WP_RULE"} and len(fields) == 4:
            records.append({
                "kind": kind.lower(),
                "owner_theory": fields[0],
                "name_hint": fields[1],
                "derivation_name": fields[2],
                "statement": fields[3],
            })
        else:
            raise ValueError(
                f"invalid automation context record at line {line_number}: {kind}"
            )
    if counts is None:
        raise ValueError("automation context lacks COUNTS record")
    identity = {"schema_version": 1, "counts": counts, "records": records}
    payload = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return {
        **identity,
        "context_id": "automation-" + hashlib.sha256(payload.encode()).hexdigest()[:20],
    }


def _aliases(record: dict[str, Any]) -> set[str]:
    name = str(record.get("canonical_name", ""))
    return {item for item in {name, name.rsplit(".", 1)[-1]} if item}


def _active_names(record: dict[str, str]) -> set[str]:
    result = set()
    for field in ("name_hint", "derivation_name"):
        name = record.get(field, "")
        if name:
            result.add(name)
            result.add(name.rsplit(".", 1)[-1])
    return result


def build_automation_advice(
    snapshot: dict[str, Any], legal_premises: list[dict[str, Any]], proof_state: str,
    *, max_rules: int = 12,
) -> dict[str, Any]:
    legal_by_alias: dict[str, dict[str, Any]] = {}
    for premise in legal_premises:
        for alias in _aliases(premise):
            legal_by_alias.setdefault(alias, premise)

    relevant: list[dict[str, Any]] = []
    for active in snapshot["records"]:
        premise = next(
            (legal_by_alias[name] for name in _active_names(active)
             if name in legal_by_alias),
            None,
        )
        relevant.append({
            **active,
            "legal_premise_id": premise.get("record_id") if premise else None,
            "canonical_name": premise.get("canonical_name") if premise else None,
            "provenance_status": "resolved" if premise else "active-context-only",
        })

    actions: list[dict[str, Any]] = []
    simp_records = [item for item in relevant if item["kind"] == "simp_rule"]
    wp_records = [item for item in relevant if item["kind"] == "wp_rule"]
    if simp_records:
        actions.append({
            "command": "apply simp", "kind": "simp-probe", "priority": 0.78,
            "reason": "goal shares constants with active simplifier rules",
        })
        named = [
            item["canonical_name"] for item in simp_records
            if item["canonical_name"]
        ][:3]
        if named:
            actions.append({
                "command": "apply (simp add: " + " ".join(named) + ")",
                "kind": "targeted-simp-probe", "priority": 0.86,
                "reason": "goal-relevant active simp rules resolved in Premise Database",
                "premise_names": named,
            })
    monadic_goal = bool(re.search(
        r"\b(?:corres|valid|validE|validE_R|wp|do|bind|gets|return)\b|\\<lbrace>",
        proof_state,
    ))
    if wp_records and monadic_goal:
        actions.append({
            "command": "apply wp", "kind": "wp-probe", "priority": 0.9,
            "reason": "monadic goal shares constants with active WP rules",
        })

    actions.sort(key=lambda item: item["priority"], reverse=True)
    return {
        "schema_version": 1,
        "context_id": snapshot["context_id"],
        "counts": snapshot["counts"],
        "relevant_rules": relevant[:max_rules],
        "candidate_actions": actions,
    }


def advice_prompt(advice: dict[str, Any]) -> str:
    if not advice.get("candidate_actions") and not advice.get("relevant_rules"):
        return ""
    return (
        "\nIsabelle Automation Advisor (current proof context):\n"
        + json.dumps(advice, ensure_ascii=False, indent=2)
        + "\nThese are bounded candidates, not trusted proof results. Isabelle will "
          "execute every proposed command and return the actual next state.\n"
    )


def build_probe_advice(
    legal_premises: list[dict[str, Any]], proof_state: str,
) -> dict[str, Any]:
    """Build bounded commands that Isabelle, not the advisor, evaluates."""
    state_tokens = set(re.findall(r"[A-Za-z][A-Za-z0-9_']+", proof_state))
    definitions = [item for item in legal_premises
                   if str(item.get("canonical_name", "")).endswith("_def")]

    def definition_score(item: dict[str, Any]) -> tuple[int, int]:
        name = str(item.get("canonical_name", "")).rsplit(".", 1)[-1]
        stem = name.removesuffix("_def")
        statement_tokens = set(re.findall(
            r"[A-Za-z][A-Za-z0-9_']+", str(item.get("exact_statement", "")),
        ))
        return (int(stem in state_tokens), len(state_tokens & statement_tokens))

    definitions.sort(key=definition_score, reverse=True)
    definitions = [item for item in definitions
                   if definition_score(item)[0] == 1]
    wp_like = [item for item in legal_premises
               if "\\<lbrace>" in str(item.get("exact_statement", ""))]
    actions: list[dict[str, Any]] = []
    if definitions:
        actions.append({
            "command": "apply (simp add: " + " ".join(
                item["canonical_name"] for item in definitions[:3]) + ")",
            "kind": "targeted-simp-probe", "priority": 0.84,
        })
    actions.append({"command": "apply simp", "kind": "simp-context-probe",
                    "priority": 0.72})
    if re.search(r"\b(?:Some|None|option)\b", proof_state) and "case" in proof_state:
        actions.append({
            "command": "apply (simp split: option.splits)",
            "kind": "option-split-cleanup-probe", "priority": 0.80,
        })
    if re.search(r"\bif\b.*\bthen\b", proof_state, flags=re.S):
        actions.append({
            "command": "apply (simp split: if_split)",
            "kind": "if-split-cleanup-probe", "priority": 0.80,
        })
    hoare_goal = bool(re.search(
        r"\\<lbrace>.*\\<rbrace>|\bvalidE?_R?\b", proof_state, flags=re.S,
    ))
    if wp_like and hoare_goal:
        actions.append({"command": "apply wp", "kind": "wp-context-probe",
                        "priority": 0.88})
    actions.sort(key=lambda item: item["priority"], reverse=True)
    identity = {"schema_version": 1, "mode": "premise-guided-isabelle-probes",
                "candidate_actions": actions}
    payload = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return {
        **identity, "context_id": "probe-" + hashlib.sha256(
            (payload + proof_state).encode()).hexdigest()[:20],
    }
