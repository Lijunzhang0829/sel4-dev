# find_theorems Guide

`find_theorems` is Isabelle's built-in theorem search facility. It searches the current theory context for lemmas matching specified criteria.

## Basic Syntax

```isabelle
find_theorems "pattern"
find_theorems name: "substring"
find_theorems criterion1 criterion2  (* intersection of results *)
```

## Search Criteria

### By Pattern (Term Matching)

```isabelle
(* Find lemmas mentioning a specific term pattern *)
find_theorems "_ + _ = _ + _"
find_theorems "length (map _ _)"
find_theorems "_ div _ = _"

(* With schematic variables *)
find_theorems "?a + ?b = ?b + ?a"
```

### By Name

```isabelle
(* Find lemmas whose name contains a substring *)
find_theorems name: "comm"
find_theorems name: "assoc"
find_theorems name: "append"
find_theorems name: "induct"
```

### By Conclusion/Introduction Pattern

```isabelle
(* Lemmas whose conclusion matches *)
find_theorems "_ : set _"

(* Intro rules for a connective *)
find_theorems intro  (* all intro rules *)
find_theorems elim   (* all elimination rules *)
find_theorems dest   (* all destruction rules *)
find_theorems simp   (* all active simp rules *)
```

### By Number of Results

```isabelle
find_theorems (50) "pattern"   (* return at most 50 results *)
find_theorems (10) name: "map" (* top 10 matching "map" *)
```

### Negated Criteria

```isabelle
(* Exclude results matching a criterion *)
find_theorems "_ + _" -name: "comm"  (* addition lemmas, not commutativity *)
```

## Combining Criteria

Multiple criteria are intersected (AND):

```isabelle
(* Lemmas about list append that mention length *)
find_theorems "length" "append"

(* Lemmas named "add" about natural numbers *)
find_theorems name: "add" "(_::nat) + _"

(* Simp rules about division *)
find_theorems simp "_ div _"
```

## Common Search Patterns

### Finding Rewrite Rules

```isabelle
(* What simplification rules exist for this term? *)
find_theorems simp "length (xs @ ys)"
find_theorems simp "map _ (xs @ ys)"

(* How to simplify conditional expressions *)
find_theorems simp "if _ then _ else _"
```

### Finding Introduction/Elimination Rules

```isabelle
(* How to prove a conjunction *)
find_theorems intro "_ & _"        (* returns conjI *)

(* How to use a disjunction *)
find_theorems elim "_ | _"         (* returns disjE *)

(* How to use an existential *)
find_theorems elim "EX _. _"       (* returns exE *)
```

### Finding Lemmas About Specific Types

```isabelle
(* List operations *)
find_theorems "length (rev _)"
find_theorems "set (filter _ _)"
find_theorems "sorted (sort _)"

(* Set operations *)
find_theorems "_ Un _ = _ Un _"
find_theorems "_ Int (_ Un _)"

(* Natural numbers *)
find_theorems "Suc _ = Suc _"
find_theorems "_ < Suc _"
```

### Finding Monotonicity/Congruence Rules

```isabelle
find_theorems "mono _"
find_theorems name: "cong"
find_theorems "_ <= _ ==> f _ <= f _"
```

## Workflow Integration

### Before Writing a Proof

```
1. Identify what facts you need
2. Search for each fact:
   find_theorems "pattern matching your needed fact"
3. Verify the found lemma matches your needs
4. GATE: All needed facts confirmed?
   - YES → start proving
   - NO → adjust proof plan, search again
```

### Feeding Results to Sledgehammer

```isabelle
(* Step 1: Find relevant lemmas *)
find_theorems "length (xs @ ys)"
(* Returns: length_append: length (xs @ ys) = length xs + length ys *)

(* Step 2: Feed to sledgehammer *)
sledgehammer [add: length_append]
```

### Feeding Results to simp/auto

```isabelle
(* Step 1: Find simp rules *)
find_theorems simp "rev (rev _)"
(* Returns: rev_rev_ident[simp]: rev (rev xs) = xs *)

(* Step 2: Use in proof *)
by (simp add: rev_rev_ident)
```

## Tips

- Start broad, narrow down: `find_theorems "map"` then `find_theorems "map _ (filter _ _)"`
- Use `_` as wildcard for any subterm
- Limit results with `(N)` to avoid overwhelming output
- Combine name and pattern search for best results
- Check both `simp` and non-simp variants of a lemma
- If no results: try different formulations of the same concept

## MCP Tool Usage

When using the MCP server, `find_theorems` queries are submitted through the theory context:

```isabelle
(* In a theory file, add temporarily: *)
find_theorems "your pattern"
(* Check diagnostics for results *)
```

## See Also

- [lemma-retrieval.md](lemma-retrieval.md) — Full search workflow
- [sledgehammer-guide.md](sledgehammer-guide.md) — Using found lemmas with sledgehammer
- [tactic-patterns.md](tactic-patterns.md) — Choosing tactics based on goal type
