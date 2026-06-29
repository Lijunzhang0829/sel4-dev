#!/usr/bin/env python3
"""spec_signal_miner.py — LLM-guided detector-signal mining (manual, propose-only).

The meta-level loop of the LLM-led spec-strengthening experiment: after a round
of scanning+strengthening, an operator runs this on demand. It mines the LOCAL
experiment archive (wins AND losses — failures live ONLY here / in the ledger,
NOT in commits/deliveries which package successes), asks an LLM to abstract a
MECHANICAL signal the current detector misses (precision mode: a feature that
separates losses from wins so low-quality candidates are demoted before they
burn trial budget), then CALIBRATES the proposed predicate deterministically
(hard gate: it must demote losses without killing a single verified win), and
writes a signal-proposal report.

It does NOT edit the detector. A human/LLM reviews the proposal and hand-writes
the signal into spec_slot_hints.py + commits — keeping the controllability and
reproducibility that motivated using a deterministic detector in the first
place. LLM leads the DIRECTION (what signal); a deterministic gate + git keep
the TRUTH and the TRACE.

Division of labour (mirrors the object level one layer up):
  detector hint  ->  LLM proposes signal   (this tool, step 2)
  trial          ->  deterministic calibration on the labeled set (step 3)
  git            ->  human promotes reviewed signal into the detector

Usage:
  spec_signal_miner.py [--slot P|Q|F] [--mode precision] [--model sonnet]
                       [--max-losses N] [--out FILE] [--dry-prompt]

Reads:  spec-strengthen/experiments/strengthen-*/[0-9]*-*/  (+ proposal/verdict/trial)
Writes: reports/spec-strengthen/signal-proposals/<ts>.md    (proposal, never the detector)
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spec_slot_hints import (  # noqa: E402
    extract_op, classify_op, head_ident, split_pre, split_assumptions,
)

REPO = Path(__file__).resolve().parents[2]
EXPERIMENTS = REPO / "spec-strengthen" / "experiments"
WIN = {"trial_passed"}
LOSS = {"TRIAL-FAILED", "IMPACT-FAILED", "trial_failed", "impact_failed"}

# Signals the detector ALREADY has — the miner must find something BEYOND these.
CURRENT_SIGNALS = [
    "unused-premise (head absent from proof body, prefix match incl _def/_E)",
    "differential (≥1 OTHER conjunct visibly consumed → high)",
    "frame-premise (head also in post/conclusion → spared, never flagged)",
    "op read/write class (write-op premise demoted, read-op kept)",
    "implication scope (\\<lbrakk>...\\<rbrakk> ==> C assumption-weakening)",
    "rule-precondition dependency (head feeds an INVOKED named rule's "
    "precondition → demoted)",
]


# --------------------------------------------------------------------------
def _proof_of(new_lemma: str) -> str:
    """Everything after the closing quote of the statement = the proof script."""
    m = re.search(r'"\s*(.*)$', new_lemma, re.DOTALL)
    tail = m.group(1) if m else new_lemma
    # the statement itself sits in the first "...": drop up to the 2nd quote
    parts = new_lemma.split('"')
    return parts[2].strip() if len(parts) >= 3 else tail.strip()


def _rule_tokens(proof: str) -> list:
    """Lemma-name-shaped identifiers in the proof (candidate invoked rules)."""
    toks = re.findall(r"[A-Za-z_][\w']{4,}", proof)
    return sorted({t for t in toks if "_" in t})


def featurize(new_lemma: str, dropped_head: str) -> dict:
    """Stable feature dict a proposed predicate operates on."""
    pre, conjs = split_pre(new_lemma)
    form = "hoare"
    if pre is None:
        pre, conjs, _ = split_assumptions(new_lemma)
        form = "impl"
    proof = _proof_of(new_lemma)
    op = extract_op(new_lemma)
    return {
        "dropped_head": dropped_head,
        "op": op,
        "op_class": classify_op(op),
        "form": form,
        "proof": proof,
        "stmt": new_lemma[:400],
        "invoked_rules": _rule_tokens(proof),
    }


def collect_labeled_set(slot=None):
    """Walk the experiment archive → list of records with features + win/loss."""
    recs = []
    for cd in sorted(glob.glob(str(EXPERIMENTS / "strengthen-*" / "[0-9]*-*"))):
        cd = Path(cd)
        vf = cd / "verdict.txt"
        pf = cd / "proposal.json"
        if not vf.exists() or not pf.exists():
            continue
        verdict = vf.read_text().strip()
        label = "win" if verdict in WIN else "loss" if verdict in LOSS else None
        if label is None:
            continue
        try:
            prop = json.loads(pf.read_text())
        except (ValueError, OSError):
            continue
        if slot and prop.get("slot") != slot:
            continue
        new_lemma = prop.get("new_lemma", "")
        if not new_lemma:
            continue
        # dropped premise head: prefer p_claim_check, else lemma-name suffix
        dropped = ""
        pc = cd / "p_claim_check.json"
        if pc.exists():
            try:
                dc = json.loads(pc.read_text()).get("dropped_conjuncts") or []
                if dc:
                    dropped = head_ident(dc[0])
            except (ValueError, OSError):
                pass
        if not dropped:
            m = re.search(r"_no_([a-z_]+)|'$", prop.get("lemma_name", ""))
            dropped = (m.group(1) if m and m.group(1) else "")
        trial_err = ""
        tl = cd / "trial.log"
        if label == "loss" and tl.exists():
            for ln in tl.read_text(errors="replace").splitlines():
                if re.search(r"Failed to|error|unsolved|\*\*\*|noop|weakening", ln, re.I):
                    trial_err = ln.strip()[:160]
                    break
        theory = ""
        mt = re.search(r"strengthen-(.+?)-\d{8}", cd.parent.name)
        if mt:
            theory = mt.group(1)
        recs.append({
            "lemma": prop.get("lemma_name"),
            "slot": prop.get("slot"),
            "label": label,
            "verdict": verdict,
            "trial_err": trial_err,
            "theory": theory,
            "hint_lemma": prop.get("hint_lemma") or re.sub(
                r"_no_.*|'+$", "", prop.get("lemma_name", "")),
            "dropped_head": dropped,
            "rationale": (prop.get("rationale") or "")[:300],
            "feat": featurize(new_lemma, dropped),
        })
    return recs


def build_recall_prompt(low_wins, losses, slot):
    """Recall mode: find a BOOST signal that promotes under-ranked WINS (the
    detector ranked them low and almost missed) without boosting any LOSS."""
    def fmt(r):
        f = r["feat"]
        s = (f"- lemma {r['lemma']} | drop `{f['dropped_head']}` | op {f['op']}"
             f" ({f['op_class']}) | form {f['form']}\n"
             f"  proof: {f['proof'][:240].replace(chr(10),' ')}\n")
        if r["label"] == "loss" and r["trial_err"]:
            s += f"  TRIAL-ERR: {r['trial_err']}\n"
        if r["rationale"]:
            s += f"  agent-said: {r['rationale'][:160]}\n"
        return s
    pos = "".join(fmt(r) for r in low_wins)
    neg = "".join(fmt(r) for r in losses)
    sigs = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(CURRENT_SIGNALS))
    return f"""You are improving a DETERMINISTIC detector that RANKS candidate \
{slot}-slot spec strengthenings (premise drops) high/low; the agent only \
drafts the top-N, so a real strengthening ranked LOW is almost missed (a RECALL \
gap). The detector's current signals:
{sigs}

