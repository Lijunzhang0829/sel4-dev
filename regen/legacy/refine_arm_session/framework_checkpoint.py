#!/usr/bin/env python3
"""Assemble and checkpoint one fixed Refine framework theory."""
from __future__ import annotations

# Allow both python -m and direct script invocation.
if __package__ in {None, ""}:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import argparse
import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from regen.legacy.refine_arm_session.framework_inventory import strip_framework_theory


REPO = Path(__file__).resolve().parents[3]
DEFAULT_CAMPAIGN = REPO / "reports" / "regen" / "refine-arm-session"
CONTAINER = os.environ.get("REGEN_CONTAINER", "sel4-l4v")
CHECKPOINTS = {
    "Refine.StateRelation": {
        "parent_session": "RegenRefineARM_L2",
        "session": "RegenRefineARM_L3",
        "generated_dir": "generated-layer3-state-relation",
        "check_dir": "checks-layer3-state-relation",
        "seal_dir": "seal-layer3-state-relation",
        "dependency_dirs": [
            "generated", "generated-layer1", "generated-layer2-invariant",
            "generated-layer2-invocations",
        ],
        "imports": {
            "Refine.InvariantUpdates_H": "RegenRefineARM_L2I.InvariantUpdates_H",
        },
    },
    "Refine.Corres": {
        "parent_session": "RegenRefineARM_L3",
        "session": "RegenRefineARM_L4",
        "generated_dir": "generated-layer4-corres",
        "check_dir": "checks-layer4-corres",
        "seal_dir": "seal-layer4-corres",
        "dependency_dirs": [
            "generated", "generated-layer1", "generated-layer2-invariant",
            "generated-layer2-invocations", "generated-layer3-state-relation",
        ],
        "imports": {
            "Refine.StateRelation": "RegenRefineARM_L3.StateRelation",
        },
    },
}


