#!/usr/bin/env python3
"""regen_incr.py — incremental, lock-in regeneration driver (plan -> per-lemma).

Replaces the whole-theory-rewrite loop (regen.sh) whose failure mode was
measured on SubMonad_R: every round rewrites all ~15 declarations, gets a
DIFFERENT 5-15% wrong each time, so the union of per-round successes covers
everything but no single round is green (see reports/regen/submonad_r-20260701-235944).

Architecture (user-specified):
  [PLAN]   opus emits a STRUCTURE plan (ordered decls: fixed statements +
           its own helpers + verbatim defs; no proofs yet)      - claude -p, recorded
  [ITEMS]  per plan item: generate ONE proof body               - claude -p, recorded
           -> append to the growing prefix -> check-theory the prefix
           -> pass: LOCK (never regenerated)  fail: feed *** back, retry
           -> retries exhausted: REPLAN the item (helper: may revise/split
              its own statement; fixed: statement immutable, may only insert
              new helpers before it)                            - claude -p, recorded
  [FINAL]  all items green -> full-file check + anti-cheat gate
           (every fixed statement verbatim via the assembler, no sorry)
  [pre/post] recoverability: l4v source snapshot + byte-identity assert

Anti-cheat by construction: fixed statements and verbatim defs are rendered
from the keepset by the ASSEMBLER — the model only ever supplies proof bodies
and its own helper statements, so it cannot weaken/restate ground truth.

Usage:
  python3 regen/regen_incr.py SubMonad_R.thy [--model opus] [--item-tries 4]
          [--check-budget 80] [--timeout 1800]
"""
import argparse, hashlib, json, os, re, shutil, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "regen" / "scripts"))
sys.path.insert(0, str(REPO / "spec-strengthen" / "scripts"))
from spec_agent import find_claude, run_claude_streaming          # noqa: E402
from regen_triage import extract_undefined_facts                  # noqa: E402

REFINE_DIR = os.environ.get("REGEN_REFINE_DIR", "verification/l4v/proof/refine/ARM")
L4V_TREE = "verification/l4v"
SESSION = os.environ.get("REGEN_SESSION", "Refine")
CONTAINER = os.environ.get("REGEN_CONTAINER", "sel4-l4v")
CTR_CHECK = "/workspace/.claude/skills/isabelle_prover/scripts-container/check-theory.sh"

BODY_START = r'(apply|by|proof|using|unfolding|supply|including|subgoal|txt|have|show)\b'


# --------------------------------------------------------------------------- import-fact index
# The 3 hardest failures all cited machinery that EXISTS in imported theories
# (TcbAcc_R/KHeap_R/lib) — the model just cannot retrieve the names (tool-less).
# Imports are FIXED environment (same standing as the code artifacts), so
# injecting a relevant slice of import fact names+statements is legitimate
# context, not leakage of the deleted interior. The target theory itself is
# excluded.
def build_import_index(target_path):
    """Index facts from the target's full ANCESTOR import closure — resolving
    theory names to files ANYWHERE in l4v (lib/Monads, lib/, AInvs, ASpec...),
    not just proof/refine/ARM. The old version only looked in the refine dir, so
    the whole monad/Hoare library was invisible — the static pre-check then
    false-rejected real lemmas like hoare_strengthen_post. Downstream theories
    (which import the target) are excluded: their facts don't exist yet here."""
    from regen_extract import slice_decls
    l4v = REPO / "verification/l4v"
    # ARCH ISOLATION: theory basenames (Schedule_R, KHeap_R, ...) exist under
    # ARM/, RISCV64/, X64/, AARCH64/ — a naive first-hit map silently pulled
    # OTHER architectures' lemmas into the index (measured: bitmap_lookup_queue_
    # is_max_non_empty exists only in RISCV64/X64, AARCH64_H.* cited on an ARM
    # build → Undefined-fact stuck roots). Exclude non-target arch dirs entirely.
    arch = os.environ.get("L4V_ARCH", "ARM")
    OTHER_ARCH = {"ARM", "ARM_HYP", "RISCV64", "X64", "AARCH64"} - {arch}

    def wrong_arch(root):
        return any(f"/{a}/" in root + "/" or root.endswith(f"/{a}") for a in OTHER_ARCH)

    # basename -> path map over the tree, ARM-only; prefer /ARM/ paths on ties.
    name2path = {}
    for root, _, files in os.walk(l4v):
        if any(seg in root for seg in ("/.git", "/autocorres/tests", "/tools")):
            continue
        if wrong_arch(root):
            continue
        for f in files:
            if f.endswith(".thy"):
                cur = name2path.get(f[:-4])
                # prefer a path that explicitly carries the target arch
                if cur is None or (f"/{arch}/" in os.path.join(root, f) and f"/{arch}/" not in cur):
                    name2path[f[:-4]] = os.path.join(root, f)

    def imports_of(path):
        try:
            src = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            return []
        m = re.search(r'imports(.*?)begin', src, re.S)
        return [t.split(".")[-1] for t in
                re.findall(r'"?([A-Za-z][A-Za-z0-9_./]*)"?', m.group(1))] if m else []

    seen, queue, reachable = set(), [Path(target_path).stem], []
    while queue and len(reachable) < 2000:                 # bound the closure
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        p = name2path.get(name.split("/")[-1])
        if not p:
            continue
        reachable.append(p)
        for imp in imports_of(p):
            if imp not in seen:
                queue.append(imp)

    idx, consts = {}, set()
    for p in reachable:
        if os.path.abspath(p) == os.path.abspath(target_path):
            continue
        src = open(p, encoding="utf-8", errors="ignore").read()
        for d in slice_decls(src):
            if not d["name"]:
                continue
            if d["kind"] in ("lemma", "theorem", "corollary", "lemmas"):
                idx.setdefault(d["name"], " ".join(d["stmt"].split())[:200])
            elif d["kind"] in ("definition", "abbreviation", "fun", "primrec",
                               "function", "record", "datatype", "type_synonym"):
                consts.add(d["name"])
        # cheap extra: constant names from `consts`/`definition NAME ::` heads
        consts.update(re.findall(r'(?m)^\s*(?:definition|abbreviation)\s+([A-Za-z][A-Za-z0-9_\']+)', src))
    return idx, consts


def relevant_import_facts(index, stmt, err_txt=None, cap=24):
    toks = {t for t in re.findall(r"[A-Za-z_][A-Za-z0-9_']{4,}", stmt or "")}
    if err_txt:
        toks |= {t for t in re.findall(r"[A-Za-z_][A-Za-z0-9_']{5,}", err_txt)}
    toks -= {"lemma", "theorem", "shows", "assumes", "corres", "valid", "lbrace", "rbrace"}
    scored = []
    for name, s in index.items():
        hits = sum(1 for t in toks if t in name) * 3 + sum(1 for t in toks if t in s)
        if hits >= 3:
            scored.append((hits, name, s))
    scored.sort(reverse=True)
    top = scored[:cap]
    # IMPROVEMENT 2 — retrieval fallback: if the prover named a specific
    # Undefined fact that DOES exist in the index but relevance-ranking missed,
    # force it in (measured miss: st_tcb_at_tcb_at' on possibleSwitchTo_corres).
    if err_txt:
        have = {n for _, n, _ in top}
        for bad in re.findall(r'Undefined fact:\s*"([^"]+)"', err_txt):
            base = bad.split(".")[-1]
            for name, s in index.items():
                if name in have:
                    continue
                if name == bad or name == base or base in name:
                    top.insert(0, (999, name, s))
                    have.add(name)
                    if len([1 for h, _, _ in top if h == 999]) >= 6:
                        break
    return top[:cap + 6]


