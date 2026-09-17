#!/usr/bin/env python3
"""Freeze semantic framework declarations without retaining proof helpers."""
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
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_CAMPAIGN = REPO / "reports" / "regen" / "refine-arm-session"
CONTAINER = os.environ.get("REGEN_CONTAINER", "sel4-l4v")
LAYER0_SESSION = "RegenRefineARM_L0"
LAYER1_SESSION = "RegenRefineARM_L1"
FRAMEWORK_THEORIES = {
    "Refine.Invariants_H": "executable invariant semantics",
    "Refine.StateRelation": "abstract-to-executable state relation semantics",
    "Refine.Corres": "refinement predicate abbreviations",
}
SEMANTIC_KINDS = {
    "definition", "defs", "abbreviation", "primrec", "fun", "function",
    "inductive", "inductive_set", "coinductive", "coinductive_set",
    "record", "datatype", "type_synonym", "axiomatization",
}
PROOF_KINDS = {
    "lemma", "theorem", "corollary", "lemmas", "schematic_goal", "crunch",
    "crunches", "declare", "interpretation", "global_interpretation", "sublocale",
    "method", "add_upd_simps", "setup", "local_setup", "ML", "ML_file",
    "named_theorems",
}
STRUCTURAL_KINDS = {
    "chapter", "section", "subsection", "subsubsection", "paragraph", "text", "txt",
    "context", "experiment", "begin", "end", "locale", "class", "instantiation",
    "instance", "bundle", "unbundle", "notation", "no_notation", "syntax",
    "translations", "global_naming", "requalify_facts",
}
COMMAND_KINDS = sorted(SEMANTIC_KINDS | PROOF_KINDS | STRUCTURAL_KINDS, key=len, reverse=True)
COMMAND_RE = re.compile(
    r"^(?:(private|local|global)\s+)?("
    + "|".join(map(re.escape, COMMAND_KINDS)) + r")\b"
)
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_']*")
HEADER_RE = re.compile(
    r"\btheory\s+(?:\"[^\"]+\"|\S+)\s+imports\s+.*?\s+begin\b", re.S
)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def extractors():
    from regen.preparation.regen_extract import slice_decls
    from regen.infrastructure.retrieval_utils import declaration_statement
    return slice_decls, declaration_statement


def build_inventory(campaign_dir: Path) -> dict:
    graph = read_json(campaign_dir / "theory_graph.json")["theories"]
    contracts = read_json(campaign_dir / "contracts.json")["contracts"]
    by_theory = {item["name"]: item for item in graph}
    slice_decls, clean_statement = extractors()
    declarations = []
    excluded = []
    for theory, role in FRAMEWORK_THEORIES.items():
        path = REPO / by_theory[theory]["source_path"]
        for decl in slice_decls(path.read_text(encoding="utf-8", errors="replace")):
            name = decl.get("name")
            if decl["kind"] in SEMANTIC_KINDS:
                statement = clean_statement(decl["stmt"])
                if decl["kind"] == "axiomatization":
                    raise ValueError(f"framework inventory must not freeze axioms: {theory}:{name}")
                declarations.append({
                    "id": f"framework:{theory}:{name or decl['start'] + 1}",
                    "theory": theory,
                    "name": name,
                    "kind": decl["kind"],
                    "statement": statement,
                    "statement_sha256": sha256_text(" ".join(statement.split())),
                    "source_line": decl["start"] + 1,
                    "policy": "keep_verbatim",
                    "role": role,
                })
            else:
                excluded.append({
                    "theory": theory,
                    "name": name,
                    "kind": decl["kind"],
                    "source_line": decl["start"] + 1,
                    "policy": "regenerate_or_delete",
                })

    named = {item["name"]: item for item in declarations if item["name"]}
    contract_text = {item["id"]: item["statement"] for item in contracts}
    for item in declarations:
        name = item["name"]
        item["direct_contract_consumers"] = (
            sorted(cid for cid, text in contract_text.items()
                   if name and re.search(
                       r"(?<![A-Za-z0-9_'])" + re.escape(name) + r"(?![A-Za-z0-9_'])",
                       text,
                   ))
        )
        tokens = set(TOKEN_RE.findall(item["statement"]))
        item["framework_dependencies"] = sorted(token for token in tokens if token in named and token != name)

    reverse = defaultdict(list)
    for item in declarations:
        for dependency in item["framework_dependencies"]:
            reverse[dependency].append(item["id"])
    for item in declarations:
        item["framework_consumers"] = sorted(reverse.get(item["name"], ()))

    declaration_set_sha256 = sha256_text(json.dumps(
        [
            {
                "id": item["id"],
                "kind": item["kind"],
                "statement_sha256": item["statement_sha256"],
            }
            for item in declarations
        ],
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ))
    result = {
        "schema_version": 1,
        "declaration_set_sha256": declaration_set_sha256,
        "policy": {
            "kept_kinds": sorted(SEMANTIC_KINDS - {"axiomatization"}),
            "excluded_proof_kinds": sorted(PROOF_KINDS),
            "designated_framework_theories": FRAMEWORK_THEORIES,
            "rationale": (
                "Freeze semantic constants/types that define invariant and refinement meaning; "
                "delete theorem statements, aliases, generated crunch facts, and proof attributes."
            ),
        },
        "summary": {
            "kept_declarations": len(declarations),
            "excluded_declarations": len(excluded),
            "kept_by_theory": dict(Counter(item["theory"] for item in declarations)),
            "kept_by_kind": dict(Counter(item["kind"] for item in declarations)),
            "directly_referenced_by_contract": sum(bool(item["direct_contract_consumers"]) for item in declarations),
        },
        "declarations": declarations,
        "excluded": excluded,
    }
    write_json(campaign_dir / "framework" / "inventory.json", result)
    return result


