# spec-0011 — tools: witness generator + trial-based premise probe (Meta PR record)

| Field | Value |
|---|---|
| **Variant** | Meta PR (rule 5 simplified — no source change to verification/l4v) |
| **Branch** | `spec-strengthen` |
| **Date** | 2026-06-02 |
| **Verdict** | applied |

## Scope

User 2026-06-02 reviewed the spec-strengthening toolchain and
identified 5 weaknesses. After triage we agreed to ship two of the
four proposed remediations:

- **Tool 3** — auto-generate `<name>_old` witness skeleton (cheap,
  closes the loop with the playbook's witness-rule-by-triple-shape
  table that 0010 added).
- **Tool 2** — Isa-REPL premise-usage probe to reduce Pattern C
  false-positive trial-and-error.

Tool 1 (Isabelle/PIDE parser replacement for regex parser) and
Tool 4 (Isabelle-aware usage graph replacing grep) are deferred.

## Tool 3 — `spec_witness_gen.py` (ships as-is)

### Design

Input: a `check-theory.sh`-format patch + the unmodified theory
file. Output: the `<name>_old` witness lemma the patch needs
(shape 1 — modify), or an explanation of why no witness applies
(shape 2 — additive, shape 3 — deletion, or weakening rejection).

Algorithm:
1. Parse patch via `parse_patch()` from `spec_impact.py`.
2. Parse OLD theory via `parse_thy_lemmas()`.
3. Apply patch in memory; parse NEW lemma set.
4. Set arithmetic on lemma names → identify modify / additive /
   delete / weakening shapes.
5. For each shape-1 (modify) lemma, compare pre/post conjunct
   counts to determine direction (pre-weaken vs post-strengthen).
6. Detect triple shape (`valid` / `validE` / `validE_R` /
   `validE_E`) via regex on the raw lemma source window.
7. Table lookup `(shape, direction) → rule` to pick the Hoare
   monotonicity rule. Falls back to a `sorry`-marked TODO skeleton
   if the lookup misses (never emits a confidently-wrong witness).
8. Render `lemma <name>_old: "<old>" by (rule <rule>[OF <name>])
   simp` for each modification.

### Verification

Smoke-tested against the 3 historical patches in `logs/`:

| Patch | Expected shape | Actual verdict | Notes |
|---|---|---|---|
| `exp_smoke_C.patch` | 1-modify, pre-weaken, `valid` shape | shape 1, rule = `hoare_weaken_pre` | ✓ |
| `exp_smoke_G.patch` | 2-additive | shape 2 (no witness) | ✓ |
| `exp_smoke_weakening.patch` | weakening | weakening (exit 3) | ✓ |

**Ground-truth check on Pattern C**: the generated witness was
diffed against the historical hand-written witness in
`exp_smoke_C.patch` (verified previously by `check-theory.sh`
returning `OK (76137ms)`).

```
$ diff <(generated, whitespace-normalized) <(historical, whitespace-normalized)
✓ SEMANTICALLY IDENTICAL
```

Both produce the same `hoare_weaken_pre[OF ...]` rule application
on the same OLD statement. The generated output is provably valid
Isabelle text.

### Limitations

- Edge cases inherited from the regex parser (locale-scoped
  lemmas, schematic_goals, multi-line statement bundles) silently
  fail or emit a TODO skeleton — never a confidently-wrong
  witness.
- Mixed-direction patches (both pre weakened AND post
  strengthened) emit a TODO with rule guess noted; agent picks.

## Tool 2 — `spec_premise_probe.py` (v0 rejected, v1 ships)

### v0 (rejected — substring heuristic on subgoals)

**Approach**: load theory via Isa-REPL, step through proof,
inspect goal state after each step, return `likely-unused` iff
the suspect premise name never appears in any subgoal text.

**Differential test result** (the data that killed v0):

| Lemma | Premise | True nature | v0 verdict |
|---|---|---|---|
| `unbind_maybe_notification_not_bound` | `valid_objs` | unused (exp_smoke_C ground-truth) | `appears-in-subgoal` |
| `unbind_maybe_notification_not_bound` | `ntfn_at` | load-bearing intuition | `appears-in-subgoal` |

