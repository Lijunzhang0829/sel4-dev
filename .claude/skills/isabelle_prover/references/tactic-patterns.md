# Tactic Patterns by Goal Type

Quick reference for choosing Isabelle/HOL tactics based on goal structure.

## Goal Structure Patterns

### Equality (`a = b`)

**Primary tactics:**
- `simp` / `simp add: lemmas` — Simplification with rewrite rules
- `auto` — Combines simp + classical reasoning
- `subst` — Substitution when equality is in assumptions

**Rewriting:**
- `subst_tac` — Substitute equal terms
- `(simp only: lemma)` — Controlled rewriting
- `(unfold def_name)` — Unfold specific definitions

### Universal Quantifier (`ALL x. P x`)

- `fix x` — Introduce universally quantified variable (Isar)
- `(rule allI)` — Introduction rule (apply style)
- `(intro x)` — Introduce variable

### Existential Quantifier (`EX x. P x`)

- `(rule exI[where x="witness"])` — Provide witness explicitly
- `(rule exI)` then prove — Let unification find witness
- `auto` — Sometimes finds witness automatically

### Implication (`P --> Q`)

- `assume "P"` — Assume hypothesis (Isar)
- `(rule impI)` — Introduction rule (apply style)
- `(erule impE)` — Elimination from assumption

### Conjunction (`P & Q`)

- `(rule conjI)` — Split into two subgoals
- `auto` — Often handles conjunctions directly
- `(erule conjE)` — Destruct conjunction in assumptions

### Disjunction (`P | Q`)

- `(rule disjI1)` / `(rule disjI2)` — Prove left/right
- `(erule disjE)` — Case split on disjunction in assumptions
- `(cases "P | Q")` — Explicit case analysis

### Inequality (`<`, `<=`, `>`, `>=`)

- `arith` — Linear arithmetic over ordered fields
- `linarith` — Linear arithmetic (from HOL-Library)
- `omega` — Integer/natural number arithmetic (Presburger)
- `auto` — Sometimes sufficient for simple inequalities

### Negation (`~ P`)

- `(rule notI)` — Assume P, derive False
- `(rule ccontr)` — Classical contradiction
- `(erule notE)` — From ~P and P, derive False

## Domain-Specific Patterns

### Set Theory

Goal contains: `Un`, `Int`, `UNION`, `member`, `subset`

- `auto` / `blast` — Set reasoning
- `(force simp add: member_def)` — With membership definitions
- `(rule subsetI)` — Prove subset

### Natural Number Arithmetic

- `(induct n)` — Structural induction
- `arith` / `omega` — Arithmetic goals
- `simp add: algebra_simps` — Algebraic simplification

### Lists

- `(induct xs)` — Structural induction on lists
- `(cases xs)` — Case split (nil vs cons)
- `simp add: list.map list.filter` — List operation lemmas

## General Tactics (Always Worth Trying)

### Automation Power Ranking

| Tactic | Strength | Speed | When to Use |
|--------|----------|-------|-------------|
| `simp` | Medium | Fast | Rewriting, simplification |
| `auto` | High | Medium | First-order + simp |
| `blast` | High | Medium | Pure classical logic |
| `fast` | Medium | Fast | Quick tableau proof |
| `force` | High | Slow | auto + backtracking |
| `arith` | Domain | Fast | Linear arithmetic |
| `omega` | Domain | Fast | Presburger arithmetic |
| `metis` | High | Medium | With specific lemmas |
| `meson` | High | Medium | Pure first-order logic |
| `smt` | Very High | Slow | SMT solver reconstruction |

### Structuring (Isar)

- `have "P" by tactic` — Intermediate result
- `moreover ... ultimately` — Chain of facts
- `obtain x where "P x" by tactic` — Existential elimination
- `proof (rule someRule)` ... `qed` — Rule-guided proof

### Hypothesis Work

- `(erule conjE)` — Destruct conjunction
- `(erule exE)` — Destruct existential
- `(drule mp)` — Forward reasoning with implication
- `assumption` — Use existing hypothesis directly

### Application

- `(rule lemma)` — Apply lemma backwards
- `(erule lemma)` — Apply and consume first assumption
- `(drule lemma)` — Forward reasoning
- `(frule lemma)` — Forward reasoning (keep original)

## Workflow Tips

1. **Try automation first:** `try0` runs simp/auto/blast/fast/force/arith in parallel
2. **Sledgehammer for hard goals:** External ATPs + premise selection
3. **Introduce/destruct:** `fix`, `assume`, `obtain`, `cases`
4. **Break it down:** `have`, intermediate lemmas
5. **Search HOL library:** `find_theorems` with patterns

## See Also

- [sledgehammer-guide.md](sledgehammer-guide.md) — Sledgehammer usage and configuration
- [isar-patterns.md](isar-patterns.md) — Structured Isar proof patterns
