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

## Wall B root: `defs` deleted by the keepset — first tower-leaf CLOSED (entry 25)

25. **`defs`-style spec definitions must be kept (keepset bug) — setCurThread_corres
    GREEN, pure machine.** Peeling entry-24's wall A exposed wall B: try2 hit
    `*** Undefined fact: "idleThreadNotQueued_def"`, and try3/4 (after dropping that
    unfold) `*** killed after 600s` — a fastforce search-explosion. Root cause is NOT
    a tactic: `idleThreadNotQueued` is the concrete predicate `setCurThread` asserts,
    and its body lives in `defs idleThreadNotQueued_def:` on Schedule_R line 676 —
    the design-spec pattern (declare the constant `consts'` in KernelStateData_H,
    give its body via `defs` in the proof theory). The extractor's `DECL_KW` matched
    `definition` but NOT `defs`, so this declaration was invisible: never tracked,
    never kept — mode-A stripped it as private interior. But a `defs` is a DEFINITION,
    not a provable obligation: the machine cannot regenerate it (there is nothing to
    prove), and a kept concrete op that mentions the constant is un-dischargeable
    without it — the model either cites the missing fact (undefined) or brute-forces
    an OPAQUE predicate until fastforce explodes. Fix (regen_extract.py): add `defs`
    to DECL_KW/NAME_RE and classify every `defs` as KEPT_DEF unconditionally — keep
    it verbatim, like the code it defines. RESULT: with `idleThreadNotQueued_def`
    restored to the keepset AND the entry-24 stateassert directive active, the leaf
    `setCurThread_corres` locks GREEN pure-machine (try4, 13.6s, `--tools ""`, zero
    per-lemma hint). The green proof uses `corres_stateAssert_add_assertion` (wall A),
    unfolds `idleThreadNotQueued_def` (wall B1), and discharges via the cross lemma
    `not_queued_curThread_state_relation` — NOT the exploding fastforce (wall B2
    vanished: B1 and B2 were the SAME root — an opaque predicate forces brute force;
    restore its definition and the clean path opens). LESSON (mode-A methodology): the
    contract/interior split MUST classify definitional commands (`defs`, giving bodies
    to spec `consts'`) as KEEP — they are spec surface, not regenerable proof. A single
    mis-bucketed `defs` silently made an entire leaf (and its tower above) unprovable.
    Two GENERIC harness fixes (combinator directive + keepset defs-retention), no
    per-lemma assistance, cracked the first tower-bottom leaf and its dependents
    (switchToThread/switchToIdleThread lose their setCurThread blocker).

## Repo-level proof-engineering metrics → harness direction (entry 26)

26. **First repo-level proof-engineering measurement — the numbers that redirect
    harness work.** Problem setting sharpened to "LLM proof engineering for large
    proof repositories": the question is not per-lemma pass-rate but how the machine
    REUSES / ORGANIZES / GENERALIZES proofs vs the human corpus. Five metrics
    (integration / generality / compositionality / dedup / maintainability). First
    numbers, Schedule_R machine 138-locked (contract arm, 20260712) vs the human
    theory, computed statically:

    - **Generality (helper downstream reuse):** machine mean **0.15, max 1**, 85%
      one-off; human **0.65, max 7**, 64% one-off. Machine lemmas ~4× less reused,
      NEVER reused more than once. (Human `sch_act_wf` reused 7×; no machine helper >1.)
    - **Compositionality (per-proof local fan-out):** machine median **0** (mean
      0.22, max 2); human median **2** (mean 2.16, max 13). support/target **0.89
      vs 7.42**. Machine proofs are near-MONOLITHIC. Corrects an earlier qualitative
      claim: the machine `setCurThread_corres_helper` (abstracting `modify`) was ONE
      example of over-factoring; SYSTEMATICALLY the machine UNDER-factors (median
      fan-out 0 = most machine proofs rest on zero local lemmas).
    - **Dedup:** of 65 machine helpers, **14 re-invent an existing import** (22% —
      e.g. `getIdleThread_corres`, `setSchedulerAction_corres`, both imported corres,
      re-proved verbatim), only **7 net-new-reusable** (11%), 44 one-off. Re-invention
      (14) > net-new-reusable (7): NET REUSE IS NEGATIVE.
    - Caveat: human reuse counted WITHIN Schedule_R only → under-counts (human lemmas
      reused downstream); true gap is larger.

    **Unifying finding: the machine does LOCAL, MONOLITHIC proof engineering** — closes
    each obligation in near-isolation, mostly throwaway scaffolding, frequently
    re-derives existing facts. The human does LIBRARY-level engineering. The LLM's
    deficit is proof ORGANIZATION, not single-point proving.

    **Harness direction this sets (the standing to-do list, priority order):**
    1. **Reuse-of-imports** — DONE (entry 23 shadow guard). Extend: warn/deprioritize
       when a proposed helper's STATEMENT (not just name) duplicates an import.
    2. **Reuse-of-locked-helpers** — surface already-locked helpers of adjacent
       targets in the proposer context and reward citing them (few-shot does this for
       structure; add explicit "prefer an existing lemma over a new one" pressure so
       the 44 one-offs shrink).
    3. **Reward general lemma design** — when the same fact is needed by ≥2 targets,
       steer the machine to state it ONCE as a shared helper rather than inline it per
       target (attacks the fan-out-0 monolith pattern + the max-reuse-1 ceiling).
    4. **Maintainability** — the projection-over-unfold directive (relation_explosion,
       entry-25-adjacent) is the tactic-brittleness lever; keep converting
       fastforce-over-big-def into targeted projection.

    The metrics are the SCOREBOARD for harness changes: a harness improvement is only
    real if it moves fan-out / reuse / dedup toward the human distribution, not just
    the green count. Recompute the five metrics after any harness change that touches
    proposal/planning.

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

## Skill compliance review → split repair harness from regen benchmark harness (entry 27)

27. **Current `regen_incr.py` is a productive repair/shakedown harness, not yet the strict `isabelle_prover_regen` benchmark harness.** A review against `.claude/skills/isabelle_prover_regen/SKILL.md` found a real mismatch. The skill requires: fixed code endpoints, op-level `corres` contract surface + external interface as the kept oracle, private interior statements deleted, whole proof corpus regenerated, final aggregate metrics, and memorization-gap controls. The current driver still says what it is in its header: `plan -> per-lemma`. It keeps many fixed statements beyond the op-level contract surface and then fills bodies or replan helpers incrementally. That is valid for debugging proof-generation mechanics and root-cause discovery, but if reported as the final benchmark it would collapse toward the anti-pattern the skill warns about: per-lemma infill with too much human decomposition retained.

    Evidence reviewed:
    - `regen/HARNESS-EVOLUTION.md` entries 1-26 record defect -> fix -> evidence for the existing driver.
    - Historical Claude project transcripts under `~/.claude/projects/...` preserve earlier `claude -p` prompts/responses for SubMonad_R/Schedule_R regeneration attempts.
    - Per-call audit artifacts now also live in `reports/regen/<run>/items/*/{gen,replan}*.{prompt,ndjson,final,meta}` for both Claude-style and Codex relay calls.
    - Latest Schedule_R run `reports/regen/schedule_r-incr-20260720-021707` demonstrated residual-obligation discovery: `chooseThread_corres` spawned `chooseThread_ksReadyQueues_asrt_from_relation`, which locked green, and left `chooseThread_ready_qs_runnable_from_relation` / `chooseThread_ready_queues_corres` as the next true units.

    Design decision: do not delete the current harness. Keep it as **Mode R: repair/obligation-discovery harness** because it is useful for finding latent obligations, testing prompt/retrieval changes, and generating evidence. Add a separate **Mode B: skill-compliant benchmark harness** that starts from a thinner oracle and reports only corpus-level results.

    Required Mode B changes:
    1. Contract/interface extraction: compute the op-level `corres` contract surface and the shared external interface; do not keep private helper statements as fixed items.
    2. Private-interior stripping: render a stripped input where code endpoints, framework definitions, `defs`-style code definitions, contract statements, and external interface are kept, but private helper statements are removed.
    3. Obligation tree state: root nodes are contract lemmas; generated helper statements are children discovered from checked residual goals; `stuck` remains audit state, not the generation unit.
    4. Planner change: initial planning may propose helper families, but no human private statement is treated as fixed ground truth. Replan helpers are persisted as generated nodes with origin and consumer links.
    5. Metrics gate: final green corpus must produce aggregate wall/LOC/lemma-count plus integration/generality/compositionality/dedup/maintainability metrics before being called a result.
    6. Memorization-gap bundle: require at least two controls, e.g. perturbation + divergence or temporal + oracle-thinning, recorded with the run.

    Practical next step: implement Mode B behind explicit flags or a new driver (`regen_contract.py`) rather than silently changing `regen_incr.py`. The current driver has accumulated useful operational fixes (incremental sealing, import retrieval, stuck queue, codex-relay audit, historical cases); Mode B should reuse those components but change the kept-oracle/stripper/planner semantics.

28. **Artificial branch helper split exposed by `chooseThread_ready_queues_empty_corres`.**

    Symptom: after `switchToIdleThread_corres` was locked, retrying the generated
    helper `chooseThread_ready_queues_empty_corres` still failed. The helper tried
    to prove the empty-ready-queue branch in isolation, but its statement retained
    the whole concrete `if l1 \<noteq> 0 then ... else switchToIdleThread` program.
    As a result the proof still had to account for the non-empty concrete branch,
    even though the abstract precondition asserted all ready queues were empty.

    Diagnosis: this was not a missing `switchToIdleThread_corres` proof anymore.
    It was a bad generation unit. The original human `chooseThread_corres` proof
    does not isolate this empty branch. It first aligns `curDomain`, then uses
    `corres_gets_queues_getReadyQueuesL1Bitmap` to obtain the boolean relation
    `(l1 = 0) = (\<forall>p. queues p = [])`, and only then applies `corres_if2` so the
    empty and non-empty branches are discharged under matched branch assumptions.
    Splitting before that discriminator was available created an artificial
    helper with an unreachable-branch obligation.

    Harness implication: replan should not split branch helpers across an
    unresolved discriminator correspondence. For `if`/case programs, the better
    unit is the nearest enclosing correspondence that produces the branch
    relation, plus child obligations under the branch assumptions. `stuck` remains
    an audit state; it should not define the generation unit. A future planner
    should represent discriminator facts such as
    `corres_gets_queues_getReadyQueuesL1Bitmap` as explicit parent nodes and only
    split children after those facts are locked.

    Evidence:
    - `reports/regen/schedule_r-incr-20260720-154143` locks
      `switchToIdleThread_corres`.
    - `reports/regen/schedule_r-incr-20260720-154656` retries
      `chooseThread_ready_queues_empty_corres` and still fails on the branch
      alignment shape.
    - The subsequent useful path moved to the original `chooseThread_corres`
      dependency chain: `ksReadyQueuesL1Bitmap_return_wp`, `Max_prio_helper`,
      `curDomain_or_return_0`, then bitmap lookup helpers.

29. **Ordinary replan → hard lemma mode + lemma pool.**
   Defect: hard cases such as `bitmapL1_highest_lookup`,
   `switchToThread_corres`, and `possibleSwitchTo_corres` were handled by the
   same retry/replan loop as ordinary lemmas. This blurred different failure
   classes: word-level bitmap arithmetic, corres-composition search explosion,
   and malformed schematic proof scripts.
   Fix: add `classify_hard_failure`, `hard_mode_prompt_section`, and
   `hard_lemma_pool.json` recording. Hard mode is enabled by default and can be
   disabled with `--no-hard-lemma-mode`. Failed attempts now emit
   `items/*/hard-profile-N.json`; replans receive category-specific guidance
   adapted from recent prover-agent patterns: verifier-feedback search,
   APOLLO-style syntax repair, Prover-Agent auxiliary lemmas, and Seed-Prover
   lemma-pool bookkeeping.
   Evidence: smoke replay in `reports/regen/hard-lemma-mode-smoke-20260721`: the
   bitmap case classifies as `word_bitmap_arithmetic`; `switchToThread_corres`
   classifies as `timeout_search_explosion` + `corres_composition`;
   `possibleSwitchTo_corres` classifies as `syntax_schematic_repair`.

30. **Separate structural `corres` composition failures from hard-lemma mining.**
   Defect: the first hard-mode smoke classified `possibleSwitchTo_corres` as a
   syntax/schematic repair case because one generated proof used a malformed
   schematic variable. A later live retry showed the deeper blocker: after
   syntax repair, generated proofs still timed out while composing existing
   leaf correspondences, cross guards, scheduler-action cases, and WP
   side-conditions. This is not primarily a missing arithmetic/helper lemma.

   Policy constraint: original human proof bodies may be read only for
   post-hoc diagnosis of failure class. They must not be supplied to the LLM as
   proof templates, replay order, tactic scripts, or oracle-like composition
   sequences. The harness should learn generic repair operators from failures
   and checked probes, not copy the original proof's local ordering.

   Harness implication: add a distinct structural-composition mode alongside
   hard-lemma mode. It should try generic, auditable operations such as
   splitting broad guard additions into smaller guard obligations, localizing
   the exact timed-out subgoal, changing only one composition combinator at a
   time, limiting `wp`/`wpsimp` search, and generating small bridge lemmas from
   residual goals. This mode is expected to help cases like
   `possibleSwitchTo_corres`, `switchToThread_corres`, and queue-scheduler
   composition lemmas, while the bitmap `word_log2`/Max chain remains a
   different hard-lemma class.

   Evidence:
   - `reports/regen/schedule_r-incr-20260721-001730` retried
     `possibleSwitchTo_corres`; after the initial syntax precheck issue was
     fixed, attempts still hit 600s timeouts and were classified as
     `timeout_search_explosion`, `corres_composition`, and
     `state_relation_plumbing`.
   - `reports/regen/schedule_r-incr-20260721-004701`,
     `reports/regen/schedule_r-incr-20260721-005959`, and
     `reports/regen/schedule_r-incr-20260721-011013` show hard-lemma mode can
     lock some queue/bitmap helper lemmas, but still needs premise-forcing or
     positive checked exemplars for facts such as `list_queue_relation_nil`.

31. **Literature-backed direction for structural composition mode.**
   Literature review: recent prover-agent work supports treating long proofs as
   verifier-driven search rather than one-shot script generation. COPRA uses a
   stateful backtracking search in which proposed tactics are executed and the
   next prompt is built from proof-environment feedback plus selected history and
   retrieved lemmas. ReProver/LeanDojo emphasizes accessible-premise retrieval.
   HunyuanProver and LeanProgress add guided tree search / progress prediction,
   and older tactical systems such as TacticToe and Isabelle PSL show that
   tactic-level search with reusable strategy schemas is a mature direction.

   Relevance to Schedule_R: `possibleSwitchTo_corres` and `switchToThread_corres`
   should not be handled by ordinary hard-lemma replan alone. They need a
   structural-composition search mode that expands candidate proof prefixes,
   checks them immediately, keeps only prefixes that reduce or simplify subgoals,
   and retries locally around the first bad composition point. This is compatible
   with the no-original-proof-reuse policy because the search operators are
   generic (`corres_split`, guard splitting, restricted WP, case localization,
   premise retrieval) and are selected from current verifier feedback, not copied
   from human proof bodies.

   Proposed implementation sketch:
   - represent a proof attempt as a small tree of checked prefixes rather than a
     single generated body;
   - score nodes by exact checker result, subgoal count/size, timeout category,
     and dependency closure;
   - retrieve premises from the current subgoal, with forced positive premises
     allowed only when discovered from generated/checked probes or public import
     facts;
   - keep all LLM conversations and checked-prefix decisions as audit artifacts;
   - ban prompt injection of original proof bodies or original tactic order.

32. **Standalone structural-composition probe smoke.**
   Implemented `regen/structural_composition_probe.py` as an isolated module,
   not merged into `regen_incr.py`. The probe checks prefixes of generated proof
   attempts, closes each prefix with `oops`, and records the first deterministic
   checker error or timeout. It supports `--seal-run/--seal-id` so late
   Schedule_R items can be checked against an existing incremental heap instead
   of replaying the locked prefix.

   Smoke results:
   - `possibleSwitchTo_corres`: sealed probe found the first timeout at generated
     step 3, a broad guard-side `fastforce` over `pspace_aligned_cross`,
     `pspace_distinct_cross`, `tcb_at_cross`, and state-relation facts. This
     supports a generic guard-splitting / automation-limiting repair mode.
   - `switchToThread_corres`: sealed probe found prefixes 1-10 execute quickly;
     the first timeout is step 11, a bare `apply fastforce` after
     `corres_stateAssert_ignore`. This supports local side-condition probing
     rather than whole-body regeneration.
   - `isHighestPrio_corres`: first deterministic error occurs at step 3 on
     `corres_gets`, exposing the existing `ready_queues`/`Max`/
     `lookupBitmapPriority` equivalence. This classifies it as bitmap-Max hard
     lemma work, not structural composition search.

   Evidence report: `reports/regen/structural-composition-probe/README.md`.

33. **Standalone structural repair candidates + continuation smoke.**
   Extended `regen/structural_composition_probe.py` with `--repair`,
   `--repair-continue-steps`, and a targeted PolyML reaper for its own probe
   report path. The continuation check appends a small number of original
   generated suffix steps after a repaired prefix, so a candidate must show more
   than "checks under `oops`" to be considered proof-progress evidence.

   Results:
   - `possibleSwitchTo_corres`: the broad generated `fastforce` timeout can be
     replaced by `blast` over the same explicit `intro`/`dest` facts. This repair
     checks and also supports a `+3` suffix continuation through the next
     generated `corres_split[OF curDomain_corres]` step. `clarsimp` and `simp`
     only expose concrete side-condition errors; they are diagnostic, not direct
     progress. Mechanical conjunctive guard splitting still times out.
   - `switchToThread_corres`: replacing the bare generated `fastforce` with
     `clarsimp`/`simp` avoids timeout but `+3` continuation reaches a concrete
     error around `corres_split[OF tcbSchedDequeue_corres]`. This is useful
     localization, not yet a complete repair.

   Harness implication: promote checked-prefix localization, sealed-heap repair
   probes, bounded `blast` over explicit generated facts, diagnostic
   `clarsimp`/`simp`, and targeted PolyML cleanup. Do not yet promote naive
   conjunctive guard splitting.

   Evidence report: `reports/regen/structural-composition-probe/README.md`.

34. **possibleSwitchTo fastforce-to-blast closure attempt.** The fastforce_to_blast repair removes the first timeout and reaches scheduler-action branch code, but the generated proof does not close. The next failures are deterministic branch-structure problems: untyped case_tac action, leaked branch subgoals after typing it as scheduler_action, and schematic branch-correspondence obligations after generic corres_if/corres_if2 and case-block attempts. Harness implication: promote bounded blast over explicit facts as first-step repair, but add branch-subgoal search after case/if splits before merging this into the main harness.

35. **Main harness structural-composition repair integration.**
   Integrated the standalone structural-composition findings into
   `regen/regen_incr.py` without using original human proof bodies or proof order.

   Added capabilities:
   - shared proof-step splitter for apply/by/proof/supply/subgoal commands;
   - timeout localization now checks `oops`-closed generated prefixes, avoiding
     misleading failures from an unterminated proof state;
   - localized timeout metadata is written to `timeout-localized.json`;
   - structural repair probes run after a localized timeout. If the bad generated
     step is an explicit-fact `fastforce`, the harness tests local generated-body
     repairs such as bounded `blast` over the same `intro`/`dest` facts plus
     diagnostic `clarsimp`/`simp`, and records results under
     `items/*/structural-repair-probes/`;
   - probe summaries are appended to the next LLM error feedback, so the model can
     prefer a checked local repair instead of regenerating the whole proof;
   - branch-subgoal leakage is detected from errors such as untyped
     `case_tac action`, many subgoals after `case_tac`/`corres_if`/`corres_if2`,
     or schematic branch `corres` obligations;
   - a new `branch_subgoals` strategy directive tells the model not to continue
     with a flat linear apply suffix after if/case splits, but to align the
     discriminator and solve branches separately;
   - `classify_hard_failure` now records `branch_subgoal_search` so these failures
     are tracked as structural-composition blockers, not hard semantic lemmas.

   Promotion boundary: the harness now has checked local repair probes and
   branch-aware feedback. It still does not automatically construct a full
   branch proof tree. The next step, if needed, is to turn branch-subgoal feedback
   into a real search layer that stores each branch subgoal, retrieves premises
   per branch, and locks branch proofs independently.

   Evidence behind this change:
   - `possibleSwitchTo_corres` experiments showed `fastforce_to_blast` removes
     the first guard-side timeout and can continue through several generated
     suffix steps;
   - full closure then failed at deterministic scheduler-action branch structure,
     confirming the need for branch-subgoal search rather than more lemma mining;
   - `switchToThread_corres` showed similar broad-automation localization, where
     `clarsimp`/`simp` expose concrete subgoals but do not close the proof.


36. **Branch-aware retry evidence: `possibleSwitchTo_corres` partially converges but
    still needs a real branch-search layer.**
   After entry 35, two focused Codex-relay runs retried `possibleSwitchTo_corres`
   from the locked Schedule_R prefix without using original human proof bodies:
   `reports/regen/schedule_r-incr-20260721-065301` and
   `reports/regen/schedule_r-incr-20260721-072417`.

   Result:
   - `065301` locked three generated guard bridge helpers:
     `possibleSwitchTo_pspace_aligned_bridge`,
     `possibleSwitchTo_pspace_distinct_bridge`, and
     `possibleSwitchTo_target_tcb_bridge`.
   - The main `possibleSwitchTo_corres` body then moved from the earlier broad
     timeout / 11-subgoal shape to a scheduler-action branch-composition failure
     with 3 residual goals.
   - `072417` retried against the 167-locked prefix. Try 1 reached a 2-subgoal
     state: one concrete `tcb_at' t` guard obligation and the remaining main
     `corres`; try 2 regressed to a 3-subgoal branch/schematic state after
     `corres_guard_imp`.

   Interpretation: the new feedback is doing useful work. It discovers and locks
   real state-relation bridge helpers, and it can push the proof past the first
   search explosion. The remaining blocker is not a hard semantic lemma like the
   bitmap `word_log2` chain. It is proof-structure control: after
   `corres_if`/scheduler-action case splitting, the harness still asks the model
   for a single linear proof suffix instead of managing each residual branch as a
   checked subgoal with its own local premises.

   Next harness boundary: add a branch-subgoal search module that captures the
   residual subgoals after a promising prefix, retrieves premises per branch,
   and asks the LLM to close branches independently before reassembling the
   script. Until that exists, repeated whole-body retries can make progress but
   are unstable.


