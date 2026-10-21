# AutoCorres Guide: C-to-Isabelle Lifting Pipeline

AutoCorres automatically translates C code (parsed by the C-to-Isabelle parser)
into clean, abstract Isabelle/HOL representations suitable for verification.

## The Lifting Pipeline

AutoCorres applies four successive abstraction phases:

### Phase 1: Type Strengthening (C -> SIMPL)

The C parser produces a SIMPL representation with explicit memory model,
machine-word types, and byte-level operations. This is the raw starting point.

```
C source --> c-parser --> SIMPL (Gamma, guards, explicit state)
```

### Phase 2: Local Variable Lifting

Replaces the global mutable-record state for local variables with natural
Isabelle function parameters and return values.

**Before:** `gets (\<lambda>s. x_' (locals s))`
**After:** Direct parameter `x`

### Phase 3: Heap Abstraction

Replaces raw byte-level heap operations with typed heap accessors.

**Before:** `h_val (hrs_mem (t_hrs_' s)) (Ptr addr :: word32 ptr)`
**After:** `heap_w32 s addr`

Typed heaps generated:
- `heap_w8`, `heap_w16`, `heap_w32`, `heap_w64` -- word heaps
- `heap_ptr` -- pointer heaps (for pointer-to-pointer)
- Struct-typed heaps for each struct type

### Phase 4: Word Abstraction

Lifts machine-word types (`word32`, `word64`) to natural numbers or integers
where safe, making proofs cleaner.

**Before:** `(x :: word32) + (y :: word32)`
**After:** `(x :: nat) + (y :: nat)` (with bounds guard)

## Using AutoCorres

### Basic Invocation

```isabelle
install_C_file "my_program.c"
autocorres [
  ts_rules = nondet,          (* type strengthening rules *)
  unsigned_word_abs = my_func  (* lift word32 to nat in my_func *)
] "my_program.c"
```

### Key Options

| Option               | Effect                                      |
|----------------------|---------------------------------------------|
| `ts_rules`           | Type strengthening: `nondet`, `option`, etc |
| `unsigned_word_abs`  | Functions to apply word abstraction (nat)   |
| `signed_word_abs`    | Functions to apply signed word abstraction  |
| `no_heap_abs`        | Skip heap abstraction for named functions   |
| `heap_abs_syntax`    | Generate readable heap syntax               |
| `scope`              | Restrict to specific functions              |

### Accessing Lifted Functions

After `autocorres`, the lifted function is available in a locale:

```isabelle
context my_program begin
  thm my_func'_def        (* lifted definition *)
  thm my_func'_ac_corres  (* correspondence with SIMPL *)
end
```

The naming convention: C function `foo` becomes `foo'` after lifting.

## Pointer Reasoning

### Typed Pointers

AutoCorres uses typed pointers: `('a :: c_type) ptr`.

```isabelle
typ_synonym word32_ptr = "word32 ptr"
```

### Valid Pointers

A pointer `p` is valid if it points to allocated, typed memory:

```isabelle
is_valid_w32 s p    (* p points to a valid word32 *)
heap_w32 s p        (* read word32 at p *)
heap_w32_update (\<lambda>h. h(p := v)) s  (* write v at p *)
```

### Pointer Arithmetic

```isabelle
p +\<^sub>p n   (* pointer p offset by n elements *)
```

### Non-aliasing

Proving that two pointers do not alias:

```isabelle
ptr_valid_disjoint:
  "\<lbrakk> is_valid_w32 s p; is_valid_w32 s q; p \<noteq> q \<rbrakk>
   \<Longrightarrow> ptr_span p \<inter> ptr_span q = {}"
```

## Common Patterns

### Verifying a Simple Function

```c
unsigned add(unsigned a, unsigned b) { return a + b; }
```

```isabelle
autocorres [unsigned_word_abs = add] "add.c"

lemma add_correct:
  "\<lbrace>\<lambda>s. a + b \<le> UINT_MAX\<rbrace>
     add' a b
   \<lbrace>\<lambda>rv s. rv = a + b\<rbrace>!"
  unfolding add'_def
  by (wpsimp)
```

### Verifying a Loop

```isabelle
lemma sum_array_correct:
  "\<lbrace>\<lambda>s. is_valid_array s arr n\<rbrace>
     sum_array' arr n
   \<lbrace>\<lambda>rv s. rv = (\<Sum>i<n. heap_w32 s (arr +\<^sub>p int i))\<rbrace>"
  unfolding sum_array'_def
  apply (rule whileLoop_rule)
     apply (wpsimp simp: ...)  (* invariant preserved *)
    apply (wpsimp)              (* loop exit *)
   apply simp                   (* initial condition *)
  done
```

### Working with Structs

```isabelle
(* Access struct field *)
point_x_' (heap_point s p)

(* Update struct field *)
heap_point_update (\<lambda>h. h(p := point_x_'_update (\<lambda>_. v) (h p))) s
```

## Debugging AutoCorres

- If lifting fails, try `no_heap_abs = func_name` to skip heap abstraction.
- Use `autocorres [scope = func_name]` to lift one function at a time.
- Check `thm func'_def` to see what AutoCorres produced.
- Phase failures often stem from function pointers or complex unions.

## Limitations

- Function pointers require manual correspondence proofs.
- Unions are not fully supported; use `no_heap_abs` as a workaround.
- Recursive struct types may need manual type annotations.
- `volatile` and `restrict` qualifiers are largely ignored.
