#!/usr/bin/env python3
"""trial_probe.py — measure WASTED conditional-rewrite trials on a hot simp step.

Background: anon_scan.py / goal_aware_reduce.profile() trace at simp_trace_depth_limit=1 and count only
FIRED `rewrite rule "..."` lines. That structurally hides the cost hypothesis behind direction (b)
"reduce references": ambient CONDITIONAL simp rules whose LHS matches the goal, get TRIED (condition
discharge recursively invokes the simplifier), and then FAIL — so the rule never fires and never shows
up as a rewrite, yet the discharge work was spent. Removing/scoping such ambient rules would cut this
waste globally (every downstream automation call), including on the critical path.

The classic simp_trace prints, even at depth_limit=1:
    [d]Applying instance of rewrite rule "NAME":   <- LHS matched, attempt begins
    [d]Trying to rewrite: <conditional rewrite>    <- it's CONDITIONAL: must discharge a premise
    [d]FAILED  / [d]SUCCEEDED                       <- premise discharge outcome
    [d]Rewriting: <eqn>                             <- an actual rewrite happened (fired)

So at the SAME cost as the existing depth-1 scan we can count wasted trials (FAILED) and attribute each
to the ambient rule that caused it. High wasted fraction => direction (b) has a target; near-zero =>
the time is genuine def-unfolding (consistent with high anon%) and (b) is dead on this line.

Run INSIDE the container.  Usage:  trial_probe.py <thy_abs> <session> <line> [cap_mb]
"""
import sys, os, re, subprocess, shutil
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goal_aware_reduce as G

RULE_RE  = re.compile(r'Applying instance of rewrite rule "([^"]+)"')
TRYING   = "]Trying to rewrite:"
REWRITE  = "]Rewriting:"
DEPTH_RE = re.compile(r'^\[(\d+)\](.*)$')


