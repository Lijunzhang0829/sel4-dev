"""Reliable timing+correctness gate for A->B static-ization.

Produces lemma-level before/after artifacts suitable for audit:
- original lemma source
- per-line original timing + total lemma timing
- patched lemma source
- per-line patched timing + total lemma timing
- correctness verdict for the patched lemma
"""
import json
import os
import re
import subprocess
import tempfile

L4V = os.environ.get("L4V_DIR", "/sel4-project/verification/l4v")
ISA_HOME = os.environ.get("ISABELLE_HOME", "/workspace/verification/isabelle")
KW = os.environ.get(
    "ISAR_EXTRA_KEYWORDS",
    "/workspace/tools/seL4-proof-search/Isa-Repl/runs/l4v_keywords.json",
)
CHECK = os.environ.get(
    "CHECK_THEORY",
    "/workspace/.claude/skills/isabelle_prover/scripts-container/check-theory.sh",
)
# Resolve the IsarLite CLI robustly — it is NOT on the container's default PATH
# (a restart loses any runtime symlink), so a bare `isar` fails with FileNotFound
# and timing silently dies. The runtime ships an executable wrapper.
ISAR = os.environ.get("ISAR_BIN") or next(
    (p for p in ("/workspace/IsarLite-runtime/isar",) if os.path.exists(p)), "isar")
_TOP_RE = re.compile(r"^\s*(lemma|theorem|corollary|lemmas|definition)\b")


def _abs(thy):
    return thy if os.path.isabs(thy) else os.path.join(L4V, thy)


def _read_lines(thy):
    return open(_abs(thy), encoding="utf-8").read().splitlines()


def _line_text(lines, lineno):
    return lines[lineno - 1] if 1 <= lineno <= len(lines) else None


def find_proof_span(thy, proof_line):
    """Return the line span of the proof command beginning at proof_line."""
    lines = _read_lines(thy)
    start = proof_line
    txt = lines[start - 1]
    depth = txt.count("(") - txt.count(")")
    end = start
    while depth > 0 and end < len(lines):
        end += 1
        depth += lines[end - 1].count("(") - lines[end - 1].count(")")
    return start, end


def find_lemma_span(thy, lemma, proof_line):
    """Best-effort full lemma span from the statement to just before the next item."""
    lines = _read_lines(thy)
    exact = re.compile(rf"^\s*(lemma|theorem|corollary)\b.*(?<![\w']){re.escape(lemma)}\s*[:\[]")
    start = None
    for i in range(min(proof_line - 1, len(lines) - 1), -1, -1):
        if exact.search(lines[i]):
            start = i + 1
            break
    if start is None:
        for i in range(min(proof_line - 1, len(lines) - 1), -1, -1):
            if _TOP_RE.match(lines[i]):
                start = i + 1
                break
    if start is None:
        start = max(1, proof_line)
    end = len(lines)
    for i in range(start, len(lines)):
        if _TOP_RE.match(lines[i]):
            end = i
            break
    return start, end


def make_patch(start, end, replacement):
    return f"{start} {end}\n{replacement}\n---\n"


def render_patched_lines(src_lines, start, end, replacement):
    repl = replacement.split("\n")
    return src_lines[: start - 1] + repl + src_lines[end:]


def write_patched_theory(thy, start, end, replacement):
    abs_thy = _abs(thy)
    src = open(abs_thy, encoding="utf-8").read().split("\n")
    patched = render_patched_lines(src, start, end, replacement)
    d = os.path.dirname(abs_thy)
    base = os.path.basename(abs_thy)[:-4]
    new_base = f"{base}_GATEPATCH"
    new_abs = os.path.join(d, new_base + ".thy")
    body = "\n".join(patched)
    body = re.sub(rf"\btheory\s+{re.escape(base)}\b", f"theory {new_base}", body, count=1)
    open(new_abs, "w", encoding="utf-8").write(body)
    return os.path.relpath(new_abs, L4V), new_abs, body.splitlines()


