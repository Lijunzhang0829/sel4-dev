#!/usr/bin/env python3
"""Generate, verify, and seal audited support-only theory checkpoints."""
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
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from regen.legacy.refine_arm_session.theory_trace import TheoryAuditTrail


REPO = Path(__file__).resolve().parents[3]
DEFAULT_CAMPAIGN = REPO / "reports" / "regen" / "refine-arm-session"
DEFAULT_THEORY = "Refine.InvariantUpdates_H"
L8_DEPENDENCY_DIRS = [
    "generated", "generated-layer1", "generated-layer2-invariant",
    "generated-layer2-invocations", "generated-layer3-state-relation",
    "generated-layer4-corres", "generated-layer5-bits",
    "generated-layer6-support-wave", "generated-layer7-kheap",
]
CHECKPOINTS = {
    "Refine.InvariantUpdates_H": {
        "parent_session": "RegenRefineARM_L1", "session": "RegenRefineARM_L2I",
        "generated_dir": "generated-layer2-invariant",
        "check_dir": "checks-layer2-invariant", "seal_dir": "seal-layer2-invariant",
        "dependency_dirs": ["generated", "generated-layer1"],
    },
    "Refine.Invocations_R": {
        "parent_session": "RegenRefineARM_L2I", "session": "RegenRefineARM_L2",
        "generated_dir": "generated-layer2-invocations",
        "check_dir": "checks-layer2-invocations", "seal_dir": "seal-layer2-invocations",
        "dependency_dirs": ["generated", "generated-layer1", "generated-layer2-invariant"],
    },
    "Refine.Init_R": {
        "parent_session": "RegenRefineARM_L7", "session": "RegenRefineARM_L8Init",
        "generated_dir": "generated-layer8-init",
        "check_dir": "checks-layer8-init", "seal_dir": "seal-layer8-init",
        "dependency_dirs": L8_DEPENDENCY_DIRS,
    },
    "Refine.SubMonad_R": {
        "parent_session": "RegenRefineARM_L7", "session": "RegenRefineARM_L8SubMonad",
        "generated_dir": "generated-layer8-submonad",
        "check_dir": "checks-layer8-submonad", "seal_dir": "seal-layer8-submonad",
        "dependency_dirs": L8_DEPENDENCY_DIRS,
    },
}
GENERATED_IMPORTS = {
    "Refine.Invariants_H": "RegenRefineARM_L1.Invariants_H",
    "Refine.EmptyFail": "RegenRefineARM_L6.EmptyFail",
    "Refine.KHeap_R": "RegenRefineARM_L7.KHeap_R",
}
CONTAINER = os.environ.get("REGEN_CONTAINER", "sel4-l4v")
FORBIDDEN_RE = re.compile(r"\b(sorry|oops|axiomatization)\b")


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
            "docker", "exec", "-e", "L4V_ARCH=ARM",
            "-e", "L4V_DIR=/sel4-project/verification/l4v",
            CONTAINER, *arguments,
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
    )


