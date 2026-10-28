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
from regen.planning.revision_decisions import decisions_valid


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
    p.add_argument('--allow-external-call',action='store_true')
    a=p.parse_args()
    if not a.allow_external_call: p.error('Explicit external-call authorization required')
    root=a.output.resolve(); root.mkdir(parents=True,exist_ok=False)
    source=a.input.resolve();policy=read(a.policy)
    policy['model'].update(model=a.model,reasoning_effort=a.reasoning_effort)
    if 'revision_context' in policy or 'pilot' in policy: raise ValueError('Expected a clean initial policy')
    write(root/'draft-policy.json',policy)
    binding={'input_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'policy_sha256':digest(policy),'model':a.model,'reasoning_effort':a.reasoning_effort,
        'review_max_repairs':None,'review_output_repair_policy':OUTPUT_REPAIR_POLICY,
        'final_plan_review_required':True,
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
        review=read(root/'review/review.json')
        mapping={original:public for public,original in read(root/'draft/private-contract-map.json').items()}
        policy['revision_context']={'initial_plan':read(root/'draft/agent-proposal.json'),
            'review':replace_ids(review,mapping),
            'notice':'Review hypotheses are not proof failures; keep local proof work local. No historical execution evidence.'}
        write(root/'revision-policy.json',policy)
        planner('revision',root/'revision-policy.json')
        decisions=read(root/'revision/revision-decisions.json')
        decisions_valid(decisions,review)
        # The first review concerns the draft. Review the exact submitted
        # revision too; record remaining risks without pretending a model
        # judgment certifies provability or automatically changing the plan.
        run_command(root,'final-review',['regen.planning.plan_review',
            '--plan',str(root/'revision/planner-proposal.json'),
            '--input',str(source),'--output',str(root/'final-review'),'--allow-external-call',
            '--model',a.model,'--reasoning-effort',a.reasoning_effort,'--timeout','600'],budget=budget)
        final_review=read(root/'final-review/review.json')
        before=read(root/'draft/planner-proposal.json');after=read(root/'revision/planner-proposal.json')
        contracts=read(source)['contracts']
        old=analyze(before,contracts);new=analyze(after,contracts)
        owner=lambda plan:{c:t['name'] for t in plan['theories'] for c in t['contract_ids']}
        b,e=owner(before),owner(after)
        write(root/'comparison.json',{'before':old,'after':new,
            'moved_contracts':[{'contract_id':c,'before':b[c],'after':e[c]} for c in b if b[c]!=e[c]],
            'decisions':decisions,'quality_improvement_certified':False})
        # Only freeze after genuine planner completion, native validation and review decisions.
        artifacts={str(f.relative_to(root)):hashlib.sha256(f.read_bytes()).hexdigest()
            for folder in ['draft','review','revision','final-review'] for f in sorted((root/folder).rglob('*')) if f.is_file()}
        artifacts['comparison.json']=hashlib.sha256((root/'comparison.json').read_bytes()).hexdigest()
        frozen={'state':'frozen','plan':str(root/'revision/repository-plan.json'),
            'file_generation_list':str(root/'revision/file-generation-list.json'),
            'plan_sha256':hashlib.sha256((root/'revision/repository-plan.json').read_bytes()).hexdigest(),
            'review_sha256':digest(review),'artifacts':artifacts,
            'final_review_sha256':digest(final_review),
            'final_review_plan_sha256':hashlib.sha256((root/'revision/planner-proposal.json').read_bytes()).hexdigest(),
            'final_review_issues':final_review['issues'], 'quality_improvement_certified':False,
            'proof_generation_started':False,'remaining_risks':[d['remaining_risk'] for d in decisions]}
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
