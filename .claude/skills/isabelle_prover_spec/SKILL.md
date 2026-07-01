---
name: isabelle-prover-spec
description: "Strengthen seL4 Abstract Spec postconditions and invariant-abstract guarantees. Use when adding stronger statements to spec/abstract/ or proof/invariant-abstract/."
---

# Spec — strengthen the specification (execute_additive)

A Hoare-triple lemma `⟨P⟩ f ⟨Q⟩` is **strengthened** when a strictly
stronger statement about the same operation holds:

```
P ⟹ P'    (new precondition is weaker — every old P still satisfies it)
Q' ⟹ Q    (new postcondition is stronger — it still yields the old Q)
```

This sub-skill works in **one shape only: additive**.

> **Every spec strengthening = add a new lemma `L'`, leave the original
> `L` untouched.**

This is the central rule of [execute-additive-design.md][design]. Modifying
an existing lemma (the old "shape 1" + `_old` witness) triggers a re-search
of every consumer of `L` — the cascade that sank Pattern A/C. Adding `L'`
beside an unchanged `L` makes the change **cascade-free**: nobody who relied
on `L` is disturbed, and `L'` is sound by construction (it is its own
theorem, verified by `check-theory.sh`; there is no old form to derive, so
**no `_old` witness is needed**). The strengthening relation above is the
*claim you record*, not a proof obligation on a separate witness.

## The two axes every candidate must answer

A candidate is a point in a 2-D space: **which slot** it fills × **how it
reaches downstream**. Answer both or it is rejected before any build.

### Axis 1 — the slot (what is strengthened)

Three slots exhaust additive strengthening:

| Slot | What's empty | New lemma `L'` | Anchor |
|---|---|---|---|
| **P** | a precondition conjunct `P_c` that `L`'s proof never uses | `L` with `P_c` dropped, **same proof body verbatim** | existing `L` |
| **Q** | `L`'s post is weaker than provable (proof passes through `Q_strong` then weakens it via `hoare_strengthen_post`) | `L` with post `Q_strong`, proved `by (rule <strong_lemma>)` | existing `L` |
| **F** | op never publicly promises to preserve `<field>` | `⟨λs. P (<field> s)⟩ op args ⟨λ_ s. P (<field> s)⟩`, `by (wpsimp simp: <op>_def)` | the op's def (no `L` needed) |

- **Q sub-case (exactness):** `Q_weak` has `f s ≤ x`, `Q_strong` has
  `f s = x`. Same slot, just a `≤`→`=` tightening.
- **P+Q compound** (weaken pre *and* strengthen post in one `L'`) is legal
  but treated as two candidates that may share one patch.

### Axis 2 — the delivery (how `L'` reaches downstream)

A provable-but-unused lemma is dead code that only pollutes search. Every
`L'` must declare its downstream path ([design §2][design]):

| Delivery | Meaning | When |
|---|---|---|
| **wp / simp** | registers `[wp]`/`[simp]`; auto-fires in tactic search | **F-slot default.** |
| **named** | no attribute; an explicit consumer does `rule/wp/drule L'` | **P/Q default.** |
| **block** | `L'` is a building block for another strengthening lemma | helper chains |

`named` and `block` carry a **substate**, and the gate **verifies** it (it
does not take the claim on faith):
- `realized` — a consumer references `L'` by name (first-class). Because `L'`
  is brand new, a *same-patch* consumer is how this is reached: the proposal
  carries `consumer_hunks` (extra patch hunks editing/adding the consumer),
  the driver applies them in one compound patch, and the gate greps the
  **post-patch** file. For `block`, the named downstream candidate must exist
  in the ledger (this round's proposals are pre-registered, so an A-needs-B
  chain proposed together resolves). An unbacked `realized` claim (no
  `consumer_hunks`, downstream absent) is **demoted to `planned`**, not faked.
- `planned` — only a *promise* of a future consumer (provisional; an
  8-week grace period (`grace_period_weeks`), then `orphan` per
  [design §6.2][design]). This is the **normal** path for a lone stronger
  lemma; bundle a consumer only when you want `realized`.

