"""build_wall_test.py — does the search-space reduction move the END-TO-END PARALLEL build wall?

The per-line in-REPL/serial savings (~40 s of CPU on one command) only matter if they reduce the
real thing developers wait for: the parallel `isabelle build <SESSION>` wall. This builds the
session ORIGINAL vs with the reductions applied, default (parallel) threads, N reps each, and
compares the wall delta against the build-to-build wall NOISE (3 reps per arm). If the delta is
below the noise, the reduction does NOT move the build wall (the line is not on the critical
path / the saving is in the parallel slack) — Direction A is academically real but practically
marginal for build speed.

Forces a rebuild each rep by appending a unique comment to the target theory (changes the content
hash → Isabelle re-checks it + downstream). Run inside the l4v container.

Usage: python3 build_wall_test.py <session> <thy_rel> <reps>   (variants are hard-coded below)
"""
import os, sys, re, time, subprocess, shutil, statistics as st

L4V = os.environ.get("L4V_DIR", "/sel4-project/verification/l4v")
ISA = "/workspace/verification/isabelle/bin/isabelle"
SESSION, THY_REL, REPS = sys.argv[1], sys.argv[2], int(sys.argv[3])
THY = os.path.join(L4V, THY_REL)

# (line, end, replacement) hunks — both confirmed reductions in CNode_AC. end=line means 1-line cmd;
# the tool re-derives the command span by paren balance so we just give the start line + new text.
HUNKS = [
    (1103, ["  by (fastforce dest: pas_refined_Control pas_refined_mem[OF sta_cdt] "
            "pas_refined_mem[OF sta_cdt_transferable] all_childrenD is_transferable_all_children)"]),
    (1032, ["  apply (clarsimp simp: cte_wp_at_caps_of_state Option.is_none_def simp del: split_paired_All)",
            "  apply (fastforce split: if_split_asm simp: pas_refined_refl F[symmetric] valid_mdb_def2 "
            "mdb_cte_at_def simp del: split_paired_All dest: aag_cdt_link_Control aag_cdt_link_DeleteDerived "
            "cap_auth_caps_of_state)", "  done"]),
]


def span(lines, start):
    depth = lines[start - 1].count("(") - lines[start - 1].count(")"); end = start
    while depth > 0 and end < len(lines):
        end += 1; depth += lines[end - 1].count("(") - lines[end - 1].count(")")
    return start, end


def patched_lines(orig_lines):
    lines = list(orig_lines)
    for start, repl in sorted(HUNKS, key=lambda h: -h[0]):   # apply bottom-up to keep line nums
        s, e = span(lines, start)
        lines[s - 1:e] = repl
    return lines


def build_wall(tag, body_lines, rep):
    open(THY, "w", encoding="utf-8").write("\n".join(body_lines) + f"\n(* wallrep-{tag}-{rep} *)\n")
    env = os.environ.copy(); env["L4V_ARCH"] = env.get("L4V_ARCH", "ARM")
    t0 = time.time()
    p = subprocess.run([ISA, "build", "-d", L4V, SESSION], env=env, capture_output=True, text=True, timeout=10800)
    wall = time.time() - t0
    out = p.stdout + p.stderr
    base = os.path.basename(THY)[:-4]
    m = re.search(rf"Finished {re.escape(base)} \((\d+):(\d+):(\d+) elapsed", out)
    thy_el = (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))) if m else None
    print(f"[{tag} rep{rep}] rc={p.returncode} total_wall={wall:.0f}s  {base}_elapsed={thy_el}s", flush=True)
    return (wall, thy_el) if p.returncode == 0 else (None, None)


def main():
    orig = open(THY, encoding="utf-8").read().split("\n")
    bak = THY + ".wallbak"; shutil.copy(THY, bak)
    res = {"orig": [], "variant": []}
    try:
        for r in range(REPS):
            res["orig"].append(build_wall("orig", orig, r))
        var = patched_lines(orig)
        for r in range(REPS):
            res["variant"].append(build_wall("variant", var, r))
    finally:
        shutil.move(bak, THY)
    for arm in ("orig", "variant"):
        walls = [w for w, _ in res[arm] if w]; thys = [t for _, t in res[arm] if t]
        wm = st.median(walls) if walls else None
        tm = st.median(thys) if thys else None
        wn = (max(walls) - min(walls)) / wm * 100 if len(walls) > 1 and wm else None
        print(f"[{arm}] total_wall median={wm}s (noise={wn}%, samples={[round(w) for w in walls]})  "
              f"{os.path.basename(THY)[:-4]}_elapsed median={tm}s (samples={thys})", flush=True)
    ow = [w for w, _ in res["orig"] if w]; vw = [w for w, _ in res["variant"] if w]
    if ow and vw:
        d = st.median(ow) - st.median(vw)
        noise = max((max(ow) - min(ow)), (max(vw) - min(vw))) if (len(ow) > 1 and len(vw) > 1) else None
        print(f"\n[VERDICT] total-build-wall delta = {d:.0f}s "
              f"(orig {st.median(ow):.0f}s -> variant {st.median(vw):.0f}s); within-arm noise ~{noise}s. "
              f"{'ABOVE noise -> reduction MOVES the build wall.' if (noise and d > noise) else 'WITHIN noise -> reduction does NOT measurably move the build wall.'}", flush=True)


if __name__ == "__main__":
    main()
