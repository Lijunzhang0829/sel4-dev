# spec-0014 — add `set_cdt_cdt_update` functional postcondition (seL4-source PR)

| Field | Value |
|---|---|
| **Variant** | seL4-source PR (rule 5 full record) |
| **Branch** | `spec-strengthen` |
| **Date** | 2026-06-02 |
| **Verdict** | applied |
| **Patch shape** | 2 (additive) |
| **Impact verdict** | `additive` |
| **Acceptance** | PASS (all 4 gates) |

## What changed

`verification/l4v/proof/invariant-abstract/CSpace_AI.thy` — added a
new companion lemma right after `set_cdt_valid_pspace`:

```isabelle
lemma set_cdt_cdt_update:
  "\<lbrace>\<top>\<rbrace> set_cdt t \<lbrace>\<lambda>_ s. cdt s = t\<rbrace>"
  by (wpsimp simp: set_cdt_def)
```

The lemma states that **after `set_cdt t`, the CDT field equals
exactly `t`** — a precise functional postcondition for a kernel
operation that previously had no published lemma in this form.

The l4v submodule HEAD before this change: `00d9073f70d0` (recorded
in the audit dir so future replay can pin the baseline).

## Why this is a real strengthening (and not just noise)

`set_cdt` writes the CDT field of the kernel state. Existing
companion lemmas at this point in the file:
- `set_cdt_valid_pspace` — `set_cdt` preserves `valid_pspace`.
- Various `set_cdt_<frame>` lemmas — `set_cdt` doesn't change
  unrelated state components.

What was missing: the **direct functional fact** about what
`set_cdt t` produces. Before this change, downstream proofs that
needed `cdt s = t` after a `set_cdt t` step had to unfold
`set_cdt_def` manually. Examples in the existing tree:
- `update_cdt_cdt` — unfolds `set_cdt_def` to prove `cdt s = ...`.
- `cap_move_typ_at` — similarly unfolds.

Per the playbook [[case-5]] analysis, this is the canonical
Pattern B (missing-functional) shape: a write that produces a
deterministic value, with no lemma exposing that value as a wp
target.

## Why shape 2 (additive), not shape 1 (modify)

The lemma is **brand new**. No existing lemma carries the form
`set_cdt t \<lbrace>\<lambda>_ s. cdt s = t\<rbrace>`; we are not weakening or
deleting anything. Per SKILL §witness-contract shape 2: purely
additive companion lemmas skip the `_old` witness (no old form
to derive from).

`spec_witness_gen.py` confirmed:
```
$ python3 tools/spec_strengthen/spec_witness_gen.py \
    logs/spec-strengthen-CSpace_AI-set_cdt_cdt_update-20260602.patch \
    verification/l4v/proof/invariant-abstract/CSpace_AI.thy
SHAPE 2 (additive) — new lemma(s) set_cdt_cdt_update added; no
old form to derive. No witness required. (exit 1 — additive is
SKILL-compliant; the non-zero exit reports SHAPE selection, not
failure.)
```

## Acceptance gate trace

Per SKILL Acceptance section, all 4 gates:

1. **`check-theory.sh --patch` returns OK** — verified once with
   `--patch` (OK, 49271 ms), once again at `--apply` time (OK,
   55846 ms). Both passes verify the new lemma's proof
   (`wpsimp simp: set_cdt_def`) closes. ✓
2. **Impact tool emits no `weakening` verdict and at least one
   of `premise-weaken` / `monotone-strengthen` /
   `postcond-strengthen` / `additive`** — `spec_impact.py`
   emitted `additive` (the SKILL-listed accepting verdict for
   shape 2). ✓
3. **Trial wall ≤ baseline × 1.30** — baseline 45330 ms → trial
   49271 ms = +8.7%, well under the +30% threshold. ✓
4. **Parent SKILL 5 hard rules inherited** — no
   `sorry`/`oops`/`axiomatization`; check-theory.sh is the sole
   verification gate; PR-tracked mainline; this audit dir
   records the change; no JVM kill / heap volatility incident. ✓

## Impact on seL4 (上下游)

### Same-file wall (immediate)

