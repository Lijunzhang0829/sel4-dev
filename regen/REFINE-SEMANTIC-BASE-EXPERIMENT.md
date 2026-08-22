# Refine Semantic-Base Experiment

Status: branch setup; sanitizer and campaign not yet started

## Purpose

The previous Setting B froze every Refine theory with no selected contract as a
Support theory. That made the experiment manageable, but it also admitted human
theorems from those files into the premise database. The new setting removes
that distinction as a proof-preservation boundary.

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

The old Proof/Support classification may still be retained as descriptive
metadata, but it no longer decides which theorem facts survive.

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

Only the legal input boundary changes. The initial Premise Database contains
parent-session facts, fixed Semantic Base declarations, and facts Isabelle
mechanically derives from those declarations. It contains no human-written
theorem from any Refine-owned theory.

## First Pilot

The first pilot is `KHeap_R`, because the previous setting already provides a
complete generation trace for comparison. The pilot will answer:

- which generated KHeap proofs used frozen Support theorem facts;
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
- No Semantic-Base campaign ID or generation result exists yet.

The next implementation step is a command-aware Semantic Base extractor plus an
Isabelle elaboration audit. Generation must not begin until that audit confirms
that the target context contains no Refine-owned human theorem facts.
