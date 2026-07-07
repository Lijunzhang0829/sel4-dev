#!/usr/bin/env python3
"""spec_agent.py — host-side LLM agent that SCANS a theory for additive
spec-strengthening slots and PROPOSES concrete new lemmas L'.

This is the `execute_additive` analog of lemma-staticize's
`proposer_host.py`: it rides the bundled Claude CLI (Claude Max OAuth on
the host — no API key, no creds in any container) and turns one theory
file into a list of *candidate proposals*. It does NOT verify anything —
`strengthen.sh` runs each proposal through `check-theory.sh --patch`
(trial) + `spec_impact.py` (verdict/gate). The agent proposes; the
trial gate filters. That division of labour is the whole design
(execute-additive-design.md §6.3: detector/agent proposes, trial proves).

The agent works in the execute_additive model (execute-additive-design.md):

  Every spec strengthening is ADDITIVE — add a new lemma L', leave the
  original L untouched. L' lands in exactly ONE of three slots:

    P-slot : weaken a precondition (drop a conjunct L's proof never uses)
    Q-slot : strengthen a postcondition (L under-commits; a stronger,
             provable post exists — often the proof already passes through
             it before weakening via hoare_strengthen_post)
    F-slot : field-frame — op never promised to preserve <field>; add the
             frame lemma {P (field s)} op {_ s. P (field s)}

  Each proposal MUST answer how L' reaches downstream (delivery):
    wp / simp : auto-fires in wp/simp search (F-slot default)
    named     : an explicit consumer does `rule/wp/drule L'`
    block     : L' is a building block for another strengthening lemma

Output: a JSON array of proposals on stdout (and to --out). Each item:

  {
    "slot":              "P" | "Q" | "F",
    "delivery":          "wp" | "simp" | "named" | "block",
    "delivery_substate": "realized" | "planned" | null,   # named/block only
    "delivery_target":   "<consumer name+line | downstream cand keys | null>",
    "lemma_name":        "<new lemma name>",
    "anchor_line":       <int>,        # insert AFTER this line (a by/done line)
    "new_lemma":         "<full Isabelle text of the new lemma incl. proof>",
    "rationale":         "<why this slot is real>",
    "strengthening_claim": "<P=>P' or Q'=>Q or pure-additive frame>"
  }

Usage (normally invoked by strengthen.sh, but standalone-runnable):

  python3 spec_agent.py <theory.thy> [--slot P|Q|F|all] [--n 3]
                        [--model sonnet] [--focus-lemma NAME]
                        [--out proposals.json]
"""
import argparse
import json
import os
import re
import select
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_CLAUDE = (
    "/home/lijun/.vscode-server/extensions/"
    "anthropic.claude-code-2.1.170/resources/native-binary/claude"
)


def find_claude():
    env = os.environ.get("CLAUDE_BIN")
    if env and Path(env).exists():
        return env
    if Path(DEFAULT_CLAUDE).exists():
        return DEFAULT_CLAUDE
    # fall back to PATH, then to a glob over installed extensions
    from shutil import which
    w = which("claude")
    if w:
        return w
    cands = sorted(Path("/home/lijun/.vscode-server/extensions").glob(
        "anthropic.claude-code-*/resources/native-binary/claude"))
    if cands:
        return str(cands[-1])
    return "claude"


