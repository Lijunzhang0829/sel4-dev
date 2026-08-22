#!/usr/bin/env python3
"""Planning control plane for session-staged seL4 proof regeneration.

The catalog built here is deliberately split into two layers:

* accessibility is determined by the Isabelle theory import DAG;
* retrieval ranks only records that survived that legality filter.

Human declarations from the target session are never entered into the premise
catalog.  Only fixed contract statements are read from the target sources.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
DEFAULT_DAG = REPO / "reports" / "theory-dag-ARM.json"
DEFAULT_OUT = REPO / "reports" / "regen" / "refine-arm-session"
DEFAULT_FROZEN = ("AInvs", "BaseRefine", "Lib", "CorresK")
PROOF_KINDS = {"lemma", "theorem", "corollary", "lemmas", "schematic_goal"}
DECL_KINDS = {
    "definition", "defs", "abbreviation", "primrec", "fun", "function",
    "inductive", "inductive_set", "coinductive", "coinductive_set",
    "record", "datatype", "type_synonym", "named_theorems",
}
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_']{2,}")
TOP_LEVEL_BOUNDARY_RE = re.compile(
    r"^(?:chapter|section|subsection|subsubsection|paragraph|text|txt|"
    r"class|locale|context|experiment|bundle|unbundle|notation|no_notation|"
    r"syntax|translations|ML|ML_file|setup|local_setup|begin|end)\b"
)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_stmt(stmt: str) -> str:
    return " ".join(stmt.split())


def declaration_statement(raw: str) -> str:
    """Trim top-level prose/context accidentally included by the coarse slicer."""
    lines = raw.splitlines()
    kept = []
    for index, line in enumerate(lines):
        if index and line == line.lstrip() and TOP_LEVEL_BOUNDARY_RE.match(line):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def read_json(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def load_extractors():
    sys.path.insert(0, str(REPO / "regen" / "scripts"))
    from regen_extract import is_contract, slice_decls
    return slice_decls, is_contract


def graph_from_dag(dag: dict):
    nodes = {n["name"]: n for n in dag["nodes"]}
    imports = {name: set() for name in nodes}
    for source, target in dag["edges"]:
        if source in imports and target in nodes:
            imports[source].add(target)
    return nodes, imports


def closure(start: str, imports: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    pending = list(imports.get(start, ()))
    while pending:
        node = pending.pop()
        if node in seen:
            continue
        seen.add(node)
        pending.extend(imports.get(node, ()))
    return seen


def topo_layers(targets: set[str], imports: dict[str, set[str]]) -> list[list[str]]:
    remaining = set(targets)
    completed: set[str] = set()
    layers: list[list[str]] = []
    while remaining:
        ready = sorted(
            node for node in remaining
            if (imports.get(node, set()) & targets) <= completed
        )
        if not ready:
            raise ValueError(f"cycle in target theory graph: {sorted(remaining)[:8]}")
        layers.append(ready)
        completed.update(ready)
        remaining.difference_update(ready)
    return layers


def git_metadata() -> dict:
    def run(*args: str) -> str:
        return subprocess.check_output(args, cwd=REPO, text=True).strip()

    try:
        commit = run("git", "rev-parse", "HEAD")
        branch = run("git", "branch", "--show-current")
        dirty = bool(run("git", "status", "--porcelain"))
    except (OSError, subprocess.CalledProcessError):
        commit, branch, dirty = "unknown", "unknown", None
    return {"commit": commit, "branch": branch, "dirty": dirty}


def extract_contracts(targets: set[str], nodes: dict, slice_decls, is_contract):
    contracts = []
    for theory in sorted(targets):
        path = Path(nodes[theory]["path"])
        source = path.read_text(encoding="utf-8", errors="replace")
        for decl in slice_decls(source):
            if not is_contract(decl):
                continue
            statement = declaration_statement(decl["stmt"])
            statement_hash = sha256_text(normalize_stmt(statement))
            contracts.append({
                "id": f"contract:{theory}:{decl['name']}:{statement_hash[:12]}",
                "theory": theory,
                "name": decl["name"],
                "kind": decl["kind"],
                "attributes": decl["attr"],
                "statement": statement,
                "statement_hash": statement_hash,
                "source_line": decl["start"] + 1,
                "status": "fixed_unproved",
            })
    return contracts


def candidate_names(decl: dict) -> list[str]:
    name = decl["name"]
    if not name:
        return []
    if decl["kind"] in PROOF_KINDS or decl["kind"] == "defs":
        return [name]
    if decl["kind"] in {"definition", "abbreviation", "primrec", "fun", "function"}:
        return [name, f"{name}_def"]
    return [name]


def build_premise_catalog(
    external_theories: set[str], nodes: dict, frozen_sessions: set[str], slice_decls
):
    records = []
    ids_by_theory: dict[str, list[str]] = defaultdict(list)
    for theory in sorted(external_theories):
        info = nodes[theory]
        path = Path(info["path"])
        source = path.read_text(encoding="utf-8", errors="replace")
        for decl in slice_decls(source):
            if not decl["name"] or decl["kind"] not in PROOF_KINDS | DECL_KINDS:
                continue
            statement = declaration_statement(decl["stmt"])
            digest = sha256_text(f"{theory}\0{decl['kind']}\0{decl['name']}\0{statement}")
            record_id = f"premise:{digest[:20]}"
            record = {
                "id": record_id,
                "theory": theory,
                "session": info["session"],
                "name": decl["name"],
                "candidate_names": candidate_names(decl),
                "kind": decl["kind"],
                "record_role": "proof_fact" if decl["kind"] in PROOF_KINDS else "declaration",
                "attributes": decl["attr"],
                "statement": statement,
                "statement_hash": sha256_text(normalize_stmt(statement)),
                "source_class": (
                    "frozen_parent" if info["session"] in frozen_sessions
                    else "endpoint_framework"
                ),
                "source_path": str(path.relative_to(REPO)),
                "source_line": decl["start"] + 1,
            }
            records.append(record)
            ids_by_theory[theory].append(record_id)
    return records, ids_by_theory


def build_theory_graph(
    targets: set[str], nodes: dict, imports: dict[str, set[str]], layers: list[list[str]],
    contracts: list[dict],
):
    layer_of = {node: i for i, layer in enumerate(layers) for node in layer}
    contracts_by_theory: dict[str, list[str]] = defaultdict(list)
    for contract in contracts:
        contracts_by_theory[contract["theory"]].append(contract["id"])

    all_ancestors = {node: closure(node, imports) for node in targets}
    result = []
    visibility = {}
    for theory in sorted(targets):
        ancestors = all_ancestors[theory]
        target_ancestors = sorted(ancestors & targets)
        external_ancestors = sorted(ancestors - targets)
        descendants = sorted(
            other for other in targets if theory in all_ancestors[other]
        )
        direct = sorted(imports.get(theory, set()))
        result.append({
            "name": theory,
            "source_path": str(Path(nodes[theory]["path"]).relative_to(REPO)),
            "topological_layer": layer_of[theory],
            "direct_imports": direct,
            "target_imports": sorted(set(direct) & targets),
            "target_ancestors": target_ancestors,
            "external_ancestors": external_ancestors,
            "descendants": descendants,
            "contracts": sorted(contracts_by_theory[theory]),
            "status": "discovered",
        })
        visibility[theory] = {
            "generated_target_ancestors": target_ancestors,
            "frozen_external_ancestors": external_ancestors,
            "original_target_premises_allowed": False,
        }
    return result, visibility


def write_bootstrap_shell(out_dir: Path, first: str, imports: dict[str, set[str]]) -> Path:
    theory_name = first.split(".", 1)[1]
    lines = [f"theory {theory_name}", "imports"]
    lines.extend(f'  "{name}"' for name in sorted(imports[first]))
    lines.extend(["begin", "", "end", ""])
    path = out_dir / "generated" / f"{theory_name}.thy"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def init_campaign(args) -> None:
    dag = read_json(args.dag)
    nodes, imports = graph_from_dag(dag)
    targets = {name for name, info in nodes.items() if info["session"] == args.session}
    if not targets:
        raise SystemExit(f"no theories found for session {args.session}")
    if args.first_theory not in targets:
        raise SystemExit(f"first theory is outside {args.session}: {args.first_theory}")

    layers = topo_layers(targets, imports)
    roots = sorted(node for node in targets if not (imports[node] & targets))
    if args.first_theory not in roots:
        raise SystemExit(f"first theory must be a target-session root; roots={roots}")

    slice_decls, is_contract = load_extractors()
    contracts = extract_contracts(targets, nodes, slice_decls, is_contract)
    ancestors_by_target = {theory: closure(theory, imports) for theory in targets}
    external_theories = set().union(*ancestors_by_target.values()) - targets
    frozen_sessions = set(args.frozen_session)
    premises, ids_by_theory = build_premise_catalog(
        external_theories, nodes, frozen_sessions, slice_decls
    )
    if any(record["session"] == args.session for record in premises):
        raise AssertionError("target-session declaration leaked into premise catalog")

    theory_graph, visibility = build_theory_graph(
        targets, nodes, imports, layers, contracts
    )
    first_visibility = visibility[args.first_theory]
    if first_visibility["generated_target_ancestors"]:
        raise AssertionError("first theory unexpectedly has target-session ancestors")
    first_allowed_count = sum(
        len(ids_by_theory.get(theory, ()))
        for theory in first_visibility["frozen_external_ancestors"]
    )

    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    shell_path = write_bootstrap_shell(out_dir, args.first_theory, imports)
    contract_ids = [c["id"] for c in contracts if c["theory"] == args.first_theory]
    lemma_graph = {
        "schema_version": 1,
        "theory": args.first_theory,
        "status": "awaiting_session_plan" if not contract_ids else "unplanned",
        "contract_roots": contract_ids,
        "nodes": [],
        "dynamic_growth_policy": "new support nodes may only be attached below a red frontier",
        "note": (
            "This support-only root has no fixed contract. Its generated exports must be "
            "justified by declared downstream consumers in the session plan."
            if not contract_ids else ""
        ),
    }
    campaign = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "repository": git_metadata(),
        "architecture": args.arch,
        "target_session": args.session,
        "claim_level": "session-staged repo-level",
        "frozen_parent_sessions": sorted(frozen_sessions),
        "target_theory_count": len(targets),
        "contract_count": len(contracts),
        "theory_roots": roots,
        "first_theory": args.first_theory,
        "first_theory_role": "bootstrap premise-firewall and sealing experiment",
        "first_contract_milestone": "Refine.Bits_R",
        "human_target_premises_allowed": False,
        "cross_session_joint_planning": False,
        "cross_session_frozen_premises": True,
        "source_dag": str(args.dag.relative_to(REPO)),
        "bootstrap_shell": str(shell_path.relative_to(REPO)),
        "completion_gate": "clean generated Refine session build with all fixed contracts green",
    }
    catalog = {
        "schema_version": 1,
        "target_session": args.session,
        "policy": "accessibility-filter-before-ranking",
        "extractor_sha256": sha256_path(REPO / "regen" / "scripts" / "regen_extract.py"),
        "record_count": len(premises),
        "theory_count": len({r["theory"] for r in premises}),
        "records_sha256": sha256_text(json.dumps(premises, sort_keys=True)),
        "records": premises,
    }
    write_json(out_dir / "campaign.json", campaign)
    write_json(out_dir / "contracts.json", {"contracts": contracts})
    write_json(out_dir / "theory_graph.json", {"layers": layers, "theories": theory_graph})
    write_json(out_dir / "visibility.json", visibility)
    write_json(out_dir / "premise_catalog.json", catalog)
    write_json(out_dir / f"lemma_graph-{args.first_theory.split('.', 1)[1]}.json", lemma_graph)
    print(json.dumps({
        "out": str(out_dir),
        "target_theories": len(targets),
        "layers": len(layers),
        "contracts": len(contracts),
        "premise_records": len(premises),
        "first_theory": args.first_theory,
        "first_theory_contracts": len(contract_ids),
        "first_theory_allowed_premises": first_allowed_count,
        "bootstrap_shell": str(shell_path),
    }, indent=2))


def bm25(records: list[dict], query: str, k: int) -> list[dict]:
    query_tokens = TOKEN_RE.findall(query)
    if not query_tokens or not records:
        return []
    docs = []
    document_frequency = Counter()
    for record in records:
        name_text = " ".join(record.get("candidate_names", ()))
        tokens = TOKEN_RE.findall(f"{name_text} {name_text} {record['statement']}")
        counts = Counter(tokens)
        docs.append((record, counts, len(tokens)))
        document_frequency.update(counts.keys())
    avg_len = sum(length for _, _, length in docs) / len(docs)
    scored = []
    for record, counts, length in docs:
        score = 0.0
        for token in query_tokens:
            frequency = counts[token]
            if not frequency:
                continue
            df = document_frequency[token]
            idf = math.log(1.0 + (len(docs) - df + 0.5) / (df + 0.5))
            denom = frequency + 1.5 * (1.0 - 0.75 + 0.75 * length / max(avg_len, 1.0))
            score += idf * frequency * 2.5 / denom
        if score:
            scored.append((score, record))
    scored.sort(key=lambda item: (-item[0], item[1]["id"]))
    return [{"score": round(score, 6), **record} for score, record in scored[:k]]


def retrieve(args) -> None:
    catalog = read_json(args.campaign_dir / "premise_catalog.json")
    visibility = read_json(args.campaign_dir / "visibility.json")
    if args.theory not in visibility:
        raise SystemExit(f"unknown target theory: {args.theory}")
    allowed_theories = set(visibility[args.theory]["frozen_external_ancestors"])
    records = [record for record in catalog["records"] if record["theory"] in allowed_theories]
    print(json.dumps({
        "theory": args.theory,
        "query": args.query,
        "legal_candidate_count": len(records),
        "results": bm25(records, args.query, args.k),
    }, ensure_ascii=False, indent=2))


def refresh_catalog(args) -> None:
    campaign_dir = args.campaign_dir.resolve()
    campaign = read_json(campaign_dir / "campaign.json")
    visibility = read_json(campaign_dir / "visibility.json")
    dag_path = REPO / campaign["source_dag"]
    dag = read_json(dag_path)
    nodes, _imports = graph_from_dag(dag)
    target_theories = set(visibility)
    external_theories = set().union(
        *(set(item["frozen_external_ancestors"]) for item in visibility.values())
    )
    if external_theories & target_theories:
        raise AssertionError("target theory leaked into external premise closure")

    slice_decls, _is_contract = load_extractors()
    records, _ids_by_theory = build_premise_catalog(
        external_theories,
        nodes,
        set(campaign["frozen_parent_sessions"]),
        slice_decls,
    )
    if any(record["session"] == campaign["target_session"] for record in records):
        raise AssertionError("target-session declaration leaked into premise catalog")
    catalog = {
        "schema_version": 1,
        "target_session": campaign["target_session"],
        "policy": "accessibility-filter-before-ranking",
        "extractor_sha256": sha256_path(REPO / "regen" / "scripts" / "regen_extract.py"),
        "record_count": len(records),
        "theory_count": len({record["theory"] for record in records}),
        "records_sha256": sha256_text(json.dumps(records, sort_keys=True)),
        "records": records,
    }
    write_json(campaign_dir / "premise_catalog.json", catalog)
    print(json.dumps({
        "catalog": str(campaign_dir / "premise_catalog.json"),
        "record_count": catalog["record_count"],
        "theory_count": catalog["theory_count"],
        "extractor_sha256": catalog["extractor_sha256"],
        "records_sha256": catalog["records_sha256"],
    }, indent=2))


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="build a session campaign and legal premise catalog")
    init.add_argument("--dag", type=Path, default=DEFAULT_DAG)
    init.add_argument("--out", type=Path, default=DEFAULT_OUT)
    init.add_argument("--session", default="Refine")
    init.add_argument("--arch", default="ARM")
    init.add_argument("--first-theory", default="Refine.LevityCatch")
    init.add_argument("--frozen-session", action="append", default=list(DEFAULT_FROZEN))
    init.set_defaults(func=init_campaign)

    recall = sub.add_parser("retrieve", help="rank premises after theory visibility filtering")
    recall.add_argument("--campaign-dir", type=Path, default=DEFAULT_OUT)
    recall.add_argument("--theory", required=True)
    recall.add_argument("--query", required=True)
    recall.add_argument("-k", type=int, default=20)
    recall.set_defaults(func=retrieve)

    refresh = sub.add_parser(
        "refresh-catalog",
        help="rebuild the premise catalog with the current extractor without resetting a campaign",
    )
    refresh.add_argument("--campaign-dir", type=Path, default=DEFAULT_OUT)
    refresh.set_defaults(func=refresh_catalog)
    return ap


def main() -> None:
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
