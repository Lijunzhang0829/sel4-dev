# Refine Semantic-Base Experiment

Status: classification-free harness foundation implemented; M1 smoke PASS; M2 blocked on framework resolution

## Purpose

The earlier experiment admitted human theorems from Refine files outside its
generation target set. That made the experiment manageable, but preserved a
large amount of proof scaffolding. The new setting removes that boundary.

This experiment keeps only the declarations required to determine what the
fixed Refine endpoints and contracts mean in Isabelle. All other human-written
theorems in the Refine target scope are erased and must not enter premise
retrieval, `simp`, `wp`, or any other automation pool.

## What Is Kept

The fixed Semantic Base includes declarations such as:

- `datatype`, `record`, and `type_synonym`;
- `definition`, `abbreviation`, `consts`, and `constants`;
- `locale` assumptions and fixed parameters;
- `syntax`, `notation`, and `translations`;
- framework declarations that are demonstrably necessary to elaborate the
  contracts and fixed endpoints.

Facts generated mechanically by Isabelle from an accepted declaration are
legal. Examples include a definition's `_def` equation, record selectors, and
datatype induction/case rules. Their provenance is the fixed declaration, not a
human proof.

## What Is Erased

Across the complete Refine target scope, erase:

- human `lemma`, `theorem`, `corollary`, and proposition implementations;
- helper statements and proof bodies;
- `notes`, `lemmas`, `declare`, and similar human theorem registrations;
- human additions or deletions to `simp`, `wp`, `intro`, `cong`, and related
  automation pools;
- the stock human Refine heap.

No file classification is produced or consumed. Every Refine theory is scanned
under the same declaration policy.

## Mixed Commands

Commands such as `interpretation`, `sublocale`, `instantiation`, and `instance`
are not automatically safe. They can establish necessary framework structure,
but they can also contain proof obligations and expose human proof choices.
The sanitizer must split or reconstruct them:

1. preserve the framework declaration only when contract elaboration requires
   it;
2. erase its human proof body and theorem registrations;
3. expose the resulting obligation to the generated theorem pipeline;
4. reject selective copying of an old theorem merely to make elaboration pass.

The same review applies to executable or theorem-producing commands such as
`fun`, `function`, `inductive`, `typedef`, `setup`, and `ML`.

## Pipeline Boundary

The modular generation pipeline remains unchanged:

```text
Semantic Base Builder
  -> Isabelle Environment Checker
  -> Session Planner
  -> File Planner
  -> Proof Graph / Statement Shell Check
  -> Premise Retrieval + Automation Advisor + Outline
  -> Lemma Prover / Stepwise-Codex
  -> Graph Revision Agent
  -> Isabelle assembly and clean build
```

The legal input boundary and environment preparation change. The initial
Premise Database contains
parent-session facts, fixed Semantic Base declarations, and facts Isabelle
mechanically derives from those declarations. It contains no human-written
theorem from any Refine-owned theory.

## First Pilot

The first pilot is `KHeap_R`, because the previous setting already provides a
complete generation trace for comparison. The pilot will answer:

- which generated KHeap proofs previously depended on retained human Refine facts;
- which facts can be replaced by generated theorems or definitional automation;
- whether the File Planner must introduce new cross-contract nodes;
- whether the existing graph-revision and lemma-proving modules remain adequate
  when all Refine theorem scaffolding is removed.

The existing KHeap result is a Setting B baseline. It is not evidence of success
under this setting and will not be copied into the new campaign.

## Preparation State

- The automation-advisor harness baseline was committed as `7369371`.
- It was merged into `regen-refine-session-harness` as `fc930d1`.
- This experiment starts on branch `regen-refine-semantic-base`.
- The normative configuration is
  `regen/config/refine-semantic-base-campaign.json`.
- The complete harness is documented in regen/SEMANTIC-BASE-HARNESS.md.
- No production Semantic-Base campaign ID or proof generation exists yet.
- A disposable M1 smoke extracted 41 theories, 499 fixed declarations and all
  287 contracts while deleting 4,886 human theorem commands.
- M2 is blocked on 51 mixed framework commands that require explicit resolution.

The next implementation step is the Framework Declaration Resolver, followed by
an Isabelle elaboration and export audit. Generation must not begin until M2
confirms that the target context contains no Refine-owned human theorem facts.