SLOT_DOC = {
    "P": (
        "P-slot (weaken a precondition): the proof of an existing lemma L "
        "does NOT consume some conjunct P_c in L's precondition. Propose "
        "L' = L with P_c dropped. new_lemma = copy of L's statement minus "
        "P_c, with L's ORIGINAL proof body verbatim. strengthening_claim = "
        "'(P_a and P_b) <= (P_a and P_b and P_c)'. Only propose if the "
        "proof body shows no explicit use of P_c (no `rule P_c_E`, "
        "`simp add: P_c_def`, `frule P_c_D`, `drule P_c`...). The trial "
        "decides the implicit wp-chain dependency."
    ),
    "Q": (
        "Q-slot (strengthen a postcondition): an existing lemma L commits a "
        "weaker post than is provable. The tell is a 'redirect-shaped' proof "
        "body: `hoare_strengthen_post[E[_R]]` then a stronger rule then a "
        "weakening bridge (e.g. `rule strong_lemma, simp add: bridge`) — L "
        "passes through Q_strong then weakens it to Q_weak. Propose L' with "
        "post = Q_strong and proof `by (rule <strong_lemma>)` (or "
        "`by (rule <strong_lemma>, simp)`). delivery_target MUST name the "
        "strong rule. strengthening_claim = 'Q_strong ==> Q_weak'. A common "
        "sub-case: Q_weak has `f s <= x`, Q_strong has `f s = x`."
    ),
    "F": (
        "F-slot (field frame): an operation `op` never publicly promises to "
        "preserve some kernel state field. Propose the frame lemma "
        "L' = `{\\<lambda>s. P (<field> s)} op <args> {\\<lambda>_ s. P "
        "(<field> s)}` proved `by (wpsimp simp: <op>_def)` (add "
        "`wp: get_object_wp` if op reads an object; append `| clarsimp)+` "
        "for case-splitting bodies). No existing L needed — pure-additive. "
        "delivery defaults to wp. Prefer fields the file already proves for "
        "SIBLING ops via existing `<op2>_<field>[wp]` lemmas (mirror them)."
    ),
}

DELIVERY_DOC = (
    "delivery — how L' reaches downstream (MANDATORY, execute-additive §2):\n"
    "  wp / simp : registers `[wp]`/`[simp]`; auto-fires in tactic search. "
    "This is the DEFAULT and ONLY sanctioned delivery for F-slot.\n"
    "  named     : no attribute; an explicit consumer proof does "
    "`rule/wp/drule L'`. Use for Q-slot/P-slot. Two substates:\n"
    "    'realized' — admitted ONLY if a consumer references L' by name in the "
    "post-patch file. Since L' is brand new, this means you MUST bundle the "
    "consumer edit as `consumer_hunks` (see below) — a same-PATCH consumer. "
    "Without consumer_hunks the gate DEMOTES 'realized' to 'planned'.\n"
    "    'planned'  — you only promise a future consumer (delivery_target = the "
    "prose plan). This is the NORMAL case: add the stronger lemma now, wire "
    "consumers in a follow-up. Prefer this unless you are bundling a consumer.\n"
    "  block     : L' is a helper feeding another strengthening lemma; "
    "delivery_target = the downstream lemma/candidate name(s). A block whose "
    "downstream candidate does not yet exist is REJECTED (no "
    "'future-maybe-useful helper'). To be promoted from provisional to "
    "realized, set `block_dependency_evidence` to a VERIFIABLE object that "
    "actually cites L' — one of: "
    "{\"reference_snippet\": \"wp L'\" (the exact tactic line in the "
    "downstream proof that names L')}, or {\"downstream_patch\": \"<path>\"} "
    "(a patch the gate greps for an L' citation), or {\"ab_trial\": "
    "{\"without_block\": \"fail\", \"with_block\": \"ok\"}}. A snippet/patch "
    "that does not actually cite L' via a tactic is NOT accepted — the block "
    "stays provisional.\n"
    "HARD RULE: do NOT propose delivery='wp' (or 'simp') for a P-slot or "
    "Q-slot lemma — wp on P/Q is reject-by-default (needs a regression "
    "escalation the agent cannot perform). For P/Q always use 'named' or "
    "'block'.\n"
    "GRACE: for any 'planned' substate you MAY set `grace_period_weeks` (int; "
    "default 8) — the window before an unrealized lemma becomes an orphan.\n"
    "EVIDENCE OVER OPTIMISM: do NOT claim substate='realized' unless an "
    "existing lemma in the file already references L' by name (rare for a "
    "brand-new lemma). When unsure use 'planned' — the gate demotes an "
    "unbacked 'realized' to 'planned' anyway, so honesty costs nothing."
)


