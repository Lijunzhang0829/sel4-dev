"""Shared declaration slicing and text retrieval for preparation and analysis."""

from __future__ import annotations

import math
import re
from collections import Counter


TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_']{2,}")
TOP_LEVEL_BOUNDARY_RE = re.compile(
    r"^(?:chapter|section|subsection|subsubsection|paragraph|text|txt|"
    r"class|locale|context|experiment|bundle|unbundle|notation|no_notation|"
    r"syntax|translations|ML|ML_file|setup|local_setup|begin|end)\b"
)


def declaration_statement(raw: str) -> str:
    """Trim top-level prose or context accidentally included by a coarse slicer."""
    lines = raw.splitlines()
    kept = []
    for index, line in enumerate(lines):
        if index and line == line.lstrip() and TOP_LEVEL_BOUNDARY_RE.match(line):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def bm25(records: list[dict], query: str, k: int) -> list[dict]:
    query_tokens = TOKEN_RE.findall(query)
    if not query_tokens or not records:
        return []
    docs = []
    document_frequency = Counter()
    for record in records:
        name_text = " ".join(record.get("candidate_names", ()))
        tokens = TOKEN_RE.findall(f"{name_text} {name_text} {record['statement']}")
        counts = Counter(tokens)
        docs.append((record, counts, len(tokens)))
        document_frequency.update(counts.keys())
    avg_len = sum(length for _, _, length in docs) / len(docs)
    scored = []
    for record, counts, length in docs:
        score = 0.0
        for token in query_tokens:
            frequency = counts[token]
            if not frequency:
                continue
            df = document_frequency[token]
            idf = math.log(1.0 + (len(docs) - df + 0.5) / (df + 0.5))
            denom = frequency + 1.5 * (
                1.0 - 0.75 + 0.75 * length / max(avg_len, 1.0)
            )
            score += idf * frequency * 2.5 / denom
        if score:
            scored.append((score, record))
    scored.sort(key=lambda item: (-item[0], item[1]["id"]))
    return [
        {"score": round(score, 6), **record}
        for score, record in scored[:k]
    ]
