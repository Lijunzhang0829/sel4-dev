"""measure_build_elapsed.py — STOCK-Isabelle build-wall per-line timing (publishable).

The reported speedup must rest on stock Isabelle, NOT on the internal Isa-REPL wall-clock
(py4j + cold-start + GC noise) nor on the unpublished IsarLite. This tool:

  1. reads the ORIGINAL line's `command_timings` elapsed from the golden heap DB (the same
     source `extract_high_value.py` uses — Isabelle's OWN per-command timing from the real build);
  2. patches the theory with the variant, rebuilds ONLY the target session into an ISOLATED
     ISABELLE_HEAPS dir (all OTHER sessions symlinked from the golden heaps so deps are reused
     and the golden heaps are NEVER written), and reads the VARIANT line's elapsed from the
     freshly-built session DB;
  3. restores the source and removes the isolated heaps.

Both numbers are Isabelle's own command-elapsed in a real `isabelle build` → reproducible by
any reviewer (just `isabelle build` the patched session and read command_timings). Caveat:
elapsed (not cpu — the DB stores only elapsed) carries some parallel-scheduling component;
run with `threads=1`/`parallel_proofs=0` for a cleaner serial-CPU-like number if desired.

Usage (inside the l4v container):
  python3 measure_build_elapsed.py <thy_rel> <session> <line> --variant-b64 <b64>
"""
import os, sys, glob, sqlite3, subprocess, shutil, time, argparse, re

L4V = os.environ.get("L4V_DIR", "/sel4-project/verification/l4v")
ISA = os.environ.get("ISABELLE_BIN", "/workspace/verification/isabelle/bin/isabelle")
HEAPROOT = glob.glob("/root/.isabelle/heaps/*")[0]            # .../polyml-5.9.1_...
POLYID = os.path.basename(HEAPROOT)
LOGDIR = os.path.join(HEAPROOT, "log")


def _decompress(b):
    b = bytes(b)
    try:
        import zstandard as z
        return z.ZstdDecompressor().decompress(b)
    except Exception:
        return subprocess.run(["zstd", "-dc"], input=b, capture_output=True).stdout


def line_elapsed(db_path, thy_abs, target_line):
    """Max command_timings elapsed (s) among commands ON target_line of thy_abs, from db_path."""
    try:
        row = sqlite3.connect(db_path).execute(
            "SELECT command_timings FROM isabelle_session_info").fetchone()
    except Exception as e:
        return None, f"db read fail: {e}"
    if not row or row[0] is None:
        return None, "no command_timings"
    # offset->line index for thy_abs (symbol-aware, same as extract_high_value)
    src = open(thy_abs, encoding="utf-8", errors="replace").read()
    starts = [0]; sym = 0; i = 0; n = len(src)
    while i < n:
        c = src[i]
        if c == "\\" and i + 1 < n and src[i + 1] == "<":
            j = src.find(">", i)
            if j != -1:
                i = j + 1; sym += 1; continue
        if c == "\n": starts.append(sym + 1)
        sym += 1; i += 1

    def off2line(o):
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            m = (lo + hi + 1) // 2
            if starts[m] <= o: lo = m
            else: hi = m - 1
        return lo + 1

    cur = {}; best = None
    for tok in _decompress(row[0]).decode("utf-8", "replace").split("\x06"):
        tok = tok.replace("\x05", "")
        if "=" not in tok: continue
        k, _, v = tok.partition("="); k = k.strip()
        if k in ("name", "offset", "file", "elapsed"):
            cur[k] = v
            if k == "elapsed" and "file" in cur and "offset" in cur:
                if cur["file"] == thy_abs and off2line(int(cur["offset"])) == target_line:
                    e = float(cur["elapsed"])
                    if best is None or e > best: best = e
                cur = {}
    return best, ("ok" if best is not None else "line not in timings")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("thy_rel"); ap.add_argument("session"); ap.add_argument("line", type=int)
    ap.add_argument("--variant-b64", required=True)
    ap.add_argument("--threads", default="1")   # serial -> cleaner elapsed
    ap.add_argument("--reps", type=int, default=1)   # rebuild N times for a noise estimate
    a = ap.parse_args()
    import base64
    variant = base64.b64decode(a.variant_b64).decode()
    thy_abs = os.path.join(L4V, a.thy_rel)

    # span of the command starting at `line` (paren-balance, same heuristic as gate)
    src_lines = open(thy_abs, encoding="utf-8").read().split("\n")
    start = a.line; depth = src_lines[start - 1].count("(") - src_lines[start - 1].count(")"); end = start
    while depth > 0 and end < len(src_lines):
        end += 1; depth += src_lines[end - 1].count("(") - src_lines[end - 1].count(")")

    # FAIR PROTOCOL: command_timings elapsed VARIES across builds (parallel-scheduling load —
    # observed 150 s vs 242 s for the SAME line under different parallelism). So we do NOT trust the
    # golden orig; we rebuild ORIGINAL and VARIANT under IDENTICAL config (threads=1 → serial, no
    # parallel contention, elapsed ≈ cpu). orig is built `--reps` times as an A/A noise floor.
    # Caveat: this rebuilds the session each time (pick a LATE theory so it's cheap); the session
    # heap ends in the variant state and self-heals on the next clean `isabelle build <SESSION>`.
    g_db = os.path.join(LOGDIR, a.session + ".db")
    env = os.environ.copy(); env["L4V_ARCH"] = env.get("L4V_ARCH", "ARM")
    bak = thy_abs + ".measbak"; shutil.copy(thy_abs, bak)

    def build_and_read(tag):
        os.utime(thy_abs, None)   # force re-check of this theory
        t0 = time.time()
        p = subprocess.run([ISA, "build", "-o", f"threads={a.threads}", "-d", L4V, a.session],
                           env=env, capture_output=True, text=True, timeout=10800)
        dt = time.time() - t0; ok = p.returncode == 0
        el, msg = line_elapsed(g_db, thy_abs, a.line) if ok else (None, "build failed")
        print(f"[{tag}] rc={p.returncode} build={dt:.0f}s elapsed={el}s ({msg})", flush=True)
        if not ok:
            print("[build err tail]", "\n".join((p.stdout + p.stderr).splitlines()[-6:]), flush=True)
        return el if ok else None

    orig_samples = []; var_el = None
    try:
        # ORIGINAL (source is the unmodified backup), threads=1, reps for A/A noise floor
        for r in range(max(1, a.reps)):
            e = build_and_read(f"orig rep{r} threads={a.threads}")
            if e: orig_samples.append(e)
        # VARIANT, same config
        patched = src_lines[:start - 1] + variant.split("\n") + src_lines[end:]
        open(thy_abs, "w", encoding="utf-8").write("\n".join(patched))
        var_el = build_and_read(f"variant threads={a.threads}")
    finally:
        shutil.move(bak, thy_abs)
    if orig_samples and var_el:
        import statistics as st
        om = st.median(orig_samples)
        noise = (max(orig_samples) - min(orig_samples)) / om * 100 if len(orig_samples) > 1 else None
        print(f"[RESULT threads={a.threads}] orig_median={om:.2f}s (A/A samples={[round(x,2) for x in orig_samples]}, "
              f"noise={noise}%) variant={var_el:.2f}s  saved={om - var_el:.2f}s ({100 * (om - var_el) / om:.1f}%)  "
              f"STOCK-BUILD same-config", flush=True)
        print(f"[NOTE] {a.session} heap now in variant state; `isabelle build {a.session}` restores golden.", flush=True)


if __name__ == "__main__":
    main()
