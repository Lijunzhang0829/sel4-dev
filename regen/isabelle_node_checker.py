#!/usr/bin/env python3
"""Isabelle authority for statement shells, node proofs, and complete theories."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from regen.refine_m2_isolated import SESSION_CATALOGS
from regen.progress_events import emit_event
from regen.assembly_stabilizer import extract_last_node_marker

FORBIDDEN_PROOF = re.compile(r"\b(?:sorry|oops|axiomatization|admit)\b")
DIAGNOSTIC_LINE = re.compile(r'\(line\s+(\d+)\s+of\s+"[^"]+"\)')

AUDIT_FIELDS = {
    "SIMP_RULE": ("simpset_name", "owner_theory", "name_hint", "derivation_name", "statement"),
    "SIMP_CONG": ("weak", "congruence_name", "owner_theory", "name_hint", "derivation_name", "statement"),
    "SIMP_WEAK_CONG": ("weak", "congruence_name"),
    "SIMP_PROC_LHS": ("simproc_name", "pattern_index", "pattern"),
    "SIMP_LOOPER": ("name",),
    "SIMP_SAFE_SOLVER": ("name",),
    "SIMP_UNSAFE_SOLVER": ("name",),
    "WP_RULE": ("owner_theory", "name_hint", "derivation_name", "statement"),
    "WP_TRIP": ("owner_theory", "name_hint", "derivation_name", "statement"),
    "WP_SPLIT": ("owner_theory", "name_hint", "derivation_name", "statement"),
    "WP_COMB": ("owner_theory", "name_hint", "derivation_name", "statement"),
    "WP_UNSAFE": ("owner_theory", "name_hint", "derivation_name", "statement"),
    "WP_PRE": ("owner_theory", "name_hint", "derivation_name", "statement"),
}


def sha256_json(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def parse_hex_tsv(path: Path) -> list[tuple[str, list[str]]]:
    rows: list[tuple[str, list[str]]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        parts = line.split("\t")
        if not parts or not parts[0]:
            continue
        try:
            fields = [bytes.fromhex(value).decode("utf-8") for value in parts[1:]]
        except (ValueError, UnicodeDecodeError) as error:
            raise ValueError(f"invalid audit TSV at line {line_number}: {path}") from error
        rows.append((parts[0], fields))
    return rows


def parse_automation_snapshot(path: Path, *, node_id: str) -> dict[str, Any]:
    records = []
    for kind, fields in parse_hex_tsv(path):
        names = AUDIT_FIELDS.get(kind)
        if names is None:
            raise ValueError(f"unknown automation audit record: {kind}")
        if len(names) != len(fields):
            raise ValueError(f"invalid {kind} field count in {path}")
        records.append({"kind": kind.lower(), **dict(zip(names, fields))})
    identity = {"schema_version": 1, "records": records}
    snapshot = {**identity, "node_id": node_id}
    snapshot["automation_context_id"] = (
        "automation-" + sha256_json(identity)[:20]
    )
    return snapshot


def parse_proof_dependencies(path: Path, *, node_id: str) -> dict[str, Any]:
    dependencies = []
    has_skip_proof = None
    for kind, fields in parse_hex_tsv(path):
        if kind == "THM_DEP" and len(fields) == 2:
            dependencies.append({
                "canonical_name": fields[0], "fact_index": int(fields[1]),
            })
        elif kind == "HAS_SKIP_PROOF" and len(fields) == 1:
            has_skip_proof = fields[0] == "true"
        else:
            raise ValueError(f"invalid proof dependency record {kind} in {path}")
    if has_skip_proof is None:
        raise ValueError(f"proof dependency audit lacks HAS_SKIP_PROOF: {path}")
    identity = {
        "schema_version": 1,
        "has_skip_proof": has_skip_proof,
        "dependencies": dependencies,
    }
    result = {**identity, "node_id": node_id}
    result["dependency_set_id"] = "dependencies-" + sha256_json(identity)[:20]
    return result


def automation_audit_ml(theorem_name: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_'.]*", theorem_name):
        raise ValueError(f"unsupported theorem name for audit: {theorem_name}")
    return rf'''ML \<open>
local
  val thy = @{{theory}};
  val ctxt = Proof_Context.init_global thy;
  val plain_ctxt = Config.put show_markup false ctxt;
  val target = Proof_Context.get_thm ctxt "{theorem_name}";
  val hex_digits = "0123456789abcdef";
  fun hex_char char =
    let val byte = Char.ord char
    in String.implode
      [String.sub (hex_digits, byte div 16), String.sub (hex_digits, byte mod 16)]
    end;
  fun enc value = String.translate hex_char value;
  fun row stream tag fields =
    TextIO.output (stream, space_implode "\t" (tag :: map enc fields) ^ "\n");
  fun prop_of thm =
    Print_Mode.setmp [] (Syntax.string_of_term plain_ctxt) (Thm.full_prop_of thm);
  fun owner_of thm = Long_Name.qualifier (Thm.raw_derivation_name thm);
  fun thm_row stream tag extras thm =
    row stream tag
      (extras @ [owner_of thm, Thm.get_name_hint thm,
                 Thm.raw_derivation_name thm, prop_of thm]);
  fun bool_text value = if value then "true" else "false";
  fun app_index f =
    let fun apply _ [] = ()
          | apply index (item :: items) =
              (f (index, item); apply (index + 1) items)
    in apply 0 end;
  val context_stream = TextIO.openOut "/audit-output/automation-context.tsv";
  val dependency_stream = TextIO.openOut "/audit-output/proof-dependencies.tsv";
  val {{simps, procs, congs, weak_congs, loopers,
       unsafe_solvers, safe_solvers}} =
    Raw_Simplifier.dest_ss (simpset_of ctxt);
  val wp = WeakestPre.debug_get ctxt;
  val wp_rules = WeakestPre.dest_rules (#rules wp);
  val wp_trips = fst (#trips wp);
  val wp_pre = Named_Theorems.get ctxt @{{named_theorems wp_pre}};
  val dependencies = Thm_Deps.thm_deps thy [target];
in
  val _ = List.app
    (fn (name, thm) => thm_row context_stream "SIMP_RULE" [name] thm) simps;
  val _ = List.app
    (fn ((weak, name), thm) =>
      thm_row context_stream "SIMP_CONG" [bool_text weak, name] thm) congs;
  val _ = List.app
    (fn (weak, name) =>
      row context_stream "SIMP_WEAK_CONG" [bool_text weak, name]) weak_congs;
  val _ = List.app
    (fn (name, patterns) =>
      app_index (fn (index, pattern) =>
        row context_stream "SIMP_PROC_LHS"
          [name, string_of_int index,
           Print_Mode.setmp [] (Syntax.string_of_term plain_ctxt) pattern]) patterns)
    procs;
  val _ = List.app (fn name => row context_stream "SIMP_LOOPER" [name]) loopers;
  val _ = List.app
    (fn name => row context_stream "SIMP_SAFE_SOLVER" [name]) safe_solvers;
  val _ = List.app
    (fn name => row context_stream "SIMP_UNSAFE_SOLVER" [name]) unsafe_solvers;
  val _ = List.app (thm_row context_stream "WP_RULE" []) wp_rules;
  val _ = List.app (thm_row context_stream "WP_TRIP" []) wp_trips;
  val _ = List.app (thm_row context_stream "WP_SPLIT" []) (#splits wp);
  val _ = List.app (thm_row context_stream "WP_COMB" []) (#combs wp);
  val _ = List.app (thm_row context_stream "WP_UNSAFE" []) (#unsafe_rules wp);
  val _ = List.app (thm_row context_stream "WP_PRE" []) wp_pre;
  val _ = TextIO.closeOut context_stream;
  val _ = List.app
    (fn (_, (name, index)) =>
      row dependency_stream "THM_DEP" [name, string_of_int index]) dependencies;
  val _ = row dependency_stream "HAS_SKIP_PROOF"
    [bool_text (Thm_Deps.has_skip_proof [target])];
  val _ = TextIO.closeOut dependency_stream;
end
\<close>'''


def append_before_theory_end(source: str, block: str) -> str:
    head, separator, tail = source.rpartition("\nend\n")
    if not separator:
        raise ValueError("generated theory lacks final end command")
    return head + "\n\n" + block + separator + tail


def wrap_context(context: list[str], declaration: str) -> str:
    if not context:
        return declaration
    return "\n".join([*context, declaration, *(["end"] * len(context))])


def declaration(node: dict[str, Any], proof: str) -> str:
    if FORBIDDEN_PROOF.search(proof):
        raise ValueError("proof contains a forbidden command")
    return wrap_context(node.get("context", []), node["statement"] + "\n" + proof.strip())


def failed_statement_node(
    graph: dict[str, Any], source: str, diagnostic: str,
) -> str | None:
    match = DIAGNOSTIC_LINE.search(diagnostic)
    if not match:
        return None
    failure_line = int(match.group(1))
    by_id = {node["id"]: node for node in graph["nodes"]}
    for node_id in graph["execution_order"]:
        statement = by_id[node_id]["statement"]
        offset = source.find(statement)
        if offset < 0:
            continue
        first = source[:offset].count("\n") + 1
        last = first + statement.count("\n")
        if first <= failure_line <= last:
            return node_id
    return None


def dependency_closure(
    graph: dict[str, Any], node_id: str,
) -> set[str]:
    by_id = {node["id"]: node for node in graph["nodes"]}
    result: set[str] = set()
    pending = list(by_id[node_id]["dependencies"])
    while pending:
        dependency = pending.pop()
        if dependency in result:
            continue
        result.add(dependency)
        pending.extend(by_id[dependency]["dependencies"])
    return result


class IsabelleChecker:
    def __init__(
        self, run_dir: Path, work_dir: Path, *, image: str = "sel4-public:tuned",
        architecture: str = "ARM", timeout: int = 900,
        previous_theories: list[str] | None = None,
        generated_dir: Path | None = None,
        heaps_dir: Path | None = None,
        base_session: str = "Semantic_Base",
        base_import: str = "Semantic_Base.Semantic_Root",
        base_catalog: str = "/m2/semantic-base",
    ) -> None:
        self.run_dir = run_dir.resolve()
        self.work_dir = work_dir.resolve()
        self.image = image
        self.architecture = architecture
        self.timeout = timeout
        self.previous_theories = previous_theories or []
        self.generated_dir = (generated_dir or self.work_dir.parent / "generated").resolve()
        self.base_session = base_session
        self.base_import = base_import
        self.base_catalog = base_catalog
        self.heaps_dir = (
            heaps_dir
            or self.run_dir / "M2" / "isolated-environment" / "run-heaps"
        ).resolve()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.generated_dir.mkdir(parents=True, exist_ok=True)

    def sanitized_workspace(self) -> Path:
        matches = sorted(self.run_dir.glob("M2-preflight*/sanitized-workspace"))
        if len(matches) != 1:
            raise ValueError(
                f"expected one M2 sanitized workspace, found {len(matches)}"
            )
        return matches[0]

    def imports(self) -> list[str]:
        if self.previous_theories:
            return [f"Generated_Refine.{self.previous_theories[-1]}"]
        return [self.base_import]

    def _source(
        self, graph: dict[str, Any], proofs: dict[str, str], *,
        theory_name: str, target: str | None = None, statements_only: bool = False,
        diagnostic_markers: bool = False,
    ) -> str:
        by_id = {node["id"]: node for node in graph["nodes"]}
        included = (
            dependency_closure(graph, target) | {target}
            if target is not None else None
        )
        blocks = []
        for node_id in graph["execution_order"]:
            node = by_id[node_id]
            if statements_only:
                blocks.append(wrap_context(
                    node.get("context", []), node["statement"] + "\n  oops"
                ))
                continue
            if included is not None and node_id not in included:
                continue
            if included is None and node_id not in proofs:
                continue
            proof = proofs.get(node_id)
            if proof is None:
                raise ValueError(f"target proof is missing: {node_id}")
            if diagnostic_markers:
                marker = node_id.replace("\\", "\\\\").replace(chr(34), "\\" + chr(34))
                blocks.append(f'ML \\<open>writeln "REGEN_NODE {marker}"\\<close>')
            blocks.append(declaration(node, proof))
        return "\n".join([
            f"theory {theory_name}",
            "  imports",
            *(f'    "{item}"' for item in self.imports()),
            "begin",
            "",
            "unbundle l4v_word_context",
            "",
            *blocks,
            "",
            "end",
            "",
        ])

    def _command(
        self, source: Path, *, session: str,
        writable_mounts: list[tuple[Path, str]] | None = None,
    ) -> list[str]:
        l4v = self.run_dir / "M2" / "isolated-environment" / "l4v"
        isabelle = Path("/home/lijun/seL4-docker-main/verification/isabelle")
        workspace = self.sanitized_workspace()
        heaps = self.heaps_dir
        command = [
            "docker", "run", "--rm", "--network", "none",
            "--mount", f"type=bind,src={l4v},dst=/sel4-project/verification/l4v,readonly",
            "--mount", f"type=bind,src={isabelle},dst=/workspace/verification/isabelle,readonly",
            "--mount", f"type=bind,src={workspace},dst=/m2,readonly",
            "--mount", f"type=bind,src={heaps},dst=/root/.isabelle/heaps",
            "--mount", f"type=bind,src={self.work_dir},dst=/candidate,readonly",
            "--mount", f"type=bind,src={self.generated_dir},dst=/generated,readonly",
            "-e", f"L4V_ARCH={self.architecture}", self.image,
            "/workspace/verification/isabelle/bin/isabelle", "process",
            "-l", session,
        ]
        if writable_mounts:
            insertion = command.index("-e")
            mount_args = []
            for host, container in writable_mounts:
                mount_args.extend([
                    "--mount", f"type=bind,src={host.resolve()},dst={container}",
                ])
            command[insertion:insertion] = mount_args
        for item in SESSION_CATALOGS:
            command.extend(["-d", f"/sel4-project/verification/l4v/{item}"])
        command.extend(["-d", self.base_catalog])
        if (self.generated_dir / "ROOT").is_file():
            command.extend(["-d", "/generated"])
        command.extend(["-T", f"/candidate/{source.stem}"])
        return command

    def _check(
        self, source_text: str, label: str, *,
        writable_mounts: list[tuple[Path, str]] | None = None,
    ) -> dict[str, Any]:
        source = self.work_dir / f"{label}.thy"
        source.write_text(source_text, encoding="utf-8")
        session = "Generated_Refine" if self.previous_theories else self.base_session
        started = time.time()
        container_name = f"regen-isabelle-check-{os.getpid()}"
        command = self._command(
            source, session=session, writable_mounts=writable_mounts,
        )
        command[2:2] = ["--name", container_name]
        emit_event(
            "isabelle_started", label=label, session=session,
            timeout_seconds=self.timeout, container=container_name,
        )
        finished = threading.Event()

        def heartbeat() -> None:
            while not finished.wait(30):
                emit_event(
                    "isabelle_heartbeat", label=label, session=session,
                    elapsed_seconds=int(time.time() - started),
                    container=container_name,
                )

        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        heartbeat_thread.start()
        try:
            result = subprocess.run(
                command, text=True, capture_output=True, timeout=self.timeout,
            )
            returncode = result.returncode
            full_diagnostic = result.stdout + result.stderr
        except subprocess.TimeoutExpired as error:
            returncode = 124
            def timeout_text(value: str | bytes | None) -> str:
                if isinstance(value, bytes):
                    return value.decode("utf-8", errors="replace")
                return value or ""

            full_diagnostic = (
                timeout_text(error.stdout) + timeout_text(error.stderr)
                + f"\nIsabelle checker timed out after {self.timeout} seconds.\n"
            )
            try:
                subprocess.run(
                    ["docker", "rm", "-f", container_name],
                    text=True, capture_output=True, timeout=30,
                )
            except (OSError, subprocess.SubprocessError):
                pass
        finally:
            finished.set()
            heartbeat_thread.join(timeout=1)
        diagnostic = full_diagnostic[-12000:]
        last_marker, marker_count = extract_last_node_marker(full_diagnostic)
        log = self.work_dir / f"{label}.log"
        log.write_text(full_diagnostic, encoding="utf-8")
        check = {
            "verdict": "green" if returncode == 0 else "red",
            "returncode": returncode,
            "diagnostic": diagnostic,
            "source": str(source),
            "log": str(log),
            "wall_ms": int((time.time() - started) * 1000),
            "last_diagnostic_marker": last_marker,
            "diagnostic_marker_count": marker_count,
        }
        emit_event(
            "isabelle_finished", label=label, session=session,
            verdict=check["verdict"], returncode=returncode,
            wall_ms=check["wall_ms"], log=str(log),
        )
        return check

    def check_graph(self, graph: dict[str, Any]) -> dict[str, Any]:
        name = f"Graph_Check_v{graph['revision']}"
        source = self._source(
            graph, {}, theory_name=name, statements_only=True,
        )
        result = self._check(source, name)
        result["failed_node_id"] = failed_statement_node(
            graph, source, result["diagnostic"],
        )
        return result

    def check_node(
        self, graph: dict[str, Any], proofs: dict[str, str], node_id: str, *,
        audit: bool = False,
    ) -> dict[str, Any]:
        safe = re.sub(r"[^A-Za-z0-9_]", "_", node_id)[-80:]
        suffix = "_Audit" if audit else ""
        label = f"Node_Check_{safe}{suffix}"
        source = self._source(
            graph, proofs, theory_name=label, target=node_id,
        )
        writable_mounts = None
        audit_dir = None
        if audit:
            by_id = {node["id"]: node for node in graph["nodes"]}
            audit_dir = self.work_dir / "automation-audit" / safe
            audit_dir.mkdir(parents=True, exist_ok=True)
            for name in (
                "automation-context.tsv", "proof-dependencies.tsv",
                "automation-context.json", "proof-dependencies.json",
            ):
                artifact = audit_dir / name
                if artifact.exists():
                    artifact.unlink()
            source = append_before_theory_end(
                source, automation_audit_ml(by_id[node_id]["name"]),
            )
            writable_mounts = [(audit_dir, "/audit-output")]

        result = self._check(
            source, label, writable_mounts=writable_mounts,
        )
        if not audit or result["verdict"] != "green":
            return result
        assert audit_dir is not None
        context_tsv = audit_dir / "automation-context.tsv"
        dependencies_tsv = audit_dir / "proof-dependencies.tsv"
        if not context_tsv.is_file() or not dependencies_tsv.is_file():
            raise ValueError(f"Isabelle audit did not emit expected files: {audit_dir}")
        snapshot = parse_automation_snapshot(context_tsv, node_id=node_id)
        dependencies = parse_proof_dependencies(dependencies_tsv, node_id=node_id)
        context_json = audit_dir / "automation-context.json"
        dependencies_json = audit_dir / "proof-dependencies.json"
        context_json.write_text(
            json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
        dependencies_json.write_text(
            json.dumps(dependencies, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        result["automation_audit"] = {
            "automation_context_id": snapshot["automation_context_id"],
            "dependency_set_id": dependencies["dependency_set_id"],
            "automation_context": str(context_json),
            "proof_dependencies": str(dependencies_json),
            "raw_automation_context": str(context_tsv),
            "raw_proof_dependencies": str(dependencies_tsv),
        }
        return result

    def complete_source(self, graph: dict[str, Any], proofs: dict[str, str]) -> str:
        stem = graph["file_slot"].removesuffix(".thy")
        return self._source(graph, proofs, theory_name=stem)

    def check_complete(
        self, graph: dict[str, Any], proofs: dict[str, str],
    ) -> dict[str, Any]:
        stem = graph["file_slot"].removesuffix(".thy")
        return self._check(
            self._source(
                graph, proofs, theory_name=f"Complete_{stem}",
                diagnostic_markers=True,
            ),
            f"Complete_{stem}",
        )

    def build_generated_session(self, theories: list[str]) -> dict[str, Any]:
        root = self.generated_dir / "ROOT"
        root.write_text("\n".join([
            f"session Generated_Refine = {self.base_session} +",
            "  sessions",
            "    Lib",
            "    CorresK",
            "    AInvs",
            "  theories",
            *(f"    {name}" for name in theories),
            "",
        ]), encoding="utf-8")
        l4v = self.run_dir / "M2" / "isolated-environment" / "l4v"
        isabelle = Path("/home/lijun/seL4-docker-main/verification/isabelle")
        workspace = self.sanitized_workspace()
        heaps = self.heaps_dir
        command = [
            "docker", "run", "--rm", "--network", "none",
            "--mount", f"type=bind,src={l4v},dst=/sel4-project/verification/l4v,readonly",
            "--mount", f"type=bind,src={isabelle},dst=/workspace/verification/isabelle,readonly",
            "--mount", f"type=bind,src={workspace},dst=/m2,readonly",
            "--mount", f"type=bind,src={heaps},dst=/root/.isabelle/heaps",
            "--mount", f"type=bind,src={self.generated_dir},dst=/generated,readonly",
            "-e", f"L4V_ARCH={self.architecture}", self.image,
            "/workspace/verification/isabelle/bin/isabelle", "build", "-b",
        ]
        for item in SESSION_CATALOGS:
            command.extend(["-d", f"/sel4-project/verification/l4v/{item}"])
        command.extend([
            "-d", self.base_catalog, "-d", "/generated", "Generated_Refine",
        ])
        started = time.time()
        container_name = f"regen-isabelle-build-{os.getpid()}"
        command[2:2] = ["--name", container_name]
        emit_event(
            "isabelle_build_started", session="Generated_Refine",
            timeout_seconds=self.timeout, container=container_name,
        )
        finished = threading.Event()

        def heartbeat() -> None:
            while not finished.wait(30):
                emit_event(
                    "isabelle_build_heartbeat", session="Generated_Refine",
                    elapsed_seconds=int(time.time() - started),
                    container=container_name,
                )

        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        heartbeat_thread.start()
        try:
            result = subprocess.run(
                command, text=True, capture_output=True, timeout=self.timeout,
            )
            returncode = result.returncode
            diagnostic = (result.stdout + result.stderr)[-12000:]
        except subprocess.TimeoutExpired as error:
            returncode = 124

            def timeout_text(value: str | bytes | None) -> str:
                if isinstance(value, bytes):
                    return value.decode("utf-8", errors="replace")
                return value or ""

            diagnostic = (
                timeout_text(error.stdout) + timeout_text(error.stderr)
                + f"\nIsabelle build timed out after {self.timeout} seconds.\n"
            )[-12000:]
            try:
                subprocess.run(
                    ["docker", "rm", "-f", container_name],
                    text=True, capture_output=True, timeout=30,
                )
            except (OSError, subprocess.SubprocessError):
                pass
        finally:
            finished.set()
            heartbeat_thread.join(timeout=1)
        log = self.generated_dir / "build.log"
        log.write_text(diagnostic, encoding="utf-8")
        check = {
            "verdict": "green" if returncode == 0 else "red",
            "returncode": returncode,
            "diagnostic": diagnostic,
            "log": str(log),
            "wall_ms": int((time.time() - started) * 1000),
        }
        emit_event(
            "isabelle_build_finished", session="Generated_Refine",
            verdict=check["verdict"], returncode=returncode,
            wall_ms=check["wall_ms"], log=str(log),
        )
        return check
