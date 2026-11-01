#!/usr/bin/env python3
"""Prepare definition-only Isabelle inputs and a VERO-compatible contract list.

This is trusted benchmark preparation, not an agent runner. Reference lemmas
and their statements are excluded from sanitized/. Build that session before
using it in an isolated evaluation.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
THEORIES = [
    "Interval_Model", "Interval_Baseline", "Interval_Extensions",
    "Interval_Tree", "Interval_Refinement",
]
COMMANDS = {
    "theory", "imports", "begin", "end", "text", "text_raw", "section",
    "subsection", "subsubsection", "record", "datatype", "type_synonym",
    "definition", "abbreviation", "fun", "function", "primrec", "termination",
    "lemma", "theorem", "corollary", "proposition", "lemmas", "declare", "ML",
    "setup", "local_setup", "method_setup", "value", "export_code", "context",
    "locale", "axiomatization", "axioms", "consts", "typedecl", "typedef",
    "instantiation", "instance", "syntax", "translations", "notation",
}
KEEP = {
    "record", "datatype", "type_synonym", "definition", "abbreviation",
    "fun", "function", "primrec", "termination",
}
DISCARD = {
    "theory", "imports", "begin", "end", "text", "text_raw", "section",
    "subsection", "subsubsection", "lemma", "theorem", "corollary",
    "proposition", "lemmas",
}


def commands(text: str):
    """Recognize outer line commands outside strings, nested comments/cartouches.

    The project deliberately uses top-level declarations only. Unsupported
    commands fail closed rather than being silently copied into trusted input.
    """
    starts = []
    i = 0
    comment = cartouche = 0
    quoted = False
    line_start = True
    while i < len(text):
        if not comment and not cartouche and not quoted and line_start:
            match = re.match(r"[ \t]*([A-Za-z_][A-Za-z_0-9]*)\b", text[i:])
            if match and match[1] in COMMANDS:
                starts.append((i, match[1]))
        if comment:
            if text.startswith("(*", i):
                comment += 1; i += 2; continue
            if text.startswith("*)", i):
                comment -= 1; i += 2; continue
        elif quoted:
            if text[i] == "\\" and i + 1 < len(text):
                i += 2; line_start = False; continue
            if text[i] == '"':
                quoted = False
        elif text.startswith("(*", i):
            comment = 1; i += 2; continue
        elif text.startswith(r"\<open>", i) or text[i] == "‹":
            cartouche += 1
            i += len(r"\<open>") if text.startswith(r"\<open>", i) else 1
            line_start = False; continue
        elif text.startswith(r"\<close>", i) or text[i] == "›":
            if not cartouche:
                raise ValueError("unmatched cartouche")
            cartouche -= 1
            i += len(r"\<close>") if text.startswith(r"\<close>", i) else 1
            line_start = False; continue
        elif not cartouche and text[i] == '"':
            quoted = True
        line_start = text[i] == "\n"
        i += 1
    if comment or cartouche or quoted:
        raise ValueError("unterminated comment, cartouche, or string")
    for n, (start, command) in enumerate(starts):
        stop = starts[n + 1][0] if n + 1 < len(starts) else len(text)
        yield command, text[start:stop]


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def spec_body(goal: str, block: str) -> str:
    """Expose the closed spec body, without unfolding its implementation.

    This deliberately accepts only our nullary bool definitions. The native
    audit below checks the parsed proposition against the defining equation;
    this text extraction is not trusted to establish semantic equivalence.
    """
    match = re.match(
        r'\s*definition\s+' + re.escape(goal) +
        r'\s*::\s*bool\s+where\s*"((?:\\.|[^"\\])*)"', block, re.S)
    if not match:
        raise ValueError("unsupported closed spec definition: " + goal)
    equation = re.fullmatch(
        r'\s*' + re.escape(goal) + r'\s*\\<longleftrightarrow>\s*(.+?)\s*',
        match[1], re.S)
    if not equation:
        raise ValueError("spec is not a named bool equivalence: " + goal)
    return equation[1]


def expansion_audit(targets: list[dict]) -> str:
    pairs = ",\n".join(
        '    ("' + t["id"] + '", @{prop "' + t["theory"] + "." + t["definition"] +
        '"}, @{prop "' + t["expanded_proposition"] + '"}, @{thm ' +
        t["theory"] + "." + t["definition"] + '_def})' for t in targets)
    return r'''ML \<open>
  val ctxt = @{context};
  fun check_expansion (id, original, expanded, equation) =
    let
      val conversion = Simplifier.rewrite
        (put_simpset HOL_basic_ss ctxt addsimps [equation])
        (Thm.cterm_of ctxt original);
      val actual = Thm.term_of (Thm.rhs_of conversion);
      val _ = if actual aconv expanded then ()
        else error ("Spec expansion changed the proposition: " ^ id);
      val _ = if null (Thm.hyps_of conversion) andalso
                 null (Thm_Deps.all_oracles [conversion]) then ()
        else error ("Untrusted spec expansion: " ^ id);
    in writeln ("INTERVALTREE_SPEC_EXPANSION " ^ id) end;
  val _ = List.app check_expansion [
''' + pairs + r'''
  ];
\<close>
'''


def main():
    sanitized = ROOT / "sanitized"
    theories_out = sanitized / "theories"
    theories_out.mkdir(parents=True, exist_ok=True)
    goals_out = sanitized / "goals"
    goals_out.mkdir(parents=True, exist_ok=True)
    proof_names = {}
    for path in (ROOT / "theories").glob("*.thy"):
        if path.stem == "Interval_Audit":
            continue
        for command, block in commands(path.read_text()):
            if command in {"lemma", "theorem", "corollary", "proposition"}:
                m = re.match(r"\s*\w+\s+(verified_[A-Za-z_0-9]+)\b", block)
                if m:
                    proof_names[m[1][len("verified_"):]] = path.stem + "." + m[1]
    targets = []
    for name in THEORIES:
        text = (ROOT / "theories" / (name + ".thy")).read_text()
        header = re.search(r"\Atheory\s+\w+\s+imports\s+(.*?)\bbegin\b", text, re.S)
        if not header:
            raise ValueError("unsupported theory header: " + name)
        chunks = [f"theory {name}\n  imports {header[1].strip()}\nbegin\n"]
        for command, block in commands(text):
            if command in KEEP:
                chunks.append(block.rstrip() + "\n")
                m = re.match(r'\s*definition\s+((?:spec_|ext_|tree_|refine_)\w+)\s*::\s*bool\s+where', block)
                if m:
                    goal = m[1]
                    group = "baseline" if goal.startswith("spec_") else (
                        "extension" if goal.startswith("ext_") else "concrete")
                    body = spec_body(goal, block)
                    targets.append({
                        "id": goal,
                        "definition": goal,
                        "theory": name,
                        "group": group,
                        "statement": f'lemma {goal}: "{body}"',
                        "named_statement": f'lemma {goal}: "{name}.{goal}"',
                        "expanded_proposition": body,
                        # Only a completed kernel audit can upgrade this status.
                        "status": "pending",
                        "proof_theorem": proof_names.get(goal),
                        "reference_proof_candidate": goal in proof_names,
                    })
            elif command == "declare" and re.fullmatch(
                    r"\s*declare\s+\w+(?:\.\w+)*\s*\[code\s+del\]\s*", block):
                # The associated executable equation lemma is removed too.
                # Proof-only input keeps the original logical definition.
                continue
            elif command not in DISCARD:
                raise ValueError(f"unsupported outer command {command!r} in {name}")
        chunks.append("end\n")
        (theories_out / (name + ".thy")).write_text("\n".join(chunks))

    manifest = json.loads((ROOT / "vendor/vero/manifest.json").read_text())
    original = manifest["packages"][0]["modules"][0]["specs"]
    rename = {"spec_merge_getElem?_lo_split": "spec_merge_getElem_lo_split"}
    expected = {rename.get(n, n) for n in original}
    actual = {t["id"] for t in targets if t["group"] == "baseline"}
    if actual != expected or len(actual) != 56:
        raise ValueError(f"baseline mismatch: missing={expected-actual}, extra={actual-expected}")
    if len({t["id"] for t in targets}) != len(targets):
        raise ValueError("duplicate target")

    source_hashes = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((ROOT / "vendor").rglob("*")) if p.is_file()
        and "__pycache__" not in p.parts and p.suffix != ".pyc"
    }
    write_json(ROOT / "source-lock.json", {"files": source_hashes})
    write_json(ROOT / "targets.json", {
        "stage": "preparation", "formal_evaluation_started": False,
        "input_revision": "expanded-specs-v2",
        "semantic_feedback_file": "model-feedback.json",
        "status_note": "pending until a matching full build and native theorem audit succeed",
        "baseline_name_map": rename, "targets": targets,
    })
    (theories_out / "Interval_Input.thy").write_text(
        "theory Interval_Input\n  imports " + " ".join(THEORIES) + "\nbegin\nend\n")
    (sanitized / "ROOT").write_text(
        'session Intervaltree_Definitions = HOL +\n'
        '  options [document = false, threads = 2]\n'
        '  directories "theories"\n'
        '  theories Interval_Input\n'
        'session Intervaltree_Goals in "goals" = Intervaltree_Definitions +\n'
        '  options [document = false, threads = 2]\n'
        '  theories Interval_Goals\n')
    (sanitized / "Interval_Goals.thy").unlink(missing_ok=True)
    (goals_out / "Interval_Goals.thy").write_text(
        "theory Interval_Goals\n  imports Intervaltree_Definitions.Interval_Input\nbegin\n\n" +
        expansion_audit(targets) + "\n" +
        "\n\n".join(t["statement"] + "\n  oops" for t in targets) + "\n\nend\n")
    contracts = [{
        "contract_id": t["id"], "name": t["id"],
        "owner_theory": t["theory"], "exact_statement": t["statement"],
        "contract_owned_context": [],
    } for t in targets]
    (sanitized / "contracts.jsonl").write_text(
        "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in contracts))
    # Use the existing harness schema; this only prepares fixed contracts.
    # No planner, prover or generation experiment is started here.
    harness_path = ROOT.parent / "vero-isabelle/src/vero/isabelle/benchmark.py"
    if harness_path.is_file():
        spec = importlib.util.spec_from_file_location("vero_benchmark_preparation", harness_path)
        harness = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(harness)
        harness.prepare(sanitized / "contracts.jsonl", sanitized,
                        base_import="Intervaltree_Definitions.Interval_Input",
                        provenance={"project": "intervaltree", "stage": "preparation",
                                    "input_revision": "expanded-specs-v2",
                                    "python_commit": "1bc406e1f441577c4e421fc51aba2ab67fbd97fb",
                                    "formal_evaluation_started": False})
    write_json(sanitized / "input-manifest.json", {
        "base_import": "Intervaltree_Definitions.Interval_Input",
        "input_revision": "expanded-specs-v2",
        "contract_representation": "closed spec bodies, definitionally equivalent to named bool goals",
        "target_count": len(targets), "reference_proofs_included": False,
        "helper_statements_included": False,
        "validation": "requires an independent build; native ML checks every spec expansion; oops establishes no target theorem",
        "files": {
            str(p.relative_to(sanitized)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(sanitized.rglob("*")) if p.is_file()
            and p.name != "input-manifest.json" and "build" not in p.parts
        },
    })
    # Native proof audit checks each preparation proof's exact target and oracle
    # closure. It is never included in the sanitized evaluation environment.
    candidates = [t for t in targets if t["proof_theorem"]]
    pairs = ",\n".join(
        f'    ("{t["id"]}", "{t["theory"]}.{t["id"]}", "{t["proof_theorem"]}")'
        for t in candidates)
    audit = """theory Interval_Audit
  imports Interval_Baseline_Proofs Interval_Extensions Interval_Refinement
begin
ML \\<open>
  val ctxt = @{context};
  fun audit (id, target, theorem_name) =
    let
      val th = Proof_Context.get_thm ctxt theorem_name;
      val expected = Syntax.read_prop ctxt target;
      val _ = if Thm.prop_of th aconv expected then ()
        else error ("Wrong preparation theorem: " ^ id);
      val _ = if null (Thm.hyps_of th) then ()
        else error ("Undischarged assumptions: " ^ id);
      val _ = if null (Thm_Deps.all_oracles [th]) then ()
        else error ("Oracle dependency: " ^ id);
    in writeln ("INTERVALTREE_VERIFIED " ^ id) end;
  val _ = List.app audit [
PAIRS
  ];
\\<close>
end
"""
    (ROOT / "theories/Interval_Audit.thy").write_text(audit.replace("PAIRS", pairs))
    print(json.dumps({
        "targets": len(targets),
        "baseline": len(actual),
        "reference_proof_candidates": len(candidates),
        "sanitized": str(sanitized),
    }))


if __name__ == "__main__":
    main()
