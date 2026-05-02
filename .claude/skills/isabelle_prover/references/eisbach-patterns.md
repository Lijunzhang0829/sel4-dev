# Eisbach Proof Method Patterns for seL4

Eisbach is a framework for writing custom proof methods in Isabelle/HOL.
seL4 relies heavily on Eisbach-defined methods for structured verification.

## Core seL4 Eisbach Methods

### wp (Weakest Precondition)

The `wp` method propagates weakest preconditions backward through monadic code.

```isabelle
lemma my_lemma:
  "\<lbrace>P\<rbrace> f \<lbrace>Q\<rbrace>"
  apply wp        (* unfolds monadic operations, generates VCs *)
  apply clarsimp  (* discharge remaining proof obligations *)
  done
```

**When to use:** Hoare triples over monadic (nondeterministic state monad) code.
The method walks backward from the postcondition, applying wp rules registered
in the `wp` attribute set.

**Registering wp rules:**
```isabelle
lemma my_wp_rule[wp]:
  "\<lbrace>P\<rbrace> some_op \<lbrace>\<lambda>rv. Q rv\<rbrace>"
```

### wpsimp

Combines `wp` with `simp` in a single step. This is the workhorse for most
seL4 Hoare-triple proofs.

```isabelle
lemma invoke_example:
  "\<lbrace>\<lambda>s. valid_state s \<and> ct_active s\<rbrace>
     handle_invocation call blocking
   \<lbrace>\<lambda>rv. invs\<rbrace>"
  apply (wpsimp wp: hoare_drop_imps
              simp: handle_invocation_def)
  done
```

**Parameters:**
- `wp:` -- additional wp rules to apply
- `simp:` -- additional simp rules
- `simp_del:` -- simp rules to remove
- `cong:` -- congruence rules

**When to use:** Default choice for monadic Hoare triples. Prefer over bare `wp`
unless you need fine-grained control over simplification.

### ccorres (C-to-abstract Correspondence)

Proves that C code (represented in SIMPL) refines the abstract Haskell/monadic
specification.

```isabelle
lemma ccorres_example:
  "ccorres dc xfdc
     (invs' and ct_active')
     UNIV hs
     (doMachineOp cleanCaches)
     (Call cleanCaches_'proc)"
  apply (cinit lift: ...)
  apply (ctac add: cleanCaches_ccorres)
  apply (wpsimp simp: ...)
  done
```

**Key sub-methods for ccorres proofs:**
- `cinit` -- initialize ccorres goal, lift C variables
- `ctac` -- apply a ccorres rule for a function call
- `csymbr` -- symbolic execution of C reads
- `ceqv` -- establish equality of C expressions
- `clift` -- lift a pointer dereference

### corres (Abstract-to-Executable Refinement)

Proves refinement between the abstract specification and the executable
(Haskell) specification.

```isabelle
lemma corres_example:
  "corres dc
     (valid_state and pspace_aligned)
     (valid_state' and pspace_aligned')
     (delete_cap slot)
     (deleteCap slot)"
  apply (simp add: delete_cap_def deleteCap_def)
  apply (rule corres_guard_imp)
    apply (rule corres_split)
       apply (rule get_cap_corres)
      apply (rule corres_trivial, simp)
     apply wp+
  apply auto
  done
```

## Choosing the Right Method

| Goal shape                        | Method     | Notes                        |
|-----------------------------------|------------|------------------------------|
| `\<lbrace>P\<rbrace> f \<lbrace>Q\<rbrace>`          | `wpsimp`   | Default for Hoare triples    |
| `\<lbrace>P\<rbrace> f \<lbrace>Q\<rbrace>`          | `wp`       | When simp causes issues      |
| `ccorres r xf G G' hs a c`       | `cinit/ctac` | C-to-Haskell refinement    |
| `corres r G G' a a'`             | `corres_*` | Abstract-to-Haskell          |
| Anything with `\<Longrightarrow>` | `clarsimp` | Pure logic, no monadic code  |

## Common Pitfalls

1. **wp divergence:** If `wp` loops, provide explicit rules via `wp:` or use
   `wp (once)` to apply a single step.
2. **simp interference:** If `wpsimp` simplifies too aggressively, switch to
   bare `wp` followed by manual `simp` with controlled rule sets.
3. **Missing wp rules:** If `wp` leaves unsolved goals, check whether the
   function has a registered `[wp]` lemma. Use `find_theorems` to search.
4. **Guard discharge:** After `ccorres` methods, guards often remain. Discharge
   with `clarsimp simp: guard_simps` or `fastforce`.

## Debugging Tips

- Use `apply (wp; fail)?` to see if wp makes progress without committing.
- `method_check wp` shows which rules wp would apply.
- Break complex goals with `apply (rule ccorres_symb_exec_l)` to handle
  one monadic operation at a time.
