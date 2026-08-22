# Semantic-Base Refine Regeneration Harness

Status: implementation foundation complete; M1 smoke PASS; M2 blocked on framework-command resolution; no production campaign or proof generation started.

## 1. Experimental Setting

This setting keeps fixed endpoints and only the Isabelle declarations needed to give selected Refine contracts their meaning. It removes every human-written theorem implementation owned by Refine.

There is no file category based on contract ownership. Every Refine theory has one uniform record:

    theory identity
    fixed semantic declarations
    fixed contract roots
    resolved framework-obligation roots

A generation task exists exactly when either root set is non-empty. A theory containing only fixed declarations is loaded into Semantic_Base but is not preserved as a human proof library. Setting B artifacts remain historical evidence and are not inputs.

## 2. Legal Boundary

**Kept fixed**

- datatype, record, type synonym, constants and locale structure;
- definitions, abbreviations and defs;
- syntax, notation, translations and required bundle context;
- primrec, fun, inductive and coinductive definitions;
- facts Isabelle mechanically derives from those declarations;
- framework declarations individually approved by the Framework Declaration Resolver.

**Removed**

- every human Refine lemma, theorem, corollary and helper statement;
- every human proof body;
- notes, lemmas, declare and other human fact registration;
- human changes to simp, wp, corres, intro, cong and other automation pools;
- the stock human Refine heap;
- unsealed generated declarations.

**Mixed commands**

Interpretation, global interpretation, sublocale, function, typedef, instance, axiomatization, setup and ML may combine semantic structure with proof obligations. Framework Declaration Resolver returns KEEP_DECLARATION, SPLIT_AND_GENERATE, REMOVE or BLOCK. M2 cannot pass while any item remains unresolved. An axiomatization is never silently accepted as a replacement for a proof.

## 3. Complete Flow

    M0 Campaign Freeze
      |
    M1 Semantic Base Extractor
      |-- all Refine theory packages
      |-- fixed contracts and declarations
      |-- framework review queue
      |
    Framework Declaration Resolver
      |
    M2 Isabelle Environment Checker
      |-- Semantic_Base session and heap
      |-- declaration and contract-shell elaboration
      |-- theorem and automation leakage audit
      |
    M3 Premise Publisher
      |-- parent facts
      |-- fixed-declaration generated facts
      |-- no human Refine theorem facts
      |
    M4 Session Planner
      |-- layered file-generation list
      |-- mutable session ledger
      |
    For each generation file:
      File Planner
      -> Statement Shell Check
      -> Premise Retrieval + Automation Advisor + Local Proof Outline
      -> Lemma Prover / Stepwise-Codex
      <-> Graph Revision Agent
      -> Assembly Stabilizer
      -> Theory Seal
      -> Dynamic Premise Publication
      |
    Generated Refine clean build and session seal

## 4. Environment Components

### M0 Campaign Freeze

Implementation: regen/refine_campaign.py

Input is the semantic-base campaign configuration, repository/l4v revisions, policies, backend identities and budgets. Output is one immutable manifest and campaign ID. M0 makes no model call. This campaign covers Refine only.

### M1 Semantic Base Extractor

Implementation: regen/semantic_base_m1.py

Output:

    M1/environment-manifest.json
    M1/audit.json
    M1/private/source-inventory.json
    M1/public/theory-packages/<file>.thy.json

All 41 theories receive the same package type. It contains identity, fixed declaration shells, exact contracts, unresolved framework shells and negative visibility assertions. It contains no human theorem statement, proof body or automation registration.

Current smoke: 41 theories, 499 fixed declarations, 287 contracts, 4,886 erased human theorem commands, 51 framework reviews and zero legacy file-category fields.

### Framework Declaration Resolver

Status: interface specified; resolver decisions not yet implemented.

Input is one review item, its shell, legal context and Isabelle diagnostics. Output is a decision, retained shell, exact generated obligations and audit reason. It cannot copy an old theorem or proof body.

### M2 Isabelle Environment Checker

Implementation: regen/semantic_base_m2.py and regen/isabelle_node_checker.py

Output is M2/environment-gate.json, checked Semantic_Base sources/heap, elaboration results and leakage audit. The current implementation returns blocked while reviews remain. Ready-for-Isabelle is not PASS until the actual Isabelle build and export audit succeed.

Later checkers use:

    base_session = Semantic_Base
    base_import  = Semantic_Base.Semantic_Root
    base_catalog = /m2/semantic-base

