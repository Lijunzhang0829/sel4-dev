#!/usr/bin/env python3
"""Read the project experiment's planning, service and retained budget records."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo


def status(root):
    def read(path):
        return json.loads(path.read_text()) if path.is_file() else None
    planning = root/'planning-002' if (root/'planning-002').exists() else root/'planning'
    result = {'observed_unix':time.time(),'remote_root':str(root),
              'planning':read(planning/'status.json'),
              'planning_timing':read(root/(planning.name+'-timing.json'))}
    frozen = read(planning/'frozen-plan.json')
    if frozen:
        proposal = read(planning/'revision/planner-proposal.json')
        result['planning_summary'] = {'theories':len(proposal['theories']),
            'contracts':sum(len(t['contract_ids']) for t in proposal['theories']),
            'plan_sha256':frozen['plan_sha256'],'remaining_issues':frozen['final_review_issues']}
    proof = root/'proof'
    run = read(proof/'run.json')
    if run:
        sys.path.insert(0,str(root/'application'))
        from regen.infrastructure.experiment_ledger import Ledger
        rows = Ledger(proof/'effective-generation-budget',run['run_id']).rows
        wall = [r['data'] for r in rows if r['kind']=='generation_wall_limit']
        checkpoints = [r for r in rows if r['kind']=='effective_time_checkpoint']
        # Keep the policy constant authoritative rather than assuming its name.
        from regen.infrastructure.effective_time_budget import CHECKPOINT_KIND
        checkpoints = [r for r in rows if r['kind']==CHECKPOINT_KIND]
        result['generation'] = {'fixed_green':len({r['data']['contract_id'] for r in rows if r['kind']=='time_budget_green'}),
            'sealed_theories':len({r['data']['theory'] for r in rows if r['kind']=='time_budget_sealed'}),
            'wall_registrations':len(wall)}
        if wall:
            result['generation']['wall_limit']=wall[0]
            result['generation']['deadline_beijing']=datetime.fromtimestamp(wall[0]['deadline_unix'],ZoneInfo('Asia/Shanghai')).isoformat()
            result['generation']['elapsed_seconds']=time.time()-wall[0]['started_unix']
        if checkpoints:
            state=checkpoints[-1]['data']['state']
            result['generation']['checkpoint']={k:state.get(k) for k in ['effective_seconds','worker_seconds','paused','finished','reason','gate_open']}
        resume = read(root/'generation-resume-001.json')
        initial = read(proof/'service-launch.json')
        unit = resume['unit'] if resume else initial['unit']
        observed = subprocess.check_output(['systemctl','--user','show',unit,
            '--property=ActiveState','--property=SubState','--property=MainPID',
            '--property=ExecMainStatus','--property=InvocationID'],text=True)
        service = dict(line.split('=',1) for line in observed.splitlines() if '=' in line)
        result['generation']['service']={'unit':unit,**service}
        result['generation']['running']=(service.get('ActiveState')=='active' and service.get('SubState')=='running'
                                          and int(service.get('MainPID','0'))>0)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(status(args.root.resolve()),ensure_ascii=False,indent=2))
