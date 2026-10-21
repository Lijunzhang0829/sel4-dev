#!/usr/bin/env python3
"""spec_delivery_gate.py — enforce the execute_additive delivery contract,
*with evidence* (not just "a field was filled in").

execute-additive-design.md §2.5: "merely provable is not enough." Every
candidate L' must declare HOW it reaches downstream AND that declaration must
SURVIVE a check before any trial build is spent. This is the cheap static
admission gate between the agent proposal and the trial.

Inputs:
  candidate JSON      stdin, or --in FILE
  --theory FILE       L's source theory — used to VERIFY named-realized
                      (a real consumer must reference L' by name)
  --ledger FILE       the candidate ledger — used to VERIFY block condition-1
                      (the downstream candidate must already exist)
  --escalation FILE   a completed §2.5.1 P/Q+wp regression record — the ONLY
                      way a P/Q+wp candidate is admitted (default reject)
  --lemma-name NAME   override L's name (else taken from candidate.lemma_name)

Output (one JSON object) — exit 0 iff accept:
  {
    "accept": bool,
    "reason": "...",
    "escalation_required": bool,
    "resolved_substate":      "realized" | "planned" | null,
    "resolved_delivery_state":"realized" | "pending"  | null,
    "grace_period_weeks":     int | null,
    "escalation_record":      "<path>" | null,
    "downgraded":             bool      # realized claim demoted to planned
  }

Admission policy (faithful to design §2.3 / §2.4 / §2.5.1):

  F + wp|simp                         -> accept, state=realized (frame default)

  named-realized                      -> accept iff --theory shows a consumer
                                         that references L' by name; otherwise
                                         DEMOTED to planned (not faked realized)
  named-planned                       -> accept, provisional, grace period

  block, downstream NOT in ledger     -> REJECT (design §2.4 forbidden state:
                                         "future-maybe-useful helper")
  block, downstream in ledger,
        + dependency evidence         -> accept, realized
  block, downstream in ledger,
        no dependency evidence        -> accept, provisional (planned), grace

  P|Q + wp|simp, no escalation        -> REJECT, escalation_required=true
  P|Q + wp|simp, valid escalation     -> accept, state=realized, record cited

  delivery missing/empty/unknown      -> REJECT
"""
import argparse
import json
import re
import sys
from pathlib import Path

VALID_DELIVERY = {"wp", "simp", "named", "block"}
DEFAULT_GRACE_WEEKS = 8
# §2.5.1 numeric gates
ESC_WALL_RATIO_MAX = 1.05
# Reversibility is ONE-SIDED: the risk is that registering [wp] leaves a
# persistent cost, i.e. baseline2 is materially SLOWER than baseline1. A
# baseline2 that is faster (or equal within noise) is fine. The tolerance must
# also exceed real single-measurement wall noise — a first live escalation run
# on small invariant-abstract files showed ~6% run-to-run noise, so a 3%
# two-sided band false-FAILs on noise alone. 5% one-sided covers it.
ESC_REVERSIBLE_TOL = 0.05     # (baseline2 - baseline1) / baseline1, signed
ESC_MIN_FILES = 3             # L's file + in-file consumer file + >=1 cross-file


# ----------------------------------------------------------------------------
def _targets_as_list(target):
    if target is None:
        return []
    if isinstance(target, list):
        return [str(t).strip() for t in target if str(t).strip()]
    # string: split on comma / whitespace
    return [t for t in re.split(r"[,\s]+", str(target).strip()) if t]


def consumer_references(theory_text, lemma_name):
    """True iff `lemma_name` is used as a fact somewhere OTHER than its own
    declaration — i.e. a real consumer exists in the source."""
    if not theory_text or not lemma_name:
        return False
    name = re.escape(lemma_name)
    # strip the lemma's own declaration line(s) so we don't count the def
    decl = re.compile(rf"^\s*lemmas?\s+{name}\b", re.MULTILINE)
    text = decl.sub("", theory_text)
    # a use: `rule L`, `wp L`, `drule/erule/frule L`, `intro:/dest:/elim: L`,
    # `simp add: L`, or `L[` (attribute/instantiation) — i.e. the bareword
    # appearing anywhere that isn't its own declaration.
    return re.search(rf"\b{name}\b", text) is not None