def precheck_facts(body, known_facts, known_consts, known_local, own_stmt):
    """Ultra-conservative static hallucination catch. Static parsing can't
    reliably tell a cited fact from a constant/def/fragment/instantiation, and a
    false reject SUPPRESSES A VALID PROOF (worse than the ~140s it saves). So we
    only flag a token that is ALL of:
      - in a clear FACT position (wp:/rule/intro:/dest:/elim:/OF/THEN) — NOT
        unfolding:/simp: (those take defs & constants freely),
      - a well-formed single fact identifier (letter start, no leading '_',
        proper l4v prime shape), not a def (_def) / qualified name,
      - absent from the lemma index, the constants/defs index, locals, own names,
      - with NO reasonable prefix match in the index (a genuine invention, not a
        near-miss the model can be nudged to).
    This catches st_tcb_at_tcb_at' (invented composite) while passing
    hoare_strengthen_post (indexed), valid_objs' (const), valid_objs'_maxDomain
    (indexed), and unfolding targets."""
    FACT_POS = re.compile(r'\b(?:wp|intro|dest|elim|rule|rule_tac|erule|drule|frule|'
                          r'OF|THEN|induct)\b[: ]([^\n\]]*)')
    cited = set()
    for m in FACT_POS.finditer(body):
        seg = m.group(1)
        # split on separators; keep full l4v identifiers incl a single trailing prime
        for tok in re.findall(r"[A-Za-z][A-Za-z0-9_]*'?(?:_[A-Za-z0-9_]+'?)*", seg):
            cited.add(tok)
    known = known_facts | known_consts | known_local \
        | set(re.findall(r"[A-Za-z][A-Za-z0-9_']+", own_stmt))
    SAFE = {"assms", "refl", "conjI", "impI", "allI", "exI", "ext", "TrueI",
            "sym", "trans", "mp", "iffD1", "iffD2", "ballI", "bexI", "notI",
            "ccontr", "disjE", "conjE", "subst", "cong", "arg_cong"}
    missing = {}
    for c in cited:
        if (c in known or c in SAFE or c.endswith("_def") or c.endswith("_defs")
                or '.' in c or c.startswith("_") or len(c) < 5):
            continue
        # near-match ⇒ not an invention, let the check surface it (cheap nudge)
        base = c.rstrip("'")
        if any(k.startswith(base[:12]) for k in known_facts):
            continue
        missing[c] = ""
    return missing


STRATEGY_HINTS = {
    "timeout": ("Your previous proof DID NOT TERMINATE (search explosion). This is "
                "almost always an over-broad `simp`/`auto`/`fastforce` with a large "
                "simp set or too many `split:` rules, or unfolding a big definition. "
                "Do the OPPOSITE of unfolding more: use TARGETED `rule`/`wp`/`corres_*` "
                "lemmas to discharge goals, keep `simp add:` sets minimal, avoid "
                "`fastforce`/`auto` on complex goals."),
    "same_goals": ("Two attempts reduced to the SAME residual goals — your current "
                   "top-level decomposition is a dead end. Do NOT tweak the same "
                   "script; switch STRATEGY: if you were unfolding defs, instead apply "
                   "a high-level combinator (corres_split / wp lemma) named in the "
                   "context; if you were combinator-driven, try unfolding one more "
                   "layer. Change the FIRST tactic."),
    "schematic": ("A goal still has an UNRESOLVED SCHEMATIC (?Q, ?P …) — your wp/rule "
                  "chain left a postcondition undetermined. Reorder so the schematic "
                  "is fixed before it's used: apply the wp rule that DETERMINES the "
                  "postcondition last, add `hoare_vcg_conj_lift`/`hoare_vcg_imp_lift` "
                  "glue, or give the intermediate assertion explicitly with `rule_tac "
                  "Q=... in ...`. Do not leave `?Q` for the final `clarsimp`."),
    "growing": ("Your subgoal count GREW versus the previous attempt — you are going "
                "the wrong direction (over-splitting/over-unfolding). The target is "
                "usually provable in a FEW steps; retreat to a shorter, more targeted "
                "script rather than expanding."),
}


# --------------------------------------------------------------------------- utils
def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def log(msg):
    print(msg, flush=True)


class QuotaExhausted(Exception):
    """The claude account hit its weekly/usage limit — NOT a proof failure.
    Must halt the whole run cleanly (parked items are not real failures) rather
    than burning every remaining item as `unparseable`/`empty`."""


def is_quota(raw):
    # FALSE-POSITIVE FIX: a proof body / reasoning text can legitimately contain
    # "quota"/"resets"/"upgrade", which used to halt a run mid-way despite ample
    # quota. Require the CLI's specific limit-exhaustion phrasing AND that the
    # whole response is short (a real quota notice is a brief system line; a real
    # generation is long and contains proof text).
    low = (raw or "").strip().lower()
    if len(low) > 400 or "lemma" in low or "apply" in low or "```" in low:
        return False
    strong = ("hit your weekly limit", "hit your usage limit", "usage limit reached",
              "you've hit your limit", "reached your limit", "rate limit exceeded")
    return any(m in low for m in strong)


def is_transient(raw):
    if not raw or not raw.strip():
        return True
    low = raw.lower()
    markers = ("api error", "overloaded", "529", "rate limit", "connection error",
               "connection closed", "connection reset", "service unavailable",
               "internal server error", "timed out")
    has_marker = any(m in low for m in markers)
    has_content = ("lemma" in low) or ("```" in raw) or ("\\<" in raw) or ("apply" in low)
    return has_marker and not has_content


def claude_call(prompt, model, timeout, raw_path, max_turns="3"):
    """One recorded claude -p call with transient-error backoff. tools=0.
    Raises QuotaExhausted if the account limit is hit (caller halts the run)."""
    claude = find_claude()
    argv = [claude, "-p", prompt, "--model", model, "--max-turns", max_turns,
            "--tools", "", "--output-format", "stream-json", "--verbose"]
    tries = int(os.environ.get("REGEN_API_TRIES", "6"))
    for t in range(1, tries + 1):
        try:
            out = run_claude_streaming(argv, timeout, raw_path=raw_path)
        except subprocess.TimeoutExpired:
            out = ""
            log(f"    [agent] timed out (try {t}/{tries})")
        if is_quota(out):
            raise QuotaExhausted(out.strip()[:200])
        if not is_transient(out):
            return out
        back = min(180, 15 * t)
        log(f"    [agent] transient API failure (try {t}/{tries}); backoff {back}s")
        time.sleep(back)
    return ""


def fenced(text, kind_hint="json"):
    m = re.search(r'```(?:%s|isabelle|isar|thy)?\s*\n(.*?)```' % kind_hint, text, re.S)
    return (m.group(1) if m else text).strip()


def summarize_errors(out):
    """Structured feedback instead of a raw dump. Returns (n_blocks, summary, sig).
    Old behavior fed the first ~30 *** lines — usually ONE giant unfolded subgoal —
    so the model never saw the error LANDSCAPE. Now: every block gets a one-line
    entry (header + At-command), the FIRST block is included in full, and a
    normalized signature detects no-progress across tries."""
    err_lines = [l for l in out.splitlines() if l.startswith("***")]
    blocks, cur = [], []
    for l in err_lines:
        cur.append(l)
        if l.startswith("*** At command"):
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    norm = [re.sub(r'\(line \d+ of "[^"]*"\)', '', l) for l in err_lines]
    sig = hashlib.sha256("\n".join(norm).encode()).hexdigest()[:12]
    if not blocks:
        return 0, out[-1500:], sig
    first = "\n".join(blocks[0][:40])
    others = []
    for b in blocks[1:]:
        at = b[-1] if b[-1].startswith("*** At command") else ""
        others.append((b[0][:140] + "  " + at[:80]).strip())
    summary = f"{len(blocks)} error block(s) total.\nFIRST BLOCK (full):\n{first}"
    if others:
        summary += "\n\nREMAINING BLOCKS (one line each):\n" + "\n".join(others[:15])
    return len(blocks), summary, sig


# --------------------------------------------------------------------------- check-theory
def localize_timeout(item, body, locked, verify_of, idir, budget_s=120, max_probes=4):
    """Bisect the apply-steps of a timed-out `body` to find the first step whose
    cumulative prefix does not finish within budget_s. Returns a one-line hint
    naming the culprit step, or None. Each probe is a fast check (residual-goal
    errors return quickly; only a genuine explosion hits the short cap)."""
    stmt = item["_canonical"]
    steps = re.findall(r'(?m)^\s*(?:apply|by|unfolding|using|supply|proof|subgoal)\b.*'
                       r'(?:\n(?!\s*(?:apply|by|unfolding|using|supply|proof|subgoal|done|qed)\b).*)*',
                       body)
    if len(steps) < 2:
        return None
    lo, hi, culprit = 1, len(steps), None
    probes = 0
    while lo <= hi and probes < max_probes:
        mid = (lo + hi) // 2
        probes += 1
        prefix_body = "\n  ".join(steps[:mid])
        # close with `oops`? no — use a truncated script; residual goals -> fast fail
        cand = (item, stmt + "\n  " + prefix_body)
        try:
            ok, wall, out = verify_of(cand, budget_s)
        except Exception:
            break
        if "killed after" in out:          # explosion already within first `mid` steps
            culprit, hi = mid, mid - 1
        else:                               # first `mid` steps are fast
            lo = mid + 1
    if culprit:
        (idir / "timeout-localized.txt").write_text(f"step {culprit}: {steps[culprit-1][:200]}")
        return (f"LOCALIZED: the explosion is at proof step #{culprit}: "
                f"`{steps[culprit-1].strip()[:120]}` — replace THIS step.")
    return None


