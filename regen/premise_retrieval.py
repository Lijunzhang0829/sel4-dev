#!/usr/bin/env python3
"""Runtime premise retrieval for one executable theorem node."""
from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_']{2,}")
CAMEL_PART = re.compile(r"[A-Z]+(?=[A-Z][a-z]|$)|[A-Z]?[a-z]+|[0-9]+")
SEMANTIC_ALIASES = {
    "ep_at": ("ep_at_def2", "obj_at_def", "is_ep_def2"),
    "ntfn_at": ("ntfn_at_def2", "obj_at_def", "is_ntfn_def2"),
    "ep_at'": ("obj_at'", "obj_at'_def", "obj_atE'", "projectKO"),
    "ntfn_at'": ("obj_at'", "obj_at'_def", "obj_atE'", "projectKO"),
    "tcb_at'": ("obj_at'", "obj_at'_def", "obj_atE'", "projectKO"),
    "get_endpoint": ("get_simple_ko_def", "get_object_def"),
    "get_notification": ("get_simple_ko_def", "get_object_def"),
    "set_endpoint": ("set_simple_ko_def", "set_object_def"),
    "set_notification": ("set_simple_ko_def", "set_object_def"),
}


def semantic_bridge_tokens(text: str) -> list[str]:
    """Map concrete residual shapes back to likely named bridge facts."""
    compact = " ".join(text.split())
    lowered = compact.lower()
    result: list[str] = []

    if re.search(r"(?<![A-Za-z0-9_'])corres(?![A-Za-z0-9_'])", text):
        result.extend(["corres_underlyingI", "corres_underlying_def"])

    aligned_upper_bound = (
        "is_aligned" in lowered
        and ("2 ^" in compact or "2^" in compact)
        and ("\\<le>" in compact or "<=" in compact or "≤" in compact)
    )
    aligned_mask_residual = (
        "&&" in compact
        and re.search(r"0x[0-9a-f]+\s*=\s*0", lowered) is not None
        and ("\\<le>" in compact or "<=" in compact or "≤" in compact)
    )
    if aligned_upper_bound or aligned_mask_residual:
        result.extend([
            "is_aligned_no_overflow",
            "is_aligned_no_overflow_mask",
        ])
    if aligned_mask_residual:
        result.extend(["is_aligned_mask", "mask_def"])

    if "objbits" in lowered and (
        "koendpoint" in lowered or "epsizebits" in lowered
    ):
        result.extend([
            "objBits_simps", "injectKO_ep", "objBitsKO_def", "epSizeBits_def",
        ])

    if "lookuparound2" in lowered and "fst" in lowered and "snd" in lowered:
        result.extend(["prod_eqI", "surjective_pairing"])
    if "lookuparound2" in lowered and (
        "some (" in lowered or "some(" in lowered
    ):
        result.extend(["lookupAround2_char1"])

    monad_membership = (
        "fst (" in lowered
        and (
            "getobject" in lowered
            or "assert_opt" in lowered
            or "<in>" in lowered
            or "∈" in compact
        )
    )
    if monad_membership:
        result.extend([
            "in_bind", "in_gets", "in_return", "in_assert",
            "in_assert_opt", "in_fail",
        ])
    if "projectko" in lowered and "fst (" in lowered:
        result.extend(["projectKO_eq2", "projectKO_opt_ep"])
    if "projectko_opt" in lowered and "injectko" in lowered:
        result.extend(["project_inject"])

    return list(dict.fromkeys(item.lower() for item in result))