def split_command_blocks(body: str):
    lines = body.splitlines(keepends=True)
    starts = []
    for index, line in enumerate(lines):
        if line != line.lstrip():
            continue
        match = COMMAND_RE.match(line)
        if match:
            starts.append((index, match.group(1), match.group(2)))
    if not starts:
        return [(None, "raw", body)]
    blocks = []
    if starts[0][0]:
        blocks.append((None, "raw", "".join(lines[:starts[0][0]])))
    for pos, (start, modifier, kind) in enumerate(starts):
        end = starts[pos + 1][0] if pos + 1 < len(starts) else len(lines)
        blocks.append((modifier, kind, "".join(lines[start:end])))
    return blocks


def declaration_key(block: str, modifier: str | None, slice_decls):
    if modifier:
        block = re.sub(r"^(?:private|local|global)\s+", "", block, count=1)
    declarations = slice_decls(block)
    if not declarations:
        return None
    declaration = declarations[0]
    return declaration["kind"], declaration["name"]


def strip_framework_theory(
    source: str,
    theory_name: str,
    imports: list[str],
    inventory_items: list[dict],
) -> tuple[str, dict]:
    header = HEADER_RE.search(source)
    if not header:
        raise ValueError(f"could not parse theory header for {theory_name}")
    body = source[header.end():]
    blocks = split_command_blocks(body)
    slice_decls, _ = extractors()
    allowed = Counter((item["kind"], item["name"]) for item in inventory_items)
    selected = set()
    found = Counter()
    for index, (modifier, kind, block) in enumerate(blocks):
        if kind == "axiomatization":
            raise ValueError("axiomatization is forbidden")
        if kind not in SEMANTIC_KINDS or modifier == "private":
            continue
        key = declaration_key(block, modifier, slice_decls)
        if key in allowed and found[key] < allowed[key]:
            selected.add(index)
            found[key] += 1

    missing = allowed - found
    if missing:
        raise AssertionError(f"framework declarations missing from {theory_name}: {dict(missing)}")

    # Preserve only scopes that contain an inventoried semantic declaration.
    # Locales with only proof rules disappear entirely; contexts carrying an
    # actual definition are reduced to their header, definitions, and end.
    scope_starts = {"context", "class", "instantiation", "experiment", "bundle"}
    preceding_scope = {
        "context", "locale", "class", "instantiation", "experiment", "bundle",
    }
    stack = []
    keep = set(selected)
    previous_command = None
    for index, (_modifier, kind, block) in enumerate(blocks):
        if kind in scope_starts and re.search(r"\bbegin\b", block):
            stack.append({"starts": [index], "needed": False})
        elif kind == "begin":
            starts = [index]
            if previous_command is not None and blocks[previous_command][1] in preceding_scope:
                starts.insert(0, previous_command)
            stack.append({"starts": starts, "needed": False})

        if index in selected:
            for scope in stack:
                scope["needed"] = True

        if kind == "end" and stack:
            scope = stack.pop()
            if scope["needed"]:
                keep.update(scope["starts"])
                keep.add(index)

        if kind != "raw":
            previous_command = index

    # The unmatched final end closes the theory itself.
    unmatched_ends = [
        index for index, (_modifier, kind, _block) in enumerate(blocks)
        if kind == "end" and index not in keep
    ]
    if unmatched_ends:
        keep.add(unmatched_ends[-1])

    kept_blocks = [block for index, (_modifier, _kind, block) in enumerate(blocks) if index in keep]
    rendered = "\n".join([
        "(* Generated framework-only theory; proof declarations removed. *)",
        f"theory {theory_name}",
        "imports",
        *(f'  "{item}"' for item in imports),
        "begin",
        "".join(kept_blocks).lstrip("\n").rstrip(),
        "",
    ])
    rendered_declarations = [
        declaration for declaration in slice_decls(rendered)
        if declaration["kind"] in SEMANTIC_KINDS
    ]
    rendered_keys = Counter((item["kind"], item["name"]) for item in rendered_declarations)
    if rendered_keys != allowed:
        raise AssertionError(
            f"generated framework differs from inventory for {theory_name}: "
            f"missing={dict(allowed - rendered_keys)}, extra={dict(rendered_keys - allowed)}"
        )
    return rendered, {
        "inventoried_declarations": sum(allowed.values()),
        "kept_scope_blocks": len(keep - selected),
        "source_command_blocks": len(blocks),
        "generated_command_blocks": len(keep),
    }