HINT_GUIDANCE = {
    "exactness": ("the post only bounds the return value (e.g. `rv <= e`). "
                  "Read the operation's definition in the SOURCE: enumerate "
                  "the exact values it can return, and propose L' with the "
                  "exact post (an equality or a disjunction of cases, e.g. "
                  "`rv = 0 \\<or> rv = idx`). Same pre, usually the same "
                  "proof shape closes it."),
    "unused-premise": ("a precondition conjunct never appears in the proof "
                       "text. Check whether a simp/wp rule used by the proof "
                       "needs it as a hypothesis (e.g. `*_st_tcb_at` rules "
                       "often need `sym_refs`) — if you can NAME the "
                       "consuming rule, skip the hint. Otherwise PROPOSE IT: "
                       "for P-slot the trial IS the intended ground truth and "
                       "costs under a minute — a borderline hint is worth a "
                       "trial, returning [] wastes the cheap experiment. "
                       "Draft L' = same statement minus that conjunct, "
                       "ORIGINAL proof body copied verbatim. Also remember "
                       "ops that ASSERT on missing objects (thread_get etc.) "
                       "make triples vacuously true on the failure path — "
                       "`tcb_at t`-style premises are often droppable for "
                       "that reason."),
    "inline-redirect": ("the proof passes through a stronger post then "
                        "weakens it. Verify the stronger post is about THIS "
                        "lemma's op (leading redirect) and is NOT logically "
                        "equivalent to the existing post (beware pure form "
                        "adaptations like `(=) x` vs `\\<lambda>c. c = x`, "
                        "or `\\<exists>ct. ct = e \\<and> P ct` vs `P e` — "
                        "those are NOT strengthenings; skip them)."),
    "named-redirect": ("the strong fact already exists as a named rule — the "
                       "slot is FILLED. Only propose an alias-style L' if "
                       "there is a strong family-naming argument; otherwise "
                       "skip."),
}


def render_hints(hints):
    if not hints:
        return ""
    out = ["\n=== DETECTOR HINTS (mechanical scan — verify each before drafting) ===",
           "A static scanner flagged these locations. They are HYPOTHESES, not",
           "facts: your job is the semantic judgment the scanner cannot do.",
           "Prefer drafting proposals grounded in a hint (set \"hint_lemma\" to",
           "the hinted lemma name). If NO hint survives your scrutiny, return",
           "[] — do NOT pad with off-slot or weak proposals.\n"]
    for i, h in enumerate(hints):
        guide = HINT_GUIDANCE.get(h.get("kind", ""), "")
        out.append(f"[hint {i}] slot={h['slot']} kind={h.get('kind')} "
                   f"priority={h.get('priority')} lemma=`{h['lemma']}` "
                   f"(L{h['line']})")
        if h.get("statement"):
            out.append(f"  statement: {h['statement'][:220]}")
        if h.get("premise"):
            out.append(f"  premise to drop: {h['premise']}")
        if h.get("q_strong_text"):
            out.append(f"  stronger post seen in proof: {h['q_strong_text'][:160]}")
        if h.get("strong_rule"):
            out.append(f"  existing strong rule: {h['strong_rule']}")
        out.append(f"  scanner evidence: {h.get('evidence','')}")
        if guide:
            out.append(f"  how to act: {guide}")
        out.append("")
    out.append("=== END HINTS ===\n")
    return "\n".join(out)


