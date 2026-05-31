# Isar Proof Patterns

Isar (Intelligible Semi-Automated Reasoning) is Isabelle's structured proof language. It produces human-readable proofs that are also machine-checked.

## Basic Structure

```isabelle
lemma my_lemma: "P x"
proof -
  show ?thesis by auto
qed
```

The `proof` block opens a structured proof; `qed` closes it. Use `-` after `proof` when no initial rule is applied.

## Core Constructs

### have — Intermediate Facts

```isabelle
proof -
  have h1: "A x" by simp
  have h2: "B x" using h1 by auto
  show ?thesis using h1 h2 by blast
qed
```

`have` introduces a local fact. It does not change the goal — it adds to the proof context.

### show — Proving the Goal

```isabelle
proof -
  show "P x" by auto
qed
```

`show` must match the current goal (or a remaining subgoal). The proof block is complete when all goals are shown.

### fix — Universal Introduction

```isabelle
lemma "ALL x. P x"
proof (rule allI)
  fix x
  show "P x" by auto
qed
```

`fix` introduces an arbitrary but fixed variable, corresponding to universal quantifier introduction.

### assume — Implication Introduction

```isabelle
lemma "A --> B"
proof (rule impI)
  assume h: "A"
  show "B" using h by auto
qed
```

`assume` introduces a hypothesis into the proof context.

### obtain — Existential Elimination

```isabelle
lemma "EX x. P x ==> Q"
proof -
  assume "EX x. P x"
  then obtain x where px: "P x" by auto
  show "Q" using px by blast
qed
```

`obtain` destructs an existential statement, introducing both the witness and its property.

## Chaining Patterns

### then — Forward Chaining

```isabelle
proof -
  have "A" by simp
  then have "B" by auto     (* A is available as chained fact *)
  then show ?thesis by blast
qed
```

`then` passes the previous fact as input to the next step.

### moreover/ultimately — Accumulating Facts

```isabelle
proof -
  have "A" by simp
  moreover have "B" by auto
  moreover have "C" by blast
  ultimately show "A & B & C" by auto
qed
```

`moreover` accumulates facts; `ultimately` makes all accumulated facts available for the conclusion.

### from — Explicit Fact Reference

```isabelle
proof -
  have h1: "A" by simp
  have h2: "B" by auto
  from h1 h2 show "A & B" by auto
qed
```

## Proof Methods After proof

### Rule-Guided Proofs

```isabelle
(* Conjunction *)
lemma "A & B"
proof (rule conjI)
  show "A" by simp
  show "B" by auto
qed

(* Induction *)
lemma "P (n::nat)"
proof (induct n)
  case 0
  show ?case by simp
next
  case (Suc n)
  show ?case using Suc.IH by auto
qed

(* Case analysis *)
lemma "P (xs :: 'a list)"
proof (cases xs)
  case Nil
  show ?thesis by simp
next
  case (Cons y ys)
  show ?thesis using Cons by auto
qed
```

### next — Separating Subgoals

`next` moves to the next subgoal within a structured proof. Each `case` or `next` block handles one subgoal.

## Common Patterns

### Pattern 1: Chain of Equalities

```isabelle
lemma "f x = g x"
proof -
  have "f x = h x" by simp
  also have "... = k x" by auto
  also have "... = g x" by (simp add: some_lemma)
  finally show ?thesis .
qed
```

`also`/`finally` chains transitivity steps. `...` refers to the RHS of the previous step.

### Pattern 2: Contradiction

```isabelle
lemma "~ P"
proof (rule notI)
  assume "P"
  then have "False" by auto
  then show "False" .
qed

(* Or classical contradiction *)
lemma "Q"
proof (rule ccontr)
  assume "~ Q"
  then show "False" by auto
qed
```

### Pattern 3: Set Equality

```isabelle
lemma "A = B"
proof (rule set_eqI)
  fix x
  show "x : A <-> x : B"
  proof
    assume "x : A"
    then show "x : B" by auto
  next
    assume "x : B"
    then show "x : A" by auto
  qed
qed
```

### Pattern 4: If-and-Only-If

```isabelle
lemma "P <-> Q"
proof
  assume "P"
  then show "Q" by auto
next
  assume "Q"
  then show "P" by auto
qed
```

### Pattern 5: Existential Witness

```isabelle
lemma "EX x. P x & Q x"
proof -
  have "P 42 & Q 42" by auto
  then show ?thesis by (rule exI)
qed

(* Or more directly *)
lemma "EX x. P x"
  by (rule exI[where x=42]) auto
```

## Style Guidelines

- Use Isar for proofs longer than one line
- Name important intermediate facts (`have h1: ...`)
- Use `moreover`/`ultimately` for accumulating multiple independent facts
- Use `also`/`finally` for transitivity chains
- Prefer `by auto` or `by simp` at leaf steps over long apply-scripts
- Each `have`/`show` should be self-contained and independently checkable

## See Also

- [tactic-patterns.md](tactic-patterns.md) — Choosing tactics for leaf steps
- [sledgehammer-guide.md](sledgehammer-guide.md) — Using sledgehammer at leaf steps
- [sorry-filling.md](sorry-filling.md) — When to escalate to Isar from apply-scripts
