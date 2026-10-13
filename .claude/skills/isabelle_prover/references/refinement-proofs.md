# Refinement Proof Patterns for seL4

Refinement proofs establish that a concrete implementation correctly implements
an abstract specification. seL4 uses a chain of refinement layers.

## Refinement Architecture

```
Abstract Spec  (high-level functional spec)
     |  corres
Executable Spec  (Haskell-derived monadic code)
     |  ccorres
C Implementation  (SIMPL representation of C code)
```

Each layer must be shown to refine the one above it.

## corres: Abstract-to-Executable Refinement

### Goal Shape

```isabelle
corres r        (* return-value relation *)
  P             (* abstract precondition / guard *)
  P'            (* concrete precondition / guard *)
  (abs_op ...)  (* abstract operation *)
  (conc_op ...) (* concrete/executable operation *)
```

### State Relations

The state relation `state_relation` connects abstract state to executable
(Haskell) state. It is implicit in `corres` goals via the framework.

Key components:
- `pspace_relation` -- physical address space correspondence
- `cdt_relation` -- capability derivation tree
- `ghost_relation` -- ghost state (scheduling, etc.)

### Standard corres Rules

**corres_guard_imp** -- weaken guards:
```isabelle
apply (rule corres_guard_imp)
  apply (rule actual_corres_lemma)
 apply clarsimp   (* discharge abstract guard *)
apply clarsimp     (* discharge concrete guard *)
```

**corres_split** -- sequential composition:
```isabelle
apply (rule corres_split[OF first_corres])
   apply (rule second_corres)
  apply wp          (* wp for abstract *)
 apply wp           (* wp for concrete *)
apply auto          (* side conditions *)
```

**corres_when** -- conditional:
```isabelle
apply (rule corres_when)
 apply (rule body_corres)
apply simp  (* condition equivalence *)
```

**corres_mapM / corres_mapM_x** -- iterating over lists:
```isabelle
apply (rule corres_mapM)
    apply (rule element_corres)
   apply wp+
  apply simp+
done
```

### Template: Function Correspondence

```isabelle
lemma my_function_corres:
  "corres dc
     (invs and valid_cap cap and (\<lambda>s. P s))
     (invs' and valid_cap' cap')
     (my_abstract_function cap slot)
     (myConcreteFunction cap' slot')"
  apply (simp add: my_abstract_function_def myConcreteFunction_def)
  apply (rule corres_guard_imp)
    apply (rule corres_split[OF get_cap_corres])
       apply (rule corres_split[OF set_cap_corres])
          apply (rule corres_trivial, simp)
         apply (wp get_cap_wp getCap_wp)+
  apply (clarsimp simp: invs_def valid_state_def)
  apply (clarsimp simp: invs'_def valid_state'_def)
  done
```

## ccorres: C-to-Executable Refinement

### Goal Shape

```isabelle
ccorres r xf     (* return relation, extraction function *)
  G              (* abstract guard *)
  G'             (* C guard -- set of concrete states *)
  hs             (* handler stack *)
  (haskell_op)   (* executable/Haskell operation *)
  (c_code)       (* SIMPL C code *)
```

### Key ccorres Sub-methods

**cinit** -- initialize a ccorres proof, lifting C parameters:
```isabelle
apply (cinit lift: arg1_' arg2_')
```

**ctac** -- apply ccorres rule for a function call:
```isabelle
apply (ctac add: callee_ccorres)
```

**csymbr** -- symbolically execute a C read/assignment:
```isabelle
apply csymbr  (* for: x = expr; or struct field reads *)
```

**ceqv** -- establish C expression equivalence:
```isabelle
apply (ceqv, clarsimp)
```

### Template: Syscall Refinement

```isabelle
lemma handleSyscall_ccorres:
  "ccorres dc xfdc
     (\<lambda>s. invs' s \<and> ct_active' s \<and> ksSchedulerAction s = ResumeCurrentThread)
     ({s. syscall_' s = scast sc}) []
     (handleSyscall sc)
     (Call handleSyscall_'proc)"
  apply (cinit lift: syscall_')
  apply (simp add: handleSyscall_def)
  apply (rule ccorres_symb_exec_r)  (* symbolic exec on C side *)
    apply (ctac add: handleInvocation_ccorres)
       apply (ctac add: schedule_ccorres)
          apply (ctac add: activateThread_ccorres)
         apply (wp schedule_invs')+
       apply (vcg exspec=schedule_modifies)
      apply wp
     apply vcg
    apply (clarsimp simp: invs'_def)
  done
```

## Guard Discharge

After refinement rules are applied, guard obligations remain. Common strategies:

```isabelle
(* Simple guards *)
apply (clarsimp simp: invs_def valid_state_def)

(* Guards with pointer validity *)
apply (clarsimp simp: typ_heap_simps c_guard_clift)

(* Complex guards: try fastforce or blast *)
apply (fastforce simp: valid_cap'_def objBits_simps)
```

### Guard Debugging

If guards are hard to discharge:
1. Use `apply clarsimp` to simplify and see what remains.
2. Check if a missing invariant needs to be strengthened in the precondition.
3. Look for missing `wp` lemmas that would propagate the invariant.
4. Use `find_theorems` to locate relevant simplification lemmas.

## Common Pitfalls

1. **Forgetting wp for both sides:** In `corres_split`, you need wp lemmas
   for BOTH the abstract and concrete operations.
2. **Guard mismatch:** The abstract and concrete guards must be compatible
   under the state relation. Strengthen if needed.
3. **Return relation:** Ensure `r` relates the return values correctly.
   Use `dc` (don't care) for void-like operations.
4. **Handler stack:** In ccorres, the handler stack `hs` must match the
   exception-handling structure of the C code.
5. **Schematic leakage:** Avoid letting schematics escape `corres_split`
   subgoals; instantiate early with `where` or `OF`.
