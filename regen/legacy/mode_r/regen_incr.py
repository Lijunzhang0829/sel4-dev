#!/usr/bin/env python3
"""regen_incr.py — incremental, lock-in regeneration driver (plan -> per-lemma).

Replaces the whole-theory-rewrite loop (regen.sh) whose failure mode was
measured on SubMonad_R: every round rewrites all ~15 declarations, gets a
DIFFERENT 5-15% wrong each time, so the union of per-round successes covers
everything but no single round is green (see reports/regen/submonad_r-20260701-235944).

Architecture (user-specified):
  [PLAN]   opus emits a STRUCTURE plan (ordered decls: fixed statements +
           its own helpers + verbatim defs; no proofs yet)      - LLM provider, recorded
  [ITEMS]  per plan item: generate ONE proof body               - LLM provider, recorded
           -> append to the growing prefix -> check-theory the prefix
           -> pass: LOCK (never regenerated)  fail: feed *** back, retry
           -> retries exhausted: REPLAN the item (helper: may revise/split
              its own statement; fixed: statement immutable, may only insert
              new helpers before it)                            - LLM provider, recorded
  [FINAL]  all items green -> full-file check + anti-cheat gate
           (every fixed statement verbatim via the assembler, no sorry)
  [pre/post] recoverability: l4v source snapshot + byte-identity assert

Anti-cheat by construction: fixed statements and verbatim defs are rendered
from the keepset by the ASSEMBLER — the model only ever supplies proof bodies
and its own helper statements, so it cannot weaken/restate ground truth.

Usage:
  python3 regen/legacy/mode_r/regen_incr.py SubMonad_R.thy [--model opus] [--item-tries 4]
          [--check-budget 80] [--timeout 1800]
"""

# Allow both python -m and direct script invocation.
if __package__ in {None, ""}:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import argparse, hashlib, json, os, re, shutil, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

sys.path.insert(0, str(REPO / "spec-strengthen" / "scripts"))
from spec_agent import find_claude, run_claude_streaming          # noqa: E402
from regen.legacy.whole_theory.regen_triage import extract_undefined_facts                  # noqa: E402

REFINE_DIR = os.environ.get("REGEN_REFINE_DIR", "verification/l4v/proof/refine/ARM")
L4V_TREE = "verification/l4v"
SESSION = os.environ.get("REGEN_SESSION", "Refine")
CONTAINER = os.environ.get("REGEN_CONTAINER", "sel4-l4v")

# Premise library (proven-lemma DB with file/theory), populated in main(). Module
# globals so item_prompt / the replan filter can use it without signature churn.
PREMISE_RECORDS = []          # [{name, stmt, theory, file, kind}, ...] import closure
FACT_THEORY = {}              # name -> theory (for [theory] tags + collision awareness)
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
    from regen.preparation.regen_extract import slice_decls
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

    def theory_aliases(src, path):
        aliases = {Path(path).stem}
        tm = re.search(r'(?m)^\s*theory\s+([A-Za-z][A-Za-z0-9_\']*)\b', src)
        if tm:
            aliases.add(tm.group(1))
        aliases.update(re.findall(r'\bglobal_naming\s+([A-Za-z][A-Za-z0-9_\']*)\b', src))
        return aliases

    def add_fact(name, stmt, aliases, expose_open=True):
        text = " ".join(stmt.split())[:200]
        if expose_open:
            idx.setdefault(name, text)
        for alias in aliases:
            idx.setdefault(f"{alias}.{name}", text)

    idx, consts = {}, set()
    for p in reachable:
        if os.path.abspath(p) == os.path.abspath(target_path):
            continue
        src = open(p, encoding="utf-8", errors="ignore").read()
        aliases = theory_aliases(src, p)
        hidden_open = set(re.findall(r'(?m)^\s*hide_fact\s*\(open\)\s+([A-Za-z][A-Za-z0-9_\']*)', src))
        for d in slice_decls(src):
            if not d["name"]:
                continue
            if d["kind"] in ("lemma", "theorem", "corollary", "lemmas"):
                add_fact(d["name"], d["stmt"], aliases, d["name"] not in hidden_open)
            elif d["kind"] == "defs":
                add_fact(d["name"], d["stmt"], aliases, d["name"] not in hidden_open)
                if d["name"].endswith("_def"):
                    consts.add(d["name"][:-4])
            elif d["kind"] in ("definition", "abbreviation", "fun", "primrec",
                               "function", "record", "datatype", "type_synonym"):
                consts.add(d["name"])
                # Definitional facts are frequently cited via `unfolding foo_def`
                # or their qualified form (`Kernel_Config.foo_def`). Expose the
                # synthetic theorem name in the import-fact index so retrieval and
                # undefined-fact fallback can surface it.
                fact_name = f"{d['name']}_def"
                add_fact(fact_name, d["stmt"], aliases, fact_name not in hidden_open)
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
            "ccontr", "disjE", "conjE", "subst", "cong", "arg_cong",
            # Isabelle method modifiers / tactic keywords that can appear next
            # to facts in wp/rule positions but are not facts themselves.
            "erule", "drule", "frule", "rule", "assumption", "where",
            "rotated", "OF", "THEN", "intro", "dest", "elim"}
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
    "relation_explosion": ("Your proof DID NOT TERMINATE, and it unfolds a big relation "
                    "definition (`state_relation_def` / a `*_relation_def`) under "
                    "`simp`/`fastforce`/`clarsimp` — that relation is HUGE and blows up the "
                    "search. Do NOT `simp add: state_relation_def`. Instead cross the "
                    "relation with its TARGETED PROJECTION lemma and work from that: "
                    "`frule state_relation_ready_queues_relation` (for ready-queue facts), "
                    "`state_relation_pspace_relation`, `state_relation_sched_act_relation`, "
                    "`...`; then unfold only the SMALL sub-relation you need "
                    "(`ready_queues_relation_def`, `ready_queue_relation_def`) and instantiate "
                    "with `drule_tac x=<dom> in spec` / `x=<prio>`. Discharge cross-guards via "
                    "the specific `*_cross` lemma (e.g. `st_tcb_at_runnable_cross`), not a raw "
                    "`fastforce` over the whole state relation."),
    "stateassert": ("The concrete side still has an UNDISCHARGED `stateAssert Q' []` with"
                    "no abstract counterpart, and your `rule` FAILED TO UNIFY (the goal is "
                    "untouched). This is a combinator-SELECTION error — pick the right one "
                    "(all in scope):\n"
                    "  - `corres_stateAssert_add_assertion` (USUAL CHOICE): folds Q' into the "
                    "concrete guard; you then prove `Q' s'` from the ABSTRACT precondition via "
                    "the state relation. No extra instantiation needed.\n"
                    "  - `corres_stateAssert_ignore`: use when Q' is already implied by the "
                    "concrete guard in scope.\n"
                    "  - `corres_stateAssert_implied` takes an ABSTRACT-typed side condition "
                    "P'; do NOT pass the concrete assert predicate to P' (type mismatch — that "
                    "is exactly why your `rule ...[where P'=...]` did not unify).\n"
                    "After stripping the assert, discharge Q' by crossing the state relation "
                    "(e.g. `frule state_relation_ready_queues_relation` + the abstract fact "
                    "that the asserted property holds)."),
    "branch_subgoals": ("Your proof has entered an if/case split and leaked multiple branch "
                        "subgoals. Do NOT continue with a flat linear apply suffix. Treat the "
                        "proof as a branch tree: first align the discriminator with `corres_if` "
                        "or `corres_if2`, then handle each generated branch separately. If you "
                        "split `scheduler_action`, give the variable a concrete type such as "
                        "`action :: scheduler_action`. Use structured bullets or a single "
                        "bounded tactic block per branch; do not let later rules intended for "
                        "one branch run on all branches. For each branch choose the matching "
                        "corres rule/WP facts independently, e.g. set-scheduler-action branch "
                        "versus reschedule-required branch. If the branch discriminator is "
                        "`sched_act_relation act act'`, prefer the already-proved "
                        "`corres_split_sched_act` over nested `corres_if2`; it aligns the "
                        "three scheduler-action constructors in one rule and prevents leaked "
                        "schematic branch guards. When a residual branch is a pure guard "
                        "implication, use already-locked bridge lemmas as INTRO rules "
                        "(`blast intro: ..._bridge`) rather than `clarsimp dest!: ..._bridge`, "
                        "because the latter often fails to instantiate the abstract/concrete "
                        "state pair in scheduler guards."),
}


