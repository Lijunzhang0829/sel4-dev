#!/usr/bin/env python3
"""spec_slot_hints.py — per-theory mechanical hint scanner for Q/P slots.

WHY: the agent-only scan proved unable to find real Q/P slots — asked for
--slot Q on a frame-lemma file it silently fell back to F proposals. The
design (execute-additive-design.md §6.3) prescribes the fix: a mechanical
detector supplies "directions worth probing", the LLM turns them into concrete
candidate statements, the trial decides truth. This is that detector layer.

Q hints — redirect-shaped proofs (the proof PASSES THROUGH a stronger post,
then deliberately weakens it). Two kinds, very different value:

  inline-redirect  (TRUE Q gap — the strong post has NO name):
      apply (rule_tac Q="<Q_strong text>" in hoare_post_imp)   <- Q_strong text
      apply (rule hoare_strengthen_post)  + following wp/simp inline chain
    The stronger postcondition is established inline and never exposed as a
    lemma. Adding L' = ⟨same pre⟩ op ⟨Q_strong⟩ fills a real slot.

  named-redirect   (slot ALREADY FILLED — low value, alias only):
      apply (rule hoare_strengthen_post, rule <strong_rule>)
      apply (rule hoare_strengthen_post[OF <strong_rule>])
    The strong fact already exists by name; a new lemma would be an alias
    (0054-style). Reported with kind=named-redirect so the consumer can
    deprioritize.

P hints — a precondition conjunct never mentioned in the proof body:
    lemma L: ⟨P_a and P_b and P_c⟩ op ⟨Q⟩  where the proof text contains no
    reference to P_c's head identifier. This is the design's §3.3 static
    precheck (explicit-consumption HARD_LB filter); the wp-chain implicit
    dependency is left to the trial.

Output: JSON array of hints on stdout (and --out FILE):
  {"slot":"Q"|"P", "kind":..., "lemma":..., "line":N, "statement":...,
   "evidence":..., "strong_rule":...|null, "q_strong_text":...|null,
   "premise":...|null, "priority":"high"|"low"}

Usage: spec_slot_hints.py <theory.thy> [--slot Q|P|all] [--out hints.json]
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

REDIRECT_RE = re.compile(r"hoare_strengthen_post\w*|hoare_post_imp\w*")
# rule_tac Q="..." in hoare_post_imp / hoare_strengthen_post  (Q_strong text!)
RULE_TAC_Q_RE = re.compile(
    r'rule_tac\s+Q[0-9\']*\s*=\s*"((?:[^"\\]|\\.)*)"\s+in\s+'
    r'(hoare_post_imp\w*|hoare_strengthen_post\w*)')
# named redirect: strengthen_post applied directly to a named rule
NAMED_FORMS = [
    re.compile(r"(?:hoare_strengthen_post\w*|hoare_post_imp\w*)\s*\[\s*OF\s+([A-Za-z_][\w.']*)"),
    re.compile(r"(?:rule|erule)\s+(?:hoare_strengthen_post\w*|hoare_post_imp\w*)\s*,\s*rule\s+([A-Za-z_][\w.']*)"),
]
# tactics that mean "the strong post is being proven INLINE"
INLINE_NEXT_RE = re.compile(
    r"^\s*(apply\s*\(?\s*)?(wp|wpsimp|simp|clarsimp|fastforce|force|auto|wpc"
    r"|cases|rule\s+conjI|intro|subst|unfold)\b")
# schematic / trivial conjuncts to skip in P scan
P_SKIP_HEADS = {"", "p", "q", "r", "k", "s", "t", "x", "top", "true"}
P_KNOWN_LOADBEARING_HINTS = ()  # left empty: trial decides; no semantic guessing


def parse_lemmas(text):
    """Yield dicts {name, line, statement, proof, proof_start}. Best-effort:
    only simple `lemma <name>[attrs]: "<stmt>"` forms; assumes/shows skipped
    for statement but proof still captured for Q scan."""
    lines = text.splitlines()
    n = len(lines)
    i = 0
    out = []
    while i < n:
        m = re.match(r"^lemma\s+([A-Za-z_][\w']*)", lines[i])
        if not m:
            i += 1
            continue
        name = m.group(1)
        decl_line = i + 1  # 1-based
        # find statement: first balanced double-quoted block after the header
        j = i
        stmt_chunks = []
        in_stmt = False
        stmt_done = False
        has_assumes = False
        while j < n:
            ln = lines[j]
            if j > i and re.match(r"^(lemma|theorem|corollary|definition|end|crunch|context|locale)\b", ln):
                break
            if re.search(r"\bassumes\b|\bshows\b|\bfixes\b", ln):
                has_assumes = True
            if not stmt_done:
                k = 0
                while k < len(ln):
                    c = ln[k]
                    if c == '"':
                        if in_stmt:
                            in_stmt = False
                            stmt_done = True
                            k += 1
                            break
                        in_stmt = True
                    elif in_stmt:
                        stmt_chunks.append(c)
                    k += 1
                if in_stmt:
                    stmt_chunks.append("\n")
                if stmt_done:
                    proof_start = j + 1
            j += 1
        if not stmt_done:
            i += 1
            continue
        # proof: lines after the statement up to terminator.
        # Isar-aware (defect G): inside a structured `proof ... qed` block, a
        # `by` line closes a sub-goal, NOT the lemma — terminating there would
        # truncate the captured proof and mislead the Q position/redirect scan.
        # Track proof-block nesting; `by`/`done`/`.` only terminate at depth 0.
        proof_lines = []
        pj = proof_start
        proof_depth = 0
        while pj < n:
            ln = lines[pj]
            if re.match(r"^(lemma|theorem|corollary|definition|end|crunch|context|locale|lemmas)\b", ln):
                break
            proof_lines.append(ln)
            stripped = ln.strip()
            if re.match(r"^proof\b", stripped):
                proof_depth += 1
            elif re.match(r"^qed\b", stripped):
                proof_depth -= 1
                if proof_depth <= 0:
                    break
            elif proof_depth == 0 and (
                    re.match(r"^done\s*$", stripped)
                    or re.match(r"^by\b", stripped)
                    or re.match(r"^\.\.?\s*$", stripped)):
                break
            pj += 1
        out.append({"name": name, "line": decl_line,
                    "statement": "".join(stmt_chunks).strip(),
                    "has_assumes": has_assumes,
                    "proof": "\n".join(proof_lines),
                    "proof_lines": proof_lines})
        i = max(pj, j - 1) + 1 if proof_lines else j
    return out


# ---------------------------------------------------------------- Q scan ----
def _tactic_index(plines, needle_re):
    """Index (0-based, counting only non-empty tactic lines) of the first line
    matching needle_re; None if absent."""
    ti = 0
    for ln in plines:
        if not ln.strip():
            continue
        if needle_re.search(ln):
            return ti
        ti += 1
    return None


def _norm(s):
    return re.sub(r"\s+", "", s or "")


def _strip_outer_parens(s):
    """Drop fully-enclosing matched parentheses: `(P)` -> `P`, `((P))` -> `P`,
    but leave `(P) and (Q)` untouched (the first `(` does not match the last
    `)`). Used so a retained conjunct re-parenthesized by the agent still
    compares equal in the premise-subset check (defect F)."""
    s = s.strip()
    while len(s) >= 2 and s[0] == "(" and s[-1] == ")":
        depth = 0
        encloses = True
        for i, ch in enumerate(s):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and i != len(s) - 1:
                    encloses = False
                    break
        if encloses:
            s = s[1:-1].strip()
        else:
            break
    return s


def _normc(s):
    """Conjunct/post normalizer for structural comparison: strip enclosing
    parens then all whitespace. Tolerates cosmetic re-parenthesization."""
    return _norm(_strip_outer_parens(s or ""))


def scan_q(lemmas):
    hints = []
    for lm in lemmas:
        proof = lm["proof"]
        if not REDIRECT_RE.search(proof):
            continue
        plines = lm["proof_lines"]
        kind = None
        strong_rule = None
        q_text = None
        evidence = None
        # POSITION matters: a redirect in the first 1-2 tactics describes the
        # LEMMA'S OWN post (the whole proof is "prove stronger, then weaken").
        # A mid-proof redirect describes an intermediate Hoare step of some
        # inner call — still a possible Q hint for that inner op, but it says
        # nothing about this lemma's post. Leading → high, interior → low.
        pos_idx = _tactic_index(plines, REDIRECT_RE)
        position = "leading" if (pos_idx is not None and pos_idx <= 1) else "interior"
        # 1) rule_tac Q="..." — explicit Q_strong text (best signal)
        mt = RULE_TAC_Q_RE.search(proof)
        if mt:
            kind, q_text = "inline-redirect", mt.group(1).strip()
            evidence = f"explicit stronger post in rule_tac Q=\"...\" in {mt.group(2)}"
            # the lemma's own post — if the redirect text only restates it,
            # there is no strengthening to expose
            mpost = re.findall(r"\\<lbrace>(.*?)\\<rbrace>", lm["statement"], re.DOTALL)
            if len(mpost) >= 2 and _norm(q_text) == _norm(mpost[1]):
                continue
        else:
            # 2) named redirect forms
            for rex in NAMED_FORMS:
                mn = rex.search(proof)
                if mn:
                    kind, strong_rule = "named-redirect", mn.group(1)
                    evidence = f"redirects to existing named rule `{strong_rule}` — slot already filled"
                    break
            # 3) bare strengthen_post followed by an inline tactic chain
            if kind is None:
                for idx, ln in enumerate(plines):
                    if REDIRECT_RE.search(ln) and "rule_tac" not in ln:
                        rest = ln.split("hoare_", 1)[-1]
                        if re.search(r",\s*rule\s+[A-Za-z_]", rest) or "[OF" in rest:
                            continue  # named, caught above normally
                        nxt = plines[idx + 1] if idx + 1 < len(plines) else ""
                        if INLINE_NEXT_RE.match(nxt) or nxt.strip() == "":
                            kind = "inline-redirect"
                            evidence = ("bare strengthen-post followed by inline proof "
                                        "— stronger post proven but never named")
                            break
        if kind is None:
            continue
        if kind == "inline-redirect":
            priority = "high" if position == "leading" else "low"
            if position == "interior":
                evidence += (" [INTERIOR redirect — describes an inner Hoare "
                             "step, not this lemma's post; verify which op it "
                             "strengthens]")
        else:
            priority = "low"
        hints.append({
            "slot": "Q", "kind": kind, "lemma": lm["name"], "line": lm["line"],
            "statement": lm["statement"][:300], "evidence": evidence,
            "strong_rule": strong_rule, "q_strong_text": q_text,
            "premise": None, "position": position,
            "priority": priority,
        })
    return hints


# ---------------------------------------------------------------- P scan ----
def head_ident(conj):
    conj = conj.strip()
    conj = re.sub(r"^\(+", "", conj)
    conj = re.sub(r"^\\<lambda>\S*\s*\.?\s*", "", conj)
    m = re.match(r"([A-Za-z_][\w']*)", conj)
    return m.group(1) if m else ""


def split_pre(stmt):
    """Extract the precondition between the first ⟨…⟩ and split into top-level
    conjuncts.

    A Hoare precondition is a `pred_conj` chain `c1 and c2 and ... and cn`
    where each `ci` is a predicate (`'s \\<Rightarrow> bool`). The ONLY
    top-level separator is the predicate-level `and`. HOL `\\<and>` is
    `bool \\<Rightarrow> bool \\<Rightarrow> bool` and therefore lives INSIDE a
    conjunct's lambda body (`\\<lambda>s. P s \\<and> Q s`), never at the
    pred_conj top level. Splitting on `\\<and>` shreds a single lambda
    predicate into syntactically-invalid fragments (`\\<lambda>s. P s`, `Q s`)
    — confirmed to produce bogus `unused-premise` hints — so we split on `and`
    only. Dropping half a lambda body is not "dropping a precondition
    conjunct" anyway, so this is the correct granularity, not just the safe
    one."""
    m = re.search(r"\\<lbrace>(.*?)\\<rbrace>", stmt, re.DOTALL)
    if not m:
        return None, []
    pre = m.group(1).strip()
    # split at parenthesis depth 0, on pred_conj `and` only (NOT HOL \<and>).
    # The `(?<!<)` lookbehind is essential: `\band\b` on its own matches the
    # `and` INSIDE `\<and>` (a word boundary sits between `<` and `a`), which
    # would re-introduce the shredding this split is meant to avoid.
    parts, depth, cur = [], 0, []
    tokens = re.split(r"((?<!<)\band\b|\(|\))", pre)
    for t in tokens:
        if t == "(":
            depth += 1
            cur.append(t)
        elif t == ")":
            depth -= 1
            cur.append(t)
        elif t == "and" and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(t)
    if cur:
        parts.append("".join(cur).strip())
    return pre, [p for p in parts if p]


OPAQUE_PROOF_RE = re.compile(
    r"^\s*by\s*\(?\s*(wp|wpsimp|auto|simp|fastforce|clarsimp|force)\b[^\n]*$")


# ---- op read/write classification (P-slot yield signal) --------------------
# Empirical, from every live P case this line has produced:
#   READ / decode op  → droppable premise  (gts_wf' on get_thread_state,
#                       decode_unbind_notification_wf_strong on decode_*) : 2/2 PASS
#   WRITE / modify op → load-bearing premise (setup_reply_master, pinv_tcb on
#                       perform_invocation)                                : 4/4 FAIL
# So the op class is a strong prior on whether dropping a premise can build.
_OP_SKIP = {"do", "doE", "od", "odE", "return", "returnOk", "liftE", "liftM",
            "when", "unless", "whenE", "K", "case", "if", "let", "the"}
_OP_READ = ("get_", "gets_", "read_", "thread_get", "decode_", "lookup_",
            "resolve_", "ensure_", "is_", "const_on_failure", "return")
_OP_WRITE = ("set_", "perform_", "do_", "handle_", "send_", "receive_",
             "cancel_", "delete_", "finalise_", "retype_", "create_",
             "activate_", "deactivate_", "restart_", "suspend_", "resume_",
             "update_", "store_", "write_", "invoke_", "switch_", "schedule_",
             "reschedule_", "bind_", "unbind_", "insert_", "remove_", "empty_",
             "reset_", "init_", "setup_", "copy_", "transfer_", "mask_",
             "preemption", "unmap_", "map_", "arch_")


def extract_op(statement):
    """Head identifier of the program between pre and post: ⟨P⟩ <op> args ⟨Q⟩."""
    m = re.search(r"\\<rbrace>(.*?)\\<lbrace>", statement, re.DOTALL)
    if not m:
        return ""
    for tok in re.findall(r"[A-Za-z_][\w']*", m.group(1)):
        if tok not in _OP_SKIP:
            return tok
    return ""


def classify_op(op):
    """read | write | unknown — a prior on P-slot droppability."""
    if not op:
        return "unknown"
    o = op.lower()
    if any(o.startswith(p) for p in _OP_READ):
        return "read"
    if any(o.startswith(p) for p in _OP_WRITE):
        return "write"
    return "unknown"


def split_assumptions(stmt):
    """Plain implication lemma `\\<lbrakk>A1; A2; ...\\<rbrakk> \\<Longrightarrow> C`
    -> (body, [A1, A2, ...], conclusion). NEW SCOPE (novel-slot discovery):
    the detector otherwise only sees Hoare triples (\\<lbrace>...\\<rbrace>) and was
    blind to this large class of seL4 helper lemmas (Untyped/CSpace have ~50
    each). Assumption-weakening on these is a valid additive strengthening
    (the lemma holds under weaker hypotheses). Assumptions split on top-level
    `;` (paren depth 0)."""
    m = re.search(r"\\<lbrakk>(.*?)\\<rbrakk>\s*\\<Longrightarrow>(.*)",
                  stmt, re.DOTALL)
    if not m:
        return None, [], ""
    body, concl = m.group(1).strip(), m.group(2).strip()
    parts, depth, cur = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1; cur.append(ch)
        elif ch == ")":
            depth -= 1; cur.append(ch)
        elif ch == ";" and depth == 0:
            parts.append("".join(cur).strip()); cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur).strip())
    return body, [p for p in parts if p], concl


# ---- rule-precondition dependency (P-slot load-bearing signal) --------------
# Learned from live success/failure: dropping a premise FAILS when it feeds a
# named rule the proof invokes (ArchAcc: drop equal_kernel_mappings, proof calls
# kernel_mapping_slots_empty_pdeI whose precondition lists it → load-bearing),
# and SUCCEEDS when no invoked rule needs it (pd_at_asid: drop valid_vspace_objs,
# proof uses valid_vs_lookupD/unique_table_refsD/asid_low_high_bits, none need
# it). This upgrades the P signal from "head absent from proof body" to "head
# absent from the dependency closure of the rules the proof actually invokes".

def build_precond_index(thy_paths):
    """name -> set of precondition-conjunct heads, across the given theories.
    Covers Hoare (\\<lbrace>...\\<rbrace>) and implication (\\<lbrakk>...\\<rbrakk>)
    forms. Used to look up what an invoked rule's precondition needs."""
    idx = {}
    for p in thy_paths:
        try:
            text = Path(p).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lm in parse_lemmas(text):
            pre, conjs = split_pre(lm["statement"])
            if pre is None:
                _, conjs, _ = split_assumptions(lm["statement"])
            heads = {head_ident(c) for c in conjs}
            heads.discard("")
            if heads:
                idx[lm["name"]] = heads
    return idx


