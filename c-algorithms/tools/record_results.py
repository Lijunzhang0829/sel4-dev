#!/usr/bin/env python3
"""Validate current-input identities before publishing preparation evidence."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def verify_files(files):
    for name, expected in files.items():
        path = ROOT / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"Evidence is stale for {name}")

def main():
    build = json.loads((ROOT / "build/latest.json").read_text())
    native = json.loads((ROOT / "evidence/native-check.json").read_text())
    catalog = json.loads((ROOT / "targets.json").read_text())
    if build["exit_code"] or native["exit_code"]:
        raise RuntimeError("A required preparation check failed")
    if not build.get("inputs_unchanged_during_check"):
        raise RuntimeError("Build did not attest unchanged inputs")
    for files in (build["inputs"], native["files"], catalog["source_closure"]):
        verify_files(files)
    if catalog["target_count"] != 6:
        raise RuntimeError("Unexpected target count")
    command = build["command"]
    command = command[:command.index("/screen/Isabelle2026/bin/isabelle") + 1]
    command += ["build_log", "-v", "-m", "120", "Calg_Binary_Targets"]
    messages = subprocess.check_output(command, text=True, timeout=90)
    markers = {}
    for key in ("TRANSLATION_FACTS_CHECKED", "PREPARATION_WITNESSES_CHECKED", "TARGETS_NOT_EXPORTED_AS_FACTS"):
        matches = re.findall(r"CALG_" + key + r" (\d+)", messages)
        if len(matches) != 1:
            raise RuntimeError(f"Missing or ambiguous native audit marker: {key}")
        markers[key.lower()] = int(matches[0])
    (ROOT / "evidence/isabelle-messages.txt").write_text(messages)
    shutil.copyfile(ROOT / build["log"], ROOT / "evidence/isabelle-build.log")
    (ROOT / "evidence/isabelle-build.json").write_text(json.dumps(build, indent=2) + "\n")
    result = {
        "scope": "preparation-only; generation outcomes are reported separately",
        "status": ("preparation_and_adapter_passed" if catalog["native_generation_adapter_checked"]
                   else "semantics_approved_adapter_pending" if catalog["generation_allowed"]
                   else "preparation_passed_pending_human_semantic_review"),
        "semantic_binding": catalog["semantic_binding"],
        "native_audit": markers, "native_differential": native["counts"],
        "target_count": 6, "functional_targets_proved_during_preparation": 0,
        "generation_evidence": "evidence/experiment-summary.json",
        "independent_readback_completed": catalog["independent_readback_completed"],
        "native_generation_adapter_checked": catalog["native_generation_adapter_checked"],
        "libc_implementation_verified": False,
        "binomial_heap_prepared": False,
        "source_closure": catalog["source_closure"],
        "limitations": ["Finite native tests do not establish universal correctness",
                        "Semantic review is not a proof of target validity",
                        "Preparation success does not imply generated target or repository proof success"]}
    (ROOT / "validation.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ("status", "native_audit", "target_count")}))

if __name__ == "__main__":
    main()