def build_prompt(theory_rel, slot, n, numbered_src, focus_lemma, hints=None):
    slots = ["P", "Q", "F"] if slot == "all" else [slot]
    slot_docs = "\n\n".join(f"- {SLOT_DOC[s]}" for s in slots)
    focus = ""
    if focus_lemma:
        focus = (f"\nFOCUS: concentrate on the lemma `{focus_lemma}` and its "
                 f"immediate neighbourhood.\n")
    if slot != "all":
        focus += (f"\nSLOT DISCIPLINE: this run asks for {slot}-slot ONLY. "
                  f"Proposals with any other slot will be DISCARDED by the "
                  f"driver. If you find no sound {slot}-slot candidate, "
                  f"return [] — an empty answer is a valid, useful result.\n")
    focus += render_hints(hints or [])
    return f"""You are a seL4 Isabelle/HOL spec-strengthening agent. You read ONE \
theory file and propose ADDITIVE strengthenings: NEW lemmas that are strictly \
stronger statements about EXISTING definitions. You never modify or delete an \
existing lemma — every proposal ADDS a new lemma L' and leaves the original L \
untouched (this is what makes the change cascade-free).

A Hoare triple lemma is strictly strengthened when the new pre is weaker \
(P ==> P') and/or the new post is stronger (Q' ==> Q). You must be able to \
state that entailment. Three slots exhaust the additive design:

{slot_docs}

{DELIVERY_DOC}

ANCHOR: for each proposal give `anchor_line` = the line number of an existing \
proof-terminator (`by ...` or `done`) AFTER which the new lemma should be \
inserted. Pick the end of the most closely related existing lemma so the new \
lemma sits in its natural neighbourhood. The new lemma will be inserted on the \
line after `anchor_line`.

PROOF: `new_lemma` must contain the COMPLETE Isabelle text of the new lemma — \
its `lemma <name>[...]:` header, the `"\\<lbrace>...\\<rbrace> ... \
\\<lbrace>...\\<rbrace>"` statement, AND a proof (`by (...)` or `apply (...)` \
... `done`). Keep proofs short and deterministic; the trial build will reject \
anything that does not verify, so prefer the canonical tactic for the slot \
over creative ones. Use the SAME Isabelle source conventions (escapes like \
`\\<lbrace>`, `\\<lambda>`, `\\<and>`) you see in the source below.

Theory file: {theory_rel}{focus}

THE TRIAL BUILD IS THE JUDGE — NOT YOU. Do NOT try to PROVE in your head \
whether a premise is droppable or a postcondition holds; you cannot, and \
attempting it makes you loop. Instead: read the hints, pick your best {n} \
candidates by quick pattern-match, write each in one pass, and STOP. A wrong \
guess costs exactly one trial build — that is acceptable and expected. Do not \
deliberate exhaustively, do not re-rank, do not revisit a candidate you have \
written. For P-slot specifically: a premise on a READ/decode op (get_*, \
decode_*, lookup_*) is usually droppable; a premise on a WRITE/modify op \
(set_*, perform_*, handle_*, do_*) is usually load-bearing — trust the hint's \
`op_class` and prefer read-op candidates. Return [] only if NO hint looks even \
plausible; a plausible guess beats an empty answer.

Propose the {n} BEST candidates, best first. Output ONLY a JSON \
array (no prose, no markdown fence) of objects with these keys: \
slot, delivery, delivery_substate, delivery_target, lemma_name, anchor_line, \
new_lemma, rationale, strengthening_claim, and OPTIONALLY: grace_period_weeks \
(int, for 'planned'); block_dependency_evidence (str, for 'block'); and \
consumer_hunks (for named-'realized' only) — a list of \
{{"start_line": int, "end_line": int, "replacement": "<verbatim new text for \
those lines, a consumer proof that calls L'>"}} objects applied as extra patch \
hunks alongside the new lemma. Use null/omit where a key does not apply.

=== SOURCE (line-numbered) ===
{numbered_src}
=== END SOURCE ==="""


def number_source(text, max_lines):
    lines = text.splitlines()
    if max_lines and len(lines) > max_lines:
        # keep the head (imports + early lemmas) — most anchors live there;
        # strengthen.sh can re-run with --focus-lemma to reach deeper.
        lines = lines[:max_lines]
        truncated = True
    else:
        truncated = False
    width = len(str(len(lines)))
    body = "\n".join(f"{i+1:>{width}}  {ln}" for i, ln in enumerate(lines))
    return body, truncated