**Hard delivery rules** (enforced *with evidence* by `spec_delivery_gate.py`
*before* any build):
- `delivery` missing → reject.
- `named` with empty target, or `realized` with no consumer in source →
  demoted to `planned`; `planned` with no plan → reject.
- `block` whose downstream candidate is **not already in the ledger** →
  **reject** (design §2.4 forbidden "future-maybe-useful helper"). The driver
  pre-registers every proposal in the round as `discovered` before the loop,
  so an A-depends-on-B chain proposed in the same run satisfies condition-1.
  With the downstream present: `realized` only if `block_dependency_evidence`
  actually **cites `L'`** — a `{reference_snippet}` whose tactic names `L'`, a
  `{downstream_patch}` the gate greps for an `L'` citation, or a
  `{ab_trial: {without_block:"fail", with_block:"ok"}}`. Evidence that does
  not cite `L'` (or a bare prose string) keeps the block provisional
  `planned`. The lifecycle sweep later confirms the *real* dependency: a block
  becomes `realized` only when a downstream **lands AND its source references
  the block** — merely landing (the downstream may have bypassed the helper)
  does not count.
- **`wp`/`simp` on a P-slot or Q-slot → reject by default.** `wp` is the
  strongest delivery (it changes the whole proof-search ecosystem for that
  op). It is admitted **only** by passing `--escalation <record>`, where the
  record comes from `spec_wp_escalation.sh` — the [§2.5.1][design] multi-file,
  3-round baseline-trial-baseline regression (trial wall ≤ baseline×1.05,
  reversible within 3%, ≥3 files). Without a *passing* record the candidate is
  rejected; re-propose P/Q as `named`/`block`.

### Delivery lifecycle (planned → realized → orphan)

`realized` candidates are done. `planned`/provisional candidates enter the
ledger with `delivery_state: pending` + `grace_period_weeks`. The state
machine is advanced by a **periodic sweep**, not by the driver:

```bash
python3 spec-strengthen/scripts/spec_delivery_lifecycle.py            # report
python3 spec-strengthen/scripts/spec_delivery_lifecycle.py --apply    # commit transitions
```

It marks a pending candidate **realized** once a consumer/downstream lands,
or **orphan** once the grace period elapses with none (orphans are aggregated
for GC review, never auto-deleted — design §6.2).

## Workflow

The whole pipeline is one driver, `spec-strengthen/strengthen.sh` — the
spec analog of `lemma-staticize/bench.sh`. An **agent** scans and proposes;
**`check-theory.sh` + `spec_impact.py`** verify; the run is archived under
`spec-strengthen/experiments/strengthen-<theory>-<ts>/`.

```bash
# dry-run (default): trial + impact only, source untouched
spec-strengthen/strengthen.sh proof/invariant-abstract/Ipc_AI.thy --slot Q --n 3

# commit accepted candidates into the source file
spec-strengthen/strengthen.sh proof/invariant-abstract/KHeap_AI.thy --slot F --apply -y
```

Per theory the driver runs:

1. **Baseline** — `check-theory.sh` on the unpatched file (once) → baseline
   wall.
2. **Detector hints (Q/P)** — `spec_slot_hints.py` scans the theory
   mechanically and writes `hints.json`. Q hints classify redirect-shaped
   proofs (`hoare_strengthen_post*`/`hoare_post_imp`) into **inline-redirect**
   (stronger post proven but never named — a true gap), **named-redirect**
   (strong rule already exists — slot filled, alias value only) and
   **exactness** (post commits only `rv ≤ e` where the computation pins exact
   values). P hints flag precondition conjuncts that never appear in the
   proof body (prefix match — `simp: valid_objs_def` counts as consuming
   `valid_objs`), requiring **differential evidence** (some other conjunct IS
   visibly consumed) for high priority and skipping opaque one-liner proofs.
   The agent alone proved unable to find Q/P slots; the detector supplies
   directions, the agent supplies judgment, the trial supplies truth
   ([design §6.3][design]).