def check_theory(host_path, timeout_s=None):
    """Run container check-theory on a repo-relative path. Returns (ok, wall_ms, output)."""
    # orphan reaper: driver deaths (server restart, kill -9) leave the container's
    # polyml children spinning at 100% CPU forever (observed: 7 orphans x 26h x
    # ~3GB nearly OOM'd the box). Any poly older than the 600s check ceiling +
    # margin is by definition dead weight — reap before starting a new check.
    sh(["docker", "exec", CONTAINER, "bash", "-c",
        'for p in $(pgrep -x poly); do et=$(ps -o etimes= -p $p | tr -d " "); '
        '[ "$et" -gt 900 ] && kill -9 $p; done; true'])
    rel = os.path.relpath(host_path, REPO)
    cmd = ["docker", "exec", "-e", "L4V_DIR=/sel4-project/verification/l4v",
           "-e", "L4V_ARCH=ARM"]
    if timeout_s:
        cmd += ["-e", f"CHECK_THEORY_TIMEOUT_S={int(timeout_s)}"]
    cmd += [CONTAINER, "bash", CTR_CHECK, f"/workspace/{rel}", SESSION]
    p = sh(cmd)
    out = p.stdout + p.stderr
    ok = (p.returncode == 0) and ("OK (" in out)
    m = re.search(r'(?:OK|FAILED) \((\d+)ms\)', out)
    return ok, (int(m.group(1)) if m else None), out


# --------------------------------------------------------------------------- incremental heap sealing (opt-in --incremental)
# Spike-confirmed: build prefix→heap once (~70s), then load-heap+check-new-lemma
# ~12-20s vs ~141s reprocessing the whole prefix. Kills the O(N²) check wall.
def _ctr_path(host_path):
    return "/workspace/" + os.path.relpath(host_path, REPO)


def seal_prefix(exp, header_comment, imports, sealed_items, seal_id):
    """isabelle build the sealed portion into a heap. Returns a seal dict
    {sess, thy, dir} or None. Sealed theory self-closes arch context."""
    sess, thy = f"RegenSeal_{seal_id}", f"Sealed_{seal_id}"
    sdir = exp / "seals" / str(seal_id)
    sdir.mkdir(parents=True, exist_ok=True)
    # qualify bare imports with the parent session (build won't auto-qualify like
    # check-theory does): KHeap_R -> Refine.KHeap_R
    qimports = " ".join(t if "." in t else f"{SESSION}.{t}" for t in imports.split())
    (sdir / f"{thy}.thy").write_text(
        render(header_comment, thy, qimports, sealed_items, close_open_arch=True))
    (sdir / "ROOT").write_text(f"session {sess} = {SESSION} +\n  theories\n    {thy}\n")
    t0 = time.time()
    p = sh(["docker", "exec", "-e", "L4V_DIR=/sel4-project/verification/l4v",
            "-e", "L4V_ARCH=ARM", CONTAINER,
            "/workspace/verification/isabelle/bin/isabelle", "build",
            "-d", _ctr_path(sdir), "-d", "/sel4-project/verification/l4v", "-b", sess])
    (sdir / "build.log").write_text(p.stdout + p.stderr)
    if p.returncode != 0:
        return None
    return {"sess": sess, "thy": thy, "cdir": _ctr_path(sdir),
            "build_ms": int((time.time() - t0) * 1000)}


def check_incremental(exp, tbase, seal, unsealed_items, cand, timeout_s=None):
    """Load sealed prefix heap + process ONLY [unsealed tail + candidate].
    cand = (item, text). Returns (ok, wall_ms, output)."""
    wdir = exp / "work"
    wdir.mkdir(parents=True, exist_ok=True)
    wname = f"Work_{tbase}"
    body = render("(* incremental working theory *)", wname,
                  f"{seal['sess']}.{seal['thy']}",
                  list(unsealed_items) + [cand], close_open_arch=True)
    (wdir / f"{wname}.thy").write_text(body)
    tcap = int(timeout_s or 600)
    cmd = ["docker", "exec", "-e", "L4V_DIR=/sel4-project/verification/l4v",
           "-e", "L4V_ARCH=ARM", CONTAINER,
           "timeout", "--kill-after=10s", f"{tcap}s",
           "/workspace/verification/isabelle/bin/isabelle", "process",
           "-l", seal["sess"], "-d", seal["cdir"],
           "-d", "/sel4-project/verification/l4v", "-T", _ctr_path(wdir / wname)]
    t0 = time.time()
    p = sh(cmd)
    dt = int((time.time() - t0) * 1000)
    out = p.stdout + p.stderr
    if p.returncode in (124, 137):
        out += f"\n*** killed after {tcap}s wall-time ceiling. Search-explosion guard."
    ok = (p.returncode == 0) and not re.search(r'^\*\*\*', out, re.M)
    return ok, dt, out


# --------------------------------------------------------------------------- assembly
def render(header_comment, theory_name, imports, rendered_items, close_open_arch=True):
    """Deterministic file from item list. Consecutive arch_context items are
    wrapped in one `context begin interpretation Arch .` block."""
    parts = [header_comment.rstrip() + "\n" if header_comment else "",
             f"theory {theory_name}\nimports {imports}\nbegin\n"]
    in_arch = False
    for it, text in rendered_items:
        arch = bool(it.get("arch_context"))
        if arch and not in_arch:
            parts.append("\ncontext begin interpretation Arch . (*FIXME: arch_split*)\n")
            in_arch = True
        if not arch and in_arch:
            parts.append("\nend\n")
            in_arch = False
        parts.append("\n" + text.rstrip() + "\n")
    if in_arch and close_open_arch:
        parts.append("\nend\n")
    parts.append("\nend\n")
    return "".join(parts)


def item_start_line(header_comment, theory_name, imports, rendered_items):
    """1-based line where the LAST item's text begins in the render."""
    prefix = render(header_comment, theory_name, imports, rendered_items[:-1],
                    close_open_arch=False)
    # crude but exact enough: count lines of everything before last item text
    full = render(header_comment, theory_name, imports, rendered_items,
                  close_open_arch=False)
    last_text = rendered_items[-1][1].rstrip().splitlines()[0]
    # find last occurrence of the first line of the last item
    lines = full.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == last_text.strip():
            return i + 1
    return max(1, len(prefix.splitlines()))


# --------------------------------------------------------------------------- prompts
def plan_prompt(spec):
    fixed = "\n\n".join(k["statement"] for k in spec["to_prove"])
    defs = "\n\n".join(k["text"] for k in spec.get("verbatim", []))
    return f"""You are planning the regeneration of the seL4 Isabelle/HOL theory
{spec['theory_name']} (imports: {spec['imports']}). The abstract spec and the
Haskell executable spec are fixed and reachable through the imports. You will
NOT write proofs now — you output a STRUCTURE PLAN only.

GROUND TRUTH you must build toward (statements verbatim, all must be proved):

{fixed}

VERBATIM DEFINITIONS that must appear (they reference ARM_H record accessors
like atcbContextGet — decide whether they need an `interpretation Arch` context):

{defs}

Output a JSON object (in one ```json fence) with:
{{"items": [
   {{"kind": "verbatim_def"|"fixed"|"helper",
     "name": "<decl name; for the two defs: asUser_fetch / asUser_replace>",
     "statement": "<ONLY for kind=helper: the full lemma header incl name, attrs and statement>",
     "arch_context": true|false,
     "why": "<1 line: role + expected proof approach>"}} ...]}}

Rules:
- Every fixed statement and both verbatim defs appear EXACTLY once, referenced by name.
- Order = the order they will be proved; a decl may only use facts from items before it.
- Add your OWN helper lemmas where they make the fixed proofs tractable (submonad_fn
  reformulations, threadGet/threadSet bridges, record-accessor simp rules, ...). You
  are re-deriving the interior from scratch — do not assume the original file's helpers exist.
- Prefer session-qualified fact names in your mental proof sketches (e.g. ARM_H.atcbContextGet_def).
- No proofs, no prose outside the JSON fence."""


def rank_prefix(proved, stmt, err_txt=None, cap=40):
    """RepoCoder-style retrieval over the LOCKED prefix instead of dumping all of
    it. Full-dump was O(N) prompt growth + 'lost in the middle' (relevant facts
    buried among 100+). Rank by token overlap with the target statement (and the
    current error), keep the most recent K unconditionally (locality), plus the
    top relevance matches."""
    if len(proved) <= cap:
        return proved
    toks = {t for t in re.findall(r"[A-Za-z_][A-Za-z0-9_']{4,}", stmt or "")}
    if err_txt:
        toks |= {t for t in re.findall(r"[A-Za-z_][A-Za-z0-9_']{5,}", err_txt)}
    recent = proved[-8:]                       # locality: last few always in
    pool = proved[:-8]
    scored = sorted(pool, key=lambda ns: -(sum(1 for t in toks if t in ns[0]) * 3
                                           + sum(1 for t in toks if t in ns[1])))
    keep = scored[:cap - len(recent)]
    # preserve original (dependency) order among the kept set
    kept = set(id(x) for x in keep) | set(id(x) for x in recent)
    return [x for x in proved if id(x) in kept]