Below are REAL outcomes. The POSITIVES are verified WINS (the drop built fine) \
that the detector nonetheless ranked LOW — it under-valued them (e.g. demoted \
by the write-op rule, or low because non-differential). The NEGATIVES are \
LOSSES (the drop was load-bearing). Your job: find ONE mechanical BOOST signal \
— a structural feature that the LOW-RANKED WINS share but the LOSSES do NOT — \
so the detector can promote these missed wins to high WITHOUT promoting any \
loss (which would re-introduce noise). Computable from the feature dict alone \
(keys: dropped_head, op, op_class, form, proof, stmt, invoked_rules); NO \
Isabelle, NO semantic reasoning.

=== LOW-RANKED WINS ({len(low_wins)}) — your signal SHOULD fire (boost) ===
{pos}
=== LOSSES ({len(losses)}) — your signal must NOT fire ===
{neg}

Output ONLY a JSON object (no prose, no fence):
{{
  "signature": "<one paragraph: the structural pattern + WHY it predicts droppable>",
  "predicate_code": "def proposed_signal(feat):\\n    # returns True to BOOST (predict win)\\n    ...",
  "expected_effect": "<which low-wins it boosts, why it spares the losses>",
  "novelty": "<why this is a recall/boost signal not captured by the 6 above>"
}}
Keep it conservative: prefer missing some low-wins over firing on any loss."""


# --------------------------------------------------------------------------
def build_prompt(wins, losses, slot):
    def fmt(r):
        f = r["feat"]
        s = (f"- lemma {r['lemma']} | drop `{f['dropped_head']}` | op {f['op']}"
             f" ({f['op_class']}) | form {f['form']}\n"
             f"  proof: {f['proof'][:240].replace(chr(10),' ')}\n")
        if r["label"] == "loss":
            s += f"  TRIAL-ERR: {r['trial_err']}\n"
        if r["rationale"]:
            s += f"  agent-said: {r['rationale'][:160]}\n"
        return s
    wins_s = "".join(fmt(r) for r in wins)
    losses_s = "".join(fmt(r) for r in losses)
    sigs = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(CURRENT_SIGNALS))
    return f"""You are improving a DETERMINISTIC detector that flags candidate \
{slot}-slot spec strengthenings (premise/assumption drops) for an expensive \
Isabelle trial. The detector already has these signals:
{sigs}

