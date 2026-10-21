#!/usr/bin/env python3
"""Isabelle authority for declarations, proof batches, and complete theories.

Legacy single-node and graph entry points remain for historical experiment replay.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from regen.preparation.semantic_base_builder import SESSION_CATALOGS
from regen.infrastructure.progress_events import emit_event
from regen.execution.checker_policy import (
    ISOLATED_CONTEXT, ARCH_REUSE_CONTEXT, validate_complete_context_policy,
)
from regen.execution.assembly_stabilizer import extract_last_node_marker

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


def automation_audit_ml(
    theorem_name: str, *, dependency_filename: str = "proof-dependencies.tsv",
) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_'.]*", theorem_name):
        raise ValueError(f"unsupported theorem name for audit: {theorem_name}")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", dependency_filename):
        raise ValueError(f"unsupported dependency audit filename: {dependency_filename}")
    return rf'''ML \<open>
local
  val thy = @{{theory}};
  val ctxt = Proof_Context.init_global thy;
  val plain_ctxt = Config.put show_markup false ctxt;
  val targets = Proof_Context.get_thms ctxt "{theorem_name}";
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
  val dependency_stream = TextIO.openOut "/audit-output/{dependency_filename}";
  val {{simps, procs, congs, weak_congs, loopers,
       unsafe_solvers, safe_solvers}} =
    Raw_Simplifier.dest_ss (simpset_of ctxt);
  val wp = WeakestPre.debug_get ctxt;
  val wp_rules = WeakestPre.dest_rules (#rules wp);
  val wp_trips = fst (#trips wp);
  val wp_pre = Named_Theorems.get ctxt @{{named_theorems wp_pre}};
  val _ = if null targets then error "Audit target has no theorems" else ();
  val _ = Thm.consolidate targets;
  val dependencies = Thm_Deps.thm_deps thy targets;
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
    [bool_text (Thm_Deps.has_skip_proof targets)];
  val _ = TextIO.closeOut dependency_stream;
end
\<close>'''


def proof_dependency_audit_ml(
    theorem_name: str, *, dependency_filename: str,
) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_'.]*", theorem_name):
        raise ValueError(f"unsupported theorem name for audit: {theorem_name}")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", dependency_filename):
        raise ValueError(f"unsupported dependency audit filename: {dependency_filename}")
    return rf'''ML \<open>
local
  val thy = @{{theory}};
  val ctxt = Proof_Context.init_global thy;
  val targets = Proof_Context.get_thms ctxt "{theorem_name}";
  val hex_digits = "0123456789abcdef";
  fun hex_char char =
    let val byte = Char.ord char
    in String.implode
      [String.sub (hex_digits, byte div 16), String.sub (hex_digits, byte mod 16)]
    end;
  fun enc value = String.translate hex_char value;
  fun row stream tag fields =
    TextIO.output (stream, space_implode "\t" (tag :: map enc fields) ^ "\n");
  fun bool_text value = if value then "true" else "false";
  val stream = TextIO.openOut "/audit-output/{dependency_filename}";
  val _ = if null targets then error "Audit target has no theorems" else ();
  val _ = Thm.consolidate targets;
  val dependencies = Thm_Deps.thm_deps thy targets;
in
  val _ = List.app
    (fn (_, (name, index)) =>
      row stream "THM_DEP" [name, string_of_int index]) dependencies;
  val _ = row stream "HAS_SKIP_PROOF"
    [bool_text (Thm_Deps.has_skip_proof targets)];
  val _ = TextIO.closeOut stream;
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


def failed_statement_declaration(
    index: dict[str, Any], source: str, diagnostic: str,
) -> str | None:
    match = DIAGNOSTIC_LINE.search(diagnostic)
    if not match:
        return None
    failure_line = int(match.group(1))
    by_id = {item["id"]: item for item in index["nodes"]}
    for declaration_id in index["execution_order"]:
        statement = by_id[declaration_id]["statement"]
        offset = source.find(statement)
        if offset < 0:
            continue
        first = source[:offset].count("\n") + 1
        last = first + statement.count("\n")
        if first <= failure_line <= last:
            return declaration_id
    return None


def failed_statement_node(
    graph: dict[str, Any], source: str, diagnostic: str,
) -> str | None:
    """Historical name for declaration error attribution."""
    return failed_statement_declaration(graph, source, diagnostic)


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


from regen.execution.check_executor import native_operation, native_run, CURRENT_RESOURCES


class IsabelleChecker:
    def __init__(
        self, run_dir: Path, work_dir: Path, *, image: str = "sel4-public:tuned",
        architecture: str = "ARM", timeout: int = 900,
        previous_theories: list[str] | None = None,
        generated_dir: Path | None = None,
        heaps_dir: Path | None = None,
        l4v_dir: Path | None = None,
        semantic_workspace: Path | None = None,
        base_session: str = "Semantic_Base",
        base_import: str = "Semantic_Base.Semantic_Root",
        base_catalog: str = "/semantic-base",
        isabelle_dir: Path | None = None,
        import_all_previous: bool = False,
        complete_context_policy: str = ISOLATED_CONTEXT,
    ) -> None:
        self.complete_context_policy = validate_complete_context_policy(complete_context_policy)
        self._last_node_check = None
        self.persistent = False
        self.batch_incremental = True
        self._batch_worker = None
        self._prefix_worker = None
        self._prefix_key = None
        self._persistent_disabled = False
        self.run_dir = run_dir.resolve()
        self.work_dir = work_dir.resolve()
        self.isabelle_dir = (isabelle_dir or Path("/home/lijun/seL4-docker-main/verification/isabelle")).resolve()
        self.image = image
        self.architecture = architecture
        self.timeout = timeout
        self.previous_theories = previous_theories or []
        self.import_all_previous = import_all_previous
        self.generated_dir = (generated_dir or self.work_dir.parent / "generated").resolve()
        self.base_session = base_session
        self.base_import = base_import
        self.base_catalog = base_catalog
        if heaps_dir is None or l4v_dir is None or semantic_workspace is None:
            raise ValueError(
                "checker requires dataset-bound heaps, l4v and Semantic Base workspace"
            )
        self.heaps_dir = heaps_dir.resolve()
        self.l4v_dir = l4v_dir.resolve()
        self.semantic_workspace = semantic_workspace.resolve()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.generated_dir.mkdir(parents=True, exist_ok=True)

    def imports(self) -> list[str]:
        if self.previous_theories:
            if self.import_all_previous:
                return [f"Generated_Refine.{name}" for name in self.previous_theories]
            return [f"Generated_Refine.{self.previous_theories[-1]}"]
        return [self.base_import]

    def _source(
        self, graph: dict[str, Any], proofs: dict[str, str], *,
        theory_name: str, target: str | None = None, statements_only: bool = False,
        diagnostic_markers: bool = False, phase_timing: bool = False, arch_timing: bool = False,
        reuse_arch_context: bool = False, reuse_declaration_context: bool = False,
    ) -> str:
        if reuse_arch_context and (phase_timing or statements_only):
            raise ValueError('Arch reuse is an isolated proof-check experiment only')
        if reuse_declaration_context and not statements_only:
            raise ValueError('Declaration context reuse requires statements_only')
        if arch_timing and not phase_timing:
            raise ValueError('Arch profiling requires phase_timing')
        by_id = {node["id"]: node for node in graph["nodes"]}
        included = (
            dependency_closure(graph, target) | {target}
            if target is not None else None
        )
        order = graph["execution_order"]
        if graph.get('context_policy') == 'committed-prefix-v1':
            from regen.execution.fact_context import visible_ids
            if target is not None:
                order = [*visible_ids(graph, proofs, target), target]
                included = set(order)
            else:
                prefix = graph.get('accepted_order', [])
                order = [*prefix, *(key for key in order if key not in prefix)]
        blocks = []
        arch_open = False
        arch_prefix_names = []
        if phase_timing:
            blocks.append('ML \\<open>if ! Multithreading.parallel_proofs = 0 then () '
                          'else error "phase timing requires parallel_proofs=0"\\<close>')
        for node_id in order:
            node = by_id[node_id]
            if statements_only:
                # Declaration probes abort every goal: they must never make an
                # unproved fact available to subsequent goals. Reuse only the
                # known Arch interpretation, and keep each statement in its own
                # anonymous scope. Context changes (including nested or merely
                # similar Arch contexts) are barriers. This is independent of
                # the complete-theory proof/export policy.
                reuse_node = (reuse_declaration_context
                    and node.get('context') == ['context begin interpretation Arch .'])
                if arch_open and not reuse_node:
                    blocks.append('end')
                    arch_open = False
                if reuse_node and not arch_open:
                    blocks.append('context begin interpretation Arch .')
                    arch_open = True
                context = ['context begin'] if reuse_node else node.get('context', [])
                blocks.append(wrap_context(context, node["statement"] + "\n  oops"))
                continue
            if included is not None and node_id not in included:
                continue
            if included is None and node_id not in proofs:
                continue
            proof = proofs.get(node_id)
            if proof is None:
                raise ValueError(f"target proof is missing: {node_id}")
            # Experimental, opt-in only. Attribute-bearing declarations are
            # barriers: reinterpreting Arch can affect rule insertion order.
            reuse_node = (reuse_arch_context
                and node.get('context') == ['context begin interpretation Arch .']
                and re.fullmatch(r'[A-Za-z][A-Za-z0-9_\']*', node.get('name', '')) is not None
                and re.match(r'\s*lemma\s+' + re.escape(node.get('name', '')) + r'\s*:',
                             node['statement']) is not None)
            if arch_open and not reuse_node:
                blocks.append('end')
                arch_open = False
                arch_prefix_names = []
            if reuse_node and not arch_open:
                blocks.append('context begin interpretation Arch .')
                arch_open = True
            if reuse_node and arch_prefix_names:
                # Nested targets retain unnamed local copies of prior results.
                # Original independent scopes instead see exported global
                # facts. Restore that binding (and indexed global facts), not
                # merely equal propositions, for proof dependency provenance.
                names = ', '.join(json.dumps(n) for n in arch_prefix_names)
                blocks.append('local_setup \\<open>fn lthy => Local_Theory.map_contexts '
                    '(fn level => fn ctxt => if level = 0 then ctxt else ctxt |> '
                    'fold (fn name => Proof_Context.put_thms false (name, NONE)) [' + names + '] '
                    '|> Proof_Context.transfer_facts (Proof_Context.theory_of lthy)) lthy\\<close>')
            if diagnostic_markers:
                marker = node_id.replace("\\", "\\\\").replace(chr(34), "\\" + chr(34))
                blocks.append(f'ML \\<open>writeln "REGEN_NODE {marker}"\\<close>')
            timing_id = node_id.replace("\\", "\\\\").replace(chr(34), "\\" + chr(34))
            blocks.append(f'ML \\<open>writeln ("REGEN_TIMING_START {timing_id} " ^ Time.toString (Time.now ()))\\<close>')
            if phase_timing:
                from regen.execution.node_phase_timing import timed_declaration
                declaration(node, proof)  # Retain the ordinary forbidden-proof guard.
                blocks.append(timed_declaration(node, proof, split_arch=arch_timing))
            else:
                rendered_node = dict(node, context=['context begin']) if reuse_node else node
                blocks.append(declaration(rendered_node, proof))
            blocks.append(f'ML \\<open>writeln ("REGEN_TIMING_END {timing_id} " ^ Time.toString (Time.now ()))\\<close>')
            if reuse_node:
                arch_prefix_names.append(node['name'])
        if arch_open:
            blocks.append('end')
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
        l4v = self.l4v_dir
        isabelle = self.isabelle_dir
        workspace = self.semantic_workspace
        heaps = self.heaps_dir
        command = [
            "docker", "run", "--rm", "--network", "none",
            "--mount", f"type=bind,src={l4v},dst=/sel4-project/verification/l4v,readonly",
            "--mount", f"type=bind,src={isabelle},dst=/workspace/verification/isabelle,readonly",
            "--mount", f"type=bind,src={workspace},dst=/semantic-base,readonly",
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
        resources = CURRENT_RESOURCES.get()
        if resources is not None:
            at = command.index("process") + 1
            command[at:at] = ["-o", "threads=" + str(resources.policy["threads"])]
            # Hot workers and cold process checks need the same ML headroom as
            # builds. A Docker limit alone does not constrain Poly/ML's chosen
            # heap; without this, a retained worker can grow close to cgroup OOM.
            # Apply after Isabelle settings, just like the build path below.
            heap = resources.policy['build_ml_heap_mb']
            ml_options = (f'-H {min(1024, heap)} --maxheap {heap} --stackspace 64 '
                          f'--gcthreads {resources.policy["threads"]}')
            executable = '/workspace/verification/isabelle/bin/isabelle'
            at = command.index(executable) + 1
            command[at:at] = ['env', f'ML_OPTIONS={ml_options}', executable]
        from regen.execution.native_container import docker_limits
        command[2:2] = docker_limits()
        command.extend(["-T", f"/candidate/{source.stem}"])
        return command

    @native_operation
    def _check(
        self, source_text: str, label: str, *,
        writable_mounts: list[tuple[Path, str]] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if CURRENT_RESOURCES.get() is not None:
            self.close_prefix()  # A cold process replaces, never accompanies, this resident.
        timeout = self.timeout if timeout is None else timeout
        source = self.work_dir / f"{label}.thy"
        source.write_text(source_text, encoding="utf-8")
        # Both mounts can affect a cold process check. In particular, a VERO
        # target may import another generated theory from /generated even when
        # its own bytes at /candidate are unchanged.
        mounted_sources = {'candidate/' + source.name: source.read_bytes()}
        mounted_sources.update({'generated/' + path.name: path.read_bytes()
            for path in sorted(self.generated_dir.glob('*.thy'))})
        generated_root = self.generated_dir / 'ROOT'
        if generated_root.is_file():
            mounted_sources['generated/ROOT'] = generated_root.read_bytes()
        session = "Generated_Refine" if self.previous_theories else self.base_session
        started = time.time()
        container_name = f"regen-isabelle-check-{os.getpid()}-{uuid.uuid4().hex}"
        command = self._command(
            source, session=session, writable_mounts=writable_mounts,
        )
        command[2:2] = ["--name", container_name]
        emit_event(
            "isabelle_started", label=label, session=session,
            timeout_seconds=timeout, container=container_name,
        )
        try:
            result = native_run(
                command, text=True, capture_output=True, timeout=timeout,
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
                + f"\nIsabelle checker timed out after {timeout} seconds.\n"
            )
            try:
                subprocess.run(
                    ["docker", "rm", "-f", container_name],
                    text=True, capture_output=True, timeout=30,
                )
            except (OSError, subprocess.SubprocessError):
                pass
        diagnostic = full_diagnostic[-12000:]
        last_marker, marker_count = extract_last_node_marker(full_diagnostic)
        log = self.work_dir / f"{label}.log"
        log.write_text(full_diagnostic, encoding="utf-8")
        phase_starts = {}
        node_wall_ms = {}
        for kind, node, stamp in re.findall(
            r"^REGEN_TIMING_(START|END) (\S+) ([0-9.]+)$", full_diagnostic, re.M
        ):
            if kind == 'START':
                phase_starts[node] = float(stamp)
            elif node in phase_starts:
                node_wall_ms[node] = round((float(stamp) - phase_starts[node]) * 1000)
        check = {
            "node_wall_ms": node_wall_ms,
            "verdict": "green" if returncode == 0 else "red",
            "returncode": returncode,
            "diagnostic": diagnostic,
            "source": str(source),
            "log": str(log),
            "wall_ms": int((time.time() - started) * 1000),
            "last_diagnostic_marker": last_marker,
            "diagnostic_marker_count": marker_count,
            "timeout_seconds": timeout,
            "timeout_scope": "cold-full-theory" if returncode == 124 else None,
            "check_scope": "cold-full-theory",
            "incremental": False,
        }
        from regen.execution.native_check_receipt import native_check_receipt
        check['native_receipt'] = native_check_receipt(
            self, check, source=mounted_sources, scope='cold-full-theory',
            log_text=full_diagnostic, candidate_execution_ms=check['wall_ms'])
        if 'REGEN_PHASE ' in full_diagnostic or 'REGEN_ARCH_PHASE ' in full_diagnostic:
            from regen.execution.node_phase_timing import parse_phase_timings
            check['node_phase_timing'] = parse_phase_timings(full_diagnostic)
        emit_event(
            "isabelle_finished", label=label, session=session,
            verdict=check["verdict"], returncode=returncode,
            wall_ms=check["wall_ms"], log=str(log),
        )
        return check

    def _check_observation(self, source_text, label, *, writable_mounts=None, timeout=None):
        owner = getattr(self, '_observation_owner', None)
        if owner is not None:
            # Observation tasks keep independent paths/caches. Borrow the owning
            # theory's native lease, not another resident slot; serialize against
            # proof actions that may change its work_dir or accepted prefix.
            with self._observation_lock:
                worker = getattr(owner, '_batch_worker', None)
                if (worker is not None and not worker.closed and worker.process.poll() is None
                        and worker.imports == self.imports() == owner.imports()):
                    return owner._check_observation_native(source_text, label,
                        writable_mounts=writable_mounts, timeout=timeout)
        return self._check_observation_native(source_text, label,
            writable_mounts=writable_mounts, timeout=timeout)

    @native_operation
    def _check_observation_native(self, source_text, label, *, writable_mounts=None, timeout=None):
        """Reuse only the process, never its accepted or speculative context."""
        worker = getattr(self, '_batch_worker', None)
        mounts = writable_mounts or []
        supported = not mounts or (len(mounts) == 1 and mounts[0][1] == '/symbol-output')
        if (supported and worker is not None and not worker.closed
                and worker.process.poll() is None and worker.imports == self.imports()):
            emit_event('isabelle_observation_started', worker=worker.name, label=label)
            try:
                result = worker.observe_source(source_text, label,
                    timeout=self.timeout if timeout is None else timeout,
                    output_directory=mounts[0][0] if mounts else None)
            except BaseException as error:
                emit_event('isabelle_observation_failed', worker=worker.name, label=label,
                           error_type=type(error).__name__)
                raise
            emit_event('isabelle_observation_finished', worker=worker.name, label=label,
                       verdict=result['verdict'], wall_ms=result['wall_ms'])
            return result
        return self._check(source_text, label, writable_mounts=writable_mounts, timeout=timeout)

    @native_operation
    def check_sketch_source(self, source, prefix_source, admitted_names, *, timeout=None):
        """Native conditional diagnostic; never certify or commit this source."""
        from regen.execution.batch_checker import BatchWorker, body
        worker = getattr(self, '_batch_worker', None)
        if getattr(self, 'batch_incremental', False):
            if (worker is None or worker.closed or worker.process.poll() is not None
                    or worker.imports != self.imports()
                    or not worker.can_align(body(prefix_source))):
                if worker is not None:
                    worker.close()
                self._batch_worker = None
                # Recover names from the trusted caller's exact prefix snapshot.
                names = getattr(self, '_sketch_prefix_names', [])
                worker = BatchWorker(self, prefix_source, names, self.imports())
                self._batch_worker = worker
            worker.align(body(prefix_source))
            try:
                return worker.check_sketch(source, admitted_names, timeout=timeout)
            except (TimeoutError, RuntimeError, OSError):
                worker.close()
                self._batch_worker = None
                raise
        label = prefix_source.splitlines()[0].split()[1]
        result = self._check(source, label, timeout=timeout if timeout is not None else getattr(self, 'batch_candidate_timeout', 120))
        result.update(check_scope='isolated-conditional-sketch', publishable=False,
                      incremental=False)
        return result

    def close_prefix(self):
        errors = []
        for attribute in ("_batch_worker", "_prefix_worker"):
            worker = getattr(self, attribute, None)
            try:
                if worker is not None:
                    worker.close()
            except BaseException as error:
                # Keep the failed worker reachable for a later cleanup retry.
                errors.append(error)
            else:
                setattr(self, attribute, None)
                if attribute == "_prefix_worker":
                    self._prefix_key = None
        if errors:
            for error in errors[1:]:
                errors[0].add_note(f"Additional worker cleanup failure: {type(error).__name__}: {error}")
            raise errors[0]

    def resolve_symbols(self, names, context=()):
        from regen.execution.symbol_resolution import resolve_symbols
        return resolve_symbols(self,names,list(context))

    def _check_declaration_source(self, index, *, name, scope, failure_key,
                                  reuse_declaration_context=False):
        from regen.infrastructure.progress_events import generation_exclusion
        with generation_exclusion('statement_preflight'):
            return self._check_declaration_source_timed(index, name=name, scope=scope,
                failure_key=failure_key, reuse_declaration_context=reuse_declaration_context)

    def _check_declaration_source_timed(self, index, *, name, scope, failure_key,
                                      reuse_declaration_context=False):
        self._last_node_check = None
        source = self._source(index, {}, theory_name=name, statements_only=True,
                              reuse_declaration_context=reuse_declaration_context)
        from regen.execution.declaration_cache import checked_declarations
        result = checked_declarations(self, source, name)
        result["check_scope"] = scope
        result["incremental"] = False
        result["proofs_checked"] = False
        result["declaration_context_policy"] = ("arch-declarations-reuse-v1"
            if reuse_declaration_context else "isolated-v1")
        result[failure_key] = failed_statement_declaration(
            index, source, result["diagnostic"],
        )
        return result

    def check_declarations(self, index: dict[str, Any]) -> dict[str, Any]:
        """Check document declarations without constructing a proof dependency graph."""
        return self._check_declaration_source(
            index, name=f"Declaration_Check_v{index['revision']}",
            scope="all-document-declarations", failure_key="failed_declaration_id",
            reuse_declaration_context=True,
        )

    def check_graph(self, graph: dict[str, Any]) -> dict[str, Any]:
        """Legacy entry point preserving historical source and evidence labels."""
        return self._check_declaration_source(
            graph, name=f"Graph_Check_v{graph['revision']}",
            scope="all-graph-statements", failure_key="failed_node_id",
        )

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

        # One-shot reuse for the Runner's immediate acceptance recheck.  It is
        # confined to this checker (one frozen environment), exact source and
        # command, and byte-identical source/log artifacts.  Audits always run.
        identity = (source, tuple(self._command(
            self.work_dir / (label + ".thy"),
            session="Generated_Refine" if self.previous_theories else self.base_session,
        )))
        cached = self._last_node_check
        self._last_node_check = None
        if not audit and cached and cached[0] == identity:
            _, prior, source_bytes, log_bytes = cached
            try:
                intact = (Path(prior['source']).read_bytes() == source_bytes
                          and Path(prior['log']).read_bytes() == log_bytes)
            except OSError:
                intact = False
            if intact:
                return {**prior, 'reused_check': True, 'execution_wall_ms': 0,
                        'check_scope':'target-with-committed-prefix',
                        'incremental':'cached-cold-certification'}
        acceleration = None
        if self.persistent and not audit and not self._persistent_disabled:
            from regen.execution.persistent_checker import PrefixWorker
            import time as clock
            node = next(n for n in graph['nodes'] if n['id'] == node_id)
            # Arbitrary ML and file commands can mutate process globals outside
            # the immutable Toplevel state. Such candidates always run cold.
            body = declaration(node, proofs[node_id])
            unsafe = re.search(r"\b(?:ML\w*|SML\w*|setup|local_setup|attribute_setup|method_setup|oracle|axiomatization|sorry|oops|tactic|simproc_setup|use|external_file)\b", body)
            if not unsafe:
                marker = 'ML \\<open>writeln ("REGEN_TIMING_START ' + node_id + ' '
                key = (source[:source.index(marker)], tuple(self.imports()), node_id, node['name'])
                started = clock.monotonic()
                try:
                    if self._prefix_key != key:
                        emit_event('isabelle_prefix_started', node_id=node_id)
                        self.close_prefix()
                        self._prefix_worker = PrefixWorker(self, source, node_id, node['name'])
                        self._prefix_key = key
                        emit_event('isabelle_prefix_ready', node_id=node_id, wall_ms=round((clock.monotonic()-started)*1000))
                    prefix_prepare_ms = round((clock.monotonic()-started)*1000)
                    trial = self._prefix_worker.check(body)
                    trial['prefix_prepare_ms'] = prefix_prepare_ms
                    trial['execution_wall_ms'] = round((clock.monotonic()-started)*1000)
                    acceleration = trial
                    emit_event('isabelle_candidate_finished', node_id=node_id, verdict=trial['verdict'], wall_ms=trial['wall_ms'], execution_wall_ms=trial['execution_wall_ms'], speculative=True)
                    if trial['verdict'] == 'red':
                        # Keep full source with original line structure for repair diagnostics.
                        full = Path(trial['source']).with_suffix('.thy')
                        full.write_text(source)
                        trial['candidate_source'] = trial['source']
                        trial['source'] = str(full)
                        trial['persistent_log'] = str(self._prefix_worker.root/'worker.log')
                        trial['check_scope'] = 'target-with-committed-prefix'
                        trial['incremental'] = 'fixed-prefix-worker'
                        return trial
                except Exception as error:
                    acceleration = {'fallback': str(error), 'execution_wall_ms':round((clock.monotonic()-started)*1000)}
                    self.close_prefix()
                    self._persistent_disabled = True
                    emit_event('isabelle_prefix_fallback', node_id=node_id, reason=str(error))
        result = self._check(
            source, label, writable_mounts=writable_mounts,
        )
        if acceleration is not None:
            result['prefix_check'] = acceleration
            result['execution_wall_ms'] = result['wall_ms'] + acceleration['execution_wall_ms']
        result['check_scope'] = 'target-with-committed-prefix'
        result['incremental'] = (
            'fixed-prefix-worker-plus-cold-certification'
            if acceleration is not None and acceleration.get('verdict') == 'green'
            else False
        )

        if not audit and result['verdict'] == 'green':
            self._last_node_check = (identity, dict(result),
                Path(result['source']).read_bytes(), Path(result['log']).read_bytes())
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

    def complete_audit_source(self, graph, proofs, *, theory_name=None):
        ordered = [next(n for n in graph['nodes'] if n['id'] == key)
                   for key in graph['execution_order']]
        blocks = []
        for index, node in enumerate(ordered):
            token = re.sub(r"[^A-Za-z0-9_.-]", "_", node['id'])[-48:]
            token += '-' + hashlib.sha256(node['id'].encode()).hexdigest()[:10]
            filename = 'proof-dependencies-' + token + '.tsv'
            blocks.append(automation_audit_ml(node['name'], dependency_filename=filename)
                          if index == 0 else proof_dependency_audit_ml(node['name'], dependency_filename=filename))
        label = theory_name or 'Complete_Audit_' + graph['file_slot'].removesuffix('.thy')
        return append_before_theory_end(
            self._complete_theory_source(graph, proofs, theory_name=label), '\n\n'.join(blocks))

    def audit_complete_theory(
        self, graph: dict[str, Any], proofs: dict[str, str], *,
        theory_name: str | None = None,
    ) -> dict[str, Any]:
        ordered = [
            next(node for node in graph["nodes"] if node["id"] == node_id)
            for node_id in graph["execution_order"]
        ]
        if not ordered:
            raise ValueError("complete-theory audit requires at least one node")
        audit_dir = self.work_dir / "complete-theory-audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        for artifact in audit_dir.iterdir():
            if artifact.is_file():
                artifact.unlink()

        filenames = {}
        for node in ordered:
            token = re.sub(r"[^A-Za-z0-9_.-]", "_", node["id"])[-48:]
            token += "-" + hashlib.sha256(node["id"].encode()).hexdigest()[:10]
            filename = f"proof-dependencies-{token}.tsv"
            filenames[node["id"]] = filename

        self._last_node_check = None
        stem = graph["file_slot"].removesuffix(".thy")
        label = theory_name or f"Complete_Audit_{stem}"
        source = self.complete_audit_source(graph, proofs, theory_name=label)
        result = self._check(
            source, label, writable_mounts=[(audit_dir, "/audit-output")],
        )
        if result["verdict"] != "green":
            return result

        context_tsv = audit_dir / "automation-context.tsv"
        if not context_tsv.is_file():
            raise ValueError("complete-theory audit lacks automation context")
        context = parse_automation_snapshot(
            context_tsv, node_id=f"theory:{graph['theory']}",
        )
        context_json = audit_dir / "automation-context.json"
        context_json.write_text(
            json.dumps(context, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        dependencies = {}
        dependency_paths = {}
        for node in ordered:
            raw_path = audit_dir / filenames[node["id"]]
            if not raw_path.is_file():
                raise ValueError(
                    f"complete-theory audit lacks dependencies for {node['id']}"
                )
            parsed = parse_proof_dependencies(raw_path, node_id=node["id"])
            json_path = raw_path.with_suffix(".json")
            json_path.write_text(
                json.dumps(parsed, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            dependencies[node["id"]] = parsed
            dependency_paths[node["id"]] = {
                "json": str(json_path), "raw": str(raw_path),
            }
        result["complete_theory_audit"] = {
            "automation_context": context,
            "proof_dependencies": dependencies,
            "automation_context_path": str(context_json),
            "raw_automation_context_path": str(context_tsv),
            "proof_dependency_paths": dependency_paths,
        }
        return result

    def _complete_theory_source(self, graph, proofs, *, theory_name, diagnostic_markers=False):
        policy = validate_complete_context_policy(
            getattr(self, 'complete_context_policy', ISOLATED_CONTEXT))
        return self._source(graph, proofs, theory_name=theory_name,
            diagnostic_markers=diagnostic_markers,
            reuse_arch_context=policy == ARCH_REUSE_CONTEXT)

    def complete_source(
        self, graph: dict[str, Any], proofs: dict[str, str], *,
        theory_name: str | None = None,
    ) -> str:
        stem = theory_name or graph["file_slot"].removesuffix(".thy")
        return self._complete_theory_source(graph, proofs, theory_name=stem)

    def check_complete(
        self, graph: dict[str, Any], proofs: dict[str, str],
    ) -> dict[str, Any]:
        self._last_node_check = None
        stem = graph["file_slot"].removesuffix(".thy")
        result = self._check(
            self._complete_theory_source(
                graph, proofs, theory_name=f"Complete_{stem}",
                diagnostic_markers=True,
            ),
            f"Complete_{stem}",
        )
        result["check_scope"] = "complete-theory"
        result["incremental"] = False
        return result

    @native_operation
    def build_generated_session(self, theories: list[str]) -> dict[str, Any]:
        return self._build_session(theories, seal=False)

    @native_operation
    def build_sealed_session(self, theories: list[str]) -> dict[str, Any]:
        """Build the target in a child session; never replace its provider heap."""
        if not theories or theories[:-1] != list(self.previous_theories):
            raise ValueError("seal must extend exactly the frozen provider closure")
        return self._build_session(theories, seal=True)

    def _build_session(self, theories: list[str], *, seal: bool) -> dict[str, Any]:
        from regen.infrastructure.progress_events import generation_exclusion
        with generation_exclusion('seal' if seal else 'provider_build'):
            return self._build_session_timed(theories, seal=seal)

    def _build_session_timed(self, theories: list[str], *, seal: bool) -> dict[str, Any]:
        if CURRENT_RESOURCES.get() is not None:
            self.close_prefix()
        self._last_node_check = None
        session = "Sealed_Refine" if seal else "Generated_Refine"
        root = self.generated_dir / "ROOT"
        providers = theories[:-1] if seal else theories
        definitions = [
            f"session Generated_Refine = {self.base_session} +",
            "  sessions",
            "    Lib",
            "    CorresK",
            "    AInvs",
            "  theories",
            *(f"    {name}" for name in providers),
            "",
        ] if providers or not seal else []
        if seal:
            # Isabelle requires distinct session directories, even for a child.
            sealed_dir = self.generated_dir / 'sealed-session'
            sealed_dir.mkdir(exist_ok=True)
            (sealed_dir / (theories[-1] + '.thy')).write_bytes(
                (self.generated_dir / (theories[-1] + '.thy')).read_bytes())
            definitions += [
                f'session {session} in "sealed-session" = {"Generated_Refine" if providers else self.base_session} +',
                "  sessions Lib CorresK AInvs", "  theories", f"    {theories[-1]}", "",
            ]
        root.write_text("\n".join(definitions), encoding="utf-8")
        build_sources = {name: (self.generated_dir / (name + '.thy')).read_text()
                         for name in theories}
        # The ROOT may name only the target while its source imports another
        # generated theory. Bind and guard every theory mounted at /generated,
        # rather than hashing just the explicitly requested target list.
        mounted_sources = {path.name: path.read_bytes()
                           for path in sorted(self.generated_dir.glob('*.thy'))}
        if seal:
            child = sealed_dir / (theories[-1] + '.thy')
            mounted_sources['sealed-session/' + child.name] = child.read_bytes()
        root_content = root.read_text()
        l4v = self.l4v_dir
        isabelle = self.isabelle_dir
        workspace = self.semantic_workspace
        heaps = self.heaps_dir
        command = [
            "docker", "run", "--rm", "--network", "none",
            "--mount", f"type=bind,src={l4v},dst=/sel4-project/verification/l4v,readonly",
            "--mount", f"type=bind,src={isabelle},dst=/workspace/verification/isabelle,readonly",
            "--mount", f"type=bind,src={workspace},dst=/semantic-base,readonly",
            "--mount", f"type=bind,src={heaps},dst=/root/.isabelle/heaps",
            "--mount", f"type=bind,src={self.generated_dir},dst=/generated,readonly",
            "-e", f"L4V_ARCH={self.architecture}", self.image,
            "/workspace/verification/isabelle/bin/isabelle", "build", "-b",
        ]
        for item in SESSION_CATALOGS:
            command.extend(["-d", f"/sel4-project/verification/l4v/{item}"])
        command.extend([
            "-d", self.base_catalog, "-d", "/generated", session,
        ])
        resources = CURRENT_RESOURCES.get()
        build_timeout = self.timeout
        resource_policy = None
        if resources is not None:
            at = command.index("build") + 1
            command[at:at] = ["-j", "1", "-o", "threads=" + str(resources.policy["threads"])]
            resource_policy = dict(resources.policy)
            if not seal:
                build_timeout = resource_policy['provider_build_timeout']
            heap = resource_policy['build_ml_heap_mb']
            java_heap = resource_policy['build_java_heap_mb']
            ml_options = (f'-H {min(1024, heap)} --maxheap {heap} --stackspace 64 '
                          f'--gcthreads {resource_policy["threads"]}')
            # The image's user settings overwrite Docker -e ML_OPTIONS. Load
            # settings first, then override inside that initialized environment.
            # Nested Isabelle retains ISABELLE_SETTINGS_PRESENT and the limits.
            executable = '/workspace/verification/isabelle/bin/isabelle'
            at = command.index(executable) + 1
            command[at:at] = ['env', f'ML_OPTIONS={ml_options}',
                f'ISABELLE_TOOL_JAVA_OPTIONS=-Djava.awt.headless=true -Xms128m -Xmx{java_heap}m -Xss4m',
                f'ISABELLE_BUILD_JAVA_OPTIONS=-Xms128m -Xmx{java_heap}m -Xss4m', executable]
        from regen.execution.native_container import docker_limits
        command[2:2] = docker_limits()
        started = time.time()
        container_name = f"regen-isabelle-build-{os.getpid()}-{uuid.uuid4().hex}"
        command[2:2] = ["--name", container_name]
        emit_event(
            "isabelle_build_started", session=session,
            timeout_seconds=build_timeout, container=container_name,
            resource_policy=resource_policy,
        )
        try:
            result = native_run(
                command, text=True, capture_output=True, timeout=build_timeout,
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
                + f"\nIsabelle build timed out after {build_timeout} seconds.\n"
            )[-12000:]
            try:
                subprocess.run(
                    ["docker", "rm", "-f", container_name],
                    text=True, capture_output=True, timeout=30,
                )
            except (OSError, subprocess.SubprocessError):
                pass
        log = self.generated_dir / ("seal-build.log" if seal else "build.log")
        if root.read_text() != root_content or any(
                (self.generated_dir / (name + '.thy')).read_text() != source
                for name, source in build_sources.items()) or any(
                (self.generated_dir / name).read_bytes() != source
                for name, source in mounted_sources.items()):
            raise ValueError('session build inputs changed during execution')
        if seal and (sealed_dir / (theories[-1] + '.thy')).read_text() != build_sources[theories[-1]]:
            raise ValueError('sealed session build inputs changed during execution')
        log.write_text(diagnostic, encoding="utf-8")
        check = {
            "verdict": "green" if returncode == 0 else "red",
            "returncode": returncode,
            "diagnostic": diagnostic,
            "log": str(log),
            "wall_ms": int((time.time() - started) * 1000),
            "check_scope": "accumulated-generated-session",
            "incremental": False,
            "theories": list(theories),
            "session": session,
            "timeout_seconds": build_timeout,
            "resource_policy": resource_policy,
            "source_files": build_sources,
            "root_content": root_content,
            "root_sha256": hashlib.sha256(root_content.encode()).hexdigest(),
        }
        from regen.execution.native_check_receipt import native_check_receipt
        check['native_receipt'] = native_check_receipt(
            self, check, source={**mounted_sources, 'ROOT': root_content},
            scope='generated-session-build', log_text=diagnostic)
        emit_event(
            "isabelle_build_finished", session=session,
            verdict=check["verdict"], returncode=returncode,
            wall_ms=check["wall_ms"], log=str(log),
            timeout_seconds=build_timeout, resource_policy=resource_policy,
        )
        return check