Both verdicts identical → v0 has zero discriminative power. Root
cause: Isabelle keeps the full Hoare-triple precondition visible
in every intermediate subgoal printout, so any premise name in
the original statement banner trivially appears in all goal
captures regardless of whether it's actually consumed by a
tactic. `wp`/`simp` consume premises via unification — invisible
in goal text. Refining the heuristic (count occurrences,
post-initial-only, tactic-arg matching) all failed for similar
reasons.

User direction 2026-06-02: "丢掉探针错测改为 TRIAL 式探针".

### v1 (ships) — TRIAL via synthesized lemma + Isa-REPL

**Approach**: ground-truth check, not heuristic.

1. Parse the target lemma. Extract its precondition's top-level
   conjuncts (handling `\<lambda>s.` binder + `\<and>` / ` and `
   splits).
2. Drop the suspect conjunct. Reassemble the precondition (using
   `\<top>` if dropping leaves it empty). Build a synthesized
   lemma `<name>_probe` with the weakened pre but unchanged body
   and post.
3. Init Isa-REPL with the theory's session (heap load ≈ 30s).
4. Step the file's preamble up to (NOT including) the target
   lemma. Each step is fast post-heap-load.
5. Send the synthesized lemma header via `_step`. Then send each
   tactic of the **original** proof body, one at a time.
6. Verdict:
   - All proof steps succeed → `likely-unused` (the original
     proof closes without the premise).
   - Any step fails → `load-bearing` (with the failing step + the
     residual subgoal text in the output, for diagnosis).

This is the same correctness signal as `check-theory.sh --patch`
on a hand-written drop-premise patch — just driven through
Isa-REPL.

### Verification

Three differential cases, two files (Finalise_AI.thy +
CSpace_AI.thy), per-probe wall measured:

| Lemma | Premise | Ground truth | v1 verdict | Wall |
|---|---|---|---|---|
| `unbind_maybe_notification_not_bound` | `valid_objs` | unused (exp_smoke_C) | `likely-unused` ✓ | 57s |
| `unbind_maybe_notification_not_bound` | `ntfn_at` | unused (vacuous-truth in nondet monad) | `likely-unused` ✓ | 57s |
| `lsfco_cte_at` | `invs` | load-bearing | `load-bearing` ✓ | 199s |

Output excerpt for the load-bearing case proves diagnostic depth:

```
verdict: load-bearing
reason: proof failed at step 5/5 (`done`) after dropping `invs`
        — premise is consumed by this tactic.
failure_msg: failed for prove the goal using the tactic `done`.
             Get msg: Failed to finish proof:
             goal (1 subgoal):
              1. ⋀s. ⟦s ⊢ cap; is_cnode_cap cap; ...⟧ ⟹ ...
synthesized_pre_preview: "⟨valid_cap cap⟩
  lookup_slot_for_cnode_op bl cap ref depth
  ⟨λrv. cte_at rv⟩,-"
```

The residual subgoal explicitly shows what `invs` was needed
for — info that a raw `check-theory.sh --patch` failure does NOT
expose.

3/3 differential cases correct. Zero false positives, zero false
negatives across the test set.

### Wall-time characterization

| File | Preamble lemma count | Init wall | Preamble wall | Total wall |
|---|---:|---:|---:|---:|
| Finalise_AI.thy (~1500 lines) | 364 steps | 32s | 57s | 57s |
| CSpace_AI.thy (~5000 lines) | 2515 steps | 32s | 199s | 199s |

**Cost-vs-`check-theory.sh` reality check**: the probe is NOT
faster than direct `check-theory.sh --patch` on a single
candidate — both pay the heap load + preamble walk. The probe's
marginal value is:
1. **Diagnostic output** — the residual subgoal text on failure
   pinpoints which tactic needed the premise.