def semantic_bridge_guidance(text: str) -> list[str]:
    """Explain the proof phase implied by a concrete residual shape."""
    compact = " ".join(text.split())
    lowered = compact.lower()
    guidance: list[str] = []
    if re.search(r"(?<![A-Za-z0-9_'])corres(?![A-Za-z0-9_'])", text):
        guidance.extend([
            "For a Refine corres goal, use corres_underlyingI rather than "
            "inventing a corresI rule. The first obligation runs from each "
            "concrete result to a matching abstract result; the second proves "
            "the concrete computation does not fail when nf' is True.",
            "Do not reverse the result direction: construct an abstract result "
            "for the concrete result supplied by corres_underlyingI.",
        ])
    if (
        "ps_clear" in lowered
        and "dom (kspspace" in lowered
        and ("+ 1" in compact or "0xf" in lowered)
    ):
        guidance.extend([
            "Do not re-prove the word interval set equality element by element. "
            "Use Invariants_H.ps_clear_def2 in the forward direction.",
            "First discharge its side condition p <= p + 1 from alignment: "
            "use Aligned.is_aligned_get_word_bits and, in the bounded-size "
            "branch, Aligned.is_aligned_no_wrap' with offset 1. Then obtain "
            "the exact range/domain equation via iffD1 of ps_clear_def2.",
        ])
    if (
        "&&" in compact
        and re.search(r"0x[0-9a-f]+\s*=\s*0", lowered) is not None
        and ("\\<le>" in compact or "<=" in compact or "≤" in compact)
    ):
        guidance.extend([
            "The current state is already below the is_aligned abstraction: its "
            "assumption is a mask equality, not an is_aligned fact.",
            "Do not erule is_aligned_no_overflow directly at this state. First "
            "derive a local is_aligned fact with is_aligned_mask and mask_def, "
            "then apply the no-overflow rule and normalize the mask/addition.",
        ])
    if "lookuparound2" in lowered and (
        "some (" in lowered or "some(" in lowered
    ):
        guidance.extend([
            "Use lookupAround2_char1 on the fst equality to recover the exact "
            "map entry; do not unfold lookupAround2 or DataMap.lookupAround.",
        ])
    if (
        "fst (" in lowered
        and ("getobject" in lowered or "assert_opt" in lowered)
    ):
        guidance.extend([
            "Treat this as monad-result membership. Prefer in_bind, in_gets, "
            "in_return, in_assert, in_assert_opt, and in_fail to unfolding "
            "bind_def.",
            "Eliminate the impossible assert_opt None result before expanding "
            "loadObject or splitting kernel_object.",
        ])
    if "projectko" in lowered and "fst (" in lowered:
        guidance.extend([
            "Convert projectKO result membership with projectKO_eq2, then use "
            "the endpoint projection equation to recover the constructor.",
        ])
    if "obj_at\x27" in lowered and "projectko" in lowered:
        guidance.extend([
            "Do not obtain projectKO_opt or an injectKO equality directly from "
            "obj_at\x27 with auto or clarsimp. First obtain ko and obj with "
            "fst (projectKO ko s) = {(obj, s)}; then prove (obj, s) membership "
            "in that fst set; only then rewrite the membership with "
            "projectKO_eq2 and, if needed, project_inject.",
        ])
    if "projectko_opt" in lowered and "injectko" in lowered:
        guidance.extend([
            "Use PSpaceStorable_H.pre_storable_class.project_inject to convert "
            "projectKO_opt ko = Some obj into injectKO obj = ko; do not split "
            "all kernel_object constructors with cases and broad simp.",
        ])
    if (
        "get_endpoint" in lowered
        and "kheap" in lowered
        and re.search(r"some\s*\(endpoint\s+[A-Za-z0-9_']+\)", lowered)
    ):
        guidance.extend([
            "The endpoint constructor payload already present in kheap is the witness for the abstract get_endpoint result.",
            "Name that payload first, then prove its exact monad-result membership; do not ask clarsimp or fastforce to invent a fresh existential endpoint.",
        ])
    if "set_endpoint" in lowered:
        guidance.extend([
            "The lower-case abstract set_endpoint is an abbreviation for "
            "set_simple_ko Endpoint; there is no set_endpoint_def theorem. "
            "Unfold KHeap_A.set_simple_ko_def instead.",
            "Do not confuse lower-case abstract set_endpoint with the "
            "capital-E concrete/Haskell setEndpoint or setEndpoint_def.",
        ])
    if "set_notification" in lowered:
        guidance.extend([
            "The lower-case abstract set_notification is an abbreviation for "
            "set_simple_ko Notification; there is no set_notification_def theorem. "
            "Unfold KHeap_A.set_simple_ko_def instead.",
        ])
    if "proof (state)" in lowered:
        guidance.extend([
            "This is Isabelle Isar state mode, not prove mode. If a previous "
            "case has just closed, emit the structural transition next and "
            "the matching case command before any apply/by tactic.",
        ])
    return guidance
STOP_TOKENS = {
    "and", "begin", "candidate", "command", "context", "cpu", "draft",
    "duplicate", "elapsed", "exception", "failed", "finish", "fixes", "goal", "lemma",
    "lbrakk", "line", "loading", "longrightarrow", "proof", "raised",
    "rbrakk", "rewrite", "rule", "shows", "structures_a", "structures_h", "subgoal",
    "subgoals", "then", "theory", "thy", "time", "toplevel_error",
    "using",
}