# a fact citation: a tactic combinator naming a lemma
TACTIC_CITE = re.compile(
    r"\b(rule|erule|drule|frule|intro|elim|dest|wp|wpsimp|unfold|subst|fold|"
    r"cut_tac|rule_tac|OF|THEN)\b|simp\s+(add|only|del)\s*:")


def cites_lemma(text, lemma):
    """True iff `text` references `lemma` as a FACT (via a tactic combinator,
    a `simp add:`/`only:` list, or an attribute `lemma[...]`), not just any
    bare mention of the string."""
    if not text or not lemma:
        return False
    name = re.escape(lemma)
    if not re.search(rf"\b{name}\b", text):
        return False
    # attribute form `lemma[...]` or `lemma[THEN ...]`
    if re.search(rf"\b{name}\s*\[", text):
        return True
    # appears alongside a tactic-citation keyword somewhere in the text
    return TACTIC_CITE.search(text) is not None


def _open_relpath(p):
    """Read a patch/text artifact named by an absolute, repo-relative, or
    l4v-relative path. Returns text or None."""
    repo = Path(__file__).resolve().parents[2]
    for cand in (Path(p), repo / p, repo / "verification/l4v" / p):
        try:
            return cand.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return None


def verify_block_evidence(evidence, lemma_name):
    """Verify §2.4 condition-2: the downstream candidate really depends on this
    block. Returns (confirmed, detail). Accepts three structured shapes plus a
    legacy string; in every case the evidence must actually CITE `lemma_name`
    (or attest an A/B trial), not merely be non-empty.

      {"reference_snippet": "<tactic line citing L_new>"}
      {"downstream_patch":  "<path to the downstream range-patch>"}  # grepped
      {"ab_trial": {"without_block": "fail", "with_block": "ok"}}    # A/B result
      "<string>"   # legacy — must itself cite L_new via a tactic
    """
    if not evidence:
        return False, "no dependency evidence"

    if isinstance(evidence, dict):
        if "ab_trial" in evidence:
            ab = evidence["ab_trial"] or {}
            wo = str(ab.get("without_block", "")).lower()
            wi = str(ab.get("with_block", "")).lower()
            ok = wo in ("fail", "failed", "no", "false") and \
                wi in ("ok", "pass", "passed", "yes", "true")
            return ok, (f"A/B trial: without_block={wo!r} with_block={wi!r}"
                        + ("" if ok else " — not a fail→ok pair"))
        if evidence.get("downstream_patch"):
            text = _open_relpath(evidence["downstream_patch"])
            if text is None:
                return False, f"downstream_patch unreadable: {evidence['downstream_patch']}"
            ok = cites_lemma(text, lemma_name)
            return ok, (f"downstream_patch {'cites' if ok else 'does NOT cite'} "
                        f"`{lemma_name}`")
        snippet = evidence.get("reference_snippet")
        if snippet:
            ok = cites_lemma(snippet, lemma_name)
            return ok, (f"reference_snippet {'cites' if ok else 'does NOT cite'} "
                        f"`{lemma_name}`")
        return False, "evidence object has no recognized field " \
                      "(reference_snippet / downstream_patch / ab_trial)"

    # legacy string evidence — must itself cite the block lemma
    ok = cites_lemma(str(evidence), lemma_name)
    return ok, (f"string evidence {'cites' if ok else 'does NOT cite'} "
                f"`{lemma_name}` via a tactic")


def ledger_keys(ledger_path):
    keys = set()
    try:
        for line in Path(ledger_path).read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                keys.add(json.loads(line)["key"])
            except (json.JSONDecodeError, KeyError):
                continue
    except FileNotFoundError:
        pass
    return keys