# --------------------------------------------------------------------------- utils
def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def log(msg):
    print(msg, flush=True)


class QuotaExhausted(Exception):
    """The LLM account hit a quota/auth/availability limit — NOT a proof failure.
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
    raw = Path(raw_path)
    raw.with_suffix(raw.suffix + ".prompt.txt").write_text(prompt)
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


def codex_call(prompt, model, timeout, raw_path, max_turns="3"):
    """One recorded Codex exec call.

    Codex is used as a replacement LLM generator for the old `claude -p`
    surface. Run from /tmp with no repo attached so the model gets only the
    prompt payload, matching the spirit of the tool-less regeneration arm.
    """
    codex = shutil.which("codex")
    if not codex:
        raise RuntimeError("codex CLI not found on PATH")
    raw = Path(raw_path)
    raw.with_suffix(raw.suffix + ".prompt.txt").write_text(prompt)
    final_path = raw.with_suffix(raw.suffix + ".final.txt")
    meta_path = raw.with_suffix(raw.suffix + ".meta.json")
    argv = [
        codex, "-m", model, "-C", "/tmp", "-s", "read-only",
        "exec", "--skip-git-repo-check", "--json",
        "-o", str(final_path), "-",
    ]
    meta_path.write_text(json.dumps({
        "provider": "codex",
        "model": model,
        "argv": argv,
        "max_turns_note": "codex exec has no direct max-turns analogue",
    }, indent=2))
    try:
        proc = subprocess.run(
            argv, input=prompt, text=True, capture_output=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raw.write_text("")
        log("    [agent:codex] timed out")
        return ""
    raw.write_text(proc.stdout)
    if proc.stderr:
        raw.with_suffix(raw.suffix + ".stderr.txt").write_text(proc.stderr)
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if re.search(r"401 Unauthorized|Missing bearer|authentication", combined, re.I):
        raise QuotaExhausted("codex authentication failed: missing/invalid API credentials")
    if proc.returncode != 0 and not combined.strip():
        raise QuotaExhausted(f"codex exec failed with exit code {proc.returncode}")
    if final_path.exists():
        final = final_path.read_text()
        if final.strip():
            return final
    return proc.stdout


def _read_codex_relay_token():
    if os.environ.get("CODEX_RELAY_TOKEN"):
        return os.environ["CODEX_RELAY_TOKEN"]
    env_path = Path(os.environ.get("CODEX_RELAY_ENV", "/home/lijun/codex-relay/.env"))
    if not env_path.exists():
        return None
    for line in env_path.read_text(errors="ignore").splitlines():
        if line.startswith("CODEX_RELAY_TOKEN="):
            return line.split("=", 1)[1].strip()
    return None


def codex_relay_call(prompt, model, timeout, raw_path, max_turns="3"):
    """One recorded call through the local codex-relay service."""
    import urllib.error
    import urllib.request

    token = _read_codex_relay_token()
    if not token:
        raise QuotaExhausted("codex-relay token missing; set CODEX_RELAY_TOKEN or CODEX_RELAY_ENV")
    raw = Path(raw_path)
    prompt_path = raw.with_suffix(raw.suffix + ".prompt.txt")
    final_path = raw.with_suffix(raw.suffix + ".final.txt")
    meta_path = raw.with_suffix(raw.suffix + ".meta.json")
    prompt_path.write_text(prompt)
    url = os.environ.get("REGEN_CODEX_RELAY_URL", "http://127.0.0.1:18080/v1/codex")
    repo = os.environ.get("REGEN_CODEX_RELAY_REPO", "default")
    payload = {"repo": repo, "prompt": prompt, "write": False}
    meta_path.write_text(json.dumps({
        "provider": "codex-relay",
        "model": model,
        "url": url,
        "repo": repo,
        "prompt_path": str(prompt_path),
        "final_path": str(final_path),
        "auth": "bearer token from CODEX_RELAY_TOKEN/CODEX_RELAY_ENV (redacted)",
    }, indent=2))
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")
        raw.write_text(body)
        raw.with_suffix(raw.suffix + ".stderr.txt").write_text(str(e))
        if e.code in (401, 403):
            raise QuotaExhausted(f"codex-relay authentication failed: HTTP {e.code}")
        raise
    except Exception as e:
        raw.write_text("")
        raw.with_suffix(raw.suffix + ".stderr.txt").write_text(repr(e))
        raise QuotaExhausted(f"codex-relay unavailable: {e}")
    raw.write_text(body)
    try:
        data = json.loads(body)
    except ValueError:
        final_path.write_text(body)
        return body
    answer = data.get("answer") or data.get("result") or data.get("message") or ""
    if data.get("error") and not answer.strip():
        raw.with_suffix(raw.suffix + ".stderr.txt").write_text(str(data.get("error")))
        raise QuotaExhausted(f"codex-relay returned error: {data.get('error')}")
    final_path.write_text(answer)
    return answer


def file_agent_call(prompt, model, timeout, raw_path, max_turns="3"):
    """Recorded filesystem bridge for a human/current-session agent.

    The harness writes the exact prompt and metadata, then waits for the agent
    to write a non-empty `<raw>.final.txt`. This keeps the same audit shape as
    CLI providers without requiring an external API credential.
    """
    raw = Path(raw_path)
    final_path = raw.with_suffix(raw.suffix + ".final.txt")
    meta_path = raw.with_suffix(raw.suffix + ".meta.json")
    prompt_path = raw.with_suffix(raw.suffix + ".prompt.txt")
    prompt_path.write_text(prompt)
    meta_path.write_text(json.dumps({
        "provider": "file-agent",
        "model": model,
        "prompt_path": str(prompt_path),
        "final_path": str(final_path),
        "instructions": "Write the agent response to final_path; raw transcript is optional.",
    }, indent=2))
    deadline = time.time() + timeout
    log(f"    [agent:file] waiting for response in {final_path}")
    while time.time() < deadline:
        if final_path.exists():
            text = final_path.read_text()
            if text.strip():
                raw.write_text(json.dumps({"type": "file-agent", "result": text}) + "\n")
                return text
        time.sleep(2)
    raw.write_text("")
    log("    [agent:file] timed out waiting for response")
    return ""


def llm_call(provider, prompt, model, timeout, raw_path, max_turns="3"):
    if provider == "claude":
        return claude_call(prompt, model, timeout, raw_path, max_turns=max_turns)
    if provider == "codex":
        return codex_call(prompt, model, timeout, raw_path, max_turns=max_turns)
    if provider == "codex-relay":
        return codex_relay_call(prompt, model, timeout, raw_path, max_turns=max_turns)
    if provider == "file-agent":
        return file_agent_call(prompt, model, timeout, raw_path, max_turns=max_turns)
    raise ValueError(f"unknown LLM provider: {provider}")


def fenced(text, kind_hint="json"):
    m = re.search(r'```(?:%s|isabelle|isar|thy)?\s*\n(.*?)```' % kind_hint, text, re.S)
    return (m.group(1) if m else text).strip()


def json_loads_fenced(text, kind_hint="json"):
    """Parse fenced JSON, tolerating Isabelle symbol escapes in strings."""
    raw = fenced(text, kind_hint)
    try:
        return json.loads(raw)
    except ValueError:
        # LLMs often emit Isabelle syntax in JSON strings as `\<...>` instead
        # of JSON's required `\\<...>`. Escape only backslashes that are not
        # valid JSON escapes, preserving quotes/newlines handling.
        repaired = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', raw)
        return json.loads(repaired)


def normalize_statement_text(stmt):
    """Normalize LLM-emitted lemma statements before rendering Isabelle.

    Some relay/model responses double-escape newlines inside JSON strings, so
    Isabelle receives `lemma foo:\n ...` as literal source and reports a
    malformed command. Convert only line-structure escapes here; leave Isabelle
    symbol escapes (`\\<...>`) intact.
    """
    if stmt is None:
        return stmt
    stmt = str(stmt).replace("\\r\\n", "\n").replace("\\n", "\n")
    return stmt.strip()

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



# --------------------------------------------------------------------------- structural composition repair
PROOF_STEP_RE = re.compile(r'(?m)^\s*(?:apply|by|unfolding|using|supply|proof|subgoal|prefer|defer|done|qed)\b.*'
                           r'(?:\n(?!\s*(?:apply|by|unfolding|using|supply|proof|subgoal|prefer|defer|done|qed)\b).*)*')


def split_proof_steps(body):
    steps = [m.group(0).strip() for m in PROOF_STEP_RE.finditer(body or '')]
    if steps and steps[-1] in {'done', 'qed'}:
        steps = steps[:-1]
    return steps


def _extract_method_facts(step, key):
    m = re.search(rf"\b{re.escape(key)}:\s*([^)]*)", step or '', re.S)
    if not m:
        return []
    stop_words = {'intro', 'intro!', 'dest', 'dest!', 'elim', 'elim!', 'simp', 'where'}
    facts = []
    for x in m.group(1).split():
        if x.rstrip(':') in stop_words:
            break
        if re.match(r"[A-Za-z_][A-Za-z0-9_'.]*$", x):
            facts.append(x)
    return facts


def fastforce_to_blast_step(step):
    if 'fastforce' not in (step or ''):
        return None
    intro = _extract_method_facts(step, 'intro') + _extract_method_facts(step, 'intro!')
    dest = _extract_method_facts(step, 'dest') + _extract_method_facts(step, 'dest!')
    if not intro and not dest:
        return None
    bits = []
    if intro:
        bits.append('intro: ' + ' '.join(intro))
    if dest:
        bits.append('dest: ' + ' '.join(dest))
    return 'apply (blast ' + ' '.join(bits) + ')'


def branch_subgoal_directive(checker_out, body):
    hay = '\n'.join([checker_out or '', body or ''])
    if ('Cannot determine type of "action"' in hay or
            (re.search(r'goal \(([3-9]|[1-9][0-9]+) subgoals', hay) and
             re.search(r'case_tac|cases |corres_if|corres_if2|scheduler_action|sched_act_relation', hay))):
        return STRATEGY_HINTS['branch_subgoals']
    if re.search(r'\?P\d+|\?Q\d+', hay) and re.search(r'corres_if|corres_if2|case_tac|scheduler_action', hay):
        return STRATEGY_HINTS['branch_subgoals']
    return None


def structural_repair_probe(item, body, verify_of, idir, bad_step, budget_s=90, continue_steps=3):
    """Try tiny local repairs around a localized structural timeout.

    The probe is evidence only: it writes candidates/check logs and returns a
    concise note for the next LLM turn. It never changes the lemma statement or
    imports, and it does not use the original human proof.
    """
    steps = split_proof_steps(body)
    if not bad_step or bad_step < 1 or bad_step > len(steps):
        return None
    stmt = item['_canonical']
    bad = steps[bad_step - 1]
    prefix = steps[:bad_step - 1]
    candidates = []
    blast = fastforce_to_blast_step(bad)
    if blast:
        candidates.append(('fastforce_to_blast', blast,
                           'bounded blast over the same explicit intro/dest facts'))
    if 'fastforce' in bad:
        candidates += [
            ('fastforce_to_clarsimp', 'apply clarsimp', 'diagnostic degradation exposing side conditions'),
            ('fastforce_to_simp', 'apply simp', 'cheap simplification-only degradation'),
        ]
    if not candidates:
        return None
    out = []
    probe_dir = idir / 'structural-repair-probes'
    probe_dir.mkdir(exist_ok=True)
    for name, repl, why in candidates:
        cand_steps = prefix + [repl]
        cand_text = stmt + '\n  ' + '\n  '.join(cand_steps) + '\n  oops'
        try:
            ok, wall, chk = verify_of((item, cand_text), budget_s)
        except Exception as e:
            chk, ok, wall = f'probe exception: {e}', False, None
        (probe_dir / f'{name}.log').write_text(chk)
        rec = {'name': name, 'replacement': repl, 'why': why, 'ok': ok,
               'wall_ms': wall, 'kind': 'timeout' if 'killed after' in chk else ('error' if re.search(r'^\*\*\*', chk, re.M) else 'checked')}
        if ok and continue_steps:
            suffix = steps[bad_step:bad_step + continue_steps]
            if suffix:
                cont_text = stmt + '\n  ' + '\n  '.join(cand_steps + suffix) + '\n  oops'
                try:
                    cok, cwall, cchk = verify_of((item, cont_text), budget_s)
                except Exception as e:
                    cchk, cok, cwall = f'probe exception: {e}', False, None
                (probe_dir / f'{name}-continue.log').write_text(cchk)
                rec['continuation'] = {'suffix_steps': len(suffix), 'ok': cok,
                                       'wall_ms': cwall,
                                       'kind': 'timeout' if 'killed after' in cchk else ('error' if re.search(r'^\*\*\*', cchk, re.M) else 'checked')}
        out.append(rec)
    (probe_dir / 'results.json').write_text(json.dumps(out, indent=2))
    lines = ['STRUCTURAL REPAIR PROBE results (generated-attempt local edits only; no original proof):']
    for r in out:
        msg = f"- {r['name']}: prefix ok={r['ok']} kind={r['kind']} wall={r['wall_ms']}ms; replacement `{r['replacement']}`"
        if r.get('continuation'):
            c = r['continuation']
            msg += f"; +{c['suffix_steps']} continuation ok={c['ok']} kind={c['kind']} wall={c['wall_ms']}ms"
        lines.append(msg)
    lines.append('If a bounded `blast` candidate checks and continuation checks, prefer that repair over broad `fastforce`. If only `simp`/`clarsimp` checks, use it to expose branch/side-condition subgoals and solve those locally.')
    return '\n'.join(lines)

# --------------------------------------------------------------------------- hard lemma mode
def classify_hard_failure(item, checker_out='', proof_body='', err_txt=''):
    """Classify failures that need more than ordinary retry/replan.

    The classifier is heuristic and audit-oriented. It routes repeated failures
    into smaller lemma exploration loops inspired by verifier-feedback search,
    auxiliary lemma pools, and syntax/semantic repair separation.
    """
    name = item.get('name', '?')
    stmt = item.get('_canonical') or item.get('statement') or ''
    hay = '\n'.join(str(x or '') for x in (name, stmt, checker_out, proof_body, err_txt))
    cats, reasons, policies = [], [], []

    def add(cat, reason, policy):
        if cat not in cats:
            cats.append(cat)
            reasons.append(reason)
            policies.append(policy)

    if ('killed after' in hay and 'wall-time ceiling' in hay) or 'Search-explosion guard' in hay:
        add('timeout_search_explosion',
            'checker hit the wall-time ceiling, usually from broad simp/auto/fastforce or large definition unfolding',
            'do not broaden search; use targeted rule/wp/corres steps and explicit intermediate assertions')
    if re.search(r'No such variable in theorem: "?\?[A-Za-z]', hay) or re.search(r'\?[QP][0-9]?\b', hay):
        add('syntax_schematic_repair',
            'proof left schematic variables or used a malformed schematic instantiation',
            'run APOLLO-style repair first: sanitize rule_tac/OF/where clauses before inventing semantic lemmas')
    if re.search(r'word_log2|prioToL1Index|l1IndexToPrio|lookupBitmapPriority|bitmapQ|mask wordRadix|ksReadyQueuesL[12]Bitmap|word_of_nat|unat', hay):
        add('word_bitmap_arithmetic',
            'goal crosses bitmap membership, word_log2, casts, masks, and priority ordering',
            'mine small bound/order/existence bridge lemmas; prove each independently before retrying the parent')
    if re.search(r'Max \{|Max_eqI|isHighestPrio|is_highest_prio', hay):
        add('max_set_reasoning',
            'goal identifies a concrete bitmap lookup with a Max over non-empty queues',
            'separate existence-in-set from upper-bound proof; make the upper-bound lemma explicit')
    if re.search(r'heap_path|tcbQueue|tcbSchedNexts_of|tcbSchedPrevs_of|tcbQueueHead|tcbQueueEnd|ksReadyQueues_asrt', hay):
        add('heap_list_queue',
            'goal reasons about scheduler linked-list queue shape and head/tail/next/prev fields',
            'prefer mid-level queue lemmas such as list_queue_relation_nil before unfolding heap_ls; mine heap_path append/remove rewrites only when no queue lemma applies')
    if re.search(r'corres_split|corres_stateAssert|stateAssert|Arch\.switchToThread|tcbSchedDequeue|setCurThread|switchToThread_corres', hay):
        add('corres_composition',
            'proof composes several corres/WP steps and needs precise pre/postcondition threading',
            'split by executable phase, keep stateAssert obligations explicit, avoid unfolding large relations')
    if branch_subgoal_directive(checker_out, proof_body):
        add('branch_subgoal_search',
            'if/case/corres_if split produced multiple branch subgoals that a flat apply suffix cannot manage',
            'treat proof as a branch tree; align the discriminator and solve each branch with its own local tactic/premise set')
    if re.search(r"state_relation|ready_queues_relation|list_queue_relation|tcbSchedNexts_of s'|tcbSchedPrevs_of s'", hay):
        add('state_relation_plumbing',
            'residual goal has abstract/concrete state relation variables not connected by the right projection lemma',
            'retrieve projection lemmas and introduce named intermediate facts before simplification')

    hard = bool(cats)
    severity = 'hard' if any(c in cats for c in (
        'timeout_search_explosion', 'word_bitmap_arithmetic', 'heap_list_queue', 'corres_composition')) else ('repair' if cats else 'ordinary')
    return {'name': name, 'hard_mode': hard, 'severity': severity,
            'categories': cats, 'reasons': reasons, 'policies': policies}


def hard_mode_prompt_section(profile):
    if not profile or not profile.get('hard_mode'):
        return ''
    cats = ', '.join(profile.get('categories', []))
    reasons = '\n'.join(f'- {r}' for r in profile.get('reasons', []))
    policies = '\n'.join(f'- {p}' for p in profile.get('policies', []))
    return f"""