def assemble_layer1(campaign_dir: Path) -> list[Path]:
    inventory = read_json(campaign_dir / "framework" / "inventory.json")
    graph = read_json(campaign_dir / "theory_graph.json")["theories"]
    by_theory = {item["name"]: item for item in graph}
    generated = campaign_dir / "generated-layer1"
    generated.mkdir(parents=True, exist_ok=True)
    targets = ("Refine.ArchMove_R", "Refine.Invariants_H")
    outputs = []
    manifest = []
    for theory in targets:
        info = by_theory[theory]
        source = (REPO / info["source_path"]).read_text(encoding="utf-8", errors="replace")
        imports = [
            f"RegenRefineARM_L0.{item.split('.', 1)[1]}" if item in {"Refine.Move_R", "Refine.LevityCatch"}
            else item
            for item in info["direct_imports"]
        ]
        basename = theory.split(".", 1)[1]
        theory_inventory = [item for item in inventory["declarations"] if item["theory"] == theory]
        rendered, assembly_stats = strip_framework_theory(
            source, basename, imports, theory_inventory
        )
        if re.search(
            r"^(?:(?:private|local|global)\s+)?(?:lemma|theorem|corollary|lemmas|"
            r"schematic_goal|crunch|declare|interpretation|locale|method|add_upd_simps)\b",
            rendered,
            re.M,
        ):
            raise AssertionError(f"proof declaration leaked into {basename}")
        path = generated / f"{basename}.thy"
        path.write_text(rendered, encoding="utf-8")
        outputs.append(path)
        manifest.append({
            "theory": theory,
            "path": str(path.relative_to(REPO)),
            "source_sha256": sha256_text(rendered),
            "framework_declarations": sum(
                item["theory"] == theory for item in inventory["declarations"]
            ),
            "assembly": assembly_stats,
        })
    write_json(campaign_dir / "framework" / "layer1-assembly.json", {
        "schema_version": 1,
        "parent_session": "RegenRefineARM_L0",
        "framework_inventory_sha256": inventory["declaration_set_sha256"],
        "theories": manifest,
    })
    return outputs


def container_run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "exec", "-e", "L4V_ARCH=ARM", "-e",
         "L4V_DIR=/sel4-project/verification/l4v", CONTAINER, *args],
        cwd=REPO, text=True, capture_output=True,
    )


