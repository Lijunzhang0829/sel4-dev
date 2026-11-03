"""Fresh planning: draft, consumer review, revision, final consumer review, freeze.

Only the final freeze receipt authorizes this workflow's downstream plan choice.
Each underlying planner still requires audited submission and native preflight.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from regen.planning.plan_review import analyze, digest, read, write, OUTPUT_REPAIR_POLICY
from regen.planning.revision_decisions import (
    decisions_valid, prior_dispositions, unresolved_review_issues,
    build_review_handoff, REVIEW_POLICY)


def replace_ids(value, mapping):
    if isinstance(value, str):
        # Also translate exact IDs inside prose; no original paths or logs supplied.
        for source, target in sorted(mapping.items(), key=lambda x:-len(x[0])):
            value = value.replace(source, target)
        return value
    if isinstance(value, list): return [replace_ids(v,mapping) for v in value]
    if isinstance(value, dict): return {k:replace_ids(v,mapping) for k,v in value.items()}
    return value


def run_command(root, stage, args, *, budget=None):
    write(root/'status.json', {'state':'running','stage':stage})
    with (root/(stage+'.log')).open('w') as log:
        command = [sys.executable,'-m',*args]
        if budget is None:
            subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
        else:
            budget.run(command, log)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--policy',type=Path,default=Path('regen/config/repository-planning-policy.json'))
    p.add_argument('--model',default='gpt-6-astra')
    p.add_argument('--reasoning-effort',default='medium')
    p.add_argument('--include-planning-time',action='store_true',
                   help='Charge draft, review repairs and revision to the shared effective budget')
    p.add_argument('--effective-seconds',type=float,default=172800,
                   help='Total planning plus proof allowance; handed off without resetting costs')
    p.add_argument('--max-revision-rounds',type=int,default=3,
                   help='Bound actual plan revisions; unresolved final review prevents freezing')
    p.add_argument('--allow-external-call',action='store_true')
    a=p.parse_args()
    if not a.allow_external_call: p.error('Explicit external-call authorization required')
    if a.max_revision_rounds < 1: p.error('At least one actual revision round is required')
    root=a.output.resolve(); root.mkdir(parents=True,exist_ok=False)
    source=a.input.resolve();policy=read(a.policy)
    policy['model'].update(model=a.model,reasoning_effort=a.reasoning_effort)
    if 'revision_context' in policy or 'pilot' in policy: raise ValueError('Expected a clean initial policy')
    write(root/'draft-policy.json',policy)
    binding={'input_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'policy_sha256':digest(policy),'model':a.model,'reasoning_effort':a.reasoning_effort,
        'review_max_repairs':None,'review_output_repair_policy':OUTPUT_REPAIR_POLICY,
        'final_plan_review_required':True,'review_closure_policy':REVIEW_POLICY,
        'max_revision_rounds':a.max_revision_rounds,
        'include_planning_time':a.include_planning_time,
        'historical_execution_inputs':False,'proof_generation':False,
        'implementation':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in
            [Path(__file__),Path('regen/planning/workspace_planner.py'),Path('regen/planning/plan_review.py'),
             Path('regen/infrastructure/reviewed_planning_budget.py')]}}
    if a.include_planning_time:
        binding['effective_seconds']=a.effective_seconds
    write(root/'binding.json',binding)
    budget=None
    if a.include_planning_time:
        from regen.infrastructure.reviewed_planning_budget import ReviewedPlanningBudget
        budget=ReviewedPlanningBudget(root,seconds=a.effective_seconds,
            binding={key:binding[key] for key in ('input_sha256','model','reasoning_effort','policy_sha256')})
    def planner(stage, config):
        run_command(root,stage,['regen.tools.repository_planning_retry','--planning-input',str(source),
            '--policy',str(config),'--output',str(root/stage),'--allow-external-call'],budget=budget)
    try:
        if budget is not None: budget.start()
        planner('draft',root/'draft-policy.json')
        run_command(root,'review',['regen.planning.plan_review','--plan',str(root/'draft/planner-proposal.json'),
            '--input',str(source),'--output',str(root/'review'),'--allow-external-call',
            '--model',a.model,'--reasoning-effort',a.reasoning_effort,'--timeout','600'],budget=budget)
        initial_review=read(root/'review/review.json')
        review = initial_review
        previous_stage = 'draft'
        dispositions = {}
        rounds = []
        artifact_folders = ['draft', 'review']
        for round_number in range(1, a.max_revision_rounds + 1):
            revision_stage = 'revision' if round_number == 1 else f'revision-{round_number:04d}'
            review_stage = 'final-review' if round_number == 1 else f'final-review-{round_number:04d}'
            mapping={original:public for public,original in read(root/previous_stage/'private-contract-map.json').items()}
            policy['revision_context']={
                'initial_plan':read(root/previous_stage/'agent-proposal.json'),
                'review':replace_ids(review,mapping), 'review_policy':REVIEW_POLICY,
                'notice':'Review hypotheses are not proof failures. Allocate concrete local obligations or justified '
                         'providers. Every issue needs a disposition; no historical execution evidence.'}
            config = root/(revision_stage+'-policy.json')
            write(config,policy)
            planner(revision_stage,config)
            before_revision=read(root/previous_stage/'planner-proposal.json')
            after=read(root/revision_stage/'planner-proposal.json')
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
                '--model',a.model,'--reasoning-effort',a.reasoning_effort,'--timeout','600'],budget=budget)
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
        before=read(root/'draft/planner-proposal.json')
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
