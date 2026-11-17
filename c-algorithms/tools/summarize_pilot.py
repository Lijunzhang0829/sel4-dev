#!/usr/bin/env python3
"""Project authoritative frozen-run progress into a compact experiment report."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[1]

def sha(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--source', type=Path, default=PROJECT,
                        help='Approved benchmark root (binary by default; select binomial explicitly).')
    args = parser.parse_args(); root = args.root.resolve(); source = args.source.resolve()
    preparation = json.loads((root/'dataset/preparation.json').read_text())
    catalog = json.loads((source/'targets.json').read_text())
    if preparation['semantic_binding'] != catalog['semantic_binding']:
        raise ValueError('summary destination belongs to another benchmark revision')
    phase = json.loads((root/'phase-status.json').read_text())
    if phase['stage'] != 'pilot_finished': raise ValueError('pilot is still running')
    script = '''import json,sys
from pathlib import Path
from regen.tools.application_release import verify
from regen.execution.repository_execution import progress
r=Path(sys.argv[1])
verify(r/'application',r/'release/manifest.json')
p=progress(r/'proof')
(r/'final-progress.json').write_text(json.dumps(p,indent=2)+'\\n')
'''
    env = dict(os.environ, REGEN_MODEL_CONFIG=str(root/'config/models.json'))
    subprocess.run([sys.executable,'-c',script,str(root)],cwd=root/'application',env=env,check=True)
    progress = json.loads((root/'final-progress.json').read_text())
    context = json.loads((root/'dataset/checker-context.json').read_text())
    for group, key in [('semantic_files','semantic_workspace'),('heap_files','heaps')]:
        directory = root/'dataset'/context['paths'][key]
        for path, expected in preparation[group].items():
            if sha(Path(directory)/path) != expected: raise ValueError('frozen input drift: '+path)
    mapping = json.loads((root/'dataset/contract-map.json').read_text())
    targets = []
    for f in progress['files']:
        for cid, status in f['contract_statuses'].items():
            targets.append({'id':mapping[cid],'contract_id':cid,'status':status,'theory':f['theory']})
    events = []
    log_text = (root/'generation.log').read_text()
    tail = log_text.rfind('\n{\n')
    outcome = json.loads(log_text[tail+1:]) if tail >= 0 else {}
    for line in log_text.splitlines():
        if line.startswith('[regen-event] '): events.append(json.loads(line[len('[regen-event] '):]))
    result = {'run_id':root.name,'run_directory':str(root),'semantic_binding':preparation['semantic_binding'],
              'model':'gpt-6-astra','reasoning_effort':'low','status':'partial' if not progress['repository_verified'] else 'verified',
              'purpose':'15-minute readiness pilot; no baseline comparison',
              'planning':json.loads((root/'planning-timing.json').read_text()),
              'generation':json.loads((root/'generation-timing.json').read_text()),
              'stop_reason':outcome.get('reason'),
              'runner_status':outcome.get('status'),
              'generation_budget':outcome.get('generation_budget'),
              'contracts':{'total':progress['contracts'],'proved':progress['proved_contracts'],
                'refuted':progress['refuted_contracts'],'unknown':progress['unknown_contracts'],
                'pending':sum(t['status']=='pending' for t in targets)},
              'sealed_theories':sum(f['status']=='sealed' for f in progress['files']),
              'accepted_helpers':sum(f.get('checked_auxiliaries',0) for f in progress['files']),
              'repository_verified':progress['repository_verified'],'targets':targets,
              'native_check_events':sum(e['event']=='isabelle_finished' for e in events),
              'batch_responses':sum(e['event']=='isabelle_batch_wait_finished' and e.get('waiting_for')!='ready' for e in events),
              'infrastructure_errors':[e for e in events if e.get('status')=='infrastructure_error'],
              'fixed_semantic_files_and_parent_heaps_unchanged':True,
              'supervisor_target_proofs_or_hints':0,
              'earlier_attempts':json.loads((root/'configuration.json').read_text()).get('prior_attempts',[]),
              'evidence_files':{str(p):sha(p) for p in [root/'final-progress.json',root/'configuration.json',root/'release/manifest.json',root/'generation.log',root/'execution-preflight.json']},
              'limits':['Partial/unattempted goals are not refutations.','A short pilot does not establish long-run stability.',
                        'Results apply only to this semantic binding; other heap datasets are independent.']}
    path = source/'evidence/experiment-summary.json'
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ['run_id','contracts','sealed_theories','accepted_helpers','repository_verified']},ensure_ascii=False))

if __name__ == '__main__': main()