They do not load M2_Frozen_Support.

### M3 Premise Publisher

Implementation: regen/semantic_base_m3.py and regen/semantic_base_pipeline.py

Initial legal source classes are frozen_parent, fixed_semantic and fixed_declaration_generated. Dynamic publication accepts generated_theorem only with a theory seal ID.

Output:

    M3/premise-snapshot.json
    M3/premises.sqlite3

The SQLite schema is consumed directly by Lemma Prover. Human Refine sources, unknown source classes and unsealed generated records are rejected. frozen_support is not legal.

Premise Retrieval answers which named legal facts exist. Automation Advisor separately probes which local simp/wp transformations help in the actual checker state.

### M4 Session Planner

Implementation: regen/semantic_base_m4.py and regen/semantic_base_pipeline.py

Input is the M1 manifest and contract-justified generation-priority edges. Output is a layered file-generation list and mutable ledger.

Only theories with contract or resolved framework roots occur. Fixed-declaration-only theories are already in Semantic_Base and have no unlock state. M4 validates exact coverage, unknown edges and cycles. It does not reuse the human theorem import graph or design file-internal helpers.

Ledger:

    pending -> generating -> sealed
                         -> failed / retry / skipped

A failure does not stop traversal, but partial traversal cannot seal Refine.

## 5. Generation Components

### Session Runner

Implementation: regen/proof_session_runner.py

It detects M1/environment-manifest.json, reads theory-packages, validates M2/M3, binds M4 and passes Semantic_Base to ordinary and Stepwise checkers. Setting B paths remain only for historical replay.

### File Planner

Implementation: regen/file_planner.py

Input is every fixed root in one file, legal declaration context and budgets. Output is contract roots plus generated theorem/helper nodes, exact statements, roles, edges, consumers and strategies. It emits no proof body or external-premise node.

### Statement Shell Check

Implementation: regen/isabelle_node_checker.py

It elaborates graph declarations in Semantic_Base plus sealed generated theories and local green nodes. Elaboration does not claim theorem truth.

### Premise Retrieval

Implementation: regen/premise_retrieval.py

It queries only M3 SQLite and sealed generated records. It cannot scan raw Refine sources. regen/premise_db.py is a legacy analysis utility and not a production component.

### Automation Advisor

Implementation: regen/automation_advisor.py

It performs checker-backed definition-aware simp, plain simp, guarded wp and structural cleanup probes. Isabelle supplies actual successor states.

### Local Proof Outline

Implementation: regen/local_proof_outline.py and regen/proof_outline_salvage.py

It supplies a compositional route over generated dependencies and identifies local simplification phases. It cannot add graph nodes.

### Lemma Prover and Stepwise-Codex

Implementation: regen/lemma_prover.py, regen/stepwise_lemma_backend.py and regen/stepwise_codex.py

Lemma Prover owns one certified node and may change only its proof body. Stepwise searches:

    current Isabelle state
      -> Codex command
      -> IsaREPL
      -> verified successor state or residual
      -> search policy

Stepwise now uses the checker-provided base session and catalog.

### Graph Revision Agent

Implementation: regen/graph_revision_agent.py

It receives graph state, failed node, attempts, residuals, retrieval/advisor/outline evidence and budgets. It may add, split, delete or replace generated nodes and modify roles/edges. Fixed contracts and green-node statements are immutable. Requests, traces, decisions and transactions are logged.

### Assembly Stabilizer

Implementation: regen/assembly_stabilizer.py

It repairs proof bodies exposed by complete-theory context effects using Isabelle errors and the last node marker. Structural changes require Graph Revision Agent approval.

### Theory Seal and Publication

After complete-theory and generated-session builds: seal source/graph/evidence, export actual dependencies, publish generated records with the seal ID, and expose them to later files. A green but unsealed node remains local.

## 6. Logging

Each file has one generation ID. Logs contain exact module inputs, prompts, provider streams, responses, tool events, Isabelle sources/results, graph revisions and assembly evidence. Resolver and premise-publication decisions use the same provenance discipline.

## 7. Current Gate

M1 smoke restores all 287 contracts. M2 is blocked by 51 unresolved commands: 36 interpretation, 7 axiomatization, 5 global interpretation, 2 function and 1 sublocale.

Next is Framework Declaration Resolver, beginning with KHeap_R and its one function item. File Planner and Lemma Prover cannot start until the KHeap semantic declaration shell elaborates and M2 passes.
