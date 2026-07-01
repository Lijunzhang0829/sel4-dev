#!/usr/bin/env python3
"""Generate + run ISOLATED single-lemma theories to settle, without cross-lemma
contamination, whether plain / OLD-snippet / Fix-B agree on faithfulness.

Two goals:
  conjI : "(P::bool) \\<and> Q"  via `rule conjI`  -- P,Q FREE => subgoals P,Q are
          NOT closeable => a FAITHFUL `by` MUST FAIL ("Failed to finish proof").
  simp  : "sum_list [0..<700] = (244650::nat)" via `simp`.

Each (method-variant x goal) is its OWN theory & process run; raw saved.
"""
import subprocess, os

PROF = "/workspace/tools/seL4-proof-search/Isa-Repl/profiler"
ISA = "/workspace/verification/isabelle/bin/isabelle"

TP_OLD = r'''method_setup tp_old = \<open>
  Method.text_closure >> (fn m => fn ctxt =>
    (fn facts => fn cst =>
      ML_Profiling.profile_time
        (fn () => (case Seq.pull (Method.evaluate m ctxt facts cst) of
            SOME (r, s') => Seq.cons r s' | NONE => Seq.empty)) ()))
\<close>'''
TP_FIXB = r'''method_setup tp_fixB = \<open>
  Method.text_closure >> (fn m => fn ctxt =>
    (fn facts => fn cst =>
      Seq.of_list (ML_Profiling.profile_time
        (fn () => Seq.list_of (Method.evaluate m ctxt facts cst)) ())))
\<close>'''

CONJI = r'"(P::bool) \<and> Q"'                      # impossible (P,Q free) -> MUST fail
SIMP = r'"sum_list [0..<700] = (244650::nat)"'      # plain simp FAILS here
SIMPOK = r'"rev (rev (xs::nat list)) = xs"'         # plain simp SUCCEEDS
BLASTOK = (r'"(\<forall>x. P x \<longrightarrow> Q x) \<Longrightarrow> '
           r'(\<forall>x. Q x \<longrightarrow> R x) \<Longrightarrow> P a \<Longrightarrow> R a"')  # plain blast SUCCEEDS

# name -> (setup, lemma_proof_line). A FAITHFUL snippet must match plain on ALL.
CASES = {
    "iso_conjI_plain": ("",      f'lemma t: {CONJI} by (rule conjI)'),
    "iso_conjI_old":   (TP_OLD,  f'lemma t: {CONJI} by (tp_old \\<open>rule conjI\\<close>)'),
    "iso_conjI_fixB":  (TP_FIXB, f'lemma t: {CONJI} by (tp_fixB \\<open>rule conjI\\<close>)'),
    "iso_simp_plain":  ("",      f'lemma t: {SIMP} by simp'),
    "iso_simp_old":    (TP_OLD,  f'lemma t: {SIMP} by (tp_old \\<open>simp\\<close>)'),
    "iso_simp_fixB":   (TP_FIXB, f'lemma t: {SIMP} by (tp_fixB \\<open>simp\\<close>)'),
    "iso_simpok_plain":("",      f'lemma t: {SIMPOK} by simp'),
    "iso_simpok_old":  (TP_OLD,  f'lemma t: {SIMPOK} by (tp_old \\<open>simp\\<close>)'),
    "iso_simpok_fixB": (TP_FIXB, f'lemma t: {SIMPOK} by (tp_fixB \\<open>simp\\<close>)'),
    "iso_blastok_plain":("",     f'lemma t: {BLASTOK} by blast'),
    "iso_blastok_old": (TP_OLD,  f'lemma t: {BLASTOK} by (tp_old \\<open>blast\\<close>)'),
    "iso_blastok_fixB":(TP_FIXB, f'lemma t: {BLASTOK} by (tp_fixB \\<open>blast\\<close>)'),
}


def run(name, setup, proof):
    thy = f"{PROF}/{name}.thy"
    with open(thy, "w") as f:
        f.write(f"theory {name}\n  imports Main\nbegin\n{setup}\n{proof}\nend\n")
    raw = f"{PROF}/raw-{name}.out"
    cmd = (f"cd {PROF} && L4V_ARCH=ARM {ISA} process -l HOL -o parallel_proofs=0 "
           f"-T {name} </dev/null > {raw} 2>&1; true")
    subprocess.run(["bash", "-lc", cmd])
    txt = open(raw, "rb").read().decode("utf-8", "replace")
    txt = "".join(c for c in txt if c == "\n" or ord(c) >= 32)
    failed = ("Failed to finish proof" in txt) or ("Failed to apply" in txt)
    return "FAIL(proof-not-closed)" if failed else "PASS(proof-closed)"


print(f"{'case':20} {'verdict':24}")
print("-" * 46)
res = {}
for name, (setup, proof) in CASES.items():
    res[name] = run(name, setup, proof)
    print(f"{name:20} {res[name]}")

print("\n=== interpretation ===")
print(f"conjI (P,Q free => MUST fail if faithful):")
print(f"  plain={res['iso_conjI_plain']}  old={res['iso_conjI_old']}  fixB={res['iso_conjI_fixB']}")
print(f"simp  (sum_list; plain settles the truth):")
print(f"  plain={res['iso_simp_plain']}  old={res['iso_simp_old']}  fixB={res['iso_simp_fixB']}")
print("\nA snippet is FAITHFUL iff its verdict == plain's verdict on BOTH goals.")