3. **Scan (agent)** — `spec_agent.py` calls the bundled Claude CLI on the
   host (Claude Max OAuth — no API key, no container creds). The agent reads
   the theory **plus the detector hints** and proposes up to `--n` additive
   candidates, each a JSON record: `{slot, delivery, delivery_substate,
   delivery_target, lemma_name, anchor_line, new_lemma, rationale,
   strengthening_claim}`. The agent only **proposes** — it verifies nothing.
   **Slot discipline is enforced**: when `--slot` is not `all`, off-slot
   proposals are discarded (a Q run produces Q or nothing — early live runs
   showed the agent silently substituting F proposals).
4. **Per candidate**, in order:
   - **Delivery gate** — `spec_delivery_gate.py` (Axis-2 rules above). A
     reject costs zero build time.
   - **Patch** — a range-replace patch inserts `new_lemma` after
     `anchor_line` (original lemmas untouched).
   - **Trial with closed-loop repair** — `check-theory.sh --patch` must reach
     `OK`. On failure the prover's error + the rejected proposal + a source
     window go back to the agent (`--repair`), which emits a corrected
     proposal (wrong anchor, unclosed proof) **or `[]` when it judges the
     strengthening semantically false** (e.g. a genuinely load-bearing
     premise). Up to `REPAIR_TRIES` rounds (default 1); every attempt is
     archived (`proposal-0.json`, `repair-N.json`, `trial-error.txt`). The
     trial remains the sole source of truth.
   - **Impact** — `spec_impact.py` → verdict + wall gate → `measurement.json`.
   - **Apply** — only with `--apply`; otherwise the would-be `patch.diff` is
     rendered against a temp copy and the source stays clean.
   - **Record** — a per-candidate audit bundle + one ledger event.

You normally just run the driver and review `summary.md`. Hand-driving a
single candidate (custom `new_lemma`, manual anchor) is still possible — see
`spec-strengthen/README.md` — but the driver is the main path.

## What the driver archives

`spec-strengthen/experiments/strengthen-<theory>-<ts>/`:

| Path | Content |
|---|---|
| `summary.md` / `summary.csv` | per-candidate verdict table |
| `proposals.json` | exactly what the agent proposed |
| `agent.log` / `agent-raw.txt` | agent invocation trace + raw reply |
| `command.sh` | reproduce the whole run |
| `NN-<slot>-<lemma>/` | one dir per candidate (below) |

Each candidate dir is a self-contained, rule-5-compliant audit bundle.
**Start with `record.md`** — the one-page replay rendered for EVERY candidate
(success or failure): original lemma source (untouched), new lemma source,
why (detector hint + agent rationale + mechanically-derived strengthening
claim), the repair chain (attempt → prover error → corrected proposal), and
the verification (trial/impact/apply). The raw artifacts behind it:
`proposal.json`, `delivery_gate.json`, `range-patch.patch.txt`, `trial.log`,
`measurement.json`, `patch.diff`, `decision.md`, `command.sh`,
`p_claim_check.json` (P-slot), `proposal-0.json`/`repair-N.json` (repairs). For a candidate
that was **applied**, that bundle is the seL4-source-PR record (parent SKILL
rule 5): open the PR from the `spec-strengthen` branch citing the lemma name +
file, the `reports/golden-baseline/walls.json` entry, and the trial wall.

## Acceptance

A candidate is **accepted** (dry-run) / **applied** (`--apply`) iff **all**
hold:

1. **Delivery gate** passes (Axis-2 contract).
2. `check-theory.sh --patch` returns `OK` on the changed file.
3. `spec_impact.py` emits no `weakening` verdict and at least one of
   `premise-weaken` / `monotone-strengthen` / `postcond-strengthen` /
   `additive`. (`rewrite` / `noop` / `removal` alone is insufficient.)
4. Trial wall ≤ baseline × 1.30 (regression cap; spec strengthening trades
   build time for verification strength — a slightly slower, stronger file is
   the intended direction).