def _repair_json_escapes(s):
    """Double any lone backslash that is not a valid JSON escape lead.

    Isabelle source like `\\<lbrace>` requires `\\\\<lbrace>` in a JSON string,
    but the model NON-DETERMINISTICALLY emits a single backslash (`\\<lbrace>`),
    which `json.loads` rejects as an illegal escape. This rewrites only the
    illegal lone backslashes — valid escapes (`\\"`, `\\n`, `\\\\`, `\\uXXXX`)
    are left intact, so a reply that was already well-formed is unchanged.
    """
    out = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            nxt = s[i + 1] if i + 1 < n else ""
            if nxt in '"\\/bfnrtu':       # valid JSON escape — keep the pair
                out.append(c)
                out.append(nxt)
                i += 2
                continue
            out.append("\\\\")            # illegal lone backslash — double it
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _balanced_blob(s, start):
    """Return the substring from `s[start]=='['` to its matching `]`, honoring
    JSON string quoting/escapes. None if unbalanced."""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None


def extract_json_array(out):
    """Pull the proposals array out of the model text.

    Robust to two things the model does NON-DETERMINISTICALLY:
    - prose before the JSON that itself contains `[` — notably Isabelle
      attribute syntax like `set_mrs_typ_at[wp]`, whose `[` must NOT be
      mistaken for the array start. So we try EVERY `[` as a candidate start
      and return the first that parses to a list (`[wp]` fails and is skipped).
    - lone backslashes in Isabelle source (`\\<lbrace>`) that are illegal JSON
      escapes — repaired via _repair_json_escapes before a second parse attempt.
    """
    idx = out.find("[")
    while idx != -1:
        blob = _balanced_blob(out, idx)
        if blob:
            for candidate in (blob, _repair_json_escapes(blob)):
                try:
                    v = json.loads(candidate)
                    if isinstance(v, list):
                        return v
                except json.JSONDecodeError:
                    continue
        idx = out.find("[", idx + 1)
    return None


REQUIRED_KEYS = ("slot", "delivery", "lemma_name", "anchor_line", "new_lemma")


def normalize(c):
    """Fill defaults + drop malformed proposals. Returns None to drop."""
    if not isinstance(c, dict):
        return None
    for k in REQUIRED_KEYS:
        if not c.get(k) and c.get(k) != 0:
            return None
    c.setdefault("delivery_substate", None)
    c.setdefault("delivery_target", None)
    c.setdefault("rationale", "")
    c.setdefault("strengthening_claim", "")
    # coerce common slot spellings: "F-slot"/"f"/"Frame" -> "F"
    slot = str(c["slot"]).strip().upper()
    slot = slot.split("-")[0].split("_")[0][:1] if slot else ""
    if slot not in ("P", "Q", "F"):
        return None
    c["slot"] = slot
    # coerce delivery: lower, strip "[wp]" etc.
    deliv = str(c["delivery"]).strip().lower()
    if deliv in ("wp", "[wp]", "wp/simp"):
        deliv = "wp"
    elif deliv in ("simp", "[simp]"):
        deliv = "simp"
    if deliv not in ("wp", "simp", "named", "block"):
        return None
    c["delivery"] = deliv
    try:
        c["anchor_line"] = int(c["anchor_line"])
    except (TypeError, ValueError):
        return None
    return c


