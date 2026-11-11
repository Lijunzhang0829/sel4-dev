"""One source of truth for current and frozen historical proof workflows.

Version names bind prompts and derived capabilities, not independent flags.
Keep historical prompt bytes stable so existing run evidence can be replayed.
"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import MappingProxyType

_INSTRUCTIONS = {
    'baseline': '',
    'sketch-groups-v1': '''\nENFORCED HELPER GROUPS: SKETCH GROUPS V1
When adding helpers, supply sketches=[{"target_entry_id":"parent ID",
"helper_ids":["h1","h2"]}] on edit_theory and include the executable parent
consumer proof in the same draft. A group may contain one or many helpers; its
complete interface must close the parent. Multiple parent groups may share one
edit. Call check_sketch with source_sha256 (optional target_entry_ids) before
proving a group. The harness rejects orphan or unchecked helpers regardless of
your explanation. Recursively add a checked group for a difficult helper.
Use check_theory with entry_ids to check several selected proof updates while
other failed or sketch-only bodies stay in the draft. After the helpers are
proved, check their parent using its actual proof. Conditional results are never
GREEN, never premises for helper proofs, and never replenish generation budget.
''',
    'bounded-audited-prefix-v1': '''\nONLINE CHECK POLICY: BOUNDED AUDITED PREFIX V1
For an append-only check_theory draft, the harness checks your new proof bodies
one declaration at a time in source order, independently auditing and committing
each GREEN declaration. It stops at the first rejected declaration or the first
accepted fixed contract. The full draft, including all untested proof bodies,
remains saved and readable. Inspect accepted_ids and bounded_prefix feedback;
an untested body is never a premise. Non-append rewrites use an explicitly marked
whole-document fallback. No proof bodies or new mathematical content are supplied
by this policy. Keep existing fixed-contract and legal-fact restrictions.
''',
    'helper-closure-v1': '''\nEXPERIMENTAL WORKFLOW: HELPER CLOSURE V1
Keep the original unproved fixed contract as the objective of each small helper batch.
Before expanding that batch, state which observed obligation of that contract it addresses.
After a helper batch is accepted, return to the original contract: inspect its current
obligations and check a revised contract proof before starting another helper batch.
If that check is not yet meaningful, state the exact remaining prerequisite and keep the
next batch focused on it. Record remaining obligations in your ordinary check reason or
analysis, not new formal declarations. Helpers do not renew the effective generation
budget. Do not invent numeric remaining time: only harness-reported measurements count.
This changes workflow only: preserve all contract statements, legal-fact restrictions,
accepted-proof boundaries and complete native proof validation.
''',
}


_INSTRUCTIONS['sketch-local-demands-v1'] = _INSTRUCTIONS['sketch-groups-v1'] + '''
LOCAL HELPER DEMANDS: inspect requirements before generalizing
Every pending group must include use_sites covering every helper. Each selector
has helper_id, consumer_line (1-based line of the parent's proof body), and fact
(the exact helper fact expression, e.g. h[where x=x]). Put its using/from/with/
apply/by command on its own line. check_sketch observes the native fact after
attributes, the pending goals and local assumptions at that position. Read the
full receipt's helper_uses before proving helpers. Schematic variables still in
the fact are unresolved; neither the selector nor its observation proves actual
dependency, minimality, truth or difficulty. Keep selectors current after edits.
Default to the instance, available hypotheses and conclusion required at the
consumer. Avoid dropping useful premises, broadening objects or adding stronger
conclusions merely for generality. If a general helper stalls, specialize it or
restore legal local premises, then edit the consumer and rerun check_sketch.
The consumer must prove all restored premises. Generalization remains allowed
when useful for induction or measured reuse. A conditional receipt does not
prove a complexity reduction. Briefly probe all leaves before committing the
main budget; repair a script/known-fact gap before creating more helpers.
'''

_INSTRUCTIONS['sketch-reviewed-v1'] = _INSTRUCTIONS['sketch-local-demands-v1'] + '''
ENFORCED DECOMPOSITION REVIEW
After check_sketch closes, use probe_helpers to briefly check each pending leaf
in its own legal GREEN prefix (10 candidate seconds; preparation is separate).
An oops leaf receives a basic simp probe. Probes never publish GREEN or assume
siblings. Missing dependencies remain diagnostic proof failures, not admissions.
Call review_sketch bottom-up, referencing the exact receipt and native use
indices. Review domain_scope, premises and conclusion as local/generalized;
generalization needs induction/reuse justification. Reject unchanged burden;
explain the isolated reasoning, or delegate to a reviewed child sketch. Every
pending leaf needs a native probe; its failure/timeout does not prove hardness.
Native failure IDs appear in read_theory.workflow. Use diagnose_failure before
revising related decompositions. For script/known_fact failures, change and
check the existing proof first. Unrelated parents may continue independently.
Reviews are accountable model judgments, not kernel difficulty certificates.
Use proof_obstacles in check feedback and read_theory.workflow to compare the
last repair with its failure. same_observation means repeated rendered feedback
in the same context, not semantic equivalence; changed_observation does not
certify progress. unknown covers timeouts, infrastructure and changed context.
Read linked read_failure receipts when needed. Explain which failed command or
residual obligation the next edit addresses and how it differs from prior attempts.
If the same observation survives edited candidates, reassess the rule instantiation,
missing invariant or helper interface rather than merely renaming tactics. Continue
hard proof work while useful; these observations impose no repair-round limit.
In decomposition reviews describe both the removed obligations and the remaining
hard mechanism (e.g. an invariant or address relation). An isolated obligation or
a short probe timeout is not evidence that a leaf is easy. Preserve successful
siblings; extract independently useful stable subproofs as checked helpers when
appropriate, without treating any uncommitted script prefix as GREEN.
'''

_INSTRUCTIONS['sketch-reviewed-v2'] = _INSTRUCTIONS['sketch-reviewed-v1'].replace(
    'Briefly probe all leaves before committing the\nmain budget;',
    'Use targeted diagnostic probes when useful, or an explicit formal proof plan;').replace(
    'After check_sketch closes, use probe_helpers to briefly check each pending leaf',
    'After check_sketch closes, optionally use probe_helpers to diagnose selected leaves').replace(
    'Every\npending leaf needs a native probe; its failure/timeout does not prove hardness.',
    'Explicit formal leaves may skip a probe; timeout does not prove hardness.') + '''
SEMANTIC ROUTE REVIEW AND EXPLICIT FORMAL LEAVES
Every review row also needs helper_role (classification, bridge, local), coverage,
and proof_mode (diagnostic or formal). coverage contains definition_refs,
premise_sources, branches [{case,status:handled|excluded|unknown,evidence}], and
unknowns. Cite the semantic basis and enumerate feasible representation/case
branches; record what remains unknown. These are your claims, not kernel-certified
semantic coverage. Do not treat conditional closure or a probe timeout as proof
of the helper or of reduced difficulty.
Classification and representation-bridge helpers decide whether a route works:
prove them before spending formal proof work on sibling helpers. Helpers serving
such a critical helper through a child sketch remain available. Unrelated fixed
contracts can proceed independently. A refuted helper rejects its route only.
Use proof_mode=formal with a concrete proof_plan to start ordinary proof work
without a short probe. No probe-success threshold, retry count or elapsed proof
time forces decomposition. diagnostic mode uses a compatible probe receipt;
a timeout leaves feasibility unestablished. Read workflow.readiness for exact
prerequisites and next_actions instead of guessing stale selectors or reviews.
Existing source, import, native-audit and final parent/seal requirements remain.
'''


_INSTRUCTIONS['sketch-guided-v1'] = _INSTRUCTIONS['sketch-local-demands-v1'].replace(
    'Briefly probe all leaves before committing the\nmain budget;',
    'Assess the current sketch before committing the main budget;') + '''
NATIVE STRUCTURAL SCREENING AND LOCAL REPAIR
After conditional closure call assess_sketch. It observes the parent and helpers
in legal native contexts without assuming siblings, ranks archived alternatives
by worst remaining obligation, and recommends at most two diagnostic probes.
Scores are untrained structural heuristics, not solvability probabilities.
review_sketch bottom-up remains required. A repeated parent, retained multi-operation
burden, iteration, or unknown profile needs a successful native probe or a reviewed
child decomposition. Merely labeling it isolated cannot override these signals.
Other leaves can proceed without mandatory probes. Do not expand definitions or
generalize just to manipulate a score; the final parent must still be proved.
For generalized demands add generalization_evidence: induction needs variable,
invariant, base_case, step_case; reuse needs consumers and shared_pattern.
These explanations are auditable judgments, not proofs of induction or reuse.
Use read_failure for the exact failed entry, saved proof, native diagnostic and
proof state. Diagnose script/known_fact errors and check a changed local proof
before revising related interfaces. Preserve accepted siblings and recheck every
changed consumer; no temporary admission or successful prefix publishes GREEN.
'''


_INSTRUCTIONS['sketch-phased-v1'] = _INSTRUCTIONS['sketch-guided-v1'] + '''
PHASED WORKFLOW (overrides probe-success-only rules above)
At most two short probes per leaf: initial attempt plus one changed-script repair.
Then stop probing. Use transition_proof with current source_sha256, failure_id,
decision and concrete plan. formal_leaf keeps the interface, requires a closed
consumer and prior probe, and permits risk-reviewed formal proof via check_theory
without first solving the leaf in a probe. This is not GREEN or a difficulty certificate.
Continue with the existing script and diagnostics; budgets never reset.
After an initial failed check and one failed local repair, a transition is required.
revise_consumer preserves the entry interface and requires a changed consumer proof;
revise_interface requires changing a helper declaration (contracts stay fixed);
decompose_helper requires a saved child group. Diagnose failures before interface
edits; recheck modified sketches and review bottom-up. Transitions bind the exact
current candidate; execute it before editing again. Formal proof work may continue
while making native progress; repeated residual obligations should be decomposed.
These actions gate entries, not arbitrary tactic checkpoints: do not claim a kernel
verified unchanged prefix when no native checkpoint evidence exists.
'''

# Ordinary proved lemmas are not conditional decomposition assumptions.
_INSTRUCTIONS['sketch-reviewed-v3'] = _INSTRUCTIONS['sketch-reviewed-v2'] + """
ORDINARY HELPERS: Do not create a sketch for every helper. Ungrouped helpers may
be proved directly with check_theory. Use a sketch only to test a proposed
conditional decomposition before proving its leaves. Explicit groups retain
closure and review requirements. No unproved helper is a usable premise.
This ordinary-helper rule supersedes the inherited orphan-helper restriction.
"""

@dataclass(frozen=True)
class GenerationPolicy:
    name: str
    review: str = 'none'
    bounded_prefix: bool = False
    ordinary_helpers: bool = False

    @property
    def requires_document(self):
        return self.bounded_prefix or self.review != 'none'

    def store_options(self):
        """Legacy attribute projection; callers choose one version, not bits."""
        result = {
            'require_sketch': self.review != 'none',
            'require_use_sites': self.review not in {'none', 'groups'},
            'require_review': self.review in {'reviewed', 'semantic', 'guided', 'phased'},
        }
        if self.review in {'guided', 'phased'}:
            result['require_assessment'] = True
        if self.review == 'phased':
            result['require_phases'] = True
        if self.review == 'semantic':
            result.update(require_semantic_review=True, allow_formal_leaf=True)
        if self.ordinary_helpers:
            result['allow_ordinary_helpers'] = True
        return result


POLICIES = MappingProxyType({p.name: p for p in (
    GenerationPolicy('baseline'),
    GenerationPolicy('helper-closure-v1'),
    GenerationPolicy('bounded-audited-prefix-v1', bounded_prefix=True),
    GenerationPolicy('sketch-groups-v1', 'groups'),
    GenerationPolicy('sketch-local-demands-v1', 'local'),
    GenerationPolicy('sketch-reviewed-v1', 'reviewed'),
    GenerationPolicy('sketch-reviewed-v2', 'semantic'),
    GenerationPolicy('sketch-reviewed-v3', 'semantic', ordinary_helpers=True),
    GenerationPolicy('sketch-guided-v1', 'guided'),
    GenerationPolicy('sketch-phased-v1', 'phased'),
)})
GENERATION_STRATEGIES = MappingProxyType(_INSTRUCTIONS)
CURRENT_STRATEGIES = ('baseline', 'sketch-reviewed-v3')
DEFAULT_STRATEGY = 'sketch-reviewed-v3'


def policy_for(value):
    if value == 'and-or-helper-selection-v1':
        raise ValueError('and-or-helper-selection-v1 was removed from the current document workflow; '
                         'use baseline for free helper/batch generation, or the original frozen '
                         'application to replay historical AND/OR runs')
    if value not in POLICIES:
        raise ValueError('unknown generation strategy')
    return POLICIES[value]


def generation_strategy(value):
    policy_for(value)
    text = _INSTRUCTIONS[value]
    return {'name':value, 'instructions':text,
            'instructions_sha256':hashlib.sha256(text.encode()).hexdigest()}


def resolve_strategy(run_dir, requested=None):
    """Use the frozen version on resume; use the current default for a new run."""
    path = Path(run_dir) / 'parallel-workspace-policy.json'
    if not path.exists():
        name = DEFAULT_STRATEGY if requested is None else requested
        policy_for(name)
        return name
    frozen = json.loads(path.read_text())['generation_strategy']
    name = frozen['name'] if requested is None else requested
    if generation_strategy(name) != frozen:
        raise ValueError('generation strategy differs from frozen workspace policy')
    return name

