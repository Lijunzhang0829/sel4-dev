# How the literature quantifies "stronger spec" — and what it says about P/Q

Branch: `bridge-consumer-trace`. Date: 2026-06-16.
Source: deep-research pass (Task wodz137rl), 11 adversarially-confirmed claims.

## The decisive answer

**There is NO accepted scalar for "this spec/invariant is stronger by X."** Strength
is, across the entire field, exactly two things:

1. **A PARTIAL ORDER** — by implication (Daikon: suppress any invariant implied by
   others — `x>y` implies `x≥y`) or by precision-within-a-fixed-language
   (LOUD/ASPIRE, OOPSLA'25: "strongest L-consequence" = maximally precise
   over-approximation in language L). Always an order, never a number. Code2Inv
   (NeurIPS'18) states the objective is explicitly "non-continuous" — correct or not.
2. **TASK-RELATIVE SUFFICIENCY** — does the (stronger) statement *suffice*, with
   inductiveness, to discharge the target obligation. ICE (CAV'14), Code2Inv,
   LIV (ASE'23) all reduce adequacy to the SAME three checkable Hoare conditions
   (holds-after-init · inductive · invariant∧exit ⇒ assertion).

The **headline evaluation metric** is a **COUNT of benchmark instances solved**
(a sufficient, prover-verified invariant found) — Code2Inv 106/133 vs 100/77/73
baselines. Not a strength score among valid solutions.

The **closest thing to a quantitative utility delta** is **precision/recall against
a verifier**: Daikon (SCP'07) reports >95% precision / >90% recall vs ESC/Java, and
~90% of lemmas auto-generated for distributed-algorithm proofs — i.e.
"fraction of properties NEEDED for verification that were found" (recall) = a
downstream-utility measure, not an intrinsic-strength measure.

## Mapping to the project's P/Q cases

| Literature concept | Project analog | Status |
|---|---|---|
| strength = implication partial order | P's `p_claim_check.json`: `(tcb_at t and invs) ⟹ invs`, reverse fails → **strict** | **P does this right**; Q only "agent 自述" → **mechanize Q** |
| Daikon "suppress weaker, keep stronger" | the strict-stronger gate | aligned |
| **inductive-but-INSUFFICIENT** failure mode (LIV benchmark04: holds + inductive, but doesn't imply the assertion) | **P/Q: strictly stronger but not-yet-consumed** | this is exactly the field's "valid but insufficient" class |
| sufficiency = the operative quality metric (counted) | **consumption** — does a downstream obligation actually use it | P/Q delivery = **pending**, not realized → by the field's metric, **not yet "solved"** |
| Daikon **recall** (% of needed properties found) | downstream obligations the stronger lemma discharges | the project's consumer-first count = this |
| IsaCoSy irreducibility / Hipster difficulty filters (discard trivially-derivable / routine conjectures) | Pattern-G `set_X_field[wp]` one-line wpsimp facts | would likely be **filtered as trivial/routine** |

## Three takeaways for the project's Spec metric

1. **Do NOT invent a "how-much-stronger" scalar** — the field is unanimous that
   strength is a partial order. The project's implication-check is already the
   correct, accepted strength criterion. **Mechanize Q's strictness** (it's a
   trivial obligation; P already does it).
2. **The accepted QUANTITATIVE metric is sufficiency, counted — i.e. consumption.**
   ICE/Code2Inv/LIV count "invariant suffices to prove the target"; Daikon counts
   recall against a verifier. The proof-assistant analog is exactly the
   **consumer-first count** (downstream obligations the stronger lemma discharges).
   So the metric switch we recommended — from "additive lemmas applied" (mere
   validity) to "downstream obligations discharged" (sufficiency) — is the
   field-sanctioned one. P/Q being "pending" means, by this metric, **effect not
   yet realized** (the analog of an inductive-but-insufficient invariant).
3. **Borrow the triviality filters.** IsaCoSy's irreducibility and Hipster's
   "needs hard reasoning" heuristics would pre-filter the Pattern-G one-liners
   before they enter any count — a cheap way to stop trivial additions inflating
   results.

## Honest gaps (flagged by the pass itself)

- **Spec mutation testing** ("does the spec catch injected bugs" → mutation kill
  rate as a completeness scalar) was requested but **no surviving claim covered
  it** — a SECOND, distinct quantification axis (completeness-by-bug-catching vs
  strength-by-implication) remains uncharacterized here.
- Whether **downstream-proof-utility is ever used AS the acceptance criterion** in
  ITP/CPP is **not established** — so "consumption-as-acceptance" is relatively
  novel, which helps Thesis 2 (a contribution) but means there is no off-the-shelf
  standard to copy.

## Citations
ICE (Garg, Löding, Madhusudan, Neider, CAV'14); Code2Inv (Si et al., NeurIPS'18);
LIV (Sosy-Lab, ASE'23); LOUD/ASPIRE (OOPSLA'25, PACMPL 10.1145/3720470); Daikon
(Ernst et al., SCP'07); IsaCoSy (Johansson, Dixon, Bundy, JAR'11); Hipster
(CICM'14); QuickSpec.
