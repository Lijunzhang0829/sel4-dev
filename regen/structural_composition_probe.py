#!/usr/bin/env python3
"""Standalone structural-composition probe for Schedule_R regeneration runs.

This is intentionally outside regen_incr.py.  It tests whether a stuck proof body
can be decomposed into checked prefixes so we can diagnose composition/search
failures without using the original human proof as a replay template.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

REPO = Path(os.environ.get("REPO", "/home/lijun/seL4-docker-main"))
CONTAINER = os.environ.get("L4V_CONTAINER", "sel4-l4v")
SESSION = os.environ.get("L4V_SESSION", "Refine")
CTR_CHECK = "/workspace/.claude/skills/isabelle_prover/scripts-container/check-theory.sh"
COMMAND_RE = re.compile(
    r'(?m)^\s*(?:apply|by|unfolding|using|supply|proof|subgoal|prefer|defer|done|qed)\b.*'
    r'(?:\n(?!\s*(?:apply|by|unfolding|using|supply|proof|subgoal|prefer|defer|done|qed)\b).*)*'
)


def sh(argv: list[str], timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, text=True, capture_output=True, timeout=timeout)


def reap_stale_probe_poly(max_age_s: int) -> None:
    """Kill only stale PolyML processes spawned by this standalone probe.

    Timeout wrappers have been observed to leave Isabelle/Poly children running.
    Keep this targeted to the structural-composition-probe report path so it does
    not interfere with unrelated Isabelle work in the container.
    """
    script = (
        'for p in $(pgrep -x poly); do '
        'et=$(ps -o etimes= -p $p | tr -d " "); '
        'args=$(ps -o args= -p $p); '
        f'[ "${{et:-0}}" -gt {int(max_age_s)} ] && '
        'case "$args" in *"/workspace/reports/regen/structural-composition-probe/"*) kill -9 $p;; esac; '
        'done; true'
    )
    sh(["docker", "exec", CONTAINER, "bash", "-lc", script])


def ctr_path(host_path: Path) -> str:
    return "/workspace/" + os.path.relpath(host_path, REPO)


def check_theory(host_path: Path, timeout_s: int) -> tuple[bool, int, str]:
    reap_stale_probe_poly(max(60, int(timeout_s) + 15))
    rel = os.path.relpath(host_path, REPO)
    cmd = [
        "docker", "exec", "-e", "L4V_DIR=/sel4-project/verification/l4v",
        "-e", "L4V_ARCH=ARM", "-e", f"CHECK_THEORY_TIMEOUT_S={int(timeout_s)}",
        CONTAINER, "bash", CTR_CHECK, f"/workspace/{rel}", SESSION,
    ]
    t0 = time.time()
    p = sh(cmd, timeout=timeout_s + 30)
    wall = int((time.time() - t0) * 1000)
    out = p.stdout + p.stderr
    if p.returncode in (124, 137):
        reap_stale_probe_poly(0)
    ok = p.returncode == 0 and "OK (" in out
    return ok, wall, out


def check_with_seal(host_path: Path, seal_run: Path, seal_id: int, timeout_s: int) -> tuple[bool, int, str]:
    reap_stale_probe_poly(max(60, int(timeout_s) + 15))
    sess = f"RegenSeal_{seal_id}"
    sdir = seal_run / "seals" / str(seal_id)
    thy_name = host_path.with_suffix("").name
    cmd = [
        "docker", "exec", "-e", "L4V_DIR=/sel4-project/verification/l4v",
        "-e", "L4V_ARCH=ARM", CONTAINER,
        "timeout", "--kill-after=10s", f"{int(timeout_s)}s",
        "/workspace/verification/isabelle/bin/isabelle", "process",
        "-l", sess, "-d", ctr_path(sdir), "-d", "/sel4-project/verification/l4v",
        "-T", ctr_path(host_path.with_suffix("")),
    ]
    t0 = time.time()
    p = sh(cmd, timeout=timeout_s + 30)
    wall = int((time.time() - t0) * 1000)
    out = p.stdout + p.stderr
    if p.returncode in (124, 137):
        out += f"\n*** killed after {timeout_s}s wall-time ceiling. Search-explosion guard."
        reap_stale_probe_poly(0)
    ok = p.returncode == 0 and not re.search(r'^\*\*\*', out, re.M)
    return ok, wall, out


def render(theory_name: str, imports: str, rendered_items: list[tuple[dict[str, Any], str]]) -> str:
    parts = [f"theory {theory_name}\nimports {imports}\nbegin\n"]
    in_arch = False
    for it, text in rendered_items:
        arch = bool(it.get("arch_context"))
        if arch and not in_arch:
            parts.append("\ncontext begin interpretation Arch . (* structural_probe *)\n")
            in_arch = True
        if not arch and in_arch:
            parts.append("\nend\n")
            in_arch = False
        parts.append("\n" + text.rstrip() + "\n")
    if in_arch:
        parts.append("\nend\n")
    parts.append("\nend\n")
    return "".join(parts)


def load_items(run: Path) -> list[dict[str, Any]]:
    plan = json.loads((run / "plan.json").read_text())
    return plan["items"] if isinstance(plan, dict) else plan


def load_locked_before(run: Path, item_name: str) -> list[tuple[dict[str, Any], str]]:
    locked_path = run / "locked.json"
    if not locked_path.exists():
        return []
    out = []
    for entry in json.loads(locked_path.read_text()):
        it = entry["item"]
        if it.get("name") == item_name:
            break
        out.append((it, entry["text"]))
    return out


def find_item(run: Path, item_name: str) -> tuple[int, dict[str, Any]]:
    for idx, it in enumerate(load_items(run)):
        if it.get("name") == item_name:
            return idx, it
    raise SystemExit(f"item not found in plan: {item_name}")


def find_body(run: Path, item_idx: int, item_name: str, body_no: int | None) -> Path:
    item_dirs = sorted((run / "items").glob(f"{item_idx:02d}-{item_name}"))
    if not item_dirs:
        item_dirs = sorted((run / "items").glob(f"*-{item_name}"))
    if not item_dirs:
        raise SystemExit(f"item directory not found for {item_name}")
    idir = item_dirs[0]
    if body_no is not None:
        p = idir / f"body-{body_no}.thy"
        if not p.exists():
            raise SystemExit(f"body file not found: {p}")
        return p
    bodies = sorted(idir.glob("body-*.thy"))
    if not bodies:
        raise SystemExit(f"no body-*.thy under {idir}")
    return bodies[-1]


def split_steps(body: str) -> list[str]:
    steps = [m.group(0).strip() for m in COMMAND_RE.finditer(body)]
    if steps and steps[-1] in {"done", "qed"}:
        steps = steps[:-1]
    return steps


def summarize(out: str) -> dict[str, Any]:
    if "killed after" in out or "Timeout" in out or "timeout" in out.lower():
        kind = "timeout"
    elif re.search(r"^\*\*\*", out, re.M):
        kind = "error"
    else:
        kind = "residual_or_unknown"
    lines = [ln for ln in out.splitlines() if ln.startswith("***") or "goal (" in ln or "Failed" in ln]
    return {"kind": kind, "excerpt": "\n".join(lines[:40]) or out[-1600:]}


def classify_step(step: str, out: str) -> list[str]:
    cats = []
    blob = step + "\n" + out
    if "corres_split" in blob or "corres_" in blob:
        cats.append("corres_composition")
    if "corres_cross" in blob or "cross_add_guard" in blob:
        cats.append("guard_plumbing")
    if re.search(r"\bwp\b|wpsimp|hoare", blob):
        cats.append("wp_search")
    if "state_relation" in blob or "tcb_at" in blob or "pspace" in blob:
        cats.append("state_relation_plumbing")
    if "word_log2" in blob or "Max" in blob or "bitmap" in blob or "lookupBitmapPriority" in blob:
        cats.append("bitmap_max_arithmetic")
    if "list_queue_relation" in blob or "ready_queues_relation" in blob or "tcbQueue" in blob:
        cats.append("heap_list_queue")
    return sorted(set(cats)) or ["ordinary"]


def _extract_named_facts(step: str, key: str) -> list[str]:
    m = re.search(rf"\b{re.escape(key)}:\s*([^)]*)", step, re.S)
    if not m:
        return []
    stop_words = {"intro", "intro!", "dest", "dest!", "elim", "elim!", "simp", "where"}
    facts = []
    for x in m.group(1).split():
        if x.rstrip(":") in stop_words:
            break
        if re.match(r"[A-Za-z_][A-Za-z0-9_'.]*$", x):
            facts.append(x)
    return facts


def repair_candidates(steps: list[str], bad_n: int) -> list[dict[str, Any]]:
    """Generate small local alternatives for the first bad generated prefix.

    These are generic repair operators over the generated attempt. They do not
    inspect or replay the original human proof.
    """
    if bad_n < 1 or bad_n > len(steps):
        return []
    bad = steps[bad_n - 1]
    prefix_before_bad = steps[:bad_n - 1]
    candidates: list[dict[str, Any]] = []

    if "fastforce" in bad:
        candidates.append({
            "name": "fastforce_to_clarsimp",
            "why": "Replace broad automation with clarsimp to expose the side-condition instead of timing out.",
            "steps": prefix_before_bad + ["apply clarsimp"],
        })
        candidates.append({
            "name": "fastforce_to_simp",
            "why": "Try a cheaper simplification-only step before semantic search.",
            "steps": prefix_before_bad + ["apply simp"],
        })
        intro = _extract_named_facts(bad, "intro") + _extract_named_facts(bad, "intro!")
        dest = _extract_named_facts(bad, "dest") + _extract_named_facts(bad, "dest!")
        if intro or dest:
            fact_bits = []
            if intro:
                fact_bits.append("intro: " + " ".join(intro))
            if dest:
                fact_bits.append("dest: " + " ".join(dest))
            candidates.append({
                "name": "fastforce_to_blast",
                "why": "Try a more bounded classical step over the same named facts.",
                "steps": prefix_before_bad + [f"apply (blast {' '.join(fact_bits)})"],
            })

    if bad_n >= 2:
        prev = steps[bad_n - 2]
        qmatch = re.search(r"Q'=\"([^\"]+)\"", prev)
        if "corres_cross_add_guard" in prev and qmatch and " and " in qmatch.group(1):
            guards = [g.strip() for g in qmatch.group(1).split(" and ") if g.strip()]
            split_steps = steps[:bad_n - 2]
            for guard in guards:
                split_steps.append(f"apply (rule_tac Q'=\"{guard}\" in corres_cross_add_guard)")
                if guard == "pspace_aligned'":
                    split_steps.append("apply (fastforce intro: pspace_aligned_cross)")
                elif guard == "pspace_distinct'":
                    split_steps.append("apply (fastforce intro: pspace_distinct_cross)")
                elif guard.startswith("tcb_at'"):
                    split_steps.append("apply (fastforce intro: tcb_at_cross dest: st_tcb_at_tcb_at)")
                else:
                    split_steps.append("apply clarsimp")
            candidates.append({
                "name": "split_conjunctive_cross_add_guard",
                "why": "Split one generated conjunctive cross guard into smaller checked guard obligations.",
                "steps": split_steps,
            })

    seen = set()
    unique = []
    for cand in candidates:
        key = "\n".join(cand["steps"])
        if key not in seen:
            unique.append(cand)
            seen.add(key)
    return unique


def main() -> None:
    ap = argparse.ArgumentParser(description="Checked-prefix structural-composition probe")
    ap.add_argument("--run", required=True, type=Path)
    ap.add_argument("--item", required=True)
    ap.add_argument("--body", type=int, default=None)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--prefix-timeout", type=int, default=45)
    ap.add_argument("--max-steps", type=int, default=12)
    ap.add_argument("--imports", default=None, help="override imports; default from task_spec.json")
    ap.add_argument("--seal-run", type=Path, default=None, help="run directory containing seals/<id>; if set, load heap instead of replaying locked prefix")
    ap.add_argument("--seal-id", type=int, default=1)
    ap.add_argument("--continue-after-error", action="store_true", help="keep checking longer prefixes after the first deterministic checker error")
    ap.add_argument("--repair", action="store_true", help="try local repair candidates for the first error/timeout prefix")
    ap.add_argument("--repair-timeout", type=int, default=None, help="checker timeout for repair candidates; defaults to --prefix-timeout")
    ap.add_argument("--repair-continue-steps", type=int, default=0, help="after a repair candidate checks, append this many original suffix steps and check again")
    args = ap.parse_args()

    run = args.run.resolve()
    idx, item = find_item(run, args.item)
    body_path = find_body(run, idx, args.item, args.body)
    body = body_path.read_text().strip()
    steps = split_steps(body)
    out_dir = (args.out or (REPO / "reports/regen/structural-composition-probe" / f"{run.name}-{args.item}"))
    out_dir.mkdir(parents=True, exist_ok=True)

    spec_path = run / "task_spec.json"
    spec = json.loads(spec_path.read_text()) if spec_path.exists() else {}
    if args.seal_run:
        imports = args.imports or f"RegenSeal_{args.seal_id}.Sealed_{args.seal_id}"
        locked = []
        seal_run = args.seal_run.resolve()
    else:
        imports = args.imports or spec.get("imports") or "Refine.Schedule_R"
        locked = load_locked_before(run, args.item)
        seal_run = None
    results: dict[str, Any] = {
        "run": str(run),
        "item": args.item,
        "item_index": idx,
        "body": str(body_path),
        "step_count": len(steps),
        "checked_prefixes": [],
        "policy": "No original human proof body/order is used; only generated body prefixes are checked.",
    }

    last_fast = None
    first_bad = None
    first_error = None
    for n in range(1, min(len(steps), args.max_steps) + 1):
        prefix = "\n  ".join(steps[:n])
        candidate_text = item["_canonical"] + "\n  " + prefix + "\n  oops"
        thy_name = f"StructProbe_{args.item}_{n}".replace("'", "_").replace("-", "_")
        thy_path = out_dir / f"{thy_name}.thy"
        thy_path.write_text(render(thy_name, imports, locked + [(item, candidate_text)]))
        if seal_run:
            ok, wall, checker_out = check_with_seal(thy_path, seal_run, args.seal_id, args.prefix_timeout)
        else:
            ok, wall, checker_out = check_theory(thy_path, args.prefix_timeout)
        (out_dir / f"check-{n}.log").write_text(checker_out)
        summary = summarize(checker_out)
        rec = {
            "prefix_steps": n,
            "ok": ok,
            "wall_ms": wall,
            "last_step": steps[n - 1],
            "categories": classify_step(steps[n - 1], checker_out),
            "summary": summary,
            "theory": str(thy_path),
            "log": str(out_dir / f"check-{n}.log"),
        }
        results["checked_prefixes"].append(rec)
        if ok:
            last_fast = rec
        elif summary["kind"] != "timeout" and first_error is None:
            first_error = rec
            if not args.continue_after_error:
                break
        if summary["kind"] == "timeout" and first_bad is None:
            first_bad = rec
            break

    results["last_fast_prefix"] = last_fast
    results["first_timeout_prefix"] = first_bad
    results["first_error_prefix"] = first_error

    repair_results = []
    bad_rec = first_bad or first_error
    if args.repair and bad_rec:
        bad_n = bad_rec["prefix_steps"]
        for cand in repair_candidates(steps, bad_n):
            cand_text = item["_canonical"] + "\n  " + "\n  ".join(cand["steps"]) + "\n  oops"
            safe_name = re.sub(r"[^A-Za-z0-9_]+", "_", cand["name"])
            thy_name = f"StructRepair_{args.item}_{bad_n}_{safe_name}".replace("'", "_").replace("-", "_")
            thy_path = out_dir / f"{thy_name}.thy"
            thy_path.write_text(render(thy_name, imports, locked + [(item, cand_text)]))
            timeout = args.repair_timeout or args.prefix_timeout
            if seal_run:
                ok, wall, checker_out = check_with_seal(thy_path, seal_run, args.seal_id, timeout)
            else:
                ok, wall, checker_out = check_theory(thy_path, timeout)
            log_path = out_dir / f"repair-{safe_name}.log"
            log_path.write_text(checker_out)
            summary = summarize(checker_out)
            cont_rec = None
            if ok and args.repair_continue_steps > 0:
                suffix = steps[bad_n:bad_n + args.repair_continue_steps]
                if suffix:
                    cont_steps = cand["steps"] + suffix
                    cont_text = item["_canonical"] + "\n  " + "\n  ".join(cont_steps) + "\n  oops"
                    cont_name = f"StructRepairCont_{args.item}_{bad_n}_{safe_name}".replace("'", "_").replace("-", "_")
                    cont_path = out_dir / f"{cont_name}.thy"
                    cont_path.write_text(render(cont_name, imports, locked + [(item, cont_text)]))
                    if seal_run:
                        cont_ok, cont_wall, cont_out = check_with_seal(cont_path, seal_run, args.seal_id, timeout)
                    else:
                        cont_ok, cont_wall, cont_out = check_theory(cont_path, timeout)
                    cont_log = out_dir / f"repair-cont-{safe_name}.log"
                    cont_log.write_text(cont_out)
                    cont_summary = summarize(cont_out)
                    cont_rec = {
                        "suffix_steps": len(suffix),
                        "ok": cont_ok,
                        "wall_ms": cont_wall,
                        "kind": cont_summary["kind"],
                        "summary": cont_summary,
                        "last_appended_step": suffix[-1],
                        "theory": str(cont_path),
                        "log": str(cont_log),
                    }
            repair_results.append({
                "name": cand["name"],
                "why": cand["why"],
                "ok": ok,
                "wall_ms": wall,
                "kind": summary["kind"],
                "summary": summary,
                "steps": cand["steps"],
                "theory": str(thy_path),
                "log": str(log_path),
                "continuation": cont_rec,
            })
    results["repair_candidates"] = repair_results
    (out_dir / "results.json").write_text(json.dumps(results, indent=2))
    readme = [
        f"# Structural Composition Probe: `{args.item}`",
        "",
        f"Source run: `{run}`",
        f"Generated body: `{body_path}`",
        f"Detected proof-command steps: {len(steps)}",
        "",
        "Policy: original human proof bodies/order are not used. This probe only checks prefixes of generated attempts, closed with `oops`.",
        "",
        "## Prefix Results",
        "",
    ]
    for rec in results["checked_prefixes"]:
        readme.append(f"- step {rec['prefix_steps']}: ok={rec['ok']} wall={rec['wall_ms']}ms categories={','.join(rec['categories'])} kind={rec['summary']['kind']}")
        readme.append(f"  last step: `{rec['last_step'][:180]}`")
    if first_error:
        readme.extend(["", "## First Error", "", f"Step {first_error['prefix_steps']}: `{first_error['last_step']}`"])
    if first_bad:
        readme.extend(["", "## First Timeout", "", f"Step {first_bad['prefix_steps']}: `{first_bad['last_step']}`"])
    if repair_results:
        readme.extend(["", "## Repair Candidates", ""])
        for rec in repair_results:
            readme.append(f"- `{rec['name']}`: ok={rec['ok']} wall={rec['wall_ms']}ms kind={rec['kind']}")
            readme.append(f"  why: {rec['why']}")
            if rec.get("continuation"):
                c = rec["continuation"]
                readme.append(f"  continuation(+{c['suffix_steps']}): ok={c['ok']} wall={c['wall_ms']}ms kind={c['kind']}")
    (out_dir / "README.md").write_text("\n".join(readme) + "\n")
    print(json.dumps({"out": str(out_dir), "checked": len(results["checked_prefixes"]), "first_error_step": first_error and first_error["prefix_steps"], "first_timeout_step": first_bad and first_bad["prefix_steps"], "repairs": len(repair_results)}, indent=2))


if __name__ == "__main__":
    main()