def tokens(text: str) -> list[str]:
    result = []
    for item in TOKEN.findall(text):
        lowered = item.lower()
        if (
            lowered in STOP_TOKENS
            or lowered.startswith(("node_check_", "stepwise_"))
        ):
            continue
        result.append(lowered)
        for part in item.replace("'", "").split("_"):
            for component in CAMEL_PART.findall(part):
                component = component.lower()
                if len(component) >= 3 and component not in STOP_TOKENS:
                    result.append(component)
    return result


def identifier_tokens(text: str) -> list[str]:
    return [
        item.lower() for item in TOKEN.findall(text)
        if item.lower() not in STOP_TOKENS
        and not item.lower().startswith(("node_check_", "stepwise_"))
    ]


def semantic_alias_tokens(text: str) -> list[str]:
    identifiers = set(identifier_tokens(text))
    return list(dict.fromkeys(
        alias
        for identifier, aliases in SEMANTIC_ALIASES.items()
        if identifier in identifiers
        for alias in (item.lower() for item in aliases)
    ))


def _score(
    query: list[str], name: str, statement: str,
    focus: list[str] | None = None,
) -> float:
    terms = Counter(tokens(name + " " + statement))
    name_terms = tokens(name)
    score = 0.0
    for query_terms, weight in ((query, 1.0), (focus or [], 4.0)):
        for term in set(query_terms):
            count = terms.get(term, 0)
            if count:
                score += weight * (1.0 + math.log(count))
            for name_term in name_terms:
                if name_term == term:
                    score += weight * (30.0 if "_" in term else 12.0)
                elif len(term) >= 5 and name_term.startswith(term):
                    score += weight * 10.0
    return score


def _query_terms(
    query_tokens: list[str], limit: int = 20, *, balanced: bool = False,
) -> list[str]:
    unique = list(dict.fromkeys(query_tokens))
    if balanced:
        identifiers = [term for term in unique if "_" in term]
        constructors = [term for term in unique if "_" not in term]
        identifier_limit = (limit + 1) // 2
        return [
            *identifiers[:identifier_limit],
            *constructors[:limit - min(identifier_limit, len(identifiers))],
        ][:limit]
    return sorted(unique, key=lambda term: "_" not in term)[:limit]


def _fact_stem(name: str) -> str:
    return re.sub(r"\(\d+\)$", "", name)


def _fact_family(name: str) -> str:
    stem = _fact_stem(name)
    parts = stem.split(".")
    return ".".join(parts[:-1]) if len(parts) >= 3 else stem


def _is_public_record(record: dict[str, Any]) -> bool:
    """Reject Isabelle exports that contain private, unnameable entities."""
    return (
        "??." not in record["canonical_name"]
        and "??." not in record["exact_statement"]
        and "__internal__" not in record["canonical_name"]
    )


def _focus_records(
    records: list[dict[str, Any]], focus_terms: list[str], per_term: int = 3,
) -> list[dict[str, Any]]:
    result = []
    seen = set()
    semantic_focus = set(focus_terms)
    generic_semantics = {
        "projectko", "relation", "object", "state", "some", "case",
        "shows", "using", "where",
    }
    indexed = [(
        record,
        tokens(record["canonical_name"]),
        tokens(record["exact_statement"]),
    ) for record in records]
    for term in dict.fromkeys(focus_terms):
        if len(term) < 7:
            continue
        candidates = []
        for record, name_terms, statement_terms in indexed:
            name_match = any(
                name_term == term or name_term.startswith(term)
                for name_term in name_terms
            )
            if term not in statement_terms and not name_match:
                continue
            name = record["canonical_name"].lower()
            bridge_bonus = 100.0 if name_match else 0.0
            if re.search(r"(?:_|\.)eq\d*(?:$|\()", name):
                bridge_bonus += 100.0
            if "_iff" in name:
                bridge_bonus += 100.0
            if "_opt_" in name:
                bridge_bonus += 10.0
            semantic_overlap = (
                semantic_focus & set(statement_terms)
            ) - generic_semantics - {term}
            bridge_bonus += 80.0 * min(2, len(semantic_overlap))
            if any(marker in name for marker in (".cong", "_dict", "__internal__")):
                bridge_bonus -= 100.0
            candidates.append((record["score"] + bridge_bonus, record))
        candidates.sort(
            key=lambda item: (-item[0], item[1]["canonical_name"]),
        )
        for _, record in candidates[:per_term]:
            if record["record_id"] not in seen:
                result.append(record)
                seen.add(record["record_id"])
    return result


