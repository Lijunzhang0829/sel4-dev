"""Classify slow lemmas into search/simp/setup/mixed buckets.

Input:
  runs/db_candidates.json

Output:
  runs/db_candidates_classified.json

Goal:
  Turn the current coarse "search_frac" view into a more actionable 4-way
  taxonomy for proof-optimization work:

    - search-dominated
    - simp-dominated
    - context/setup-dominated
    - mixed

This is intentionally a rule-based classifier, not a claim of semantic truth.
It gives:
  1. a hard bucket for downstream sampling,
  2. the timing breakdown that justified the bucket,
  3. a confidence score so borderline cases can be reviewed manually.
"""

from __future__ import annotations

import json
import os
import re
from typing import Iterable

ROOT = "/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl"
IN_JSON = f"{ROOT}/runs/db_candidates.json"
OUT_JSON = f"{ROOT}/runs/db_candidates_classified.json"

SEARCH_METHOD_RE = re.compile(r"\b(auto|blast|fastforce|force|metis|fast|safe)\b")
SIMP_METHOD_RE = re.compile(r"\b(simp|clarsimp|simp_all|asm_simp_tac|wpsimp)\b")
SIMP_MOD_RE = re.compile(r"\b(simp:|add:|split:|split del:|split_del:|cong:|del:)\b")
SETUP_HEAD_RE = re.compile(
    r"^\s*(locale|interpretation|sublocale|context|instantiation|instance|datatype|record|"
    r"primrec|fun|function|termination|named_theorems|bundle|notation|no_notation)\b"
)
SETUP_METHOD_RE = re.compile(r"\b(cinit|ctac|ceqv|csymbr|vcg|wpc|wp)\b")


def _norm_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _cmd_features(name: str, text: str) -> dict[str, bool]:
    text = _norm_space(text)
    has_search = bool(SEARCH_METHOD_RE.search(name) or SEARCH_METHOD_RE.search(text))
    has_simp_method = bool(SIMP_METHOD_RE.search(name) or SIMP_METHOD_RE.search(text))
    has_simp_mod = bool(SIMP_MOD_RE.search(text))
    is_setup_head = bool(SETUP_HEAD_RE.match(text))
    is_setup_method = bool(SETUP_METHOD_RE.search(text))
    return {
        "has_search": has_search,
        "has_simp_method": has_simp_method,
        "has_simp_mod": has_simp_mod,
        "is_setup_head": is_setup_head,
        "is_setup_method": is_setup_method,
    }


def _empty_breakdown() -> dict[str, float]:
    return {
        "search_pure_s": 0.0,
        "search_mixed_s": 0.0,
        "simp_s": 0.0,
        "setup_s": 0.0,
        "other_s": 0.0,
    }


def _add_breakdown(
    bucket: dict[str, float], name: str, elapsed: float, text: str
) -> tuple[str, dict[str, bool]]:
    ft = _cmd_features(name, text)
    if ft["is_setup_head"]:
        bucket["setup_s"] += elapsed
        return "setup", ft
    if ft["has_search"] and not (ft["has_simp_method"] or ft["has_simp_mod"]):
        bucket["search_pure_s"] += elapsed
        return "search-pure", ft
    if ft["has_search"] and (ft["has_simp_method"] or ft["has_simp_mod"]):
        bucket["search_mixed_s"] += elapsed
        return "search-mixed", ft
    if ft["has_simp_method"] or ft["has_simp_mod"]:
        bucket["simp_s"] += elapsed
        return "simp", ft
    if ft["is_setup_method"] and elapsed >= 0.5:
        bucket["setup_s"] += elapsed
        return "setup", ft
    bucket["other_s"] += elapsed
    return "other", ft


def _top_contributors(cmds: Iterable[dict[str, object]], kind: str, limit: int = 3) -> list[dict[str, object]]:
    rows = [c for c in cmds if c["kind"] == kind]
    rows.sort(key=lambda c: -float(c["elapsed_s"]))
    return rows[:limit]