def read_json(path: Path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def dependency_args(campaign_dir: Path, names: list[str]) -> list[str]:
    result = []
    for name in names:
        result.extend([
            "-d", "/workspace/" + str((campaign_dir / name).relative_to(REPO)),
        ])
    return result


def heap_identity(session: str) -> dict:
    getenv = container_run([
        "/workspace/verification/isabelle/bin/isabelle", "getenv", "-b", "ISABELLE_HEAPS",
    ])
    heap_root = getenv.stdout.strip()
    heap_path = f"{heap_root}/polyml-5.9.1_x86_64_32-linux/{session}"
    identity = container_run(["bash", "-lc", f"sha256sum '{heap_path}'; stat -c %s '{heap_path}'"])
    lines = identity.stdout.splitlines()
    if identity.returncode or len(lines) < 2:
        raise SystemExit(f"could not identify heap for {session}")
    return {
        "container_path": heap_path,
        "sha256": lines[0].split()[0],
        "size_bytes": int(lines[1]),
    }


def update_state(campaign_dir: Path, theory_name: str, session: str) -> None:
    state_path = campaign_dir / "state.json"
    state = read_json(state_path)
    basename = theory_name.split(".", 1)[1]
    theories = list(state.get("theories", []))
    if basename not in theories:
        theories.append(basename)
    next_ready = {
        "Refine.StateRelation": ["Refine.Corres"],
        "Refine.Corres": ["Refine.Bits_R"],
    }[theory_name]
    write_json(state_path, {
        "schema_version": 1,
        "layer": 3 if theory_name.endswith("StateRelation") else 4,
        "status": "structural_checkpoint",
        "checkpoint_kind": "fixed_framework_complete",
        "session": session,
        "theories": theories,
        "next_ready_theories": next_ready,
        "wave_status": "complete",
    })


def run(campaign_dir: Path, theory_name: str) -> dict:
    if theory_name not in CHECKPOINTS:
        raise SystemExit(f"no framework checkpoint configuration for {theory_name}")
    config = CHECKPOINTS[theory_name]
    basename = theory_name.split(".", 1)[1]
    inventory = read_json(campaign_dir / "framework" / "inventory.json")
    graph = read_json(campaign_dir / "theory_graph.json")["theories"]
    graph_entry = next(item for item in graph if item["name"] == theory_name)
    state = read_json(campaign_dir / "state.json")
    sealed = {name if "." in name else f"Refine.{name}" for name in state["theories"]}
    waiting = sorted(set(graph_entry["target_imports"]) - sealed)
    if waiting:
        raise SystemExit(f"framework checkpoint is not ready; waiting for {waiting}")

    imports = [config["imports"].get(item, item) for item in graph_entry["direct_imports"]]
    source = (REPO / graph_entry["source_path"]).read_text(encoding="utf-8", errors="replace")
    theory_inventory = [
        item for item in inventory["declarations"] if item["theory"] == theory_name
    ]
    rendered, assembly = strip_framework_theory(
        source, basename, imports, theory_inventory
    )

    generated = campaign_dir / config["generated_dir"]
    generated.mkdir(parents=True, exist_ok=True)
    theory_path = generated / f"{basename}.thy"
    theory_path.write_text(rendered, encoding="utf-8")
    root_path = generated / "ROOT"
    root_path.write_text("\n".join([
        f'session {config["session"]} in "." = {config["parent_session"]} +',
        "  theories",
        f"    {basename}",
        "",
    ]), encoding="utf-8")

    deps = dependency_args(campaign_dir, config["dependency_dirs"])
    generated_ctr = "/workspace/" + str(generated.relative_to(REPO))
    theory_ctr = "/workspace/" + str(theory_path.with_suffix("").relative_to(REPO))
    check_dir = campaign_dir / config["check_dir"]
    check_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    check = container_run([
        "/workspace/verification/isabelle/bin/isabelle", "process",
        "-l", config["parent_session"], *deps,
        "-d", "/sel4-project/verification/l4v", "-T", theory_ctr,
    ])
    check_log = check_dir / f"{basename}.log"
    check_log.write_text(check.stdout + check.stderr, encoding="utf-8")
    check_record = {
        "verdict": "pass" if check.returncode == 0 else "fail",
        "returncode": check.returncode,
        "wall_ms": int((time.time() - started) * 1000),
        "parent_session": config["parent_session"],
        "source_sha256": sha256_path(theory_path),
        "log": str(check_log.relative_to(REPO)),
    }
    write_json(check_dir / "checks.json", {"checks": [check_record]})
    if check.returncode:
        raise SystemExit(f"{basename} framework theory check failed; see {check_log}")

    seal_dir = campaign_dir / config["seal_dir"]
    seal_dir.mkdir(parents=True, exist_ok=True)
    build_started = time.time()
    build = container_run([
        "/workspace/verification/isabelle/bin/isabelle", "build", *deps,
        "-d", generated_ctr, "-d", "/sel4-project/verification/l4v",
        "-b", config["session"],
    ])
    build_log = seal_dir / "build.log"
    build_log.write_text(build.stdout + build.stderr, encoding="utf-8")
    if build.returncode:
        raise SystemExit(f"{basename} framework heap build failed; see {build_log}")
    heap = heap_identity(config["session"])

    smoke_dir = seal_dir / "smoke"
    smoke_dir.mkdir(parents=True, exist_ok=True)
    smoke_path = smoke_dir / f"{basename}Smoke.thy"
    smoke_path.write_text("\n".join([
        f"theory {basename}Smoke",
        "imports",
        f'  "{config["session"]}.{basename}"',
        "begin",
        "end",
        "",
    ]), encoding="utf-8")
    smoke_started = time.time()
    smoke = container_run([
        "/workspace/verification/isabelle/bin/isabelle", "process",
        "-l", config["session"], *deps, "-d", generated_ctr,
        "-d", "/sel4-project/verification/l4v", "-T",
        "/workspace/" + str(smoke_path.with_suffix("").relative_to(REPO)),
    ])
    smoke_log = smoke_dir / "check.log"
    smoke_log.write_text(smoke.stdout + smoke.stderr, encoding="utf-8")
    if smoke.returncode:
        raise SystemExit(f"{basename} child-import smoke failed; see {smoke_log}")

    exports_path = campaign_dir / "exports" / f"{basename}.json"
    write_json(exports_path, {
        "schema_version": 1,
        "theory": theory_name,
        "interface_kind": "fixed_framework",
        "proof_exports": [],
        "semantic_exports": [
            {
                "id": item["id"], "name": item["name"], "kind": item["kind"],
                "statement_sha256": item["statement_sha256"],
            }
            for item in theory_inventory
        ],
        "source_sha256": sha256_path(theory_path),
    })
    manifest = {
        "schema_version": 1,
        "status": "structural_checkpoint",
        "checkpoint_kind": "fixed_framework_complete",
        "sealed_at": datetime.now(timezone.utc).isoformat(),
        "session": config["session"],
        "parent_session": config["parent_session"],
        "theory": theory_name,
        "source": str(theory_path.relative_to(REPO)),
        "source_sha256": sha256_path(theory_path),
        "framework_inventory_sha256": inventory["declaration_set_sha256"],
        "framework_declarations": len(theory_inventory),
        "assembly": assembly,
        "theory_check": check_record,
        "build_returncode": build.returncode,
        "build_wall_ms": int((time.time() - build_started) * 1000),
        "build_log": str(build_log.relative_to(REPO)),
        "heap": heap,
        "downstream_smoke": {
            "verdict": "pass",
            "returncode": smoke.returncode,
            "wall_ms": int((time.time() - smoke_started) * 1000),
            "log": str(smoke_log.relative_to(REPO)),
        },
        "human_refine_heap_loaded": False,
        "human_refine_imports": [],
        "export_interface": str(exports_path.relative_to(REPO)),
    }
    write_json(seal_dir / "manifest.json", manifest)
    write_json(campaign_dir / f"lemma_graph-{basename}.json", {
        "schema_version": 1,
        "theory": theory_name,
        "status": "framework_complete",
        "contract_roots": [],
        "nodes": [],
        "semantic_declarations": len(theory_inventory),
        "seal_session": config["session"],
        "source_sha256": sha256_path(theory_path),
    })
    update_state(campaign_dir, theory_name, config["session"])
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN)
    parser.add_argument("--theory", required=True, choices=sorted(CHECKPOINTS))
    args = parser.parse_args()
    print(json.dumps(run(args.campaign_dir.resolve(), args.theory), indent=2))


if __name__ == "__main__":
    main()