def build_repair_prompt(theory_rel, text, proposal, error_text):
    """Closed-loop repair: the trial REJECTED this proposal; give the model the
    failing proposal + the prover's error + a source window around the anchor,
    ask for ONE corrected proposal. This is the feedback loop the F-pipeline
    never had (a failed trial used to be a dead end)."""
    lines = text.splitlines()
    anchor = int(proposal.get("anchor_line") or 1)
    lo = max(0, anchor - 40)
    hi = min(len(lines), anchor + 40)
    width = len(str(hi))
    window = "\n".join(f"{i+1:>{width}}  {lines[i]}" for i in range(lo, hi))
    return f"""You are repairing ONE rejected seL4 spec-strengthening proposal. The \
proposal below was turned into a patch (the new lemma inserted after line \
{anchor} of `{theory_rel}`) and the Isabelle trial build FAILED. Diagnose from \
the error and emit a corrected proposal.

Common failure causes, in order of likelihood:
- WRONG ANCHOR: the lemma landed outside a proof block or inside another \
lemma ("Bad context for command \\"lemma\\""). Pick a correct anchor_line — it \
must be the FINAL line of a complete lemma (its `by ...` / `done`), at the \
theory's top level (not inside `context`...`end` unless intended).
- PROOF DOESN'T CLOSE: residual subgoals. Adjust the tactic (e.g. add the \
needed simp rules you can see used by neighbouring lemmas) or weaken the \
statement to what IS provable while still being a strict strengthening.
- SYNTAX: missing escapes, unbalanced quotes, wrong lemma-name clash.

If the failure shows the strengthening is SEMANTICALLY FALSE (e.g. a dropped \
premise is genuinely load-bearing — the residual subgoal needs it), return [] \
instead of forcing it.

=== REJECTED PROPOSAL ===
{json.dumps(proposal, ensure_ascii=False, indent=1)}

=== TRIAL ERROR ===
{error_text.strip()[:2000]}

=== SOURCE WINDOW (lines {lo+1}-{hi} of {theory_rel}) ===
{window}
=== END ===

Output ONLY a JSON array containing exactly ONE corrected proposal object \
(same keys as the original; keep slot and delivery unchanged; you may change \
lemma_name, anchor_line, new_lemma, rationale, strengthening_claim), or [] if \
unrepairable."""


# --------------------------------------------------------------------------
# Live `claude -p` tracing. Default mode streams the model's FULL process
# (stream-json events: session init, thinking, text, tool calls, final result)
# to stderr as it happens, instead of buffering everything behind
# capture_output and only keeping the final text. Set SPEC_AGENT_STREAM=0 to
# fall back to the old quiet single-shot capture.
# --------------------------------------------------------------------------
def _safe_json(line):
    line = line.strip()
    if not line or line[0] not in "{[":
        return None
    try:
        return json.loads(line)
    except (ValueError, TypeError):
        return None


def _emit_event(line):
    """Render one stream-json event (or raw log line) as a readable trace line
    on stderr so the operator sees the full claude -p process live."""
    ev = _safe_json(line)
    if ev is None:
        s = line.rstrip()
        if s:
            print(f"    │ {s}", file=sys.stderr, flush=True)
        return
    t = ev.get("type")
    if t == "system":
        sub = ev.get("subtype", "init")
        # `thinking_tokens` is a per-token streaming counter — it fires
        # hundreds of times during extended thinking and renders as identical
        # noise lines. Drop it; the ◇ thinking block already shows the content.
        if sub == "thinking_tokens":
            return
        print(f"  ● session {sub}: model={ev.get('model', '?')} "
              f"cwd={ev.get('cwd', '?')} tools={len(ev.get('tools', []))}",
              file=sys.stderr, flush=True)
    elif t == "assistant":
        for blk in ev.get("message", {}).get("content", []):
            bt = blk.get("type")
            if bt == "text" and blk.get("text", "").strip():
                print(f"  ▸ {blk['text'].strip()}", file=sys.stderr, flush=True)
            elif bt == "thinking" and blk.get("thinking", "").strip():
                print(f"  ◇ (thinking) {blk['thinking'].strip()[:300]}",
                      file=sys.stderr, flush=True)
            elif bt == "tool_use":
                print(f"  → tool_use {blk.get('name', '?')}", file=sys.stderr, flush=True)
    elif t == "user":
        print("  ← tool_result", file=sys.stderr, flush=True)
    elif t == "result":
        cost = ev.get("total_cost_usd")
        cost_s = f" ${cost:.4f}" if isinstance(cost, (int, float)) else ""
        print(f"  ✓ result: {ev.get('num_turns', '?')} turn(s), "
              f"{ev.get('duration_ms', '?')}ms{cost_s}"
              + (" [ERROR]" if ev.get("is_error") else ""),
              file=sys.stderr, flush=True)