HARD LEMMA MODE is active for this item.
Failure classes: {cats}
Why this is hard:
{reasons}
Required strategy, adapted from recent prover-agent/lemma-pool workflows:
{policies}
Rules for hard mode:
- Do not retry the same broad proof. Produce a smaller, checkable proof path.
- Prefer 1-3 local bridge facts already present in context or generated as helper lemmas.
- Keep helper statements minimal: one bound, one rewrite, one existence fact, or one ordering fact.
- Generated helpers are not new contract-level obligations; do not invent new `*_corres` names unless the fixed item already has that exact name.
- If the category is syntax_schematic_repair, fix the malformed tactic first; do not invent semantic lemmas for a syntax failure.
"""


def update_hard_lemma_pool(exp, item, attempt, profile, err_txt, body=None, proposed=None, outcome='failed'):
    if not profile or not profile.get('hard_mode'):
        return
    pool_p = exp / 'hard_lemma_pool.json'
    pool = json.load(open(pool_p)) if pool_p.exists() else {'items': {}}
    name = item.get('name', '?')
    rec = pool['items'].setdefault(name, {
        'kind': item.get('kind'),
        'statement': item.get('_canonical') or item.get('statement'),
        'cluster': item.get('cluster') or item.get('serves') or name,
        'categories': profile.get('categories', []),
        'attempts': [],
        'proposed_helpers': [],
    })
    for c in profile.get('categories', []):
        if c not in rec['categories']:
            rec['categories'].append(c)
    entry = {
        'attempt': attempt,
        'outcome': outcome,
        'severity': profile.get('severity'),
        'categories': profile.get('categories', []),
        'reasons': profile.get('reasons', []),
        'policies': profile.get('policies', []),
        'error_signature': hashlib.sha256((err_txt or '').encode()).hexdigest()[:12],
        'error_excerpt': (err_txt or '')[:1600],
    }
    if body is not None:
        entry['body_excerpt'] = body[:1200]
    rec['attempts'].append(entry)
    for h in proposed or []:
        hrec = {k: h.get(k) for k in ('name', 'statement', 'why', 'origin', 'cluster') if h.get(k) is not None}
        if hrec and hrec not in rec['proposed_helpers']:
            rec['proposed_helpers'].append(hrec)
    json.dump(pool, open(pool_p, 'w'), indent=2)


# --------------------------------------------------------------------------- check-theory
def localize_timeout(item, body, locked, verify_of, idir, budget_s=120, max_probes=4):
    """Bisect the apply-steps of a timed-out `body` to find the first step whose
    cumulative prefix does not finish within budget_s. Returns a one-line hint
    naming the culprit step, or None. Each probe is a fast check (residual-goal
    errors return quickly; only a genuine explosion hits the short cap)."""
    stmt = item["_canonical"]
    steps = split_proof_steps(body)
    if len(steps) < 2:
        return None
    lo, hi, culprit = 1, len(steps), None
    probes = 0
    while lo <= hi and probes < max_probes:
        mid = (lo + hi) // 2
        probes += 1
        prefix_body = "\n  ".join(steps[:mid])
        cand = (item, stmt + "\n  " + prefix_body + "\n  oops")
        try:
            ok, wall, out = verify_of(cand, budget_s)
        except Exception:
            break
        if "killed after" in out:          # explosion already within first `mid` steps
            culprit, hi = mid, mid - 1
        else:                               # first `mid` steps are fast
            lo = mid + 1
    if culprit:
        info = {'step': culprit, 'command': steps[culprit-1]}
        (idir / "timeout-localized.json").write_text(json.dumps(info, indent=2))
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
   {{"kind": "verbatim_def"|"verbatim_decl"|"fixed"|"helper",
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
        if not n or n == tgt or it["kind"] in {"verbatim_def", "verbatim_decl"}:
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


def historical_cases(item, theory_base, exp, cap=4, max_lines=50, errors=None):
    """Mine prior regen runs as a small case library.

    This is intentionally separate from sibling_examples(): siblings are facts
    locked in the *current* prefix and therefore usable as Isabelle facts.
    Historical cases are prompt-only guidance from previous Claude/Codex runs;
    the model may copy proof shape, but must only cite facts available in the
    current prefix/imports.
    """
    tgt = item.get("name") or ""
    if not tgt:
        return []
    tt = _name_tokens(tgt)
    err_toks = {t for t in re.findall(r"[A-Za-z_][A-Za-z0-9_']{5,}", errors or "")}
    tsuf = re.search(r"(_[A-Za-z0-9']+'?)$", tgt)
    tsuf = tsuf.group(1) if tsuf else None
    root = REPO / "reports" / "regen"
    if not root.exists():
        return []

    scored = []

    def add_case(score, source, outcome, name, text, note=""):
        if not text or len(text.strip()) < 20:
            return
        lines = text.strip().splitlines()
        if len(lines) > max_lines:
            lines = lines[:max_lines] + ["  (* ... truncated historical case ... *)"]
        scored.append((score, source, outcome, name, "\n".join(lines), note))

    for run in root.glob(f"{theory_base.lower()}-*"):
        if run == exp or not run.is_dir():
            continue
        locked_p = run / "locked.json"
        if locked_p.exists():
            try:
                entries = json.load(open(locked_p))
            except Exception:
                entries = []
            for e in entries:
                it = e.get("item", {})
                name = it.get("name") or ""
                if not name or it.get("kind") in {"verbatim_def", "verbatim_decl"}:
                    continue
                st = _name_tokens(name)
                score = len(tt & st) * 6
                if name == tgt:
                    score += 80
                if tsuf and name.endswith(tsuf):
                    score += 8
                text = e.get("text", "")
                if tgt in text:
                    score += 10
                score += min(24, sum(3 for tok in err_toks if tok in text or tok in name))
                if score >= 14:
                    add_case(score, str(locked_p.relative_to(REPO)), "LOCKED", name, text)

        # Exact-name attempts are useful even when they failed: they preserve
        # Claude's exploration and expose anti-patterns such as timeout-heavy
        # fastforce or pseudo-methods that do not exist in Isabelle.
        for body in run.glob(f"items/*-{tgt}/body-*.thy"):
            score = 70
            check = body.with_name(body.name.replace("body-", "check-").replace(".thy", ".log"))
            note = ""
            outcome = "ATTEMPT"
            if check.exists():
                try:
                    first = check.read_text(errors="ignore").splitlines()[:3]
                except Exception:
                    first = []
                note = " ".join(first)[:240]
                if "PASSED" in note:
                    outcome = "PASSED ATTEMPT"
                    score += 20
                elif "killed after" in note or "timeout" in note.lower():
                    outcome = "TIMEOUT ATTEMPT"
                    score += 8
                elif "Undefined method" in note or "Undefined fact" in note:
                    outcome = "FAILED ATTEMPT"
            text = body.read_text(errors="ignore")
            score += min(24, sum(3 for tok in err_toks if tok in text))
            add_case(score, str(body.relative_to(REPO)), outcome, tgt, text, note)

    best = {}
    for row in scored:
        score, source, outcome, name, text, note = row
        key = re.sub(r"\s+", " ", text)
        if key not in best or score > best[key][0]:
            best[key] = row
    ranked_all = sorted(best.values(), key=lambda r: (-r[0], r[1]))
    locked_ranked = [r for r in ranked_all if r[2] == "LOCKED"]
    attempt_ranked = [r for r in ranked_all if r[2] != "LOCKED"]
    # Keep both positive examples and negative exact-name archaeology. A pile of
    # timeout attempts is less useful than one or two plus nearby locked proofs.
    ranked = attempt_ranked[:min(2, cap)]
    ranked += locked_ranked[:max(0, cap - len(ranked))]
    if len(ranked) < cap:
        ranked += attempt_ranked[len(ranked):cap]
    out = []
    for _, source, outcome, name, text, note in ranked:
        hdr = f"(* {outcome}: {name} from {source}"
        if note:
            hdr += f"; checker note: {note}"
        hdr += " *)"
        out.append(f"{hdr}\n{text}")
    return out


def item_prompt(spec, item, proved, body_hint=None, prev_body=None, errors=None,
                human_hint=None, template_body=None, import_facts=None,
                siblings=None, historical=None, hard_profile=None):
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
    p += hard_mode_prompt_section(hard_profile)
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
    if historical:
        ex = "\n\n".join(f"```isabelle\n{e}\n```" for e in historical)
        p += (f"\n\nHISTORICAL CLAUDE/CODEX CASES from previous regeneration runs "
              f"(prompt-only guidance, NOT automatically available facts). Reuse "
              f"proof shape and tactic sequencing, but cite only facts shown in "
              f"the current locked/import context. Avoid repeating historical "
              f"anti-patterns called out by checker notes, especially timeout-heavy "
              f"fastforce chains and pseudo-methods that are not defined:\n{ex}")
    if import_facts:
        fx = "\n".join(
            f"  [{FACT_THEORY.get(n, '?')}] {n}: {s}" for _, n, s in import_facts)
        p += (f"\n\nPossibly relevant PREMISES from IMPORTED theories ([theory] "
              f"disambiguates same-named lemmas across files — cite the qualified "
              f"one on collision). Fixed environment, cite freely — prefer these "
              f"over unfolding raw definitions:\n{fx}")
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


def replan_prompt(spec, item, errors, is_fixed, template_body=None, hard_profile=None):
    stmt = item["_canonical"]
    helper_naming = (
        "Helper naming rule: do NOT name generated helpers as new contract-level "
        "`*_corres` facts unless that exact name is already a fixed ground-truth "
        "item. If the helper proves a local correspondence shape, name it with a "
        "role suffix such as `_bridge`, `_fragment`, `_branch`, or `_case`. "
        "For if/case failures, do not split a branch before the discriminator "
        "correspondence has been established; prefer a helper for the discriminator "
        "or the nearest enclosing correspondence that aligns the branches."
    )
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
{helper_naming}
{hard_mode_prompt_section(hard_profile)}
For hard failures, follow lemma-pool style exploration: propose helpers that are independently checkable and map each helper to one residual obligation class. Do not use a helper as a dumping ground for the whole failed proof.

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
        tries = s.get("tries", "?")
        md += [f"## [{s.get('kind', '?')}] `{s.get('name', '?')}`  ({s.get('reason', '?')}, {tries} tries)",
               "```isabelle", s.get("statement", "?"), "```",
               f"artifacts: `{s.get('item_dir', '?')}`", "```", s.get("errors", "")[:2000], "```", ""]
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
    ap.add_argument("--provider", choices=["claude", "codex", "codex-relay", "file-agent"],
                    default=os.environ.get("REGEN_PROVIDER", "claude"),
                    help="LLM generator backend: claude uses `claude -p`; codex uses `codex exec`; codex-relay uses the local HTTP relay; file-agent waits for a response file.")
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
    ap.add_argument("--unpark", default="",
                    help="comma-separated stuck item names to retry on --resume-from. "
                         "Useful after improving retrieval/harness or adding hints.")
    ap.add_argument("--unpark-roots", action="store_true",
                    help="on --resume-from, retry stuck items that are dependency roots "
                         "rather than carried dependents. This is a cluster-level "
                         "recovery lever after fixing a root-cause harness issue.")
    ap.add_argument("--focus", default="",
                    help="regex for focused recovery runs: only matching item names, "
                         "clusters, or serves targets are attempted; other unlocked "
                         "items are skipped without adding stuck records.")
    ap.add_argument("--historical-cases", type=int,
                    default=int(os.environ.get("REGEN_HISTORICAL_CASES", "4")),
                    help="number of prompt-only proof cases mined from previous "
                         "reports/regen runs. These preserve prior Claude/Codex "
                         "case context while keeping current-prefix fact hygiene.")
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
    ap.add_argument("--no-hard-lemma-mode", action="store_true",
                    help="disable hard-failure classification, hard-mode prompts, and hard_lemma_pool.json recording")
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
    log(f"provider={args.provider} model={args.model} item-tries={args.item_tries} check-budget={args.check_budget}")

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
    sh(["python3", "regen/preparation/regen_extract.py", "--refine", REFINE_DIR,
        "--out", str(exp / "keepset.json"), str(tp)])
    sh(["python3", "regen/legacy/whole_theory/regen_strip.py", "--keepset", str(exp / "keepset.json"),
        "--theory", str(tp), "--outdir", str(exp)])
    spec = json.load(open(exp / "task_spec.json"))
    fixed_by_name = {k["name"]: k["statement"] for k in spec["to_prove"]}
    defs_by_name = {k["name"]: k["text"] for k in spec.get("verbatim", [])}
    log(f"[1] keepset: {len(fixed_by_name)} fixed, {len(defs_by_name)} verbatim defs")

    # template arm: human original bodies by lemma name (upper control arm)
    orig_bodies = {}
    if args.arm == "template":
        from regen.preparation.regen_extract import slice_decls
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
    # premise library: same ancestor closure, but records file/theory and keeps
    # same-name collisions (import_index setdefault drops them). Powers [theory]
    # tags on retrieved premises + the replan reuse-gate. Best-effort; a failure
    # here must not kill the run (the token index above already covers retrieval).
    global PREMISE_RECORDS, FACT_THEORY
    try:
        from regen.legacy.mode_r.premise_db import build as _pdb_build
        PREMISE_RECORDS, _pmeta, _ = _pdb_build(tp, write=False)
        for r in PREMISE_RECORDS:
            FACT_THEORY.setdefault(r["name"], r["theory"])
        log(f"[1] premise library: {_pmeta['n_records']} records, "
            f"{_pmeta['n_collision_names']} same-name collisions (file-disambiguated)")
    except Exception as e:
        log(f"[1] premise library unavailable ({e}); retrieval falls back to token index")

    # [2] PLAN (or reuse a prior run's plan on --resume-from)
    if args.resume_from:
        old = Path(args.resume_from)
        plan = json.load(open(old / "plan.json"))
        items = plan["items"]
        log(f"[2] plan REUSED from {old} ({len(items)} items)")
    else:
        log(f"[2] PLAN ({args.provider}) ...")
        reply = llm_call(args.provider, plan_prompt(spec), args.model, args.plan_timeout,
                            str(exp / "plan-transcript.ndjson"), max_turns="3")
        (exp / "plan-raw.txt").write_text(reply)
        plan = json_loads_fenced(reply, "json")
        items = plan["items"]
    # validate + canonicalize
    # drop plan helpers that shadow an IMPORT lemma: the import already provides
    # the fact, and a same-named divergent helper silently shadows it, breaking
    # composability (see replan filter below for the tcbQueueHead_iff_tcbQueueEnd
    # case). Downstream items citing the name resolve to the imported version.
    _shadow = [it for it in items
               if it["kind"] == "helper"
               and it.get("name") in import_index
               and it.get("name") not in defs_by_name]
    for it in _shadow:
        log(f"[2] plan helper `{it['name']}` shadows an IMPORT lemma — dropped "
            f"(use the imported one)")
    if _shadow:
        items = [it for it in items if it not in _shadow]

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
        elif it["kind"] == "verbatim_decl":
            assert it.get("_canonical"), f"verbatim_decl {it.get('name')} missing _canonical"
            assert not re.search(r'\b(sorry|oops|axiomatization)\b', it["_canonical"])
        else:
            it["statement"] = normalize_statement_text(it.get("statement"))
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
        log(f"[2b] PLAN CRITIQUE ({args.provider}) ...")
        reply = llm_call(args.provider, critique_prompt(spec, items), args.model,
                            args.plan_timeout, str(exp / "critique-transcript.ndjson"))
        (exp / "critique-raw.txt").write_text(reply)
        added = 0
        try:
            crit = json_loads_fenced(reply, "json")
            for entry in crit.get("scaffolding", []):
                serves = entry.get("serves")
                tgt = next((k for k, x in enumerate(items)
                            if x.get("name") == serves and x["kind"] == "fixed"), None)
                if tgt is None:
                    continue
                new = []
                for h in entry.get("helpers", []):
                    st = normalize_statement_text(h.get("statement", ""))
                    h["statement"] = st
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
            # (measured: >=2 zombie parks). If the blocker is now locked, free it.
            # Manual/root unpark handles the next case: the blocker was not locked,
            # but the harness changed (retrieval/import aliases/hints), so the right
            # move is to retry roots while continuing to carry their dependents.
            locked_names = {e["item"].get("name")
                            for e in json.load(open(old / "locked.json"))} \
                if (old / "locked.json").exists() else set()
            manual_unpark = {n.strip() for n in re.split(r"[,\s]+", args.unpark or "") if n.strip()}
            dep_re = re.compile(r"depends on stuck item `([^`]+)`")
            stuck_names_all = {s_.get("name") for s_ in old_stuck}
            dependent_blockers = {m.group(1) for s_ in old_stuck
                                  for m in [dep_re.match(s_.get("reason", ""))] if m}
            root_unpark = (stuck_names_all - dependent_blockers) if args.unpark_roots else set()
            requested_unpark = manual_unpark | root_unpark
            kept, freed, stale = [], [], []
            for s_ in old_stuck:
                m_dep = dep_re.match(s_.get("reason", ""))
                name_s = s_.get("name")
                if m_dep and m_dep.group(1) in locked_names:
                    stale.append(name_s)
                elif name_s in requested_unpark or (m_dep and m_dep.group(1) in requested_unpark):
                    freed.append(name_s)
                else:
                    kept.append(s_)
            old_stuck = kept
            json.dump(old_stuck, open(exp / "stuck.json", "w"), indent=2)
            carried_stuck = {s["name"] for s in old_stuck}
            missing_unpark = manual_unpark - stuck_names_all
            if missing_unpark:
                log(f"[3] requested unpark name(s) not in carried stuck: {sorted(missing_unpark)}")
            log(f"[3] carried {len(carried_stuck)} stuck item(s); "
                f"auto-un-parked stale={len(stale)} {stale}; "
                f"requested/root-unparked={len(freed)} {freed}")
        if (old / "locked.json").exists():
            # preferred: exact locked state persisted by the prior run. Items are
            # matched BY NAME (not prefix count): with the stuck-queue, locked is
            # no longer a contiguous prefix of the plan — skipped items get
            # re-attempted, already-locked ones are skipped in the main loop.
            for e in json.load(open(old / "locked.json")):
                it_l, txt = e["item"], e["text"]
                locked.append((it_l, txt))
                locked_keys.add((it_l.get("name"), it_l["kind"]))
                if it_l["kind"] not in {"verbatim_def", "verbatim_decl"}:
                    proved_stmts.append((it_l.get("name", "?"),
                                         " ".join(it_l["_canonical"].split())[:160]))
            i = 0
        else:
            # fallback: reconstruct from the prior run's items/ dirs
            for idx, it in enumerate(items):
                name = it.get("name") or f"item{idx}"
                idir_old = old / "items" / f"{idx:02d}-{name}"
                if it["kind"] in {"verbatim_def", "verbatim_decl"}:
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
            # the seal BUILD is itself a full verification of the prefix. If it
            # fails for infrastructure reasons (container cold start, stale lock),
            # do not abort the whole focused recovery run; fall back to the stable
            # whole-prefix verifier and keep the failure in seals/*/build.log.
            do_seal()
            if seal["cur"] is not None:
                log(f"[3] resume: {len(locked)} items locked; sealed into heap "
                    f"(incremental checks active); continuing at item {i+1}/{len(items)}")
            else:
                log("[3] resume seal unavailable — falling back to whole-prefix check")
                ok, wall, out = verify_prefix()
                assert ok, "resumed prefix does not check — resume aborted"
                log(f"[3] resume: {len(locked)} items locked from prior run "
                    f"[prefix re-verified, {wall}ms]; continuing at item {i+1}/{len(items)}")
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
        if args.focus:
            focus_haystack = " ".join(str(x or "") for x in
                                      (name, it.get("cluster"), it.get("serves")))
            if not re.search(args.focus, focus_haystack):
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

        if it["kind"] in {"verbatim_def", "verbatim_decl"}:
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
            locked_keys.add((it.get("name"), it["kind"]))
            save_locked()
            maybe_reseal()
            i += 1
            continue

        # lemma item (fixed or helper): generate body, verify, retry
        prev_body, err_txt, passed, parked = None, None, False, False
        current_hard_profile = None
        prev_sig, prev_nblocks, prev_ngoals = None, None, None
        prev_strat_key = None
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
            hist = historical_cases(it, tbase, exp, cap=args.historical_cases, errors=err_txt) \
                if args.historical_cases and args.arm != "template" else None
            if sibs and t == 1:
                log(f"  [few-shot: {len(sibs)} sibling proof(s) from same family]")
            if hist and t == 1:
                log(f"  [historical cases: {len(hist)} prior proof/attempt case(s)]")
            reply = llm_call(args.provider, item_prompt(spec, it, proved_stmts,
                                            body_hint=hint, prev_body=prev_body,
                                            errors=err_txt, human_hint=hh,
                                            template_body=orig_bodies.get(name),
                                            import_facts=imp, siblings=sibs,
                                            historical=hist, hard_profile=current_hard_profile),
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
                # relation-unfold explosion (measured: ready_qs_runnable_cross,
                # setCurThread B2) — `simp add: state_relation_def` unfolds the whole
                # (huge) refinement relation. The generic timeout hint says "avoid
                # fastforce"; this one names the PROJECTION lemmas + sub-relations to
                # use instead. Fires when the exploding body touches a *_relation_def.
                if re.search(r'\bstate_relation_def\b|\b\w*_relation_def\b', body or ""):
                    strat = STRATEGY_HINTS["relation_explosion"]
                else:
                    strat = STRATEGY_HINTS["timeout"]
                # IMPROVEMENT 1 — localize the exploding step (timeout was a
                # black hole: agent couldn't see WHERE it hung). Bisect the
                # apply-steps: a truncated prefix either fails FAST with residual
                # goals (explosion is later) or times out (explosion is here).
                loc = localize_timeout(it, body, locked, verify_prefix_of, idir)
                if loc:
                    strat += f"\n{loc}"
                    bad_step = None
                    loc_json = idir / "timeout-localized.json"
                    if loc_json.exists():
                        try:
                            bad_step = json.load(open(loc_json)).get("step")
                        except Exception:
                            bad_step = None
                    repair_note = structural_repair_probe(it, body, verify_prefix_of, idir, bad_step)
                    if repair_note:
                        strat += f"\n{repair_note}"
            elif 'stateAssert' in out and 'Failed to apply proof method' in out:
                # a corres_stateAssert_* rule mis-selected/mis-instantiated leaves
                # the whole goal untouched with the stateAssert still present. The
                # generic same_goals hint doesn't name the discriminating combinator;
                # this one does. Generic (fires for any stalled stateAssert corres),
                # so a close under it stays pure-machine (measured: setCurThread_corres
                # picked corres_stateAssert_implied[where P'=<concrete pred>], a type
                # mismatch — the right rule is corres_stateAssert_add_assertion).
                strat = STRATEGY_HINTS["stateassert"]
            elif re.search(r'\?[A-Z]', out) and ('No such variable' in out
                                                 or 'schematic' in out.lower()
                                                 or re.search(r'\?Q\d?\b', out)):
                strat = STRATEGY_HINTS["schematic"]
            elif prev_ngoals is not None and n_goals is not None and n_goals > prev_ngoals:
                strat = STRATEGY_HINTS["growing"]
            elif sig == prev_sig:
                strat = STRATEGY_HINTS["same_goals"]
            extra_branch = branch_subgoal_directive(out, body)
            if extra_branch and not strat:
                strat = extra_branch
            elif extra_branch and extra_branch not in strat:
                strat += f"\n\nBRANCH-SUBGOAL DIRECTIVE: {extra_branch}"
            strat_key = next((k for k, v in STRATEGY_HINTS.items()
                              if strat and strat.startswith(v[:30])), None)
            err_txt = f"Error blocks this try: {nblocks}{trend}.\n{summary}"
            if strat:
                err_txt += f"\n\nSTRATEGY DIRECTIVE: {strat}"
            if not args.no_hard_lemma_mode:
                current_hard_profile = classify_hard_failure(it, out, body, err_txt)
                if current_hard_profile.get("hard_mode"):
                    update_hard_lemma_pool(exp, it, t, current_hard_profile, err_txt, body=body)
                    (idir / f"hard-profile-{t}.json").write_text(
                        json.dumps(current_hard_profile, indent=2))
                    log(f"  hard-mode: {current_hard_profile['severity']} "
                        f"{current_hard_profile['categories']}")
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
            # AND the SAME strategy directive already shown. Guillotining on identical
            # errors alone kills a run the moment a NEWLY-triggered directive appears
            # (measured: setCurThread stuck at try2 before its stateassert directive —
            # first issued from try1's error — got a fair shot). Require the model to
            # have SEEN this directive category once and still not moved.
            if sig == prev_sig and strat_key == prev_strat_key:
                if (not args.no_hard_lemma_mode and current_hard_profile
                        and current_hard_profile.get("hard_mode")
                        and it["kind"] == "fixed" and not it.get("_replanned")):
                    log("  hard-mode no-progress: leaving retry loop for lemma-pool replan")
                    prev_sig, prev_nblocks, prev_body, prev_ngoals = sig, nblocks, body, n_goals
                    prev_strat_key = strat_key
                    break
                queue_stuck(exp, it, idir, t, err_txt,
                            "two consecutive tries produced IDENTICAL errors under the "
                            "SAME strategy directive — feedback not moving the model")
                parked = True
                break
            prev_sig, prev_nblocks, prev_body, prev_ngoals = sig, nblocks, body, n_goals
            prev_strat_key = strat_key
        if passed or parked:
            i += 1
            continue

        # retries exhausted -> replan this item (once), else park it
        if it.get("_replanned"):
            queue_stuck(exp, it, idir, args.item_tries, err_txt,
                        "tries exhausted after replan")
            i += 1
            continue
        log(f"  REPLAN {name} ({args.provider}) ...")
        if not args.no_hard_lemma_mode and current_hard_profile is None:
            current_hard_profile = classify_hard_failure(it, '', prev_body or '', err_txt or '')
        reply = llm_call(args.provider, replan_prompt(spec, it, err_txt or "", it["kind"] == "fixed",
                                          template_body=orig_bodies.get(name),
                                          hard_profile=None if args.no_hard_lemma_mode else current_hard_profile),
                            args.model, args.timeout, str(idir / "replan.ndjson"))
        try:
            rp = json_loads_fenced(reply, "json")
        except ValueError:
            queue_stuck(exp, it, idir, args.item_tries, err_txt, "replan unparseable")
            i += 1
            continue
        it["_replanned"] = True
        # name-collision pre-check: a replan helper reusing an existing item/
        # locked name causes "Duplicate fact declaration"; reusing an IMPORT name
        # is worse — it SHADOWS the imported lemma with a divergent (specialized)
        # statement, silently breaking composability (measured: machine's
        # tcbQueueHead_iff_tcbQueueEnd shadowed TcbAcc_R's general version, no `ts`
        # → a human proof citing it failed "No such variable ?ts"). A helper that
        # duplicates an import is redundant anyway — the import already provides it.
        taken = {x.get("name") for x in items} | {l[0].get("name") for l in locked}

        def _fresh(hs):
            out = []
            for h in hs:
                h["statement"] = normalize_statement_text(h.get("statement", ""))
                nm = h.get("name")
                if nm in taken:
                    log(f"  replan helper `{nm}` collides with existing item — dropped")
                    continue
                if nm in import_index or nm in import_consts:
                    log(f"  replan helper `{nm}` shadows an IMPORT lemma — dropped "
                        f"(use the imported one; don't redefine it)")
                    continue
                # premise reuse-gate: exact-name shadows are dropped above; here we
                # catch NEAR-named library lemmas — a proposed helper that is likely a
                # re-invention / misname / hallucination of an existing premise
                # (measured: ready_qs_runnable_cross, tcbQueueAppend_corres). Don't
                # drop (it may be genuinely new), but surface the neighbours in `why`
                # so the model cites/specializes an existing lemma instead of proving
                # a fresh one — the dedup lever.
                if PREMISE_RECORDS:
                    try:
                        from regen.legacy.mode_r.premise_db import recall_name
                        near = recall_name(PREMISE_RECORDS, nm, k=3)["fuzzy"]
                        if near:
                            hint = "; ".join(
                                f"{r['name']} [{r['theory']}]: {r['stmt'][:80]}"
                                for r in near)
                            h["why"] = (h.get("why", "") +
                                        f" [reuse-gate: existing library lemmas with "
                                        f"similar names — cite/instantiate one of these "
                                        f"if it serves, don't reprove: {hint}]")
                            log(f"  replan helper `{nm}` near library premises: "
                                f"{[r['name'] for r in near]}")
                    except Exception:
                        pass
                out.append(h)
            return out

        if it["kind"] == "fixed":
            new = _fresh(rp.get("helpers", []))
            for h in new:
                h.update(kind="helper", _canonical=h["statement"], origin="replan",
                         cluster=it.get("cluster", name))   # inherit parent cluster
            if not args.no_hard_lemma_mode and current_hard_profile:
                update_hard_lemma_pool(exp, it, args.item_tries, current_hard_profile,
                                       err_txt or '', proposed=new, outcome='replanned')
            items[i:i] = new       # insert before the fixed item; loop re-enters
            plan["items"] = items
            json.dump(plan, open(exp / "plan.json", "w"), indent=2)
            log(f"  inserted {len(new)} helper(s) before {name}")
        else:
            new = _fresh(rp.get("replace", []))
            for h in new:
                h.update(kind="helper", _canonical=h["statement"], _replanned=True,
                         origin="replan", cluster=it.get("cluster", name))
            if not args.no_hard_lemma_mode and current_hard_profile:
                update_hard_lemma_pool(exp, it, args.item_tries, current_hard_profile,
                                       err_txt or '', proposed=new, outcome='replanned')
            items[i:i+1] = new
            plan["items"] = items
            json.dump(plan, open(exp / "plan.json", "w"), indent=2)
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
    n_defs = sum(1 for it, _ in locked if it["kind"] in {"verbatim_def", "verbatim_decl"})
    # structure-discovery split: how much scaffolding was planned upfront vs
    # predicted by the critique pass vs discovered reactively via replan
    from collections import Counter
    helper_origin = Counter(it.get("origin", "plan") for it, _ in locked
                            if it["kind"] == "helper")
    over_budget = len(human_bodies) > args.human_budget
    hard_pool = json.load(open(exp / "hard_lemma_pool.json")) if (exp / "hard_lemma_pool.json").exists() else {"items": {}}
    meas = dict(final_green=final_ok and not over_budget,
                green_but_over_human_budget=final_ok and over_budget,
                arm=args.arm,
                items_total=len(items), items_locked=n_locked, items_stuck=n_stuck,
                machine_locked_lemmas=n_locked - n_defs - len(human_bodies),
                helper_origin_split=dict(helper_origin),
                hard_lemma_mode=not args.no_hard_lemma_mode,
                hard_lemma_items=len(hard_pool.get("items", {})),
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
