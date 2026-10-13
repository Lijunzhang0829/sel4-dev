"""Apply contract-only inactivity to an already running legacy policy, without restart.

Read-only consumer of the experiment ledger. Intervention receipts are separate:
never race the application's append-only ledger writer or alter frozen launch files.
"""
import argparse,json,subprocess,time
from pathlib import Path
from regen.infrastructure.incremental_log import read_artifact
from regen.execution.proof_commits import durable

class ContractDeadline:
    def __init__(self, contracts, seconds=7200):
        self.contracts=set(contracts);self.seconds=seconds;self.deadline=None;self.last=None
    def consume(self,row):
        if row['kind']=='activity_started':
            self.last=row;self.deadline=row['time_unix']+self.seconds
        elif row['kind']=='target_updated' and row['data']['target_id'] in self.contracts:
            self.last=row;self.deadline=row['time_unix']+self.seconds
    def expired(self,now):return self.deadline is not None and now>=self.deadline

def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--unit',required=True);p.add_argument('--receipt',type=Path,required=True);a=p.parse_args()
    launch=read_artifact(a.run/'launch.json');dataset=read_artifact(Path(launch['dataset'])/'repository-planning-input.json')
    clock=ContractDeadline([c['contract_id'] for c in dataset['contracts']],launch['inactivity_seconds'])
    next_sequence=1;started=time.time()
    while True:
        ended=False
        while (path:=a.run/'events'/f'{next_sequence:09d}.json').exists():
            row=read_artifact(path);clock.consume(row);next_sequence+=1
            ended=row['kind']=='experiment_finished'
        now=time.time();report={'policy':'new-contract-content-or-validation-state-v2','run':str(a.run.resolve()),'unit':a.unit,'activated_at_unix':started,'observed_at_unix':now,'last_sequence':next_sequence-1,'last_contract_or_start_sequence':clock.last['sequence'] if clock.last else None,'deadline_unix':clock.deadline,'remaining_seconds':None if clock.deadline is None else clock.deadline-now,'retroactive_anchor':'latest contract update in current continuous run; activation does not reset clock','status':'finished' if ended or (a.run/'result.json').exists() else 'watching'}
        if report['status']=='finished':durable(a.receipt,report);return
        if clock.expired(now):
            report['status']='stop_requested';report['reason']='no_contract_update';durable(a.receipt,report)
            result=subprocess.run(['systemctl','--user','stop',a.unit],capture_output=True,text=True,timeout=120)
            report.update(stop_returncode=result.returncode,stop_stderr=result.stderr);durable(a.receipt,report)
            if result.returncode:raise RuntimeError('failed to stop experiment unit')
            return
        durable(a.receipt,report)
        time.sleep(2)

if __name__=='__main__':main()