def _diversify(
    records: list[dict[str, Any]], limit: int,
    focus_terms: list[str] | None = None,
) -> list[dict[str, Any]]:
    result = []
    seen = set()
    stem_counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()
    focused = _focus_records(records, focus_terms or [])
    for record in [*focused, *records]:
        if record["record_id"] in seen:
            continue
        stem = _fact_stem(record["canonical_name"])
        family = _fact_family(record["canonical_name"])
        if stem_counts[stem] >= 2 or family_counts[family] >= 6:
            continue
        result.append(record)
        seen.add(record["record_id"])
        stem_counts[stem] += 1
        family_counts[family] += 1
        if len(result) >= limit:
            break
    return result


def retrieve_premises(
    database: Path, query: str, *, limit: int = 32,
    generated_records: Path | None = None, focus_query: str = "",
) -> list[dict[str, Any]]:
    query_aliases = [
        *semantic_bridge_tokens(query), *semantic_alias_tokens(query),
    ]
    focus_aliases = [
        *semantic_bridge_tokens(focus_query), *semantic_alias_tokens(focus_query),
    ]
    # Put semantic bridge terms first so bounded SQL/focus queries cannot drop
    # them behind a long Isabelle residual from the preceding proof round.
    query_tokens = [*query_aliases, *tokens(query)]
    focus_tokens = [*focus_aliases, *tokens(focus_query)]
    focus_identifiers = [
        *focus_aliases, *identifier_tokens(focus_query),
    ]
    if not query_tokens and not focus_tokens:
        return []
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    terms = list(dict.fromkeys(
        _query_terms(focus_identifiers, limit=24, balanced=True)
        + _query_terms(query_tokens, limit=20)
    ))
    select = (
        "SELECT record_id, canonical_name, exact_statement, owner_theory, "
        "owner_session, source_class, attributes_json FROM premises WHERE "
        "canonical_name LIKE ? OR exact_statement LIKE ? "
        "ORDER BY canonical_name, record_id LIMIT 512"
    )
    rows_by_id = {}
    for term in terms:
        pattern = f"%{term}%"
        for row in connection.execute(select, (pattern, pattern)).fetchall():
            rows_by_id.setdefault(row["record_id"], row)
    connection.close()
    rows = list(rows_by_id.values())
    records = [{
        "record_id": row["record_id"],
        "canonical_name": row["canonical_name"],
        "exact_statement": row["exact_statement"],
        "owner_theory": row["owner_theory"],
        "owner_session": row["owner_session"],
        "source_class": row["source_class"],
        "attributes": json.loads(row["attributes_json"]),
    } for row in rows]
    if generated_records and generated_records.is_file():
        value = json.loads(generated_records.read_text(encoding="utf-8"))
        records.extend(value.get("records", []))
    records = [record for record in records if _is_public_record(record)]
    for record in records:
        record["score"] = _score(
            query_tokens, record["canonical_name"], record["exact_statement"],
            focus_tokens,
        )
    records.sort(key=lambda item: (-item["score"], item["canonical_name"]))
    return _diversify(
        [item for item in records if item["score"] > 0], limit,
        [*query_aliases, *focus_identifiers],
    )


def retrieve_proof_premises(
    database: Path, query: str, *, retrieval_hint: str = "",
    residual: str = "", limit: int = 32,
    generated_records: Path | None = None,
) -> list[dict[str, Any]]:
    """Keep revision-requested facts while also searching the current goal."""
    targeted = []
    if retrieval_hint.strip():
        targeted = retrieve_premises(
            database, retrieval_hint, limit=min(8, limit),
            generated_records=generated_records,
            focus_query=retrieval_hint,
        )
    broad = retrieve_premises(
        database, query, limit=limit,
        generated_records=generated_records,
        focus_query=residual,
    )
    result = []
    seen = set()
    for record in [*targeted, *broad]:
        if record["record_id"] in seen:
            continue
        result.append(record)
        seen.add(record["record_id"])
        if len(result) >= limit:
            break
    return result