def validate_escalation(path, slot):
    """Validate a §2.5.1 regression record. Returns (ok, reason)."""
    try:
        rec = json.loads(Path(path).read_text())
    except Exception as e:  # noqa: BLE001
        return False, f"escalation record unreadable: {e}"
    files = rec.get("files") or []
    if len(files) < ESC_MIN_FILES:
        return False, (f"escalation covers {len(files)} file(s); need "
                       f">= {ESC_MIN_FILES} (L's file + in-file consumer + "
                       f">=1 cross-file)")
    rounds = rec.get("rounds") or {}
    b1, t, b2 = (rounds.get("baseline_ms"), rounds.get("trial_ms"),
                 rounds.get("baseline2_ms"))
    if not all(isinstance(x, (int, float)) and x > 0 for x in (b1, t, b2)):
        return False, "escalation rounds missing baseline_ms/trial_ms/baseline2_ms"
    ratio = t / b1
    if ratio > ESC_WALL_RATIO_MAX:
        return False, (f"trial wall ratio {ratio:.3f} > {ESC_WALL_RATIO_MAX} "
                       f"(P/Q+wp must not regress wall)")
    rev = (b2 - b1) / b1                      # signed; >0 means slower on re-measure
    if rev > ESC_REVERSIBLE_TOL:
        return False, (f"not reversible: baseline2 is {rev*100:.1f}% SLOWER than "
                       f"baseline1 (> {ESC_REVERSIBLE_TOL*100:.0f}%) — [wp] may "
                       f"carry a persistent cost")
    if rec.get("passed") is False:
        return False, "escalation record marked passed=false"
    return True, (f"escalation OK: {len(files)} files, wall ratio {ratio:.3f}, "
                  f"reversible within {rev*100:.1f}%")