37. **Textual branch hints are insufficient; checked branch-prefix search is now the
    next hard boundary.**
   After entry 36, the branch hint was strengthened twice in `regen_incr.py`:
   first to prefer bridge lemmas as `intro`/`blast` for pure guard implications,
   then to prefer `corres_split_sched_act` over nested `corres_if2` when the
   discriminator is `sched_act_relation act act'`. These are generic scheduler
   composition rules, not `possibleSwitchTo_corres`-specific proof templates.

   Evidence from `reports/regen/schedule_r-incr-20260721-073655`:
   - try 1 adopted the bridge-intro idea but invented an undefined fact
     `st_tcb_at_tcb_at'`;
   - try 2 removed the invented fact but still left branch/schematic residuals
     after `corres_if2`.

   Evidence from `reports/regen/schedule_r-incr-20260721-074901`:
   - both tries stabilized at 2 residual goals, so the new guidance reduced
     branch drift but did not close the proof;
   - the model still used `clarsimp dest!` / low-level `obj_at'_def` expansion
     in a guard context where a checked `blast intro: possibleSwitchTo_target_tcb_bridge`
     prefix works.

   Standalone probes then checked generated-body local variants without using
   original human proof order:
   - `reports/regen/structural-composition-probe/possibleSwitchTo-guard-blast-20260721`
     shows the guard replacement checks through prefix step 5 and the first
     deterministic error is step 6, `corres_split[OF thread_get_corres[where r="(=)"]]`,
     leaving five schematic pre/postcondition subgoals. This indicates a wrong
     domain-thread correspondence choice / missing explicit intermediate guards.
   - `reports/regen/structural-composition-probe/possibleSwitchTo-ethread-guard-blast-20260721`
     shows a naive switch to `curDomain_corres'` + `ethread_get_when_corres` fails
     earlier at the `curDomain_corres'` prefix with three residual goals.

   Conclusion: repeated whole-body LLM retry has reached a local optimum. The
   remaining problem is not a hard semantic lemma and not merely prompt wording.
   The harness must grow a checked branch-prefix search layer that can choose
   among alternative composition prefixes (`curDomain_corres` vs `curDomain_corres'`,
   `thread_get_corres` vs `ethread_get_when_corres`, `corres_if2` vs
   `corres_split_sched_act`), observe the residual subgoals after each choice,
   and ask the LLM to solve those subgoals locally.

38. **First session-level root planning and sealing exposed two trust-boundary
    fixes.** On branch `regen-refine-session-harness`, the ARM Refine campaign
   planned the two theory-DAG roots, `LevityCatch` and `Move_R`, from only the
   target DAG and fixed contract inventory.

   First, the original `is_contract` predicate classified any goal-bearing
   `corres` statement as a contract. This leaked human decomposition rules such
   as `corres_cap_fault`, `corres_lookup_error`, and `corres_injection` into the
   oracle. The classifier now requires operation-name-first `_corres`/
   `_ccorres` names and excludes `corres_*`/`ccorres_*` rules, with regression
   tests. The ARM inventory changed from 347 broad candidates to 287 strict
   contracts; `Bits_R` changed from seven candidates to the two operation
   contracts `getCurThread_corres` and `getIdleThread_corres`.

   Second, a corres-only oracle does not justify concrete exports from
   support-only roots. An audited planner draft proposed one high-risk concept
   per root, but the critique removed both because their ownership could not be
   derived from reachable fixed contracts. The resulting empty-interface plan
   was assembled deterministically. Both theories checked against `BaseRefine`,
   then built into `RegenRefineARM_L0`; a child-heap import smoke test passed.
   The heap manifest records its SHA-256 and explicitly records that no human
   Refine heap or import was used.

   Harness implications:
   - archive prompt, raw events, final answer, metadata, hashes, and no-tool audit
     for every planner/prover call;
   - distinguish independent provider calls from current-session file-agent
     development calls;
   - permit an explicit empty plan for support-only roots, but label it as an
     empty structural interface rather than proof-library success;
   - require every planned root export to cite reachable fixed contract IDs;
   - inventory kept framework definitions before planning `Invariants_H` and
     `StateRelation`; corres statements alone are insufficient at that layer.

   Evidence: `reports/regen/refine-arm-session/` (`planner/`, `checks.json`,
   `seal/manifest.json`, `provenance.jsonl`).

39. **Framework declarations require an independent, scope-aware trust-boundary
    inventory.** The next ARM Refine layer contains `ArchMove_R` and
    `Invariants_H`. The latter has no strict op-level corres root, but defines
    the executable invariant vocabulary consumed throughout Refine. Inferring
    framework only from contract token occurrence would therefore delete the
    meaning of later obligations before planning them.

    `regen/framework_inventory.py` now inventories exact semantic declarations
    in the designated framework owners (`Invariants_H`, `StateRelation`, and
    `Corres`), records normalized statement hashes and dependency/consumer
    metadata, excludes proof declarations, and assembles proof-free theories
    from the inventory. The current ARM result contains 240 declarations: 183
    from `Invariants_H`, 54 from `StateRelation`, and 3 from `Corres`. Only 74
    are directly named by the 287 strict contracts, so direct corres-reference
    closure sees roughly one third of the fixed semantic surface.

    A follow-up identity audit fixed apostrophe-bearing Isabelle names such as
    `invs'` and `obj_at'`; the earlier token-boundary approximation reported 82
    direct declarations, while exact Isabelle-identifier boundaries report 74.

    The first dynamic check exposed why command-kind filtering alone is unsafe:
    it retained a `private definition`, four private lemmas, a custom method,
    and `add_upd_simps` because command modifiers and proof-package commands
    were not recognized. After recognizing those boundaries, the next check
    exposed a split `context ... begin` around the inductive `subtree`
    declaration. The final assembler selects inventory entries exactly, keeps
    only enclosing contexts required by selected declarations, removes all 11
    proof locales, and checks that the generated declaration multiset equals
    the inventory with no missing or extra entry.

    The generated `ArchMove_R` and 1,205-line framework-only `Invariants_H`
    passed against `RegenRefineARM_L0` (11.7 s and 146.8 s). They built into
    `RegenRefineARM_L1` in 197.7 s; a downstream child-import smoke passed in
    20.7 s. The sealed heap is 10,165,559 bytes with SHA-256
    `923dc194b72c3ab742d6d61724e4fce78a986a6dbb4824c9e3d1d43a5363b74a`,
    and its manifest records `human_refine_heap_loaded: false`.

    Main implication: contracts determine the proof goals, but do not fully
    determine the semantic language of those goals. Framework freezing must
    solve declaration classification, indirect semantic closure, Isabelle
    scope reconstruction, and proof-structure non-leakage before lemma-DAG
    planning starts.

    Evidence: `reports/regen/refine-arm-session/framework/inventory.json`,
    `framework/layer1-assembly.json`, `framework/checks-layer1.json`, and
    `framework/seal-layer1/manifest.json`.

40. **`InvariantUpdates_H` established a premise-health and context-budget
    preflight gate.** Before planning the first support-only theory above the
    frozen invariant framework, the harness rebuilt the premise catalog with
    the current extractor. This caught a stale-identity risk introduced by the
    earlier apostrophe-name fix and added extractor/record-set hashes to the
    catalog.

    For `InvariantUpdates_H`, 14,627 of 15,280 catalog records are accessible
    through frozen imports; there are zero target-session leaks and zero bad
    statement hashes. The accessible namespace contains 385 ambiguous
    candidate names, with maximum multiplicity 13, so selected collisions now
    require canonical theory qualification instead of first-match resolution.
    The planner receives 32 BM25-ranked legal premises, 183 generated-framework
    summaries, and 287 reachable contract summaries. Its payload is 82,949
    characters under a 120,000-character hard limit. A minimal theory shell
    checked against the exact `RegenRefineARM_L1` heap in 11.8 seconds.

    An independent external planner call was rejected because exporting the
    repository-derived payload lacked explicit approval. The safe fallback was
    recorded as a current-session file-agent call, with no tool events and an
    explicit `not independent; current session contaminated` label. Its draft
    proposed a high-risk generic state-update preservation concept; critique
    removed it because contract-name summaries did not establish a common
    theorem shape or owner. The frozen plan was therefore `empty`.

    The generated `InvariantUpdates_H` contains only the legal import of
    generated `Invariants_H`. It checked in 11.9 seconds, built the
    `RegenRefineARM_L2I` checkpoint in 54.3 seconds, and passed a downstream
    smoke in 12.0 seconds. The heap SHA-256 is
    `96841cebde7a8515b267a2b9c9de31a3b577ce49a3629f09e527c7491f738d0d`.
    This is a structural zero-export result, not evidence that the historical
    helper library was reproduced. Concrete downstream residuals may still
    create generated helpers beneath red contract nodes.

    Evidence: `reports/regen/refine-arm-session/preflight/InvariantUpdates_H/`,
    `planner/InvariantUpdates_H/`, `generated-layer2-invariant/`, and
    `seal-layer2-invariant/manifest.json`.

41. **The first six green theories are structural checkpoints, not generated
    proof evidence.** A generated-vs-human prefix audit found zero strict
    op-level corres contracts in all six theories and zero generated proof
    declarations. The human prefix has 390 proof declarations; the generated
    prefix preserves exactly the 183 semantic declarations from `Invariants_H`
    and deletes every proof declaration. Human/generated LOC is 4,203/1,239.

    Several deleted support facts are highly reused by later ARM Refine files:
    `updateObject_default_inv` has 111 explicit occurrences across 12 theories,
    `obj_at'_def` has 393 across 24, `mdb_next_unfold` has 163 across 10, and
    `ps_clear_upd` has 36 across eight. `InvariantUpdates_H` also contributes 66
    `[simp]` lemmas, whose implicit effect is not captured by explicit citation
    counts. In contrast, the generated versions currently contain no proof
    script to compare: their checks only validate imports, declarations, heap
    composition, and premise isolation.

    Harness implication: distinguish `structural_checkpoint` from
    `proof_complete_seal`. A support-only theory with an empty export plan must
    remain invalidatable until immediate consumer planning/proving validates
    the empty interface or yields residual obligations that justify generated
    exports. A no-backpatch policy applied immediately after an empty build
    freezes under-planning too early and works against repo-level reuse.

    Evidence: `reports/regen/refine-arm-session/sealed-prefix-comparison.md`.

42. **`Bits_R` completed the first generated operation-level corres milestone
    and validated its immediate framework prefix.** The harness reconstructed
    `StateRelation` from exactly 54 inventoried semantic declarations and
    `Corres` from exactly three abbreviations. Both generated theories contain
    zero human proof declarations, checked against generated ancestor heaps,
    built as `RegenRefineARM_L3` and `RegenRefineARM_L4`, and passed child-import
    smoke tests. Their manifests record `human_refine_heap_loaded: false`.

    The `Bits_R` preflight exposed 14,763 legal upstream records, rejected all
    target-session premises, retrieved 26 records, and produced a 42,340-character
    planner payload under the 120,000-character limit. The fixed contract DAG
    has two roots, `getCurThread_corres` and `getIdleThread_corres`, and no
    support nodes. Both contracts closed on their first Isabelle proof attempt
    by unfolding the executable getter and fixed `state_relation` semantics.
    Thus no residual justified a `StateRelation` backpatch for this consumer.

    The resulting `RegenRefineARM_L5` heap is 3,796,499 bytes with SHA-256
    `b7941dc5d6e39ab93c7e3b8bc076dd07615f34bb41e27e4d8f40a845988b733d`.
    A child-import smoke re-resolved both exported contract theorems in 12.0 s.
    This is the first `proof_complete_seal`: both fixed statement hashes are
    preserved and both contract roots are proof-green. The planner/prover calls
    were recorded through the local current-session file-agent because tenant
    policy rejected repository-derived external payload disclosure even after
    user authorization; the metadata correctly marks them non-independent and
    contaminated by the orchestrating session.

    The run also exposed and fixed a checker bug: temporary prefix files must
    rename their internal theory to match the file basename before Isabelle
    processing. A filename mismatch is a harness failure, not a proof residual,
    and must never trigger lemma expansion.

    Evidence: `reports/regen/refine-arm-session/preflight/Bits_R/`,
    `planner/Bits_R/`, `generated-layer3-state-relation/`,
    `generated-layer4-corres/`, `generated-layer5-bits/`, and
    `seal-layer5-bits/manifest.json`.

43. **The first post-contract DAG wave uses consumer-planned structural
    checkpoints rather than reconstructing human support libraries.** `Bits_R`
    is alone in its theory-DAG layer, so the next ready layer contains the two
    independent support-only theories `EmptyFail` and `Machine_R`. Both own
    zero fixed operation-level contracts and import only generated
    `RegenRefineARM_L5.Bits_R`.

    Each preflight exposed 14,763 legal frozen-parent/endpoint records, sent 24
    ranked premises, found zero target-session leaks, and checked a minimal L5
    shell in about 12 seconds. The `EmptyFail` planner received a 21,088-character
    payload. Its direct consumer `SubMonad_R` owns no fixed contract, so neither
    the exact shape nor ownership of an `empty_fail` export was justified. The
    `Machine_R` planner received a 22,820-character payload including the five
    exact fixed `KHeap_R` contracts. Its draft proposed a high-risk generic
    machine-state frame concept; critique removed it because none of the cited
    contracts states a machine-state update, IRQ operation, or preservation
    obligation.

    Both final plans are therefore `empty`. This does not claim that the human
    helper libraries were unnecessary. The generated theories are explicitly
    marked `structural_checkpoint` and
    `invalidatable_until_consumer_validated`; a concrete `KHeap_R` or later
    residual may still justify an exact helper and upstream backpatch.

    `EmptyFail` and `Machine_R` checked independently in 12.0 and 11.8 seconds,
    then built together as `RegenRefineARM_L6` in 57.0 seconds. The 3,792,063-byte
    heap has SHA-256
    `5f28be53fbb415ebdd48aafaffe5ca9d402250ae90f121c13eb11e25fec43809`;
    a child theory importing both generated nodes passed in 12.8 seconds. The
    manifest records no human Refine heap/import use. Planner and critique
    transcripts are local current-session file-agent records and are correctly
    labelled non-independent/contaminated.

    Harness implication: readiness is evaluated per theory, but a same-layer
    wave advances persistent state only after every independent theory check,
    the combined child heap build, and a smoke importing all wave exports. The
    next ready theory is `Refine.KHeap_R`.

    Evidence: `reports/regen/refine-arm-session/preflight/EmptyFail/`,
    `preflight/Machine_R/`, `planner/EmptyFail/`, `planner/Machine_R/`,
    `generated-layer6-support-wave/`, and
    `seal-layer6-support-wave/manifest.json`.
44. **KHeap_R turned structural checkpoints into a consumer-driven
    declaration closure and completed the five-contract L7 seal.** The
    preflight parsed five immutable contracts against RegenRefineARM_L6.
    A bare shell deliberately failed on the missing exst_same-prime constant,
    while the closed shell passed after freezing exst_same and
    exst_same-prime as contract-local semantic input. The explicit constant
    identity check was necessary because Isabelle can otherwise reinterpret an
    undeclared identifier as a free variable without changing stored statement
    text.

    The first proof candidate exposed several concrete declaration gaps. The
    getter and set-object residuals required LevityCatch behavior equations for
    alignCheck and magnitudeCheck, a local lookupAround2_same1 simp bridge, and
    a narrow local in_alignCheck membership theorem. Broadly unfolding
    assert_def inside the main clarsimp produced much larger irrelevant proof
    states; the narrow behavioral theorem restored the intended abstraction
    boundary.

    The setObject_other_corres residual then required the StateRelation closure
    obj_relation_cuts_def3 plus its consumable corollary
    is_other_obj_relation_type. The representation theorem alone was not a
    sufficient downstream interface. The final projection residual reused the
    already-generated regen_projectKO_eq alias from Bits_R. These backpatches
    invalidated generated ancestor identities and triggered successful
    cascading rebuilds from L0 through L6 and, for StateRelation-only changes,
    from L3 through L6.

    All five fixed roots are proof-green: getEndpoint_corres,
    setObject_other_corres, setEndpoint_corres, setNotification_corres, and
    getNotification_corres. The formal RegenRefineARM_L7 heap is 6,375,827
    bytes with SHA-256
    99af543c756a34d709f08afa03c51b51e3acff3504032f4233ac65c5a9058283.
    A child session imported RegenRefineARM_L7.KHeap_R and resolved all five
    theorems. No human Refine heap was loaded.

    Because the user explicitly requested comparison with the human KHeap proof
    during repair, the planner/proof/repair transcripts are correctly marked
    current-session contaminated. This is a valid harness and seal milestone,
    not an independent proof-generation measurement. The next DAG-ready
    theories are Refine.Init_R and Refine.SubMonad_R.

    Evidence: reports/regen/refine-arm-session/preflight/KHeap_R/,
    planner/KHeap_R/, contract-local-semantics/KHeap_R.json,
    invalidation-KHeap_R/manifest.json, generated-layer7-kheap/,
    lemma_graph-KHeap_R.json, and seal-layer7-kheap/manifest.json.

## Immutable per-theory transcript gate (entry 45)

45. **Shared planner files were audit records in name but not immutable evidence.**
    Defect: the session harness archived prompt/final/meta files, but the active
    theory scripts reused fixed planner paths, so a rerun could overwrite an
    earlier conversation. Most existing Bits_R/KHeap_R/support-wave records were
    current-session file-agent replays, not independent Codex API/CLI calls, and
    no seal gate joined model calls, candidate checks, repairs, heap build, and
    smoke into one ordered theory history.

    Fix: `llm_transcript.py` now provides one audited Codex/Claude interface;
    `theory_trace.py` creates an append-only, hash-chained ledger under
    `audit/<Theory>/<generation-id>/`, snapshots every non-LLM artifact, and
    rejects mutation after seal. `theory_export_planner.py` and
    `theory_checkpoint.py` share a required generation ID.
    `contract_milestone.py` records preflight, plan, proof, every repair/check,
    assembly, build, and smoke, and cannot write its production manifest until
    transcript sealing passes. The legacy `support_wave.py` is explicitly
    replay-only.

    Seal policy: real `codex exec --json` or tool-disabled `claude -p` calls
    only; exact prompt/raw/final/stderr/meta/audit six-tuples; mandatory stage
    coverage; no tool events; hash integrity; red check followed by a repair
    call; final green check. Current-session/file-agent records remain useful
    development evidence but cannot production-seal. Historical contaminated
    transcripts cannot be upgraded retroactively.

    Evidence: `regen/llm_transcript.py`, `regen/theory_trace.py`,
    `regen/scripts/test_theory_trace.py` (real seal, replay rejection, tamper
    rejection, repair-order tests).

## Script-owned theory generations (entry 46)

46. **Conversation-driven startup left generation identity and resumption
    underspecified.** A planner and prover could accidentally receive different
    IDs, a subprocess restart could silently open a second audit directory, and
    there was no stable command that reproduced the complete lifecycle of one
    theory.

    Fix: `run_theory_generation.sh` is the canonical production launcher. It
    defines a generation as one complete attempt for one `.thy`, creates a UTC
    timestamp plus random-suffix ID once, locks per theory, passes the ID to all
    stages, and retains launcher status and a resumable command on failure.
    Lemma calls, provider retries, red-to-repair cycles, Isabelle checks, build,
    and smoke remain events in the same generation. `--resume ID|latest`
    continues it; a fresh invocation creates a distinct immutable attempt.

    `--dry-run` previews the exact pipeline without writing campaign state.
    Direct production Python CLIs require `--generation-id`, and
    `TheoryAuditTrail` no longer invents one. The launcher rejects unregistered
    theories, mismatched pipelines, concurrent runs for one theory, reuse of an
    existing ID without `--resume`, and successful drivers that fail to produce
    a production audit manifest. The current registry covers support-checkpoint theories including
    `InvariantUpdates_H`, `Invocations_R`, `Init_R`, and `SubMonad_R`, plus
    the monolithic `Bits_R` contract pipeline; later DAG nodes must register
    their deterministic driver before execution.

    Evidence: `regen/run_theory_generation.sh`, `regen/theory_trace.py`,
    `regen/theory_export_planner.py`, `regen/theory_checkpoint.py`, and
    `regen/contract_milestone.py`.

## Generated-premise-aware L8 support wave (entry 47)

47. **Frozen-parent retrieval alone was insufficient after the first generated
    contract theory.** L8 consumers needed legal access to facts exported by
    generated ancestors, while the original premise catalog intentionally
    contains no human Refine records and no generated campaign records.

    Fix: L8 preflight extracts declarations directly from generated ancestor
    sources, verifies source and statement hashes, filters by each target’s
    generated import closure, and ranks that legal subset separately from the
    15,246 frozen-parent/endpoint records. Init_R may see KHeap_R; SubMonad_R may
    see EmptyFail and KHeap_R. Neither can see its sibling or an unimported
    target theory.

    Two real Codex planning calls per theory produced empty reviewed interfaces.
    Individual theory check/build/smoke gates and the combined L8 wave all
    passed. Both generation manifests report zero replay calls and complete
    real-provider stage coverage. The resulting RegenRefineARM_L8 heap SHA-256
    is 1613022c566b8533b7cbe039856257c3c2c5bb2611d082a6ee85b0201d89c1e7.

    Evidence: `reports/regen/refine-arm-session/audit/{Init_R,SubMonad_R}/`,
    `preflight/{Init_R,SubMonad_R}/`, `seal-layer8-wave/manifest.json`, and
    `generation-order.json`.

## False-empty planning audit (entry 48)

