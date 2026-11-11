"""Reviewed planning, optionally revising a frozen predecessor after generated failures.

Only the final freeze receipt authorizes this workflow's downstream plan choice.
Each underlying planner still requires audited submission and native preflight.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from regen.planning.plan_review import analyze, digest, read, write, validate_review, OUTPUT_REPAIR_POLICY
from regen.planning.recovery_revision import (
    recovery_evidence, validate_revision_constraints, validate_recovery_source)
from regen.planning.revision_decisions import (
    decisions_valid, prior_dispositions, unresolved_review_issues,
    build_review_handoff, REVIEW_POLICY)
from regen.infrastructure.progress_events import current_event_monitor, event_scope


def replace_ids(value, mapping):
    if isinstance(value, str):
        # Also translate exact IDs inside prose; no original paths or logs supplied.
        for source, target in sorted(mapping.items(), key=lambda x:-len(x[0])):
            value = value.replace(source, target)
        return value
    if isinstance(value, list): return [replace_ids(v,mapping) for v in value]
    if isinstance(value, dict): return {k:replace_ids(v,mapping) for k,v in value.items()}
    return value


def replace_exact_ids(value, mapping):
    """Match OwnerBlindView.decode: prose is retained, only exact IDs translate."""
    if isinstance(value, str): return mapping.get(value,value)
    if isinstance(value, list): return [replace_exact_ids(v,mapping) for v in value]
    if isinstance(value, dict): return {k:replace_exact_ids(v,mapping) for k,v in value.items()}
    return value


def run_command(root, stage, args, *, budget=None):
    write(root/'status.json', {'state':'running','stage':stage})
    monitor = current_event_monitor()
    if monitor is not None:
        monitor.transition(stage)
    try:
        with (root/(stage+'.log')).open('w') as log:
            command = [sys.executable,'-m',*args]
            if budget is None:
                subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
            else:
                budget.run(command, log)
    except BaseException as error:
        if monitor is not None:
            monitor.transition(stage, _monitor_failure_state(error),
                               error_type=type(error).__name__, reason=str(error))
        raise
    if monitor is not None:
        monitor.transition(stage, 'completed')


def _monitor_failure_state(error):
    from regen.execution.check_tasks import CheckCancelled
    from regen.infrastructure.planning_retry import PlanningFeedbackTimeout
    return ('paused' if isinstance(error, (CheckCancelled, PlanningFeedbackTimeout,
                                           KeyboardInterrupt)) else 'failed')


def prepare_revision_seed(root, seed, review_path, source, constraints):
    """Copy only plan data; old worker logs/proofs never enter the new workspace."""
    names = ('planner-proposal.json', 'agent-proposal.json',
             'private-contract-map.json', 'repository-plan.json',
             'planner-input.json','planner-validation-input.json','planner-evidence.json')
    data = {name: read(seed/name) for name in names}
    canonical = data['planner-proposal.json']
    mapping = data['private-contract-map.json']
    from regen.planning.workspace_harness import short_view
    from regen.preparation.refine_campaign import sha256_json
    view=short_view(source)
    if (not isinstance(mapping, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                           for k, v in mapping.items())
            or len(set(mapping.values())) != len(mapping)
            or mapping != view.to_original
            or replace_exact_ids(data['agent-proposal.json'], mapping) != canonical):
        raise ValueError('Revision seed public/canonical proposal binding differs')
    analyze(canonical, source['contracts'])
    published = data['repository-plan.json']
    if (data['planner-input.json'] != source
            or data['planner-validation-input.json'] != view.canonical_payload
            or published.get('proposal_sha256') != sha256_json(canonical)
            or published.get('planning_input_id') != source['planning_input_id']
            or published.get('planner_input_sha256') != sha256_json({
                **view.canonical_payload,'planner_evidence':data['planner-evidence.json']})):
        raise ValueError('Revision seed published proposal/input binding differs')
    if ({t['name']: t['contract_ids'] for t in published['theories']}
            != {t['name']: t['contract_ids'] for t in canonical['theories']}):
        raise ValueError('Revision seed published ownership differs')
    validate_revision_constraints(canonical, canonical, constraints)
    review = validate_review(read(review_path), canonical, source['contracts'])
    folder = root/'revision-seed'; folder.mkdir()
    for name, value in data.items():
        write(folder/name, value)
    write(folder/'review.json', review)
    write(folder/'provenance.json', {'source': str(seed.resolve()),
        'artifacts': {name: hashlib.sha256((seed/name).read_bytes()).hexdigest() for name in names},
        'review_source': str(review_path.resolve()),
        'review_sha256': hashlib.sha256(review_path.read_bytes()).hexdigest()})
    return canonical, review


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--policy',type=Path,default=Path('regen/config/repository-planning-policy.json'))
    p.add_argument('--model',default='gpt-6-astra')
    p.add_argument('--reasoning-effort',default='medium')
    p.add_argument('--include-planning-time',action='store_true',default=True,help=argparse.SUPPRESS)
    p.add_argument('--budget-seconds',type=float,
                   help='Total effective experiment allowance, including planning; default 172800 seconds')
    p.add_argument('--effective-seconds',type=float,help=argparse.SUPPRESS)
    p.add_argument('--max-revision-rounds',type=int,default=3,
                   help='Bound actual plan revisions; unresolved final review prevents freezing')
    p.add_argument('--revision-seed',type=Path,
                   help='Existing published planner directory; revise it instead of producing a fresh draft')
    p.add_argument('--initial-review',type=Path,
                   help='Unresolved predecessor review; required with --revision-seed')
    p.add_argument('--recovery-evidence',type=Path,
                   help='Explicitly authorized, hash-bound summaries of generated execution evidence')
    p.add_argument('--revision-constraints',type=Path,
                   help='Hard preservation bounds for sealed theories and accepted graph membership')
    p.add_argument('--allow-external-call',action='store_true')
    a=p.parse_args()
    import math
    from regen.infrastructure.experiment_runtime_budget import MODE, DEFAULT_SECONDS
    a._budget_explicit=a.budget_seconds is not None or a.effective_seconds is not None
    if a.budget_seconds is not None and a.effective_seconds is not None and a.budget_seconds!=a.effective_seconds:
        p.error('conflicting budget allowances')
    a.effective_seconds=(a.budget_seconds if a.budget_seconds is not None else
        a.effective_seconds if a.effective_seconds is not None else DEFAULT_SECONDS)
    if not math.isfinite(a.effective_seconds) or a.effective_seconds<=0:p.error('positive --budget-seconds required')
    a.budget_mode=MODE
    if not a.allow_external_call: p.error('Explicit external-call authorization required')
    if a.max_revision_rounds < 1: p.error('At least one actual revision round is required')
    if bool(a.revision_seed) != bool(a.initial_review):
        p.error('--revision-seed and --initial-review must be supplied together')
    if not a.revision_seed and (a.recovery_evidence or a.revision_constraints):
        p.error('Recovery evidence and preservation constraints require a revision seed')
    root=a.output.resolve(); root.mkdir(parents=True,exist_ok=False)
    with event_scope(root, run_id=root.name, phase='planning') as monitor:
        monitor.transition('preparation')
        try:
            _run(a, root)
        except BaseException as error:
            status=read(root/'status.json') if (root/'status.json').exists() else {}
            state=_monitor_failure_state(error)
            monitor.transition(status.get('stage', 'preparation'), state,
                               error_type=type(error).__name__, reason=str(error))
            # The lexical scope may otherwise label every raised exception as
            # failed. Preserve an explicitly observed cancellation as paused.
            monitor.close(state)
            raise
        monitor.transition('frozen', 'completed',
                           frozen_plan=str(root/'frozen-plan.json'))


def _run(a, root):
    source=a.input.resolve();policy=read(a.policy)
    policy['model'].update(model=a.model,reasoning_effort=a.reasoning_effort)
    if 'revision_context' in policy or 'pilot' in policy: raise ValueError('Expected a clean initial policy')
    write(root/'draft-policy.json',policy)
    constraints=read(a.revision_constraints) if a.revision_constraints else None
    baseline=initial_review=evidence=None
    if a.revision_seed:
        baseline,initial_review=prepare_revision_seed(root,a.revision_seed,a.initial_review,
                                                     read(source),constraints)
        if constraints is not None: write(root/'revision-constraints.json',constraints)
        if a.recovery_evidence:
            raw_evidence=read(a.recovery_evidence)
            evidence=recovery_evidence(raw_evidence,baseline)
            write(root/'recovery-evidence.json',raw_evidence)
    binding={'input_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'policy_sha256':digest(policy),'model':a.model,'reasoning_effort':a.reasoning_effort,
        'review_max_repairs':None,'review_output_repair_policy':OUTPUT_REPAIR_POLICY,
        'final_plan_review_required':True,'review_closure_policy':REVIEW_POLICY,
        'max_revision_rounds':a.max_revision_rounds,
        'include_planning_time':a.include_planning_time,
        'historical_execution_inputs':bool(evidence),'proof_generation':False,
        'implementation':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in
            [Path(__file__),Path('regen/planning/workspace_planner.py'),Path('regen/planning/plan_review.py'),
             Path('regen/infrastructure/reviewed_planning_budget.py'),
             Path('regen/planning/recovery_revision.py'),Path('regen/planning/workspace_harness.py')]}}
    if a.revision_seed:
        binding.update(planning_mode='incremental-recovery-v1',
            seed_plan_sha256=digest(baseline),seed_review_sha256=digest(initial_review),
            revision_constraints_sha256=digest(constraints),recovery_evidence_sha256=digest(evidence))
        if evidence and 'recovery_source_run' in raw_evidence:
            binding.update({k:raw_evidence[k] for k in
                ('recovery_source_run','recovery_source_tail_event_id')})
            old_clock=validate_recovery_source(raw_evidence['recovery_source_run'],
                                              raw_evidence['recovery_source_tail_event_id'])
            if not getattr(a,'_budget_explicit',True):
                a.effective_seconds=old_clock['seconds']-old_clock['effective_seconds']
            # A historical revision inherits its frozen predecessor clock;
            # this is replay compatibility, never a new selectable strategy.
            from regen.infrastructure.effective_time_budget import POLICY as legacy_policy
            if old_clock['policy']==legacy_policy:a.budget_mode='effective-time'
            if (a.include_planning_time
                    and abs(a.effective_seconds-(old_clock['seconds']-old_clock['effective_seconds'])) > 1e-6):
                raise ValueError('Recovery planning allowance must equal the paused source remainder')
    if a.include_planning_time:
        binding['effective_seconds']=a.effective_seconds
    write(root/'binding.json',binding)
    budget=None
    if a.include_planning_time:
        from regen.infrastructure.reviewed_planning_budget import ReviewedPlanningBudget
        budget=ReviewedPlanningBudget(root,seconds=a.effective_seconds,budget_mode=a.budget_mode,
            binding={key:binding[key] for key in ('input_sha256','model','reasoning_effort','policy_sha256',
                'recovery_source_run','recovery_source_tail_event_id') if key in binding})
    def planner(stage, config):
        run_command(root,stage,['regen.tools.repository_planning_retry','--planning-input',str(source),
            '--policy',str(config),'--output',str(root/stage),'--allow-external-call'],budget=budget)
    try:
        if budget is not None: budget.start()
        if not a.revision_seed:
            planner('draft',root/'draft-policy.json')
            run_command(root,'review',['regen.planning.plan_review','--plan',str(root/'draft/planner-proposal.json'),
                '--input',str(source),'--output',str(root/'review'),'--allow-external-call',
                '--model',a.model,'--reasoning-effort',a.reasoning_effort,'--timeout','600'],budget=budget)
            initial_review=read(root/'review/review.json')
            baseline=read(root/'draft/planner-proposal.json')
        review = initial_review
        previous_stage = 'revision-seed' if a.revision_seed else 'draft'
        dispositions = {}
        rounds = []
        artifact_folders = ['revision-seed'] if a.revision_seed else ['draft', 'review']
        for round_number in range(1, a.max_revision_rounds + 1):
            revision_stage = 'revision' if round_number == 1 else f'revision-{round_number:04d}'
            review_stage = 'final-review' if round_number == 1 else f'final-review-{round_number:04d}'
            mapping={original:public for public,original in read(root/previous_stage/'private-contract-map.json').items()}
            policy['revision_context']={
                'initial_plan':read(root/previous_stage/'agent-proposal.json'),
                'review':replace_ids(review,mapping), 'review_policy':REVIEW_POLICY,
                'notice':'Review hypotheses are not proof failures. Allocate concrete local obligations or justified '
                         'providers. Every issue needs a disposition; no historical execution evidence.'}
            if a.revision_seed:
                policy['revision_context'].update(recovery_mode='incremental-recovery-v1',
                    recovery_baseline_plan=replace_ids(baseline,mapping),
                    revision_constraints=replace_ids(constraints,mapping),
                    notice='Revise the predecessor plan using its final review and explicitly supplied generated '
                           'execution observations. Respect hard preservation constraints. Never use removed human '
                           'proofs or infer that a failed route is mathematically necessary.')
                if evidence: policy['revision_context']['recovery_evidence']=replace_ids(evidence,mapping)
            config = root/(revision_stage+'-policy.json')
            write(config,policy)
            planner(revision_stage,config)
            before_revision=read(root/previous_stage/'planner-proposal.json')
            after=read(root/revision_stage/'planner-proposal.json')
            validate_revision_constraints(baseline,after,constraints)
            # The workspace speaks public IDs; canonicalize structured duties
            # using its frozen map, without rewriting its submitted artifacts.
            public_map = read(root/previous_stage/'private-contract-map.json')
            decisions=replace_ids(read(root/revision_stage/'revision-decisions.json'),public_map)
            decisions_valid(decisions,review,before=before_revision,after=after,strict=True)
            for item in prior_dispositions(review,decisions):
                dispositions[item['issue_id']] = item
            dispositions_path=root/(revision_stage+'-dispositions.json')
            write(dispositions_path,list(dispositions.values()))
            run_command(root,review_stage,['regen.planning.plan_review',
                '--plan',str(root/revision_stage/'planner-proposal.json'),
                '--input',str(source),'--output',str(root/review_stage),'--allow-external-call',
                '--prior-dispositions',str(dispositions_path),
                '--model',a.model,'--reasoning-effort',a.reasoning_effort,'--timeout','600',
                *(['--recovery-baseline-plan',str(root/'revision-seed/planner-proposal.json')]
                  if a.revision_seed else []),
                *(['--recovery-evidence',str(root/'recovery-evidence.json')] if evidence else []),
                *(['--revision-constraints',str(root/'revision-constraints.json')]
                  if constraints is not None else [])],budget=budget)
            final_review=read(root/review_stage/'review.json')
            unresolved=unresolved_review_issues(final_review,list(dispositions.values()))
            rounds.append({'round':round_number,'revision':revision_stage,'review':review_stage,
                           'decisions':decisions,'unresolved_issue_ids':[i['id'] for i in unresolved]})
            artifact_folders.extend([revision_stage,review_stage])
            write(root/'review-closure.json',{'policy':REVIEW_POLICY,'rounds':rounds,
                  'state':'needs_revision' if unresolved else 'closed'})
            if not unresolved:
                break
            if round_number == a.max_revision_rounds:
                write(root/'review-blocked.json',{'policy':REVIEW_POLICY,'state':'blocked',
                      'reason':'unresolved_final_review','issues':unresolved,'rounds':rounds})
                raise ValueError('Final review has unhandled issues; revision bound reached; no plan frozen')
            previous_stage, review = revision_stage, final_review
        before=baseline
        validate_revision_constraints(before,after,constraints)
        published_plan=read(root/revision_stage/'repository-plan.json')
        handoff=build_review_handoff(published_plan,final_review,
                                    accepted_dispositions=list(dispositions.values()))
        write(root/revision_stage/'review-handoff.json',handoff)
        contracts=read(source)['contracts']
        old=analyze(before,contracts);new=analyze(after,contracts)
        owner=lambda plan:{c:t['name'] for t in plan['theories'] for c in t['contract_ids']}
        b,e=owner(before),owner(after)
        write(root/'comparison.json',{'before':old,'after':new,
            'moved_contracts':[{'contract_id':c,'before':b[c],'after':e[c]} for c in b if b[c]!=e[c]],
            'decisions':decisions,'review_rounds':rounds,'quality_improvement_certified':False})
        # Only freeze after genuine planner completion, native validation and review decisions.
        artifacts={str(f.relative_to(root)):hashlib.sha256(f.read_bytes()).hexdigest()
            for folder in artifact_folders for f in sorted((root/folder).rglob('*')) if f.is_file()}
        for name in ['comparison.json','review-closure.json',
                     *[name for name in ('revision-constraints.json','recovery-evidence.json') if (root/name).exists()],
                     *[f.name for f in root.glob('*-dispositions.json')],
                     *[f.name for f in root.glob('*-policy.json')]]:
            artifacts[name]=hashlib.sha256((root/name).read_bytes()).hexdigest()
        frozen={'state':'frozen','plan':str(root/revision_stage/'repository-plan.json'),
            'file_generation_list':str(root/revision_stage/'file-generation-list.json'),
            'plan_sha256':hashlib.sha256((root/revision_stage/'repository-plan.json').read_bytes()).hexdigest(),
            'review_sha256':digest(initial_review),'artifacts':artifacts,
            'final_review_sha256':digest(final_review),
            'final_review_plan_sha256':hashlib.sha256((root/revision_stage/'planner-proposal.json').read_bytes()).hexdigest(),
            'final_review_issues':final_review['issues'], 'quality_improvement_certified':False,
            'review_closure_policy':REVIEW_POLICY,'review_rounds':len(rounds),
            'review_handoff':str(root/revision_stage/'review-handoff.json'),
            'review_handoff_sha256':hashlib.sha256((root/revision_stage/'review-handoff.json').read_bytes()).hexdigest(),
            'proof_generation_started':False,'remaining_risks':[d['disposition']['remaining_risk'] for d in dispositions.values()]}
        if a.revision_seed:
            frozen.update(planning_mode='incremental-recovery-v1',seed_plan_sha256=digest(baseline),
                          preservation_constraints_checked=True)
        if budget is None: write(root/'frozen-plan.json',frozen)
        else: budget.complete(frozen)
        write(root/'status.json',{'state':'complete','stage':'frozen'})
    except BaseException as error:
        if budget is not None: budget.close(failed=True)
        old=read(root/'status.json') if (root/'status.json').exists() else {}
        write(root/'status.json',{'state':'failed','stage':old.get('stage'),'error':str(error)})
        raise
    finally:
        if budget is not None: budget.close()


if __name__=='__main__':main()
