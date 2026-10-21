#!/usr/bin/env python3
"""Assemble, verify, and seal planned root theories for a regen campaign."""
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
CHECK_THEORY = REPO / ".claude" / "skills" / "isabelle_prover" / "scripts" / "check-theory.sh"
CONTAINER = os.environ.get("REGEN_CONTAINER", "sel4-l4v")
SESSION_NAME = "RegenRefineARM_L0"
FORBIDDEN_RE = re.compile(r"\b(sorry|oops|axiomatization)\b")


def read_json(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def root_plans(campaign_dir: Path) -> list[dict]:
    plan = read_json(campaign_dir / "planner" / "session-plan.json")
    return sorted(plan["root_export_plans"], key=lambda item: item["theory"])


def assemble(campaign_dir: Path) -> list[Path]:
    graph = read_json(campaign_dir / "theory_graph.json")["theories"]
    by_name = {item["name"]: item for item in graph}
    generated = campaign_dir / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    outputs = []
    for plan in root_plans(campaign_dir):
        theory = plan["theory"]
        if plan["decision"] != "empty" or plan.get("requirements"):
            raise SystemExit(
                f"{theory} has planned exports; run the per-theory prover before sealing"
            )
        basename = theory.split(".", 1)[1]
        imports = by_name[theory]["direct_imports"]
        source = "\n".join([
            "(* Generated from the audited session plan. Empty export interface. *)",
            f"theory {basename}",
            "imports",
            *(f'  "{item}"' for item in imports),
            "begin",
            "",
            "end",
            "",
        ])
        path = generated / f"{basename}.thy"
        path.write_text(source, encoding="utf-8")
        outputs.append(path)
        write_json(campaign_dir / "exports" / f"{basename}.json", {
            "schema_version": 1,
            "theory": theory,
            "decision": "empty",
            "exports": [],
            "attributes": [],
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "planner_plan": "planner/session-plan.json",
        })

    root_text = "\n".join([
        f'session {SESSION_NAME} in "." = BaseRefine +',
        "  sessions",
        "    Lib",
        "    CorresK",
        "    AInvs",
        "  theories",
        *(f"    {path.stem}" for path in outputs),
        "",
    ])
    (generated / "ROOT").write_text(root_text, encoding="utf-8")
    write_json(campaign_dir / "assembly.json", {
        "schema_version": 1,
        "parent_session": "BaseRefine",
        "target_session": SESSION_NAME,
        "theories": [
            {"name": path.stem, "path": str(path.relative_to(REPO)), "sha256": sha256_path(path)}
            for path in outputs
        ],
        "root_sha256": hashlib.sha256(root_text.encode()).hexdigest(),
        "human_refine_imports": [],
    })
    return outputs


def verify(campaign_dir: Path, paths: list[Path]) -> dict:
    checks = []
    check_dir = campaign_dir / "checks"
    check_dir.mkdir(parents=True, exist_ok=True)
    for path in paths:
        t0 = time.time()
        proc = subprocess.run(
            [str(CHECK_THEORY), str(path), "BaseRefine"],
            cwd=REPO, text=True, capture_output=True,
        )
        output = proc.stdout + proc.stderr
        (check_dir / f"{path.stem}.log").write_text(output, encoding="utf-8")
        check = {
            "theory": path.stem,
            "session": "BaseRefine",
            "returncode": proc.returncode,
            "wall_ms": int((time.time() - t0) * 1000),
            "verdict": "pass" if proc.returncode == 0 and "OK (" in output else "fail",
            "source_sha256": sha256_path(path),
            "forbidden_commands": sorted(set(FORBIDDEN_RE.findall(path.read_text()))),
            "log": str((check_dir / f"{path.stem}.log").relative_to(REPO)),
        }
        checks.append(check)
        if check["verdict"] != "pass" or check["forbidden_commands"]:
            write_json(campaign_dir / "checks.json", {"checks": checks})
            raise SystemExit(f"theory gate failed for {path.stem}")
    result = {"schema_version": 1, "checks": checks}
    write_json(campaign_dir / "checks.json", result)
    return result


def seal(campaign_dir: Path, paths: list[Path], checks: dict) -> dict:
    generated = campaign_dir / "generated"
    container_dir = "/workspace/" + str(generated.relative_to(REPO))
    cmd = [
        "docker", "exec", "-e", "L4V_DIR=/sel4-project/verification/l4v",
        "-e", "L4V_ARCH=ARM", CONTAINER,
        "/workspace/verification/isabelle/bin/isabelle", "build",
        "-d", container_dir, "-d", "/sel4-project/verification/l4v",
        "-b", SESSION_NAME,
    ]
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=REPO, text=True, capture_output=True)
    output = proc.stdout + proc.stderr
    build_log = campaign_dir / "seal" / "build.log"
    build_log.parent.mkdir(parents=True, exist_ok=True)
    build_log.write_text(output, encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "sealed_at": datetime.now(timezone.utc).isoformat(),
        "session": SESSION_NAME,
        "parent_session": "BaseRefine",
        "theories": [path.stem for path in paths],
        "source_hashes": {path.stem: sha256_path(path) for path in paths},
        "individual_checks": checks["checks"],
        "build_returncode": proc.returncode,
        "build_wall_ms": int((time.time() - t0) * 1000),
        "build_log": str(build_log.relative_to(REPO)),
        "human_refine_heap_loaded": False,
        "human_refine_imports": [],
        "export_interfaces": {
            path.stem: str((campaign_dir / "exports" / f"{path.stem}.json").relative_to(REPO))
            for path in paths
        },
        "status": "sealed" if proc.returncode == 0 else "seal_failed",
    }
    write_json(campaign_dir / "seal" / "manifest.json", manifest)
    if proc.returncode != 0:
        raise SystemExit("layer-0 heap build failed")
    return manifest