Below are REAL labeled outcomes from past rounds. WINS built fine (the drop was \
sound); LOSSES failed the trial (the dropped premise was load-bearing) — these \
wasted build budget. Your job: find ONE NEW mechanical signal, NOT already in \
the list above, that separates LOSSES from WINS — i.e. a structural feature of \
the lemma/proof that predicts the drop will FAIL. The signal must be computable \
from the feature dict alone (keys: dropped_head, op, op_class, form, proof, \
stmt, invoked_rules), with NO Isabelle and NO semantic reasoning at scan time.

=== WINS ({len(wins)}) — your signal must NOT fire on these ===
{wins_s}
=== LOSSES ({len(losses)}) — your signal SHOULD fire on these ===
{losses_s}

Output ONLY a JSON object (no prose, no fence):
{{
  "signature": "<one paragraph: the structural pattern + WHY it predicts load-bearing>",
  "predicate_code": "def proposed_signal(feat):\\n    # returns True to DEMOTE (predict loss)\\n    ...",
  "expected_effect": "<which losses it catches, why it spares the wins>",
  "novelty": "<why this is NOT just one of the 6 existing signals>"
}}
The predicate gets the feature dict and may use the `re` module. Keep it \
conservative: prefer missing some losses over firing on any win."""


def call_llm(prompt, model, timeout=600):
    """Stream the claude -p process (reusing spec_agent's runner) so the
    operator sees progress and gets a clean timeout/kill instead of a blind
    subprocess.run hang."""
    from spec_agent import find_claude, run_claude_streaming
    claude = os.environ.get("CLAUDE_BIN")
    if not claude or not Path(claude).exists():
        claude = find_claude()
    effort = os.environ.get("SPEC_MINER_EFFORT", "medium")
    argv = [claude, "-p", prompt, "--model", model, "--strict-mcp-config",
            "--mcp-config", '{"mcpServers":{}}', "--tools", "", "--effort", effort,
            "--output-format", "stream-json", "--verbose", "--max-turns", "6"]
    return run_claude_streaming(argv, timeout=timeout)


def extract_json(text):
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


# --------------------------------------------------------------------------
def calibrate(predicate_code, positives, negatives):
    """Run the proposed predicate. Generic over both modes:
      precision: positives=losses (want fire=demote), negatives=wins (must NOT)
      recall:    positives=low-wins (want fire=boost), negatives=losses (must NOT)
    Hard gate: the predicate must NEVER fire on a NEGATIVE."""
    ns = {"re": re}
    try:
        exec(predicate_code, ns)  # noqa: S102 — local dev tool, restricted ns
        fn = ns["proposed_signal"]
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"predicate did not compile: {e}"}
    pos_fire = neg_fire = errs = 0
    misfired = []
    for r in positives:
        try:
            pos_fire += bool(fn(r["feat"]))
        except Exception:  # noqa: BLE001
            errs += 1
    for r in negatives:
        try:
            if fn(r["feat"]):
                neg_fire += 1
                misfired.append(r["lemma"])
        except Exception:  # noqa: BLE001
            errs += 1
    return {
        "ok": True, "n_pos": len(positives), "n_neg": len(negatives),
        "pos_fired": pos_fire, "neg_fired": neg_fire,
        "predicate_errors": errs,
        "recall_on_pos": round(pos_fire / len(positives), 3) if positives else 0,
        "regression_free": neg_fire == 0,
        "misfired_negatives": misfired[:5],
    }


def detector_priority(theory, hint_lemma, dropped_head):
    """Re-run the detector on the win's file → what priority did it give this
    drop? 'low' means a recall gap (real win the detector under-ranked)."""
    import glob as _g
    B = "verification/l4v/proof/invariant-abstract"
    f = None
    for c in (f"{B}/{theory}.thy", f"{B}/ARM/{theory}.thy"):
        if os.path.exists(c):
            f = c
            break
    if not f:
        g = _g.glob(f"{B}/**/{theory}.thy", recursive=True)
        f = g[0] if g else None
    if not f:
        return None
    try:
        hs = json.loads(subprocess.run(
            ["python3", str(Path(__file__).with_name("spec_slot_hints.py")),
             f, "--slot", "P", "--max", "120"],
            capture_output=True, text=True, timeout=40).stdout)
    except Exception:  # noqa: BLE001
        return None
    for h in hs:
        if h["lemma"] == hint_lemma and (not dropped_head
                                         or dropped_head in (h.get("premise") or "")):
            return h["priority"]
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slot", default="P", choices=["P", "Q", "F"])
    ap.add_argument("--mode", default="precision",
                    choices=["precision", "recall"])
    ap.add_argument("--model", default=os.environ.get("SPEC_AGENT_MODEL", "sonnet"))
    ap.add_argument("--max-losses", type=int, default=40)
    ap.add_argument("--out", default=None)
    ap.add_argument("--dry-prompt", action="store_true",
                    help="print the prompt + labeled-set counts, skip the LLM")
    args = ap.parse_args()

    recs = collect_labeled_set(slot=args.slot)
    wins = [r for r in recs if r["label"] == "win"]
    losses = [r for r in recs if r["label"] == "loss"]
    print(f"[miner] slot={args.slot} mode={args.mode} labeled: {len(wins)} wins"
          f" / {len(losses)} losses", file=sys.stderr)

    if args.mode == "recall":
        # positives = wins the detector ranked LOW (recall gaps); want BOOST.
        low_wins = []
        for r in wins:
            if detector_priority(r["theory"], r["hint_lemma"],
                                 r["dropped_head"]) == "low":
                low_wins.append(r)
        print(f"[miner] under-ranked wins (recall gaps): {len(low_wins)}",
              file=sys.stderr)
        if len(low_wins) < 3 or len(losses) < 3:
            print("[miner] too few recall gaps to mine a boost signal",
                  file=sys.stderr)
            return 1
        positives, negatives = low_wins, losses[:args.max_losses]
        prompt = build_recall_prompt(low_wins, negatives, args.slot)
    else:  # precision
        if len(wins) < 2 or len(losses) < 3:
            print("[miner] too few labeled examples to mine a signal",
                  file=sys.stderr)
            return 1
        positives, negatives = losses[:args.max_losses], wins
        prompt = build_prompt(wins, losses[:args.max_losses], args.slot)

    if args.dry_prompt:
        print(prompt)
        return 0

    print(f"[miner] asking {args.model} to propose a signal ...", file=sys.stderr)
    out = call_llm(prompt, args.model)
    obj = extract_json(out)
    if not obj or "predicate_code" not in obj:
        print("[miner] LLM did not return a usable proposal:\n" + out[:800],
              file=sys.stderr)
        return 3

    cal = calibrate(obj["predicate_code"], positives, negatives)
    ts = max((p.name.split("-")[-1] for p in EXPERIMENTS.glob("strengthen-*")),
             default="manual")  # avoid Date.now; tag from latest experiment
    report = render_report(args.slot, args.mode, obj, cal)
    out_path = Path(args.out) if args.out else (
        REPO / "reports" / "spec-strengthen" / "signal-proposals"
        / f"{ts}-{args.mode}.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\n[miner] proposal written: {out_path}", file=sys.stderr)
    print(f"[miner] calibration: fires {cal.get('pos_fired')}/{cal.get('n_pos')}"
          f" positives, {cal.get('neg_fired')} false-fires on negatives, "
          f"regression_free={cal.get('regression_free')}", file=sys.stderr)
    return 0


def render_report(slot, mode, obj, cal):
    fire_verb = "demote" if mode == "precision" else "boost"
    pos_name = "losses" if mode == "precision" else "low-ranked wins"
    neg_name = "wins" if mode == "precision" else "losses"
    verdict = ("✅ PROMOTABLE" if cal.get("regression_free")
               and cal.get("pos_fired", 0) > 0 else "⚠ NEEDS REVIEW")
    return f"""# Signal proposal — {slot}-slot (mode: {mode})

> LLM-proposed, deterministically calibrated, **propose-only**. Review then
> hand-write into `spec-strengthen/scripts/spec_slot_hints.py` if promotable.

Mode: **{mode}** — the signal should fire ({fire_verb}) on {pos_name}, NEVER on {neg_name}.

## Verdict: {verdict}

| metric | value |
|---|---|
| {pos_name} fired ({fire_verb}d) | {cal.get('pos_fired')}/{cal.get('n_pos')} (recall {cal.get('recall_on_pos')}) |
| **{neg_name} fired (regression — must be 0)** | **{cal.get('neg_fired')}** |
| regression-free | {cal.get('regression_free')} |
| predicate errors | {cal.get('predicate_errors')} |
| misfired {neg_name} | {cal.get('misfired_negatives')} |

## Structural signature
{obj.get('signature')}

## Why it's novel (not an existing signal)
{obj.get('novelty')}

## Expected effect
{obj.get('expected_effect')}

## Proposed predicate (for calibration; review before promoting)
```python
{obj.get('predicate_code')}
```

## Promote?
- If regression-free AND it catches a meaningful share of losses → integrate the
  pattern into `scan_p` (a new demote rule, mirroring rule-precondition), add a
  `lb_*`/evidence field, re-run the detector on the labeled lemmas to confirm,
  then commit.
- If it misfires on wins → reject or tighten; the hard gate is **zero wins
  demoted**.
"""


if __name__ == "__main__":
    sys.exit(main())