def verify_and_seal_layer1(campaign_dir: Path, paths: list[Path]) -> dict:
    inventory = read_json(campaign_dir / "framework" / "inventory.json")
    layer0_dir = campaign_dir / "generated"
    layer1_dir = campaign_dir / "generated-layer1"
    layer0_ctr = "/workspace/" + str(layer0_dir.relative_to(REPO))
    layer1_ctr = "/workspace/" + str(layer1_dir.relative_to(REPO))
    check_dir = campaign_dir / "framework" / "checks-layer1"
    check_dir.mkdir(parents=True, exist_ok=True)
    checks = []
    for path in paths:
        theory_ctr = "/workspace/" + str(path.with_suffix("").relative_to(REPO))
        t0 = time.time()
        proc = container_run([
            "/workspace/verification/isabelle/bin/isabelle", "process",
            "-l", LAYER0_SESSION,
            "-d", layer0_ctr,
            "-d", "/sel4-project/verification/l4v",
            "-T", theory_ctr,
        ])
        log_path = check_dir / f"{path.stem}.log"
        log_path.write_text(proc.stdout + proc.stderr, encoding="utf-8")
        check = {
            "theory": path.stem,
            "parent_session": LAYER0_SESSION,
            "returncode": proc.returncode,
            "verdict": "pass" if proc.returncode == 0 else "fail",
            "wall_ms": int((time.time() - t0) * 1000),
            "source_sha256": sha256_text(path.read_text(encoding="utf-8")),
            "log": str(log_path.relative_to(REPO)),
        }
        checks.append(check)
        if proc.returncode != 0:
            write_json(campaign_dir / "framework" / "checks-layer1.json", {"checks": checks})
            raise SystemExit(f"layer-1 theory gate failed for {path.stem}")
    write_json(campaign_dir / "framework" / "checks-layer1.json", {"checks": checks})

    root_text = "\n".join([
        f'session {LAYER1_SESSION} in "." = {LAYER0_SESSION} +',
        "  theories",
        *(f"    {path.stem}" for path in paths),
        "",
    ])
    (layer1_dir / "ROOT").write_text(root_text, encoding="utf-8")
    t0 = time.time()
    build = container_run([
        "/workspace/verification/isabelle/bin/isabelle", "build",
        "-d", layer0_ctr,
        "-d", layer1_ctr,
        "-d", "/sel4-project/verification/l4v",
        "-b", LAYER1_SESSION,
    ])
    build_log = campaign_dir / "framework" / "seal-layer1" / "build.log"
    build_log.parent.mkdir(parents=True, exist_ok=True)
    build_log.write_text(build.stdout + build.stderr, encoding="utf-8")
    if build.returncode != 0:
        raise SystemExit("layer-1 heap build failed")

    getenv = container_run([
        "/workspace/verification/isabelle/bin/isabelle", "getenv", "-b", "ISABELLE_HEAPS"
    ])
    heap_root = getenv.stdout.strip()
    heap_path = f"{heap_root}/polyml-5.9.1_x86_64_32-linux/{LAYER1_SESSION}"
    identity = container_run(["bash", "-lc", f"sha256sum '{heap_path}'; stat -c %s '{heap_path}'"])
    identity_lines = identity.stdout.splitlines()
    if identity.returncode != 0 or len(identity_lines) < 2:
        raise SystemExit("could not identify layer-1 heap")

    smoke_dir = campaign_dir / "framework" / "seal-layer1" / "smoke"
    smoke_dir.mkdir(parents=True, exist_ok=True)
    smoke_path = smoke_dir / "Layer1Smoke.thy"
    smoke_path.write_text("\n".join([
        "theory Layer1Smoke",
        "imports",
        f'  "{LAYER1_SESSION}.ArchMove_R"',
        f'  "{LAYER1_SESSION}.Invariants_H"',
        "begin",
        "end",
        "",
    ]), encoding="utf-8")
    smoke_ctr = "/workspace/" + str(smoke_path.with_suffix("").relative_to(REPO))
    smoke_start = time.time()
    smoke = container_run([
        "/workspace/verification/isabelle/bin/isabelle", "process",
        "-l", LAYER1_SESSION,
        "-d", layer0_ctr,
        "-d", layer1_ctr,
        "-d", "/sel4-project/verification/l4v",
        "-T", smoke_ctr,
    ])
    smoke_log = smoke_dir / "check.log"
    smoke_log.write_text(smoke.stdout + smoke.stderr, encoding="utf-8")
    if smoke.returncode != 0:
        raise SystemExit("layer-1 downstream smoke test failed")

    manifest = {
        "schema_version": 1,
        "status": "sealed",
        "sealed_at": datetime.now(timezone.utc).isoformat(),
        "session": LAYER1_SESSION,
        "parent_session": LAYER0_SESSION,
        "framework_inventory_sha256": inventory["declaration_set_sha256"],
        "theories": [path.stem for path in paths],
        "checks": checks,
        "build_wall_ms": int((time.time() - t0) * 1000),
        "build_log": str(build_log.relative_to(REPO)),
        "heap": {
            "container_path": heap_path,
            "sha256": identity_lines[0].split()[0],
            "size_bytes": int(identity_lines[1]),
        },
        "downstream_smoke": {
            "verdict": "pass",
            "returncode": smoke.returncode,
            "wall_ms": int((time.time() - smoke_start) * 1000),
            "log": str(smoke_log.relative_to(REPO)),
        },
        "human_refine_heap_loaded": False,
    }
    write_json(campaign_dir / "framework" / "seal-layer1" / "manifest.json", manifest)
    write_json(campaign_dir / "state.json", {
        "schema_version": 1,
        "layer": 1,
        "status": "sealed",
        "session": LAYER1_SESSION,
        "theories": ["LevityCatch", "Move_R", "ArchMove_R", "Invariants_H"],
        "next_ready_theories": ["Refine.InvariantUpdates_H", "Refine.Invocations_R"],
    })
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN)
    ap.add_argument("--assemble-layer1", action="store_true")
    ap.add_argument("--seal-layer1", action="store_true")
    args = ap.parse_args()
    campaign_dir = args.campaign_dir.resolve()
    inventory = build_inventory(campaign_dir)
    result = {"summary": inventory["summary"]}
    if args.assemble_layer1:
        paths = assemble_layer1(campaign_dir)
        result["assembled"] = [str(path) for path in paths]
        if args.seal_layer1:
            result["seal"] = verify_and_seal_layer1(campaign_dir, paths)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