def container_capture(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "exec", "-e", "L4V_ARCH=ARM", CONTAINER, *args],
        cwd=REPO, text=True, capture_output=True,
    )


def finalize_existing(campaign_dir: Path) -> dict:
    manifest_path = campaign_dir / "seal" / "manifest.json"
    manifest = read_json(manifest_path)
    if manifest.get("status") != "sealed":
        raise SystemExit("cannot finalize a failed or missing seal")

    getenv = container_capture([
        "/workspace/verification/isabelle/bin/isabelle", "getenv", "-b", "ISABELLE_HEAPS"
    ])
    heap_root = getenv.stdout.strip()
    if getenv.returncode != 0 or not heap_root:
        raise SystemExit("could not resolve ISABELLE_HEAPS")
    heap_path = f"{heap_root}/polyml-5.9.1_x86_64_32-linux/{SESSION_NAME}"
    identity = container_capture(["bash", "-lc", f"sha256sum '{heap_path}'; stat -c %s '{heap_path}'"])
    if identity.returncode != 0:
        raise SystemExit("sealed heap is missing")
    identity_lines = identity.stdout.splitlines()
    manifest["heap"] = {
        "container_path": heap_path,
        "sha256": identity_lines[0].split()[0],
        "size_bytes": int(identity_lines[1]),
    }

    smoke_dir = campaign_dir / "seal" / "smoke"
    smoke_dir.mkdir(parents=True, exist_ok=True)
    smoke_path = smoke_dir / "SealSmoke.thy"
    smoke_path.write_text("\n".join([
        "theory SealSmoke",
        "imports",
        f'  "{SESSION_NAME}.LevityCatch"',
        f'  "{SESSION_NAME}.Move_R"',
        "begin",
        "end",
        "",
    ]), encoding="utf-8")
    generated_ctr = "/workspace/" + str((campaign_dir / "generated").relative_to(REPO))
    smoke_ctr = "/workspace/" + str(smoke_path.with_suffix("").relative_to(REPO))
    t0 = time.time()
    smoke = container_capture([
        "/workspace/verification/isabelle/bin/isabelle", "process",
        "-l", SESSION_NAME,
        "-d", generated_ctr,
        "-d", "/sel4-project/verification/l4v",
        "-T", smoke_ctr,
    ])
    smoke_log = smoke_dir / "check.log"
    smoke_log.write_text(smoke.stdout + smoke.stderr, encoding="utf-8")
    manifest["downstream_smoke"] = {
        "returncode": smoke.returncode,
        "verdict": "pass" if smoke.returncode == 0 else "fail",
        "wall_ms": int((time.time() - t0) * 1000),
        "source_sha256": sha256_path(smoke_path),
        "log": str(smoke_log.relative_to(REPO)),
    }
    if smoke.returncode != 0:
        write_json(manifest_path, manifest)
        raise SystemExit("sealed-heap downstream smoke test failed")
    write_json(manifest_path, manifest)

    for theory in manifest["theories"]:
        write_json(campaign_dir / f"lemma_graph-{theory}.json", {
            "schema_version": 1,
            "theory": f"Refine.{theory}",
            "status": "sealed",
            "contract_roots": [],
            "nodes": [],
            "export_interface": str(
                (campaign_dir / "exports" / f"{theory}.json").relative_to(REPO)
            ),
            "seal_session": SESSION_NAME,
            "source_sha256": manifest["source_hashes"][theory],
        })

    planner_dir = campaign_dir / "planner"
    records = [
        {
            "event": "session_plan_draft",
            "artifact": str((planner_dir / "01-session-plan.final.txt").relative_to(REPO)),
            "meta": str((planner_dir / "01-session-plan.meta.json").relative_to(REPO)),
        },
        {
            "event": "session_plan_critique",
            "artifact": str((planner_dir / "02-session-critique.final.txt").relative_to(REPO)),
            "meta": str((planner_dir / "02-session-critique.meta.json").relative_to(REPO)),
        },
        {
            "event": "root_plan_frozen",
            "artifact": str((planner_dir / "session-plan.json").relative_to(REPO)),
        },
        {
            "event": "theory_checks_green",
            "artifact": str((campaign_dir / "checks.json").relative_to(REPO)),
        },
        {
            "event": "layer_sealed",
            "artifact": str(manifest_path.relative_to(REPO)),
            "session": SESSION_NAME,
            "heap_sha256": manifest["heap"]["sha256"],
        },
    ]
    provenance_path = campaign_dir / "provenance.jsonl"
    provenance_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    write_json(campaign_dir / "state.json", {
        "schema_version": 1,
        "layer": 0,
        "status": "sealed",
        "session": SESSION_NAME,
        "theories": manifest["theories"],
        "next_ready_theories": ["Refine.ArchMove_R", "Refine.Invariants_H"],
    })
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN)
    ap.add_argument("--assemble-only", action="store_true")
    ap.add_argument("--finalize-existing", action="store_true")
    args = ap.parse_args()
    campaign_dir = args.campaign_dir.resolve()
    if args.finalize_existing:
        print(json.dumps(finalize_existing(campaign_dir), indent=2))
        return
    paths = assemble(campaign_dir)
    if args.assemble_only:
        print(json.dumps({"assembled": [str(path) for path in paths]}, indent=2))
        return
    checks = verify(campaign_dir, paths)
    manifest = seal(campaign_dir, paths, checks)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