# ----------------------------------------------------------------------------
def evaluate(c, theory_text=None, lkeys=None, escalation_path=None,
             lemma_name=None):
    slot = c.get("slot")
    delivery = c.get("delivery")
    target = c.get("delivery_target")
    substate = c.get("delivery_substate")
    grace = c.get("grace_period_weeks") or DEFAULT_GRACE_WEEKS
    lemma_name = lemma_name or c.get("lemma_name")

    def res(accept, reason, *, esc=False, sub=None, state=None,
            grace_w=None, rec=None, down=False):
        return {
            "accept": accept, "reason": reason, "escalation_required": esc,
            "resolved_substate": sub, "resolved_delivery_state": state,
            "grace_period_weeks": grace_w, "escalation_record": rec,
            "downgraded": down,
        }

    if delivery not in VALID_DELIVERY:
        return res(False, f"delivery '{delivery}' not in {sorted(VALID_DELIVERY)}")
    if slot not in ("P", "Q", "F"):
        return res(False, f"slot '{slot}' is not P/Q/F")

    # --- P/Q + wp escalation gate (§2.5.1) ---------------------------------
    if delivery in ("wp", "simp") and slot in ("P", "Q"):
        if not escalation_path:
            return res(False,
                       f"{slot}+{delivery} is reject-by-default; admit only via "
                       f"a completed §2.5.1 wp-regression escalation "
                       f"(pass --escalation <record>). Else re-propose as "
                       f"named/block.",
                       esc=True)
        ok, why = validate_escalation(escalation_path, slot)
        if not ok:
            return res(False, f"escalation supplied but invalid: {why}", esc=True)
        return res(True, f"{slot}+{delivery} ADMITTED via escalation — {why}",
                   sub="realized", state="realized", rec=str(escalation_path))

    # --- F + wp/simp : the sanctioned frame default ------------------------
    if slot == "F" and delivery in ("wp", "simp"):
        return res(True, f"F+{delivery} accepted (frame-lemma default)",
                   sub="realized", state="realized")
    if slot == "F" and delivery == "named":
        # rare but legal; treat like any named below
        pass
    elif delivery in ("wp", "simp"):
        return res(False, f"unhandled {slot}+{delivery}")

    # --- named -------------------------------------------------------------
    if delivery == "named":
        tgt = _targets_as_list(target)
        if not tgt:
            return res(False, "delivery=named requires a non-empty "
                              "delivery_target (consumer name+line, or plan)")
        if substate not in ("realized", "planned"):
            return res(False, "delivery=named requires delivery_substate "
                              "'realized' or 'planned'")
        if substate == "realized":
            if theory_text is None:
                # cannot verify -> be honest: provisional, not faked realized
                return res(True, "named-realized claim could not be verified "
                                 "(no --theory); admitted as PLANNED",
                           sub="planned", state="pending", grace_w=grace, down=True)
            if consumer_references(theory_text, lemma_name):
                return res(True, f"named-realized verified: a consumer "
                                 f"references `{lemma_name}` in source",
                           sub="realized", state="realized")
            return res(True, f"named-realized claimed but NO consumer "
                             f"references `{lemma_name}` in source — admitted "
                             f"as PLANNED (provisional)",
                       sub="planned", state="pending", grace_w=grace, down=True)
        # planned
        return res(True, f"named-planned accepted (provisional, "
                         f"{grace}-week grace)",
                   sub="planned", state="pending", grace_w=grace)

    # --- block (§2.4) ------------------------------------------------------
    if delivery == "block":
        tgt = _targets_as_list(target)
        if not tgt:
            return res(False, "delivery=block requires delivery_target = "
                              "downstream candidate key(s)/lemma name(s)")
        # condition 1: downstream candidate must already exist in the ledger
        if lkeys is not None:
            present = [k for k in tgt if k in lkeys]
            if not present:
                return res(False, "delivery=block but NO downstream candidate "
                                  "in the ledger (design §2.4 forbidden state: "
                                  "'future-maybe-useful helper'). Land the "
                                  "downstream candidate first.")
        else:
            present = tgt  # ledger not supplied — advisory only
        # condition 2: dependency evidence must actually CITE this block (or
        # attest an A/B trial) -> realized; otherwise provisional planned.
        confirmed, detail = verify_block_evidence(
            c.get("block_dependency_evidence"), lemma_name)
        if confirmed:
            return res(True, f"block realized: downstream {present} present + "
                             f"verified dependency ({detail})",
                       sub="realized", state="realized")
        note = "" if lkeys is not None else " (ledger not checked)"
        return res(True, f"block provisional: downstream {present} present but "
                         f"dependency NOT verified ({detail}) — admitted as "
                         f"PLANNED, {grace}-week grace{note}",
                   sub="planned", state="pending", grace_w=grace, down=True)

    return res(False, f"unhandled (slot={slot}, delivery={delivery})")


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="infile", default=None)
    ap.add_argument("--theory", default=None,
                    help="L's source theory (verifies named-realized)")
    ap.add_argument("--ledger", default=None,
                    help="candidate ledger (verifies block condition-1)")
    ap.add_argument("--escalation", default=None,
                    help="completed §2.5.1 escalation record (admits P/Q+wp)")
    ap.add_argument("--lemma-name", default=None)
    args = ap.parse_args()

    raw = open(args.infile).read() if args.infile else sys.stdin.read()
    try:
        c = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"accept": False, "reason": f"invalid candidate JSON: {e}",
                          "escalation_required": False}))
        sys.exit(2)

    theory_text = None
    if args.theory:
        try:
            theory_text = Path(args.theory).read_text(encoding="utf-8", errors="replace")
        except OSError:
            theory_text = None
    lkeys = ledger_keys(args.ledger) if args.ledger else None

    out = evaluate(c, theory_text=theory_text, lkeys=lkeys,
                   escalation_path=args.escalation, lemma_name=args.lemma_name)
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(0 if out["accept"] else 1)


if __name__ == "__main__":
    main()
