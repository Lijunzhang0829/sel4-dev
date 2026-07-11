# regen harness evolution — defect → fix → evidence

Chronological log of every harness change on the corpus-level regeneration
driver (`regen_incr.py`), each tied to the MEASURED defect that forced it and
the archived evidence. This is the "how did you know to add these" record that
per-lemma item archives (`items/NN-*/`) do not capture on their own.

Substrate: Schedule_R.thy, mode-A (keep op-level contract surface + exported
interface, delete the private interior, machine regenerates it). Model: opus,
tool-less (`--tools ""`), verified only by batch `check-theory` on the growing
locked prefix.

## The chain

1. **Whole-theory rewrite → incremental lock-in.**
   Defect: rewriting all ~15 decls each round got a DIFFERENT 5–15% wrong every
   time — union of per-round successes covered everything, intersection never
   green (SubMonad_R: 8 rounds, $19.90, 0 green).
   Fix: per-item generate → check prefix → **lock on green, never regenerate**.
   Evidence: `reports/regen/submonad_r-20260701-235944` (pathology) vs
   `submonad_r-incr-*` (monotone).

2. **Blind repair → SIGPIPE fix + structured triage.**
   Defect: container `check-theory` used `grep '^***' | head -20`; under
   `pipefail`, head closing the pipe killed the script at RC=141 BEFORE printing
   any `***` — repair got only "FAILED", spun blind.
   Fix: `grep -m20`; `summarize_errors` (block count + first block full + one
   line per remaining block + cross-try trend).
   Evidence: `submonad_r-incr-*/items/*/error.txt` before/after.

3. **`--tools ""` mandatory (memorization integrity).**
   Defect: `--allowedTools ""` did NOT disable tools (tools=26); the agent ran
   Bash/WebFetch to read the on-disk original theory — `cat`, not regeneration.
   Fix: `--tools ""` (verified tools=0). Perturbation/divergence controls bound
   residual recall.

4. **Header hallucination → deterministic assembly.**
   Defect: model prefixed a copyright comment before `theory`, wrapper double-
   wrapped → two `theory` commands ("Bad context for command theory").
   Fix: assembler renders header + kept statements from keepset; model supplies
   ONLY proof bodies (also the anti-cheat guarantee — statements immutable).

5. **API 529 / weekly-quota → transient handling + clean halt.**
   Defect: overload/limit responses were wrapped as an 8-line fake theory and
   charged as a failure; a quota-exhausted run parked every remaining item as
   "unparseable" (4 corres cases falsely failed).
   Fix: `is_transient` backoff-retry; `QuotaExhausted` halts cleanly, state
   preserved, `HALTED.json`.
   Evidence: `schedule_r-incr-20260706-003655` (quota-killed corres) then locked
   on resume.

6. **Timeout black hole → bisection localizer + directional hint.**
   Defect: on 600s search-explosion the only feedback was "killed after 600s" —
   zero subgoal info, agent changed body blindly (possibleSwitchTo_corres).
   Fix: `localize_timeout` bisects apply-steps (short-cap probes) to name the
   exploding step; `STRATEGY_HINTS["timeout"]` = stop unfolding, use targeted
   rule/wp.