def _cmd_text(row):
    for key in ("cmd", "command", "command_text", "text", "source"):
        val = row.get(key)
        if val:
            return val
    return None


def _as_float(v):
    return None if v is None else float(v)


def isar_lemma_profile(thy_rel, lemma, span, source_lines, reps=3, timeout=900):
    """Per-line and total timing for one lemma via `isar timing --lemma`."""
    out = tempfile.mktemp(suffix=".json")
    env = os.environ.copy()
    env.update(
        ISABELLE_HOME=ISA_HOME,
        ISAR_EXTRA_KEYWORDS=KW,
        ISAR_LOAD_DRAIN="450",
        ISAR_LOAD_QUIET="20",
        ISAR_TIMING_QUIET="20",
    )
    cmd = [
        ISAR,
        "-d",
        L4V,
        "timing",
        thy_rel,
        "--l4v",
        "--lemma",
        lemma,
        "--reps",
        str(reps),
        "--json",
        out,
        "--collect-timeout",
        "450",
    ]
    try:
        p = subprocess.run(cmd, cwd=L4V, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "total_ms": None,
            "line_timings": [],
            "raw_line_count": 0,
            "stderr_tail": "timeout",
        }
    if not os.path.exists(out):
        return {
            "status": "missing-json",
            "total_ms": None,
            "line_timings": [],
            "raw_line_count": 0,
            "stderr_tail": (p.stderr or p.stdout or "")[-160:],
        }
    data = json.load(open(out))
    os.remove(out)
    totals = data.get("lemma_totals_ms", {}) if isinstance(data, dict) else {}
    rows = [r for r in data.get("lines", []) if r.get("lemma") == lemma]
    total_ms = totals.get(lemma)
    if total_ms is None:
        vals = [_as_float(r.get("elapsed_median_ms")) for r in rows]
        vals = [v for v in vals if v is not None]
        total_ms = sum(vals) if vals else None
    by_line = {}
    for row in rows:
        line = int(row.get("line") or 0)
        if line <= 0:
            continue
        rec = by_line.setdefault(
            line,
            {
                "line": line,
                "text": _line_text(source_lines, line),
                "elapsed_ms": 0.0,
                "cpu_ms": 0.0,
                "timed": False,
                "commands": [],
            },
        )
        el = _as_float(row.get("elapsed_median_ms"))
        cpu = _as_float(row.get("cpu_median_ms"))
        if el is not None:
            rec["elapsed_ms"] += el
            rec["timed"] = True
        if cpu is not None:
            rec["cpu_ms"] += cpu
        cmd_txt = _cmd_text(row)
        if cmd_txt:
            rec["commands"].append(cmd_txt)
    line_timings = []
    for line in range(span[0], span[1] + 1):
        rec = by_line.get(
            line,
            {
                "line": line,
                "text": _line_text(source_lines, line),
                "elapsed_ms": None,
                "cpu_ms": None,
                "timed": False,
                "commands": [],
            },
        )
        if rec.get("timed") and total_ms:
            rec["frac_of_total"] = round(100.0 * rec["elapsed_ms"] / total_ms, 1)
        else:
            rec["frac_of_total"] = None
        line_timings.append(rec)
    status = "ok" if total_ms is not None else "lemma-not-timed"
    return {
        "status": status,
        "total_ms": _as_float(total_ms),
        "line_timings": line_timings,
        "raw_line_count": len(rows),
        "stderr_tail": (p.stderr or "")[-160:],
    }


