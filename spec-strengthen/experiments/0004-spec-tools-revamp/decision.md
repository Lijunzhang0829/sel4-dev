# spec-0004 — spec_impact + rank_candidates revamp (Meta PR record)

| Field | Value |
|---|---|
| **Variant** | Meta PR (rule 5 simplified — no source change to verification/l4v) |
| **Branch** | `spec-strengthen` |
| **Date** | 2026-05-31 |
| **Verdict** | applied |

## Scope

Substantive rewrite of two Python tools after user code review (the
2026-05-31 review identified 5 issues in `spec_impact.py` and 7 in
`rank_candidates.py`; both were diverging from the post-Taxonomy
SKILL.md).

### `tools/critical_path/spec_impact.py`

Was: token-count heuristics drove the verdict (`postcond_grew` if
post tokens increased). Easy to fool, didn't emit SSS, had no
wall-time gate, accepted any new lemma as `additive`.

Now:

1. **Wall-time gate** — `trial_wall_ms > baseline_wall_ms × 1.30`
   → `wall_gate_pass = False`. Final `gate_pass` factors this in.
2. **Structural diff** — token counts demoted to informational notes.
   Verdict driven by `structural_diff()`: strips lambda binders via
   `extract_predicate_body`, splits on top-level ` and ` / `\<and>`
   honoring paren depth, normalizes conjuncts, set-diffs pre and
   post conjunct sets, counts added frame facts, detects `≤ → =`
   swap.
3. **Pattern B/G/E shape check for added lemmas** —
   `detect_added_pattern()` inspects a brand-new lemma and returns
   `'B' / 'G' / 'E' / None`. If `None`, the verdict is
   `unclassified-add` (not `additive`); gate fails for unmotivated
   aux lemmas.
4. **SSS + breakdown** — per SKILL §6:
   `SSS = removed_premises + added_post_facts + 0.5 × added_frame_facts
   + swap_le_to_eq`. JSON output includes per-delta `strength_score`
   + `score_breakdown` + aggregate at report level.
5. **Derivability witness detection** —
   `detect_derivability_witness(patch_text)` scans the raw patch for
   `lemma <X>_old:` constructs invoking `hoare_pre`,
   `hoare_weaken_pre`, `hoare_strengthen_post`,
   `hoare_strengthen_postE_R`, `hoare_post_imp`, or
   `hoare_post_imp_R`. Tier 1 verdicts trigger an advisory check;
   actual derivability proof runs in `check-theory.sh`.

### `tools/critical_path/rank_candidates.py`

Was: hard-coded Pattern order `C → B → A`, ranked by raw
`consumer_lines` only, hardcoded session = AInvs, broad-regex done
detection matching any backtick name in a Pattern-letter line.

Now:

1. **WEIGHTS table + PATTERN_ORDER**:
   `C=5, G=4, D=3, B=3, E=2, A=1`, output order
   `C → G → D → B → E → A`.
2. **ROI formula**: `roi_score = consumer_lines × tier_weight`. Sort
   key `(done, -roi_score, -consumer_lines)`. Each row reports
   `tier`, `tier_weight`, `roi_score`.
3. **G/D/E placeholders**: even without auto detector, each pattern
   gets a section telling the user how to find candidates manually.
4. **Done detection includes G**: `find_done_lemmas()` no longer
   filters by pattern letter.
5. **Tighter done regex**: `DONE_CONTEXT_RE` requires the line to
   contain `applied|verdict|tier T1-3|<verdict name>|OK`.
6. **Consumer grep excludes self-def line**: `grep_consumers` takes
   `self_file` + `self_line` and filters the lemma's own definition.
7. **Session column + `--target` flag**: session auto-derived via
   `SESSION_BY_PATH_PREFIX`; `--target` chooses `spec / ainvs /
   refine / all` scan roots.

## Smoke tests (in lieu of measurement.json)

Ran 2026-05-31:

1. **Pattern C + derivability** (`unbind_maybe_notification_not_bound`):
   - Verdict: `premise-weaken` ✓
   - SSS: 2.0 ✓
   - Wall: +7.2% within +30% ✓
   - Derivability witness: 1 found ✓
   - Gate: **PASS ✓**

2. **Pattern G (frame, `set_cdt_machine_state`)**:
   - Verdict: `additive` (correctly recognized G shape) ✓
   - Wall: +0.6% ✓
   - Gate: **PASS ✓**

3. **Synthetic weakening** (add a premise):
   - Verdict: `weakening` ✓
   - Gate: **FAIL ✗** (exit 1) ✓

4. **Wall +50% on valid Pattern C**:
   - Verdict still `premise-weaken`
   - Wall: +50.0% > 30% → `wall_gate_pass = False`
   - Gate: **FAIL ✗** (exit 1) ✓

5. **rank_candidates** (--target ainvs):
   - Scanned 4737 lemmas
   - Pattern order C → G → D → B → E → A ✓
   - Tier weights + ROI columns ✓
   - Sessions auto-derived ("AInvs") ✓
   - G/D/E placeholders rendered ✓
   - Top Pattern C: `lsfco_cte_at` ROI 445 (= 89 × 5) ✓

## Why meta-PR variant

- Only `.py` files under `tools/critical_path/` modified.
- No `.thy` / `.hs` / `.c` / `.h` under `verification/l4v/` touched.
- No Hoare-triple to verify; gate is the smoke-test suite above.
- Matches rule 5 Meta-PR variant in
  `.claude/skills/isabelle_prover/SKILL.md`.

## Known follow-ups (not blocking)

- `spec_strengthen_scan.py`'s Pattern B detector mis-flags Isar
  keyword `shows` (saw `shows: KHeap_AI.thy:1117` with 7165
  "consumers"). Fix is in the lemma-header parser of
  `spec_strengthen_scan.py`, not in these two tools.
- Pattern G/D/E auto-detectors still TODO (placeholders only). G is
  the highest priority — sketched in §3 of
  `references/spec-strengthen-patterns.md`.
