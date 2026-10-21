#!/usr/bin/env python3
"""Audit, generate, verify, and seal the Refine.KHeap_R milestone."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_CAMPAIGN = REPO / "reports" / "regen" / "refine-arm-session"
CONTAINER = os.environ.get("REGEN_CONTAINER", "sel4-l4v")
THEORY = "Refine.KHeap_R"
PARENT_SESSION = "RegenRefineARM_L6"
SESSION = "RegenRefineARM_L7"
DEPENDENCY_DIRS = [
    "generated", "generated-layer1", "generated-layer2-invariant",
    "generated-layer2-invocations", "generated-layer3-state-relation",
    "generated-layer4-corres", "generated-layer5-bits",
    "generated-layer6-support-wave",
]

# These declarations are part of the language of a fixed KHeap_R contract.
# They are frozen semantic input, not generated proof helpers.
CONTRACT_LOCAL_SEMANTICS = [
    {
        "name": "exst_same",
        "kind": "definition",
        "source_line": 943,
        "statement": r'''definition exst_same :: "Structures_H.tcb \<Rightarrow> Structures_H.tcb \<Rightarrow> bool"
where
  "exst_same tcb tcb' \<equiv> tcbPriority tcb = tcbPriority tcb'
                      \<and> tcbTimeSlice tcb = tcbTimeSlice tcb'
                      \<and> tcbDomain tcb = tcbDomain tcb'"''',
    },
    {
        "name": "exst_same'",
        "kind": "fun",
        "source_line": 950,
        "statement": r'''fun exst_same' :: "Structures_H.kernel_object \<Rightarrow> Structures_H.kernel_object \<Rightarrow> bool"
where
  "exst_same' (KOTCB tcb) (KOTCB tcb') = exst_same tcb tcb'" |
  "exst_same' _ _ = True"''',
    },
]


def read_json(path: Path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def normalized_hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()


def container_run(arguments: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "docker", "exec", "-e", "L4V_ARCH=ARM", "-e",
            "L4V_DIR=/sel4-project/verification/l4v", CONTAINER, *arguments,
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
    )


def dependency_args(campaign_dir: Path) -> list[str]:
    result = []
    for name in DEPENDENCY_DIRS:
        result.extend([
            "-d", "/workspace/" + str((campaign_dir / name).relative_to(REPO)),
        ])
    return result


def fixed_contracts(campaign_dir: Path) -> list[dict]:
    contracts = [
        item for item in read_json(campaign_dir / "contracts.json")["contracts"]
        if item["theory"] == THEORY
    ]
    if len(contracts) != 5:
        raise SystemExit(f"expected five KHeap_R contracts, found {len(contracts)}")
    return contracts


def render_parse_shell(contracts: list[dict], *, include_closure: bool) -> str:
    declarations = CONTRACT_LOCAL_SEMANTICS if include_closure else []
    blocks = [item["statement"].rstrip() for item in declarations]
    blocks.append(r"ML \<open>writeln @{const_name exst_same'}\<close>")
    # `oops` closes proof obligations only in this private parser audit. It is
    # never accepted in generated, checked, or sealed proof sources.
    blocks.extend(item["statement"].rstrip() + "\n  oops" for item in contracts)
    return "\n".join([
        "(* Contract parser audit; not a generated proof artifact. *)",
        "theory KHeap_R_Contract_Audit",
        "imports",
        '  "AInvs.ArchDetSchedSchedule_AI"',
        f'  "{PARENT_SESSION}.Machine_R"',
        "begin",
        "context begin interpretation Arch .",
        "\n\n".join(blocks),
        "end",
        "end",
        "",
    ])


def check_shell(campaign_dir: Path, source: str, label: str) -> dict:
    out_dir = campaign_dir / "preflight" / "KHeap_R"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{label}.thy"
    source = re.sub(r"^theory KHeap_R_Contract_Audit$", f"theory {label}", source, count=1, flags=re.M)
    path.write_text(source, encoding="utf-8")
    started = time.time()
    process = container_run([
        "/workspace/verification/isabelle/bin/isabelle", "process",
        "-l", PARENT_SESSION, *dependency_args(campaign_dir),
        "-d", "/sel4-project/verification/l4v", "-T",
        "/workspace/" + str(path.with_suffix("").relative_to(REPO)),
    ])
    log = out_dir / f"{label}.log"
    log.write_text(process.stdout + process.stderr, encoding="utf-8")
    return {
        "verdict": "pass" if process.returncode == 0 else "fail",
        "returncode": process.returncode,
        "wall_ms": int((time.time() - started) * 1000),
        "source": str(path.relative_to(REPO)),
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "log": str(log.relative_to(REPO)),
        "diagnostic_tail": (process.stdout + process.stderr)[-8000:],
    }


def preflight(campaign_dir: Path) -> dict:
    contracts = fixed_contracts(campaign_dir)
    state = read_json(campaign_dir / "state.json")
    if state["session"] != PARENT_SESSION or THEORY not in state["next_ready_theories"]:
        raise SystemExit("KHeap_R is not the unique ready theory above the L6 seal")

    bare = check_shell(
        campaign_dir, render_parse_shell(contracts, include_closure=False),
        "KHeap_R_Contract_Audit_Bare",
    )
    closed = check_shell(
        campaign_dir, render_parse_shell(contracts, include_closure=True),
        "KHeap_R_Contract_Audit_Closed",
    )
    semantics = []
    for item in CONTRACT_LOCAL_SEMANTICS:
        semantics.append(item | {
            "id": f"contract-semantic:{THEORY}:{item['name']}",
            "statement_hash": normalized_hash(item["statement"]),
            "ownership": "fixed_contract_local_semantic_closure",
            "generation_policy": "immutable_not_generated",
        })
    inventory = {
        "schema_version": 1,
        "theory": THEORY,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provenance": "human-source-comparison-requested-by-user",
        "contamination": "current-codex-session-contaminated",
        "declarations": semantics,
        "closure_reason": {
            "contract_id": "contract:Refine.KHeap_R:setObject_other_corres:473d6f388612",
            "direct_reference": "exst_same'",
            "transitive_reference": "exst_same",
        },
    }
    inventory_text = json.dumps(inventory, ensure_ascii=False, sort_keys=True)
    inventory["inventory_sha256"] = hashlib.sha256(inventory_text.encode()).hexdigest()
    inventory_path = campaign_dir / "contract-local-semantics" / "KHeap_R.json"
    write_json(inventory_path, inventory)

    report = {
        "schema_version": 1,
        "theory": THEORY,
        "verdict": "pass" if bare["verdict"] == "fail" and closed["verdict"] == "pass" else "fail",
        "expected_bare_failure": bare,
        "closed_contract_parser_check": closed,
        "fixed_contract_count": len(contracts),
        "contract_local_semantic_count": len(semantics),
        "contract_local_semantic_inventory": str(inventory_path.relative_to(REPO)),
        "human_target_proofs_loaded": False,
        "human_target_proofs_sent_to_planner": False,
        "human_refine_heap_loaded": False,
        "contamination": "current-codex-session-contaminated",
    }
    write_json(campaign_dir / "preflight" / "KHeap_R" / "report.json", report)
    if report["verdict"] != "pass":
        raise SystemExit("KHeap_R contract closure audit failed")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    result = preflight(args.campaign_dir.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