def parse_depth_split(tracef):
    """Depth-aware attribution (needs SIMP_TRACE_DEPTH>=2). Assign every actual `Rewriting:` step to the
    innermost open conditional trial. A trial closing FAILED => all its rewrite work was WASTED (the rule
    never fired). A trial closing SUCCEEDED rolls its work up to its parent (so if an ANCESTOR later fails,
    the whole discharge is wasted); work that rolls up to top-level is NECESSARY. This turns 'how many
    failed trials' into 'how much rewrite WORK was spent on trials that failed'."""
    stack = []                       # open trials: {depth, rule, rw}
    wasted = necessary = toplevel = 0
    wasted_by = Counter(); last_rule = None
    n_fail = n_succ = 0
    if not os.path.exists(tracef):
        return {}
    with open(tracef, encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            m = DEPTH_RE.match(ln.strip())
            if not m:
                continue
            d = int(m.group(1)); rest = m.group(2)
            if rest.startswith("Applying instance of rewrite rule"):
                rm = RULE_RE.search(rest);  last_rule = rm.group(1) if rm else last_rule
            elif rest.startswith("Trying to rewrite"):
                stack.append({"depth": d, "rule": last_rule, "rw": 0})
            elif rest.startswith("Rewriting"):
                if stack: stack[-1]["rw"] += 1
                else: toplevel += 1
            elif rest == "FAILED":
                fr = stack.pop() if stack else {"rw": 0, "rule": last_rule}
                wasted += fr["rw"] + 1            # +1 = the matching/attempt itself
                wasted_by[fr["rule"]] += fr["rw"] + 1
                n_fail += 1
            elif rest == "SUCCEEDED":
                fr = stack.pop() if stack else {"rw": 0}
                if stack: stack[-1]["rw"] += fr["rw"]     # roll up; parent's verdict decides
                else: necessary += fr["rw"]
                n_succ += 1
    tot = wasted + necessary + toplevel
    return {"depth_split_available": True, "rw_wasted": wasted, "rw_necessary_cond": necessary,
            "rw_toplevel": toplevel, "rw_total_attributed": tot,
            "wasted_frac": round(wasted / tot, 4) if tot else None,
            "n_fail_dsplit": n_fail, "n_succ_dsplit": n_succ,
            "top_wasted_rules": wasted_by.most_common(10)}


def probe(thy, session, line, cap_bytes):
    tmp, name, dst = G._mk(thy, session, trace_line=line)
    tracef = os.path.join(tmp, "trace.txt")
    cmd = (f"timeout --kill-after=30s {G.ABS_CAP} {G.ISA} process -l {session} -d {G.L4V} "
           f"-T {os.path.join(tmp, name)} 2>&1 | head -c {cap_bytes} > {tracef}")
    subprocess.run(cmd, shell=True)
    truncated = os.path.exists(tracef) and os.path.getsize(tracef) >= cap_bytes
    if os.environ.get("KEEP_TRACE") and os.path.exists(tracef):
        shutil.copy(tracef, "/tmp/trace_keep_%d.txt" % line)
    n_apply = n_trying = n_failed = n_succ = n_rewrite = 0
    n_anyrule = n_addrule = n_unknown = 0   # reconciliation vs old profile() counting
    last_rule = None
    failed_by = Counter(); succ_by = Counter(); applied_by = Counter()
    if os.path.exists(tracef):
        with open(tracef, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                for rm in re.finditer(r'rewrite rule "([^"]+)"', ln):   # old profile() metric
                    n_anyrule += 1
                    if rm.group(1) == "??.unknown": n_unknown += 1
                if "Adding rewrite rule" in ln: n_addrule += 1
                m = RULE_RE.search(ln)
                if m:
                    n_apply += 1; last_rule = m.group(1); applied_by[last_rule] += 1; continue
                if TRYING in ln:
                    n_trying += 1; continue
                if REWRITE in ln:
                    n_rewrite += 1; continue
                # FAILED/SUCCEEDED lines look like "[1]FAILED" possibly with leading spaces
                s = ln.strip()
                if s.endswith("FAILED") and re.match(r'^\[\d+\]FAILED$', s):
                    n_failed += 1
                    if last_rule: failed_by[last_rule] += 1
                elif s.endswith("SUCCEEDED") and re.match(r'^\[\d+\]SUCCEEDED$', s):
                    n_succ += 1
                    if last_rule: succ_by[last_rule] += 1
    dsplit = parse_depth_split(tracef) if os.environ.get("SIMP_TRACE_DEPTH", "1") != "1" else {}
    shutil.rmtree(tmp, ignore_errors=True)
    # interpretation:
    #   n_trying  = conditional-rule trials (LHS matched, premise had to be discharged)
    #   n_failed  = trials whose premise could NOT be discharged => WASTED work, rule never fired
    #   n_succ    = conditional trials that fired
    #   n_rewrite = actual rewrite steps that happened (uncond fires + the rewrites done WHILE
    #               discharging premises of both failed and successful trials — i.e. includes waste)
    return {
        "file": os.path.basename(thy), "line": line, "truncated": truncated,
        "n_apply": n_apply, "n_trying": n_trying, "n_failed": n_failed,
        "n_succeeded": n_succ, "n_rewrite": n_rewrite,
        "n_anyrule_oldmetric": n_anyrule, "n_unknown_anon": n_unknown, "n_addrule": n_addrule,
        "failed_frac_of_trials": round(n_failed / n_trying, 4) if n_trying else None,
        "top_failed_rules": failed_by.most_common(10),
        "top_succeeded_rules": succ_by.most_common(6),
        **dsplit,
    }


if __name__ == "__main__":
    thy, session, line = sys.argv[1], sys.argv[2], int(sys.argv[3])
    cap_mb = int(sys.argv[4]) if len(sys.argv) > 4 else 200
    import json
    r = probe(thy, session, line, cap_mb * 1_000_000)
    print(json.dumps(r, indent=1))