def invoked_rules(proof, index):
    """Lemma names from the index that appear in this proof body (i.e. rules the
    proof invokes). Over-matching only causes a conservative demote, never a
    false high, so a plain identifier ∩ index is sufficient and cheap."""
    toks = set(re.findall(r"[A-Za-z_][\w']*", proof))
    return toks & index.keys()


# --- proof-strategy signals (mined by spec_signal_miner.py, 2026-06-29) -------
# Both fire on the PROOF STRATEGY, not the premise. Calibrated on 14 wins / 59
# losses: each is regression-free (never fires against its forbidden class).
_AUTOMATION_RE = re.compile(r"\b(blast|fastforce|auto|force|crush)\b")
_COMP_WP_EXCLUDE = ("rule_tac", "strengthen", "hoare_gen_asm", "drule", "frule")


def has_automation(proof):
    """PRECISION signal: a proof discharged by an unconstrained automation
    tactic (blast/fastforce/auto/force/crush) silently consumes the WHOLE
    hypothesis context, so a text-absent premise can still be load-bearing →
    the unused-premise signal is unreliable here. Demote. (Mined: demotes
    15/59 losses, 0 wins.)"""
    return bool(_AUTOMATION_RE.search(proof or ""))


def is_compositional_wp(proof, op, form):
    """RECALL signal: a Hoare proof that unfolds the operation's OWN definition
    (`<op>_def`) + uses `hoare_pre` + a pure wp/wpc/clarsimp chain with NO
    backward reasoning (drule/frule/strengthen/hoare_gen_asm/rule_tac) is
    forward-driven through the op body — each sub-op discharges its own
    preconditions via [wp] lemmas, so a bundled outer premise (invs, …) is
    over-specified → droppable. BOOST, even past the write-op demote (this
    recovers the invoke_cnode write-op wins signal #4 wrongly demoted). (Mined:
    boosts 4/5 under-ranked wins, 0 losses.)"""
    p = proof or ""
    if form != "hoare" or not op or "unknown" in op.lower():
        return False
    if (op + "_def") not in p or "hoare_pre" not in p:
        return False
    return not any(x in p for x in _COMP_WP_EXCLUDE)