5. The five parent-SKILL hard rules are inherited (no `sorry` / `oops` /
   `axiomatization`, `check-theory.sh` is the only verification gate,
   PR-tracked mainline, heap-volatility care, JSONL ledger event per
   candidate).

There is **no `_old` witness** in this sub-skill — additive lemmas have no
old form to derive. (Modifying or deleting an existing lemma is *not* this
sub-skill's job: that is a refactor/behavior change, handled by
`isabelle_prover_haskell` / `isabelle_prover_c` or a standalone refactor PR.)

## Targets

| Path | Session |
|---|---|
| `spec/abstract/**` | `ASpec` |
| `proof/invariant-abstract/**` | `AInvs` |
| `proof/refine/**` (`_R` lemmas; statement-not-tactic changes only) | `Refine` |

## Anti-pattern

Changing the **definition** of a kernel operation in `spec/abstract/**` is
not spec strengthening — it modifies observable behavior and belongs in the
`isabelle_prover_haskell` / `isabelle_prover_c` flow. This sub-skill only
changes **statements about** existing definitions. The single exception is a
*derived* `definition` naming an expression already used unfolded throughout
the file — a renaming, not a behavior change.

## Tools (one-line each)

| Tool | Role |
|---|---|
| `spec-strengthen/strengthen.sh` | **Main entry.** Drives scan → delivery-gate → trial → impact → [apply] → archive. |
| `spec-strengthen/scripts/spec_slot_hints.py` | Mechanical Q/P detector: redirect-shape taxonomy (inline/named/exactness) + unused-premise differential signal. Feeds `hints.json` into the agent prompt. |
| `spec-strengthen/scripts/spec_agent.py` | The agent: reads a theory + detector hints, proposes additive P/Q/F candidates (slot + delivery + new lemma) as JSON; `--repair` mode turns a trial failure + prover error into a corrected proposal. |
| `spec-strengthen/scripts/spec_delivery_gate.py` | Axis-2 delivery contract (§2.5), enforced *with evidence* — verifies named-realized consumers, block downstream-in-ledger, P/Q+wp escalation. Cheap static reject before any build. |
| `spec-strengthen/scripts/spec_wp_escalation.sh` | Runs the §2.5.1 3-round multi-file regression → an escalation record that admits a P/Q+wp candidate (`strengthen.sh --escalation`). |
| `spec-strengthen/scripts/spec_delivery_lifecycle.py` | Periodic ledger sweep — advances `pending` planned candidates to `realized` (consumer landed) or `orphan` (grace elapsed). |
| `$ISA_SCRIPTS/check-theory.sh` | Only verification gate. Builds the patched file through Isabelle; one pass verifies the new lemma. |
| `spec-strengthen/scripts/spec_impact.py` | Verdict + wall gate. With `--measurement-out FILE` emits the rule-5 audit JSON. |
| `spec-strengthen/scripts/spec_range_apply.py` | Non-destructive range-apply used to render dry-run `patch.diff`. |

Legacy mechanical detectors (`spec_frame_gap.py`, `spec_candidates.py`) and
the old per-pattern `run.sh` remain for reference, but the agent-driven
`strengthen.sh` is the current path.

## References (read on demand)

| When | File |
|---|---|
| The 2-axis design (P/Q/F slots × wp/named/block delivery), full rationale | [`reports/spec-strengthen/execute-additive-design.md`][design] |
| F-slot mechanical preflight (4 gates) | [`reports/spec-strengthen/pattern-G-automation-pipeline.md`](../../../reports/spec-strengthen/pattern-G-automation-pipeline.md) |
| Candidate shapes, worked case studies (success + failure) | [`references/spec-strengthen-playbook.md`](references/spec-strengthen-playbook.md) |
| Which sessions rebuild for which change | [`references/spec-downstream-map.md`](references/spec-downstream-map.md) |
| Refinement-level strengthening (`corres` / `ccorres`) | [`references/refinement-proofs.md`](../isabelle_prover/references/refinement-proofs.md) |

[design]: ../../../reports/spec-strengthen/execute-additive-design.md