2. **Foundation for daemon mode** — if a future version keeps
   the JVM hot across probes (one IsaREPL JVM, many
   `<lemma, premise>` queries), per-probe wall drops to ~5-10s
   after the first. Single-process v1 doesn't deliver this yet;
   future work.

### Bugs found and fixed during implementation

1. **Sliced /tmp file mismatched theory name** — initial design
   wrote a sliced theory to /tmp and passed that path. Isa-REPL
   resolves the theory name from the file basename and matched
   `tmpXXX` against the file's `theory Finalise_AI` declaration,
   crashing with `Index 1 out of bounds for length 1`. Fix: pass
   the real theory path; rely on the synthesized lemma's
   alternate name (`<name>_probe`) to avoid collision.

2. **`steps_of()` prefix artifacts** — `_parse_to_steps()`
   returns its result with the Java call's success marker
   (`"True"`) and a leading blank string before the actual
   commands. Naive iteration tried to step `True` as a tactic
   and crashed with "Outer syntax error: command expected, but
   identifier True". Fix: filter empties + literal `True`/`False`
   from the parsed step list.

3. **Crossing lemma boundaries** — initial proof-walk loop
   continued past the target lemma's `done` and stepped into
   following lemmas, picking up unrelated mentions of the
   premise name (51 captured subgoals — clearly bogus). Fix
   in v0; carried over to v1 — stop at the first `done`/`qed`/
   `sorry`/`oops` token.

### Limitations of v1

- **Conjunct dropping**: works on `\<lambda>s. C1 \<and> C2 \<and>
  ...` shapes. Multi-line preconditions with nested quantifiers,
  let-bindings, or comments may not split cleanly — the tool
  returns `parse-error` rather than guess.
- **Ambiguous matches**: if the premise NAME appears in multiple
  top-level conjuncts (e.g. both `valid_objs s` and
  `valid_objs s'`), the tool returns `uncertain` rather than
  pick one.
- **Vacuous-truth pitfall**: for failure-monadic operations like
  `get_notification`, dropping a `<obj>_at` premise can still
  pass because the function fails on a missing object, giving
  vacuous truth. The probe correctly reports `likely-unused`
  in this case (the Hoare triple IS valid without the premise),
  but the agent should consider whether the premise is needed
  for **useful callers** — that's an orthogonal consideration
  not captured by the probe. Documented in SKILL.

## Why meta-PR variant

- No `.thy`/`.hs`/`.c`/`.h` under `verification/l4v/` modified.
- New tools + SKILL doc update.
- The differential-test data above replaces measurement.json.

## What changed (live files)

| Path | Action |
|---|---|
| `tools/spec_strengthen/spec_witness_gen.py` | NEW — tool 3 |
| `tools/spec_strengthen/spec_premise_probe.py` | NEW — tool 2 v1 |
| `tools/spec_strengthen/spec_premise_probe.sh` | NEW — host wrapper for tool 2 |
| `.claude/skills/isabelle_prover_spec/SKILL.md` | +12 lines: "Optional helpers" subsection under Tools |

SKILL went 184 → 192 lines.

## Notes / follow-ups

- **Daemon mode for tool 2**: the major outstanding win is a
  long-running Isa-REPL JVM that accepts multiple
  `<lemma, premise>` probe requests. Per-probe wall drops from
  ~60s to ~5-10s after the first. Estimated effort: ~150 LOC for
  a simple JSON-over-stdin RPC; nontrivial because the JVM must
  manage clean-up between probes (reset to a checkpoint after
  each synthesized lemma to avoid namespace pollution). Tracked
  as future work; not blocking.
- **Tool 1 (PIDE parser)**: deferred. After 2 + 3 prove out in
  real use, re-evaluate whether the regex parser's remaining
  blind spots (locale-scoped lemmas, schematic_goals) hurt
  enough to justify a Scala/Isabelle integration.
- **Tool 4 (usage graph)**: deferred. grep consumer count is
  imperfect but adequate for ranking; full PIDE-based usage
  resolution is a bigger investment for marginal precision.