def scan_p(lemmas, precond_index=None):
    """P precision rules learned from first live scan:
    - PREFIX match, not word match: `simp: valid_objs_def` consumes
      `valid_objs` — \\bvalid_objs\\b misses it because `_` is a word char.
      So a premise head H is 'consumed' if any identifier with prefix H
      appears in the proof (valid_objs / valid_objs_def / valid_objsE ...).
    - DIFFERENTIAL evidence: a hint is only high-confidence when at least one
      OTHER conjunct IS visibly consumed — that proves the proof shows its
      dependencies, making this premise's absence meaningful (0023 shape).
    - OPAQUE one-liner proofs (`by wpsimp` etc.) consume premises invisibly —
      a static hint there is worthless noise; drop it."""
    hints = []
    for lm in lemmas:
        if lm["has_assumes"]:
            continue
        # Two statement shapes: Hoare triple ⟨P⟩ op ⟨Q⟩, OR (NEW) plain
        # implication ⟦A1; A2; ...⟧ ⟹ C. The unused-premise logic is identical;
        # only the way we extract the conjunct list and the "post" differ.
        pre, conjs = split_pre(lm["statement"])
        form = "hoare"
        if pre is None:
            pre, conjs, concl = split_assumptions(lm["statement"])
            form = "impl"
        if pre is None or len(conjs) < 2:
            continue
        proof = lm["proof"]
        inv_rules = (invoked_rules(proof, precond_index)
                     if precond_index else set())
        tactic_lines = [l for l in lm["proof_lines"] if l.strip()]
        opaque = len(tactic_lines) <= 1 and bool(
            tactic_lines and OPAQUE_PROOF_RE.match(tactic_lines[0]))
        if opaque:
            continue
        # FRAME-PREMISE rule (learned from a live all-[] agent run): a conjunct
        # whose head also appears in the POSTCONDITION / CONCLUSION is the
        # lemma's framed subject (pre P → post P), consumed invisibly and
        # essentially never droppable. Mechanical kill, no semantic judgment.
        if form == "impl":
            post = concl
            op, op_class = "", "unknown"   # no op in a plain implication
        else:
            mposts = re.findall(r"\\<lbrace>(.*?)\\<rbrace>", lm["statement"], re.DOTALL)
            post = mposts[1] if len(mposts) >= 2 else ""
            op = extract_op(lm["statement"])
            op_class = classify_op(op)
        # proof-strategy signals (per-lemma, mined 2026-06-29)
        automation = has_automation(proof)
        comp_wp = is_compositional_wp(proof, op, form)
        # Two DISTINCT notions, previously conflated (caused false `high`):
        #   consumed[c]      — should c be SPARED from flagging? True if c is a
        #                      frame premise (head in post, consumed invisibly
        #                      by the wp chain) OR visibly used in the body.
        #   body_consumed[c] — is c VISIBLY consumed in the proof BODY? This is
        #                      the only valid differential evidence ("the proof
        #                      shows its dependencies"). A frame premise whose
        #                      head appears only in the post is NOT body-visible,
        #                      so it must not inflate the differential count.
        consumed = {}
        body_consumed = {}
        for c in conjs:
            h = head_ident(c)
            if h.lower() in P_SKIP_HEADS or len(h) < 3:
                consumed[c] = None      # not assessable
                body_consumed[c] = False
                continue
            in_body = bool(re.search(rf"\b{re.escape(h)}[\w']*", proof))
            body_consumed[c] = in_body
            if re.search(rf"\b{re.escape(h)}[\w']*", post):
                consumed[c] = True      # frame premise — spare from flagging
                continue
            consumed[c] = in_body
        n_consumed = sum(1 for v in body_consumed.values() if v)
        for c, used in consumed.items():
            if used is not False:
                continue
            h = head_ident(c)
            differential = n_consumed >= 1
            # RULE-PRECONDITION DEPENDENCY: if h feeds the precondition of a
            # named rule the proof INVOKES, it is consumed via that rule even
            # though it never appears in the proof TEXT — almost always
            # load-bearing (ArchAcc equal_kernel_mappings → kernel_mapping_
            # slots_empty_pdeI). Demote and name the culprit rule(s).
            lb_rules = [r for r in inv_rules
                        if h and h in (precond_index or {}).get(r, ())]
            # PRECEDENCE (strongest first):
            #  1. rule-precondition: head feeds an invoked rule's precondition
            #     → low (the drop is unsafe, consumed via that rule).
            #  2. compositional-wp idiom (mined recall) → high. Boost even past
            #     the write-op demote / non-differential — recovers wins those
            #     rules under-rank (e.g. invoke_cnode write-op wins).
            #  3. write-op demote (op-class prior) → low.
            #  4. else: differential decides.
            # NB: the mined `automation` signal is computed + recorded but NOT
            # gated on — it demoted real wins (gts_wf's embedded `blast`, and
            # `apply (fastforce simp:)` wins) that the miner's calibration missed
            # (proof-extraction bug + gts_wf absent from its labeled set). Kept
            # informational pending a cleaner whole-goal-only re-mine.
            if lb_rules:
                priority = "low"
            elif comp_wp:
                priority = "high"
            elif op_class == "write":
                priority = "low"
            else:
                priority = "high" if differential else "low"
            hints.append({
                "slot": "P", "kind": "unused-premise", "lemma": lm["name"],
                "line": lm["line"], "statement": lm["statement"][:300],
                "op": op, "op_class": op_class, "form": form,
                "evidence": ((f"[implication lemma — assumption-weakening] "
                              if form == "impl" else "")
                             + f"conjunct `{c.strip()[:60]}` head `{h}` never "
                             f"appears in the proof body (prefix match incl. "
                             f"_def/_E forms)"
                             + (f"; {n_consumed} other conjunct(s) ARE visibly "
                                f"consumed — differential signal (0023 shape)"
                                if differential else
                                "; NO conjunct visibly consumed — proof likely "
                                "opaque, weak signal")
                             + f"; op `{op}` is {op_class}"
                             + (" (write/modify → premise usually load-bearing,"
                                " demoted)" if op_class == "write" else
                                " (read/decode → premise often droppable)"
                                if op_class == "read" else "")
                             + (f"; BUT head feeds precondition of invoked "
                                f"rule(s) {lb_rules[:3]} → load-bearing, DEMOTED"
                                if lb_rules else "")
                             + ("; note: proof uses automation (blast/auto/"
                                "fastforce) — text-absence less reliable (informational"
                                ", not gated: regressed real wins)" if automation else "")
                             + ("; compositional wp-chain idiom (op_def + "
                                "hoare_pre + pure wp) → over-specified premise, "
                                "BOOSTED" if comp_wp else "")
                             + "; wp-chain implicit use is the trial's job"),
                "strong_rule": None, "q_strong_text": None,
                "premise": c.strip()[:120], "position": None,
                "priority": priority, "lb_rules": lb_rules[:3],
                "automation": automation, "comp_wp": comp_wp,
            })
    return hints