def run(campaign_dir: Path, theory_name: str, generation_id: str) -> dict:
    if theory_name not in CHECKPOINTS:
        raise SystemExit(f"no checkpoint configuration for {theory_name}")
    config = CHECKPOINTS[theory_name]
    basename = theory_name.split(".", 1)[1]
    audit_trail = TheoryAuditTrail(
        campaign_dir, theory_name, generation_id=generation_id,
    )
    parent_session = config["parent_session"]
    session = config["session"]
    plan_path = campaign_dir / "planner" / basename / "export-plan.json"
    plan = read_json(plan_path)
    if plan["theory"] != theory_name or plan["decision"] != "empty" or plan["requirements"]:
        raise SystemExit("checkpoint generator currently accepts only the audited empty plan")
    empty_interface_status = plan.get(
        "empty_interface_status", "provisional_until_direct_consumer_green"
    )
    invalidatable = empty_interface_status == "provisional_until_direct_consumer_green"
    preflight = read_json(campaign_dir / "preflight" / basename / "report.json")
    if preflight["verdict"] != "pass":
        raise SystemExit("preflight is not green")

    graph = read_json(campaign_dir / "theory_graph.json")["theories"]
    graph_entry = next(item for item in graph if item["name"] == theory_name)
    imports = []
    for imported in graph_entry["direct_imports"]:
        if imported.startswith("Refine.") and imported not in GENERATED_IMPORTS:
            raise SystemExit(f"no generated import mapping for target theory {imported}")
        imports.append(GENERATED_IMPORTS.get(imported, imported))

    generated = campaign_dir / config["generated_dir"]
    generated.mkdir(parents=True, exist_ok=True)
    theory_path = generated / f"{basename}.thy"
    theory_path.write_text("\n".join([
        "(* Generated from an audited empty export plan; human helpers removed. *)",
        f"theory {basename}",
        "imports",
        *(f'  "{imported}"' for imported in imports),
        "begin",
        "end",
        "",
    ]), encoding="utf-8")
    forbidden = sorted(set(FORBIDDEN_RE.findall(theory_path.read_text(encoding="utf-8"))))
    if forbidden or re.search(r"^\s*(?:lemma|theorem|corollary|lemmas)\b", theory_path.read_text(), re.M):
        raise SystemExit("empty-plan theory contains a proof declaration or forbidden command")

    root_path = generated / "ROOT"
    root_path.write_text("\n".join([
        f'session {session} in "." = {parent_session} +',
        "  theories",
        f"    {basename}",
        "",
    ]), encoding="utf-8")

    dependency_dirs = [
        "/workspace/" + str((campaign_dir / item).relative_to(REPO))
        for item in config["dependency_dirs"]
    ]
    dependency_args = [argument for path in dependency_dirs for argument in ("-d", path)]
    generated_ctr = "/workspace/" + str(generated.relative_to(REPO))
    theory_ctr = "/workspace/" + str(theory_path.with_suffix("").relative_to(REPO))
    check_dir = campaign_dir / config["check_dir"]
    check_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    check = container_run([
        "/workspace/verification/isabelle/bin/isabelle", "process",
        "-l", parent_session,
        *dependency_args,
        "-d", "/sel4-project/verification/l4v",
        "-T", theory_ctr,
    ])
    check_log = check_dir / f"{basename}.log"
    check_log.write_text(check.stdout + check.stderr, encoding="utf-8")
    theory_check = {
        "verdict": "pass" if check.returncode == 0 else "fail",
        "returncode": check.returncode,
        "wall_ms": int((time.time() - started) * 1000),
        "parent_session": parent_session,
        "source": str(theory_path.relative_to(REPO)),
        "source_sha256": sha256_path(theory_path),
        "log": str(check_log.relative_to(REPO)),
    }
    write_json(check_dir / "checks.json", {"checks": [theory_check]})
    audit_trail.record_isabelle("theory-check", theory_check)
    if check.returncode != 0:
        raise SystemExit(f"{basename} theory check failed")

    seal_dir = campaign_dir / config["seal_dir"]
    seal_dir.mkdir(parents=True, exist_ok=True)
    build_started = time.time()
    build = container_run([
        "/workspace/verification/isabelle/bin/isabelle", "build",
        *dependency_args, "-d", generated_ctr,
        "-d", "/sel4-project/verification/l4v",
        "-b", session,
    ])
    build_log = seal_dir / "build.log"
    build_log.write_text(build.stdout + build.stderr, encoding="utf-8")
    if build.returncode != 0:
        raise SystemExit(f"{basename} checkpoint build failed")

    heap_root_result = container_run([
        "/workspace/verification/isabelle/bin/isabelle", "getenv", "-b", "ISABELLE_HEAPS",
    ])
    heap_root = heap_root_result.stdout.strip()
    heap_path = f"{heap_root}/polyml-5.9.1_x86_64_32-linux/{session}"
    identity = container_run(["bash", "-lc", f"sha256sum '{heap_path}'; stat -c %s '{heap_path}'"])
    identity_lines = identity.stdout.splitlines()
    if identity.returncode != 0 or len(identity_lines) < 2:
        raise SystemExit("could not identify checkpoint heap")

    smoke_dir = seal_dir / "smoke"
    smoke_dir.mkdir(parents=True, exist_ok=True)
    smoke_theory = f"{basename}Smoke"
    smoke_path = smoke_dir / f"{smoke_theory}.thy"
    smoke_path.write_text("\n".join([
        f"theory {smoke_theory}",
        "imports",
        f'  "{session}.{basename}"',
        "begin",
        "end",
        "",
    ]), encoding="utf-8")
    smoke_ctr = "/workspace/" + str(smoke_path.with_suffix("").relative_to(REPO))
    smoke_started = time.time()
    smoke = container_run([
        "/workspace/verification/isabelle/bin/isabelle", "process",
        "-l", session,
        *dependency_args, "-d", generated_ctr,
        "-d", "/sel4-project/verification/l4v",
        "-T", smoke_ctr,
    ])
    smoke_log = smoke_dir / "check.log"
    smoke_log.write_text(smoke.stdout + smoke.stderr, encoding="utf-8")
    if smoke.returncode != 0:
        raise SystemExit("checkpoint downstream smoke failed")

    exports_path = campaign_dir / "exports" / f"{basename}.json"
    write_json(exports_path, {
        "schema_version": 1,
        "theory": theory_name,
        "decision": "empty",
        "empty_interface_status": empty_interface_status,
        "invalidatable_until_consumer_validated": invalidatable,
        "exports": [],
        "attributes": [],
        "source_sha256": sha256_path(theory_path),
        "planner_plan": str(plan_path.relative_to(REPO)),
    })
    write_json(campaign_dir / f"lemma_graph-{basename}.json", {
        "schema_version": 1,
        "theory": theory_name,
        "status": "sealed",
        "contract_roots": [],
        "empty_interface_status": empty_interface_status,
        "invalidatable_until_consumer_validated": invalidatable,
        "nodes": [],
        "export_interface": str(exports_path.relative_to(REPO)),
        "seal_session": session,
        "source_sha256": sha256_path(theory_path),
    })
    graph_path = campaign_dir / f"lemma_graph-{basename}.json"
    audit_trail.record_harness("assembled-interface", artifacts=[
        plan_path, campaign_dir / "preflight" / basename / "report.json",
        theory_path, root_path, exports_path, graph_path,
    ])
    audit_trail.record_harness("heap-build", artifacts=[build_log], details={
        "session": session, "returncode": build.returncode,
        "heap_sha256": identity_lines[0].split()[0],
    })
    audit_trail.record_harness("downstream-smoke", artifacts=[
        smoke_path, smoke_log,
    ], details={"verdict": "pass", "returncode": smoke.returncode})
    audit_manifest = audit_trail.seal(
        theory_path, required_llm_stages={"01-export-plan", "02-export-critique"},
    )
    manifest = {
        "schema_version": 1,
        "status": "sealed",
        "sealed_at": datetime.now(timezone.utc).isoformat(),
        "session": session,
        "parent_session": parent_session,
        "theory": theory_name,
        "source": str(theory_path.relative_to(REPO)),
        "source_sha256": sha256_path(theory_path),
        "preflight": str((campaign_dir / "preflight" / basename / "report.json").relative_to(REPO)),
        "planner_plan": str(plan_path.relative_to(REPO)),
        "theory_check": theory_check,
        "build_returncode": build.returncode,
        "build_wall_ms": int((time.time() - build_started) * 1000),
        "build_log": str(build_log.relative_to(REPO)),
        "heap": {
            "container_path": heap_path,
            "sha256": identity_lines[0].split()[0],
            "size_bytes": int(identity_lines[1]),
        },
        "downstream_smoke": {
            "verdict": "pass",
            "returncode": smoke.returncode,
            "wall_ms": int((time.time() - smoke_started) * 1000),
            "log": str(smoke_log.relative_to(REPO)),
        },
        "empty_interface_status": empty_interface_status,
        "invalidatable_until_consumer_validated": invalidatable,
        "human_refine_heap_loaded": False,
        "human_refine_imports": [],
    }
    manifest["generation_audit"] = {
        "generation_id": audit_manifest["generation_id"],
        "manifest": str(audit_trail.manifest_path.relative_to(REPO)),
        "events_sha256": audit_manifest["events_sha256"],
    }
    write_json(seal_dir / "manifest.json", manifest)
    wave_manifest = finalize_l8_wave(campaign_dir, theory_name)
    if wave_manifest is not None:
        manifest["wave_manifest"] = str(wave_manifest.relative_to(REPO))
        write_json(seal_dir / "manifest.json", manifest)
    return manifest