48. **A valid production transcript does not imply a well-supported plan.** The
    four L8 calls were genuine `codex-exec-audited` calls with return code zero,
    complete six-tuples, no replay, and no tool events. `Init_R=empty` is
    justified because the target DAG has no descendants. `SubMonad_R=empty` is
    only provisional: it directly feeds `ArchAcc_R`, whose 24 contracts include
    15 attributed declarations and seven `lift_corres_args` declarations, while
    retrieval exposed several legal submonad facts.

    Root cause: the schema-1 prompt said to prefer empty, required an export to
    cite at least two contracts, omitted consumer paths and contract attributes,
    and did not require exact statement proposals. Its critique repeated the
    same conservative assumption instead of trying to falsify it. The model
    followed its instructions and did not hallucinate; the harness made a false
    empty too easy to accept.

    Fix: support planning now includes shortest consumer import paths, exact
    contract attributes, and per-contract shared-symbol counts. Schema 2
    requires complete non-overlapping contract clusters, explicit coverage
    classification, and exact candidate Isabelle statements. The critique first
    performs an adversarial false-empty review, and validation rejects uncovered
    contracts, overlap, and an empty decision that leaves an export candidate.
    Empty interfaces with reachable consumers are marked
    `provisional_until_direct_consumer_green` and remain invalidatable. A
    diagnostic `ArchAcc_R` shell confirmed that `lift_corres_args` is available;
    it parsed the attributed declaration and failed only at the deliberately
    inadequate proof.

    The sealed L8 transcripts were not rewritten. Any corrected `SubMonad_R`
    replan requires a fresh generation ID and residual-driven transitive
    resealing. Evidence:
    `reports/regen/refine-arm-session/planning-quality/L8-empty-interface-review.json`,
    `regen/theory_preflight.py`, `regen/theory_export_planner.py`,
    `regen/theory_checkpoint.py`, and `regen/scripts/test_theory_trace.py`.

## ArchAcc_R failure taxonomy and current fix status (entry 49)

49. **A failed theory generation is not evidence that the current theorem is
    too hard.** ArchAcc_R exposed four distinct failure classes that the audit
    ledger previously mixed together:

    1. DAG/response failures: illegal or forward dependencies, incomplete proof
       response sets, malformed proof bodies, and draft support nodes that were
       not legal leaves.
    2. Premise/context failures: missing generated facts, guessed theorem names,
       full-repository proof prompts over the 120,000-character limit, and
       irrelevant facts obscuring the exact relation-cut or projection premise.
    3. Harness lifecycle failures: interrupted `docker exec` leaving Poly/ML
       alive, repeated planning on resume, incorrect residual selection when
       support and contract records shared a contract ID, and proof extraction
       dropping a leading `using assms`.
    4. Isabelle semantic residuals: concrete no-fail, guard lifting,
       relation-cut membership, address/slot normalization, duplicate-entry
       semantics, and monad reduction obligations left by a candidate proof.

    The first six ArchAcc_R contracts are now proof-green, but their history
    demonstrates that these classes must remain separate. The ASIDPool and
    direct PDE contracts became tractable after exact premise retrieval and
    small semantic interfaces. `get_master_pde_corres` was the clear oversized
    proof unit: it required six leaves for master address, concrete domain
    membership, two duplicate directions, slot alignment, and abstract
    `get_pde` reduction. Once composed from those leaves, prefix 6 passed in
    57.6 seconds.

    Current status:

    - **DAG/response containment: fixed.** Draft and certified plans are
      schema-checked; dependency IDs are normalized only on a unique match;
      illegal/forward edges, uncovered response sets, forbidden commands, and
      incomplete proof bodies fail before Isabelle. A bad draft may reach the
      critique, but a bad final plan cannot reach proving.
    - **Premise/context adequacy: partially fixed.** Frozen and generated
      premise visibility is checked, target-session/human Refine premises are
      rejected, exact required names are forced into retrieval, and proof
      prompts are projected to the transitive target dependencies and exact
      premise IDs. All current 9 support and 24 contract prompts are below the
      limit (maximum 31,392 characters; contract 7 is about 16,000). This
      projection has not yet been exercised by a new external contract-7 call,
      and retrieval completeness for the remaining 18 contracts is unknown.
    - **Lifecycle safety: partially fixed.** One generation ID owns an
      append-only transcript; verified residuals bind failed response/log and
      green source/log hashes; contract residual lookup selects a valid contract
      skeleton; `using ... by` and `using ... apply ... done` are retained;
      Isabelle `process` has an in-container 600-second wall ceiling. Still
      open: ArchAcc_R resume reruns both planner calls, and interactive interrupt
      can leave launcher `exit_code` inconsistent with `status=failed`.
    - **Semantic residual handling: working but not fully automated.** A failed
      proof can be classified, promoted to an exact local leaf statement,
      independently probed, hash-bound, and reused monotonically. This produced
      the nine current supports and moved the green frontier from zero to six.
      Difficult residual classification and statement promotion still required
      manual engineering.

    One planning defect remains visible in green evidence:
    `regen_master_pde_dom` cites `regen_master_pde_addr`, while the certified
    graph records only imported/generated-premise dependencies. The graph is
    statement-valid but not proof-citation-closed. Before a repo-level seal, the
    harness must extract generated-fact citations from each accepted proof and
    require the corresponding edge or reject it under a leaf-only policy.

    Evidence: `reports/regen/refine-arm-session/audit/ArchAcc_R/`,
    `reports/regen/refine-arm-session/residual-history/ArchAcc_R.json`,
    `reports/regen/refine-arm-session/preflight/ArchAcc_R/`,
    `regen/archacc_milestone.py`, and `regen/scripts/test_theory_trace.py`
    (20 tests).

## Lemma-DAG planning redesign (entry 50)

50. **The original ArchAcc_R plan got the contract order mostly right but
    under-planned the lemma graph.** It correctly placed unprimed operations
    before primed wrappers, object reads before master/lookup consumers, setters
    before stores, and lookup before mapping consumers. The earliest certified
    plans, however, had only one support node. The plan still had only three
    supports when `get_master_pde_corres` was first attempted; its six
    operation-specific leaves appeared only after bounded repair failed.

    The replacement planner should build a bounded, exact statement-level lemma
    DAG before proof generation:

    1. **Partition contracts into semantic operation families.** Distinguish
       object read, object write, primed guard wrapper, page-table/directory
       entry access, master-entry access, store, lookup, mapping construction,
       and ASID lookup. Use fixed statements and framework declarations, not
       theorem-name similarity alone.
    2. **Expand each family into obligation dimensions.** Object reads expose
       concrete no-fail, type projection, result relation, and relation-cut
       obligations. Primed wrappers expose guard lifting. Master entries expose
       base/slot address, domain, alignment, duplicate consistency, and monad
       reduction. Object writes expose update relation, object-size/no-fail, and
       invariant preservation.
    3. **Propose exact helper statements and ownership.** Each active helper has
       an Isabelle statement, owner theory, dependency IDs, intended consumers,
       and one obligation dimension. A local helper may serve one hard contract;
       cross-contract sharing is not required.
    4. **Typecheck declarations before proving.** Assemble a shell containing
       fixed framework declarations, fixed contracts with `oops`, and proposed
       helper statements with `oops`. Reject free-variable reinterpretation,
       missing result types, undefined constants, and inaccessible premises.
    5. **Check premise closure and proof-citation closure.** Initial helpers may
       form an acyclic helper subgraph because the complete graph is frozen
       before generation. After accepting a proof, extract citations to
       generated helpers and compare them with declared edges. Hidden edges are
       errors, not harmless proof details.
    6. **Freeze the initial graph.** Store a graph hash before proof calls.
       Statements, ownership, and existing edges cannot change during proving.
       Contracts are green roots, helper nodes are proof units, and legal
       visible premises/framework declarations are terminal leaves.
    7. **Allow residual growth only as bounded leaves.** A runtime residual may
       attach one helper directly to the failed contract, but it must depend only
       on visible premises/framework or already-green frozen nodes. It cannot
       introduce another new helper, mutate an existing statement, or trigger
       recursive replanning. Per-contract leaf and repair budgets bound growth.
    8. **Separate planning context from proving context.** The session planner
       sees the whole theory DAG, fixed contracts, framework inventory, and legal
       premise summaries. A lemma prover receives one target, its transitive
       frozen dependencies, exact premise records, relevant residuals, and green
       predecessors. Repo-level planning does not imply repo-sized proof prompts.

    Planning quality must be measured explicitly:

    - initial-helper recall: fraction of eventually required residual leaves
      already present in the frozen graph;
    - unplanned residual leaves per contract and operation family;
    - graph churn after freeze (target zero except bounded leaf additions);
    - declared-edge versus accepted-proof citation closure;
    - legal-premise closure and undefined-fact count;
    - prompt size per proof node;
    - monotone green-frontier progress and checks per contract cluster.

    For ArchAcc_R, the nine verified supports are evidence for replanning, not
    evidence that the original lemma plan succeeded. Before treating the
    remaining 18 contracts as a production repo-level run, create a fresh
    certified lemma-DAG generation that preserves the six green contract proofs,
    preplans family-specific helper statements for the remaining PDE/PTE,
    set/store, lookup, mapping, and ASID families, typechecks those statements,
    and freezes the graph. Unforeseen Isabelle residuals may still add leaves,
    but they should no longer be the primary planning mechanism.


## Frozen statement-DAG implementation (entry 51)

51. **ArchAcc_R now starts a fresh, generation-scoped planning lifecycle rather
    than extending the under-planned schema-1 graph.** The production driver
    runs four gates before proving:

    1. `00-family-analysis` accounts for all 24 fixed contracts exactly once,
       groups operation families, and records obligation dimensions, legal
       interface IDs, predicted helper requirements, rationales, and
       uncertainties.
    2. `01-lemma-dag` creates exact helper statements, ownership, family and
       obligation labels, topological support order, consumers, and explicit
       dependencies. `02-plan-critique` adversarially revises the complete graph
       even if the first draft passes.
    3. A complete Isabelle statement shell typechecks every helper and fixed
       contract before proof generation. Up to two statement-only repair calls
       are recorded separately. The accepted graph is canonicalized and frozen
       with a SHA-256 before the first proof call.
    4. Proofs run in frozen support order and contract order. Generated helper
       and contract citations in each accepted proof body must be declared DAG
       dependencies; unknown or hidden generated citations fail before Isabelle.

    `--fresh-proofs` disables direct activation of the previous six green
    contracts and their helpers while retaining their hash-bound residual
    evidence for planning/proving. This causes every new graph node to receive a
    new audited prover call. Runtime growth remains possible only as one
    append-only residual leaf per contract. Such a leaf must depend solely on
    legal premise IDs or already-green nodes, cannot recursively grow, and is
    recorded in `runtime-leaf-extensions.jsonl` against the frozen graph hash.

    Planning and checking artifacts are now generation-scoped. A generation
    directory contains the six-part transcript for every family, DAG, critique,
    statement-repair, proof, and proof-repair call, plus parsed plans, frozen
    graph, statement shells, candidate theories, Isabelle logs, and the
    hash-chained event ledger. The recorded "thought process" is therefore the
    model's explicit structured rationales/uncertainties, exact prompts and
    outputs, and deterministic/Isabelle residuals. Provider-hidden internal
    chain-of-thought is not exposed by the API and is not claimed as evidence.

    The real pre-call context contains 24 contracts, four framework records, 78
    frozen legal premises, 40 generated-ancestor premises, and 13 verified
    residual records. The family prompt is 77,880 characters and the largest
    proof projection is 95,871, both below the 120,000-character gate. Schema-2
    family coverage, support topology, consumer closure, and proof citation
    closure are covered by the regen unit suite (31 tests).


    Evidence: `regen/archacc_milestone.py`,
    `regen/run_theory_generation.sh`,
    `regen/scripts/test_archacc_planner.py`, and
    `regen/scripts/test_theory_trace.py`.


## Recorded predecessor-edge closure (entry 52)

52. **A resumed frozen graph may add a missing citation edge only when an
    audited candidate proof demonstrates it and the target is already visible
    in topological order.** Generation
    `20260723T090932Z-92a8df5c` reached
    `regen_master_pte_dom` after proving `regen_master_pte_addr`, but the
    candidate proof cited both that earlier support and the legal ancestor
    premise `pte_relation`. The original recorded-candidate closure handled
    catalog premises only, so it could not repair the missing support edge and
    aborted before Isabelle.

    The closure now admits exactly three classes: legal inventory premise IDs,
    earlier support nodes, and, for a contract, earlier contract nodes. Forward
    support or contract citations remain forbidden. Every accepted completion
    records the source `.final.txt`, rewrites the canonical graph projection,
    advances its SHA-256, and emits both
    `recorded-candidate-premise-closure` and `frozen-graph-superseded` events.
    New candidates run this closure immediately before citation validation and
    Isabelle; the resume-time scan remains a crash-recovery fallback. It adds no
    declaration and changes no
    theorem statement; it makes a previously implicit legal dependency
    explicit and auditable.

    Regression coverage verifies that an earlier support and legal framework
    premise are admitted while a forward support citation is rejected.

    Evidence: `regen/archacc_milestone.py` and
    `regen/scripts/test_archacc_planner.py`.


## Contract-equivalent support rejection (entry 53)

53. **A generated support is invalid when its proposition merely restates a
    fixed contract.** ArchAcc_R support 18 exhausted six repairs because
    `regen_setObject_PD_update_obligation` and `setObject_PD_corres` had the
    same proposition modulo whitespace. The model correctly observed in the
    audited `0221-repair-support-18-1` response that the remaining goal was the
    contract itself. A normalized scan found ten such pseudo-helpers in the
    frozen plan, so this was a planner defect rather than an isolated hard
    lemma.

    Plan validation now rejects support/fixed-contract proposition collisions.
    Residual-history requirements that themselves duplicate a fixed contract do
    not force the duplicate support to survive. The validator residual is sent
    through the normal audited `02-plan-critique-repair` call, and a successful
    repair explicitly supersedes the old certified plan and reruns the complete
    Isabelle statement shell. Resume restores green proofs only for node IDs
    retained by the revised graph; removed or replaced nodes cannot leak through
    the attempt ledger.

    This preserves the distinction between planning nodes and goals: a support
    must expose a smaller reusable obligation, while each fixed corres remains a
    contract node that must independently become green.

    Evidence: generation `20260723T090932Z-92a8df5c` events and transcripts,
    `regen/archacc_milestone.py`, and `regen/scripts/test_archacc_planner.py`.


## Residual-triggered premise retrieval (entry 54)

54. **A semantic residual may expose a premise-retrieval miss without requiring
    a new helper.** The primed PDE getObject contract repeatedly left an invalid
    side goal asking `pspace_aligned`/`pspace_distinct` alone to imply
    `pde_at`. The accessible parent catalog contained
    `stronger_corres_guard_imp`, whose guard implications are evaluated with
    both states and `state_relation`, but the initial BM25/exact projection had
    sent only `corres_guard_imp`.

    `stronger_corres_guard_imp` is now an exact required retrieval. A bounded
    residual trigger checks the latest failed contract attempt and exact
    Isabelle diagnostic patterns before adding its unique catalog ID to the
    contract dependency set. The selected premise, trigger log and SHA-256 are
    stored in `residual-premise-edge-completion.json`; the event ledger records
    `residual-premise-retrieval`, and normal graph hashing records the amended
    frozen DAG. The next proof prompt can therefore see the theorem through its
    declared dependency rather than through an unscoped repository search.

    This is an edge/retrieval repair, not graph expansion: it introduces no
    declaration, keeps the fixed contract unchanged, and remains limited to a
    unique visible ancestor premise backed by an Isabelle residual.

    Evidence: `regen/archacc_milestone.py` and generation
    `20260723T090932Z-92a8df5c` contract-6 attempts.


## Canonical declaration identity (entry 55)

55. **Premise citation identity must canonicalize duplicate inventory
    projections.** Contract 9 used the visible abbreviation `corres`, but the
    inventory exposed the identical declaration once as fixed framework and
    once as a generated ancestor premise. Name-only resolution therefore
    produced two IDs and rejected a legal repair before Isabelle.

    Fact indexing now deduplicates records by candidate name plus normalized
    declaration statement, preserving source precedence: fixed framework,
    frozen legal premise, then generated ancestor. The ordinary candidate-edge
    closure can consequently add the unique canonical framework ID while truly
    different same-name declarations remain ambiguous and rejected.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py`, and generation
    `20260723T090932Z-92a8df5c` event 362.


## Planned-helper reuse in repair (entry 56)

56. **A contract repair that proposes an already-planned helper is a reuse
    request, not a runtime graph extension.** Contract 9 returned a
    `local_helper` named `regen_get_pte_from_pt`, identical to an existing green
    support, together with a new `contract_proof`. The old lifecycle rejected
    this as a duplicate node and terminated.

    When name and normalized proposition both match a planned green support,
    the harness now keeps the graph node unchanged, closes citations only for
    the actually adopted `contract_proof`, records `planned-helper-reuse`, and
    sends that contract proof to Isabelle. A same-name/different-statement
    response remains an error. Actual new helpers still follow the one-leaf,
    nonrecursive runtime-extension policy.

    Evidence: `regen/archacc_milestone.py` and generation
    `20260723T090932Z-92a8df5c` events 379-380.


## Verified structural sibling evidence (entry 57)

57. **Proof-strategy transfer is distinct from a theorem dependency.** The
    generic token-overlap template selector projected no accepted PDE proof for
    `getObject_PTE_corres`, so repeated Codex repairs saw only the residual and
    converged on short simplification attempts.

    `structural_sibling_proof_templates` now recognizes audited PDE/PTE and
    PD/PT theorem-name peers and projects only machine-green proofs into the
    prompt as `verified_structural_strategy_only`. The projection explicitly
    forbids implicit sibling-theorem citation. Every named fact in the adapted
    candidate remains subject to the normal frozen-DAG premise closure and
    citation validator, so this supplies a proof pattern without widening the
    logical context or hiding a graph edge.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py`, and generation
    `20260723T090932Z-92a8df5c` contract 9 event 422.


## Proof-search timeout feedback (entry 58)

58. **A proof-check timeout is a distinct residual class and must survive
    resume.** The first structural transfer for `get_master_pte_corres` reached
    PTE constructor obligations, but two broad `case_tac`/`clarsimp` repairs
    each consumed 600 seconds and produced empty semantic logs.

    `check_source` now classifies return code 124 as a proof-search performance
    failure, asks for explicit bounded branches instead of a larger simp set,
    and limits each proof check to 180 seconds. The attempt ledger projects the
    latest timeout wall time and failed proof into the next resume prompt, so a
    fresh Codex call cannot silently restart the same expensive strategy.

    This does not relax correctness or consume a theorem-level helper: every
    replacement still passes the same Isabelle and citation gates. It bounds
    repo-level pipeline latency and turns a previously empty timeout into
    actionable, audited model context.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py`, and generation
    `20260723T090932Z-92a8df5c` contract 11 events 434-437.


## Timeout localization and containment (entry 59)

59. **A wall-clock timeout must identify both the expensive proof command and
    every process that consumed the budget.** The initial
    `get_master_pte_corres` failures exposed two independent harness defects.
    First, GNU `timeout` stopped the container command but left descendant
    Poly/ML processes alive, so later attempts inherited sustained CPU and
    memory pressure. `container_run` now terminates and then kills only
    processes matching the exact timed-out theory target. Second, a whole-proof
    timeout had no command-level attribution, so repairs could repeat the same
    search under slightly different syntax.

    The harness now generates bounded proof-prefix probes and records their
    sources, logs, hashes, verdicts, and wall times under the same generation
    ID. For `20260723T090932Z-92a8df5c`, the prefix before the final PTE cases
    passed in 43.778 seconds, the prefix entering the first outer PTE branch
    timed out at 120 seconds, and the prefix immediately before its inner
    simplification passed in 43.596 seconds. This localizes the blow-up to the
    broad simplifier call that simultaneously unfolds `vs_ptr_align_def`,
    `pte_relation_aligned_def`, `pte_align'_def`, `return_def`, `pt_bits_def`,
    and `pageBits_def` across nested PTE constructor cases.

    Probe evidence is projected into the matching proof prompt only. A static
    strategy gate rejects candidates that reuse that exact broad simplifier
    bundle before starting Isabelle, records a `probe-strategy-rejection`
    event, and supplies the rejection as repair feedback. Replacing the broad
    search with bounded `simp only` changed the opaque timeout into ten concrete
    residual subgoals in 67.301 seconds (event 483). The following model repair
    failed in 69.626 seconds with an untyped `aa` variable (event 485), which is
    a normal proof-repair error rather than a timeout. The original timeout
    cause is therefore localized; the theorem itself remains unfinished.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py`, generation
    `20260723T090932Z-92a8df5c` prefix probes and events 434-485.


## Semantic residual continuity (entry 60)

60. **A bounded repair must not erase the machine residual that it is meant to
    solve.** A prefix-strategy rejection previously replaced the latest
    Isabelle diagnostic with only the harness rejection message. The following
    repair therefore knew which search pattern was forbidden but no longer saw
    the concrete subgoals.

    `plan_and_prove` now appends a probe rejection to the prior diagnostic and
    skips the rejected candidate before citation validation, proof persistence,
    or Isabelle execution. On resume, `resume_failure_record` projects the
    latest failed proof, wall time, failure class, and a bounded 16000-character
    immutable Isabelle log excerpt for semantic failures as well as timeouts.

    In generation `20260723T090932Z-92a8df5c`, event 501 rejected repair 2 and
    started no Poly/ML process, confirming the control-flow gate. Events 497,
    499, 503, 516, and 520 returned ordinary semantic residuals in about 62
    seconds. Event 522 then localized a second performance failure: repair 3
    reapplied the same dangerous definition bundle through `fastforce simp:`,
    a method spelling that the first gate did not classify. The gate now covers
    that variant as well, and timeout cleanup left no descendant process.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py`, and generation
    `20260723T090932Z-92a8df5c` events 497-523.


## Monotone failed-proof frontiers (entry 61)