def _classify(total_s: float, b: dict[str, float]) -> tuple[str, float, str]:
    if total_s <= 0:
        return "mixed", 0.0, "empty-total"

    pure = b["search_pure_s"]
    mixed = b["search_mixed_s"]
    simp = b["simp_s"]
    setup = b["setup_s"]
    other = b["other_s"]

    pure_r = pure / total_s
    simp_r = (simp + mixed) / total_s
    setup_r = setup / total_s
    search_all_r = (pure + mixed) / total_s

    if setup_r >= 0.50 or (setup >= 10.0 and setup_r >= 0.35 and pure < 0.20 * total_s):
        margin = setup_r - max(search_all_r, simp_r, other / total_s)
        return "context/setup-dominated", round(max(0.0, margin), 3), "setup-share"

    if pure_r >= 0.50 and pure >= 1.5 * (simp + mixed) and pure >= 2.0 * setup:
        margin = pure_r - max((simp + mixed) / total_s, setup_r)
        return "search-dominated", round(max(0.0, margin), 3), "pure-search-share"

    if simp_r >= 0.60 and pure_r < 0.25:
        margin = simp_r - max(pure_r, setup_r)
        return "simp-dominated", round(max(0.0, margin), 3), "simp+mixed-share"

    if search_all_r >= 0.30 and (simp + mixed) / total_s >= 0.25:
        margin = min(search_all_r, (simp + mixed) / total_s)
        return "mixed", round(max(0.0, margin), 3), "search-and-simp-both-material"

    mixed_r = mixed / total_s
    if pure_r >= 0.35 and mixed_r < 0.15:
        margin = pure_r - max(simp_r, setup_r)
        return "search-dominated", round(max(0.0, margin), 3), "fallback-search-share"

    if simp_r >= 0.45:
        margin = simp_r - max(pure_r, setup_r)
        return "simp-dominated", round(max(0.0, margin), 3), "fallback-simp-share"

    return "mixed", round(max(pure_r, simp_r, setup_r) - min(pure_r, simp_r, setup_r), 3), "fallback-mixed"


def main() -> None:
    rows = json.load(open(IN_JSON))
    out = []
    summary: dict[str, dict[str, float]] = {}

    for row in rows:
        total_s = float(row.get("total_s", 0.0))
        breakdown = _empty_breakdown()
        parsed_cmds = []

        for line, name, elapsed_s, text in row.get("cmds", []):
            kind, features = _add_breakdown(breakdown, str(name), float(elapsed_s), str(text))
            parsed_cmds.append(
                {
                    "line": int(line),
                    "name": str(name),
                    "elapsed_s": float(elapsed_s),
                    "text": str(text),
                    "kind": kind,
                    "features": features,
                }
            )

        klass, confidence, reason = _classify(total_s, breakdown)
        enriched = dict(row)
        enriched["class"] = klass
        enriched["class_confidence"] = confidence
        enriched["class_reason"] = reason
        enriched["breakdown_s"] = {k: round(v, 3) for k, v in breakdown.items()}
        enriched["top_contributors"] = {
            "search_pure": _top_contributors(parsed_cmds, "search-pure"),
            "search_mixed": _top_contributors(parsed_cmds, "search-mixed"),
            "simp": _top_contributors(parsed_cmds, "simp"),
            "setup": _top_contributors(parsed_cmds, "setup"),
            "other": _top_contributors(parsed_cmds, "other"),
        }
        out.append(enriched)

        s = summary.setdefault(klass, {"count": 0, "total_s": 0.0})
        s["count"] += 1
        s["total_s"] += total_s

    out.sort(key=lambda r: (r["class"], -float(r.get("total_s", 0.0))))
    json.dump(out, open(OUT_JSON, "w"), ensure_ascii=False, indent=1)

    print(f"wrote {OUT_JSON}")
    for klass in sorted(summary):
        s = summary[klass]
        print(f"{klass:24s} count={int(s['count']):3d} total_s={s['total_s']:8.1f}")


if __name__ == "__main__":
    main()
