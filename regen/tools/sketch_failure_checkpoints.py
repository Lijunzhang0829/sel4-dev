"""Freeze failure-stage inputs and historical controls for B-only sketch trials.

This prepares checkpoint manifests; the existing green_contract_ab supervisor
and ordinary parallel-workspace runner perform all proving and acceptance.
No post-checkpoint drafts, helper hints or target proofs enter model inputs.
"""
import argparse
import json
from pathlib import Path

from regen.execution.proof_commits import durable
from regen.execution.proof_session_runner import generated_checkpoint_theory
from regen.infrastructure.incremental_log import read_artifact as read
from regen.tools.proof_idle_watch import projection, sha


CASES = [
    ('schedule','Scheduler_Dispatch',40,'schedule_corres',4135,24),
    ('reset','Untyped_Object_Lifecycle',56,'resetUntypedCap_corres',5242,7),
]


def prepare(source, output):
    source,output=source.resolve(),output.resolve()
    if output.exists() or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('checkpoint output must be new and independent')
    files=read(source/'execution-input.json')['files']
    providers={generated_checkpoint_theory(e['file_slot']):e for e in files}
    output.mkdir(parents=True)
    rows=[]
    transcripts=source/'parallel-sessions/60ea84ad16ab439ba102b3a49fc88a8a'
    for name,theory,number,target,line_number,expected_helpers in CASES:
        directory=source/'theories'/theory
        path=directory/'acceptance/commits'/f'{number:06}.json'
        record=read(path)
        package=read(source/'packages'/(theory+'.thy.json'))
        target_id=next(c['contract_id'] for c in package['contracts'] if c['name']==target)
        if target_id in record['state']['proofs']:
            raise ValueError('chosen failed-stage target is already proved')
        selected={theory:{'commit_id':record['commit_id'],'artifacts':{}}}
        cutoff=path.stat().st_mtime
        active=set()
        def provider(checkpoint_name):
            entry=providers[checkpoint_name];title=entry['theory']
            if title in selected:
                return
            if checkpoint_name in active:
                raise ValueError('cyclic provider imports')
            active.add(checkpoint_name)
            root=source/'theories'/Path(entry['file_slot']).stem
            eligible=[]
            for commit in sorted((root/'acceptance/commits').glob('*.json')):
                if commit.stat().st_mtime<=cutoff:
                    meta,_=projection(commit)
                    if meta['event']=='seal':
                        eligible.append(meta)
            if not eligible:
                raise ValueError('provider was not sealed at the checkpoint: '+title)
            for dependency in read(root/'imports.json'):
                provider(dependency)
            selected[title]={'commit_id':eligible[-1]['commit_id']}
            active.remove(checkpoint_name)
        for dependency in read(directory/'imports.json'):
            provider(dependency)
        checkpoint=output/name/'checkpoint.json'
        durable(checkpoint,{'source_run':str(source),'theories':selected},immutable=True)
        transcript=transcripts/theory/'session-transcript.jsonl'
        start=None
        for index,text in enumerate(transcript.open(),1):
            if index==line_number:
                start=json.loads(text)['time_unix'];break
        if start is None:
            raise ValueError('historical stage-start message missing')
        old_ids=set(record['state']['proofs'])
        accepted=set(old_ids);within_hour=set(old_ids);events=[]
        for commit in sorted((directory/'acceptance/commits').glob('*.json')):
            if int(commit.stem)<=number:
                continue
            meta,proofs=projection(commit)
            changed=sorted(set(proofs)-accepted)
            if changed:
                at=commit.stat().st_mtime
                events.append({'commit_id':meta['commit_id'],'commit_path':str(commit),
                    'publication_unix_from_file_metadata':at,'new_ids':changed,
                    'within_first_wall_hour':start<=at<=start+3600})
                if start<=at<=start+3600:
                    within_hour.update(changed)
            accepted=set(proofs)
        added=accepted-old_ids
        helpers=sorted(k for k in added if not k.startswith('contract:'))
        if len(helpers)!=expected_helpers or target_id in accepted:
            raise ValueError('historical failure evidence differs from the published case study')
        historical={'source_run':str(source),'target_contract_id':target_id,
            'pre_stage_commit_id':record['commit_id'],'pre_stage_commit_path':str(path),
            'pre_stage_request_count':record['state']['batch_requests'],
            'inherited_accepted_ids':sorted(old_ids),
            'target_success_at_end':False,'stage_new_helper_ids':helpers,
            'first_wall_hour_new_helper_ids':sorted(k for k in within_hour-old_ids if not k.startswith('contract:')),
            'first_wall_hour_target_success':target_id in within_hour,
            'stage_start_transcript':str(transcript),'stage_start_line':line_number,
            'stage_start_unix':start,'acceptance_events':events,
            'time_limitation':'A window uses original commit publication file metadata and stage-message time; '
                'no effective-generation clock exists for this historical stage.',
            'comparability_limitations':['historical A used two concurrent theory agents; B is serial',
                'historical A retains conversational context; B starts a fresh provider session',
                'one selected failure case per target; no statistical success-rate claim'],
            'input_sha256':{str(path):sha(path),str(transcript):sha(transcript)}}
        durable(output/name/'historical-A.json',historical,immutable=True)
        rows.append({'name':name,'theory':theory,'target_contract_id':target_id,
            'checkpoint':str(checkpoint),'checkpoint_sha256':sha(checkpoint),
            'historical_A':str(output/name/'historical-A.json')})
    config={'cases':rows,'variant_strategy':'sketch-groups-v1','variant_only':True,
        'incremental_checkpoint_window':True,'wall_budget':True,
        'budget_effective_seconds':3600,'wall_limit_seconds':3600,
        'min_free_disk_bytes':4*1024**3,
        'authorization':'User 2026-10-13: selected ineffective/incomplete-helper cases; B only; one hour per group; compare historical A',
        'future_proof_hints_imported':False,'model':'gpt-6-astra','reasoning_effort':'medium',
        'selection':'purposive failure stages, before the observed helper group was generated'}
    durable(output/'config.json',config,immutable=True)
    return config


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=prepare(args.source,args.output)
    print(json.dumps({'cases':[r['name'] for r in result['cases']],
        'config':str(args.output/'config.json')}))