61. **A red lemma is a ranked machine-checked frontier, not a replaceable proof
    string.** In `get_master_pte_corres`, event 537 reduced the contract to three
    constructor-local subgoals. Later whole-proof repairs regressed to eight
    subgoals (event 539) and then timed out under broad `wpsimp`/`fastforce`
    search (event 541). Because `proofs.partial.json` retained only the latest
    candidate, resume discarded the strongest known red state even though the
    theorem had made substantial semantic progress. The timeout reflected an
    avoidable tactic search explosion, not evidence that the contract was an
    indivisible or inherently intractable lemma.

    The harness now extracts subgoal counts from complete immutable Isabelle
    logs, reconstructs the exact checked proof from each attempt source, and
    persists `best-failed-frontiers.json`. Ranking is green, then bounded
    semantic failures by subgoal count, then semantic failures without a count,
    then timeouts; equally good historical states prefer the latest attempt. On
    resume the best proof is activated directly. During repair, a lower-count
    residual promotes the frontier, while a larger residual or timeout records
    `repair-regression-restored` and restores the prior proof and diagnostic.

    Repair prompts now name the best machine-checked frontier and require
    branch-local work once three or fewer constructor goals remain. Consecutive
    strategy rejections are reconstructed from the event ledger; after two, the
    state machine requires `action=local_helper` and rejects another whole-proof
    response before premise closure or Isabelle. One
    residual-derived nonrecursive leaf helper remains available for a genuinely
    shared local boundary. The prefix-probe strategy gate also scans multiline
    proof blocks and rejects reduced `wpsimp`/`fastforce` variants that combine
    `pte_relation_aligned_def`, `pte_align'_def`, and `vs_ptr_align_def`. The
    contract repair budget is five bounded attempts, but regressions cannot
    overwrite the best state.

    Real-ledger reconstruction selects event 537 (three subgoals) rather than
    events 539 or 541. Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py`, skill protocol step 7, and generation
    `20260723T090932Z-92a8df5c` events 535-542.


## Two-stage runtime leaf lifecycle (entry 62)

62. **A runtime leaf must become proof-green before it becomes graph state.**
    Forced residual-leaf mode produced a useful helper at event 595, but the old
    lifecycle appended it to the in-memory DAG before checking its statement.
    Isabelle then reported a type clash in the helper declaration at event 597.
    The best-frontier rollback restored the contract proof but left the red
    helper resident, so the corrected event-599 proposal was misclassified as a
    forbidden second helper and terminated the run at event 602.

    Runtime leaf handling is now two-stage. A proposal is added only to a
    temporary plan, checked for graph/citation legality, and compiled in a
    helper-only prefix that excludes the target contract. A red helper is
    removed from the temporary plan and its exact Isabelle diagnostic plus model
    response are fed to the next forced `local_helper` repair. Only a green
    helper is appended to the certified DAG, assigned a new graph hash, persisted
    with its proof, and recorded as `runtime-leaf-extension`. Subsequent repairs
    enter green-leaf consumer mode and may return `action=proof` only.

    Resume also rolls back any legacy provisional runtime node lacking a green
    helper attempt, recording `provisional-runtime-leaf-rollback` with old/new
    graph hashes. A rejected regressing contract candidate no longer erases its
    diagnostic when the older best proof is restored. This preserves both
    monotone contract progress and the one-nonrecursive-leaf graph bound.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py`, skill protocol step 6, and generation
    `20260723T090932Z-92a8df5c` events 595-602.


## Consumer-integrated runtime leaf revision (entry 63)

63. **A helper-green leaf is not yet a usable interface.** The first revised
    `regen_master_pte_branch_residual` became independently Isabelle-green at
    event 673 and was committed in place at event 674. Its consumer then
    improved from three subgoals to one at events 675-676, but the remaining
    failure occurred at the `rule_tac ... in regen_master_pte_branch_residual`
    command itself. The helper conclusion and concrete consumer residual used
    different syntactic abstraction boundaries: packaged `return` and
    `state_relation` forms versus expanded state-function and relation
    conjuncts, with already-normalized index expressions. Thus helper proof
    search was finished, while helper application could not even instantiate
    the rule.

    Two same-generation Isabelle probes confirmed the diagnosis. Simplifying
    the consumer with `simp only: return_def` still failed at the helper rule;
    citing `regen_master_pte_branch_residual[unfolded return_def]` also failed.
    This rules out a one-definition consumer normalization fix and requires an
    in-place statement revision against the exact residual. The probes are
    diagnostic artifacts only and do not enter the certified DAG.

    Plateau counting now follows the stable best failed frontier across rejected
    larger-subgoal candidates and inserted helper-only checks. A one-subgoal
    plateau can trigger same-name leaf revision only when the immutable Isabelle
    diagnostic identifies a failed proof-method line and the corresponding
    checked source command directly names the current runtime helper. The model
    must return the same helper name, a revised exact statement, a complete
    helper proof, and a complete consumer proof. The existing two-stage gate
    then checks the helper independently before testing the consumer; failure
    restores the prior green helper and success updates the same graph node and
    graph hash. No second leaf or recursive graph growth is permitted.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py`, skill protocol step 6, generation
    `20260723T090932Z-92a8df5c` events 669-708, and probes
    `ArchAcc_R_interface_probe` / `ArchAcc_R_interface_probe_unfolded`.


## Stable repair identity and pre-closure validation (entry 64)

64. **Historical numeric repair positions must not be reinterpreted after DAG
    growth.** During the same `get_master_pte_corres` repair, events 727-740
    repeatedly added thirteen unrelated PDE/setObject premises to
    `regen_master_pte_branch_residual`. The citation scanner mapped an old
    `repair-support-N` transcript through the current `support_order`; appending
    the runtime leaf had made a formerly out-of-range position resolve to that
    new node. Each accepted same-name revision then replaced the polluted
    dependencies, so the bad positional alias reappeared on the next scan.

    Historical support-stage indices now resolve only through the immutable
    non-runtime support order. Runtime leaves are identified by stable node ID.
    Resume reconciles each runtime helper with the last `helper_green` or
    `helper_revision_green` entry in `runtime-leaf-extensions.jsonl`, recording
    `runtime-leaf-ledger-reconciliation`; this removes any candidate-only edge
    or statement drift before transcript closure and proof generation.

    Events 738-743 exposed a second ordering error. Two otherwise useful helper
    revisions supplied apply-style `contract_proof` values without final
    `done`. The old grouped rejection said only that proof bodies or dependencies
    were invalid, and candidate edge closure ran before this check. The harness
    now reports `proof` and `contract_proof` structure errors separately,
    explicitly names the missing apply terminator, includes the rejected JSON in
    the next prompt, and performs candidate citation closure only after action,
    statement, proof-body, and dependency-shape validation. A structurally red
    response cannot change the frozen graph.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py`, skill protocol step 6, and generation
    `20260723T090932Z-92a8df5c` events 727-744.


## Full-command failure localization (entry 65)

65. **A failed Isabelle command must be localized as one command, not one line.**
    The `get_master_pte_corres` helper application begins with a multiline
    `rule_tac P=...` and names `regen_master_pte_branch_residual` more than ten
    lines after the reported failure line. The first detector inspected a fixed
    ten-line window, missed the helper citation, and incorrectly selected
    green-leaf proof-only mode. Events 760-769 then produced five complete
    consumer proofs that changed selector or bit normalization only in tactics
    after the failing `rule_tac`; those tactics never executed, and every check
    returned the same one-subgoal rule-application failure in 71-75 seconds.

    The detector now reads up to the parenthesis-balanced end of the failed
    Isabelle command and finds the helper citation anywhere in that block. The
    repair prompt states that no tactic after a `Failed to apply proof method`
    command ran: matching normalization must precede that command, otherwise the
    same runtime leaf statement must be revised. A same-generation probe placed
    `simp only: prod.sel` before the rule and still failed with one subgoal,
    proving that selector normalization alone is insufficient and the helper
    abstraction boundary remains mismatched.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py`, skill protocol step 6, generation
    `20260723T090932Z-92a8df5c` events 759-770, and probe
    `ArchAcc_R_pre_rule_selector_probe`.


## Semantic frontier depth and complete-proof preservation (entry 66)

66. **Equal subgoal counts do not imply equal proof progress.** Event 797 made
    the revised `regen_master_pte_branch_residual` independently Isabelle-green,
    and event 798 committed that same runtime leaf. The associated model response
    supplied a syntactically complete `contract_proof`, but it contained only the
    final helper-application tail. Event 799 installed that tail as the whole
    theorem body and failed at its first command at the top-level corres goal. It
    still reported one subgoal, so the old `(failure class, subgoal count)` rank
    treated it as equal to the historical one-subgoal frontier reached after the
    terminating outer corres proof and nested PTE cases. Latest-equal selection
    therefore replaced a deep semantic frontier with a shallow one. This is a
    harness lifecycle regression, not evidence that the helper theorem is hard.

    Semantic frontiers now record `failure_progress`, the checked Isabelle failure
    line relative to the beginning of the exact target proof. Ranking is ordered by
    failure class, subgoal count, and descending proof progress before wall time.
    Resume reconstruction and live rollback use the same rank and persist the field
    in `best-failed-frontiers.json`. Against the immutable generation history, the
    corrected reconstruction selects event 767 with one subgoal and progress 64,
    beginning with `apply (rule corres_no_failI)`, instead of event 799 at the proof
    root.

    When the active semantic frontier has at most three goals, the repair prompt now
    requires a complete contract proof beginning with the exact current outer
    command. The acceptance gate applies to `action=proof` and to `contract_proof`
    supplied by runtime-helper creation, reuse, or same-name revision; a complete
    apply-style tail ending in `done` is still rejected as
    `contract-proof-prefix-rejection`. Finally, a green `runtime_leaf` or
    `runtime_leaf_revision` resets consumer plateau counting. Failures before a
    changed helper interface cannot force another immediate statement revision.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py`, skill protocol steps 6-7, and generation
    `20260723T090932Z-92a8df5c` events 767 and 797-800.


## Depth-qualified plateau and real leaf revision (entry 67)

67. **A plateau is a repetition of the best frontier, not merely the same goal
    count.** After entry 66 restored the progress-64 frontier, event 814 produced
    a complete consumer proof and event 815 advanced the failure to progress 65.
    The old plateau counter nevertheless included the earlier shallow event 799
    because it also reported one subgoal. Together with events 813 and 815 this
    prematurely forced event 817 into provisional-leaf revision mode.

    Event 817 retained the exact helper proposition and proof and changed only the
    consumer. The helper-only check passed at event 818, and event 819 recorded a
    new graph hash despite no semantic helper-interface change. Later consumer
    candidates at events 820 and 825 regressed to progress 63 and were correctly
    rolled back, but counting them toward a one-goal plateau forced another false
    revision at event 827. That response also used an undeclared dependency name
    and was rejected at event 828.

    Historical frontier reconstruction now backfills each failed attempt with its
    source-derived `failure_progress`; live checks persist the same field into
    `attempts.json`. Plateau counting accepts an equal-subgoal failure only when
    its proof depth is at least the active best-frontier depth, and still resets at
    the latest green runtime-leaf acceptance. A same-name helper revision must
    change the normalized proposition. If only `contract_proof` changes, the model
    must use `action=proof`; a no-op helper revision cannot mutate the certified
    graph or graph hash.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py`, skill protocol step 6, and generation
    `20260723T090932Z-92a8df5c` events 813-829.


## Proof-depth regression exhaustion (entry 68)

68. **Repeatedly falling behind the best prefix is stagnation evidence, but not a
    plateau point.** After the depth-qualified counter was installed, events 842
    and 823 supplied two progress-65 failures at the green helper application.
    Events 844, 847, and 850 then returned complete proof bodies but moved the
    helper call before verified normalization, failing at progress 63. The
    monotone frontier correctly restored progress 65 each time and excluded these
    candidates from plateau counting. However, proof-only mode had no bounded exit
    for repeated depth regressions, so the model repeated the same strategy.

    The harness now counts `trailing_contract_frontier_regressions` separately
    from the best-depth plateau, resetting both at the latest green helper check.
    When the active failure command directly applies the runtime helper, at least
    two failures have reached the best depth, and at least three later/equivalent
    proof-only candidates fail before that depth, consumer mode is exhausted.
    The next repair must revise the same helper proposition against the immutable
    best residual. This is not graph expansion: the one-leaf bound and helper-only
    gate remain in force.

    Revision mode now rejects a proposition identical after normalization. A model
    that changes only `contract_proof` must use `action=proof`; rationale-only or
    proof-only churn cannot produce a new graph hash. Against the current immutable
    history, reconstruction yields progress 65, two depth-qualified failures, and
    five depth regressions, so the next resume deterministically enters a real
    helper-interface revision rather than another known progress-63 consumer move.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py`, skill protocol step 6, and generation
    `20260723T090932Z-92a8df5c` events 842-852.


## Helper-version-scoped consumer frontiers (entry 69)

69. **Consumer proof depth is comparable only within one helper-interface
    version.** Event 870 verified the materially revised mixed-mask helper and
    event 871 committed its new proposition. Event 872 then checked the supplied
    consumer against that interface and failed at progress 65. The in-memory
    best frontier still pointed to event 842, however, whose proof applied the
    previous helper proposition. Event 874 tested another new-interface consumer
    at progress 63, and event 875 incorrectly restored the event-842 old-interface
    proof. Monotonicity was being enforced across incompatible proof programs.

    A green runtime helper creation or proposition revision now atomically removes
    the target contract from `best-failed-frontiers.json` and records
    `consumer-frontier-invalidated-by-helper-revision` before installing the new
    `contract_proof`. Its first red consumer check becomes the baseline frontier
    for that helper version, regardless of numeric depth relative to the previous
    version.

    Resume applies the same boundary historically. `best_failed_frontier` scans
    only the contract attempts after the latest passing `runtime_leaf` or
    `runtime_leaf_revision` attempt. In the current history this epoch contains
    event 872 at progress 65 and event 874 at progress 63, so event 872 is selected;
    event 842 is no longer eligible. Plateau and regression counters already use
    the same green-helper boundary, giving ranking and lifecycle decisions one
    consistent interface epoch.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py`, skill protocol steps 6-7, and generation
    `20260723T090932Z-92a8df5c` events 870-876.


## Rank-regression exhaustion before the first runtime leaf (entry 70)

70. **Repeated whole-proof rank regressions must trigger the bounded residual
    leaf even when no helper exists yet.** Contract 11, `get_master_pte_corres`,
    became green at event 902 and prefix 12 passed at event 906. Contract 13,
    `setObject_PD_corres`, established a one-subgoal frontier at proof progress 2
    in event 908. The five subsequent candidates at events 913, 916, 919, 922,
    and 925 all expanded that residual to three subgoals. Events 914, 917, 920,
    923, and 926 therefore restored the immutable event-908 frontier, and the
    bounded run stopped at event 927.

    The pre-leaf exit previously counted only explicit probe-strategy rejection.
    Semantically worse but schema-valid proof responses were rolled back without
    contributing to that counter, so the harness could spend every repair round
    repeating a losing whole-proof strategy. This was a lifecycle and ranking
    integration bug, not evidence that the contract theorem itself was too hard.

    `trailing_contract_frontier_regressions` now counts every failed candidate
    below the active semantic rank since the latest green helper boundary:
    timeout, a larger subgoal count, or the same subgoal count with shallower
    proof progress. Before a runtime helper exists, three such regressions force
    `action=local_helper`; the existing one-leaf, nonrecursive graph bound and
    helper-only Isabelle gate remain unchanged. With a runtime helper already
    present, the stricter entry-68 rule still requires two best-depth
    helper-application failures plus three regressions before revising that same
    proposition.

    Replaying the immutable contract-13 attempts yields a one-subgoal,
    progress-2 active frontier, five rank regressions, and
    `force_residual_leaf=true`. The next resume therefore asks for one exact
    residual helper instead of accepting a sixth three-subgoal replacement.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py`, skill protocol step 6, and generation
    `20260723T090932Z-92a8df5c` events 902, 906, and 908-927.


## Actionable runtime dependency diagnostics (entry 71)

71. **A dependency-closure rejection must identify the bad edge and the exact
    legal alternatives.** Entry 70 correctly forced residual-leaf mode for
    contract 13. Events 941, 943, 945, 947, and 949 all returned
    `action=local_helper`, but each repeated the invented ID
    `premise:cb5a7b60a694f5e1c99ac17da2` instead of the supplied
    `premise:cb5a7b60a694f5a4d914`. The harness rejected every candidate before
    Isabelle at events 942, 944, 946, 948, and 950, but returned only the opaque
    message that runtime helpers may depend on legal premises or green nodes.
    With no offending ID or correction inventory, all five model rounds made the
    same schema error and the bounded run ended at event 951.

    Runtime dependency rejection now reports the complete set of invalid IDs and
    the exact allowed IDs already declared on the frozen consumer node. The model
    is explicitly forbidden to reconstruct premise hashes. The closure rule is
    unchanged: runtime leaves may still depend only on legal imported premises or
    existing green nodes, and no rejected candidate is appended to the graph.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py`, skill protocol step 6, and generation
    `20260723T090932Z-92a8df5c` events 941-951.


## Residual retrieval and provisional-helper frontiers (entry 72)

72. **A runtime leaf needs both a visible premise expansion and its own monotone
    proof frontier.** Entry 71 made the dependency error actionable, and event
    965 consequently used the correct premise ID and reached Isabelle. Its helper
    failed with three schematic guard goals at event 966. Event 968 moved address
    normalization earlier and event 969 improved the helper to two concrete goals:
    base-address alignment and the remaining update correspondence. Events 972
    and 975 then regressed to three goals, but the harness treated every red
    provisional helper as disposable and fed only the latest failure to the next
    round. Event 977 also repeated the invented dependency ID; event 978 now
    reported that exact error and all nine frozen alternatives, and the bounded
    run ended at event 979 without changing the graph.

    The frozen contract DAG exposed only nine premises to its repair prompt and
    selected `setObject_other_corres` as the principal strategy. Runtime
    dependency validation, however, allowed every session-visible legal premise.
    This meant a runtime leaf was permitted to cite direct-simulation facts that
    the model could not see. All five helpers therefore stayed inside the same
    inadequate generic-rule strategy. Comparison with the human source confirms
    the semantic diagnosis--the original author explicitly notes that
    `setObject_other_corres` does not work for this PageDirectory slot update--but
    no human proof text is added to the model context.

    Forced residual-leaf mode now performs a second, audited premise retrieval
    over the hash-verified catalog after session visibility filtering. Exact
    foundational interfaces (`no_fail_pre`, `set_object`, `in_monad`, `a_type`,
    `exec_get`, `exec_gets`, `bind_assoc`, and `more_pd_inner_beauty`) are included
    first, followed by BM25 records ranked against the fixed contract, active
    proof, Isabelle residual, and any best helper residual. Retrieved records are
    added to the legal-ID set for that run and persisted with the query and catalog
    hashes. This is bounded context expansion, not cross-session graph growth.

    Red provisional helpers now have a separate persisted semantic frontier. The
    harness reconstructs the candidate response from the immutable LLM event
    immediately preceding each helper-only Isabelle event, ranks timeout/subgoal
    count/proof depth exactly as for contracts, and supplies the best statement,
    proof, response, and diagnostic to the next call. A worse helper is audited as
    `runtime-leaf-regression-restored`; a green helper closes this frontier before
    its consumer is checked. Replaying the current history selects event 969 with
    two subgoals and progress 3, sourced from LLM event 968, rather than the later
    three-goal candidates.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py`, skill protocol step 6, the original
    ARM `ArchAcc_R.thy` lines 683-706 used only for harness diagnosis, and
    generation `20260723T090932Z-92a8df5c` events 965-979.


## Principal-rule exhaustion and provisional edge rollback (entry 73)

73. **A ranked helper frontier also needs a strategy-diversity gate, and all of
    its citation edges are provisional.** Runtime retrieval at event 994 exposed
    33 hash-healthy, session-visible premises and event 995 attempted a more
    explicit repair. Its undeclared `is_aligned_neg_mask` citation was rejected
    before Isabelle at event 998 with the exact legal ID. Events 999 and 1003 then
    returned near-identical `setObject_other_corres` helpers; events 1000 and 1004
    each produced three schematic guard goals, and events 1001 and 1005 correctly
    restored the two-goal event-969 frontier. Event 1007 added alignment assumptions
    but retained the same principal rule, so event 1008 again produced three goals
    and event 1009 restored the best helper. Event 1011 repeated both the strategy
    and the malformed dependency ID, and the bounded run ended at event 1013.

    Merely showing the best frontier did not force a semantic pivot. The harness
    now extracts rule/erule/drule/frule targets from every machine-checked red
    provisional-helper response. When the same non-wrapper principal rule appears
    in at least three failures, it is marked exhausted. The repair prompt requires
    a direct `corres_no_failI`/`no_fail_pre` state-relation decomposition using the
    residual-retrieved premises, and another citation of the exhausted rule is
    rejected before Isabelle as `runtime-helper-principal-rule-rejection`. Replaying
    the immutable history identifies exactly `setObject_other_corres`.

    Event 996 also exposed a transaction-ordering bug. Candidate citation closure
    ran before helper validation; because runtime retrieval had made new premise IDs
    visible, it added 21 edges across 11 existing nodes and event 997 superseded the
    graph even though event 998 rejected the helper. Local-helper creation and
    revision no longer run automatic transcript-wide edge closure before their
    helper-only gate. Their declared dependency list must be complete, and graph
    mutation occurs only after a green helper. The canonical plan and frozen graph
    were restored from immutable events 985 and 987, and event 1014 records the
    rollback from hash `13d8c936...` to `ca8756bf...`.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py`, skill protocol step 6, and generation
    `20260723T090932Z-92a8df5c` events 994-1014.


## Direct-simulation residual convergence (entry 74)

74. **A strategy pivot is successful when it exposes smaller semantic
    obligations, even before the helper is green.** After the canonical graph
    rollback, event 1029 retrieved 33 hash-healthy, session-visible premises.
    The principal-rule gate then prevented another
    `setObject_other_corres` attempt and required direct
    `corres_no_failI`/`no_fail_pre` simulation. Events 1031, 1033, and 1039
    rejected missing or malformed premise IDs before Isabelle, while event 1035
    exposed an undefined bare definition fact. The corrected event-1040 response
    reached Isabelle at event 1041 without the exhausted generic rule.

    Event 1041 leaves two concrete goals: derive the `objBits` object predicate
    required by `setObject` from `pde_at'`, then preserve `state_relation` across
    the abstract and concrete heap updates. Its two-goal, progress-7 helper
    frontier supersedes the earlier event-969 two-goal, progress-3 frontier and
    is persisted by event 1042. This is not evidence of an undifferentiated
    theorem-search timeout: the harness has isolated the remaining direct
    simulation boundary and can resume from it without expanding the graph.

    Runtime dependency diagnostics now list both the frozen consumer dependencies
    and the residual-retrieved exact IDs legal in the current round. This closes
    the final feedback gap seen in events 1031, 1033, and 1039 without weakening
    dependency validation or making rejected candidate edges durable.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py`, skill protocol step 6, and generation
    `20260723T090932Z-92a8df5c` events 1029-1044.


## Definition-fact firewall and verified-prefix repair (entry 75)

75. **A retained helper frontier must preserve its executed prefix, not merely
    its subgoal count.** The resumed run reconstructed event 1041 at event 1056
    and retrieved the same 33 legal premises at event 1059. Event 1062 repaired
    the missing `corres_no_failI` dependency, but invented `obj_at'_def`;
    Isabelle rejected it before semantic proof execution at event 1063. Events
    1067 and 1071 removed that name but replaced the progress-7 direct-simulation
    proof with a different progress-4 `corres_underlyingI` skeleton. Both had two
    goals, and the frontier rank correctly restored event 1041, but each consumed
    a full Isabelle check despite changing commands that had already succeeded.

    Proof citation validation now rejects any `_def` fact absent from the current
    payload/plan fact inventory. For a failed helper, `failure_progress` identifies
    the first rejected proof command; every earlier line is persisted as the
    `machine_verified_proof_prefix`. The prompt exposes that exact prefix, and an
    equal-frontier helper must preserve it byte-for-byte before Isabelle. Repairs
    therefore operate at the concrete failed command instead of restarting the
    proof skeleton.

    Event 1075 independently validates entry 74: its malformed dependency error
    listed the bad ID and all exact frozen plus residual-retrieved IDs legal in
    the round. None of events 1058-1076 changed the canonical graph hash
    `ca8756bf...` or promoted a red helper.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py`, skill protocol step 6, and generation
    `20260723T090932Z-92a8df5c` events 1056-1076.


## Session-scoped fact-name health (entry 76)

76. **Fact existence and DAG authorization require different scopes.** The first
    entry-75 resume stopped at event 1086 before any model call because historical
    machine-green support proofs cite legal definitions that were not selected for
    the current contract prompt. Checking `_def` existence against that narrow
    retrieval subset therefore produced false positives.

    The existence gate now uses the complete hash-healthy and visibility-filtered
    session name inventory: accessible premise catalog records, generated ancestor
    exports, and framework declarations. Its hash is recorded in `catalog_health`,
    while `proof_payload` deliberately omits the name list so LLM context does not
    grow. DAG authorization remains scoped to declared dependency IDs. Thus a real
    but undeclared fact still fails dependency closure, and the absent
    `obj_at'_def` still fails the existence gate. Events 1086 and 1096 also
    showed that imported baseline facts need not be catalog declarations. Resume
    therefore skips only fact-existence rechecking for immutable machine-green
    proofs; it still revalidates their DAG closure, while every unverified model
    candidate uses the existence gate by default.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py`, skill protocol step 6, and generation
    `20260723T090932Z-92a8df5c` events 1086 and 1096.


## Verified-prefix and strategy-exhaustion consistency (entry 77)

77. **A rule already executed in the retained prefix cannot also be prohibited.**
    The first prefix-locked run reached contract 13, but events 1116-1120 rejected
    every candidate because historical repetition marked `corres_no_failI` and
    `no_fail_pre` exhausted while the exact six-line prefix required both. This
    was a deterministic harness contradiction, so the run was stopped rather than
    consuming the remaining bounded calls.

    Principal-rule exhaustion now subtracts rule targets extracted from the
    `machine_verified_proof_prefix`. The exclusion is evidence-derived, not a
    hand-written wrapper list: `corres_no_failI` and `no_fail_pre` are protected
    because Isabelle executed them before the failing command, while rules used
    only at or after the red frontier remain eligible for exhaustion.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py`, skill protocol step 6, and generation
    `20260723T090932Z-92a8df5c` events 1116-1120.


