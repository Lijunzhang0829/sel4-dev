#!/usr/bin/env python3
"""Export legal facts and automation memberships from a sealed Semantic Base."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from regen.preparation.environment_gate import payload_sha256, read_json, sha256_path
from regen.preparation.refine_campaign import write_immutable_json
from regen.preparation.semantic_base_builder import SESSION_CATALOGS


def dump_theory_text() -> str:
    return r"""theory Premise_Catalog_Export
  imports "Semantic_Base.Semantic_Root"
begin

unbundle l4v_word_context

ML \<open>
local
  val thy = @{theory};
  val ctxt = Proof_Context.init_global thy;
  val plain_ctxt = Config.put show_markup false ctxt;
  val hex_digits = "0123456789abcdef";
  fun hex_char char =
    let val byte = Char.ord char
    in String.implode
      [String.sub (hex_digits, byte div 16), String.sub (hex_digits, byte mod 16)]
    end;
  fun enc text = String.translate hex_char text;
  fun prop_of thm =
    Print_Mode.setmp [] (Syntax.string_of_term plain_ctxt) (Thm.full_prop_of thm);
  fun owner_of thm = Thm.theory_name {long = false} thm;
  fun app_index f =
    let fun apply _ [] = ()
          | apply index (item :: items) =
              (f (index, item); apply (index + 1) items)
    in apply 0 end;
  fun write_facts path facts =
    let
      val stream = TextIO.openOut (File.platform_path path);
      fun write_fact (name, thms) =
        app_index (fn (index, thm) =>
          TextIO.output (stream,
            space_implode "\t"
              ["FACT", enc name, string_of_int index, enc (owner_of thm),
               enc (prop_of thm), enc (Thm.get_name_hint thm),
               enc (Thm.raw_derivation_name thm)] ^ "\n")) thms;
      val _ = List.app write_fact facts;
    in TextIO.closeOut stream end;
  fun write_pools path pools =
    let
      val stream = TextIO.openOut (File.platform_path path);
      fun write_pool tag thms =
        List.app (fn thm =>
          TextIO.output (stream,
            space_implode "\t"
              ["POOL", tag, enc (owner_of thm), enc (prop_of thm),
               enc (Thm.get_name_hint thm), enc (Thm.raw_derivation_name thm)]
            ^ "\n")) thms;
      val _ = List.app (fn (tag, thms) => write_pool tag thms) pools;
    in TextIO.closeOut stream end;
  fun write_metadata path rows =
    let
      val stream = TextIO.openOut (File.platform_path path);
      val _ = List.app (fn (tag, fields) =>
        TextIO.output (stream,
          space_implode "\t" ("META" :: tag :: map enc fields) ^ "\n")) rows;
    in TextIO.closeOut stream end;

  val facts = Facts.dest_all (Context.Proof ctxt) false [] (Global_Theory.facts_of thy);
  val {simps, procs, congs, weak_congs, loopers,
       unsafe_solvers, safe_solvers} =
    Raw_Simplifier.dest_ss (simpset_of ctxt);
  val wp = WeakestPre.debug_get ctxt;
  val wp_rules = WeakestPre.dest_rules (#rules wp);
  val wp_trips = fst (#trips wp);
  val wp_pre = Named_Theorems.get ctxt @{named_theorems wp_pre};
  val corres = Named_Theorems.get ctxt @{named_theorems corres};
  val named_collections =
    map_filter (fn (name, _) =>
      (case try (Named_Theorems.check ctxt) (name, Position.none) of
        SOME internal => SOME (internal, Named_Theorems.get ctxt internal)
      | NONE => NONE)) facts;
  val {safeIs, safeEs, unsafeIs, unsafeEs, swrappers, uwrappers, ...} =
    Classical.rep_cs (Classical.get_cs (Context.Proof ctxt));
  fun classical_rules rules = map #1 (Item_Net.content rules);
  val congruence_pools =
    map (fn ((weak, _), thm) =>
      (if weak then "weak_cong" else "cong", [thm])) congs;
  val named_pools =
    map (fn (name, thms) => ("named:" ^ name, thms)) named_collections;
  val pools =
    [("simp", map #2 simps),
     ("wp", wp_rules),
     ("wp_trip", wp_trips),
     ("wp_split", #splits wp),
     ("wp_comb", #combs wp),
     ("wp_unsafe", #unsafe_rules wp),
     ("wp_pre", wp_pre),
     ("corres", corres),
     ("intro_safe", classical_rules safeIs),
     ("elim_safe", classical_rules safeEs),
     ("intro_unsafe", classical_rules unsafeIs),
     ("elim_unsafe", classical_rules unsafeEs)]
    @ congruence_pools @ named_pools;
  val metadata =
    map (fn (weak, name) => ("weak_cong_name", [Bool.toString weak, name]))
      weak_congs
    @ map (fn (name, _) => ("simproc", [name])) procs
    @ map (fn name => ("simp_looper", [name])) loopers
    @ map (fn name => ("simp_safe_solver", [name])) safe_solvers
    @ map (fn name => ("simp_unsafe_solver", [name])) unsafe_solvers
    @ map (fn (name, thms) =>
        ("named_collection", [name, string_of_int (length thms)]))
      named_collections
    @ map (fn (name, _) => ("classical_safe_wrapper", [name])) swrappers
    @ map (fn (name, _) => ("classical_unsafe_wrapper", [name])) uwrappers;
in
  val _ = write_facts (Path.explode "/premise-output/facts.tsv") facts;
  val _ = write_pools (Path.explode "/premise-output/automation-pools.tsv") pools;
  val _ = write_metadata
    (Path.explode "/premise-output/context-metadata.tsv") metadata;
end
\<close>

end
"""


def root_text() -> str:
    return '''session Premise_Catalog_Export = Semantic_Base +
  sessions
    Lib
    CorresK
    AInvs
  theories
    Premise_Catalog_Export
'''


def write_text_immutable(path: Path, text: str) -> None:
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise ValueError(f"refusing to replace immutable export source: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def export_build_options() -> list[str]:
    return ["build", "-c", "-b"]


def prepare_export_heaps(source: Path, destination: Path) -> Path:
    if destination.exists():
        raise ValueError(f"export heap clone already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["cp", "-a", "--reflink=auto", str(source.resolve()), str(destination.resolve())],
        text=True, capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"failed to clone sealed heaps: {result.stderr.strip()}")
    return destination


def run_export(
    repo: Path, semantic_workspace: Path, l4v: Path, heaps: Path,
    output: Path, *, image: str, timeout_seconds: int,
) -> dict[str, Any]:
    workspace = output / "export-workspace"
    raw = output / "raw-export"
    export_heaps = prepare_export_heaps(heaps, output / "export-heaps")
    raw.mkdir(parents=True, exist_ok=True)
    write_text_immutable(workspace / "Premise_Catalog_Export.thy", dump_theory_text())
    write_text_immutable(workspace / "ROOT", root_text())
    container = f"premise-export-{os.getpid()}-{int(time.time())}"
    command = [
        "docker", "run", "--rm", "--network", "none", "--name", container,
        "--mount", f"type=bind,src={l4v.resolve()},dst=/sel4-project/verification/l4v,readonly",
        "--mount", f"type=bind,src={(repo / 'verification/isabelle').resolve()},dst=/workspace/verification/isabelle,readonly",
        "--mount", f"type=bind,src={semantic_workspace.resolve()},dst=/semantic-base,readonly",
        "--mount", f"type=bind,src={workspace.resolve()},dst=/premise-workspace,readonly",
        "--mount", f"type=bind,src={raw.resolve()},dst=/premise-output",
        "--mount", f"type=bind,src={export_heaps.resolve()},dst=/root/.isabelle/heaps",
        "-e", "L4V_ARCH=ARM", image,
        "/workspace/verification/isabelle/bin/isabelle", *export_build_options(),
    ]
    for catalog in SESSION_CATALOGS:
        command.extend(["-d", f"/sel4-project/verification/l4v/{catalog}"])
    command.extend([
        "-d", "/semantic-base", "-d", "/premise-workspace",
        "Premise_Catalog_Export",
    ])
    started = time.monotonic()
    try:
        result = subprocess.run(
            command, text=True, capture_output=True, timeout=timeout_seconds,
        )
        diagnostic = result.stdout + result.stderr
        verdict = "pass" if result.returncode == 0 else "fail"
        returncode = result.returncode
    except KeyboardInterrupt:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
        raise
    except subprocess.TimeoutExpired as error:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
        stdout = error.stdout.decode(errors="replace") if isinstance(error.stdout, bytes) else error.stdout or ""
        stderr = error.stderr.decode(errors="replace") if isinstance(error.stderr, bytes) else error.stderr or ""
        diagnostic = stdout + stderr
        verdict, returncode = "timeout", None
    log = output / "export.log"
    log.write_text(diagnostic, encoding="utf-8")
    facts = raw / "facts.tsv"
    pools = raw / "automation-pools.tsv"
    metadata = raw / "context-metadata.tsv"
    if verdict == "pass" and not (
        facts.is_file() and pools.is_file() and metadata.is_file()
    ):
        verdict = "fail"
        diagnostic += "\nmissing fact, automation, or context metadata export\n"
        log.write_text(diagnostic, encoding="utf-8")
    return {
        "schema_version": 1,
        "artifact_type": "premise-fact-export-result",
        "verdict": verdict,
        "returncode": returncode,
        "wall_ms": int((time.monotonic() - started) * 1000),
        "command": command,
        "log_sha256": sha256_path(log),
        "facts_sha256": sha256_path(facts) if facts.is_file() else None,
        "automation_pools_sha256": sha256_path(pools) if pools.is_file() else None,
        "context_metadata_sha256": (
            sha256_path(metadata) if metadata.is_file() else None
        ),
        "diagnostic": diagnostic[-20000:],
    }


def decode(value: str) -> str:
    return bytes.fromhex(value).decode("utf-8")


def theorem_identity(name_hint: str, derivation_name: str) -> tuple[str, str] | None:
    if derivation_name:
        return "derivation", derivation_name
    if name_hint and name_hint != "??.unknown":
        return "name_hint", name_hint
    return None


def parse_pool_identities(path: Path) -> dict[str, set[tuple[str, str]]]:
    pools = {"simp": set(), "wp": set(), "corres": set()}
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        if len(fields) != 6 or fields[0] != "POOL":
            raise ValueError(f"malformed automation pool line: {line[:120]}")
        identity = theorem_identity(decode(fields[4]), decode(fields[5]))
        if identity is not None:
            pools.setdefault(fields[1], set()).add(identity)
    return pools


def parse_context_metadata(path: Path) -> list[dict[str, Any]]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split("	")
        if len(fields) < 2 or fields[0] != "META":
            raise ValueError(f"malformed context metadata line: {line[:120]}")
        records.append({
            "kind": fields[1],
            "fields": [decode(value) for value in fields[2:]],
        })
    return records


def pool_row_counts(path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split("	")
        if len(fields) != 6 or fields[0] != "POOL":
            raise ValueError(f"malformed automation pool line: {line[:120]}")
        counts[fields[1]] = counts.get(fields[1], 0) + 1
    return dict(sorted(counts.items()))


def build_context_effect_disclosure(
    seal: dict[str, Any], export_result: dict[str, Any],
    records: list[dict[str, Any]], pools_path: Path, metadata_path: Path,
) -> dict[str, Any]:
    if export_result.get("verdict") != "pass":
        raise ValueError("cannot disclose context effects from a failed export")
    records = sorted(records, key=lambda item: item["record_id"])
    metadata = parse_context_metadata(metadata_path)
    metadata_counts: dict[str, int] = {}
    for item in metadata:
        metadata_counts[item["kind"]] = metadata_counts.get(item["kind"], 0) + 1
    retained = [
        item for item in records
        if item["source_class"] == "retained_semantic_export"
    ]
    membership_counts: dict[str, int] = {}
    for record in retained:
        for attribute in record.get("attributes", []):
            membership_counts[attribute] = membership_counts.get(attribute, 0) + 1
    refinement = sorted({
        item["canonical_name"] for item in retained
        if any(token in item["canonical_name"].lower() for token in (
            "corres", "refine", "relation",
        ))
    })
    named_collections = sorted({
        item["fields"][0] for item in metadata
        if item["kind"] == "named_collection" and item["fields"]
    })
    return {
        "schema_version": 2,
        "artifact_type": "context-effect-disclosure",
        "campaign_id": seal["campaign_id"],
        "semantic_base_seal_id": seal["seal_id"],
        "status": "complete",
        "gate_requirement_satisfied": True,
        "measurement_scope": (
            "active Semantic Base boundary; retained membership attribution "
            "uses exported fact ownership, not command-level causal differencing"
        ),
        "coverage": {
            "facts": True,
            "simp": True,
            "wp": True,
            "cong": True,
            "weak_cong": True,
            "classical_intro_elim": True,
            "named_collections": True,
        },
        "exported_fact_count": len(records),
        "retained_exported_fact_count": len(retained),
        "retained_automation_memberships": dict(sorted(membership_counts.items())),
        "active_pool_rows": pool_row_counts(pools_path),
        "context_metadata_counts": dict(sorted(metadata_counts.items())),
        "named_collection_count": len(named_collections),
        "named_collections": named_collections,
        "refinement_shaped_retained_fact_count": len(refinement),
        "refinement_shaped_retained_facts": refinement,
        "premise_records_payload_sha256": payload_sha256(records),
        "raw_export": {
            "facts_sha256": export_result["facts_sha256"],
            "automation_pools_sha256": export_result["automation_pools_sha256"],
            "context_metadata_sha256": export_result["context_metadata_sha256"],
        },
        "actual_generated_proof_use": {
            "status": "deferred-until-theory-generation",
            "blocking_for_environment_gate": False,
            "required_theory_gate": "retained-semantic-impact-report",
        },
    }


def parse_fact_records(
    facts_path: Path, pools_path: Path, *, campaign_id: str,
    seal_id: str, semantic_theories: set[str],
) -> list[dict[str, Any]]:
    pools = parse_pool_identities(pools_path)
    records = []
    for line in facts_path.read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        if len(fields) != 7 or fields[0] != "FACT":
            raise ValueError(f"malformed fact line: {line[:120]}")
        name, index = decode(fields[1]), int(fields[2])
        owner, proposition = decode(fields[3]), decode(fields[4])
        name_hint, derivation_name = decode(fields[5]), decode(fields[6])
        if "??." in name or "??." in proposition or not proposition.strip():
            continue
        identity = theorem_identity(name_hint, derivation_name)
        attributes = sorted(
            tag for tag, identities in pools.items()
            if identity is not None and identity in identities
        )
        owner_short = owner.rsplit(".", 1)[-1]
        semantic = owner.startswith("Semantic_Base.") or owner_short in semantic_theories
        source_class = "retained_semantic_export" if semantic else "frozen_parent"
        canonical = name if index == 0 else f"{name}({index + 1})"
        statement_hash = hashlib.sha256(" ".join(proposition.split()).encode()).hexdigest()
        record_id = "premise:" + hashlib.sha256(
            f"{canonical}\0{owner}\0{statement_hash}".encode()
        ).hexdigest()[:24]
        records.append({
            "record_id": record_id,
            "canonical_name": canonical,
            "declaration_name": name,
            "fact_index": index,
            "exact_statement": proposition,
            "normalized_statement_sha256": statement_hash,
            "owner_theory": owner,
            "owner_session": "Semantic_Base" if semantic else owner.split(".", 1)[0],
            "source_class": source_class,
            "attributes": attributes,
            "definition_equations": [proposition] if name.rsplit(".", 1)[-1].endswith("_def") else [],
            "publication_state": "published",
            "provenance": {
                "campaign_id": campaign_id,
                "semantic_base_seal_id": seal_id,
                "validation_name": name,
                "validation_owner_theory": owner,
                "name_hint": name_hint,
                "derivation_name": derivation_name,
            },
        })
    records.sort(key=lambda item: item["record_id"])
    if len(records) != len({item["record_id"] for item in records}):
        raise ValueError("premise export contains duplicate record IDs")
    return records


def semantic_theory_names(workspace_payload: dict) -> set[str]:
    """Return generated theory names from a workspace manifest or build result."""
    manifest = workspace_payload.get("workspace", workspace_payload)
    theories = manifest.get("theories")
    if not isinstance(theories, list):
        raise ValueError("workspace manifest does not contain a theories list")
    return {item["theory"].rsplit(".", 1)[-1] for item in theories}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--semantic-base-seal", type=Path, required=True)
    parser.add_argument("--workspace-manifest", type=Path, required=True)
    parser.add_argument("--semantic-workspace", type=Path, required=True)
    parser.add_argument("--l4v", type=Path, required=True)
    parser.add_argument("--heaps", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--disclosure-output", type=Path, required=True)
    parser.add_argument("--image", default="sel4-public:latest")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    seal = read_json(args.semantic_base_seal)
    manifest = read_json(args.workspace_manifest)
    result = run_export(
        args.repo.resolve(), args.semantic_workspace, args.l4v, args.heaps,
        args.output, image=args.image, timeout_seconds=args.timeout,
    )
    write_immutable_json(args.output / "fact-export-result.json", result)
    if result["verdict"] != "pass":
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2
    records = parse_fact_records(
        args.output / "raw-export/facts.tsv",
        args.output / "raw-export/automation-pools.tsv",
        campaign_id=seal["campaign_id"], seal_id=seal["seal_id"],
        semantic_theories=semantic_theory_names(manifest),
    )
    write_immutable_json(args.output / "premise-records.json", {"records": records})
    disclosure = build_context_effect_disclosure(
        seal, result, records,
        args.output / "raw-export/automation-pools.tsv",
        args.output / "raw-export/context-metadata.tsv",
    )
    write_immutable_json(args.disclosure_output, disclosure)
    counts = {
        source: sum(item["source_class"] == source for item in records)
        for source in ("frozen_parent", "retained_semantic_export")
    }
    print(json.dumps({
        "verdict": "pass",
        "records": len(records),
        "source_classes": counts,
        "context_effect_disclosure": disclosure["status"],
        "named_collections": disclosure["named_collection_count"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