7. **Direction-neutral feedback → strategy directives.**
   Defect: "still N goals" let the model drift wrong (arch_switch_thread_ksQ:
   2→9 goals by over-unfolding vs human's 2-line `simp; wp`).
   Fix: mode-keyed `STRATEGY_HINTS` (timeout / same_goals / growing); observed
   to flip the agent from "unfold defs" to "strengthen postcondition"
   (addToBitmap thinking logs).

8. **Retrieval wall → ancestor import-fact index + undefined-fact fallback.**
   Defect: the 3 hardest cases cited machinery that EXISTS in imported theories
   (TcbAcc_R/KHeap_R/lib) but tool-less opus can't retrieve the names; it raw-
   unfolded defs → explosion.
   Fix: BFS ancestor-only index (2670 facts; downstream excluded to avoid
   citing not-yet-existing facts); relevance-ranked top-N injected; if the
   prover names an Undefined fact that exists in the index, force it in.
   Evidence: `tcbSchedDequeue_iflive'` locked after the index fed back
   `tcbQueueRemove_if_live_then_nonz_cap'`.

9. **Global stop-on-stuck → per-item stuck queue + auto-un-park.**
   Defect: one no-progress item halted the whole 100-item run for a human
   round-trip; carried cascades stayed parked even after their blocker locked.
   Fix: `queue_stuck` parks one item and continues; resume auto-un-parks items
   whose blocker is now locked; dependency fast-path defers consumers of a
   stuck fact without burning tries.

10. **Alias interface gap → alias→lemma conversion (batch judgment).**
    Defect: kept `lemmas` aliases (e.g. `asUser_cteCaps_of = ...[OF asUser_ctes_of]`)
    cite deleted-interior crunch facts — verbatim keep fails.
    Fix: convert the alias to a proper lemma obligation the machine must prove;
    machine reproved both (crunch/lift structure re-invented).

11. **Replan explosion → per-cluster budget guard.**
    Defect: a hard fixed item spawns a replan tree of helpers, each with
    tries+replan — one bitmap cluster hit 20+ checks and drained the global
    budget for $5 / 0 locks.
    Fix: `--cluster-budget` caps cumulative checks charged to a cluster (fixed
    root + replan descendants, tracked via `cluster` tag); over cap → park the
    whole cluster.

12. **Template scope leak → template in replan (template arm).**
    Defect: `--arm template` injected the human body into `item_prompt` but NOT
    `replan_prompt`; any item reaching replan lost the template → template arm
    systematically UNDER-estimated.
    Fix: `replan_prompt` shows the human original (it reveals which support
    lemmas the decomposition needs — exactly what replan should reproduce).
    Evidence: arch_switch_thread_ksQ autopsy (`gen-0` had no TEMPLATE section).

## Scalability caveat (known, not yet fixed)

Each check re-verifies the WHOLE growing prefix against the heap → **O(N²)**
wall (measured: check median 40s at ~200 lines → 121s at ~739 lines). Dominant
cost is still opus generation (median 25s, tail 40 min), so this was deferred.
Options for a later target: batch re-verification every K locks with
local-only checks in between; or an intermediate compiled heap per K locks
(weigh against the isabelle-build init wall).

## Cross-cutting infra lessons

- Orphan reaper: driver deaths (server restart / kill) leave container polyml
  children spinning 100% CPU forever (7 × 26h ≈ OOM'd the box). `check_theory`
  now reaps any poly older than 900s before each check.
- Never `nohup &` inside a harness-tracked Bash call — it detaches; use
  `run_in_background` + Monitor.
- All state persisted per lock (`locked.json`) → every power outage (≈daily
  here) costs only the in-flight item; `--resume-from` re-verifies the prefix
  once and continues.

## Speed + robustness pass (before incremental-heap work)

13. **Retrieval over full-dump prefix (RepoCoder-style).**
    Defect: item_prompt dumped ALL locked facts (O(N) growth, lost-in-the-middle).
    Fix: `rank_prefix` — token-overlap retrieval, top-K + recent-K, capped.

14. **Import index followed imports into lib/ (was refine-dir only).**
    Defect: index resolved theory files ONLY under proof/refine/ARM, so the
    whole monad/Hoare library was invisible (2 hoare_ lemmas). Retrieval couldn't
    surface combinators; the static pre-check false-rejected real lemmas
    (hoare_strengthen_post, hoare_post_imp).
    Fix: basename→path map over all of l4v; BFS the full ancestor import closure.
    Index 2670 → 14274 facts (+ 2431 consts). Likely also a root cause of earlier
    "missing combinator → raw-unfold explosion" failures.

15. **Static fact pre-check, made ultra-conservative.**
    Defect: rejecting cited facts absent from the index suppressed VALID proofs
    (false positives on lib lemmas, on constants like valid_objs', and on
    fragments like valid_objs'_maxDomain split by the prime).
    Fix: only flag a token that is (a) in a clear FACT position (wp:/rule/OF/
    elim, NOT unfolding:/simp:), (b) well-formed (no leading _, proper prime),
    (c) absent from lemma index + consts index + locals + own names, (d) with NO
    prefix match. Catches invented composites (st_tcb_at_tcb_at') only.

16. **Baseline cache + retry + startup lock/orphan cleanup.**
    Defect: (a) re-checking the fixed original theory (~148s Schedule_R) every
    resume; (b) baseline transient-fail right after container cold-start assert-
    killed the run; (c) a crashed prior run left an orphan poly holding the
    Refine flock → every check died "another Isabelle tool is already running".
    Fix: baseline cached by (sha, HEAD); 3× retry; driver-startup kills container
    polys + removes stale session locks (no check of ours runs yet at startup).

Still O(N²): each check re-verifies the whole prefix (prefix reverify 123s on
resume; per-check 40→141s and growing). Next: incremental heap sealing (item 1
in the scalability caveat) — the only fix for the dominant remaining cost.