| Phase | Wall | Source |
|---|---:|---|
| baseline | 45,330 ms | `check-theory.sh CSpace_AI.thy AInvs` |
| trial (with patch) | 49,271 ms | `check-theory.sh --patch ...` |
| apply (re-verifies) | 55,846 ms | `check-theory.sh --apply ...` |
| delta | +8.7% | `(trial − baseline) / baseline` |

The +8.7% file-wall cost is the one-time price of adding a new
lemma to the theory — Isabelle parses + proves the additional
`wpsimp simp: set_cdt_def` step on every CSpace_AI rebuild.
This is well within the SKILL's +30% regression cap and aligns
with the "spec strengthening trades build time for verification
strength" framing.

### Downstream consumers (immediate)

`spec_impact.py` Tier-2 grep:
- `consumers_lines: 0`
- `consumers_files: 0`

**This is expected for a fresh additive lemma.** Pattern B's
ROI is **delayed** (per playbook §"Pattern B ROI is delayed"):
the lemma's benefit accrues when future proofs cite it as
`wp set_cdt_cdt_update` instead of unfolding `set_cdt_def`
manually. Today's wall counts no win; tomorrow's refactors do.

### Downstream sessions (Refine / CRefine / etc.) — NOT rebuilt

Cross-session impact would require rebuilding Refine
(~1h17min wall) and downstream. For a purely additive ASpec
lemma, the upper-bound impact on Refine is: **no proof in
Refine breaks** (adding a new lemma can only add provability
options, never remove them); the marginal wall increase is the
~50-100ms cost of Refine parsing the new lemma definition once
during its theory-graph load. Not rebuilt this session — judged
not worth the wall cost for a single additive lemma.

If a future refactor pass adopts `set_cdt_cdt_update` in `update_cdt_cdt`
/ `cap_move_typ_at` / similar consumers, a Refine rebuild
becomes worthwhile to measure the cleanup's ROI. That's a
separate downstream-cleanup PR, not this one.

### Why no Tier-1 strength_score

`spec_impact.py` reports `strength_score: 0.0`. The SSS is
zero because shape 2 adds a new lemma but doesn't structurally
weaken any precondition or strengthen any existing
postcondition. Per SKILL Acceptance #2, `additive` is one of
the four accepting verdicts and is sufficient on its own; SSS
is not gating.

## PR description fields (per SKILL Step 6)

| Field | Value |
|---|---|
| Lemma | `set_cdt_cdt_update` (new) |
| File | `verification/l4v/proof/invariant-abstract/CSpace_AI.thy` (inserted after line 430) |
| Baseline wall entry | `reports/golden-baseline/walls.json#AInvs` — `45,330 ms` (recorded in this dir's `measurement.json#baseline_ref`) |
| Trial wall from `--apply` | `55,846 ms` (the `--apply` re-verification run; the `--patch` trial measurement was `49,271 ms`) |
| Experiment ID | `0014-set-cdt-cdt-update-additive` |

## Files in this audit dir

| File | Source |
|---|---|
| `patch.diff` | `git -C verification/l4v diff` after `check-theory.sh --apply` (the canonical, replayable unified diff) |
| `command.sh` | re-runnable measurement command; re-builds the range-replace patch on the fly, runs baseline + trial + spec_impact |
| `measurement.json` | emitted by `spec_impact.py --measurement-out` |
| `decision.md` | this file |

## Notes / follow-ups

- The l4v submodule pointer is **not** advanced by this PR. The
  source change sits in the submodule working tree; the parent
  repo's commit on `spec-strengthen` records the audit dir +
  the range-replace patch under `logs/`. Promoting this change
  to the upstream l4v repository is a separate concern (an
  upstream PR on the l4v project's bug tracker), not handled
  by this sub-skill.
- A follow-up cleanup PR could rewrite `update_cdt_cdt` and
  `cap_move_typ_at` to use `set_cdt_cdt_update` instead of
  manually unfolding `set_cdt_def`. That measures Pattern B's
  *real* downstream ROI. Out of scope here.
- This is the first **applied** spec strengthening on this
  branch (commits 0008-0013 were tools + SKILL infrastructure,
  not verification/l4v changes). Subsequent experiments (0015+)
  will follow the same 4-file pattern.
