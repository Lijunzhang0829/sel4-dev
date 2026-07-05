# Tactic cost priors — human-readable companion

This is the human-readable mirror of `tactic-cost-priors.jsonl`. The JSONL is
the source of truth read by `propose-tactic.sh`; this file exists for the
agent to consult when reasoning about candidates.

When you confirm a new pattern via `impact-*.jsonl` records, append a row to
**both** files in lockstep.

## Search-strength swaps (4)

| id | from → to | expected | when applies | known anti-pattern | evidence |
|---|---|---:|---|---|---|
| `force-to-fastforce` | `force` → `fastforce` | −9% (n=5) | proof_cost ≥ 30s, has `simp:`/`dest:`/`intro:` arg | bare `apply force`, batch across file, small/simple proofs | Ipc_AC.thy:1057,1071 −6.8%; DetSchedSchedule_AI.thy −11.6%; FinalCaps.thy −1.4%; **CNode_AC.thy:544+571 set_cap_integrity_deletion_aux −12.6%** (Access A/B 2026-04-28); **Syscall_AC.thy:211 lcs_reply_owns −16.2%** (Access A/B 2026-04-28) |
| `auto-to-fastforce` | `auto` → `fastforce` | −5% (n=3) | proof_cost ≥ 30s, terminating tactic | open `auto` mid-block, batch | CNode_AC.thy:192 −4.0%; **Retype_AC.thy:580 untyped_slots_not_in_untyped_range −6.7%** (Access A/B 2026-04-28); **ArchArch_AC.thy:489 perform_page_invocation_respects −0.2%** (Access A/B 2026-04-28, marginal) |
| `blast-to-force` | `blast` → `force` | −2% (low conf) | blast residual subgoals | unmeasured; speculative | (none) |
| `auto-to-clarsimp-fastforce` | `auto simp:` → `clarsimp simp:; fastforce` | −10% (speculative) | very long simp arg list | unmeasured; auto's case-splitting may be load-bearing | (none) |

**The batch trap (documented v6 anti-pattern)**: any of the above applied across
many proofs at once tends to regress on the small/simple ones that didn't need
the swap. Witnesses: Tcb_AC +160%, Syscall_AC +17%, CNode_AC +7%, InfoFlow
Noninterference 380s timeout. Always patch one proof at a time.

## Modifier safety swaps (3)

`X!:` → `X:` removes the "unsafe" eager-firing modifier from elim/intro/dest
modifiers. The unsafe form forces the rule to fire on every match in the goal,
which can drive exponential search inside `auto`/`fastforce`.

| id | from → to | expected | when applies | known anti-pattern | evidence |
|---|---|---:|---|---|---|
| `elim-bang-to-elim` | `elim!:` → `elim:` | −20% | rule is delta/sym-refs/recursive, called inside `auto`/`fastforce` | rule needs eager elim to close subgoal | IpcCancel_AI.thy `blocked_cancel_ipc_invs delta_sym_refs`: −24.4% (AInvs) |
| `intro-bang-to-intro` | `intro!:` → `intro:` | −5% (low conf) | recursive rule, nested implications | rule needed eagerly | unmeasured |
| `dest-bang-to-dest` | `dest!:` → `dest:` | −5% (low conf) | introduces existentials | eager destruction usually required | unmeasured |

The single measured win on `elim!:` is large. The intro/dest analogs are
plausible by symmetry but unmeasured — treat as exploration_picks.

## Search depth limits (2)

Cap the simplifier's recursive unfolding to short-circuit suspected loops.

| id | substitution | expected | when applies | known anti-pattern | evidence |
|---|---|---:|---|---|---|
| `add-simp-depth-5` | wrap `simp`/`clarsimp`/`auto` with `simp_depth_limit=5` | −15% (speculative) | long `simp:` add list, suspected loop | depth too low → verify failure; loop is genuine | unmeasured |
| `wrap-fastforce-depth-3` | `fastforce` → `(fastforce simp_depth_limit: 3)` | −10% (speculative) | fastforce wall ≥ 30s on single proof | unfold needs more than 3 | unmeasured |

The proposer starts at higher depth (5 for simp, 3 for fastforce) because
verify-failure cost (~30s) is cheaper than retry cost. Operator can lower
manually if 5 succeeds with minimal speedup.

## Out of scope (v1)

The proposer prints `out-of-scope` and exits 0 for these:

- Structural tactics: `rule`, `subst`, `induct`, `cases`, `erule`, `drule`, `frule`
- Eisbach methods: `wp`, `wpsimp`, `hoare_vcg_*`
- ATP reconstruction: `metis`, `smt` (sledgehammer was tested separately and
  not productive — see SKILL.md "Sledgehammer linearization" section for the
  manual workflow if you want to revisit)

These categories may be added in v2 if empirical data accumulates.

## Anti-patterns surfaced as score penalties

The proposer applies a `−0.1` score penalty per matching anti-pattern. None of
these are hard blocks — operator and agent can override:

- `proof_cost_ms < 5000` — too small to optimize
- `bare tactic with no hint args` — reorder gains less without modifier args
- `batch substitution across whole file` — the v6 anti-pattern; the proposer
  generates per-line patches so this only triggers if the agent then bundles
  multiple proposer outputs into one patch
- `unmeasured — speculative` — `n_prior_static = 0`; first attempt
- `verify-fail risk` — depth limits, especially `=3`

## Empirical update mechanism

Every proposer call also walks `logs/impact-<session>-*.jsonl` and
`logs/attempts-<session>-*.jsonl`, regex-matching the `notes` field for
substitution mentions. Aggregated counts surface as
`empir n=N mean=X%` next to each candidate. After 5 empirical applies for a
pattern, the score weights shift from `(0.4 prior, 0.5 empirical, 0.1 applies)`
to `(0.2, 0.7, 0.1)` — the empirical signal dominates once it's stable.

Unmatched notes go to `_unparsed` and surface as `unparsed_notes_count: N` in
proposer output. Operator can then refine the regex set in `propose_tactic.py`.