# ------------------------------------------------------- Q exactness scan ---
def scan_q_exactness(lemmas):
    """Design §1.3 Q sub-case: post commits `e1 ≤ e2` where the computation
    may pin an exact value. Only flag CLEAN shapes: the whole post is a single
    `\\<lambda>rv s. rv \\<le> <expr>` comparison on the return value — those
    are the ones where 'what exact values can rv take' is answerable."""
    hints = []
    for lm in lemmas:
        m = re.findall(r"\\<lbrace>(.*?)\\<rbrace>", lm["statement"], re.DOTALL)
        if len(m) < 2:
            continue
        post = m[1].strip()
        # Operator broadened beyond \<le> (defect H): also <, \<ge>, > — any
        # of these bounds rv and may hide an exact value. Bound may now contain
        # Isabelle symbols (\<...>) so notation-bearing bounds like
        # `2 ^ word_bits` or `unat \<dots>` are caught; but reject a bound that
        # crosses a logical connective (that means the post is a CONJUNCTION,
        # not a clean single comparison — `[^\\]` used to block this for free).
        mm = re.match(
            r"^\\<lambda>\s*(\w+)[\w\s]*\.\s*\1\s*"
            r"(\\<le>|\\<ge>|<|>)\s*(.{1,80}?)\s*$", post)
        if not mm:
            continue
        bound = mm.group(3).strip()
        if re.search(r"\\<(and|or|longrightarrow|longleftrightarrow|imp)\b", bound):
            continue
        op_txt = {"\\<le>": "<=", "\\<ge>": ">=", "<": "<", ">": ">"}[mm.group(2)]
        hints.append({
            "slot": "Q", "kind": "exactness", "lemma": lm["name"],
            "line": lm["line"], "statement": lm["statement"][:300],
            "evidence": (f"post commits only `{mm.group(1)} {op_txt} "
                         f"{bound}` — if the computation can "
                         f"only produce specific values, an exact post "
                         f"(equality/disjunction of cases) is strictly "
                         f"stronger and likely provable with the same proof"),
            "strong_rule": None,
            "q_strong_text": None, "premise": None, "position": None,
            "priority": "high",
        })
    return hints


