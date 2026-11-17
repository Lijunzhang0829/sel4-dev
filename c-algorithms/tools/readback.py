#!/usr/bin/env python3
"""One recorded, tool-free semantic read-back using the existing provider adapter."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="readback-001")
    args = parser.parse_args()
    catalog = json.loads((ROOT / "targets.json").read_text())
    approval = json.loads((ROOT / "review-approval.json").read_text())
    if approval["approved_semantic_binding"] != catalog["semantic_binding"]:
        raise RuntimeError("Approval does not bind these inputs")
    sections = []
    for name in ("theories/Heap_Environment.thy", "theories/Binary_Heap_C.thy",
                 "targets-session/Binary_Heap_Model.thy", "targets-session/Binary_Heap_Targets.thy"):
        path = ROOT / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != catalog["source_closure"][name]:
            raise RuntimeError("Source drift: " + name)
        text = path.read_text()
        if name.endswith("Heap_Environment.thy"):
            text = text[:text.index("lemma malloc_failure_available:")]
        text = re.sub(r"text\s+\\<open>.*?\\<close>", "", text, flags=re.S)
        text = re.sub(r"\(\*.*?\*\)", "", text, flags=re.S)
        sections.append(name + "\n" + text)
    prompt = """You are a stateless mathematical read-back reviewer. Use ONLY the formal input below.
Do not call tools, inspect files, infer the author's intention, or provide proof scripts.
For EACH of the six lemma declarations, explain in Chinese exactly what the proposition asserts:
quantifiers, premises, normal/exceptional outcomes, nonfailure, existence of outcomes, multiset multiplicities,
numeric bounds, and frame/ownership guarantees. Expand the supplied nonstandard definitions.
Distinguish the explicit external-function model from any verified libc implementation.
Identify what cannot be determined from the supplied declarations. Do not assume the goals are true.
Return ONLY valid JSON with keys: targets (list of {id, readback, assumptions, exclusions}),
shared_assumptions (list), unresolved_semantics (list), possible_vacuity_or_scope_issues (list).
No intended natural-language specification has been supplied.
FORMAL INPUT:
""" + "\n\n".join(sections)
    from regen.infrastructure.audited_llm import call_codex
    prefix = ROOT / "evidence" / args.name
    response = call_codex(prompt, prefix, model="gpt-6-astra", reasoning_effort="low",
                          clean_runtime=True, timeout=600)
    parsed = json.loads(response.strip().removeprefix("```json").removesuffix("```").strip())
    if {t["id"] for t in parsed["targets"]} != {t["id"] for t in catalog["targets"]}:
        raise RuntimeError("Read-back target coverage differs")
    result = {"semantic_binding": catalog["semantic_binding"], "model": "gpt-6-astra",
              "reasoning_effort": "low", "intent_supplied": False, "tool_events_allowed": False,
              "author_comparison_pending": True, "readback": parsed,
              "transcript_prefix": str(prefix.relative_to(ROOT))}
    prefix.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"readback_targets": len(parsed["targets"]), "artifact": str(prefix.with_suffix('.json'))}))

if __name__ == "__main__":
    main()
