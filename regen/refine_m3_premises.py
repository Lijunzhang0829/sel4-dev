#!/usr/bin/env python3
"""Publish the initial Refine Premise Database from an isolated, M2-green context."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from regen.refine_campaign import read_json, sha256_json, write_immutable_json
from regen.refine_contracts import normalize_statement, sha256_path, verify_m1_sources
from regen.refine_m2_isolated import (
    PLATFORM,
    SESSION_CATALOGS,
    verify_m2_artifacts,
    verify_m2_stage,
)


def run(command: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)


def dump_theory_text() -> str:
    return r'''theory M3_Premise_Dump
  imports "M2_Frozen_Support.Corres"
begin

unbundle l4v_word_context

ML \<open>
local
  val thy = @{theory};
  val ctxt = Proof_Context.init_global thy;
  val plain_ctxt = Config.put show_markup false ctxt;
  val hex_digits = "0123456789abcdef";
  fun hex_char char =
    let
      val byte = Char.ord char;
    in
      String.implode
        [String.sub (hex_digits, byte div 16), String.sub (hex_digits, byte mod 16)]
    end;
  fun enc text = String.translate hex_char text;
  fun prop_of thm =
    Print_Mode.setmp [] (Syntax.string_of_term plain_ctxt) (Thm.full_prop_of thm);
  fun owner_of thm = Thm.theory_name {long = false} thm;
  fun app_index f =
    let
      fun apply _ [] = ()
        | apply index (item :: items) = (f (index, item); apply (index + 1) items);
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
               enc (Thm.derivation_name thm)]
            ^ "\n")) thms;
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
               enc (Thm.get_name_hint thm), enc (Thm.derivation_name thm)]
            ^ "\n")) thms;
      val _ = List.app (fn (tag, thms) => write_pool tag thms) pools;
    in TextIO.closeOut stream end;
  val facts = Facts.dest_static false [] (Global_Theory.facts_of thy);
  val simps = Raw_Simplifier.dest_simps (simpset_of ctxt);
  val wps = WeakestPre.debug_get ctxt |> #rules |> WeakestPre.dest_rules;
  val corres = Named_Theorems.get ctxt @{named_theorems corres};
in
  val _ = write_facts (Path.explode "/m3-output/facts.tsv") facts;
  val _ = write_pools (Path.explode "/m3-output/automation-pools.tsv")
    [("simp", simps), ("wp", wps), ("corres", corres)];
end
\<close>

end
'''


def dump_root_text() -> str:
    return '''session M3_Premise_Dump = M2_Frozen_Support +
  sessions
    Lib
    CorresK
    AInvs
  theories
    M3_Premise_Dump
'''


def write_text_immutable(path: Path, text: str) -> str:
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise FileExistsError(f"refusing to replace immutable artifact: {path}")
        return "unchanged"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return "created"


def copy_legal_m2_heaps(m2_stage: Path, m3_stage: Path) -> dict[str, Any]:
    source = m2_stage / "isolated-environment" / "run-heaps" / PLATFORM
    destination = m3_stage / "isolated-environment" / "run-heaps" / PLATFORM
    records = []
    for path in sorted(item for item in source.rglob("*") if item.is_file()):
        relative = path.relative_to(source)
        if relative.name.startswith("M2_Contract_Shells"):
            continue
        if relative.name in {"Refine", "CRefine", "Refine.db", "CRefine.db"}:
            raise ValueError(f"stock target heap in M2 source cache: {path}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if sha256_path(target) != sha256_path(path):
                raise FileExistsError(f"M3 heap copy differs: {target}")
            disposition = "unchanged"
        else:
            shutil.copy2(path, target)
            disposition = "created"
        records.append({
            "path": str(relative), "sha256": sha256_path(path),
            "size_bytes": path.stat().st_size, "disposition": disposition,
        })
    if not any(item["path"] == "M2_Frozen_Support" for item in records):
        raise ValueError("M2_Frozen_Support heap is missing")
    stable = [{key: item[key] for key in ("path", "sha256", "size_bytes")} for item in records]
    return {
        "records": records,
        "content_sha256": sha256_json(stable),
        "run_heaps": str(destination.parent),
    }


def docker_mounts(
    repo: Path, *, sanitized_l4v: Path, m2_workspace: Path,
    m3_workspace: Path, output: Path, run_heaps: Path,
) -> list[str]:
    return [
        "--network", "none",
        "--mount", f"type=bind,src={sanitized_l4v},dst=/sel4-project/verification/l4v,readonly",
        "--mount", f"type=bind,src={repo / 'verification/isabelle'},dst=/workspace/verification/isabelle,readonly",
        "--mount", f"type=bind,src={m2_workspace},dst=/m2,readonly",
        "--mount", f"type=bind,src={m3_workspace},dst=/m3-workspace,readonly",
        "--mount", f"type=bind,src={output},dst=/m3-output",
        "--mount", f"type=bind,src={run_heaps},dst=/root/.isabelle/heaps",
        "--tmpfs", "/tmp:rw,exec,nosuid,size=2g",
    ]


def run_fact_dump(
    repo: Path, campaign_dir: Path, m2_stage: Path, preflight: Path,
    m3_stage: Path, *, image: str, timeout_seconds: int,
) -> dict[str, Any]:
    workspace = m3_stage / "dump-workspace"
    output = m3_stage / "raw-export"
    output.mkdir(parents=True, exist_ok=True)
    write_text_immutable(workspace / "M3_Premise_Dump.thy", dump_theory_text())
    write_text_immutable(workspace / "ROOT", dump_root_text())
    cache = copy_legal_m2_heaps(m2_stage, m3_stage)
    sanitized = read_json(m2_stage / "sanitized-l4v-manifest.json")
    mounts = docker_mounts(
        repo, sanitized_l4v=Path(sanitized["path"]),
        m2_workspace=preflight / "sanitized-workspace", m3_workspace=workspace,
        output=output, run_heaps=Path(cache["run_heaps"]),
    )
    command = [
        "docker", "run", "--rm", *mounts, "-e", "L4V_ARCH=ARM", image,
        "timeout", str(timeout_seconds),
        "/workspace/verification/isabelle/bin/isabelle", "build", "-S", "-v",
    ]
    for catalog in SESSION_CATALOGS:
        command.extend(["-d", f"/sel4-project/verification/l4v/{catalog}"])
    command.extend(["-D", "/m2/frozen-support", "-D", "/m3-workspace", "M3_Premise_Dump"])
    started = time.monotonic()
    result = run(command, timeout=timeout_seconds + 60)
    facts = output / "facts.tsv"
    pools = output / "automation-pools.tsv"
    passed = result.returncode == 0 and facts.is_file() and pools.is_file()
    return {
        "verdict": "pass" if passed else ("timeout" if result.returncode == 124 else "fail"),
        "returncode": result.returncode,
        "wall_ms": int((time.monotonic() - started) * 1000),
        "command": command,
        "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(result.stderr.encode()).hexdigest(),
        "stdout_tail": result.stdout[-8000:],
        "stderr_tail": result.stderr[-8000:],
        "heap_cache_identity": cache["content_sha256"],
        "facts_sha256": None if not facts.is_file() else sha256_path(facts),
        "automation_pools_sha256": None if not pools.is_file() else sha256_path(pools),
    }


def decode(value: str) -> str:
    return bytes.fromhex(value).decode("utf-8")


def theorem_identity(name_hint: str, derivation_name: str) -> tuple[str, str] | None:
    if derivation_name:
        return ("derivation", derivation_name)
    if name_hint and name_hint != "??.unknown":
        return ("name_hint", name_hint)
    return None


def parse_pool_identities(path: Path) -> dict[str, set[tuple[str, str]]]:
    pools: dict[str, set[tuple[str, str]]] = {
        "simp": set(), "wp": set(), "corres": set(),
    }
    with path.open(encoding="utf-8") as lines:
        for line in lines:
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 6 or fields[0] != "POOL":
                raise ValueError(f"malformed M3 pool line: {line[:120]}")
            identity = theorem_identity(decode(fields[4]), decode(fields[5]))
            if identity is not None:
                pools.setdefault(fields[1], set()).add(identity)
    return pools


def initial_support_owner_names(preflight_manifest: dict[str, Any]) -> set[str]:
    return {
        item["theory"].removeprefix("Refine.")
        for item in preflight_manifest["initial_support_plan"]["eligible_theories"]
    }


def published_identity(
    name: str, owner: str, support_owner_names: set[str] | None = None,
) -> tuple[str, str, str]:
    support_prefix = "M2_Frozen_Support."
    short_owner = owner.removeprefix(support_prefix)
    if owner.startswith(support_prefix) or short_owner in (support_owner_names or set()):
        published_owner = support_prefix + short_owner
        published_name = name.removeprefix(support_prefix)
        return published_name, published_owner, "frozen_support"
    return name, owner, "frozen_parent"


def parse_fact_records(
    facts_path: Path, pools_path: Path, *, campaign_id: str,
    m2_gate_sha256: str, support_owner_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    pool_identities = parse_pool_identities(pools_path)
    records = []
    with facts_path.open(encoding="utf-8") as lines:
        for line in lines:
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 7 or fields[0] != "FACT":
                raise ValueError(f"malformed M3 fact line: {line[:120]}")
            validation_name = decode(fields[1])
            index = int(fields[2])
            name_hint = decode(fields[5])
            derivation_name = decode(fields[6])
            identity = theorem_identity(name_hint, derivation_name)
            validation_owner = decode(fields[3])
            proposition = decode(fields[4])
            if "??." in validation_name or "??." in proposition:
                continue
            normalized = normalize_statement(proposition)
            statement_hash = hashlib.sha256(normalized.encode()).hexdigest()
            name, owner, source_class = published_identity(
                validation_name, validation_owner, support_owner_names,
            )
            canonical_name = name if index == 0 else f"{name}({index + 1})"
            record_id = "premise:" + hashlib.sha256(
                f"{canonical_name}\0{owner}\0{statement_hash}".encode()
            ).hexdigest()[:24]
            attributes = sorted(
                tag for tag, identities in pool_identities.items()
                if identity is not None and identity in identities
            )
            records.append({
                "record_id": record_id,
                "canonical_name": canonical_name,
                "declaration_name": name,
                "fact_index": index,
                "exact_statement": proposition,
                "normalized_statement_sha256": statement_hash,
                "owner_theory": owner,
                "owner_session": (
                    "M2_Frozen_Support"
                    if source_class == "frozen_support" else owner.split(".", 1)[0]
                ),
                "source_class": source_class,
                "attributes": attributes,
                "definition_equations": [proposition] if name.rsplit(".", 1)[-1].endswith("_def") else [],
                "publication_state": "published_initial",
                "provenance": {
                    "campaign_id": campaign_id,
                    "m2_gate_sha256": m2_gate_sha256,
                    "validation_name": validation_name,
                    "validation_name_hint": name_hint,
                    "validation_derivation_name": derivation_name,
                    "automation_identity": None if identity is None else list(identity),
                    "validation_owner_theory": validation_owner,
                    "source_location": None,
                },
            })
    records.sort(key=lambda item: (item["canonical_name"], item["fact_index"], item["record_id"]))
    return records


def create_database(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix="m3-premises-", suffix=".sqlite3", dir="/tmp", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        connection = sqlite3.connect(temporary)
        connection.executescript('''
PRAGMA journal_mode=OFF;
PRAGMA synchronous=OFF;
CREATE TABLE premises (
  record_id TEXT PRIMARY KEY,
  canonical_name TEXT NOT NULL,
  declaration_name TEXT NOT NULL,
  fact_index INTEGER NOT NULL,
  exact_statement TEXT NOT NULL,
  normalized_statement_sha256 TEXT NOT NULL,
  owner_theory TEXT NOT NULL,
  owner_session TEXT NOT NULL,
  source_class TEXT NOT NULL,
  attributes_json TEXT NOT NULL,
  definition_equations_json TEXT NOT NULL,
  publication_state TEXT NOT NULL,
  provenance_json TEXT NOT NULL
);
CREATE INDEX premises_name_idx ON premises(canonical_name);
CREATE INDEX premises_statement_idx ON premises(normalized_statement_sha256);
CREATE INDEX premises_owner_idx ON premises(owner_theory);
CREATE INDEX premises_source_class_idx ON premises(source_class);
''')
        connection.executemany(
            "INSERT INTO premises VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(
                item["record_id"], item["canonical_name"], item["declaration_name"],
                item["fact_index"], item["exact_statement"],
                item["normalized_statement_sha256"], item["owner_theory"],
                item["owner_session"], item["source_class"],
                json.dumps(item["attributes"], sort_keys=True),
                json.dumps(item["definition_equations"], sort_keys=True),
                item["publication_state"], json.dumps(item["provenance"], sort_keys=True),
            ) for item in records],
        )
        connection.commit()
        connection.execute("VACUUM")
        connection.close()
        if path.exists():
            if sha256_path(path) != sha256_path(temporary):
                raise FileExistsError(f"refusing to replace immutable premise database: {path}")
        else:
            temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_database_records(path: Path) -> list[dict[str, Any]]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT record_id, canonical_name, declaration_name, fact_index, "
        "exact_statement, normalized_statement_sha256, owner_theory, owner_session, "
        "source_class, attributes_json, definition_equations_json, publication_state, "
        "provenance_json FROM premises"
    ).fetchall()
    connection.close()
    records = [{
        "record_id": row["record_id"],
        "canonical_name": row["canonical_name"],
        "declaration_name": row["declaration_name"],
        "fact_index": row["fact_index"],
        "exact_statement": row["exact_statement"],
        "normalized_statement_sha256": row["normalized_statement_sha256"],
        "owner_theory": row["owner_theory"],
        "owner_session": row["owner_session"],
        "source_class": row["source_class"],
        "attributes": json.loads(row["attributes_json"]),
        "definition_equations": json.loads(row["definition_equations_json"]),
        "publication_state": row["publication_state"],
        "provenance": json.loads(row["provenance_json"]),
    } for row in rows]
    records.sort(key=lambda item: (item["canonical_name"], item["fact_index"], item["record_id"]))
    return records


def verify_records_snapshot(
    snapshot: dict[str, Any], records: list[dict[str, Any]],
) -> tuple[str, dict[str, int]]:
    logical_hash = sha256_json(records)
    expected_snapshot_id = f"m3-{logical_hash[:20]}"
    if snapshot.get("records_payload_sha256") != logical_hash:
        raise ValueError("M3 records payload identity does not match reconstructed raw exports")
    if snapshot.get("snapshot_id") != expected_snapshot_id:
        raise ValueError("M3 snapshot ID is not derived from records payload identity")
    if snapshot.get("record_count") != len(records):
        raise ValueError("M3 snapshot record count does not match reconstructed records")
    source_counts = {
        source_class: sum(item["source_class"] == source_class for item in records)
        for source_class in ("frozen_parent", "frozen_support")
    }
    if snapshot.get("source_class_counts") != source_counts:
        raise ValueError("M3 snapshot source classes do not match reconstructed records")
    if any(item["publication_state"] != "published_initial" for item in records):
        raise ValueError("M3 reconstructed records contain an invalid publication state")
    if len(records) != len({item["record_id"] for item in records}):
        raise ValueError("M3 reconstructed records contain duplicate record IDs")
    return logical_hash, source_counts


def verify_published_m3(
    campaign_dir: Path, m2_stage: Path, stage_dir: Path,
) -> dict[str, Any]:
    manifest = read_json(campaign_dir / "M0/campaign-manifest.json")
    m2_gate_path = m2_stage / "m2-gate.json"
    m2_gate = verify_m2_artifacts(campaign_dir, m2_stage)
    gate = read_json(stage_dir / "m3-gate.json")
    snapshot = read_json(stage_dir / "premise-snapshot.json")
    audit = read_json(stage_dir / "audit.json")
    dump = read_json(stage_dir / "fact-dump-result.json")
    database = stage_dir / "premises.sqlite3"
    facts = stage_dir / "raw-export/facts.tsv"
    pools = stage_dir / "raw-export/automation-pools.tsv"

    expected_campaign = manifest["campaign_id"]
    if any(item.get("campaign_id") != expected_campaign for item in (m2_gate, gate, snapshot, audit)):
        raise ValueError("M2/M3 campaign identity mismatch")
    if m2_gate.get("verdict") != "pass" or gate.get("verdict") != "pass":
        raise ValueError("M3 replay requires PASS M2 and M3 gates")
    if audit.get("verdict") != "pass" or not all(audit.get("checks", {}).values()):
        raise ValueError("M3 publication audit is not PASS")
    if snapshot.get("m2_gate_sha256") != sha256_path(m2_gate_path):
        raise ValueError("M2 gate identity drifted after M3 publication")
    if snapshot.get("fact_dump_result_sha256") != sha256_path(stage_dir / "fact-dump-result.json"):
        raise ValueError("M3 fact-dump evidence identity drifted")
    if dump.get("verdict") != "pass":
        raise ValueError("M3 fact dump is not PASS")
    if not facts.is_file() or dump.get("facts_sha256") != sha256_path(facts):
        raise ValueError("M3 facts export identity drifted")
    if not pools.is_file() or dump.get("automation_pools_sha256") != sha256_path(pools):
        raise ValueError("M3 automation-pool export identity drifted")
    if not database.is_file() or snapshot.get("database_sha256") != sha256_path(database):
        raise ValueError("M3 premise database identity drifted")

    workspace_binding = m2_gate["artifact_bindings"]["preflight_workspace_manifest"]
    preflight_manifest = read_json(campaign_dir / workspace_binding["path"])
    reconstructed_records = parse_fact_records(
        facts,
        pools,
        campaign_id=expected_campaign,
        m2_gate_sha256=sha256_path(m2_gate_path),
        support_owner_names=initial_support_owner_names(preflight_manifest),
    )
    _, source_counts = verify_records_snapshot(snapshot, reconstructed_records)
    database_records = read_database_records(database)
    if database_records != reconstructed_records:
        raise ValueError("M3 premise database does not match records reconstructed from raw exports")
    record_count = len(reconstructed_records)
    if record_count != gate.get("record_count"):
        raise ValueError("M3 premise record count drifted")
    if audit.get("source_class_counts") != source_counts:
        raise ValueError("M3 audit source classes do not match reconstructed records")
    if gate.get("snapshot_id") != snapshot.get("snapshot_id"):
        raise ValueError("M3 snapshot identity mismatch")
    if gate.get("database_sha256") != snapshot.get("database_sha256"):
        raise ValueError("M3 gate database identity does not match snapshot")
    return {**gate, "replay": "unchanged"}


def publish_m3(
    repo: Path, campaign_dir: Path, m2_stage: Path, preflight: Path,
    stage_dir: Path, *, image: str, timeout_seconds: int,
) -> dict[str, Any]:
    manifest = read_json(campaign_dir / "M0/campaign-manifest.json")
    gate_path = m2_stage / "m2-gate.json"
    gate = verify_m2_stage(repo, campaign_dir, m2_stage)
    workspace_binding = gate["artifact_bindings"]["preflight_workspace_manifest"]
    expected_preflight = (campaign_dir / workspace_binding["path"]).parent
    if preflight.resolve() != expected_preflight.resolve():
        raise ValueError(
            "M3 preflight directory is not the workspace bound by the M2 gate"
        )
    if (stage_dir / "m3-gate.json").exists():
        return verify_published_m3(campaign_dir, m2_stage, stage_dir)
    dump = run_fact_dump(
        repo, campaign_dir, m2_stage, preflight, stage_dir,
        image=image, timeout_seconds=timeout_seconds,
    )
    write_immutable_json(stage_dir / "fact-dump-result.json", dump)
    if dump["verdict"] != "pass":
        return {"verdict": "fail", "fact_dump": dump["verdict"]}

    m2_gate_hash = sha256_path(gate_path)
    records = parse_fact_records(
        stage_dir / "raw-export/facts.tsv",
        stage_dir / "raw-export/automation-pools.tsv",
        campaign_id=manifest["campaign_id"], m2_gate_sha256=m2_gate_hash,
        support_owner_names=initial_support_owner_names(
            read_json(preflight / "sanitized-workspace-manifest.json")
        ),
    )
    forbidden_owners = sorted({
        item["provenance"]["validation_owner_theory"] for item in records
        if item["provenance"]["validation_owner_theory"].startswith(("Refine.", "CRefine."))
    })
    duplicate_ids = len(records) != len({item["record_id"] for item in records})
    counts = {
        source_class: sum(item["source_class"] == source_class for item in records)
        for source_class in ("frozen_parent", "frozen_support")
    }
    checks = {
        "m2_gate_pass": True,
        "records_nonempty": bool(records),
        "record_ids_unique": not duplicate_ids,
        "exact_statements_nonempty": all(item["exact_statement"].strip() for item in records),
        "no_classified_human_proof_owner": not forbidden_owners,
        "frozen_parent_records_present": counts["frozen_parent"] > 0,
        "frozen_support_records_present": counts["frozen_support"] > 0,
        "publication_state_initial_only": all(
            item["publication_state"] == "published_initial" for item in records
        ),
    }
    logical_hash = sha256_json(records)
    database = stage_dir / "premises.sqlite3"
    create_database(database, records)
    snapshot = {
        "schema_version": 1,
        "artifact_type": "m3-initial-premise-snapshot",
        "campaign_id": manifest["campaign_id"],
        "stage": "Refine",
        "snapshot_id": f"m3-{logical_hash[:20]}",
        "record_count": len(records),
        "source_class_counts": counts,
        "records_payload_sha256": logical_hash,
        "database_sha256": sha256_path(database),
        "m2_gate_sha256": m2_gate_hash,
        "fact_dump_result_sha256": sha256_path(stage_dir / "fact-dump-result.json"),
        "publication_scope": ["frozen_parent", "frozen_support"],
        "excluded_stores": [
            "classified human Proof theories", "contract shells", "generated Proof theories",
            "current local green nodes", "red/planned/abandoned/future nodes",
        ],
    }
    audit = {
        "schema_version": 1,
        "artifact_type": "m3-premise-publication-audit",
        "campaign_id": manifest["campaign_id"],
        "verdict": "pass" if all(checks.values()) else "fail",
        "checks": checks,
        "forbidden_validation_owners": forbidden_owners,
        "source_class_counts": counts,
    }
    write_immutable_json(stage_dir / "premise-snapshot.json", snapshot)
    write_immutable_json(stage_dir / "audit.json", audit)
    m3_gate = {
        "schema_version": 1,
        "artifact_type": "m3-gate",
        "campaign_id": manifest["campaign_id"],
        "verdict": audit["verdict"],
        "snapshot_id": snapshot["snapshot_id"],
        "record_count": len(records),
        "database_sha256": snapshot["database_sha256"],
    }
    write_immutable_json(stage_dir / "m3-gate.json", m3_gate)
    return m3_gate


def query_database(path: Path, query: str, limit: int) -> list[dict[str, Any]]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    pattern = f"%{query}%"
    rows = connection.execute(
        "SELECT record_id, canonical_name, exact_statement, owner_theory, source_class, attributes_json "
        "FROM premises WHERE canonical_name LIKE ? OR exact_statement LIKE ? "
        "ORDER BY CASE WHEN canonical_name = ? THEN 0 WHEN canonical_name LIKE ? THEN 1 ELSE 2 END, canonical_name "
        "LIMIT ?",
        (pattern, pattern, query, f"%{query}%", limit),
    ).fetchall()
    connection.close()
    return [{**dict(row), "attributes": json.loads(row["attributes_json"])} for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--campaign-dir", type=Path)
    parser.add_argument("--m2-stage", type=Path)
    parser.add_argument("--preflight-dir", type=Path)
    parser.add_argument("--stage-dir", type=Path)
    parser.add_argument("--image", default="sel4-public:tuned")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--query-db", type=Path)
    parser.add_argument("--query")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    repo = args.repo.resolve()
    resolve = lambda path: path if path.is_absolute() else repo / path
    if args.query_db is not None:
        if args.query is None:
            parser.error("--query-db requires --query")
        print(json.dumps(query_database(resolve(args.query_db), args.query, args.limit), indent=2))
        return 0
    required = [args.campaign_dir, args.m2_stage, args.preflight_dir, args.stage_dir]
    if any(item is None for item in required):
        parser.error("publication requires --campaign-dir, --m2-stage, --preflight-dir, and --stage-dir")
    result = publish_m3(
        repo, resolve(args.campaign_dir), resolve(args.m2_stage),
        resolve(args.preflight_dir), resolve(args.stage_dir),
        image=args.image, timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
