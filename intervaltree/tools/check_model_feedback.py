#!/usr/bin/env python3
"""Replay a frozen model counterexample against Python and Isabelle.

This is an offline modeling audit, never a proof-generation input.  A successful
run establishes agreement on one concrete execution, including its *false*
postcondition.  It adds no positive target proof or universal equivalence claim.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys


PROJECT = Path(__file__).resolve().parents[1]
FIXTURE = PROJECT / "tests/fixtures/delete_balance_counterexample.json"
PYTHON_FILES = ("vendor/intervaltree/intervaltree/node.py",
                "vendor/intervaltree/intervaltree/interval.py")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_sources(fixture: dict):
    """Read only the pinned modules; no package init or optional dependencies."""
    if (PROJECT / "vendor/intervaltree/UPSTREAM_COMMIT").read_text().strip() != fixture["upstream_commit"]:
        raise ValueError("Vendored commit does not match the feedback fixture")
    locked = json.loads((PROJECT / "source-lock.json").read_text())["files"]
    modules = []
    for name in PYTHON_FILES:
        path = PROJECT / name
        actual = digest(path)
        if actual != fixture["python_source_sha256"].get(name) or actual != locked.get(name):
            raise ValueError(f"Pinned source hash mismatch: {name}")
        spec = importlib.util.spec_from_file_location("feedback_" + path.stem, path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        modules.append(module)
    return modules[0].Node, modules[1].Interval


def encode_tree(node):
    if node is None:
        return None
    return {"center": node.x_center, "entries": sorted([list(i) for i in node.s_center]),
            "left": encode_tree(node.left_node), "right": encode_tree(node.right_node),
            "height": node.depth, "balance": node.balance}


def canonical_tree(tree):
    if tree is None:
        return None
    return {**tree, "entries": sorted(tree["entries"]),
            "left": canonical_tree(tree["left"]), "right": canonical_tree(tree["right"])}


def tree_entries(tree):
    if tree is None:
        return set()
    return {tuple(i) for i in tree["entries"]} | tree_entries(tree["left"]) | tree_entries(tree["right"])


def python_replay(fixture: dict) -> dict:
    Node, Interval = load_sources(fixture)

    def construct(tree):
        if tree is None:
            return None
        node = Node(tree["center"], [Interval(*i) for i in tree["entries"]],
                    construct(tree["left"]), construct(tree["right"]))
        # Constructors may rotate. Require that the actual source state is the
        # frozen witness, rather than silently substituting a different tree.
        if encode_tree(node) != canonical_tree(tree):
            raise ValueError("Python construction changed the frozen input state")
        return node

    before = construct(fixture["initial_tree"])
    before.verify()
    initial = encode_tree(before)
    entry = tuple(fixture["operation"]["entry"])
    if fixture["operation"]["name"] != "remove":
        raise ValueError("Unsupported feedback operation")
    if entry not in tree_entries(initial):
        raise ValueError("Deleted entry is absent from the witness")
    after = before.remove(Interval(*entry))
    final = encode_tree(after)
    if final != canonical_tree(fixture["expected_final_tree"]):
        raise ValueError("Source execution no longer matches the frozen result")
    verify_error = None
    try:
        after.verify()
        post_wf = True
    except AssertionError as error:
        post_wf = False
        verify_error = str(error).splitlines()[0]
    if fixture["expected_pre_wf"] is not True or post_wf != fixture["expected_post_wf"]:
        raise ValueError("Source wellformedness observations changed")
    expected_content = tree_entries(initial) - {entry}
    content_matches = tree_entries(final) == expected_content
    points = []
    for point in fixture["query_points"]:
        answer = set()
        after.search_point(point, answer)
        observed = sorted([list(i) for i in answer])
        expected = sorted([list(i) for i in expected_content if i[0] <= point < i[1]])
        points.append({"point": point, "entries": observed, "set_model_matches": observed == expected})
    if not content_matches or not all(p["set_model_matches"] for p in points):
        raise ValueError("Source content/query observations differ from the fixture's set model")
    return {"pre_wf": True, "post_wf": post_wf, "post_verify_error": verify_error,
            "initial_tree": initial, "final_tree": final,
            "contents": sorted([list(i) for i in expected_content]),
            "content_matches_set_model": content_matches, "point_queries": points}


def integer(value: int) -> str:
    return str(value) if value >= 0 else f"({value})"


def entry_term(value) -> str:
    return f"(entry.make {integer(value[0])} {integer(value[1])} {value[2]})"


def entries_term(values) -> str:
    return "[" + ", ".join(map(entry_term, values)) + "]"


def tree_term(tree) -> str:
    if tree is None:
        return "Empty"
    return (f"(Branch {integer(tree['center'])} {entries_term(tree['entries'])} "
            f"{tree_term(tree['left'])} {tree_term(tree['right'])} "
            f"{tree['height']} {integer(tree['balance'])})")


def render_theory(fixture: dict, observed: dict) -> str:
    queries = "[" + ", ".join(f"({integer(q['point'])}, {entries_term(q['entries'])})"
                                 for q in observed["point_queries"]) + "]"
    # Entry lists represent Python sets: compare bucket sets while preserving
    # every center, branch, cached height and balance factor.
    return r'''theory Model_Feedback
  imports Interval_Tree
begin

text \<open>Offline finite execution audit. Historical witness data only;
no generated target proof or counterexample proof is imported here.\<close>

fun feedback_same_tree :: "itree \<Rightarrow> itree \<Rightarrow> bool" where
  "feedback_same_tree Empty Empty = True"
| "feedback_same_tree (Branch c s l r h b) (Branch c' s' l' r' h' b') =
    (c = c' \<and> set s = set s' \<and> h = h' \<and> b = b' \<and>
     feedback_same_tree l l' \<and> feedback_same_tree r r')"
| "feedback_same_tree _ _ = False"

''' + (
        'definition feedback_before :: itree where\n  "feedback_before = '
        + tree_term(observed["initial_tree"]) + '"\n\n'
        'definition feedback_expected_after :: itree where\n  "feedback_expected_after = '
        + tree_term(observed["final_tree"]) + '"\n\n'
        'definition feedback_run :: "itree option" where\n  "feedback_run = tree_remove '
        + str(fixture["operation"]["isabelle_fuel"]) + ' '
        + entry_term(fixture["operation"]["entry"]) + ' feedback_before"\n\n'
        'definition feedback_observations :: "bool list" where\n'
        '  "feedback_observations = [tree_wf feedback_before,\n'
        '    (case feedback_run of None \\<Rightarrow> False | Some t \\<Rightarrow>\n'
        '      feedback_same_tree t feedback_expected_after),\n'
        '    (case feedback_run of None \\<Rightarrow> True | Some t \\<Rightarrow> tree_wf t),\n'
        '    (case feedback_run of None \\<Rightarrow> False | Some t \\<Rightarrow>\n'
        '      tree_entries t = set ' + entries_term(observed["contents"]) + '),\n'
        '    (case feedback_run of None \\<Rightarrow> False | Some t \\<Rightarrow>\n'
        '      list_all (\\<lambda>(p, es). tree_at p t = set es) ' + queries + ')]"\n\n'
    ) + r'''ML_val \<open>
  val actual = Code_Evaluation.dynamic_value_strict @{context} @{term feedback_observations};
  val expected = @{term "[True, True, False, True, True]"};
  val _ = if actual aconv expected
    then writeln "INTERVALTREE_MODEL_FEEDBACK_MATCH: pre_wf=true post_wf=false"
    else error ("INTERVALTREE_MODEL_FEEDBACK_MISMATCH: " ^ Syntax.string_of_term @{context} actual);
\<close>

end
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--output-dir", type=Path, default=PROJECT / "build/model-feedback")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--generate-only", action="store_true")
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text())
    directory = args.output_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    result = {"schema": "intervaltree-model-feedback-result-v1", "status": "started",
              "fixture": str(args.fixture.resolve()), "fixture_sha256": digest(args.fixture),
              "recorded_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "provenance": fixture["provenance"], "python_source_sha256": fixture["python_source_sha256"],
              "translation_matches": None, "positive_fixed_proofs_added": 0,
              "generator_input": False, "scope": fixture["scope"],
              "limitation": "One finite execution; no universal translation equivalence or public API reachability proof."}
    try:
        observed = python_replay(fixture)
        result.update(python=observed, pre_wf=observed["pre_wf"], post_wf=observed["post_wf"])
        (directory / "Model_Feedback.thy").write_text(render_theory(fixture, observed))
        (directory / "ROOT").write_text(
            'session Intervaltree_Model_Feedback = HOL +\n  options [document=false]\n'
            f'  directories "{PROJECT / "theories"}"\n  theories Model_Feedback\n')
        result["generated_theory_sha256"] = digest(directory / "Model_Feedback.thy")
        result["uses_preparation_code_equations"] = True
        if args.generate_only:
            result["status"] = "generated_only"
            code = 0
        else:
            run = subprocess.run([sys.executable, str(PROJECT / "tools/build.py"),
                                  "--directory", str(directory), "--session", "Intervaltree_Model_Feedback",
                                  "--build-dir", str(directory / "check"), "--timeout", str(args.timeout)])
            build = json.loads((directory / "check/build-result.json").read_text())
            unchanged = all(digest(PROJECT / name) == value
                            for name, value in fixture["python_source_sha256"].items())
            passed = run.returncode == 0 and build["status"] == "passed" and unchanged
            result.update(status="passed" if passed else "failed", translation_matches=passed,
                          source_unchanged=unchanged, isabelle_build=build,
                          target_disposition="existing_refutation_retained")
            code = 0 if passed else 1
    except Exception as error:
        result.update(status="failed", error=f"{type(error).__name__}: {error}")
        code = 1
    (directory / "validation.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: result.get(k) for k in
                      ("status", "translation_matches", "pre_wf", "post_wf", "positive_fixed_proofs_added", "error")}, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
