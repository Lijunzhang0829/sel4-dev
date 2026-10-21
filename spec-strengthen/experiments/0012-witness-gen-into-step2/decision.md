# spec-0012 — promote spec_witness_gen.py into Step 2 mainline (Meta PR record)

| Field | Value |
|---|---|
| **Variant** | Meta PR (rule 5 simplified — no source change to verification/l4v) |
| **Branch** | `spec-strengthen` |
| **Date** | 2026-06-02 |
| **Verdict** | applied |

## Scope

User 2026-06-02 asked whether the two tools landed in [[0011]]
should be integrated into the spec-strengthening workflow.

**Decision (user-confirmed)**:
- **tool 3** (`spec_witness_gen.py`) → **promote into Step 2 mainline**.
- **tool 2** (`spec_premise_probe.py`) → **keep as optional diagnostic**.

## Reasoning

### Tool 3 — yes, promote

- Zero infrastructure cost: pure Python text manipulation, no Isa-REPL,
  no docker exec, runs in <1s.
- Deterministic + correctness-verified: 3/3 historical smoke patches
  classified correctly in [[0011]]; the generated Pattern C witness
  is byte-identical (whitespace-normalized) to the historical
  hand-written one that already passed `check-theory.sh` at 76s.
- Directly prevents the 0008-style failure mode: an agent
  picking witness rules by name suffix (e.g. inventing
  `hoare_post_imp_R`) is a real, observed silent break.
- Graceful degradation: when the parser can't classify (locale-
  scoped lemmas, schematic_goals, mixed direction), the tool
  emits a `sorry`-marked TODO rather than a confidently-wrong
  witness. Hand-written witness is always allowed as fallback.

The combination of "cheap + deterministic + correctness-
verified + safe degradation" makes promotion essentially free.

### Tool 2 — no, keep optional

- v1 wall ≈ 60-200s per probe (init + preamble walk) — **same
  cost as `check-theory.sh --patch`** in single-process mode.
  No actual speedup over the existing verification gate.
- Real speedup requires daemon mode (one IsaREPL JVM, many
  `<lemma, premise>` queries) — not yet built. Until then,
  forcing the probe into the workflow adds 60-200s overhead
  per `unused-premise` candidate without saving anything.
- Probe's value-add (residual subgoal text on `load-bearing`) is
  diagnostic — only useful when `check-theory.sh` has already
  failed and you want to know why. That's not a mainline
  position.

Optional diagnostic positioning is the honest framing.

## What changed

### `.claude/skills/isabelle_prover_spec/SKILL.md`

**Step 2 (Workflow)** — added a paragraph after the patch-shape
enumeration:

> For shape 1, run `python3 tools/spec_strengthen/
> spec_witness_gen.py <patch> <theory.thy>` against the partial
> patch (modified lemma only) to emit the `<name>_old` witness.
> If the tool returns a clean witness (exit 0, no `sorry` TODO),
> paste it verbatim into the patch. If it falls back to a TODO
> skeleton (unrecognized triple shape, mixed direction, or
> parse error), consult `references/spec-strengthen-playbook.md`
> §"Witness rules by Hoare triple shape" and write the witness
> by hand. Hand-written witness is always allowed — the tool is
> a guard against picking the wrong rule by name-pattern, not a
> mandatory step.

**Tools section** — restructured into two clear tiers:

- **Mainline tools** (now 4 rows including `spec_witness_gen.py`):
  added with description "Step 2 helper — emits the `<name>_old`
  witness for shape-1 patches by looking up the correct Hoare
  monotonicity rule from triple shape + direction."
- **Optional diagnostic** (formerly "Optional helpers"):
  now contains only `spec_premise_probe.sh`, with explicit
  framing as a diagnostic for `load-bearing` failure analysis
  rather than a happy-path filter. The "future daemon mode is
  where the real speedup lives" caveat stays.

SKILL went 192 → 205 lines (+13 for the Step 2 paragraph and
the restructured Tools section).

## Why meta-PR variant

- No `.thy`/`.hs`/`.c`/`.h` under `verification/l4v/` modified.
- Pure documentation reorganization to promote a previously-
  optional tool into the workflow's main path.
- Smoke test below replaces measurement.json.

## Smoke test

Regression check on `spec_witness_gen.py` after the SKILL
promotion (no tool code changed; just verifying it still
behaves):

```
$ python3 tools/spec_strengthen/spec_witness_gen.py \
    logs/exp_smoke_C.patch \
    verification/l4v/proof/invariant-abstract/Finalise_AI.thy
# Shape 1 (modify) — witness lemma(s) below:

lemma unbind_maybe_notification_not_bound_old:
  "\<lbrace>...valid_objs s \<and> sym_refs (state_refs_of s)...\<rbrace>
     unbind_maybe_notification ntfnptr
   \<lbrace>...\<rbrace>"
  by (rule hoare_weaken_pre[OF unbind_maybe_notification_not_bound]) simp
```

✓ Matches the [[0011]] verified output verbatim. No tool change;
only the SKILL position changed.

## Notes / follow-ups

- The Step 2 paragraph deliberately stops short of making the
  tool **mandatory** ("Hand-written witness is always allowed").
  This keeps the door open for the parser-edge cases (locale-
  scoped lemmas, schematic_goals) without requiring a workflow
  exception clause.
- The Tools-section restructure now matches the visual
  hierarchy of the workflow: 4 mainline tools (check-theory.sh,
  candidates, witness_gen, impact) + 1 diagnostic (premise_probe).
  Sub-skill rule 5 inheritance unchanged.
- Daemon-mode for `spec_premise_probe.py` remains the
  blocker for any future "promote tool 2 into Step 1.5"
  decision. Until then it stays in the diagnostic tier.