def check_theory_builds(thy, session, start, end, replacement, timeout=900):
    pf = tempfile.mktemp(suffix=".patch")
    open(pf, "w").write(make_patch(start, end, replacement))
    try:
        p = subprocess.run(
            ["bash", CHECK, _abs(thy), session, "--patch", pf],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, None, "timeout"
    os.remove(pf)
    out = (p.stdout or "") + (p.stderr or "")
    m = re.search(r"OK\s*\((\d+)ms\)", out)
    ok = p.returncode == 0 and ("OK" in out)
    if ok:
        return True, (float(m.group(1)) if m else None), out.strip().split("\n")[-1][:120]
    return False, None, _extract_build_error(out)


def _extract_build_error(out):
    """The VALUABLE failure feedback for LLM self-correction. Isabelle prints the real
    diagnostic on `***`-prefixed lines (the error message AND the unsolved goal state),
    plus an `At command` location. The bare last line is usually just the location, which
    tells the model WHERE but not WHY -> useless for fixing. Capture the whole `***` block
    (+ Failed/Undefined/unification hints as fallback), capped."""
    lines = out.split("\n")
    star = [l.rstrip() for l in lines if l.lstrip().startswith("***")]
    if not star:
        star = [l.rstrip() for l in lines
                if re.search(r"Failed|Undefined|unif\w*|exception|Inner syntax|Type \w*error|No such|ambiguous",
                             l, re.I)]
    star = list(dict.fromkeys(star))   # dedup: check-theory echoes the block on stdout+stderr
    msg = "\n".join(star).strip()
    return (msg[:1000] if msg else (out.strip().split("\n")[-1] or "build failed")[:200])


def _lemma_bundle(span, lines, profile):
    return {
        "span": list(span),
        "text": "\n".join(lines[span[0] - 1 : span[1]]),
        "total_ms": profile.get("total_ms"),
        "time_status": profile.get("status"),
        "line_timings": profile.get("line_timings", []),
    }


def build_check(thy, lemma, session, proof_line, path):
    """FAST build-only verification for self-correction loops. Just patch + check-theory
    build; skips isar_lemma_profile (the expensive timing pass that evaluate() runs on every
    call). Returns the builds_ok / build_msg the loop needs to decide and feed back. Call the
    full evaluate() ONCE on the first script that builds, to get timing + final verdict."""
    proof_start, proof_end = find_proof_span(thy, proof_line)
    if not path:
        return {"builds_ok": False, "build_ms": None, "build_msg": "no static path",
                "verdict": "NO-PATH", "path": path}
    replacement = "\n".join("  " + t for t in path) + "\n  done"
    ok, build_ms, msg = check_theory_builds(thy, session, proof_start, proof_end, replacement)
    return {"builds_ok": ok, "build_ms": build_ms, "build_msg": msg, "path": path,
            "verdict": ("BUILDS" if ok else "REJECT-incorrect"),
            "proof_span": [proof_start, proof_end], "replacement": replacement}


def evaluate(thy, lemma, session, proof_line, path, reps=3):
    """Full gate with lemma-level source/timing bundles for before/after."""
    proof_start, proof_end = find_proof_span(thy, proof_line)
    lemma_start, lemma_end = find_lemma_span(thy, lemma, proof_line)
    src_lines = _read_lines(thy)
    replacement = None
    modified_count = 0
    if path:
        replacement = "\n".join("  " + t for t in path) + "\n  done"
        modified_count = 1
    v = {
        "lemma": lemma,
        "thy": thy,
        "proof_span": [proof_start, proof_end],
        "lemma_span": [lemma_start, lemma_end],
        "replacement": replacement,
        "modified_line_count": modified_count,
        "path": path,
    }

    orig_profile = isar_lemma_profile(thy, lemma, (lemma_start, lemma_end), src_lines, reps=reps)
    v["original_lemma"] = _lemma_bundle((lemma_start, lemma_end), src_lines, orig_profile)
    v["orig_ms"] = orig_profile.get("total_ms")

    if not path:
        v.update(
            builds_ok=False,
            build_ms=None,
            build_msg="no static path",
            modified_lemma=None,
            static_ms=None,
            time_status=[orig_profile.get("status"), None],
            verdict="NO-PATH",
            sped_up=None,
        )
        return v

    patched_rel, patched_abs, patched_lines = write_patched_theory(thy, proof_start, proof_end, replacement)
    delta = len(replacement.split("\n")) - (proof_end - proof_start + 1)
    patched_lemma_span = (lemma_start, lemma_end + delta)
    v["modified_lemma"] = {
        "span": list(patched_lemma_span),
        "text": "\n".join(patched_lines[patched_lemma_span[0] - 1 : patched_lemma_span[1]]),
        "total_ms": None,
        "time_status": None,
        "line_timings": [],
    }
    try:
        ok, build_ms, msg = check_theory_builds(thy, session, proof_start, proof_end, replacement)
        v.update(builds_ok=ok, build_ms=build_ms, build_msg=msg)
        if not ok:
            v.update(static_ms=None, time_status=[orig_profile.get("status"), None], verdict="REJECT-incorrect", sped_up=False)
            return v

        static_profile = isar_lemma_profile(patched_rel, lemma, patched_lemma_span, patched_lines, reps=reps)
        v["modified_lemma"] = _lemma_bundle(patched_lemma_span, patched_lines, static_profile)
        v["static_ms"] = static_profile.get("total_ms")
        v["time_status"] = [orig_profile.get("status"), static_profile.get("status")]
        if v["orig_ms"] is None or v["static_ms"] is None:
            v.update(verdict="INCONCLUSIVE-timing", sped_up=None)
            return v
        v["delta_pct"] = round(100 * (v["static_ms"] - v["orig_ms"]) / v["orig_ms"], 1) if v["orig_ms"] else None
        FLOOR = float(os.environ.get("GATE_MIN_MS", "50"))
        MARGIN = float(os.environ.get("GATE_MARGIN_MS", "20"))
        if v["orig_ms"] < FLOOR:
            v.update(
                verdict="INCONCLUSIVE-below-noise-floor",
                sped_up=None,
                note=f"orig {v['orig_ms']}ms < {FLOOR}ms floor — delta is timer noise",
            )
        elif v["static_ms"] < v["orig_ms"] - MARGIN:
            v.update(verdict="ACCEPT", sped_up=True)
        else:
            v.update(verdict="REJECT-no-speedup", sped_up=False)
        return v
    finally:
        if os.path.exists(patched_abs):
            os.remove(patched_abs)


# ---------- MULTI-LINE gate (rewrite M proof commands in ONE lemma) ----------

def _replacement(path, closes):
    """Static replacement for one command. A command that CLOSED the proof
    (B empty) gets a trailing `done`; an intermediate `apply` that left subgoals
    does NOT (the following commands consume them)."""
    body = "\n".join("  " + t for t in path)
    return body + ("\n  done" if closes else "")


def render_patched_lines_multi(src_lines, hunks):
    """Apply hunks [(start,end,repl),...] bottom-up so earlier line numbers stay valid."""
    out = list(src_lines)
    for start, end, repl in sorted(hunks, key=lambda h: -h[0]):
        out = out[: start - 1] + repl.split("\n") + out[end:]
    return out


def check_theory_builds_multi(thy, session, hunks, timeout=900):
    """check-theory.sh with a MULTI-block patch (it splits on `---`, applies bottom-up)."""
    pf = tempfile.mktemp(suffix=".patch")
    open(pf, "w").write("".join(make_patch(s, e, r) for s, e, r in hunks))
    try:
        p = subprocess.run(["bash", CHECK, _abs(thy), session, "--patch", pf],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, None, "timeout"
    os.remove(pf)
    out = (p.stdout or "") + (p.stderr or "")
    m = re.search(r"OK\s*\((\d+)ms\)", out)
    ok = p.returncode == 0 and ("OK" in out)
    return ok, (float(m.group(1)) if m else None), out.strip().split("\n")[-1][:120]


def evaluate_multiline(thy, lemma, session, line_paths, reps=3):
    """Gate M rewritten commands in one lemma. line_paths = [(proof_line, path, closes), ...]
    (only the audit-passing lines). Patches all together, builds once, times the whole
    lemma before/after — the credible per-lemma delta from rewriting all M lines."""
    lemma_start, lemma_end = find_lemma_span(thy, lemma, line_paths[0][0] if line_paths else 1)
    src_lines = _read_lines(thy)
    hunks = []
    per_line = []
    for proof_line, path, closes in line_paths:
        s, e = find_proof_span(thy, proof_line)
        repl = _replacement(path, closes)
        hunks.append((s, e, repl))
        per_line.append({"proof_line": proof_line, "proof_span": [s, e], "path": path,
                         "closes": closes, "replacement": repl})
    v = {
        "lemma": lemma, "thy": thy, "session": session,
        "lemma_span": [lemma_start, lemma_end],
        "modified_line_count": len(hunks), "lines": per_line,
    }
    orig_profile = isar_lemma_profile(thy, lemma, (lemma_start, lemma_end), src_lines, reps=reps)
    v["original_lemma"] = _lemma_bundle((lemma_start, lemma_end), src_lines, orig_profile)
    v["orig_ms"] = orig_profile.get("total_ms")
    if not hunks:
        v.update(builds_ok=False, build_ms=None, build_msg="no static path on any line",
                 modified_lemma=None, static_ms=None,
                 time_status=[orig_profile.get("status"), None], verdict="NO-PATH", sped_up=None)
        return v
    patched_all = render_patched_lines_multi(src_lines, hunks)
    total_delta = sum(len(r.split("\n")) - (e - s + 1) for s, e, r in hunks)
    patched_span = (lemma_start, lemma_end + total_delta)
    v["modified_lemma"] = {"span": list(patched_span),
                           "text": "\n".join(patched_all[patched_span[0] - 1: patched_span[1]]),
                           "total_ms": None, "time_status": None, "line_timings": []}
    ok, build_ms, msg = check_theory_builds_multi(thy, session, hunks)
    v.update(builds_ok=ok, build_ms=build_ms, build_msg=msg)
    if not ok:
        v.update(static_ms=None, time_status=[orig_profile.get("status"), None],
                 verdict="REJECT-incorrect", sped_up=False)
        return v
    # write the patched theory and time the patched lemma span
    abs_thy = _abs(thy); base = os.path.basename(abs_thy)[:-4]
    new_base = base + "_GATEPATCH"
    new_abs = os.path.join(os.path.dirname(abs_thy), new_base + ".thy")
    body = re.sub(rf"\btheory\s+{re.escape(base)}\b", f"theory {new_base}", "\n".join(patched_all), count=1)
    open(new_abs, "w", encoding="utf-8").write(body)
    try:
        patched_rel = os.path.relpath(new_abs, L4V)
        static_profile = isar_lemma_profile(patched_rel, lemma, patched_span, body.splitlines(), reps=reps)
        v["modified_lemma"] = _lemma_bundle(patched_span, body.splitlines(), static_profile)
        v["static_ms"] = static_profile.get("total_ms")
        v["time_status"] = [orig_profile.get("status"), static_profile.get("status")]
        if v["orig_ms"] is None or v["static_ms"] is None:
            v.update(verdict="INCONCLUSIVE-timing", sped_up=None); return v
        v["delta_pct"] = round(100 * (v["static_ms"] - v["orig_ms"]) / v["orig_ms"], 1) if v["orig_ms"] else None
        FLOOR = float(os.environ.get("GATE_MIN_MS", "50")); MARGIN = float(os.environ.get("GATE_MARGIN_MS", "20"))
        if v["orig_ms"] < FLOOR:
            v.update(verdict="INCONCLUSIVE-below-noise-floor", sped_up=None)
        elif v["static_ms"] < v["orig_ms"] - MARGIN:
            v.update(verdict="ACCEPT", sped_up=True)
        else:
            v.update(verdict="REJECT-no-speedup", sped_up=False)
        return v
    finally:
        if os.path.exists(new_abs): os.remove(new_abs)


if __name__ == "__main__":
    import sys

    thy, lemma, session, pline = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
    path = sys.argv[5:]
    print(json.dumps(evaluate(thy, lemma, session, pline, path or None), indent=1, ensure_ascii=False))