def _name_tokens(name):
    # split camelCase + underscores; drop generic filler
    toks = re.findall(r'[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])', name or "")
    return {t.lower() for t in toks} - {"tcb", "the", "of", "to", "in", "s", "wp"}


def sibling_examples(item, locked, cap=2, max_lines=26):
    """Reflexion-style few-shot: find already-LOCKED proofs in the same family as
    the target (name/suffix overlap) and return their full text as patterns. A
    proven sibling (e.g. tcbSchedEnqueue_corres locked → tcbSchedAppend_corres) is
    the strongest zero-human seed for the deep orchestration walls. Pure machine:
    the sibling was itself machine-proved."""
    tgt = item.get("name") or ""
    tt = _name_tokens(tgt)
    if not tt:
        return []
    # suffix signal: same trailing family (_corres, _valid_bitmapQ, _invs', ...)
    tsuf = re.search(r"(_[A-Za-z0-9']+'?)$", tgt)
    tsuf = tsuf.group(1) if tsuf else None
    scored = []
    for it, text in locked:
        n = it.get("name")
        if not n or n == tgt or it["kind"] == "verbatim_def":
            continue
        st = _name_tokens(n)
        score = len(tt & st) * 2
        if tsuf and n.endswith(tsuf):
            score += 4
        if score >= 4:                     # need real family kinship
            scored.append((score, len(text), n, text))
    scored.sort(key=lambda x: (-x[0], x[1]))   # best kinship, then shortest proof
    out = []
    for _, _, n, text in scored[:cap]:
        lines = text.splitlines()
        if len(lines) > max_lines:
            lines = lines[:max_lines] + ["  (* ... *)"]
        out.append("\n".join(lines))
    return out


def item_prompt(spec, item, proved, body_hint=None, prev_body=None, errors=None,
                human_hint=None, template_body=None, import_facts=None,
                siblings=None):
    # RETRIEVED prefix context (was the full dump): relevant locked facts only.
    stmt = item["_canonical"]
    sel = rank_prefix(proved, stmt, errors)
    ctx = "\n".join(f"  {n}: {s}" for n, s in sel) or "  (none yet)"
    if len(sel) < len(proved):
        ctx += f"\n  (+{len(proved)-len(sel)} more locked facts not shown — ask by pattern if needed)"
    defs = "\n\n".join(k["text"] for k in spec.get("verbatim", []))
    defs_sec = (f"\nDefinitions present in this theory (you may unfold them, "
                f"e.g. asUser_fetch_def):\n{defs}\n" if defs else "")
    p = f"""You are proving ONE lemma inside the seL4 Isabelle/HOL theory
{spec['theory_name']} (imports: {spec['imports']}; session {SESSION} context).
Already proved earlier in this theory and usable as facts:
{ctx}
{defs_sec}
Target (statement is FIXED, do not restate or alter it):

{stmt}

Output ONLY the proof script that follows this statement — starting with
`apply`, `by`, `proof`, `using`, `unfolding` or `supply`, ending with `done`,
`qed`, or a terminal `by`. No lemma header, no theory header, no prose,
in one ```isabelle fence. No sorry/oops.
Plan note for this item: {item.get('why','')}"""
    if prev_body and errors:
        p += f"""

--- PREVIOUS PROOF ATTEMPT FAILED ---
{errors[:4000]}

Previous proof script:
```isabelle
{prev_body[:4000]}
```
Fix it (or take a different route). Same output rules."""
    if siblings:
        ex = "\n\n".join(f"```isabelle\n{e}\n```" for e in siblings)
        p += (f"\n\nSIMILAR LEMMAS ALREADY PROVED in this run (same family — reuse "
              f"their PROOF STRUCTURE/tactics as a template; adapt names to the "
              f"target):\n{ex}")
    if import_facts:
        fx = "\n".join(f"  {n}: {s}" for _, n, s in import_facts)
        p += (f"\n\nPossibly relevant facts from IMPORTED theories (fixed "
              f"environment, cite freely — prefer these over unfolding raw "
              f"definitions):\n{fx}")
    if body_hint:
        p += f"\n\nHint: {body_hint}"
    if human_hint:
        p += (f"\n\nHUMAN STRATEGY HINT (provided by a human reviewer after the "
              f"automated loop stalled; recorded in the audit trail):\n{human_hint}")
    if template_body:
        p += (f"\n\nTEMPLATE ARM — the HUMAN ORIGINAL proof of this statement is "
              f"given as a template; you may reuse or adapt it (this run measures "
              f"the template-assisted upper bound):\n```isabelle\n{template_body}\n```")
    return p


def critique_prompt(spec, items):
    fixed = [it for it in items if it["kind"] == "fixed"]
    helpers = [it for it in items if it["kind"] == "helper"]
    stmts = "\n\n".join(it["_canonical"] for it in fixed)
    planned = "\n".join(f"  {h.get('name')}: serves {h.get('why','')[:80]}" for h in helpers) or "  (none)"
    return f"""You just produced a structure plan for regenerating seL4 theory
{spec['theory_name']}. Now CRITIQUE its scaffolding: proof needs are usually
underestimated at plan time (a prior run planned 6 helpers but needed 23).

Already-planned helpers:
{planned}

For each FIXED statement below, judge: directly provable with wp/corres
machinery from the imports, or does it need scaffolding lemmas (invariant
bridges, queue/bitmap machinery, crunch-style preservation facts)? Declare the
scaffolding NOW — full Isabelle statements, each with the consumer it serves.

{stmts}

Reply one ```json fence:
{{"scaffolding": [{{"serves": "<fixed lemma name>",
                   "helpers": [{{"name": "...", "statement": "<full lemma header incl name+attrs+statement>",
                                 "arch_context": true|false, "why": "..."}}]}} ...]}}
Only include fixed statements that genuinely need scaffolding — do NOT
over-engineer the directly-provable ones. No sorry. No prose outside the fence."""


def replan_prompt(spec, item, errors, is_fixed, template_body=None):
    stmt = item["_canonical"]
    if is_fixed:
        ask = ("The statement is IMMUTABLE ground truth. Propose 1-3 NEW helper "
               "lemmas to insert BEFORE it that would make it provable, as JSON: "
               '{"helpers": [{"name":..., "statement":"<full lemma header>", '
               '"arch_context":bool, "why":...}]}')
    else:
        ask = ("This is YOUR OWN helper — you may revise its statement or split it. "
               'Reply JSON: {"replace": [{"name":..., "statement":"<full lemma header>", '
               '"arch_context":bool, "why":...}]} (1-3 items replacing this one)')
    tmpl = ""
    if template_body:
        # TEMPLATE ARM: the human original proof reveals which support lemmas the
        # decomposition needs — the exact structure a replan should reproduce.
        tmpl = (f"\nThe HUMAN ORIGINAL proof of this statement (reproduce the support "
                f"lemmas it relies on as your helpers):\n```isabelle\n{template_body}\n```\n")
    return f"""In regenerating seL4 theory {spec['theory_name']}, this declaration
repeatedly fails to prove:

{stmt}

Last errors:
{errors[:3000]}
{tmpl}
{ask}
One ```json fence, nothing else."""


# --------------------------------------------------------------------------- human escalation
def queue_stuck(exp, item, idir, tries, err_txt, reason):
    """No-progress on ONE item: park it in the stuck queue and let the run
    continue with the remaining items. Killing the whole 100-item run per stuck
    lemma (the old behavior) costs one human round-trip PER item; queueing
    batches all judgments into one NEEDS-HUMAN report at the end."""
    name = item.get("name", "?")
    stuck_p = exp / "stuck.json"
    stuck = json.load(open(stuck_p)) if stuck_p.exists() else []
    stuck.append(dict(name=name, kind=item["kind"],
                      statement=item.get("_canonical", "?"),
                      reason=reason, tries=tries,
                      item_dir=str(idir), errors=(err_txt or "")[:3000]))
    json.dump(stuck, open(stuck_p, "w"), indent=2)
    log(f"== STUCK [{item['kind']}] {name} — parked ({reason}); continuing with next item ==")


