#!/usr/bin/env python3
"""M2 preflight for Setting B Refine generation workspaces.

This stage does not generate proofs. It builds an auditable sanitized workspace
from M0/M1 artifacts, checks its materialized contents for classified human
Proof sources, records whether mechanical source-access isolation was actually
tested, and optionally runs an Isabelle command to elaborate contract shells.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

_INITIAL_REPO = Path(__file__).resolve().parent.parent
if str(_INITIAL_REPO) not in sys.path:
    sys.path.insert(0, str(_INITIAL_REPO))

from regen.refine_campaign import (
    read_json,
    sha256_json,
    verify_frozen_inputs,
    verify_manifest,
    write_immutable_json,
)
from regen.refine_contracts import (
    forbidden_statement_evidence,
    normalize_statement,
    sha256_path,
    verify_m1_sources,
)

FORBIDDEN_HEAP_FRAGMENTS = ("/Refine", "/CRefine", "\\Refine", "\\CRefine")
IMPORTS_RE = re.compile(r"\bimports\b(.*?)\bbegin\b", re.S)
IMPORT_NAME_RE = re.compile(r'"?([A-Za-z][A-Za-z0-9_./]*)"?')


def write_text_immutable(path: Path, text: str) -> str:
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise FileExistsError(f"refusing to replace immutable artifact: {path}")
        return "unchanged"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return "created"


def copy_immutable(src: Path, dst: Path) -> str:
    data = src.read_bytes()
    if dst.exists():
        if dst.read_bytes() != data:
            raise FileExistsError(f"refusing to replace immutable artifact: {dst}")
        return "unchanged"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    return "created"


def workspace_tree_identity(path: Path) -> dict[str, Any]:
    records = [{
        "path": str(item.relative_to(path)),
        "size_bytes": item.stat().st_size,
        "sha256": sha256_path(item),
    } for item in sorted(
        candidate for candidate in path.rglob("*") if candidate.is_file()
    )]
    return {
        "file_count": len(records),
        "files_payload_sha256": sha256_json(records),
    }


def package_paths(campaign_dir: Path) -> list[Path]:
    root = campaign_dir / "M1" / "public" / "proof-file-packages"
    return sorted(root.glob("*.json"))


def load_packages(campaign_dir: Path) -> list[dict[str, Any]]:
    return [read_json(path) for path in package_paths(campaign_dir)]


def theory_name_for_shell(file_slot: str) -> str:
    stem = file_slot.removesuffix(".thy")
    return f"M2_{stem}_Contract_Shell"


def contract_shell_text(package: dict[str, Any], import_theory: str = "M2_Frozen_Support.Corres") -> str:
    theory = theory_name_for_shell(package["file_slot"])
    lines = [
        f"theory {theory}",
        f'  imports "{import_theory}"',
        "begin",
        "",
        "unbundle l4v_word_context",
        "",
        "(* Generated from sanitized M1 public contracts only. *)",
        "",
    ]
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for contract in package["contracts"]:
        groups.setdefault(tuple(contract.get("contract_owned_context", [])), []).append(contract)
    for context_key in sorted(groups):
        context = list(context_key)
        lines.extend(context)
        if context:
            lines.append("")
        for contract in sorted(groups[context_key], key=lambda item: item["contract_id"]):
            statement = contract["exact_statement"].rstrip()
            lines.append(statement)
            lines.append("  sorry")
            lines.append("")
        if context:
            lines.extend("end" for _ in reversed(context))
            lines.append("")
    lines.append("end")
    lines.append("")
    return "\n".join(lines)


def root_text(packages: list[dict[str, Any]]) -> str:
    theories = "\n".join(f"    {theory_name_for_shell(pkg['file_slot'])}" for pkg in packages)
    return (
        "session M2_Contract_Shells = M2_Frozen_Support +\n"
        "  sessions\n"
        "    Lib\n"
        "    CorresK\n"
        "    AInvs\n"
        "  theories [quick_and_dirty]\n" + theories + "\n"
    )


def theory_imports(source: str) -> list[str]:
    match = IMPORTS_RE.search(source)
    return [] if match is None else IMPORT_NAME_RE.findall(match.group(1))


def initial_support_plan(repo: Path, campaign_dir: Path) -> dict[str, Any]:
    classification = read_json(campaign_dir / "M1/private/proof-support-classification.json")
    private = read_json(campaign_dir / "M1/private/contract-manifest.json")
    by_stem = {item["file_slot"].removesuffix(".thy"): item for item in classification["theories"]}
    source_by_theory = {
        item["theory"]: item["source_path"] for item in private["trusted_source_inventory"]
    }
    support = {item["theory"]: item for item in classification["theories"] if item["classification"] == "Support"}
    records = {}
    for theory, item in support.items():
        source_path = source_by_theory[theory]
        imports = theory_imports((repo / source_path).read_text(encoding="utf-8", errors="replace"))
        local_dependencies = []
        proof_dependencies = []
        for imported in imports:
            local = by_stem.get(imported.rsplit("/", 1)[-1].rsplit(".", 1)[-1])
            if local is None:
                continue
            local_dependencies.append(local["theory"])
            if local["classification"] == "Proof":
                proof_dependencies.append(local["theory"])
        records[theory] = {
            "theory": theory,
            "file_slot": item["file_slot"],
            "source_path": source_path,
            "imports": imports,
            "local_dependencies": sorted(set(local_dependencies)),
            "direct_proof_dependencies": sorted(set(proof_dependencies)),
        }

    eligible: list[str] = []
    remaining = set(records)
    while True:
        ready = sorted(
            theory for theory in remaining
            if not records[theory]["direct_proof_dependencies"]
            and all(dep in eligible for dep in records[theory]["local_dependencies"])
        )
        if not ready:
            break
        eligible.extend(ready)
        remaining.difference_update(ready)
    blocked = []
    for theory in sorted(remaining):
        record = records[theory]
        unavailable = sorted(dep for dep in record["local_dependencies"] if dep not in eligible)
        blocked.append({**record, "unavailable_local_dependencies": unavailable})
    return {
        "schema_version": 1,
        "artifact_type": "m2-initial-support-plan",
        "eligible_theories": [records[theory] for theory in eligible],
        "blocked_support_theories": blocked,
    }


def support_root_text(plan: dict[str, Any]) -> str:
    theories = "\n".join(
        f"    {item['file_slot'].removesuffix('.thy')}"
        for item in plan["eligible_theories"]
    )
    return (
        "session M2_Frozen_Support = BaseRefine +\n"
        "  sessions\n"
        "    Lib\n"
        "    CorresK\n"
        "    AInvs\n"
        "  theories [quick_and_dirty]\n" + theories + "\n"
    )


def current_environment(repo: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    l4v_record = next((r for r in manifest.get("trusted_source_repositories", []) if r.get("id") == "l4v"), None)
    return {
        "repo": str(repo),
        "campaign_id": manifest["campaign_id"],
        "l4v_revision": None if l4v_record is None else l4v_record.get("revision"),
        "l4v_required_clean_paths": [] if l4v_record is None else l4v_record.get("required_clean_paths", []),
        "l4v_paths_clean_at_m0": None if l4v_record is None else l4v_record.get("paths_clean"),
        "forbidden_path_policy": manifest.get("forbidden_path_policy"),
    }


def build_workspace(repo: Path, campaign_dir: Path, stage_dir: Path) -> dict[str, Any]:
    workspace = stage_dir / "sanitized-workspace"
    packages = load_packages(campaign_dir)
    dispositions: list[dict[str, str]] = []

    for src in package_paths(campaign_dir):
        dst = workspace / "public" / "proof-file-packages" / src.name
        dispositions.append({"path": str(dst.relative_to(campaign_dir)), "status": copy_immutable(src, dst)})

    for rel in [
        "M1/support-inventory.json",
        "M1/private/proof-support-classification.json",
    ]:
        src = campaign_dir / rel
        dst = workspace / "metadata" / Path(rel).name
        dispositions.append({"path": str(dst.relative_to(campaign_dir)), "status": copy_immutable(src, dst)})

    support_plan = initial_support_plan(repo, campaign_dir)
    support_dir = workspace / "frozen-support"
    for item in support_plan["eligible_theories"]:
        src = repo / item["source_path"]
        dst = support_dir / item["file_slot"]
        dispositions.append({"path": str(dst.relative_to(campaign_dir)), "status": copy_immutable(src, dst)})
    support_root = support_dir / "ROOT"
    dispositions.append({
        "path": str(support_root.relative_to(campaign_dir)),
        "status": write_text_immutable(support_root, support_root_text(support_plan)),
    })

    shell_records = []
    for package in packages:
        text = contract_shell_text(package)
        shell_file = f"{theory_name_for_shell(package['file_slot'])}.thy"
        rel = Path("contract-shells") / shell_file
        dst = workspace / rel
        status = write_text_immutable(dst, text)
        shell_records.append({
            "file_slot": package["file_slot"],
            "theory": theory_name_for_shell(package["file_slot"]),
            "path": str(rel),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "contract_count": len(package["contracts"]),
        })
        dispositions.append({"path": str(dst.relative_to(campaign_dir)), "status": status})

    root = workspace / "contract-shells" / "ROOT"
    dispositions.append({"path": str(root.relative_to(campaign_dir)), "status": write_text_immutable(root, root_text(packages))})

    return {
        "workspace": workspace,
        "packages": packages,
        "shell_records": shell_records,
        "support_plan": support_plan,
        "dispositions": dispositions,
    }


def audit_public_packages(campaign_id: str, packages: list[dict[str, Any]]) -> dict[str, Any]:
    ids: list[str] = []
    leak_hits: dict[str, list[str]] = {}
    hash_mismatches: list[str] = []
    for package in packages:
        for contract in package["contracts"]:
            ids.append(contract["contract_id"])
            evidence = forbidden_statement_evidence(contract["exact_statement"])
            if evidence:
                leak_hits[contract["contract_id"]] = evidence
            actual = hashlib.sha256(normalize_statement(contract["exact_statement"]).encode("utf-8")).hexdigest()
            if actual != contract["normalized_statement_sha256"]:
                hash_mismatches.append(contract["contract_id"])
    checks = {
        "campaign_ids_match": all(package["campaign_id"] == campaign_id for package in packages),
        "contract_ids_unique": len(ids) == len(set(ids)),
        "no_public_statement_leakage": not leak_hits,
        "statement_hashes_match": not hash_mismatches,
    }
    return {
        "verdict": "pass" if all(checks.values()) else "fail",
        "checks": checks,
        "leak_hits": leak_hits,
        "hash_mismatches": hash_mismatches,
        "package_count": len(packages),
        "contract_count": len(ids),
    }


def audit_workspace(campaign_dir: Path, workspace: Path) -> dict[str, Any]:
    forbidden = read_json(campaign_dir / "M1" / "private" / "proof-source-forbidden-inventory.json")
    proof_sources = forbidden["exact_proof_sources"]
    workspace_files = sorted(path for path in workspace.rglob("*") if path.is_file())
    proof_hashes = {item["source_sha256"] for item in proof_sources}
    proof_relpaths = {item["source_path"] for item in proof_sources}

    copied_forbidden_hashes = []
    copied_forbidden_paths = []
    textual_path_hits = []
    heap_path_hits = []
    for path in workspace_files:
        digest = sha256_path(path)
        rel = str(path.relative_to(workspace))
        if digest in proof_hashes:
            copied_forbidden_hashes.append(rel)
        if rel in proof_relpaths:
            copied_forbidden_paths.append(rel)
        text = path.read_text(encoding="utf-8", errors="ignore")
        for forbidden_path in proof_relpaths:
            if forbidden_path in text:
                textual_path_hits.append({"file": rel, "forbidden_path": forbidden_path})
                break
        if any(fragment in text for fragment in FORBIDDEN_HEAP_FRAGMENTS):
            heap_path_hits.append(rel)

    checks = {
        "no_exact_forbidden_source_hash_copied": not copied_forbidden_hashes,
        "no_forbidden_source_path_materialized": not copied_forbidden_paths,
        "no_textual_forbidden_source_path_hits": not textual_path_hits,
        "no_target_heap_path_hints": not heap_path_hits,
        "forbidden_inventory_present": len(proof_sources) > 0,
    }
    return {
        "verdict": "pass" if all(checks.values()) else "fail",
        "checks": checks,
        "workspace_file_count": len(workspace_files),
        "forbidden_proof_source_count": len(proof_sources),
        "copied_forbidden_hashes": copied_forbidden_hashes,
        "copied_forbidden_paths": copied_forbidden_paths,
        "textual_path_hits": textual_path_hits,
        "heap_path_hits": heap_path_hits,
    }


def source_access_isolation_not_run(campaign_id: str) -> dict[str, Any]:
    """Record that content inspection is not an OS-level denial guarantee."""
    return {
        "schema_version": 1,
        "artifact_type": "m2-source-access-isolation-results",
        "campaign_id": campaign_id,
        "stage": "Refine",
        "verdict": "not_run",
        "access_policy_identity": None,
        "denial_probes": [],
        "reason": (
            "the preflight materializes and audits workspace contents but does "
            "not yet execute inside a mechanically isolated filesystem or test "
            "that forbidden Proof sources and stock target heaps cannot be read"
        ),
    }


def run_elaboration(
    repo: Path, workspace: Path, isabelle: str | None, *,
    architecture: str, timeout_seconds: int | None = None,
) -> dict[str, Any]:
    shells = workspace / "contract-shells"
    if isabelle is None:
        found = shutil.which("isabelle")
        if found is None:
            return {
                "verdict": "blocked",
                "reason": "isabelle command not found on PATH; shell files were materialized but not elaborated",
                "command": None,
            }
        isabelle = found
    l4v = repo / "verification" / "l4v"
    session_catalogs = [
        l4v / "lib" / "Word_Lib",
        l4v / "lib" / "ML_Utils",
        l4v / "lib" / "Basics",
        l4v / "lib" / "Eisbach_Tools",
        l4v / "lib" / "Monads",
        l4v / "lib" / "sep_algebra",
        l4v / "lib",
        l4v / "tools" / "c-parser",
        l4v / "tools" / "autocorres",
        l4v / "tools" / "asmrefine",
        l4v / "spec",
        l4v / "proof",
    ]
    command = [isabelle, "build", "-S"]
    for catalog in session_catalogs:
        command.extend(["-d", str(catalog)])
    command.extend(["-D", str(shells), "M2_Contract_Shells"])
    env = dict(__import__("os").environ)
    env.setdefault("L4V_ARCH", architecture)
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout_seconds, env=env)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return {
            "verdict": "timeout",
            "command": command,
            "timeout_seconds": timeout_seconds,
            "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
            "stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
            "stdout_tail": stdout[-4000:],
            "stderr_tail": stderr[-4000:],
        }
    return {
        "verdict": "pass" if result.returncode == 0 else "fail",
        "command": command,
        "timeout_seconds": timeout_seconds,
        "returncode": result.returncode,
        "stdout_sha256": hashlib.sha256(result.stdout.encode("utf-8")).hexdigest(),
        "stderr_sha256": hashlib.sha256(result.stderr.encode("utf-8")).hexdigest(),
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-4000:],
    }


def preflight(repo: Path, campaign_dir: Path, *, stage_dir: Path | None = None, isabelle: str | None = None, run_isabelle: bool = True, elaboration_timeout_seconds: int | None = None) -> dict[str, Any]:
    manifest_path = campaign_dir / "M0" / "campaign-manifest.json"
    private_manifest_path = campaign_dir / "M1" / "private" / "contract-manifest.json"
    manifest = read_json(manifest_path)
    verify_manifest(manifest)
    verify_frozen_inputs(repo, manifest)
    m1_verify = verify_m1_sources(repo, private_manifest_path)
    stage_dir = stage_dir or campaign_dir / "M2"
    built = build_workspace(repo, campaign_dir, stage_dir)
    package_audit = audit_public_packages(manifest["campaign_id"], built["packages"])
    source_audit = audit_workspace(campaign_dir, built["workspace"])
    elaboration = run_elaboration(
        repo, built["workspace"], isabelle,
        architecture=manifest["architecture"],
        timeout_seconds=elaboration_timeout_seconds,
    ) if run_isabelle else {
        "verdict": "skipped",
        "reason": "--no-isabelle was requested",
    }
    environment = current_environment(repo, manifest)
    workspace_manifest = {
        "schema_version": 1,
        "artifact_type": "m2-sanitized-workspace-manifest",
        "campaign_id": manifest["campaign_id"],
        "stage": "Refine",
        "workspace_path": str(built["workspace"]),
        "workspace_tree_identity": workspace_tree_identity(built["workspace"]),
        "environment": environment,
        "proof_package_count": len(built["packages"]),
        "contract_count": sum(len(package["contracts"]) for package in built["packages"]),
        "contract_shells": built["shell_records"],
        "initial_support_plan": built["support_plan"],
        "input_verification": {
            "m0": "pass",
            "m1": m1_verify,
        },
        "materialized_paths": sorted(
            item["path"] for item in built["dispositions"]
        ),
    }
    workspace_manifest["manifest_payload_sha256"] = sha256_json(workspace_manifest)
    workspace_content_audit = {
        "schema_version": 1,
        "artifact_type": "m2-workspace-content-audit",
        "campaign_id": manifest["campaign_id"],
        "stage": "Refine",
        "audit_scope": "materialized-workspace-content-only",
        "public_package_audit": package_audit,
        "workspace_source_audit": source_audit,
        "verdict": "pass" if package_audit["verdict"] == "pass" and source_audit["verdict"] == "pass" else "fail",
        "does_not_establish": [
            "OS/container/sandbox source-access denial",
            "failed read probes for classified Proof sources",
            "failed read probes for stock Refine/CRefine heaps",
        ],
    }
    source_access_isolation = source_access_isolation_not_run(manifest["campaign_id"])
    contract_elaboration = {
        "schema_version": 1,
        "artifact_type": "m2-contract-elaboration-results",
        "campaign_id": manifest["campaign_id"],
        "stage": "Refine",
        "shell_session": "M2_Contract_Shells",
        "shell_count": len(built["shell_records"]),
        "contract_count": workspace_manifest["contract_count"],
        "result": elaboration,
        "verdict": elaboration["verdict"],
    }
    return {
        "workspace_manifest": workspace_manifest,
        "workspace_content_audit": workspace_content_audit,
        "source_access_isolation": source_access_isolation,
        "contract_elaboration": contract_elaboration,
    }


def write_m2(campaign_dir: Path, artifacts: dict[str, Any], *, stage_dir: Path | None = None) -> list[tuple[Path, str]]:
    root = stage_dir or campaign_dir / "M2"
    outputs = [
        (root / "sanitized-workspace-manifest.json", artifacts["workspace_manifest"]),
        (root / "workspace-content-audit.json", artifacts["workspace_content_audit"]),
        (root / "source-access-isolation-results.json", artifacts["source_access_isolation"]),
        (root / "contract-elaboration-results.json", artifacts["contract_elaboration"]),
    ]
    return [(path, write_immutable_json(path, value)) for path, value in outputs]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=_INITIAL_REPO)
    parser.add_argument(
        "--campaign-dir", type=Path, required=True,
        help="explicit campaign directory; no superseded campaign is selected by default",
    )
    parser.add_argument("--isabelle", help="explicit Isabelle executable")
    parser.add_argument("--stage-dir", type=Path, help="M2 output directory; defaults to <campaign>/M2")
    parser.add_argument("--elaboration-timeout-seconds", type=int, default=300)
    parser.add_argument("--no-isabelle", action="store_true", help="materialize and audit only")
    return parser


def parse_args() -> argparse.Namespace:
    return build_arg_parser().parse_args()


def m2_gate_verdict(artifacts: dict[str, Any]) -> str:
    verdicts = [
        artifacts["workspace_content_audit"]["verdict"],
        artifacts["source_access_isolation"]["verdict"],
        artifacts["contract_elaboration"]["verdict"],
    ]
    if all(verdict == "pass" for verdict in verdicts):
        return "pass"
    if "fail" in verdicts:
        return "fail"
    return "incomplete"


def m2_gate_exit_code(verdict: str) -> int:
    if verdict == "pass":
        return 0
    if verdict == "fail":
        return 1
    return 2


def main() -> int:
    args = parse_args()
    repo = args.repo.resolve()
    campaign_dir = args.campaign_dir if args.campaign_dir.is_absolute() else repo / args.campaign_dir
    stage_dir = None
    if args.stage_dir:
        stage_dir = args.stage_dir if args.stage_dir.is_absolute() else repo / args.stage_dir
    artifacts = preflight(
        repo, campaign_dir, stage_dir=stage_dir, isabelle=args.isabelle,
        run_isabelle=not args.no_isabelle,
        elaboration_timeout_seconds=args.elaboration_timeout_seconds,
    )
    results = write_m2(campaign_dir, artifacts, stage_dir=stage_dir)
    gate_verdict = m2_gate_verdict(artifacts)
    summary = {
        "campaign_id": artifacts["workspace_manifest"]["campaign_id"],
        "workspace_content_audit": artifacts["workspace_content_audit"]["verdict"],
        "source_access_isolation": artifacts["source_access_isolation"]["verdict"],
        "contract_elaboration": artifacts["contract_elaboration"]["verdict"],
        "m2_gate": gate_verdict,
        "created": sum(status == "created" for _, status in results),
        "unchanged": sum(status == "unchanged" for _, status in results),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return m2_gate_exit_code(gate_verdict)


if __name__ == "__main__":
    raise SystemExit(main())