## Generated-premise residual retrieval (entry 78)

78. **A visible generated export is not useful unless residual retrieval can add
    it to the repair context.** The consistent prefix-locked run reached Isabelle
    at event 1142, but retained the same two goals. The first goal explicitly asks
    for an `obj_at'`/`objBits` fact. The generated ancestor inventory already
    contains exact export `generated-premise:Refine.Invariants_H:regen_obj_at'_def:983efc1b2bdc`,
    but runtime retrieval expanded only the ordinary premise catalog. Contract
    scoping therefore removed this generated record from the prompt even though it
    was session-visible and healthy.

    Runtime residual retrieval now covers both stores. Generated exports use exact
    priority for `regen_obj_at'_def`, objBits interfaces, and `typ_at'_def`,
    followed by BM25 ranking against the same machine residual. Ordinary and
    generated results are separately counted in the immutable artifact, merged
    into the runtime legal-ID cache, and shown to the model. This is context
    expansion only; the frozen DAG remains unchanged until a helper is green.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py`, skill protocol step 6, and generation
    `20260723T090932Z-92a8df5c` events 1136-1144.


## Residual-shape exact interface closure (entry 79)

79. **BM25 over generated exports still needs exact interfaces for the residual
    shape.** Event 1161 successfully exposed 20 generated records and enabled the
    progress-8 frontier at event 1168. However it omitted the projectKO and
    lookupAround2/ps_clear bridge lemmas needed by the newly concrete goal. The
    final model round guessed nonexistent `ko_wp_at'_weakenE`; event 1175 failed
    before semantic progress and event 1176 restored the progress-8 frontier.

    Exact residual retrieval now also includes `regen_projectKOs`,
    `regen_lookupAround2_known1`, `regen_ps_clear_lookupAround2`, `ko_wp_at'`,
    `opt_map`, projectKO option facts, `project_koType`, and `archObjSize`. These
    names follow directly from the machine residual and existing verified
    generated interfaces. The prior prompt was 100,170 bytes, leaving sufficient
    room below the 120,000-byte context limit for this bounded closure.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py`, skill protocol step 6, and generation
    `20260723T090932Z-92a8df5c` events 1161-1178.


## Bounded repair history and object-witness ordering (entry 80)

80. **The immutable audit history and the live repair context need different
    retention policies.** Events 1193-1205 confirmed that exact residual retrieval
    was healthy: 34 catalog records and 22 generated ancestor exports included
    `regen_projectKOs`, `regen_lookupAround2_known1`, and
    `regen_ps_clear_lookupAround2`. Candidate 1198 reached Isabelle, but introduced
    `exI` while the concrete object was still hidden under `ko_wp_at'`; this
    split one witness obligation into four schematic goals. Candidate 1202 restored
    the two-goal frontier, after which event 1206 failed before the next model call
    because complete prior Isabelle diagnostics and rejected responses had been
    recursively appended until the prompt exceeded 120,000 bytes.

    Repair prompts now receive at most 20,000 characters of the newest diagnostic
    evidence. Frontier restoration and candidate rejection compact their live
    diagnostic state at the same boundary; complete prompts, responses, sources,
    logs, and rejection events remain unchanged in the hash-chained generation
    ledger. A residual-shape detector also adds an ordering constraint when
    `ko_wp_at'`, `ksPSpace`, and `projectKO` coexist: normalize the object
    predicate and obtain the concrete object before `exI`, then use the exact
    projection, object-size, lookupAround2, and ps_clear interfaces. This is a
    strategy-order correction derived from the machine residual, not a new theorem
    oracle.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py`, skill protocol step 6, and generation
    `20260723T090932Z-92a8df5c` events 1193-1206.


## Deterministic runtime dependency-edge completion (entry 81)

81. **The proof text, not an LLM-copied hash array, is the authoritative source
    for candidate citations.** In the post-entry-80 resume, events 1223, 1225,
    1227, and 1233 rejected otherwise structured runtime-helper responses for a
    reconstructed premise hash or omitted edges for `corres_no_failI` and
    `projectKO_opts_defs`. Only event 1229 reached Isabelle. That candidate obeyed
    the new object-witness ordering and exposed a concrete `ko`, but ranked below
    the retained two-goal frontier with three residual goals; event 1230 restored
    the best frontier.

    Runtime helper validation now parses the helper proof before dependency
    closure. If a cited known fact intersects the current round legal-ID set in
    exactly one ID and no ID for that fact was declared, the harness adds that
    provisional edge and records a
    `runtime-helper-dependency-edge-completion` event. Unknown generated facts,
    ambiguous names, illegal IDs, duplicate IDs, and invented hashes still fail
    closed. The completion mutates neither the canonical graph nor the accepted
    proof library until the helper-only Isabelle check is green.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py`, skill protocol step 6, and generation
    `20260723T090932Z-92a8df5c` events 1221-1234.


## Parser-owned runtime edges and bounded witness closure (entry 82)