def write_needs_human(exp, items_total, locked_n):
    stuck_p = exp / "stuck.json"
    if not stuck_p.exists():
        return 0
    stuck = json.load(open(stuck_p))
    md = [f"# NEEDS HUMAN JUDGMENT — {len(stuck)} stuck item(s) "
          f"({locked_n}/{items_total} locked)\n"]
    for s in stuck:
        md += [f"## [{s['kind']}] `{s['name']}`  ({s['reason']}, {s['tries']} tries)",
               "```isabelle", s["statement"], "```",
               f"artifacts: `{s['item_dir']}`", "```", s["errors"][:2000], "```", ""]
    md += ["For each: provable in position? decompose/extra helpers? strategy hint or human body?",
           f"Resolve by injecting bodies into `{exp}/locked.json` (flag human_supplied_body, "
           f"mind --human-budget), then `--resume-from {exp}`."]
    (exp / "NEEDS-HUMAN.md").write_text("\n".join(md))
    log(f"== {len(stuck)} stuck item(s) → {exp}/NEEDS-HUMAN.md ==")
    return len(stuck)


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("theory")
    ap.add_argument("--model", default=os.environ.get("REGEN_MODEL", "opus"))
    ap.add_argument("--item-tries", type=int, default=int(os.environ.get("REGEN_ITEM_TRIES", "4")))
    ap.add_argument("--check-budget", type=int, default=int(os.environ.get("REGEN_CHECK_BUDGET", "80")))
    ap.add_argument("--timeout", type=int, default=int(os.environ.get("REGEN_TIMEOUT", "1800")))
    ap.add_argument("--plan-timeout", type=int, default=int(os.environ.get("REGEN_PLAN_TIMEOUT", "3600")))
    ap.add_argument("--resume-from", default=None,
                    help="prior experiment dir: reuse its plan + already-passing "
                         "item bodies (lock them instantly), continue from the "
                         "first unproved item")
    ap.add_argument("--hints", default=None,
                    help="JSON file {item_name: human strategy hint}. Each use is "
                         "recorded per-item and counted in measurement.json "
                         "(human_assisted_items) — honesty accounting.")
    ap.add_argument("--cluster-budget", type=int,
                    default=int(os.environ.get("REGEN_CLUSTER_BUDGET", "16")),
                    help="max cumulative check-theory calls charged to one cluster "
                         "(a fixed item + its replan-descendant helpers). Over cap "
                         "-> park the whole cluster (a hard family shouldn't drain "
                         "the global budget). Measured: bitmap cluster hit 20+.")
    ap.add_argument("--incremental", action="store_true",
                    help="INCREMENTAL HEAP SEALING (spike-confirmed ~11x on late "
                         "checks): build the locked prefix into a heap once, then "
                         "each check loads it + processes only the unsealed tail + "
                         "candidate (~13s vs ~141s). Re-seals every --seal-every "
                         "locks. Falls back to whole-prefix check if a seal build fails.")
    ap.add_argument("--seal-every", type=int,
                    default=int(os.environ.get("REGEN_SEAL_EVERY", "15")),
                    help="re-seal after this many new locks accumulate unsealed")
    ap.add_argument("--human-budget", type=int,
                    default=int(os.environ.get("REGEN_HUMAN_BUDGET", "2")),
                    help="max human-supplied BODIES per scope. Human intervention "
                         "is a FIRST-CLASS metric, not a footnote: exceeding the "
                         "budget marks the scope failed(over-human-budget), and "
                         "human-assisted items are reported in a separate column "
                         "(never mixed into the machine corpus aggregate).")
    ap.add_argument("--arm", choices=["contract", "template"], default="contract",
                    help="ablation arm: 'contract' = contract-surface oracle only "
                         "(main); 'template' = additionally show the HUMAN original "
                         "proof body for each kept statement (CertiCoq-style upper "
                         "control arm — the delta vs 'contract' quantifies structure "
                         "invention).")
    args = ap.parse_args()
    human_hints = json.load(open(args.hints)) if args.hints else {}

    os.chdir(REPO)
    tp = Path(REFINE_DIR) / Path(args.theory).name
    if not tp.exists():
        tp = Path(args.theory)
    assert tp.exists(), f"theory not found: {args.theory}"
    tbase = tp.stem
    rel_l4v = str(tp).replace(L4V_TREE + "/", "", 1)

    ts = time.strftime("%Y%m%d-%H%M%S")
    exp = Path(f"reports/regen/{tbase.lower()}-incr-{ts}")
    (exp / "items").mkdir(parents=True)
    log(f"== regen INCREMENTAL :: {tbase} :: {exp} ==")
    log(f"model={args.model} item-tries={args.item_tries} check-budget={args.check_budget}")

    # [pre] recoverability
    dirty = sh(["git", "-C", L4V_TREE, "status", "--porcelain", "--", rel_l4v]).stdout.strip()
    assert not dirty, f"ABORT: {rel_l4v} dirty"
    sha0 = hashlib.sha256(tp.read_bytes()).hexdigest()
    json.dump({"l4v_head": sh(["git", "-C", L4V_TREE, "rev-parse", "HEAD"]).stdout.strip(),
               "in_scope_file": rel_l4v, "orig_sha256": sha0},
              open(exp / "snapshot.json", "w"), indent=2)
    log(f"[pre] clean, sha={sha0[:12]}")

    # STARTUP CLEANUP: a crashed/killed prior run (power outage, daily here)
    # leaves an orphan polyml holding the Refine session flock — every check
    # then dies "another Isabelle tool is already running". At driver startup no
    # check of ours is running yet, so any container poly + Refine lock is stale.
    sh(["docker", "exec", CONTAINER, "bash", "-c",
        "pkill -9 -x poly 2>/dev/null; rm -f /tmp/isabelle-session-*.lock 2>/dev/null; true"])
    log("[pre] cleared any stale Isabelle lock / orphan poly")

    # [0] baseline — CACHED by (file sha, l4v HEAD). The original theory never
    # changes across runs; re-checking it (~250s for Schedule_R) every resume
    # was pure waste. Cache keyed on content+HEAD so it invalidates if either moves.
    bcache = REPO / "reports" / "regen" / ".baseline-cache.json"
    cache = json.load(open(bcache)) if bcache.exists() else {}
    ckey = f"{tbase}:{sha0[:12]}:{cache.get('_head','')}"
    cached = cache.get(tbase)
    if cached and cached.get("sha") == sha0[:12]:
        base_wall = cached["wall_ms"]
        log(f"[0] baseline CACHED OK ({base_wall}ms; skipped re-check)")
    else:
        # baseline can transiently fail right after a container cold-start /
        # power-restart (Isabelle not warm) — a one-shot assert killed the whole
        # resume. Retry a few times before giving up (it's the fixed original
        # theory; a real failure means a broken env, worth surfacing after retries).
        base_wall = None
        for bt in range(1, 4):
            ok, base_wall, bout = check_theory(tp)
            if ok:
                break
            log(f"[0] baseline not green (attempt {bt}/3) — container warming? retrying in 20s")
            time.sleep(20)
        assert ok, f"baseline not green after 3 attempts: {bout[-400:]}"
        cache[tbase] = {"sha": sha0[:12], "wall_ms": base_wall}
        bcache.parent.mkdir(parents=True, exist_ok=True)
        json.dump(cache, open(bcache, "w"), indent=2)
        log(f"[0] baseline OK ({base_wall}ms; cached)")
    orig_lines = len(tp.read_text().splitlines())
    orig_lemmas = len(re.findall(r'(?m)^(lemma|theorem|corollary)\b', tp.read_text()))

    # [1] extract + strip (reuse existing tools)
    sh(["python3", "regen/scripts/regen_extract.py", "--refine", REFINE_DIR,
        "--out", str(exp / "keepset.json"), str(tp)])
    sh(["python3", "regen/scripts/regen_strip.py", "--keepset", str(exp / "keepset.json"),
        "--theory", str(tp), "--outdir", str(exp)])
    spec = json.load(open(exp / "task_spec.json"))
    fixed_by_name = {k["name"]: k["statement"] for k in spec["to_prove"]}
    defs_by_name = {k["name"]: k["text"] for k in spec.get("verbatim", [])}
    log(f"[1] keepset: {len(fixed_by_name)} fixed, {len(defs_by_name)} verbatim defs")

    # template arm: human original bodies by lemma name (upper control arm)
    orig_bodies = {}
    if args.arm == "template":
        from regen_extract import slice_decls
        src = tp.read_text()
        slines = src.splitlines()
        for d in slice_decls(src):
            if d["name"]:
                stmt_n = d["stmt"].count("\n") + 1
                orig_bodies[d["name"]] = "\n".join(
                    slines[d["start"] + stmt_n:d["end"]]).strip()
        log(f"[1] template arm: {len(orig_bodies)} human bodies available as templates")

    header_comment = "(* Regenerated by regen_incr — machine proof corpus, statements are l4v ground truth *)"

    import_index, import_consts = build_import_index(tp)
    log(f"[1] import-fact index: {len(import_index)} facts, {len(import_consts)} consts "
        f"from imported theories")

    # [2] PLAN (or reuse a prior run's plan on --resume-from)
    if args.resume_from:
        old = Path(args.resume_from)
        plan = json.load(open(old / "plan.json"))
        items = plan["items"]
        log(f"[2] plan REUSED from {old} ({len(items)} items)")
    else:
        log("[2] PLAN (claude -p) ...")
        reply = claude_call(plan_prompt(spec), args.model, args.plan_timeout,
                            str(exp / "plan-transcript.ndjson"), max_turns="3")
        (exp / "plan-raw.txt").write_text(reply)
        plan = json.loads(fenced(reply, "json"))
        items = plan["items"]
    # validate + canonicalize
    seen_fixed, seen_defs = set(), set()
    for it in items:
        if it["kind"] == "fixed":
            assert it["name"] in fixed_by_name, f"unknown fixed {it['name']}"
            it["_canonical"] = fixed_by_name[it["name"]]
            seen_fixed.add(it["name"])
        elif it["kind"] == "verbatim_def":
            assert it["name"] in defs_by_name, f"unknown def {it['name']}"
            it["_canonical"] = defs_by_name[it["name"]]
            seen_defs.add(it["name"])
        else:
            assert it.get("statement"), f"helper {it.get('name')} missing statement"
            assert not re.search(r'\b(sorry|oops|axiomatization)\b', it["statement"])
            it["_canonical"] = it["statement"]
            if it.get("name") in defs_by_name:
                # alias->lemma CONVERSION (batch-judgment mechanism): a kept
                # `lemmas` alias whose RHS cites deleted interior is re-stated
                # as a proper lemma obligation the machine must prove.
                seen_defs.add(it["name"])
                log(f"[2] alias->lemma conversion: {it['name']} (judgment)")
    assert seen_fixed == set(fixed_by_name), f"plan missing fixed: {set(fixed_by_name)-seen_fixed}"
    assert seen_defs == set(defs_by_name), f"plan missing defs: {set(defs_by_name)-seen_defs}"
    for it in items:
        it.setdefault("origin", "plan")
    log(f"[2] plan OK: {len(items)} items "
        f"({sum(1 for i in items if i['kind']=='helper')} helpers)")

    # [2b] PLAN CRITIQUE (fresh plans only): plans systematically underestimate
    # scaffolding (measured: 6 planned vs 23 needed helpers). A second pass
    # predicts per-fixed-statement scaffolding upfront, with an explicit
    # consumer ("serves") — front-loads the predictable part of discovery;
    # replan still catches the residual. origin tags keep the planned /
    # critique / replan split measurable.
    if not args.resume_from:
        log("[2b] PLAN CRITIQUE (claude -p) ...")
        reply = claude_call(critique_prompt(spec, items), args.model,
                            args.plan_timeout, str(exp / "critique-transcript.ndjson"))
        (exp / "critique-raw.txt").write_text(reply)
        added = 0
        try:
            crit = json.loads(fenced(reply, "json"))
            for entry in crit.get("scaffolding", []):
                serves = entry.get("serves")
                tgt = next((k for k, x in enumerate(items)
                            if x.get("name") == serves and x["kind"] == "fixed"), None)
                if tgt is None:
                    continue
                new = []
                for h in entry.get("helpers", []):
                    st = h.get("statement", "")
                    if not st or re.search(r'\b(sorry|oops|axiomatization)\b', st) \
                       or any(x.get("name") == h.get("name") for x in items):
                        continue
                    h.update(kind="helper", _canonical=st, origin="critique",
                             serves=serves)
                    new.append(h)
                items[tgt:tgt] = new
                added += len(new)
        except ValueError:
            log("[2b] critique unparseable — continuing with base plan")
        log(f"[2b] critique added {added} scaffolding helper(s)")
    json.dump(plan, open(exp / "plan.json", "w"), indent=2)

    # [3] per-item generate -> verify -> lock
    locked = []          # [(item, rendered_text)]
    proved_stmts = []    # [(name, one-line statement)] for prompts
    checks = 0
    theory_progress = exp / f"{tbase}.thy"

    seal = {"cur": None, "upto": 0, "id": 0}   # incremental heap state

    def do_seal():
        """(Re)build a heap of the whole current locked prefix. On success sets
        seal['cur']/'upto'; on failure disables incremental (fall back)."""
        if not args.incremental or not locked:
            return
        seal["id"] += 1
        s = seal_prefix(exp, header_comment, spec["imports"], locked, seal["id"])
        if s:
            seal["cur"], seal["upto"] = s, len(locked)
            log(f"  [sealed {len(locked)} locked into heap {s['sess']} "
                f"({s['build_ms']}ms); checks now incremental]")
        else:
            seal["cur"] = None
            log("  [seal build FAILED — falling back to whole-prefix checks]")

    def maybe_reseal():
        if args.incremental and seal["cur"] and len(locked) - seal["upto"] >= args.seal_every:
            do_seal()

    def verify_prefix():
        # verify_prefix is called AFTER locked.append(candidate). Incremental:
        # load the sealed heap + process only [unsealed tail + the candidate].
        nonlocal checks
        checks += 1
        if seal["cur"] is not None:
            return check_incremental(exp, tbase, seal["cur"],
                                     locked[seal["upto"]:-1], locked[-1])
        theory_progress.write_text(render(header_comment, tbase, spec["imports"], locked))
        return check_theory(theory_progress)

    def verify_prefix_of(it_body_text, short_timeout):
        """Check the current locked prefix + a candidate item text, with a short
        wall cap — used only for timeout localization (does not count toward the
        main check budget's semantics but is logged)."""
        nonlocal checks
        checks += 1
        probe = exp / "timeout-probe.thy"
        probe.write_text(render(header_comment, tbase, spec["imports"],
                                locked + [it_body_text]))
        return check_theory(probe, timeout_s=short_timeout)

    def save_locked():
        json.dump([{"item": {k: v for k, v in it.items() if not k.startswith("_")
                             or k == "_canonical"},
                    "text": txt} for it, txt in locked],
                  open(exp / "locked.json", "w"), indent=2)

    i = 0
    locked_keys = set()
    carried_stuck = set()
    if args.resume_from:
        old = Path(args.resume_from)
        # carry the stuck queue forward: those items already burned their tries
        # (+replan) in the prior run — re-attempting them on every resume wastes
        # hours. They stay parked for the batch human judgment.
        if (old / "stuck.json").exists():
            old_stuck = json.load(open(old / "stuck.json"))
            # AUTO-UN-PARK stale cascades: an item parked as "depends on stuck
            # `X`" stays parked forever even after X locks in a later round
            # (measured: ≥2 zombie parks). If the blocker is now locked, free it.
            locked_names = {e["item"].get("name")
                            for e in json.load(open(old / "locked.json"))} \
                if (old / "locked.json").exists() else set()
            kept, freed = [], []
            for s_ in old_stuck:
                m_dep = re.match(r'depends on stuck item `([^`]+)`', s_["reason"])
                if m_dep and m_dep.group(1) in locked_names:
                    freed.append(s_["name"])
                else:
                    kept.append(s_)
            old_stuck = kept
            json.dump(old_stuck, open(exp / "stuck.json", "w"), indent=2)
            carried_stuck = {s["name"] for s in old_stuck}
            log(f"[3] carried {len(carried_stuck)} stuck item(s); "
                f"auto-un-parked {len(freed)} whose blocker is now locked: {freed}")
        if (old / "locked.json").exists():
            # preferred: exact locked state persisted by the prior run. Items are
            # matched BY NAME (not prefix count): with the stuck-queue, locked is
            # no longer a contiguous prefix of the plan — skipped items get
            # re-attempted, already-locked ones are skipped in the main loop.
            for e in json.load(open(old / "locked.json")):
                it_l, txt = e["item"], e["text"]
                locked.append((it_l, txt))
                locked_keys.add((it_l.get("name"), it_l["kind"]))
                if it_l["kind"] != "verbatim_def":
                    proved_stmts.append((it_l.get("name", "?"),
                                         " ".join(it_l["_canonical"].split())[:160]))
            i = 0
        else:
            # fallback: reconstruct from the prior run's items/ dirs
            for idx, it in enumerate(items):
                name = it.get("name") or f"item{idx}"
                idir_old = old / "items" / f"{idx:02d}-{name}"
                if it["kind"] == "verbatim_def":
                    locked.append((it, it["_canonical"]))
                    continue
                got = None
                for t in range(1, 10):
                    cl = idir_old / f"check-{t}.log"
                    bf = idir_old / f"body-{t}.thy"
                    if cl.exists() and bf.exists() and "OK (" in cl.read_text():
                        got = bf.read_text().strip()
                if got is None:
                    i = idx
                    break
                locked.append((it, it["_canonical"] + "\n  " + got))
                locked_keys.add((it.get("name"), it["kind"]))
                proved_stmts.append((name, " ".join(it["_canonical"].split())[:160]))
            else:
                i = len(items)
            locked_keys |= {(l[0].get("name"), l[0]["kind"]) for l in locked}
            i = 0
        save_locked()
        if args.incremental:
            # the seal BUILD is itself a full verification of the prefix — if it
            # builds green, the prefix is green. Replaces the ~123s whole-prefix
            # reverify with a one-time seal that also gives us the fast heap.
            do_seal()
            assert seal["cur"] is not None, "resumed prefix seal failed — resume aborted"
            log(f"[3] resume: {len(locked)} items locked; sealed into heap "
                f"(incremental checks active); continuing at item {i+1}/{len(items)}")
        else:
            ok, wall, out = verify_prefix()      # verify_prefix on full locked (no cand)
            assert ok, "resumed prefix does not check — resume aborted"
            log(f"[3] resume: {len(locked)} items locked from prior run "
                f"[prefix re-verified, {wall}ms]; continuing at item {i+1}/{len(items)}")
    quota_halt = False
    from collections import defaultdict
    cluster_checks = defaultdict(int)
    while i < len(items):
      try:
        it = items[i]
        name = it.get("name") or f"item{i}"
        if (it.get("name"), it["kind"]) in locked_keys:
            i += 1
            continue
        if name in carried_stuck:
            log(f"--- item {i+1}/{len(items)}: [{it['kind']}] {name} — parked (carried) ---")
            i += 1
            continue
        idir = exp / "items" / f"{i:02d}-{name}"
        idir.mkdir(parents=True, exist_ok=True)
        log(f"--- item {i+1}/{len(items)}: [{it['kind']}] {name} ---")
        if checks >= args.check_budget:
            log("== BUDGET: check-theory budget exhausted =="); break
        # PER-CLUSTER budget guard: a hard fixed item spawns a tree of replan
        # helpers, each with its own tries+replan — one hard cluster (bitmap
        # measured) can amplify to 20+ checks and burn the global budget. Cap
        # cumulative checks charged to a cluster (fixed root + its replan
        # descendants); over cap -> park the whole cluster for the human/template.
        cl = it.setdefault("cluster", it.get("name") or f"item{i}")
        if cluster_checks[cl] >= args.cluster_budget:
            log(f"  cluster `{cl}` over budget ({cluster_checks[cl]} checks) — parking")
            queue_stuck(exp, it, idir, 0, f"cluster `{cl}` exceeded {args.cluster_budget} checks",
                        "cluster budget exhausted (hard family — deferred to template/human)")
            i += 1
            continue

        if it["kind"] == "verbatim_def":
            locked.append((it, it["_canonical"]))
            ok, wall, out = verify_prefix()
            if not ok:
                # single auto-repair: toggle arch context (the usual failure)
                log(f"  def failed; toggling arch_context and retrying")
                it["arch_context"] = not it.get("arch_context")
                ok, wall, out = verify_prefix()
            (idir / "check.log").write_text(out)
            if not ok:
                # e.g. a kept `lemmas` alias whose RHS cites DELETED interior
                # facts (crunch-generated etc.) — a real mode-A semantic gap.
                # Park for the human batch (convert alias -> lemma obligation)
                # instead of aborting the whole run.
                locked.pop()
                _, summ, _ = summarize_errors(out)
                queue_stuck(exp, it, idir, 1, summ,
                            "verbatim decl does not check (likely cites deleted interior)")
                i += 1
                continue
            log(f"  locked (def) [{wall}ms]")
            maybe_reseal()
            i += 1
            continue

        # lemma item (fixed or helper): generate body, verify, retry
        prev_body, err_txt, passed, parked = None, None, False, False
        prev_sig, prev_nblocks, prev_ngoals = None, None, None
        for t in range(1, args.item_tries + 1):
            hint = None
            if err_txt:
                sugg = extract_undefined_facts(err_txt)
                if sugg:
                    hint = " ; ".join(f'"{b}" does not exist, use e.g. {c[0]}'
                                      for b, c in sugg.items() if c)
            hh = human_hints.get(name)
            if hh and t == 1:
                (idir / "human-hint.txt").write_text(hh)
                log(f"  [human hint active for {name}]")
            imp = relevant_import_facts(import_index, it["_canonical"], err_txt)
            sibs = sibling_examples(it, locked) if args.arm != "template" else None
            if sibs and t == 1:
                log(f"  [few-shot: {len(sibs)} sibling proof(s) from same family]")
            reply = claude_call(item_prompt(spec, it, proved_stmts,
                                            body_hint=hint, prev_body=prev_body,
                                            errors=err_txt, human_hint=hh,
                                            template_body=orig_bodies.get(name),
                                            import_facts=imp, siblings=sibs),
                                args.model, args.timeout,
                                str(idir / f"gen-{t}.ndjson"))
            body = fenced(reply, "isabelle")
            body = re.sub(r'(?m)^\s*lemma\b.*?(?=^\s*(apply|by|proof|using|unfolding|supply)\b)',
                          '', body, count=1, flags=re.S).strip()  # drop restated header if any
            (idir / f"body-{t}.thy").write_text(body)
            if not body or not re.match(r'\s*' + BODY_START, body) \
               or re.search(r'\b(sorry|oops|axiomatization)\b', body):
                err_txt = "*** body rejected: must start with a proof command, no sorry/oops."
                prev_body = body or "(empty)"
                log(f"  try {t}: body rejected (shape)")
                continue
            # NO-PROGRESS guard 1: identical proof script resubmitted
            if prev_body is not None and body.strip() == prev_body.strip():
                queue_stuck(exp, it, idir, t, err_txt,
                            "model resubmitted the IDENTICAL proof script")
                parked = True
                break
            # STATIC FACT PRE-CHECK (repo symbol-table style): reject a body that
            # cites a fact name existing NOWHERE (index + locked + own defs)
            # BEFORE paying a ~140s check. Hallucinated names (measured:
            # st_tcb_at_tcb_at') otherwise cost a full check to surface.
            # STATIC PRE-CHECK is only worth its false-positive risk when a real
            # check is EXPENSIVE (~140s). With --incremental the check is ~13s, so
            # the check itself is the cheapest, most reliable oracle — skip the
            # pre-check (it kept false-rejecting real base-HOL/lib lemmas: hoare_*,
            # disj_cong, valid_objs'). Only run it in the slow non-incremental path.
            known_local = {n for n, _ in proved_stmts} \
                | {x.get("name") for x in items if x.get("name")} \
                | {k["name"] for k in spec.get("verbatim", [])}
            missing = ({} if args.incremental else
                       precheck_facts(body, set(import_index), import_consts,
                                      known_local, it["_canonical"]))
            if missing and t < args.item_tries:
                dym = "; ".join(f'"{b}"→ maybe {c}' if c else f'"{b}" (no match)'
                                for b, c in missing.items())
                err_txt = ("*** static pre-check: these cited facts do not exist "
                           f"(skipped the prover): {dym}. Use existing names only.")
                prev_body = body
                log(f"  try {t}: PRE-CHECK reject — nonexistent facts: {list(missing)}")
                continue
            locked.append((it, it["_canonical"] + "\n  " + body))
            ok, wall, out = verify_prefix()
            cluster_checks[cl] += 1
            (idir / f"check-{t}.log").write_text(out)
            if ok:
                log(f"  try {t}: GREEN — locked [{wall}ms] (checks={checks})")
                proved_stmts.append((name, " ".join(it["_canonical"].split())[:160]))
                save_locked()
                maybe_reseal()
                passed = True
                break
            locked.pop()
            nblocks, summary, sig = summarize_errors(out)
            trend = f" (previous try: {prev_nblocks})" if prev_nblocks is not None else ""
            # IMPROVEMENT 1 + 3 — directional strategy hint keyed to failure MODE
            # (feedback was "direction-neutral": timeouts were a black hole and
            # residual-goal reports let the model drift the wrong way).
            n_goals = None
            gm = re.search(r'goal \((\d+) subgoal', out)
            if gm:
                n_goals = int(gm.group(1))
            strat = None
            if "killed after" in out and "wall-time ceiling" in out:
                strat = STRATEGY_HINTS["timeout"]
                # IMPROVEMENT 1 — localize the exploding step (timeout was a
                # black hole: agent couldn't see WHERE it hung). Bisect the
                # apply-steps: a truncated prefix either fails FAST with residual
                # goals (explosion is later) or times out (explosion is here).
                loc = localize_timeout(it, body, locked, verify_prefix_of, idir)
                if loc:
                    strat += f"\n{loc}"
            elif re.search(r'\?[A-Z]', out) and ('No such variable' in out
                                                 or 'schematic' in out.lower()
                                                 or re.search(r'\?Q\d?\b', out)):
                strat = STRATEGY_HINTS["schematic"]
            elif prev_ngoals is not None and n_goals is not None and n_goals > prev_ngoals:
                strat = STRATEGY_HINTS["growing"]
            elif sig == prev_sig:
                strat = STRATEGY_HINTS["same_goals"]
            err_txt = f"Error blocks this try: {nblocks}{trend}.\n{summary}"
            if strat:
                err_txt += f"\n\nSTRATEGY DIRECTIVE: {strat}"
            log(f"  try {t}: RED ({nblocks} error blocks{trend}, "
                f"goals={n_goals}, sig={sig}{', +strategy' if strat else ''})")
            # STATEMENT-level failure fast-path: if the FIRST error line falls
            # inside the statement span (not the body), the invented statement
            # itself does not elaborate (bad name/type) — more proof tries are
            # useless. Helpers jump straight to replan; fixed statements park.
            m_l = re.search(r'\(line (\d+) of', out)
            if m_l:
                stmt_start = item_start_line(header_comment, tbase, spec["imports"],
                                             locked + [(it, it["_canonical"])])
                stmt_end = stmt_start + it["_canonical"].count("\n")
                if stmt_start <= int(m_l.group(1)) <= stmt_end:
                    log(f"  STATEMENT-level error (line {m_l.group(1)} in stmt span "
                        f"{stmt_start}-{stmt_end}) — no more proof tries")
                    if it["kind"] == "helper":
                        err_txt = ("*** the STATEMENT itself fails to elaborate "
                                   "(bad fact/constant name or type) ***\n") + err_txt
                        break          # post-loop replan will restate/split it
                    queue_stuck(exp, it, idir, t, err_txt,
                                "fixed statement fails to elaborate in stripped context")
                    parked = True
                    break
            # dependency fast-path: failing because a STUCK item's fact is missing
            stuck_names = {s["name"] for s in
                           json.load(open(exp / "stuck.json"))} if (exp / "stuck.json").exists() else set()
            dep = next((n for n in stuck_names
                        if re.search(r'Undefined fact[^\n]*\b' + re.escape(n), out)), None)
            if dep:
                queue_stuck(exp, it, idir, t, err_txt,
                            f"depends on stuck item `{dep}` — deferred with it")
                parked = True
                break
            # NO-PROGRESS guard 2: two consecutive tries, identical error signature
            if sig == prev_sig:
                queue_stuck(exp, it, idir, t, err_txt,
                            "two consecutive tries produced IDENTICAL errors "
                            "(normalized signature) — feedback not moving the model")
                parked = True
                break
            prev_sig, prev_nblocks, prev_body, prev_ngoals = sig, nblocks, body, n_goals
        if passed or parked:
            i += 1
            continue

        # retries exhausted -> replan this item (once), else park it
        if it.get("_replanned"):
            queue_stuck(exp, it, idir, args.item_tries, err_txt,
                        "tries exhausted after replan")
            i += 1
            continue
        log(f"  REPLAN {name} (claude -p) ...")
        reply = claude_call(replan_prompt(spec, it, err_txt or "", it["kind"] == "fixed",
                                          template_body=orig_bodies.get(name)),
                            args.model, args.timeout, str(idir / "replan.ndjson"))
        try:
            rp = json.loads(fenced(reply, "json"))
        except ValueError:
            queue_stuck(exp, it, idir, args.item_tries, err_txt, "replan unparseable")
            i += 1
            continue
        it["_replanned"] = True
        # name-collision pre-check: a replan helper reusing an existing item/
        # locked name causes "Duplicate fact declaration" at check time
        # (measured on arch_switchToIdleThread_corres) — filter them out.
        taken = {x.get("name") for x in items} | {l[0].get("name") for l in locked}

        def _fresh(hs):
            out = []
            for h in hs:
                if h.get("name") in taken:
                    log(f"  replan helper `{h.get('name')}` collides with existing name — dropped")
                    continue
                out.append(h)
            return out

        if it["kind"] == "fixed":
            new = _fresh(rp.get("helpers", []))
            for h in new:
                h.update(kind="helper", _canonical=h["statement"], origin="replan",
                         cluster=it.get("cluster", name))   # inherit parent cluster
            items[i:i] = new       # insert before the fixed item; loop re-enters
            log(f"  inserted {len(new)} helper(s) before {name}")
        else:
            new = _fresh(rp.get("replace", []))
            for h in new:
                h.update(kind="helper", _canonical=h["statement"], _replanned=True,
                         origin="replan", cluster=it.get("cluster", name))
            items[i:i+1] = new
            log(f"  replaced helper with {len(new)} item(s)")
      except QuotaExhausted as e:
        log("=" * 72)
        log(f"== QUOTA EXHAUSTED at item {i+1}/{len(items)} [{name}] — HALTING ==")
        log(f"== {e}")
        log(f"== {len(locked)} locked preserved; NOT a proof failure. "
            f"Resume after reset: --resume-from {exp} ==")
        log("=" * 72)
        save_locked()
        quota_halt = True
        break

    if quota_halt:
        json.dump(dict(halted="quota_exhausted", items_locked=len(locked),
                       resume_from=str(exp)),
                  open(exp / "HALTED.json", "w"), indent=2)

    # [4] final full verification + gates
    n_locked = len(locked)
    n_stuck = write_needs_human(exp, len(items), n_locked)
    all_done = (i >= len(items)) and n_stuck == 0 and n_locked == len(items) \
        and checks < args.check_budget
    final_ok = False
    final_wall = None
    if all_done:
        final = render(header_comment, tbase, spec["imports"], locked)
        (exp / f"regenerated-{tbase}.thy").write_text(final)
        final_ok, final_wall, out = check_theory(exp / f"regenerated-{tbase}.thy")
        (exp / "final-check.log").write_text(out)
        assert all(re.search(r'\b(lemma|theorem)\s+' + re.escape(n) + r'\b', final)
                   for n in fixed_by_name), "anti-cheat: fixed statement missing"
        assert not re.search(r'\b(sorry|oops|axiomatization)\b', final)
        log(f"[4] FINAL: {'GREEN' if final_ok else 'RED'} ({final_wall}ms)")

    # measurement
    new_txt = (exp / f"regenerated-{tbase}.thy").read_text() if all_done else ""
    used_hints = sorted(n for n in human_hints
                        if any((exp / "items").glob(f"*-{n}/human-hint.txt")))
    # HUMAN INTERVENTION AS FIRST-CLASS METRIC: bodies supplied by a human are
    # counted, budgeted, and reported in their own column — a green corpus that
    # exceeds the budget is failed(over-human-budget), and human-assisted items
    # are never silently mixed into the machine aggregate.
    human_bodies = sorted(it.get("name", "?") for it, _ in locked
                          if it.get("human_supplied_body"))
    n_defs = sum(1 for it, _ in locked if it["kind"] == "verbatim_def")
    # structure-discovery split: how much scaffolding was planned upfront vs
    # predicted by the critique pass vs discovered reactively via replan
    from collections import Counter
    helper_origin = Counter(it.get("origin", "plan") for it, _ in locked
                            if it["kind"] == "helper")
    over_budget = len(human_bodies) > args.human_budget
    meas = dict(final_green=final_ok and not over_budget,
                green_but_over_human_budget=final_ok and over_budget,
                arm=args.arm,
                items_total=len(items), items_locked=n_locked, items_stuck=n_stuck,
                machine_locked_lemmas=n_locked - n_defs - len(human_bodies),
                helper_origin_split=dict(helper_origin),
                human_supplied_bodies=human_bodies,
                human_body_budget=args.human_budget,
                hint_assisted_items=used_hints,
                checks_used=checks, baseline_wall_ms=base_wall,
                machine_wall_ms=final_wall,
                human_lines=orig_lines, machine_lines=len(new_txt.splitlines()),
                human_lemmas=orig_lemmas,
                machine_lemmas=len(re.findall(r'(?m)^(lemma|theorem)\b', new_txt)))
    json.dump(meas, open(exp / "measurement.json", "w"), indent=2)
    log(json.dumps(meas, indent=2))

    # [post] recoverability
    sha1 = hashlib.sha256(tp.read_bytes()).hexdigest()
    log("[post] recoverability " + ("OK — source byte-identical" if sha1 == sha0
                                    else "VIOLATED!!"))
    log(f"== DONE ({'GREEN' if final_ok else 'not green'}) — archive: {exp} ==")


if __name__ == "__main__":
    main()