def run_claude_streaming(argv, timeout, raw_path=None):
    """Run `claude -p` in stream-json mode, echoing a live trace to stderr and
    returning the final assistant text. Raw NDJSON is also saved to raw_path
    for audit. Raises subprocess.TimeoutExpired on deadline."""
    print(f"  $ {argv[0]} -p <prompt:{len(argv[2])} chars> "
          f"{' '.join(argv[3:])}", file=sys.stderr, flush=True)
    proc = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, cwd="/tmp")
    deadline = time.time() + timeout
    raw_fh = open(raw_path, "w", encoding="utf-8") if raw_path else None
    final_text, assistant_chunks = "", []
    try:
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                proc.kill()
                raise subprocess.TimeoutExpired(argv, timeout)
            rlist, _, _ = select.select([proc.stdout], [], [], min(remaining, 1.0))
            if not rlist:
                if proc.poll() is not None:
                    break
                continue
            line = proc.stdout.readline()
            if not line:
                break  # EOF
            if raw_fh:
                raw_fh.write(line)
                raw_fh.flush()
            _emit_event(line)
            ev = _safe_json(line)
            if not ev:
                continue
            if ev.get("type") == "result":
                final_text = ev.get("result", "") or final_text
            elif ev.get("type") == "assistant":
                for blk in ev.get("message", {}).get("content", []):
                    if blk.get("type") == "text":
                        assistant_chunks.append(blk["text"])
        proc.wait(timeout=max(0.0, deadline - time.time()))
    finally:
        if raw_fh:
            raw_fh.close()
    return final_text or "\n".join(assistant_chunks)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("theory", help="Path to .thy file to scan")
    ap.add_argument("--slot", choices=["P", "Q", "F", "all"], default="all")
    ap.add_argument("--n", type=int, default=3, help="max candidates to propose")
    ap.add_argument("--model", default=os.environ.get("SPEC_AGENT_MODEL", "sonnet"))
    ap.add_argument("--focus-lemma", default=None)
    ap.add_argument("--hints", default=None,
                    help="JSON hints file from spec_slot_hints.py — grounds "
                         "the scan in mechanical detector signals")
    ap.add_argument("--repair-proposal", default=None,
                    help="repair mode: the rejected proposal JSON file")
    ap.add_argument("--repair-error", default=None,
                    help="repair mode: file with the trial error text")
    ap.add_argument("--max-lines", type=int,
                    default=int(os.environ.get("SPEC_AGENT_MAX_LINES", "1600")),
                    help="cap source lines embedded in the prompt (0 = whole file)")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--out", default=None, help="also write the JSON array here")
    ap.add_argument("--raw-out", default=None, help="dump the raw model reply here")
    args = ap.parse_args()

    theory_path = Path(args.theory)
    if not theory_path.exists():
        print(f"[spec_agent] theory not found: {args.theory}", file=sys.stderr)
        sys.exit(2)
    text = theory_path.read_text(encoding="utf-8", errors="replace")

    repair_mode = bool(args.repair_proposal)
    if repair_mode:
        proposal = json.loads(Path(args.repair_proposal).read_text())
        error_text = Path(args.repair_error).read_text() if args.repair_error else "(no error captured)"
        prompt = build_repair_prompt(args.theory, text, proposal, error_text)
        print(f"[spec_agent] REPAIR mode for `{proposal.get('lemma_name')}`",
              file=sys.stderr)
    else:
        numbered, truncated = number_source(text, args.max_lines)
        if truncated:
            print(f"[spec_agent] note: source truncated to {args.max_lines} lines "
                  f"for the prompt (use --focus-lemma / --max-lines 0 for more)",
                  file=sys.stderr)
        hints = []
        if args.hints and Path(args.hints).exists():
            try:
                hints = json.loads(Path(args.hints).read_text())
            except json.JSONDecodeError:
                hints = []
            print(f"[spec_agent] {len(hints)} detector hint(s) injected",
                  file=sys.stderr)
        prompt = build_prompt(args.theory, args.slot, args.n, numbered,
                              args.focus_lemma, hints=hints)
    claude = find_claude()
    print(f"[spec_agent] model={args.model} slot={args.slot} n={args.n} "
          f"claude={claude}", file=sys.stderr)

    try:
        # neutral cwd + MCP disabled: loading the project's CLAUDE.md / skills
        # / MCP servers makes a headless -p call ~25x slower (proposer_host.py
        # learned this the hard way). Unlike proposer_host's per-subgoal tactic
        # (1 turn), scan-and-propose is a larger task: the model sometimes
        # consumes a turn before emitting the final JSON, tripping
        # "Reached max turns (1)". A few turns give headroom and cost nothing
        # if it finishes in one. Override with SPEC_AGENT_MAX_TURNS.
        max_turns = os.environ.get("SPEC_AGENT_MAX_TURNS", "6")
        # Disable all tools: the full theory source is already inline in the
        # prompt, so the model needs none — but the bundled CLI ships ~25 tools
        # (Read/Bash/...) and the model will try Read anyway. In headless -p
        # those calls are permission-DENIED, burning a turn + latency + cost
        # each (a live run wasted 2 turns / ~tens of seconds on denied Reads).
        # An empty allow-list forces a pure single-shot reasoning task.
        # --effort low: this is a propose-from-hints task, not deep reasoning.
        # Default session effort let the model spiral into 700+ thinking-token
        # loops ("going in circles") on ambiguous premise-droppability, blowing
        # time/cost and sometimes truncating to []. Low effort + the "trial is
        # the judge" prompt keep the scan fast and decisive. Override with
        # SPEC_AGENT_EFFORT.
        effort = os.environ.get("SPEC_AGENT_EFFORT", "low")
        base_argv = [claude, "-p", prompt, "--model", args.model,
                     "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                     "--tools", "", "--effort", effort, "--max-turns", max_turns]
        if os.environ.get("SPEC_AGENT_STREAM", "1") != "0":
            # visible mode: stream the full claude -p process to stderr
            print("[spec_agent] streaming claude -p (full process trace below; "
                  "SPEC_AGENT_STREAM=0 to silence) ...", file=sys.stderr)
            out = run_claude_streaming(
                base_argv + ["--output-format", "stream-json", "--verbose"],
                timeout=args.timeout, raw_path=args.raw_out)
        else:
            proc = subprocess.run(base_argv, capture_output=True, text=True,
                                  timeout=args.timeout, cwd="/tmp")
            out = proc.stdout
            if args.raw_out:
                Path(args.raw_out).write_text(out, encoding="utf-8")
    except subprocess.TimeoutExpired:
        print("[spec_agent] claude timed out", file=sys.stderr)
        sys.exit(3)
    except Exception as e:  # noqa: BLE001
        print(f"[spec_agent] claude error: {e}", file=sys.stderr)
        sys.exit(3)

    arr = extract_json_array(out)
    if arr is None:
        print("[spec_agent] could not parse a JSON array from the model reply.",
              file=sys.stderr)
        print(out.strip()[:600], file=sys.stderr)
        # emit an empty array so the driver records a clean no-candidate run
        arr = []

    clean = [c for c in (normalize(dict(x)) for x in arr) if c]
    dropped = len(arr) - len(clean)
    if dropped:
        print(f"[spec_agent] dropped {dropped} malformed proposal(s)",
              file=sys.stderr)
    # SLOT HARD-ENFORCEMENT: a Q run must produce Q (or nothing). The first
    # live runs showed the agent silently substituting F proposals when asked
    # for Q — that made `--slot Q` results unreadable as evidence about Q.
    if args.slot != "all":
        off = [c for c in clean if c["slot"] != args.slot]
        if off:
            print(f"[spec_agent] DISCARDED {len(off)} off-slot proposal(s) "
                  f"(asked {args.slot}, got: "
                  f"{', '.join(c['slot']+':'+c['lemma_name'] for c in off)})",
                  file=sys.stderr)
        clean = [c for c in clean if c["slot"] == args.slot]

    blob = json.dumps(clean, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(blob, encoding="utf-8")
    print(blob)
    print(f"[spec_agent] {len(clean)} proposal(s) emitted", file=sys.stderr)


if __name__ == "__main__":
    main()