82. **Opaque dependency hashes should not be a model output responsibility.** In
    events 1251-1268, deterministic edge completion repeatedly recovered the
    exact IDs for `corres_no_failI`, `obj_at_def`, `projectKO_opts_defs`, and
    `exec_gets`, but model-reconstructed invalid IDs still correctly failed the
    legal-ID gate. Events 1258-1260 then localized an Isabelle rule-shape error:
    `project_koType` is an equivalence for simplification, not a destruction rule.
    Candidate 1263 replaced that error with broad `fastforce` over the object and
    state-relation residuals and timed out at the 180-second semantic-check bound.

    Runtime-helper prompts now require `dependencies: []`; the audited parser owns
    construction of every uniquely resolved provisional citation edge. The object
    residual hint now supplies the bounded verified sibling order: unfold
    `ko_wp_at'`, obtain `ko`, discharge lookupAround2/ps_clear, create a typed
    `ARM_H.pde` projection witness, solve its existence with
    `simp add: project_koType`, then simplify projection and object size. It
    explicitly prohibits `fastforce` on this residual. This separates metadata
    normalization from semantic proof search and aims first to close the object
    branch, reducing the frontier from two goals to one before the state update
    simulation is repaired.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py`, skill protocol step 6, and generation
    `20260723T090932Z-92a8df5c` events 1249-1269.


## Definition-closed constant-size residuals (entry 83)

83. **A small semantic residual can still be a premise-selection failure rather
    than a hard theorem.** Events 1285-1298 moved the provisional
    `setObject_PD_corres` leaf from two subgoals at proof progress 8 to two at
    progress 13. The first remaining goal was exactly
    `archObjSize (KOPDE x3) = archObjSize (KOPDE pde')`. Events 1302-1309
    split the enclosed PDE values and expanded that constant equality to 16
    branches; frontier ranking correctly restored the two-goal progress-13
    candidate before event 1310 ended the bounded run.

    The catalog already contained the needed declaration as
    `premise:f12240928b08d70c476e`, but its parser kind is `defs` and its sole
    candidate name is `archObjSize_def`. Runtime exact retrieval requested only
    `archObjSize`, so the record was absent from the 56-record residual artifact.
    Exact retrieval now includes the actual definition names
    `archObjSize_def`, `wordSizeCase_def`, `wordBits_def`, and `word_size_def`
    together with their base names. A dedicated residual hint preserves the
    machine-verified prefix, replaces the failing plain `apply simp` with the
    bounded `apply (simp add: archObjSize_def)`, and prohibits splitting `x3`
    or `pde'`. This closes premise availability before assigning semantic
    difficulty to the lemma.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py`, skill protocol step 6, and generation
    `20260723T090932Z-92a8df5c` events 1285-1310.


## Stage-keyed runtime frontier reconstruction (entry 84)

84. **An Isabelle source event is not necessarily adjacent to its generating LLM
    event.** Resume event 1322 reconstructed the provisional-helper frontier at
    two subgoals and proof progress 8 even though event 1298 had persisted a
    progress-13 frontier from source 1297. The reconstruction code assumed that
    a source at sequence N came from `N-1-repair-*.final.txt`; event 1296 was
    instead the audited dependency-edge completion between LLM response 1295
    and Isabelle source 1297, so the best candidate was silently omitted. The
    next bounded run consequently promoted a shallower progress-9 candidate and
    compared later attempts against the wrong baseline.

    Runtime response lookup now parses the stable contract index and repair round
    from the source stage, selects the nearest preceding matching LLM response,
    and retains the adjacent-event fallback for older generic audit fixtures. The
    same lookup drives both best-frontier reconstruction and repeated-principal-rule
    accounting. On the real event history it deterministically reconstructs source
    1297 from response 1295 at two subgoals and progress 13. Constant-size residual
    detection now also tolerates Isabelle `***` line prefixes by recognizing two
    `archObjSize (KOPDE` terms in the same diagnostic.

    The intervening run still provided positive semantic evidence: retrieval event
    1325 contained 59 records including `premise:f12240928b08d70c476e`; model
    responses 1331 onward cited that exact definition; candidate 1338 closed the
    object-size branch before exposing only the state-update simulation, although
    its broad lookupAround2 step regressed to three goals and was rolled back.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py` (75 tests), skill protocol step 6, and
    generation `20260723T090932Z-92a8df5c` events 1322-1351.


## Proof-phase-aware runtime retrieval (entry 85)

85. **A correct premise inventory can still become unavailable after node scoping,
    and a correct local rule can be wrong in the next proof phase.** Resume event
    1363 correctly reconstructed the two-subgoal, progress-13 provisional-helper
    frontier. Retrieval event 1366 supplied `archObjSize_def`, and all five model
    candidates closed the constant KOPDE size branch. Events 1369, 1374, 1379,
    1384, and 1389 then repeated `erule(1) regen_ps_clear_lookupAround2` on the
    remaining direct `state_relation` update simulation. That rule belongs to the
    preceding object-discovery/no-fail phase; here it introduced a schematic lookup
    address, expanded the frontier to three goals, and was rolled back each time.

    Two lifecycle defects kept the model on that path. First, runtime ordinary
    retrieval excluded every record already present in the full initial payload,
    even when contract dependency scoping had removed that record from the live
    repair prompt. Thus `setObject_def` was healthy and inventoried but invisible.
    Runtime generated retrieval was narrower still: it searched only the initial
    BM25-selected generated subset, so exported `regen_pspace_dom_update` and
    framework declarations `pspace_relation`, `ekheap_relation`, and
    `ghost_relation` could never be selected. Second, principal-rule extraction
    recognized `erule fact` but not Isabelle consumption-count syntax
    `erule(1) fact`, so the repeated regressing rule was not exhausted.

    Runtime retrieval now searches the full session-visible ordinary catalog and
    full generated-ancestor declaration store. Exact state-update interfaces are
    selected before a smaller BM25 tail; records already present in the full payload
    may still be returned to the scoped prompt, while payload mutation is ID-deduped.
    The real inventory check returns 61 records, close to the prior 59, and includes
    `setObject_def`, `updateObject_default_def`, `put_def`,
    `pspace_relation_def`, `ekheap_relation_def`, `ghost_relation_def`,
    `regen_pspace_dom_update`, and `regen_map_to_ctes_upd_other`. A proof-phase
    residual hint now forbids lookup/object-discovery rules after the update branch
    begins and directs bounded relation-conjunct preservation. Principal-rule parsing
    accepts consumption counts; replaying the real history marks
    `regen_ps_clear_lookupAround2` exhausted while protected machine-verified
    `corres_no_failI` and `no_fail_pre` remain legal.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py` (78 tests), skill protocol step 6, and
    generation `20260723T090932Z-92a8df5c` events 1363-1392.


## Alias-closed membership normalization (entry 86)

86. **The first direct-state repair exposed both a useful semantic frontier and
    another inventory representation gap.** Event 1407 proved that full-store
    retrieval worked: 64 records included the newly visible state-relation
    declarations and `regen_pspace_dom_update`. Candidate 1408 avoided the
    exhausted ps_clear eliminator but used broad `fastforce`; event 1410 timed out
    at 180 seconds and was rolled back. Candidate 1413 switched to bounded
    conjunct steps but was rejected before Isabelle because `split_def` was marked
    unknown. The fact is session-visible and already used by machine-green ancestor
    proofs, but the catalog represented it only on the right-hand side of
    `lemmas crunch_simps = split_def ...`, not as a candidate name.

    Candidate 1416 avoided that parser false negative and supplied positive semantic
    evidence: event 1418 reduced the provisional helper from two subgoals at proof
    progress 13 to one subgoal at progress 23. The remaining goal still contained
    concrete `alignCheck` and `magnitudeCheck` result memberships with unconstrained
    intermediate states. Applying `intro conjI` at that point failed because the
    outer shape remained an abstract-monad result existential, not a conjunction.
    Later candidates either retained that one-goal frontier or expanded it and were
    correctly rolled back; generation ended at event 1431 with the one-goal
    progress-23 frontier persisted.

    Runtime ordinary normalization now indexes right-hand fact names from visible
    `lemmas` declarations. Exact selection remains bounded to one preferred record
    per required name, preferring a direct declaration over an alias witness; the
    real retrieval is 63 records for the compact check rather than the unbounded 89
    produced by naive alias expansion. Generated exact retrieval now includes
    `regen_in_alignCheck` and `regen_in_magnitude_check`. The residual hint requires
    those facts before witness construction or relation splitting, so concrete
    intermediate states collapse first. A direct-state phase gate rejects broad
    `fastforce` before Isabelle and records a dedicated audit event.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py` (80 tests), skill protocol step 6, and
    generation `20260723T090932Z-92a8df5c` events 1404-1431.

## Conditional membership definition closure (entry 87)

87. **Making a conditional simp theorem visible is not enough; its side
    conditions must also be definition-closed.** Production retrieval event 1446
    supplied 65 bounded records, including `split_def`,
    `regen_in_alignCheck`, and `regen_in_magnitude_check`, and resume event 1443
    reconstructed the one-subgoal progress-23 frontier. Candidate 1452 used the
    two membership facts in the required order. Isabelle event 1454 moved the
    failure from proof progress 23 to 24 while retaining one subgoal: `alignCheck`
    and its intermediate state disappeared, proving the ordering correction
    useful.

    The remaining residual still contained
    `magnitudeCheck p x2 pdeBits b`. `regen_in_magnitude_check` requires
    `is_aligned p pdeBits`, `(1::word32) < 2 ^ pdeBits`, and a known object at
    `p`; the first and third assumptions were present, but the live prompt omitted
    `pdeBits_def`, so the power bound could not be discharged and repeated simp
    calls did not rewrite the membership. Later candidates retained the
    progress-24 frontier or regressed and were rolled back through event 1472.

    Runtime exact retrieval now includes `pdeBits_def`, `pageBits_def`, and
    `word_power_less_1`. The state-update hint requires establishing the pdeBits
    power bound first and using the existing `lookupAround2` pair equality to
    identify its second component with residual `x2`; only then may
    `regen_in_magnitude_check` eliminate the final concrete intermediate state.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py` (80 tests), skill protocol step 6, and
    generation `20260723T090932Z-92a8df5c` events 1443-1472.

## Resume-stable residual phase recovery (entry 88)

88. **A semantic proof phase must survive diagnostic bounding and resume.** Resume
    event 1484 reconstructed the one-subgoal progress-24 helper frontier, and
    retrieval event 1487 supplied 67 bounded records (41 ordinary and 26
    generated), including `pdeBits_def` and `word_power_less_1`. The prompt
    therefore had the required definition closure. It nevertheless omitted the
    direct-state phase hint: the 16,000-character bounded diagnostic retained
    `magnitudeCheck`, the explicit `kheap` update, `cdt_relation`, and
    `arch_state_relation`, but had lost the earlier literal `setObject` and
    `pspace_relation` markers required by the phase detector.

    This was a harness lifecycle failure, not a hard-theorem failure. Candidates
    1493 and 1498 began using `pdeBits_def`. Candidate 1508 combined
    `pdeBits_def`, `regen_in_magnitude_check`, and
    `regen_lookupAround2_known1` as bounded pair normalization; source lines
    425-426 passed. Isabelle then failed at the next command, the
    `state_relation` decomposition on line 427, and event 1511 promoted the same
    one-subgoal frontier from proof progress 24 to 26. Generation ended at event
    1513 with that successful normalization in the machine-verified prefix.

    Direct-state detection now accepts either the named transition shape or the
    expanded residual shape: `magnitudeCheck`, an explicit `kheap` update, and
    stable cdt/architecture relation conjuncts. The same predicate drives prompt
    hints and pre-Isabelle strategy checks. Only the object-discovery eliminator
    `regen_ps_clear_lookupAround2` is rejected in this phase;
    `regen_lookupAround2_known1` remains legal as bounded normalization of an
    already exposed lookup pair. Failure progress is also mapped from a reported
    physical source line back to the start of its possibly multiline Isabelle
    command, preventing a failed continuation from entering the protected
    prefix. Regression tests cover both bounded phase recovery and multiline
    command boundaries.
    Resume events 1514-1530 confirmed that prompt 1529 restored the direct-state
    hint and preserved the complete Codex prompt, stream, final response, stderr,
    metadata, and audit record. Review before Isabelle found the initially
    over-broad lookup gate; the run was deliberately stopped, with
    `KeyboardInterrupt` recorded at event 1531, before applying the narrowed rule.


    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py` (82 tests), skill protocol step 6, and
    generation `20260723T090932Z-92a8df5c` events 1473-1531.

## Shared prompt-validator phase source (entry 89)

89. **Restoring a phase only in the prompt is insufficient; validators must read
    the same persisted frontier.** Resume events 1532-1546 reconstructed the
    progress-26 helper and supplied its direct-state prompt. Candidate 1547
    preserved the verified membership-normalization prefix, avoided
    `regen_ps_clear_lookupAround2`, but proposed a broad `fastforce` for the
    remaining relation conjunction. The pre-Isabelle gate should have rejected
    it. Instead, its call used the shallow current contract-prefix diagnostic,
    while prompt construction used the persisted runtime-helper diagnostic. The
    former did not classify as direct-state, so the validator silently allowed
    the candidate.

    Isabelle event 1549 then consumed the full 180-second helper timeout without
    producing a semantic residual. Event 1550 restored the one-subgoal
    progress-26 frontier and event 1551 recorded the timeout repair diagnostic;
    the run was stopped at event 1552 before another external repair call. This
    reproduces a harness lifecycle state split, not theorem search complexity.

    `runtime_phase_strategy_error` now accepts a persisted frontier diagnostic
    and gives it precedence over the shallow contract diagnostic. The runtime
    repair call passes `failed_runtime_leaf_frontiers[node_id].diagnostic`, so
    prompt hints and pre-Isabelle rejection share one semantic phase source
    across resume. A dedicated regression supplies a shallow contract residual
    plus a direct-state helper residual and verifies that broad `fastforce` is
    rejected.

    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py` (83 tests), skill protocol step 6, and
    generation `20260723T090932Z-92a8df5c` events 1532-1552.

## Abstract-monad witness definition closure (entry 90)

90. **Relation decomposition cannot start while the abstract result witness is
    still hidden behind the monad semantics.** With the shared phase source fixed,
    resume events 1553-1567 reconstructed progress 26 and supplied the direct-state
    context. All five candidates 1568, 1573, 1578, 1583, and 1588 avoided broad
    `fastforce` and the object-discovery eliminator. Their Isabelle checks did not
    time out or regress, but every one failed at source line 427 with one subgoal
    and progress 26.

    The residual outer shape was still an existential over the result of abstract
    `set_object`; the later state-relation tactics therefore never ran. Candidates
    repeatedly cited `set_object_def`, `in_monad`, and the concrete update facts,
    but the runtime inventory omitted the definitions needed to reduce the
    abstract nondeterministic monad: `get_object_def`, `bind_def`, `gets_def`,
    `get_def`, `return_def`, `assert_def`, and `fail_def`. The plateau through
    generation-failed event 1593 is thus a bounded premise-closure failure, not
    evidence that an individual relation conjunct is too difficult.

    Runtime exact retrieval now includes that seven-name monad closure. A real
    healthy-catalog query returns all seven in 46 deduplicated ordinary records;
    the shared `lemmas` alias for `bind_def`, `assert_def`, and `fail_def` remains
    one record rather than inflating the prompt. The direct-state hint now fixes
    the semantic order: normalize concrete membership, expose the unique abstract
    state witness with the monad definitions, and only then unfold
    `state_relation_def` and split its conjuncts.

    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py` (83 tests), skill protocol step 6, and
    generation `20260723T090932Z-92a8df5c` events 1553-1593.

## Mandatory closure and incremental repair context (entry 91)

91. **A visible phase requirement must be enforced, and runtime context must be
    appended only once per payload extension.** Resume events 1594-1608 supplied
    the new abstract-monad definitions. Prompt 1609 was 97,827 bytes and remained
    below the 120,000-character harness limit, but the candidate claimed to unfold
    abstract `set_object` without citing any of `get_object_def`, `bind_def`,
    `gets_def`, `get_def`, `return_def`, `assert_def`, or `fail_def`. Isabelle
    event 1611 therefore failed at the unchanged line-427 outer existential and
    event 1612 retained the one-subgoal progress-26 frontier.

    Before round two, the first retrieval had already extended the scoped payload
    with its runtime ordinary and generated records. The harness rebuilt the base
    repair prompt from that extended payload and then appended the same runtime
    record list again. The duplicated block pushed the request over the context
    limit, causing generation-failed event 1614 before a second LLM call. This was
    context lifecycle duplication, not genuine growth in the proof obligation.

    The direct-state strategy gate now rejects a candidate that omits the exact
    seven-definition abstract-monad closure. Runtime repair assembly snapshots the
    legal premise IDs visible when the round base prompt is built, calls retrieval,
    and appends only records absent from that snapshot. The first round still gets
    newly retrieved records; later rounds use their single copy in the extended
    scoped payload. A regression covers ordinary and generated ID snapshots.

    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py` (84 tests), skill protocol step 6, and
    generation `20260723T090932Z-92a8df5c` events 1594-1614.


## Post-witness relation decomposition gate (entry 92)

92. **Exposing the abstract witness is necessary, but broad relation unfolding
    immediately afterward destroys the useful frontier.** On resume, candidate
    1630 again omitted the seven mandatory monad definitions and was rejected by
    the new phase gate at event 1631 without invoking Isabelle. Candidate 1633
    was produced successfully on the second repair round, confirming that
    incremental runtime-record assembly no longer exceeded the prompt limit. It
    included the complete monad closure and therefore exposed the abstract state
    witness.

    The next command combined `state_relation_def` and `pspace_relation_def` in
    one broad `clarsimp`. Isabelle event 1635 expanded the single semantic
    obligation into the full kernel-object constructor and global relation
    conjunction space; event 1636 measured 131 subgoals with failure progress
    zero and restored the saved one-subgoal, progress-26 frontier. A later
    candidate repeated the same decomposition pattern and event 1641 recorded
    the same 131-subgoal regression. The run was deliberately interrupted at
    event 1645 before spending another Isabelle check on that known-bad shape.

    The direct-state strategy gate now parses post-closure proof commands and
    rejects any single `clarsimp` that unfolds both relation definitions. The
    repair must keep the witness exposed while proving update-point and
    unchanged-point obligations in bounded steps, then discharge the remaining
    relation conjuncts. This is a semantic search-space guard: it does not claim
    the theorem is impossible, only that this decomposition order erases the
    strongest available structure before the local update has been isolated.

    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py` (84 tests), skill protocol step 6, and
    generation `20260723T090932Z-92a8df5c` events 1615-1645.

## Full-command phase-gate parsing (entry 93)

93. **A phase gate must classify the command containing the transition token,
    not only commands that start after it.** Resume events 1646-1665 rebuilt the
    progress-26 frontier. Candidate 1664 placed the mandatory monad closure and
    both `state_relation_def` and `pspace_relation_def` in the same `clarsimp`.
    The first post-witness gate sliced the proof at the final closure-token
    position and then searched for commands beginning in that suffix. Because
    the active command began before the slice, the gate missed it; Isabelle
    event 1666 ran and event 1667 reproduced the 131-subgoal, progress-zero
    regression. The run was stopped at event 1674.

    The parser now discovers complete `apply` command spans in the full proof and
    retains every command whose end lies beyond the last mandatory closure token.
    This includes the command containing the token as well as later commands.
    Regressions cover both same-command and next-command broad relation unfolding,
    while the bounded known-pair normalization remains accepted.

    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py` (84 tests), and generation
    `20260723T090932Z-92a8df5c` events 1646-1674.

## Expanded-relation residual phase (entry 94)

94. **A relation definition can already be expanded by the verified prefix even
    when witness construction is the most recently completed semantic phase.**
    Resume events 1675-1693 rebuilt the progress-26 frontier. Event 1694 then
    production-validated the full-command gate by rejecting a same-command broad
    relation unfolding before Isabelle. Candidate 1696 separated monad closure
    from relation work; Isabelle event 1698 advanced to one subgoal at progress
    28, and event 1699 promoted that frontier.

    The new residual directly contained `pspace_dom`, `cdt_list_relation`,
    `revokable_relation`, and domain-field conjunctions. Nevertheless, candidates
    1696, 1701, and 1706 all placed another `clarsimp simp:
    state_relation_def` at line 429. The verified prefix had already unfolded
    that definition, so the repeated command made no progress and Isabelle
    reported `Failed to apply proof method`. Events 1703 and 1708 reproduced the
    same progress-28 one-subgoal frontier; bounded repair ended at event 1711.

    The direct-state phase gate now recognizes this explicit conjunction shape
    from the persisted diagnostic and rejects any post-closure command that
    repeats `state_relation_def`. The prompt likewise branches on residual shape:
    an already expanded relation proceeds directly to bounded conjunct and local
    update steps; only a named relation target is unfolded. A regression locks
    this exact progress-28 case.

    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py` (84 tests), skill protocol step 6, and
    generation `20260723T090932Z-92a8df5c` events 1675-1711.

## One-conjunct-at-a-time relation repair (entry 95)

95. **Skipping a redundant definition unfold is not enough if the next tactic
    explodes the complete conjunction tree.** Resume event 1728
    production-validated the expanded-relation gate by rejecting a repeated
    `state_relation_def` before Isabelle. Candidate 1730 removed that no-op but
    immediately used `apply (intro conjI allI ballI impI)`. Isabelle event 1732
    reached its first local tactic with 27 open subgoals; event 1733 restored the
    retained progress-28, one-subgoal frontier. Candidate 1735 repeated the same
    tactic, event 1738 reproduced 27 subgoals, and the run was stopped at event
    1740 before further repetition.

    Expanded-relation validation now rejects a post-witness command that combines
    `intro` with `conjI`, `allI`, `ballI`, and `impI`. The repair prompt requires
    one `rule conjI` at a time, immediate closure of the current conjunct, and
    only then another split. It directs the first branch to bounded `pspace_dom`
    normalization with `regen_pspace_dom_update` and `dom_fun_upd2`, followed by
    update-point and unchanged-point object relations. This preserves the useful
    one-goal frontier while still allowing explicit semantic decomposition.

    Evidence: `regen/archacc_milestone.py`,
    `regen/scripts/test_archacc_planner.py` (85 tests), skill protocol step 6, and
    generation `20260723T090932Z-92a8df5c` events 1712-1740.

## Mandatory first-conjunct interface (entry 96)

96. **A bounded split still regresses if its first branch omits the exact local
    update interface.** Resume candidates 1756 and 1759 were rejected before
    Isabelle for missing monad closure and repeated relation unfolding. Candidate
    1762 then followed the one-conjunct structure, but its first branch used only
    `dom_fun_upd2`. Isabelle event 1764 left the `pspace_dom` equality open beside
    the unsplit remainder, and event 1765 measured two subgoals and restored the
    progress-28 one-subgoal frontier. Candidate 1767 repeated the same omission;
    the run was stopped at event 1769.

    The sealed ancestor theorem `regen_pspace_dom_update` states that replacing an
    existing heap object with one of the same `a_type` preserves `pspace_dom`.
    The current PageDirectory-to-PageDirectory update satisfies those premises;
    the existing `ksPSpace b p = Some ko` fact then normalizes the target insertion.
    Expanded-phase validation now locates the first post-witness `rule conjI` and
    requires `regen_pspace_dom_update` in that command or its immediate proof
    step. A regression rejects `dom_fun_upd2` alone and accepts the bounded exact
    interface shape.

    Evidence: generated `Refine.KHeap_R.regen_pspace_dom_update`,
    `regen/archacc_milestone.py`, `regen/scripts/test_archacc_planner.py` (85 tests),
    skill protocol step 6, and generation `20260723T090932Z-92a8df5c` events
    1741-1769.

## Phase-mandatory rules versus exhaustion (entry 97)

97. **Repeated model refusal can be caused by contradictory harness directives,
    not by missing premise visibility or theorem difficulty.** Resume events
    1786 and 1789 rejected stale relation and bulk-intro strategies. Events 1792,
    1795, and 1798 then rejected three candidates for omitting the mandatory
    `regen_pspace_dom_update`, and bounded repair ended at event 1800 without an
    Isabelle call after the prefix check.

    Audit review of prompts 1791, 1794, and 1797 found the conflict. The exact
    generated theorem record and the phase hint were present, but the later
    `MANDATORY PRINCIPAL-RULE PIVOT` listed `regen_pspace_dom_update` among rules
    that must not be cited anywhere. Historical failed helpers had used it at
    least three times, so generic principal-rule exhaustion overrode the new
    phase requirement. The model was obeying an incompatible hard constraint.

    `phase_mandatory_principal_rules` now derives protected rules from the
    persisted semantic diagnostic. In the expanded direct-state phase it protects
    `regen_pspace_dom_update`; this set is unioned with rules already present in
    the machine-verified prefix before exhaustion is computed. Prompt construction
    and final repeated-rule validation therefore agree with the phase gate, while
    unrelated repeatedly failing rules remain exhausted.

    Evidence: prompts and finals 1791/1794/1797,
    `regen/archacc_milestone.py`, `regen/scripts/test_archacc_planner.py` (86 tests),
    skill protocol step 6, and generation `20260723T090932Z-92a8df5c` events
    1770-1800.

## Conditional interface instantiation and mandatory split (entry 98)

98. **Making a phase rule visible is insufficient when a conditional rewrite is
    invoked as an undirected simp fact, and a validator must reject omission of
    the required proof structure rather than checking it only when present.**
    After the exhaustion conflict was fixed, event 1823 recorded the generated
    `regen_pspace_dom_update` dependency and candidates 1822/1827 cited it in the
    first `pspace_dom` branch. Isabelle events 1824 and 1829 nevertheless left
    two subgoals: the domain equality stayed open beside the unsplit relation
    remainder. The conditional theorem had not been instantiated by broad
    `clarsimp`. Candidate 1832 then removed `rule conjI` entirely; event 1834
    made no progress on the full conjunction and event 1835 retained the
    progress-28, one-subgoal frontier before bounded failure at event 1837.

    Expanded-state validation now requires the first post-witness `rule conjI`;
    omitting the split is rejected before Isabelle. Its immediate branch must use
    explicit `subst regen_pspace_dom_update`, not merely include the theorem in a
    simp set. The phase prompt explains the three bounded obligations: derive the
    old abstract heap lookup, prove PageDirectory-to-PageDirectory `a_type`
    equality, and use `ksPSpace b p = Some ko` to normalize the target `insert`.
    This separates premise instantiation from the later object-relation work and
    converts the observed semantic no-op into an executable harness invariant.

    Evidence: finals 1822/1827/1832, Isabelle events 1824/1829/1834,
    `regen/archacc_milestone.py`, `regen/scripts/test_archacc_planner.py`, skill
    protocol step 6, and generation `20260723T090932Z-92a8df5c` events 1801-1837.

## Structural phase rules versus exhaustion (entry 99)

99. **Exhaustion protection must cover mandatory structural tactics as well as
    named generated premises.** The corrected run resumed at event 1838 and the
    new first-split validator rejected rounds 1-3 at events 1854, 1857, and 1860
    without invoking Isabelle. Prompt 1859 then exposed a remaining contradiction:
    the phase required `rule conjI`, while `MANDATORY PRINCIPAL-RULE PIVOT` listed
    `conjI` among rules that must not be cited anywhere. The model consequently
    substituted `safe`. In round 4 it finally emitted the required structural
    rule, but event 1863 rejected that candidate through generic repeated-rule
    validation, and the bounded run ended at event 1865.

    `phase_mandatory_principal_rules` now protects both `conjI` and
    `regen_pspace_dom_update` for an expanded direct-state residual. Exhaustion
    prompt construction and final repeated-rule validation consume this same set,
    so neither can prohibit a tactic required by the phase validator. The rule is
    general: phase-local structural decomposition belongs to the protected
    interface just as much as a named ancestor theorem.

    Evidence: prompt 1859, finals 1853/1856/1859/1862, events 1838-1865,
    `regen/archacc_milestone.py`, `regen/scripts/test_archacc_planner.py`, and
    skill protocol step 6.

## Conditional rewrite variable instantiation (entry 100)

100. **An explicit conditional rewrite can still regress when a premise-only
     variable is not determined by the rewrite target.** After structural-rule
     protection, the run resumed at event 1866. The phase gate monotonically
     rejected bulk decomposition and simp-only domain updates at events 1882,
     1885, and 1888. Candidate 1890 then used the required first `conjI` and
     explicit `subst regen_pspace_dom_update`, but Isabelle event 1892 produced
     four subgoals. Candidate 1895 attempted separate premise commands and event
     1897 reproduced the same four-subgoal regression before bounded failure at
     event 1900.

     The complete log identifies the cause: `ps`, `ptr`, and the replacement
     object are fixed by the rewrite target, but theorem variable `x` occurs only
     in the assumptions. Isabelle therefore generated schematic `?x254`; the
     first premise became `ArchObj (PageDirectory pd) = ?x254`, so the following
     `a_type` step could not apply. Expanded-state validation now requires a
     `where x = ...` instantiation in the first substitution window. The prompt
     gives the current exact constructor, after which the old lookup, same-type
     equality, and `insert` normalization are three concrete bounded obligations.

     Evidence: finals 1890/1895, Isabelle events 1892/1897, events 1866-1900,
     `regen/archacc_milestone.py`, `regen/scripts/test_archacc_planner.py`, and
     skill protocol step 6.

## Post-substitution domain membership closure (entry 101)

101. **A type-preserving domain rewrite and a function-update domain lemma solve
     different obligations.** The run resumed at event 1901. After three
     pre-Isabelle phase rejections, candidate 1925 supplied the explicit old-object
     instantiation. Isabelle event 1927 successfully discharged the conditional
     update premises and reduced the first branch to
     `dom (ksPSpace b) = insert p (dom (ksPSpace b))`, leaving two subgoals.
     Candidate 1930 repeated `dom_fun_upd2`; event 1932 reproduced the same exact
     residual, and bounded repair ended at event 1935.

     The remaining fact follows from the existing concrete lookup
     `ksPSpace b p = Some ko`: via `domIff`, `p` is already in the domain, so the
     insertion is absorbed. The first-conjunct validator now spans from its first
     `rule conjI` to the next split and requires `domIff` within that branch.
     Runtime exact premise retrieval also includes `domIff`, keeping premise
     visibility consistent with the gate. The prompt distinguishes the two steps:
     `regen_pspace_dom_update` preserves the abstract domain under replacement;
     `domIff` closes concrete membership after substitution.

     Evidence: finals 1925/1930, Isabelle events 1927/1932, events 1901-1935,
     `regen/archacc_milestone.py`, `regen/scripts/test_archacc_planner.py`, and
     skill protocol step 6.

## Cataloged domain absorption interface (entry 102)

102. **A required tactic name is not a healthy premise unless its declaration is
     present in the scoped retrieval payload, and equality orientation still has
     to match the selected theorem.** The run resumed at event 1936 and phase
     validation forced the complete update/instantiation/domain structure by
     event 1964. Candidate 1963 cited `domIff`, but Isabelle event 1965 still
     failed at the following `rule conjI`; the first residual remained
     `dom (ksPSpace b) = insert p (dom (ksPSpace b))`, with two subgoals.
     Bounded repair ended at event 1968.

     Prompt audit showed that `domIff` had no declaration record in the premise
     payload. The scoped catalog does contain `dom_insert_absorb`, whose conclusion
     is the reverse equality, and `dom_in`, which converts an existential `Some`
     lookup to domain membership. Exact retrieval now includes both records. The
     first-conjunct gate and prompt require the orientation-aware sequence
     `rule sym`, `rule dom_insert_absorb`, and `simp add: dom_in`. This supersedes
     entry 101s tentative `domIff` closure while retaining its branch-bounded
     validation design.

     Evidence: prompt/final 1963, Isabelle event 1965, premise catalog records
     `dom_insert_absorb` and `dom_in`, events 1936-1968,
     `regen/archacc_milestone.py`, `regen/scripts/test_archacc_planner.py`, and
     skill protocol step 6.

## Contextual simplification before orientation (entry 103)

103. **A theorem with the right final orientation can still be applied too early,
     before an assumption has normalized the other side of the target.** The run
     resumed at event 1969 with both cataloged domain records present in prompt
     1984. Validation forced the model through the complete first-branch contract.
     Candidate 1996 then used `rule sym`, `rule dom_insert_absorb`, and `rule dom_in`.
     Isabelle event 1998 failed at the subsequent split with two goals: the first
     was `insert p (dom (ksPSpace b)) = pspace_dom (kheap a)`. The early symmetry
     step had run before the contextual `pspace_dom = dom` assumption was used.
     Bounded repair ended at event 2001.

     The branch now requires one `simp` or `clarsimp` command containing both
     `dom_insert_absorb` and `dom_in`. In that single simplifier context, the
     relation assumption rewrites `pspace_dom`, the concrete lookup establishes
     membership, and insertion absorption closes the equality. A preceding
     `rule sym` is rejected. The complete phase-mandatory interface now protects
     `conjI`, `regen_pspace_dom_update`, `dom_insert_absorb`, and `dom_in` from
     historical exhaustion.

     Evidence: prompt 1984, final 1996, Isabelle event 1998, events 1969-2001,
     `regen/archacc_milestone.py`, `regen/scripts/test_archacc_planner.py`, and
     skill protocol step 6.

## Premise-name token boundaries (entry 104)

104. **Phase-interface checks must parse theorem identifiers, not use raw
     substring containment.** The new combined-domain regression initially found
     that a command mentioning only `dom_insert_absorb` also satisfied the naive
     check for `dom_in`, because the latter is a prefix of the former. Both names
     are now matched with identifier word boundaries. The complete 86-test suite
     passes from the `regen` working directory; an earlier root-directory test
     invocation failed only during module discovery and did not execute harness
     tests.

     Evidence: `regen/archacc_milestone.py` and
     `regen/scripts/test_archacc_planner.py`.

## Explicit conditional absorption premise (entry 105)

105. **A conditional rewrite premise should be exposed as its own proof goal when
     contextual simplification cannot solve it reliably.** The run resumed at
     event 2002. After four phase-gated rounds, candidate 2029 used the required
     combined `clarsimp` with `dom_insert_absorb` and `dom_in`. Isabelle event
     2031 still failed at the following split with two goals; the first remained
     `dom (ksPSpace b) = insert p (dom (ksPSpace b))`. Bounded repair ended at
     event 2034.

     The domain branch now uses `subst dom_insert_absorb` on the target insertion.
     Isabelle can infer the pointer and heap from that exact subterm, while the
     theorem condition becomes a separate membership goal. The protocol then
     applies `simp add: dom_in` using the concrete lookup and closes the rewritten
     `pspace_dom = dom` equality by assumption. Validation requires all three
     steps before the next conjunction split and continues to reject premature
     symmetry.

     Evidence: final 2029, Isabelle event 2031, events 2002-2034,
     `regen/archacc_milestone.py`, `regen/scripts/test_archacc_planner.py`, and
     skill protocol step 6.

## Tactic-line validation and phase-aware repair budget (entry 106)

106. **A branch-level bag of tactic names is not proof that the required tactic
     was applied to the required subgoal.** The run resumed at event 2035 and
     candidate 2062 reached the explicit absorption protocol. Isabelle event 2064
     failed exactly at line 434, `apply (erule dom_in)`, with three unchanged
     subgoals: domain membership, `pspace_dom = dom`, and the remaining relation.
     The gate had incorrectly combined `clarsimp` from another indented line with
     `dom_in` from this line because the multiline parser grouped the whole
     indented branch under its outer `rule conjI`. Bounded repair ended at event
     2067.

     Absorption, membership, and final-assumption checks now use anchored
     per-tactic regexes over the first branch text. Membership specifically
     requires an `apply (simp ...)` or `apply (clarsimp ...)` line containing the
     independent identifier `dom_in`; `erule dom_in` is rejected. The contract
     repair bound is also raised from five to eight attempts. It remains finite,
     but leaves three rounds for a new semantic residual after the four recurrent
     phase-shape corrections instead of forcing another full resume and prefix
     replay.

     Evidence: final 2062, Isabelle event 2064, events 2035-2067,
     `regen/archacc_milestone.py`, `regen/scripts/test_archacc_planner.py`, and
     skill protocol step 6.

## Resumable phase-gate frontier (entry 107)

107. **A bounded repair run must persist pre-Isabelle progress, not only
     machine-checked failures.** Events 2068-2107 showed that the eight-round
     budget was sufficient to reach the intended conditional domain rewrite,
     but the last candidate used `rule dom_in` instead of the required
     simplification. The phase gate correctly rejected it before Isabelle.
     However, only machine-checked failed frontiers were reconstructed on the
     next process invocation. The rejected candidate and its increasingly
     precise diagnostic lived only in the terminated Python process, so each
     resume spent early rounds replaying already rejected phase shapes.

     The planner now persists the latest per-contract phase rejection in
     `latest-runtime-phase-rejections.json`, including the complete LLM response,
     exact residual, and repair round. A resume injects this record into the
     first repair diagnostic and records a corresponding audit event; the state
     is removed when the contract becomes green. For generations created before
     this state file existed, it is reconstructed from the immutable phase-gate
     event and its nearest preceding audited repair final. The phase validator also
     distinguishes `rule dom_in` and `erule dom_in` from a valid membership
     simplification and returns an exact line replacement:
     `apply (simp add: dom_in)`. This keeps the finite repair budget focused on
     new semantic evidence without changing or expanding the frozen lemma DAG.

     Evidence: finals 2098/2101/2104, phase rejections 2099-2106,
     `regen/archacc_milestone.py`, `regen/scripts/test_archacc_planner.py`, and
     skill protocol step 6.

## Direction-aware domain membership (entry 108)

108. **A visible theorem name is not enough; the harness must plan how its
     conclusion is oriented.** After entry 107, event 2123 proved that the last
     phase rejection was restored into the next run. Repair 2 crossed the phase
     gate but failed because the model instantiated a nonexistent theorem
     variable `A`. Repairs 4-6 removed that parser error and reached the intended
     line 434, where Isabelle consistently left three goals. The first was
     exactly `p \<in> dom (ksPSpace b)`.

     Catalog inspection showed the real declaration:
     `dom_in: (\<exists>x. c a = Some x) = (a \<in> dom c)`. Its default simp
     direction is existential-to-membership, so the harness-mandated
     `simp add: dom_in` cannot rewrite a membership goal backwards. The corrected
     protocol uses `rule iffD1[OF dom_in]`, `rule exI`, and the existing concrete
     lookup assumption, followed by a second assumption for the rewritten
     `pspace_dom` equality. The phase gate validates this ordered sequence and
     rejects direct rule, erule, and simp uses of `dom_in`.

     Persisted phase rejections now carry a protocol version. Older state is
     migrated on load so its candidate evidence remains available without
     replaying the disproved simp instruction. This correction was made after
     deliberately stopping repair 7 rather than spending the final bounded
     rounds under a known-invalid strategy.

     Evidence: events 2117-2153, Isabelle runtime-leaf checks 2141 and later,
     catalog record `premise:24c9165b3715067d2a9f`,
     `regen/archacc_milestone.py`, `regen/scripts/test_archacc_planner.py`, and
     skill protocol step 6.

## Atomic helper bundle exposes a missing semantic assumption (entry 109)

109. **A statement shell proves syntax and typing, not that a planned helper is a
     valid consequence of the consumer guard.** Generation
     `20260723T090932Z-92a8df5c` replaced the monolithic whole-`corres` runtime
     leaf for `setObject_PD_corres` with one audited, atomically frozen six-node
     micro-DAG. The planner prompt, raw provider stream, final response, validator
     residuals, proof calls, and Isabelle checks remain under the same generation
     ledger. Cutover archived 132 prior contract/helper attempts and removed the
     non-green contract proof that a `KeyboardInterrupt` had left in
     `proofs.partial.json`.

     Five nodes were Isabelle-green on their first proof call: the changed PDE
     slot relation plus projections of `pspace_relation`, `ekheap_relation`,
     `ready_queues_relation`, and `ghost_relation`. The sixth node,
     `regen_pspace_relation_update_page_directory_pde`, passed declaration-shell
     checking but failed after six bounded proof repairs. Three candidates reached
     Isabelle, reducing the residual from three goals to two; three others were
     rejected before Isabelle for guessed or undeclared facts (`dom_def`,
     `pageBits_def`, `mask_pd_bits_inner_beauty`, and `typ_at'_def`).

     The remaining relation-cut obligation shows that the helper statement is
     under-specified. It assumes incoming `pspace_relation`, the PageDirectory
     lookup, `pde_at'`, address equations, and `pde_relation_aligned`, but omits
     `pspace_aligned s`. The updated cut is covered, while preservation of cuts
     owned by other abstract objects requires non-overlap/alignment evidence; the
     fixed contract guard provides exactly `pspace_aligned`. The human proof uses
     it in the corresponding unchanged-object branch, but the model never saw the
     human source. This is therefore a planning/statement-boundary failure, not
     evidence that the leaf prover merely needed more search.

     The harness now supports a stable `--helper-bundle-contract` launcher option,
     audited residual-premise extension, atomic graph cutover, false-green state
     cleanup, resume-safe bundle premise restoration, and pre-Isabelle support
     citation repair. The next planner revision must add a semantic consumer check:
     for every preservation helper, inventory which parent guard assumptions are
     needed for the changed point and the unchanged region. Statement-shell pass
     alone must not authorize graph cutover. No further graph expansion or proof
     generation was attempted after the bounded failure.

     Evidence: events 2166-2233; helper checks 2196, 2198, 2200, 2202, and 2204;
     hard-helper Isabelle checks 2222,
     2224, and 2230; `planner/helper-bundle-setObject_PD_corres.json`;
     `planner/helper-bundle-cutover.json`; `regen/archacc_milestone.py`; and
     `regen/run_theory_generation.sh`.

## Parent-guarded preservation bundle trial (entry 110)

110. **Parent-guard provenance repairs statement applicability, but changed-point
     and same-owner preservation still need a finer executable boundary.** The
     harness replaced the failed six-node Helper 6 plan with a source-linked,
     atomically frozen four-node bundle. Its semantic gate requires the concrete
     `pde_at'` guard in domain and owner helpers, the abstract `pspace_aligned`
     guard in the foreign-owner helper, both guards in composition, complete
     frame-obligation coverage, and canonical dependencies on the three earlier
     bundle nodes. Exact premise retrieval was extended with the visible domain,
     relation-cut, alignment, and address-normalization declarations. Generation
     `20260728T092428Z-33892a56` records generation
     `20260728T083432Z-58092f81`, its v2 bundle and attempt ledger, and the
     non-timeout event-35 Isabelle residual as hash-bound source evidence; no
     human proof body was exposed to the model.

     The new bundle passed parent applicability at event 13, its isolated
     declaration shell at event 14, atomic cutover at event 15, and the complete
     graph statement shell at event 16. H6.1 domain preservation was green on
     its first model proof and Isabelle event 20. H6.2 now correctly retains
     `pde_at'`, so the previous under-specified address-alignment proposition was
     not replayed. Its first proof nevertheless used one broad `fastforce` for a
     conjunction containing both the changed slot and every unchanged owner
     slot. Isabelle event 22 left that complete conjunction as one subgoal: the
     proof did not explicitly recover and instantiate the PageDirectory owner
     cut from `pspace_relation`. This is evidence for splitting H6.2 into
     changed-point and same-owner executable leaves, or for giving the lemma
     backend a structured relation-cut strategy; it is not yet evidence that the
     guarded statement is false.

     The first repair response was never checked by Isabelle. Its explanation
     contained an unescaped Isabelle backslash, so JSON parsing failed at event
     24 after the fully audited prompt/raw/final event 23. The generation stopped
     with H6.1 green, H6.2 red, and H6.3/H6.4 unattempted, as required by the
     bundle-only failure policy. This independently exposes a response-lifecycle
     gap: proof-repair JSON parse failures need their own bounded format-repair
     path rather than terminating the generation or being counted as semantic
     attempts. Bundle-only mode also repeated the global family/DAG/critique
     calls; a later lifecycle optimization should reuse a hash-frozen base plan.

     Evidence: generation `20260728T092428Z-33892a56`, events 11-24,
     `planner/helper-bundle-setObject_PD_corres.json`,
     `planner/helper-bundle-applicability-setObject_PD_corres.json`,
     `planner/attempts.json`, `regen/archacc_milestone.py`,
     `regen/run_theory_generation.sh`, and
     `regen/scripts/test_archacc_planner.py` (68 tests).

## Three-authority graph revision direction (entry 111)

111. **Helper count is the wrong control variable; graph changes need separate
     semantic authority and deterministic transaction gates.** Review of the
     original Claude Schedule_R artifacts confirmed local replan explosion: one
     bitmap cluster spent more than twenty checks for zero locks, and later
     plans accumulated sixteen `chooseThread_corres` replan helpers. Many such
     items had no explicit `depends_on` edge. The corpus-level result was not
     uniform over-factoring, however. Machine local fan-out had median zero, 85%
     of helpers were one-off, and 14 of 65 duplicated existing imports. The old
     harness therefore combined global under-organization with pathological
     fragmentation inside a few hard clusters.

     The current opposite policy, one immutable runtime leaf per contract plus
     fixed atomic bundles, prevents unbounded trees but cannot respond when an
     Isabelle residual proves that one planned node contains independent
     semantic obligations. ArchAcc_R H6.2 is the concrete example: changed-point
     relation and quantified same-owner preservation were frozen into one
     executable lemma, and proof-only repair was not allowed to split it.

     The prospective control plane now has three named authorities. The Session
     Planner owns theory placement, exports, cross-contract sharing, scheduling,
     and ancestor invalidation. The File Planner owns one ready theory's initial
     contract-rooted semantic-obligation graph and exact executable statements.
     A new Graph Revision Arbiter receives a hash-bound red-node request,
     Isabelle residual/frontier, retrieval and duplicate evidence, consumers,
     ownership, and cluster budget; it classifies the failure and proposes
     proof repair, premise reuse, one bounded leaf, a split, rejection, session
     escalation, or a source-linked new generation. It does not prove nodes or
     commit graph changes.

     Deterministic gates remain authoritative: accessibility, cycle/forward-edge
     checks, namespace and normalized-statement deduplication, anti-vacuity,
     consumer and produced-fact dataflow, parent-guard consumption, ownership,
     budget, and Isabelle declaration-shell checking. Additive leaves may be
     audited within a generation; split/replace/move operations start a new
     source-linked generation. Green nodes stay immutable, sealed-export changes
     invalidate descendants, red nodes never enter the premise database, local
     green predecessors are ephemeral, and only sealed reviewed exports become
     persistent generated premises.

     This entry records an approved design direction, not an implemented harness
     claim. The complete responsibilities, request fields, decisions, triggers,
     mutation classes, and publication policy are normative in
     `.claude/skills/isabelle_prover_regen/references/SESSION_HARNESS.md`.

     Evidence: `regen/HARNESS-EVOLUTION.md` entries 11, 23, 26, 109, and 110;
     `reports/regen/schedule_r-incr-20260720-161749/plan.json`;
     `reports/regen/schedule_r-incr-20260721-065301/plan.json`; generation
     `20260728T092428Z-33892a56`; and the current session-harness protocol.

## Fixed-topology Proof-theory regeneration boundary (entry 112)

112. **Setting B forbids human proof architecture, even when the human theory
     DAG is retained.** Two repo-level settings are now separated. Deferred
     Setting A gives all contracts to the model and asks it to create the theory
     partition and imports. Current Setting B reuses only fixed theory/import
     packaging, mechanically classifies contract-owning Proof theories and
     zero-contract Support theories, freezes Support sources, and sanitizes each
     Proof theory to its imports and fixed contracts.

     A Setting B File Planner may receive only all local contracts, legal
     premise records from the real import closure, and evidence produced by its
     own generation. Human helper names, statements, roles, order, consumers,
     dependency graphs, export plans, and target-file bodies are forbidden. The
     Session Planner is reduced to deterministic classification, topological
     scheduling, visibility, sealing, heap identity, and Refine-to-CRefine
     handoff. It cannot predict helpers or backpatch ancestors for reuse.

     Cross-file proof generation is obtained operationally: declarations from a
     generated Proof theory enter the premise database only after that theory is
     sealed and only imported descendants may retrieve them. Generate every
     Refine Proof theory, clean-build and seal Refine, then generate every
     CRefine Proof theory against that exact heap. This supports a repo-level
     contract-proof regeneration claim under fixed topology and frozen Support,
     not a claim that Support proofs or module architecture were regenerated.

     This decision narrows entry 111: its File Planner and Graph Revision
     Arbiter remain, but its Session Planner helper-placement authority and
     ancestor-backpatch direction are not normative for Setting B. Earlier
     generated support-only theory experiments remain harness-development
     evidence and do not count toward a Setting B campaign. The complete M0-M8
     prospective implementation plan is normative in `SESSION_HARNESS.md`; it
     is not yet an implemented harness claim.


## Generated-import theorem-DAG protocol (entry 113)

113. **The current Setting B now fixes Proof file slots and contract ownership,
     but no longer retains the human Proof-theory import DAG.** Entry 112's
     fixed-topology boundary is superseded. Support theories remain frozen and
     Proof theories remain the contract-owning generation units, but a Proof
     package exposes only its file identity and complete fixed contract set.
     Human Proof imports, body commands, helper inventory, attributes other than
     contract-owned attributes, declaration order, consumers, and export design
     are forbidden. Successful generated cross-file dependencies synthesize the
     Proof imports; an import may target only the frozen environment or an
     already sealed generated theory, so the generated graph stays acyclic.

     The File Planner now outputs a DAG containing only generated theorem nodes:
     fixed contract roots, generated obligation theorems, and generated helper
     leaves. Existing premises are not graph nodes because their actual use is
     uncertain at planning time. A helper is a leaf by default and may call
     already checked premises; contracts and obligation theorems may compose
     green generated nodes. Direct contract proofs and shared helpers remain
     legal, avoiding artificial wrapper layers.

     The Premise Database has an explicit service boundary. It stores canonical
     checked declarations and their statements, owner theory/session,
     attributes, defining equations, hashes, source class, generation/heap
     provenance, and publication state. Callable sources are frozen parents,
     frozen Support, sealed generated Proof theories, and current-file green
     nodes. It excludes human Proof facts, red/planned nodes, abandoned
     generations, and unsealed future/sibling facts. Retrieval occurs when a
     node starts and again from an Isabelle residual. A retrieved cross-file
     fact proposes an import of its owner; the harness checks provenance and
     cycles before proving in that context. The database neither invents
     theorem nodes nor approves graph/import changes.

     The Graph Revision Agent is the sole LLM authority for evidence-backed
     theorem-graph changes. It receives one hash-bound red node, its role,
     consumers, attempts, exact Isabelle residual/frontier, premise
     retrieval/duplicate evidence, a proposed operation, and remaining budget.
     It may request proof repair or retrieval, approve an obligation/helper
     addition, split, role change, generated-statement replacement, existing
     premise reuse, rejection, or generation abandonment. It cannot read human
     proofs, produce proof bodies, or commit mutations. Deterministic checks
     still enforce fixed contracts, acyclicity, helper-leaf discipline,
     consumers, guard preservation, statement applicability, deduplication,
     namespace/provenance legality, and budgets.

     The authority and artifact flow is now specified as M0-M12 in
     `.claude/skills/isabelle_prover_regen/references/SESSION_HARNESS.md`:
     campaign freeze, contract extraction/classification, sanitized environment,
     initial Premise Database, incremental file selection, File Planner DAG,
     graph/statement certification, node proving, graph revision, file
     assembly/seal, premise publication, Refine seal, and CRefine seal against
     generated Refine. Each stage has explicit inputs and outputs. Baldur
     (ESEC/FSE 2023, <https://arxiv.org/abs/2303.04910>) is recorded only as a
     replaceable single-theorem generation/Isabelle-error repair backend. It has
     no repo-planning or graph-mutation authority.

     This is a design protocol, not an implementation-complete claim. Existing
     ArchAcc_R runs remain evidence for why graph revision is needed, but their
     hard-coded import masks, contract/support-node schema, and runtime
     one-helper policy are legacy implementation behavior to be replaced.


## Static session list and Codex 5.5 node-prover shortlist (entry 114)

114. **Session file scheduling is independent of premise retrieval, and file
     failure no longer stops campaign traversal.** M4 now consumes all public
     Proof-file packages, fixed contract IDs, classification, session stage, and
     scheduling policy. It emits one immutable file-generation list and a mutable
     status table. It does not consume the Premise Database. The runner records
     pending, generating, sealed, failed, skipped, and retry states. A failed or
     skipped file does not block attempts on later list entries. Later proofs can
     retrieve only facts that actually exist in frozen or sealed sources; a
     missing fact from a failed file is therefore classified as a node-level
     premise-availability failure. The session can be sealed only when all
     required files are sealed; otherwise the result is an explicitly partial
     campaign.

     The local configuration used the logged Codex relay with
     `gpt-5.5` as the default model. Recent Isabelle work was therefore screened
     for reusable control algorithms rather than only fine-tuned-model scores.
     The primary hard-node candidate is the public Stepwise/OSDI 2026
     architecture: Isabelle REPL, best-first proof-state search, symbolic repair,
     filtering, and automatic subgoal discharge, with the paper model server
     replaced by the Codex relay. The simple baseline remains Baldur-style
     whole-proof generation and Isabelle-error repair. ProofAug-style structural
     salvage/multigranular automation is the next extension. IsabeLLM's RAG,
     error tracing, counterexamples, and Sledgehammer integration are component
     candidates. AutoReal, IsaMini, and HybridProver remain useful comparisons,
     but their reported systems depend more directly on fine-tuning or proof
     language changes.

     The full paper list, links, staged comparison, and invariant Lemma Prover
     input/output boundary are recorded in
     `.claude/skills/isabelle_prover_regen/references/SESSION_HARNESS.md`.
     This entry records a design correction and backend shortlist, not an
     implemented reproduction.


## Stepwise-Codex statement-to-proof reproduction (entry 115)

115. **The public Stepwise controller can be reused as an M7 theorem backend,
     but the current Codex relay transport is not production-sealable.** The
     unchanged checkout at `tools/seL4-proof-search` commit
     `3cb5e3ba48f6998696f6c721ab382217b76dc21a` accepts a `PureLemma` containing
     an exact statement, initializes IsaREPL at its owner theory, steps to that
     statement, and queries `/generate_batch` for candidate next commands.
     `regen/stepwise_codex.py` preserves that controller and replaces only the
     paper's fine-tuned model server with the local `gpt-5.5` relay.

     Generation `20260728T172744Z-stepwise-codex-smoke` started with
     `lemma stepwise_codex_smoke: "rev (rev xs) = xs"` and no proof body visible
     in the fixture. One Codex call returned `by simp` and an induction fallback;
     IsaREPL accepted `by simp`, and the materialized theory passed a complete
     `Stepwise_Codex_Smoke` session build. The generation stores the exact proof
     state prompt, raw relay response, final answer, parsed/ranked steps, relay
     metadata, Stepwise log, IsaREPL log, configuration hashes, and result.

     Reproduction exposed four lifecycle assumptions and one retry defect. The
     official module loads tactic inventories relative to its working directory;
     the container must receive Isabelle's bundled `JAVA_HOME`; an added session
     directory needs a valid `ROOT`; and the Java gateway needs a bounded
     port-readiness wait instead of a fixed one-second sleep. The relay can also
     return HTTP 200 with an application-level error. Such permanent errors now
     become HTTP 422 at the Stepwise adapter boundary, avoiding Stepwise's five
     retries for 502 responses. Pinned setup is recorded in
     `regen/setup_stepwise_codex.sh` and
     `regen/stepwise-codex-requirements.txt`.

     This does not authorize production use. The relay marks the agent read-only
     but still permits repository reads, returns only the final answer, and does
     not expose raw model tool events. Prompt text forbidding file access is not
     an enforceable contamination barrier. Every current adapter result is
     therefore `sealable_generation=false`. M7 production activation requires a
     mechanically no-tool transport with recorded denial or complete tool-event
     auditing. The backend has no authority to add helpers or mutate the theorem
     DAG; RED results return to the Graph Revision Agent.

     The operational continuation is now frozen in
     `regen/REFINE-CREFINE-REPO-LEVEL-EXPERIMENT-PLAN.md`. The next milestone is
     fresh M0-M4 Refine bootstrap, not another legacy ArchAcc_R generation.


## Setting B M0/M1 bootstrap implementation (entry 116)

116. **The new `regen-refine` branch now has a deterministic M0 campaign freeze
     and a trusted, leakage-audited M1 contract extraction.**
     `regen/refine_campaign.py` canonicalizes the campaign configuration, hashes
     every declared M0 input, derives the campaign ID from the payload, performs
     no model call, and refuses to replace a different existing artifact. Unit
     tests establish stable replay, frozen-input sensitivity, tamper detection,
     and immutable-write rejection.

     The first bootstrap ID, `sel4-refine-setting-b-fb3af355025b5d09`, exposed
     that the M1 extractor itself had not been frozen. It remains an immutable
     development record. After adding `regen/refine_contracts.py` to the M0
     input set, the current campaign became
     `sel4-refine-setting-b-e5306f8128269b55`.

     Trusted M1 uses the ROOT ownership parser plus an explicit ARM filter; the
     latter was necessary because raw session assignment also matched AARCH64
     and other architecture copies with duplicate theory names. It reads human
     Proof sources only in this trusted stage, emits source locations only in a
     private manifest, and emits public packages containing file identity and
     exact selected contracts only. For ARM Refine, frozen selector
     `operation-corres-v1` finds 41 theory slots, 21 Proof theories, 20 frozen
     Support theories, and 287 selected contracts.

     `M1/audit.json` records exact-once private/public contract coverage, unique
     IDs, Proof/package count equality, absence of proof commands and forbidden
     source fields, and false human-content visibility assertions. It passes,
     and all 25 M1 artifacts replay as `unchanged`. These counts are claims about
     the frozen selector, not about every human lemma whose name or statement
     happens to mention `corres`. M2 sanitized workspace construction is next;
     M3/M4 and all theorem generation remain unstarted.

     A final M0 audit found that `--verify` initially checked only manifest
     self-integrity, while full frozen-input drift was detected only by rebuilding
     the manifest. The verifier now also checks the Git revision, canonical
     campaign configuration, and current size/hash of every frozen input; M1
     invokes this gate before reading trusted sources. Because the verifier and
     M1 implementation are themselves frozen, this produced the current campaign
     `sel4-refine-setting-b-360fdf9a7f7eb738`. The intermediate
     `sel4-refine-setting-b-e5306f8128269b55` remains development evidence and is
     not continued.


## M1 contamination audit and trusted-source identity repair (entry 117)

117. **Campaign `sel4-refine-setting-b-360fdf9a7f7eb738` was superseded after a
     post-hoc review found human Proof commands in public contract packages.**
     The previous line-oriented detector missed `notes`, same-line `using`,
     `including`, `supply`, `unfolding`, `(is ...)` aliases, and human comments.
     These commands altered proof context or exposed human search hints even
     though exact-once contract counts passed. The old `M1/audit.json` remains
     immutable; a separate `campaign-disposition.json` marks its PASS as a false
     negative and prohibits continuation.

     `regen/refine_contracts.py` now scans Isabelle outer syntax outside quoted
     terms and cartouches, handles nested block and symbolic comments, retains
     theorem names/declaration attributes plus `fixes`, `assumes`, `defines`,
     `shows`, `obtains`, `for`, and propositions, and removes proof-context and
     proof commands. Regression tests cover the observed Arch_R, CSpace1_R,
     Tcb_R, and Untyped_R shapes, including same-line command tails and
     `\<comment>` inside a proposition.

     M0 now treats `verification/l4v` as an independent trusted Git repository,
     freezes revision `00d9073f70d064b31b4b1fad8a0e7086bb258007`, and requires
     `proof/ROOT` plus `proof/refine` to remain clean. M1 persists a private
     inventory of all 41 theory paths and full-source hashes, provides a verifier
     that rechecks every source, and emits an exact private deny inventory for
     all 21 classified Proof sources. M0 also freezes concrete source/heap path
     patterns for the M2 isolation audit.

     The repaired campaign is `sel4-refine-setting-b-89ad2fd1fbfa667e`. Its M1
     audit reports no command/comment leaks across 287 public contracts; all 41
     source hashes verify; M0 and all 26 M1 artifacts replay unchanged. M2 has
     not started and remains gated on declaration-shell elaboration and
     executable forbidden-source probes.

## Front-includes sanitizer boundary repair (entry 118)

118. **Declaration-level `includes` must be removed as a context clause, not
     treated as the beginning of a proof tail.** A post-repair review supplied
     the counterexample `includes no_pre` before `shows`. The previous generic
     truncation rule would preserve only the theorem header, even though none of
     the frozen 287 Refine contracts used this shape.

     M1 now separates removable declaration-context clauses (`notes`,
     `includes`) from proof-tail commands (`using`, `including`, `unfolding`,
     `supply`, and proof commands). Context clauses are blanked through the next
     proposition clause, so a following `shows` and its proposition survive.
     Leakage audit still rejects either context keyword if it survives
     sanitization. A dedicated `includes ... shows ...` regression raises the
     M0/M1 suite to 11 passing tests.

     Because `regen/refine_contracts.py` is an M0-frozen input, campaign
     `sel4-refine-setting-b-89ad2fd1fbfa667e` is retained only as superseded
     bootstrap evidence. Its immutable disposition records that the frozen
     dataset was not affected. The repaired campaign is
     `sel4-refine-setting-b-739cfcb1ec51da4e`: M0 verifies and replays
     unchanged, M1 emits 41 theories / 21 Proof / 20 Support / 287 contracts,
     all 41 source hashes verify, all 26 M1 artifacts replay unchanged, and an
     independent statement scan reports zero forbidden hits and zero missing
     relations. M2 has not started.


## M2 sanitized workspace preflight and parent-cache blocker (entry 119)

119. **M2 workspace materialization has executable content-audit evidence, but
     contract-shell elaboration is blocked by legal parent-session cache
     construction.** The
     current M2 development continuation point is
     `sel4-refine-setting-b-739cfcb1ec51da4e`. It exists because the previous
     `89ad2...` campaign reached M1 verification but then failed the next
     frozen-input check once `regen/refine_contracts.py` drifted during M2
     development. This is the desired behavior: M0-frozen extractors cannot be
     silently patched underneath an existing campaign.

     `regen/refine_m2_preflight.py` now materializes a sanitized M2 workspace
     from public M1 packages, writes contract-shell theories, audits public
     statements again, and compares the workspace against the private Proof-source
     forbidden inventory by path and full-source hash. It deliberately does not
     copy the private forbidden inventory into the model-visible workspace, since
     that inventory itself contains forbidden source paths. Regression tests cover
     proof-context leakage rejection, clean public-package acceptance, and
     detection of copied forbidden source bytes. The relevant M0/M1/M2 integrity
     suite now passes 14 tests.

     `M2-preflight-0005` for `739cf...` passes the workspace-content side of M2: 21
     public Proof packages, 21 contract shell theories, 287 contracts, 21 private
     forbidden Proof sources, no copied forbidden source hashes, no materialized
     forbidden paths, no textual forbidden path hits, and no target-heap hints.
     This supports only the claim that the materialized workspace contains no
     observed human Proof source copy/path or stock target-heap hint. It does not
     prove that a process can access only that workspace, and it does not yet
     support a claim that the contract shells elaborate.

     The elaboration path exposed several harness-level defects and one remaining
     infrastructure blocker. Generated shell files must be named by their theory
     names; ROOT options must be attached to `theories [quick_and_dirty]`; legal
     session catalogs must include the l4v parent sessions without duplicating
     conflicting ROOT directories; shell theories must import
     `"BaseRefine.Include"`, not a local `Include`; and `isabelle build` should
     use `-S` for this preflight. After these repairs, the build no longer fails
     immediately on ROOT or import mistakes. It times out while still building
     `Word_Lib`, before checking the generated contract shells. A separate
     `Word_Lib` warm-build probe also timed out/terminated.

     The corrected design lesson is that M2 contains three distinct gates:
     workspace-content audit, mechanical source-access isolation, and contract
     elaboration. The first passes; the second was not tested; the third is
     blocked by legal parent-cache availability before any fixed statement is
     checked. M3/M4 and all proof generation remain blocked.

## M2 gate-semantics correction (entry 120)

120. **A clean materialized workspace is not proof of source-access denial.**
     Review of `audit_workspace` showed that it only traversed files already
     copied into the M2 workspace. It created no restricted execution
     environment, ran no forbidden-read probe, and recorded no OS/container
     access-policy identity. The historical `source-access-audit.json` from
     preflight 0005 remains evidence for content inspection only.

     `regen/refine_m2_preflight.py` now emits separate future artifacts for
     workspace-content audit and source-access isolation. Until a mechanical
     runner exists, the latter explicitly records `verdict: not_run`. The CLI
     has no default campaign and requires `--campaign-dir`, preventing accidental
     continuation from a superseded campaign. M2 success now requires content
     audit, source isolation, and contract elaboration all to pass. Success
     returns 0, a genuine failed check returns 1, and a saved incomplete result
     such as timeout/skipped/not-run returns 2. The integrity suite now contains
     17 tests. No Isabelle build was rerun for this correction.

     `tools/lemma_inventory/parse_roots.py` remains clean at the frozen top-level
     revision but is not independently content-hashed. Before production M3/M4,
     it must be added to the frozen input set and the resulting successor
     campaign must replay M0-M2 against the reproducible parent cache and
     mechanical isolation environment.

## ROOT, Support, and contract-context repair (entry 121)

121. **M2 cannot derive a legal context from contract text alone when ROOT
     parsing or contract-owned semantic context is incomplete.** The ROOT
     parser treated quoted parent sessions and bare theory entries
     inconsistently, making `Lib` appear empty and corrupting Support
     reachability. It now parses both forms and is an independently hashed M0
     input with regression coverage. The initial legal Support set is derived
     mechanically: eight theories are buildable from frozen parents/Support;
     12 others remain blocked on not-yet-generated Proof theories.

     A second failure showed that 278 selected contracts occur under the
     architecture interpretation context, with one nested-context case. The
     proposition text alone therefore did not elaborate. M1 now preserves only
     the minimal enclosing `context ... begin` openers as contract-owned
     semantic identity, while still excluding human helpers, imports, proof
     hints, and proof bodies. Public contract ordering is semantic-ID order,
     not human source order.

## Isolated M2 completion and cache identity (entry 122)

122. **M2 now has three independently evidenced PASS gates.** The isolated
     runner copies a symlink-free sanitized l4v tree with all 21 classified
     Proof sources absent, loads content-identified parent/Support caches, and
     runs in a network-disabled container without the host workspace or Docker
     socket. Twenty-one Proof-source probes plus stock `Refine` and `CRefine`
     heap probes are denied; two legal-read controls pass. All 287 contract
     shells elaborate in Isabelle.

     Cache development exposed two lifecycle errors. Immutable manifests must
     not include operational `created/unchanged` copy dispositions, because the
     same bytes then produce different replay metadata. Also, a successful
     `quick_and_dirty` contract-shell session may retain only its build database,
     not a loadable heap; Support reuse requires a real heap, while shell reuse
     verifies the PASS gate and build database. These distinctions are now
     explicit and content checked.

## Repository-scale M3 export repair (entry 123)

123. **The initial premise exporter failed first on API shape, then on scale,
     then on provenance.** Isabelle's `Base64.encode` accepts `Bytes.T`; fixing
     that type exposed expensive per-fact ML/Scala calls. Replacing it with pure
     ML hex encoding exposed a second problem: constructing all output lines
     before `File.write_list` consumed about 8.9 GiB. M3 now streams each fact
     and automation-pool theorem directly to disk, and Python parses the TSV
     line by line.

     The first complete 76,663-fact dump then failed publication because short
     theorem owners such as `InvariantUpdates_H` were mistaken for parents, and
     proposition rendering contained YXML markup. M3 now classifies short
     owners against the M2-certified eligible Support set, publishes importable
     owners as `M2_Frozen_Support.<theory>`, and renders propositions under an
     empty print mode with markup disabled. It publishes an indexed SQLite
     database with exact statements, hashes, owners, source classes,
     automation attributes, definition equations, and M2 provenance.

## M2/M3 production continuation (entry 124)

124. **Campaign `sel4-refine-setting-b-67ae972e07f9c329` is the first current
     continuation with M0-M3 all PASS.** M3 snapshot
     `m3-114402509f161cc71f91` contains 74,893 frozen-parent and 1,770
     frozen-Support facts from 382 owners. Effective automation membership is
     135 `simp`, 3,788 `wp`, and 13 `corres` records. The M3 theory completes in
     about 3:35 and the full command in about 4:14.

     M3 replay no longer reruns Isabelle or collides on runtime metadata. It
     verifies M0/M1 drift gates, the M2 gate hash, raw export hashes, database
     hash, row count, source classes, publication states, audit, and snapshot
     identity, then returns `replay: unchanged`. The next production milestone
     is M4 Session Planner output only; M5/File Planner and lemma generation
     remain gated.

## M4 evidence scheduling and audit-polarity failure (entry 125)

125. **A negative-control audit must store positive gate assertions.** M4 was
     implemented as one audited Codex 5.5 candidate-edge call followed by a
     deterministic certifier and scheduler. Public contract statements, a
     normalized M2 Support plan, and only the M3 snapshot identity are visible;
     premise records and human Proof imports remain excluded. Contract-
     composition edges form strata, while Support-unlock and operation-call
     evidence only influence the serial fallback.

     Campaign `f84ff4ae434e3c30` completed its model call and produced complete
     21-file/287-contract artifacts, but M4 correctly remained FAIL. The audit
     stored `premise_records_used_for_ordering: false`,
     `human_proof_import_graph_used: false`, and
     `final_schedule_emitted_by_model: false`, then incorrectly applied
     `all(checks.values())`. Three desired false values therefore made success
     impossible. The fix renames these to positive assertions and adds a
     regression that requires every gate check to be true. The failed campaign
     and transcript remain immutable lifecycle evidence.

## M4 PASS result and schedule-quality boundary (entry 126)

126. **M4 protocol validity and schedule quality are different claims.** The
     corrected successor `70faa08a3822a1f9` replays M0-M3, publishes 76,663 M3
     facts, and passes M4. Its one 494-second Codex call has a complete sealable
     transcript with no tool events. Deterministic checking accepts 6 of 31
     candidate edges, produces a two-layer list, initializes all 21 Proof slots
     as pending, and records eight published versus 12 blocked frozen Support
     theories. M4 replay returns unchanged without another provider call.

     The result is deliberately not called the true Refine DAG. Twenty-four of
     25 rejected candidates supplied argument-bearing phrases instead of the
     schema's single-symbol tokens. Only `Syscall_R` lands in layer 1; the other
     20 files share layer 0. A separate post-freeze comparison agrees with 9 of
     22 human direct Proof-import edges (40.9%). Human imports are not Setting B
     ground truth and cannot be fed back into generation, but the comparison and
     the variance from the earlier M4 call show that static schedule quality is
     weak and stochastic. The next decision is whether to use the list as a
     retry-capable execution priority or freeze a revised M4 schema/policy before
     M5.

## M4 reconstruction hardening and schema repair (entry 127)

127. **An immutable-looking schedule is not replay evidence when the verifier
     trusts self-hashes and pre-filled audit booleans.** Review of campaign
     `70faa08a3822a1f9` showed that a rehashed contaminated audit, a rehashed
     file list missing one Proof file and 16 contracts, and an invalid mutable
     ledger could still return `unchanged`. The M4 audit also asserted several
     trust-boundary properties as constants rather than deriving them from the
     planner payload and transcript.

     Schema v2 reconstructs M4 from its real inputs. Every generation and replay
     verifies M0/M1 drift plus the complete M3 publication, rebuilds the legal
     contract-only payload and exact prompt, validates the sole real provider
     transcript, reparses the saved final response, and recomputes dependency
     evidence, layers, list, audit, gate, and revision-zero ledger. The gate
     binds the initial ledger hash. Mutable revisions must use sequential events
     and legal Proof/Support/session status transitions. Regression tests cover
     rehashed audit/list tampering, missing coverage, unrecorded status changes,
     invalid revisions, and valid transition replay.

     The planner schema now says that each `symbols` item is exactly one Isabelle
     identifier and grounding uses complete identifier tokens, not substrings.
     Contract-composition remains the only evidence that forms layers. Acyclic
     operation-call edges are enforced as weak same-layer precedence; operation
     SCCs are collapsed and reported, while stronger composition strata win
     across layers. The post-freeze comparison now reports both direct human
     Proof imports and Proof-to-Proof paths collapsed through Support theories;
     neither comparison may feed back into generation.

     Final campaign `c55be4ed1c2f8c1b` passes M0-M4 and reconstructs without a
     second model call. Its one 154,600-character Codex 5.5 prompt produced 49
     candidates: 24 accepted and 25 rejected. The schedule has three layers,
     covers 21 Proof files and 287 contracts exactly once, and has no operation
     cycle or precedence violation. The held-out comparison agrees with 10/22
     direct imports and 13/29 Support-collapsed relations; this remains a quality
     warning, not a correctness oracle. The focused integrity suite passes 46
     tests. Campaign `6e0d35cf78b48cb5` records the brief M0-M3 development step
     before full M3 verification was wired into M4; neither it nor `70faa...` is
     a continuation point. Its regenerable M2/M3 isolated heaps were pruned after
     final verification; immutable manifests, gates, audits, exports, database,
     and snapshot remain.

## First complete generated Proof theory and assembly stabilization (entry 128)

128. **KHeap_R shows that theorem generation and theory integration fail at
     different boundaries.** The modular continuation reached graph v77 with
     122/122 generated/contract nodes GREEN and all five fixed contracts GREEN.
     The final two graph revisions corrected statement/consumer type-flow defects:
     an accidental `obj'` where the fixed `ob'` was required, and an
     underconstrained projected object that needed the same
     `'a::pspace_storable` type as its consumer. These are now recorded as
     planning/certification lessons rather than lemma-prover limitations.

     The first complete-theory checks nevertheless timed out at 1,800 and 3,600
     seconds. Node checks use only transitive generated dependencies, while the
     assembled theory accumulates all declarations and automation context.
     Diagnostic markers located two context-sensitive proof bodies. Replacing
     `simp add` by a two-rule `simp only` in `projectKO_objBits`, and replacing
     a final broad `auto` by `clarsimp` in the endpoint state-relation assembly,
     preserved every statement and graph edge. Both replacements were checked
     individually and together. The official complete theory then finished in
     1,530 seconds and `Generated_Refine` built in 1,511 seconds. KHeap_R is now
     `built`; its 3,537-line source contains no `sorry`, `oops`, or diagnostic
     marker.

     The checker now supports diagnostic node markers for complete checks, but
     never emits them into generated source. Generated-session builds emit
     started, 30-second heartbeat, and finished events, and timeout cleanup can
     remove the named container. Successful `built`/`sealed` transitions clear
     stale failure metadata. The assembly repair records old/new proof hashes,
     node-check logs, and a complete-theory GREEN log under
     `theories/KHeap_R/assembly-salvage/`.

     Repeated failures also establish prospective harness work: deterministic
     free-variable/sort-flow and parent-guard checks before proof search; a
     first-class assembly-stabilization result instead of an unclassified runner
     exception; automatic extraction of the last marker on timeout; and residual
     history aggregation for Graph Revision. The complete operational account
     and next-theory checklist are in
     `regen/KHEAP-R-GENERATION-RETROSPECTIVE.md`.

## Deferred cross-contract graph abstraction (entry 129)

129. **Keep the current GREEN-node immutability rule, and defer global graph
     refactoring.** The final KHeap_R graph demonstrates the trade-off clearly:
     residual-driven local revision is effective at closing contracts, but it
     specializes new nodes for the currently failing consumer. Once a node is
     GREEN it is not rewritten for a later contract, so opportunities to merge
     equivalent statements, generalize helpers, or introduce a shared
     cross-contract abstraction are usually discovered too late. This helps
     explain the low cross-contract sharing observed in the first complete
     theory; it is not evidence that the individual proofs are incorrect.

     A future **Graph Abstraction Pass** may inspect only generated statements,
     actual dependency records, proof bodies, and Isabelle results after a
     theory is GREEN. It could propose merge/generalize/extract transactions for
     repeated or structurally similar subgraphs, then retain a transaction only
     when every affected consumer and the complete theory recheck GREEN. Useful
     evaluation signals include one-consumer node ratio, duplicate normalized
     statements, repeated premise/use profiles, cross-contract fan-out, and
     proof/source reduction after rechecking.

     This pass is deliberately **not implemented or inserted into the current
     pipeline**. File Planner, Lemma Prover, Graph Revision Agent, and Isabelle
     keep their existing responsibilities; GREEN nodes remain immutable during
     active generation. Revisit the pass only after several complete generated
     theories show a repeated reuse deficit, so repository completion is not
     delayed by a speculative global optimization lifecycle.

## Automation-context evidence and deferred registration (entry 130)

130. **A GREEN proof and publication into an automation pool are separate
     decisions.** `isabelle_node_checker.py` now has an optional node-audit
     path that runs inside the same isolated Isabelle context as the proof. It
     snapshots the effective simplifier and WP collections, including simp
     rules, congruences, simprocs, solvers, and the WP rule families, and uses
     Isabelle's `Thm_Deps` to extract the dependencies of the accepted final
     theorem. The artifacts record stable context/dependency identities and
     whether any skipped proof entered the theorem certificate. Ordinary node
     checks remain unchanged unless `audit=True` is requested.

     Three existing KHeap_R GREEN nodes exercised broad `simp`, `simp add`, and
     assembly-stabilized `simp only` proof styles. All three exported an active
     context of 18,067 records; their final proof terms contained respectively
     60, 20, and 11 named dependencies, with `has_skip_proof = false`. This
     confirms that source-name scanning alone is insufficient: the checker can
     now preserve both the automation context made available to a tactic and
     the dependencies retained by Isabelle in the accepted theorem.

     Generated theorems still receive no new `[simp]` or `[wp]` registration by
     default. A future **Automation Interface Pass**, run only after all nodes
     in a theory are GREEN and before theory sealing, may consider generated
     facts that are repeatedly used explicitly and have safe rewrite/WP shapes.
     It may propose end-of-theory `declare theorem[simp]` or
     `declare theorem[wp]` commands, but a proposal is retained only if the
     complete theory rebuilds, dependency drift is audited, and automation
     performance does not regress. A downstream theory may instead register an
     imported fact locally; a sealed ancestor must not be backpatched.

     The Automation Interface Pass is **documented but not implemented**. The
     current GREEN-node immutability rule, generated source, and automation
     registration behavior are unchanged.

## Deterministic statement interfaces and Stepwise failure boundaries (entry 131)

131. **Reject observed planning typos before proof search, and never treat a
     runtime outage as theorem evidence.** `proof_graph.py` now runs a
     deterministic statement-interface certifier whenever a graph is validated.
     It checks generated nodes for incompatible repeated `fixes` types and for
     high-risk implicit primed variables whose spelling is one edit from an
     explicitly fixed variable. Fixed contracts are not rewritten or subjected
     to this generated-node policy. The accepted graph records the policy and
     checked-node count in `statement_certification`.

     The rule is deliberately conservative. It does not require dependency and
     consumer variable names to match, because Isabelle theorem application may
     rename and instantiate variables. It also does not claim to reproduce
     Isabelle sort inference or prove arbitrary parent-guard entailment. Those
     remain part of the declaration-shell and node checks. Against the real
     KHeap_R history, graph v75 and v76 are rejected for the accidental
     `ob'`/`obj'` interface, while corrected graph v77 accepts all 117 generated
     nodes.

     `stepwise_lemma_backend.py` now allocates a fresh OS-selected loopback port
     for every IsaREPL invocation and records it in progress events/results.
     RED outcomes are classified into theorem-search failures versus
     infrastructure failures. Port conflicts, IsaREPL target initialization,
     Docker runtime, and model-provider connection failures are infrastructure;
     they carry `consumes_proof_budget=false` and
     `graph_revision_eligible=false`. `proof_session_runner.py` emits a separate
     `node_infrastructure_error`, restores the logical node round, and stops the
     resumable invocation before proof feedback or Graph Revision. Search
     exhaustion, the overall Stepwise search timeout, tactic residuals, and
     final replay mismatches remain theorem-level evidence.

     Regression tests include the two KHeap_R statement shapes, the corrected
     explicit-variable form, the historical `BindException`, the historical
     `/generate_batch` connection refusal, and a genuine Stepwise search
     timeout. The focused modular/Stepwise/Advisor/Outline suite passes 148
     tests. Assembly stabilization remains a separate bounded proof-body repair
     phase: neither of these changes permits it to alter statements or the DAG.

## Automatic assembly timeout localization (entry 132)

132. **A complete-theory timeout now becomes a targeted, body-only assembly
     repair request instead of an unlocalized runner exception.** Diagnostic
     complete-check source already printed `REGEN_NODE <node-id>` immediately
     before each generated declaration. `assembly_stabilizer.py` now parses the
     complete, untruncated Isabelle output and returns the last marker plus the
     total number observed. `isabelle_node_checker.py` preserves both fields in
     every check result, including the `TimeoutExpired` path.

     When all graph nodes are GREEN but the complete theory is RED or times out,
     `proof_session_runner.py` writes `assembly-stabilization.json`. If the last
     marker names a current graph node, the request binds that node's exact
     statement, role, dependencies, and current proof body and permits only a
     `proof_body_only` change. Statements, roles, dependencies, graph edges and
     contracts are explicitly forbidden. The runner emits
     `assembly_stabilization_requested` with the selected node. If no valid
     marker exists, the request is `unlocalized` and does not guess a target.

     The marker is a progress locator, not proof that the selected theorem is
     semantically wrong. Any repaired body must still pass its node check and a
     new complete-theory check. The real KHeap_R complete logs contain 65
     markers and are parsed correctly. The focused modular/Stepwise/Advisor/
     Outline suite now passes 150 tests. No Support-unlock, actual-dependency
     import synthesis, or post-Refine abstraction pass was added in this change.