def finalize_l8_wave(campaign_dir: Path, theory_name: str) -> Path | None:
    if theory_name not in {"Refine.Init_R", "Refine.SubMonad_R"}:
        return None
    seal_paths = {
        "Init_R": campaign_dir / "seal-layer8-init" / "manifest.json",
        "SubMonad_R": campaign_dir / "seal-layer8-submonad" / "manifest.json",
    }
    if not all(path.is_file() for path in seal_paths.values()):
        return None
    seal_dir = campaign_dir / "seal-layer8-wave"
    manifest_path = seal_dir / "manifest.json"
    if manifest_path.is_file():
        return manifest_path
    generated = campaign_dir / "generated-layer8-wave"
    generated.mkdir(parents=True, exist_ok=True)
    sources = {
        "Init_R": campaign_dir / "generated-layer8-init" / "Init_R.thy",
        "SubMonad_R": campaign_dir / "generated-layer8-submonad" / "SubMonad_R.thy",
    }
    for basename, source in sources.items():
        if not source.is_file():
            raise SystemExit(f"missing sealed L8 source: {source}")
        shutil.copy2(source, generated / f"{basename}.thy")
    root_path = generated / "ROOT"
    root_path.write_text("\n".join([
        "session RegenRefineARM_L8 in \".\" = RegenRefineARM_L7 +",
        "  theories", "    Init_R", "    SubMonad_R", "",
    ]), encoding="utf-8")
    dependency_args = []
    for item in L8_DEPENDENCY_DIRS:
        dependency_args.extend([
            "-d", "/workspace/" + str((campaign_dir / item).relative_to(REPO)),
        ])
    generated_ctr = "/workspace/" + str(generated.relative_to(REPO))
    seal_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    build = container_run([
        "/workspace/verification/isabelle/bin/isabelle", "build",
        *dependency_args, "-d", generated_ctr,
        "-d", "/sel4-project/verification/l4v", "-b", "RegenRefineARM_L8",
    ])
    build_log = seal_dir / "build.log"
    build_log.write_text(build.stdout + build.stderr, encoding="utf-8")
    if build.returncode:
        raise SystemExit(f"L8 wave build failed; see {build_log}")
    heap_root = container_run([
        "/workspace/verification/isabelle/bin/isabelle", "getenv", "-b", "ISABELLE_HEAPS",
    ]).stdout.strip()
    heap_path = f"{heap_root}/polyml-5.9.1_x86_64_32-linux/RegenRefineARM_L8"
    identity = container_run(["bash", "-lc", f"sha256sum '{heap_path}'; stat -c %s '{heap_path}'"])
    identity_lines = identity.stdout.splitlines()
    if identity.returncode or len(identity_lines) < 2:
        raise SystemExit("could not identify L8 wave heap")
    smoke_dir = seal_dir / "smoke"
    smoke_dir.mkdir(parents=True, exist_ok=True)
    smoke_path = smoke_dir / "Layer8Smoke.thy"
    smoke_path.write_text("\n".join([
        "theory Layer8Smoke", "imports",
        "  \"RegenRefineARM_L8.Init_R\"",
        "  \"RegenRefineARM_L8.SubMonad_R\"",
        "begin", "end", "",
    ]), encoding="utf-8")
    smoke = container_run([
        "/workspace/verification/isabelle/bin/isabelle", "process",
        "-l", "RegenRefineARM_L8", *dependency_args, "-d", generated_ctr,
        "-d", "/sel4-project/verification/l4v", "-T",
        "/workspace/" + str(smoke_path.with_suffix("").relative_to(REPO)),
    ])
    smoke_log = smoke_dir / "check.log"
    smoke_log.write_text(smoke.stdout + smoke.stderr, encoding="utf-8")
    if smoke.returncode:
        raise SystemExit(f"L8 wave smoke failed; see {smoke_log}")
    wave = {
        "schema_version": 1, "status": "structural_checkpoint",
        "session": "RegenRefineARM_L8", "parent_session": "RegenRefineARM_L7",
        "theories": ["Refine.Init_R", "Refine.SubMonad_R"],
        "individual_manifests": {
            key: str(path.relative_to(REPO)) for key, path in seal_paths.items()
        },
        "sources": {
            key: {"path": str((generated / f"{key}.thy").relative_to(REPO)),
                  "sha256": sha256_path(generated / f"{key}.thy")}
            for key in sources
        },
        "build_returncode": build.returncode,
        "build_wall_ms": int((time.time() - started) * 1000),
        "build_log": str(build_log.relative_to(REPO)),
        "heap": {"container_path": heap_path,
                 "sha256": identity_lines[0].split()[0],
                 "size_bytes": int(identity_lines[1])},
        "downstream_smoke": {"verdict": "pass", "returncode": smoke.returncode,
                             "source": str(smoke_path.relative_to(REPO)),
                             "log": str(smoke_log.relative_to(REPO))},
        "human_refine_heap_loaded": False, "human_refine_imports": [],
    }
    write_json(manifest_path, wave)
    state = read_json(campaign_dir / "state.json")
    theories = list(state["theories"])
    for basename in ("Init_R", "SubMonad_R"):
        if basename not in theories:
            theories.append(basename)
    state.update({
        "layer": 8, "session": "RegenRefineARM_L8",
        "status": "structural_checkpoint", "theories": theories,
        "next_ready_theories": ["Refine.ArchAcc_R"], "wave_status": "complete",
    })
    write_json(campaign_dir / "state.json", state)
    return manifest_path





def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN)
    parser.add_argument("--theory", default=DEFAULT_THEORY)
    parser.add_argument("--generation-id", required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.campaign_dir.resolve(), args.theory, args.generation_id), indent=2))


if __name__ == "__main__":
    main()
