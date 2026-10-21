# Common Isabelle/HOL Errors

This reference provides detailed explanations and fixes for the most common errors encountered when proving in Isabelle/HOL.

## Quick Reference Table

| Error | Cause | Fix |
|-------|-------|-----|
| **"Type unification failed"** | Type mismatch in term | Add type annotations, fix argument types |
| **"Undefined constant"** | Missing import or typo | Add theory import or qualify name |
| **"Failed to finish proof"** | Proof method didn't close goal | Check remaining subgoals, strengthen tactic |
| **"Illegal application of command"** | Wrong proof state | Fix proof/qed structure |
| **"Outer syntax error"** | Isar syntax error | Fix keywords, quotes, parentheses |
| **"No subgoals!"** | Proof already complete | Remove redundant tactics |
| **"Empty result sequence"** | Tactic found no applicable rule | Try different tactic or provide more lemmas |
| **"Undeclared variable"** | Variable not in scope | Use `fix`/`assume` or check quantifier structure |
| **"Bad type for free variable"** | Polymorphism issue | Add explicit type constraint |

---

## Detailed Error Explanations

### 1. Type Unification Failed

**Error message:**
```
Type unification failed: ... :: nat vs ... :: int
```

**What it means:** Two terms have incompatible types that Isabelle cannot unify.

**Common scenarios:**
- Mixing `nat` and `int` in arithmetic
- Wrong argument order in function application
- Polymorphic lemma instantiated at wrong type

**Solutions:**

```isabelle
(* Pattern 1: Explicit type annotation *)
have h: "(x::nat) + y = z" by simp

(* Pattern 2: Coercion *)
have h: "int x + y = z" by simp

(* Pattern 3: Fix polymorphic lemma instantiation *)
have h: "P (x::nat)" by (rule lemma_name[where 'a=nat])
```

### 2. Undefined Constant

**Error message:**
```
Undefined constant: "foo"
```

**What it means:** The name is not in scope — either not imported or misspelled.

**Solutions:**

```isabelle
(* Fix 1: Add import *)
theory MyTheory
imports Main "HOL-Library.Multiset"
begin

(* Fix 2: Qualify the name *)
have h: "List.append xs ys = zs" by simp

(* Fix 3: Check spelling *)
(* Common mistakes: lenght vs length, appedn vs append *)
```

### 3. Failed to Finish Proof

**Error message:**
```
Failed to finish proof
```

**What it means:** The proof method (`by auto`, `by simp`, etc.) did not close all subgoals.

**Solutions:**

```isabelle
(* Diagnose: see remaining goals *)
lemma "P x"
  apply auto  (* Use apply instead of by to see remaining goals *)

(* Fix 1: Add more lemmas to automation *)
by (auto simp add: needed_lemma1 needed_lemma2)

(* Fix 2: Handle remaining cases manually *)
proof -
  show ?thesis
    apply auto
    apply (cases x)
     apply simp
    apply simp
    done
qed

(* Fix 3: Use stronger automation *)
by (force simp add: lemmas)
by (metis lemma1 lemma2)
```

### 4. Illegal Application of Command

**Error message:**
```
Illegal application of command "have" in proof mode
```

**What it means:** The proof structure is broken — commands appear in wrong context.

**Common causes:**
- Missing `proof -` before `have`/`show` sequence
- Extra or missing `qed`/`done`
- Mixing apply-style and Isar-style incorrectly

**Solutions:**

```isabelle
(* BAD: have without proof block *)
lemma "P x"
  have "Q x" by auto  (* ERROR *)

(* GOOD: proper Isar structure *)
lemma "P x"
proof -
  have "Q x" by auto
  then show ?thesis by blast
qed

(* BAD: done after qed *)
lemma "P x"
proof -
  show ?thesis by auto
qed
done  (* ERROR: already closed *)
```

### 5. Outer Syntax Error

**Error message:**
```
Outer syntax error: ... expected
```

**Common causes and fixes:**

```isabelle
(* Missing quotes around propositions *)
(* BAD *)  lemma P x
(* GOOD *) lemma "P x"

(* Wrong keyword *)
(* BAD *)  proof (rule conjI)  show A  show B
(* GOOD *) proof (rule conjI)  show "A" by auto next show "B" by auto qed

(* Missing 'next' between subgoals *)
(* BAD *)
proof (induct n)
  case 0 show ?case by simp
  case (Suc n) show ?case by auto  (* ERROR *)
qed

(* GOOD *)
proof (induct n)
  case 0 show ?case by simp
next
  case (Suc n) show ?case by auto
qed
```

### 6. Empty Result Sequence

**Error message:**
```
empty result sequence -- no applicable rules
```

**What it means:** A tactic like `rule`, `erule`, or `drule` found no matching rule.

**Solutions:**
```isabelle
(* Check the goal shape matches the rule *)
(* BAD: conjI needs goal "A & B" *)
lemma "A | B"
  apply (rule conjI)  (* ERROR: goal is disjunction, not conjunction *)

(* GOOD *)
lemma "A | B"
  apply (rule disjI1)  (* Matches disjunction goal *)
```

### 7. Undeclared Variable

**Error message:**
```
Undeclared variable: "x"
```

**Solutions:**
```isabelle
(* In Isar: use fix to introduce *)
proof -
  fix x :: nat
  show "P x" by auto
qed

(* In definitions: declare in signature *)
definition foo :: "nat => bool" where
  "foo x = (x > 0)"
```

### 8. Sledgehammer Reconstruction Failure

**Error message:**
```
Sledgehammer found a proof with <prover> but failed to reconstruct it
```

**Solutions:**
```isabelle
(* Try smt reconstruction *)
by (smt (verit) lemma1 lemma2)

(* Try with fewer lemmas *)
by (metis lemma1)

(* Use the lemma names manually *)
by (auto simp add: lemma1 lemma2 dest: lemma3)
```

### 9. Ambiguous Input

**Error message:**
```
Ambiguous input produces ... parse trees
```

**Solutions:**
```isabelle
(* Add type annotations to disambiguate *)
have h: "(xs :: nat list) @ ys = zs" by simp

(* Use parentheses *)
have h: "(f (g x)) = y" by simp
```

## Quick Debug Workflow

1. **Read error location** — Isabelle highlights the exact position
2. **Check proof state** — Use `apply` instead of `by` to see remaining goals
3. **Verify imports** — Ensure all needed theories are imported
4. **Simplify** — Create minimal example that reproduces the error
5. **Search** — `find_theorems` for needed lemmas

## Build Log Capture

```bash
# Full session build with log
isabelle build -d . -v -b SessionName 2>&1 | tee /tmp/isa_build.log

# Quick scan for errors
grep -n "Error\|error\|FAILED" /tmp/isa_build.log
```

## See Also

- [stuck-and-repair.md](stuck-and-repair.md) — Repair mode escalation
- [tactic-patterns.md](tactic-patterns.md) — Choosing the right tactic
- [isar-patterns.md](isar-patterns.md) — Correct Isar proof structure