# ------------------------------------------------- P claim verification ----
def check_p_claim(theory_text, proposal):
    """Mechanically derive/verify a P-slot proposal's strengthening claim.

    A P proposal is sound iff its statement = the hinted lemma's statement with
    a STRICT SUBSET of the pre conjuncts and an identical post. When that holds
    the claim direction is fixed: `<old_pre> ==> <new_pre>` (old implies the
    weaker new). The first live P case (gts_wf') had the agent write the arrow
    REVERSED — this check makes the recorded claim authoritative and catches
    proposals that silently changed the post or added conjuncts.

    Returns {"ok": bool, "reason": str, "claim": str|None,
             "dropped_conjuncts": [...]}.
    """
    hint_name = proposal.get("hint_lemma")
    if not hint_name:
        return {"ok": False, "reason": "no hint_lemma to compare against",
                "claim": None, "dropped_conjuncts": []}
    lemmas = parse_lemmas(theory_text)
    old = next((lm for lm in lemmas if lm["name"] == hint_name), None)
    if old is None:
        return {"ok": False, "reason": f"hinted lemma `{hint_name}` not found",
                "claim": None, "dropped_conjuncts": []}
    # pull statement out of the proposal's new_lemma text
    mnew = re.search(r'"(.*)"', proposal.get("new_lemma", ""), re.DOTALL)
    if not mnew:
        return {"ok": False, "reason": "no quoted statement in new_lemma",
                "claim": None, "dropped_conjuncts": []}
    new_stmt = mnew.group(1)
    old_pre, old_conjs = split_pre(old["statement"])
    new_pre, new_conjs = split_pre(new_stmt)
    if old_pre is None or new_pre is None:
        return {"ok": False, "reason": "could not parse a Hoare pre",
                "claim": None, "dropped_conjuncts": []}
    old_posts = re.findall(r"\\<lbrace>(.*?)\\<rbrace>", old["statement"], re.DOTALL)
    new_posts = re.findall(r"\\<lbrace>(.*?)\\<rbrace>", new_stmt, re.DOTALL)
    if len(old_posts) < 2 or len(new_posts) < 2 or \
            _normc(old_posts[1]) != _normc(new_posts[1]):
        return {"ok": False, "reason": "post differs from the hinted lemma — "
                "not a pure premise-weakening", "claim": None,
                "dropped_conjuncts": []}
    oldset = {_normc(c) for c in old_conjs}
    newset = {_normc(c) for c in new_conjs}
    if not newset < oldset:
        return {"ok": False, "reason": "new pre is not a strict subset of the "
                "old pre conjuncts", "claim": None, "dropped_conjuncts": []}
    dropped = [c for c in old_conjs if _normc(c) not in newset]
    claim = (f"({old_pre.strip()}) ==> ({new_pre.strip()}) — the OLD pre "
             f"strictly implies the NEW (weaker) pre; dropped conjunct(s): "
             + "; ".join(f"`{d.strip()}`" for d in dropped)
             + ". Reverse direction fails, making the weakening strict. "
               "[claim mechanically derived by spec_slot_hints --check-p-claim]")
    return {"ok": True, "reason": "pure premise-weakening verified "
            "(strict pre-conjunct subset, identical post)",
            "claim": claim, "dropped_conjuncts": [d.strip() for d in dropped]}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("theory")
    ap.add_argument("--slot", choices=["Q", "P", "all"], default="all")
    ap.add_argument("--out", default=None)
    ap.add_argument("--max", type=int, default=12, help="cap hints per slot")
    ap.add_argument("--check-p-claim", default=None, metavar="PROPOSAL_JSON",
                    help="verify a P proposal against its hinted lemma and "
                         "print the mechanically-derived claim JSON")
    args = ap.parse_args()

    if args.check_p_claim:
        text = Path(args.theory).read_text(encoding="utf-8", errors="replace")
        prop = json.loads(Path(args.check_p_claim).read_text())
        res = check_p_claim(text, prop)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        sys.exit(0 if res["ok"] else 1)

    text = Path(args.theory).read_text(encoding="utf-8", errors="replace")
    lemmas = parse_lemmas(text)
    hints = []
    if args.slot in ("Q", "all"):
        q = scan_q(lemmas) + scan_q_exactness(lemmas)
        # high-priority first, then by line
        q.sort(key=lambda h: (h["priority"] != "high", h["line"]))
        # dedup by lemma (defect I): a lemma can be flagged by BOTH scan_q
        # (redirect) and scan_q_exactness; keep the first after the sort, i.e.
        # the higher-priority / more-specific read, so the agent sees one hint.
        seen, qd = set(), []
        for h in q:
            if h["lemma"] in seen:
                continue
            seen.add(h["lemma"])
            qd.append(h)
        hints += qd[:args.max]
    if args.slot in ("P", "all"):
        # Build the rule-precondition index from the target file's directory +
        # the top-level invariant-abstract dir (covers same-arch + generic
        # rules). One-time ~seconds; lets scan_p demote premises consumed via an
        # invoked rule's precondition (load-bearing despite being absent from
        # the proof text). SPEC_HINTS_NO_RULE_INDEX=1 disables it.
        precond_index = None
        if os.environ.get("SPEC_HINTS_NO_RULE_INDEX") != "1":
            d = Path(args.theory).resolve().parent
            top = d.parent if d.name in (
                "ARM", "RISCV64", "X64", "AARCH64", "ARM_HYP") else d
            paths = set(d.glob("*.thy")) | set(top.glob("*.thy"))
            precond_index = build_precond_index(paths)
        p = scan_p(lemmas, precond_index)
        # Rank READ/decode ops above WRITE ops before the --max cap: a read-op
        # premise is the droppable mine even when NON-differential (the proven
        # decode_unbind_notification_wf_strong win was read + non-differential,
        # so it must outrank load-bearing write-op hints rather than share the
        # 'low' bucket). Within a class: differential (high) first, then line.
        _oc = {"read": 0, "unknown": 1, "write": 2}
        p.sort(key=lambda h: (_oc.get(h.get("op_class", "unknown"), 1),
                              h["priority"] != "high", h["line"]))
        hints += p[:args.max]

    blob = json.dumps(hints, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(blob, encoding="utf-8")
    print(blob)
    # Observability: parse_lemmas only sees column-0 `lemma "..."` forms. Count
    # the fact-declaring headers it could NOT parse so a near-empty hint list on
    # a cartouche/theorem/crunch-heavy file reads as "low coverage", not "clean".
    total_headers = len(re.findall(r"(?m)^(?:lemma|theorem|corollary|schematic_goal)\b", text))
    skipped = max(0, total_headers - len(lemmas))
    print(f"[slot_hints] {len(lemmas)}/{total_headers} fact headers parsed "
          f"({skipped} unparsed: theorem/cartouche/structured/indented), "
          f"{len(hints)} hint(s) (slot={args.slot})", file=sys.stderr)


if __name__ == "__main__":
    main()
