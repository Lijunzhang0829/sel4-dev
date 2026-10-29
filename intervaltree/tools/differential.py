#!/usr/bin/env python3
"""Compare pinned Python results with actual Isabelle-generated executable code.

Python supplies observations only. The generated theory calls the translated
Isabelle implementations, evaluates them using Code_Evaluation, and fails its
session when a result differs. These finite checks are not universal proofs.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import random
import subprocess
import sys
import urllib.request
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
UPSTREAM = "1bc406e1f441577c4e421fc51aba2ab67fbd97fb"


def fetch_dependency() -> None:
    target = PROJECT / "build/python-deps"
    with urllib.request.urlopen("https://pypi.org/pypi/sortedcontainers/2.4.0/json", timeout=30) as stream:
        meta = json.load(stream)
    wheel = next(f for f in meta["urls"] if f["filename"].endswith(".whl"))
    with urllib.request.urlopen(wheel["url"], timeout=30) as stream:
        data = stream.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != wheel["digests"]["sha256"]:
        raise RuntimeError("Dependency download hash mismatch")
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for member in archive.infolist():
            relative = Path(member.filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError("Unsafe dependency archive member")
            path = target / relative
            if member.is_dir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.read(member))
    (target / "dependency-lock.json").write_text(json.dumps({
        "distribution": "sortedcontainers", "version": "2.4.0",
        "filename": wheel["filename"], "url": wheel["url"], "sha256": digest,
    }, indent=2) + "\n")


def integer(value: int) -> str:
    return str(value) if value >= 0 else f"({value})"


def entry(value: tuple[int, int, int] | list[int]) -> str:
    a, b, payload = value
    return rf"\<lparr>lo={integer(a)}, hi={integer(b)}, payload={payload}\<rparr>"


def interval(value: tuple[int, int]) -> str:
    a, b = value
    return rf"\<lparr>lo={integer(a)}, hi={integer(b)}\<rparr>"


def entries(values: list[tuple[int, int, int]]) -> str:
    return "set [" + ", ".join(map(entry, values)) + "]"


def entry_list(values: list[tuple[int, int, int]]) -> str:
    return "[" + ", ".join(map(entry, values)) + "]"


def triples(values) -> list[tuple[int, int, int]]:
    return sorted((v.begin, v.end, v.data) for v in values)


def generate(args: argparse.Namespace) -> tuple[Path, dict]:
    source_root = PROJECT / "vendor/intervaltree"
    if (source_root / "UPSTREAM_COMMIT").read_text().strip() != UPSTREAM:
        raise RuntimeError("Unexpected vendored intervaltree commit")
    source_hashes = {
        str(path.relative_to(PROJECT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((source_root / "intervaltree").glob("*.py"))
    }
    lock_path = PROJECT / "source-lock.json"
    if lock_path.exists():
        locked = json.loads(lock_path.read_text())["files"]
        for name, digest in source_hashes.items():
            if locked.get(name) != digest:
                raise RuntimeError(f"Pinned Python source hash mismatch: {name}")
    sys.path.insert(0, str(PROJECT / "build/python-deps"))
    sys.path.insert(0, str(PROJECT / "vendor/intervaltree"))
    try:
        import sortedcontainers
        from intervaltree import Interval, IntervalTree
    except ImportError as error:
        raise RuntimeError("Run with --fetch-deps once to install the isolated test dependency") from error
    if sortedcontainers.__version__ != "2.4.0":
        raise RuntimeError("Differential test requires sortedcontainers==2.4.0")
    origin = Path(sys.modules["intervaltree"].__file__).resolve()
    if not origin.is_relative_to((PROJECT / "vendor/intervaltree").resolve()):
        raise RuntimeError(f"Unpinned intervaltree imported from {origin}")

    # Includes promoted center buckets, predecessor pruning, double rotations,
    # equal geometry with distinct payloads, duplicate insertion, and removal.
    traces = [
        [("Insert", (0, 25, 0)), ("Insert", (10, 11, 1)), ("Insert", (20, 21, 2))],
        [("Insert", (10, 11, 0)), ("Insert", (0, 8, 1)),
         ("Insert", (0, 4, 2)), ("Insert", (20, 21, 3)), ("Delete", (10, 11, 0))],
        [("Insert", (0, 3, 0)), ("Insert", (4, 7, 1)), ("Insert", (2, 5, 2))],
        [("Insert", (5, 8, 0)), ("Insert", (0, 2, 1)), ("Insert", (3, 4, 2))],
        [("Insert", (-2, 4, 0)), ("Insert", (-2, 4, 1)),
         ("Insert", (-2, 4, 0)), ("Delete", (-2, 4, 0)), ("Discard", (7, 9, 3))],
    ]
    rng = random.Random(args.seed)
    for _ in range(args.random_traces):
        live: set[tuple[int, int, int]] = set()
        trace = []
        for _ in range(args.steps):
            if live and rng.random() < 0.35:
                item = rng.choice(sorted(live))
                live.remove(item)
                trace.append(("Delete", item))
            else:
                a = rng.randrange(-3, 12)
                item = (a, a + rng.randrange(1, 9), rng.randrange(4))
                live.add(item)
                trace.append(("Insert", item))
        traces.append(trace)

    points = [-4, -2, -1, 0, 1, 2, 3, 4, 5, 7, 8, 10, 11, 12, 20, 21, 24, 25]
    ranges = [(-4, 0), (-2, 4), (0, 3), (2, 5), (3, 9), (0, 25), (10, 21),
              (20, 25), (4, 4), (8, 2), (-5, 30)]
    fixtures = []
    expressions = []
    for trace_index, trace in enumerate(traces):
        tree = IntervalTree()
        prefix = []
        for step_index, (op, item) in enumerate(trace):
            python_iv = Interval(*item)
            if op == "Insert": tree.add(python_iv)
            elif op == "Delete": tree.remove(python_iv)
            else: tree.discard(python_iv)
            tree.verify()
            prefix.append((op, item))
            expected = triples(tree)
            observations = {
                "trace": trace_index, "step": step_index,
                "operations": list(prefix), "entries": expected,
                "at": [(p, triples(tree.at(p))) for p in points],
                "overlap": [(a, b, triples(tree.overlap(a, b))) for a, b in ranges],
                "envelop": [(a, b, triples(tree.envelop(a, b))) for a, b in ranges],
            }
            fixtures.append(observations)
            ops = "[" + ", ".join(op + " (" + entry(iv) + ")" for op, iv in prefix) + "]"
            boundary_pairs = sorted(tree.boundary_table.items())
            expected_boundaries = "[" + ", ".join(f"({integer(k)}, {n})" for k, n in boundary_pairs) + "]"
            at_expectations = "[" + ", ".join(f"({integer(p)}, {entry_list(answer)})" for p, answer in observations["at"]) + "]"
            range_expectations = []
            for name in ("overlap", "envelop"):
                range_expectations.append("[" + ", ".join(
                    f"(({integer(a)}, {integer(b)}), {entry_list(answer)})"
                    for a, b, answer in observations[name]) + "]")
            expressions.append(f"check_tree_fixture {args.fuel} {ops} {entry_list(expected)} "
                               f"{expected_boundaries} {at_expectations} " + " ".join(range_expectations))

    # The VERO algebra model is compared only on its declared source subset.
    model_fixtures = []
    for sample in [[], [(0, 2), (2, 4)], [(0, 9), (1, 2), (3, 7)],
                   [(-3, -1), (0, 4), (7, 10)], [(0, 2), (0, 2)]]:
        tree = IntervalTree(Interval(a, b) for a, b in sample)
        tree.merge_overlaps(strict=False)
        merged = sorted((i.begin, i.end) for i in tree)
        xs = "[" + ", ".join(map(interval, sample)) + "]"
        ys = "[" + ", ".join(map(interval, merged)) + "]"
        expressions.append(f"merge_overlaps {xs} = {ys}")
        model_fixtures.append({"input": sample, "merge": merged})

    directory = args.output_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    declarations = []
    for index, expression in enumerate(expressions):
        declarations.append(f'definition differential_case_{index} :: bool where\n  "differential_case_{index} \\<longleftrightarrow> {expression}"\n')
    names = ", ".join(f"differential_case_{i}" for i in range(len(expressions)))
    theory = (
        'theory Source_Differential\n  imports Interval_Tree\nbegin\n\n'
        'text \\<open>Generated finite differential tests against pinned Python.\n'
        'Code_Evaluation executes the Isabelle definitions; no theorem is claimed.\\<close>\n\n'
        + '''definition check_tree_fixture ::
  "nat \\<Rightarrow> tree_update list \\<Rightarrow> entry list \\<Rightarrow> (int \\<times> nat) list \\<Rightarrow>
   (int \\<times> entry list) list \\<Rightarrow> ((int \\<times> int) \\<times> entry list) list \\<Rightarrow>
   ((int \\<times> int) \\<times> entry list) list \\<Rightarrow> bool" where
  "check_tree_fixture fuel ops es bs ats ovs ens \\<longleftrightarrow>
    (case run_updates fuel ops empty_state of None \\<Rightarrow> False | Some s \\<Rightarrow>
      tree_wf (root_node s) \\<and> distinct (all_items s) \\<and>
      set (all_items s) = set es \\<and> tree_entries (root_node s) = set es \\<and>
      distinct (map fst (boundaries s)) \\<and> set (boundaries s) = set bs \\<and>
      list_all (\\<lambda>(p, answer). state_at p s = set answer) ats \\<and>
      list_all (\\<lambda>((a,b), answer). tree_overlap a b s = set answer) ovs \\<and>
      list_all (\\<lambda>((a,b), answer). tree_envelop a b s = set answer) ens)"

'''
        + "\n".join(declarations)
        + '\nML_val \\<open>\n'
        + f'  val actual = Code_Evaluation.dynamic_value_strict @{{context}} @{{term "[{names}]"}};\n'
        + f'  val expected = HOLogic.mk_list @{{typ bool}} (replicate {len(expressions)} @{{term True}});\n'
        + '  val _ = if actual aconv expected then writeln "INTERVALTREE_DIFFERENTIAL_PASSED"\n'
        + '    else error ("INTERVALTREE_DIFFERENTIAL_FAILED: " ^ Syntax.string_of_term @{context} actual);\n'
        + '\\<close>\n\nend\n'
    )
    (directory / "Source_Differential.thy").write_text(theory)
    (directory / "ROOT").write_text(
        'session Intervaltree_Differential = HOL +\n  options [document=false]\n'
        f'  directories "{PROJECT / "theories"}"\n  theories Source_Differential\n'
    )
    evidence = {
        "source_commit": UPSTREAM, "source_origin": str(origin),
        "python_source_sha256": source_hashes,
        "sortedcontainers_version": sortedcontainers.__version__,
        "seed": args.seed, "fuel": args.fuel, "tree_prefix_cases": len(fixtures),
        "model_cases": len(model_fixtures), "observations_per_tree_case": len(points) + 2 * len(ranges),
        "tree_fixtures": fixtures, "model_fixtures": model_fixtures,
        "scope": "Finite source differential tests; no Python-to-Isabelle equivalence theorem.",
    }
    (directory / "fixtures.json").write_text(json.dumps(evidence, indent=2) + "\n")
    evidence["generated_theory_sha256"] = hashlib.sha256(theory.encode()).hexdigest()
    return directory, evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch-deps", action="store_true")
    parser.add_argument("--generate-only", action="store_true")
    parser.add_argument("--seed", type=int, default=20261027)
    parser.add_argument("--random-traces", type=int, default=8)
    parser.add_argument("--steps", type=int, default=6)
    parser.add_argument("--fuel", type=int, default=256)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--output-dir", type=Path, default=PROJECT / "build/differential")
    args = parser.parse_args()
    if args.random_traces < 0 or args.steps < 1 or args.fuel < 1:
        parser.error("random-traces must be nonnegative; steps and fuel must be positive")
    if args.fetch_deps:
        fetch_dependency()
    directory, evidence = generate(args)
    print(json.dumps({k: v for k, v in evidence.items() if not k.endswith("fixtures")}, indent=2), flush=True)
    if args.generate_only:
        return 0
    return subprocess.run([
        sys.executable, str(PROJECT / "tools/build.py"), "--directory", str(directory),
        "--session", "Intervaltree_Differential", "--build-dir", str(directory / "check"),
        "--timeout", str(args.timeout),
    ]).returncode


if __name__ == "__main__":
    sys.exit(main())
