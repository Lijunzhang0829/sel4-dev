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

## Incremental heap sealing (the O(N²) fix) — opt-in `--incremental`

17. **Whole-prefix re-verification → sealed heap + unsealed tail.**
    Defect: every check re-processed the whole growing locked prefix against the
    Refine heap → O(N²) (per-check 40s→141s→toward the 600s ceiling; resume paid
    a ~123s full reverify). This was the dominant remaining wall.
    Fix: `seal_prefix` runs `isabelle build` of the locked prefix into a child
    session heap (parent = Refine); the build IS a full verification. Each
    subsequent check (`check_incremental`) loads that heap and processes ONLY
    [unsealed tail + candidate] via `isabelle process -l <seal>`. Re-seal every
    `--seal-every` locks. Seal-build failure → auto-fallback to whole-prefix check.
    Measured (Schedule_R, 117-lemma prefix): seal build 145s (once, replaces the
    123s reverify); per-check 141s → **11.3s (12.5×)**; working theory 800→17
    lines. Cross-heap fact citation verified (a new lemma cites a sealed lemma).
    Bounds: imports qualified with the parent session for `build`; `timeout`
    wall-ceiling on the incremental `process` (isabelle process ignores
    check-theory.sh's env var). Default OFF — the stable whole-prefix path is
    untouched.

## Few-shot + retrieval-fix pass (harness optimization approaching its ceiling)

18. **Arch contamination in the index (fixed).** build_import_index walked all
    of l4v; per-arch theory basenames let RISCV64/X64/AARCH64 lemmas into an ARM
    build's index → Undefined-fact stuck roots. Fixed: exclude non-L4V_ARCH dirs.
19. **is_quota false-positive (fixed).** Bare "quota"/"resets" in a proof body
    halted a run despite ample quota. Fixed: require CLI limit phrasing + short body.
20. **Static pre-check RETIRED under --incremental.** ③'s whole value was saving
    a ~140s check; with incremental heaps a check is ~13s, so the check itself is
    the cheapest oracle. ③ kept false-rejecting real base-HOL/lib lemmas (hoare_*,
    disj_cong, valid_objs'). Gated to the slow non-incremental path only. LESSON:
    optimizations reconfigure each other — ① made ③ net-negative.
21. **Few-shot from locked siblings (Reflexion memory).** On generation, inject
    up to 2 already-locked proofs from the same name-family as templates. Measured:
    STRUCTURE TRANSFER works (tcbSchedAppend_corres adopted tcbSchedEnqueue_corres's
    cross-guard chain, reached 2 residual goals vs prior total failure) — but did
    NOT crack the deep roots; the tcbSched*_corres family is gated by the
    heap-list root tcbQueueAppend_corres, and bitmap roots have no proving sibling.
22. **wp-schematic strategy hint.** On an unresolved ?Q schematic, direct the
    model to reorder wp / add hoare_vcg glue / give the assertion explicitly.

## Human root-unlock probe → the composability wall

23. **Import-shadowing helpers (fixed).** Surfaced while testing the "crack a root,
    unlock the family" hypothesis: injecting the *human* proof of the fixed root
    `tcbSchedAppend_corres` over the 138-locked machine prefix. It checked ~85% of
    its 194 lines, then failed `*** No such variable in theorem: "?ts"` on
    `apply (cut_tac ts="..." in tcbQueueHead_iff_tcbQueueEnd)`. Root cause: the
    machine had regenerated a helper **named `tcbQueueHead_iff_tcbQueueEnd`**
    (origin=replan) with a *specialized* statement
    (`ksReadyQueues_asrt s ⟹ (tcbQueueHead (ksReadyQueues s (d,p)) = None) = …`, no
    `ts`), which **shadowed the imported general lemma** in TcbAcc_R.thy
    (`list_queue_relation ts q nexts prevs ⟹ tcbQueueHead q ≠ None ⟷ …`, has `ts`).
    The human proof cites the imported form by its `ts` variable; the shadow won
    name resolution → the variable vanished. The prior name-collision check only
    guarded against locked/plan item names — **not import names**. Fix: at both
    plan-validate and replan-filter, drop any helper whose name is in
    `import_index`/`import_consts` (`build_import_index(Schedule_R)` = 14274 facts;
    the culprit and all three human-cited helpers — tcbQueueHead_iff_tcbQueueEnd,
    list_queue_relation_nil, heap_ls_append — are all in it, so all get protected).
    The import already provides the fact; a same-named divergent helper is both
    redundant and a silent shadow. LESSON: a regenerated interior does not just
    need to be *internally* green — its helper NAMESPACE must not collide with the
    import closure, or proofs written against the imports (human proofs, or the
    machine's own later imports) break invisibly. Caveat: the shadow helper was
    cited by one other locked proof (`tcbSchedAppend_valid_mdb'`), so the fix is
    not a no-op on an existing corpus — a real re-run forces that proof onto the
    imported form (or a renamed helper).

**Composability finding (the real result of path 1) — EMPIRICALLY CLOSED.**
Hypothesis going in: the machine invents its OWN interior decomposition (helper
names + signatures), so a human top-level proof — entangled with the *human's*
interior lemma forms — would fail at the first divergence when dropped onto the
machine interior, and the divergences would be *plural* (one per regenerated
helper). Test: rename the single import-shadowing helper out of the way (machine
`tcbQueueHead_iff_tcbQueueEnd` → `_M`, so the human's citation resolves to the
imported general form) and re-check the human `tcbSchedAppend_corres` body
against the 138-locked prefix. RESULT: **OK (237547ms) — green end-to-end.** The
plural-divergence fear was *falsified*: there was exactly ONE blocking
divergence (the namespace shadow); once removed, the human root body composed
cleanly all the way to `done` against the machine-regenerated interior. So:
(a) the machine interior is compatible *enough* with a human proof that a single
namespace fix suffices — the interiors agree on the imported-lemma substrate,
they only clashed on a name the machine should never have redefined; (b) "crack
a root (human-supplied), unlock the family" (root破族解) IS viable — the human
root now locks, and few-shot can regenerate the rest of the family against it;
(c) the guard (entry 23) is not just defensive hygiene — it is what makes
human/machine proof interoperability possible at all on this corpus. This is a
corpus-level property and a paper-grade finding on machine-vs-human proof reuse:
divergent interiors interoperate once neither side shadows the shared import
closure.

## Peeling a leaf wall — combinator-selection directive (entry 24)

24. **stateAssert-combinator directive + strategy-aware guillotine (validated).**
    Diagnosing why the leaf `setCurThread_corres` (a 14-line human proof, high tower
    value — gates switchToThread/switchToIdleThread) stuck at 0 tries of progress.
    Forensics on the prior run's item dir showed the failure was NOT what the leaf
    taxonomy assumed: the machine HAD created both scaffolding helpers it needed
    (`setCurThread_corres_helper` for modify↔modify, and its own `idle_thread_not_queued`
    — in a weaker 2-hyp/union form than the human's 3-hyp/∃-neg). The proof was
    structurally almost right; it died at step 2 on `rule corres_stateAssert_implied
    [where P'=idleThreadNotQueued]` — a UNIFICATION failure, because `P'` in that
    combinator is ABSTRACT-typed and `idleThreadNotQueued` is a concrete-state
    predicate. The right combinator is `corres_stateAssert_add_assertion` (no P').
    BOTH are in imports — so this is neither a retrieval nor a creation gap: it is
    fine-grained discrimination inside a family of near-synonym combinators. The
    generic `same_goals` hint (fires on 2 identical errors) only says "switch
    strategy"; it never names the discriminator, and the old guillotine stuck the
    item out at try 2 (try1≡try2). Fix: (a) a `stateassert` STRATEGY_HINT that names
    the family and the discriminator ("add_assertion folds Q' into the concrete
    guard, no P'; don't pass a concrete predicate to the abstract P' — that's why
    your rule didn't unify"); fires generically on any stalled `stateAssert` corres,
    so a close under it stays pure-machine. (b) strategy-aware guillotine: stuck-out
    only when the SAME directive category fails twice, not on identical errors alone
    (so a newly-triggered directive gets a fair try). RESULT (pure machine,
    `--tools ""`, generic directive, zero per-lemma hint): the directive MOVED the
    model off the wrong combinator — try1 `corres_stateAssert_implied` → try2
    `corres_stateAssert_add_assertion` (sig changed; the original try1≡try2 deadlock
    that killed it at try 2 was broken). Progress advanced 2 tries / 2 walls deeper
    and EXPOSED the next wall precisely: the final concrete→abstract discharge —
    `idleThreadNotQueued` (¬ in ksReadyQueues) ↔ abstract `idle_thread_not_queued`
    (¬ in ready_queues) — where the model's `fastforce simp: ready_queues_relation_def
    valid_queues_def …` SEARCH-EXPLODES. The model self-diagnosed it correctly ("drop
    valid_queues_def, derive via the targeted lemma") but couldn't land the targeted
    discharge: the human bridges it with `in_ready_q_tcbQueued_eq` (an import lemma)
    used as `dest`, NOT by unfolding `_relation_def` under fastforce. LESSON: leaf
    walls are LAYERED — a "leaf" that fails is often several thin walls stacked
    (combinator-selection, then cross-relation-discharge), and a generic directive
    peels one and reveals the next. Corollary next directive: on a discharge that
    explodes while unfolding a `*_relation_def`, point at the specific cross/transfer
    lemma (`*_cross`, `in_ready_q_tcbQueued_eq`) instead of the raw unfold.

## Ceiling reached (pure machine, zero human)

Schedule_R contract arm, interface layer **73/97 (~75%)** pure machine. The
remaining ~24 stuck roots survive ALL harness levers (full-lib retrieval,
few-shot structure transfer, 12x-faster iteration, strategy hints). They are a
few genuine capability roots — heap-list corres (tcbQueueAppend_corres), bitmap
L1/L2 word arithmetic (isHighestPrio_corres + family), wp-orchestration, and
search-explosion timeouts — with the rest of each family few-shot-transferable
but dependency-gated on those roots. Time is now 98% opus generation (median
174s, tail 24min on hard cases); check is 1% (incremental). Further harness
optimization has low expected yield; the walls are opus's deep-proof ceiling,
addressable only by human root-unlock (budgeted) or a stronger prover model.
