# Sledgehammer Guide

Sledgehammer is Isabelle's automated proof discovery tool. It dispatches goals to external ATPs and SMT solvers, then reconstructs machine-checked proofs.

## Basic Usage

```isabelle
lemma "P x"
  by sledgehammer
```

Sledgehammer runs multiple provers in parallel and returns the first successful reconstruction.

## Prover Configuration

### Default Provers

Isabelle ships with these provers enabled by default:
- **E** — Equational reasoning, good for rewriting-heavy goals
- **SPASS** — Superposition prover, strong on first-order logic
- **Vampire** — Very strong general-purpose ATP
- **Z3** — SMT solver, good for arithmetic and quantifier-free theories
- **CVC5** — SMT solver, complementary to Z3

### Selecting Provers

```isabelle
sledgehammer [provers = e spass vampire z3 cvc5]
```

For arithmetic-heavy goals, prioritize SMT:
```isabelle
sledgehammer [provers = z3 cvc5 e]
```

For pure logic goals, prioritize ATPs:
```isabelle
sledgehammer [provers = vampire e spass]
```

## Timeout Tuning

```isabelle
(* Default: 30 seconds per prover *)
sledgehammer [timeout = 60]

(* For very hard goals *)
sledgehammer [timeout = 120]

(* Quick check before committing to full search *)
sledgehammer [timeout = 10]
```

**Budget guidance:**
- First attempt: default timeout (30s)
- If no proof found: increase to 60s with more premises
- If still failing: 120s is the practical maximum — beyond this, restructure the goal

## Premise Selection

Sledgehammer automatically selects relevant premises from the current theory context. You can guide it:

### Adding Premises

```isabelle
sledgehammer [add: lemma1 lemma2 lemma3]
```

### Removing Premises (reduce noise)

```isabelle
sledgehammer [del: irrelevant_lemma]
```

### Using Only Specified Premises

```isabelle
sledgehammer [only: lemma1 lemma2]
```

### Finding Good Premises

1. Use `find_theorems` to identify relevant lemmas
2. Check what `simp` uses: `using [[simp_trace]]`
3. Look at the theory imports for available facts

## Reconstruction Methods

Sledgehammer returns proofs using these methods (in order of preference):

| Method | Reliability | Speed | When Used |
|--------|-------------|-------|-----------|
| `metis` | High | Fast | Default reconstruction |
| `meson` | High | Fast | Pure first-order goals |
| `smt` | Medium | Slow | When metis/meson fail |
| `presburger` | High | Fast | Pure arithmetic |

### Example Outputs

```isabelle
(* Sledgehammer might return: *)
by (metis add_0 mult_commute)
by (meson allI impI)
by (smt (verit) lemma1 lemma2)
```

**Prefer `metis`/`meson` over `smt`** — they are kernel-checked and more robust across Isabelle versions.

## Common Patterns

### When Sledgehammer Succeeds Quickly

- First-order logic with known lemmas
- Arithmetic goals with clear bounds
- Set theory with standard operations
- Equational reasoning

### When Sledgehammer Struggles

- Goals requiring induction (use `induct` first, then sledgehammer on subgoals)
- Goals with complex `let`/`where` bindings (unfold first)
- Higher-order goals (try `blast` or `auto` instead)
- Goals requiring case analysis (use `cases` first)

### Decomposition Strategy

When sledgehammer fails on a complex goal:

```isabelle
lemma complex_goal: "P x --> Q x --> R x"
proof -
  have step1: "P x --> intermediate x"
    by sledgehammer  (* Easier subgoal *)
  have step2: "intermediate x --> Q x --> R x"
    by sledgehammer  (* Easier subgoal *)
  show ?thesis using step1 step2 by blast
qed
```

## Integration with Other Tools

### After try0 Fails

```
try0 → (no result) → sledgehammer → (metis proof)
```

### Before Manual Proof

Use sledgehammer to discover which lemmas are needed, even if the reconstruction fails:
```isabelle
(* Sledgehammer output shows relevant lemmas even on timeout *)
(* "Try this: by (metis lemma1 lemma2)" or *)
(* "Found relevant lemmas: lemma1, lemma2, lemma3" *)
```

Then use those lemmas in a manual proof.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| All provers timeout | Decompose goal, sledgehammer subgoals |
| `metis` reconstruction fails | Try `smt` or increase `metis_timeout` |
| Wrong lemmas selected | Use `[add:]` / `[del:]` to guide premise selection |
| Proof works in ATP but not reconstructed | Try `smt (verit)` or manual `metis` with fewer lemmas |
| Slow on large theories | Use `[only: ...]` to limit search space |

## See Also

- [tactic-patterns.md](tactic-patterns.md) — When to use sledgehammer vs other tactics
- [sorry-filling.md](sorry-filling.md) — Sledgehammer in the sorry-filling workflow
- [find-theorems-guide.md](find-theorems-guide.md) — Finding premises to feed sledgehammer
