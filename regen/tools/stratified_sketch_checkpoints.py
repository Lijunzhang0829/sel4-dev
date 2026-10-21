"""Seeded successful-stage sampling; proving uses the existing two-case supervisor."""
import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import random

from regen.execution.proof_commits import durable
from regen.execution.proof_session_runner import generated_checkpoint_theory
from regen.infrastructure.incremental_log import read_artifact as read
from regen.tools.proof_idle_watch import projection, sha

TIERS = [('easy',0,15),('medium',15,60),('hard',60,float('inf'))]


def sample(population, seed):
    rng=random.Random(seed)
    result=[]
    for tier,_,_ in TIERS:
        pool=sorted((r for r in population if r['tier']==tier),
                    key=lambda r:(r['source_run'],r['theory'],r['target_contract_id']))
        if len(pool)<2:
            raise ValueError('not enough eligible successful stages: '+tier)
        for index,row in enumerate(rng.sample(pool,2),1):
            result.append({**row,'name':tier+'-'+str(index).zfill(2)})
    return result


def checkpoint_selection(source, theory, pre_path):
    providers={generated_checkpoint_theory(e['file_slot']):e
               for e in read(source/'execution-input.json')['files']}
    selected={theory:{'commit_id':projection(pre_path)[0]['commit_id'],'artifacts':{}}}
    cutoff=pre_path.stat().st_mtime
    active=set()
    def provider(name):
        entry=providers[name]; title=entry['theory']
        if title in selected:return
        if name in active:raise ValueError('cyclic providers')
        active.add(name)
        root=source/'theories'/Path(entry['file_slot']).stem
        sealed=[]
        for path in sorted((root/'acceptance/commits').glob('*.json')):
            if path.stat().st_mtime<=cutoff:
                info,_=projection(path)
                if info['event']=='seal':sealed.append(info)
        if not sealed:raise ValueError('provider unsealed at checkpoint: '+title)
        for dependency in read(root/'imports.json'):provider(dependency)
        selected[title]={'commit_id':sealed[-1]['commit_id']}
        active.remove(name)
    for dependency in read(source/'theories'/theory/'imports.json'):provider(dependency)
    return selected


def population(repository, analysis):
    events=list(csv.DictReader((analysis/'green-events.csv').open()))
    gaps=list(csv.DictReader((analysis/'theory-green-gaps.csv').open()))
    rows=[];rejected=[]
    for gap in gaps:
        ids=json.loads(gap['ids'])
        if len(ids)!=1:continue  # Avoid attributing a shared successful batch to one target.
        end=datetime.fromisoformat(gap['end_utc']).timestamp()
        hit=[e for e in events if e['theory']==gap['theory'] and abs(float(e['at'])-end)<.01]
        if len(hit)!=1:raise ValueError('historical gap has no unique acceptance event')
        event=hit[0]
        prior=[e for e in events if e['theory']==event['theory'] and e['run']==event['run']
               and float(e['at'])<end]
        if not prior:continue  # No cross-run idle/recovery gap as a target difficulty label.
        previous=max(prior,key=lambda e:float(e['at']))
        before_path=(repository/previous['commit']).resolve()
        after_path=(repository/event['commit']).resolve()
        before_meta,before=projection(before_path);after_meta,after=projection(after_path)
        target=ids[0]
        helpers=sorted(k for k in set(after)-set(before) if not k.startswith('contract:'))
        if not helpers or target in before or target not in after:continue
        source=after_path.parents[4]
        theory=event['theory']
        try:selection=checkpoint_selection(source,theory,before_path)
        except ValueError as error:
            rejected.append({'target_contract_id':target,'source_run':str(source),'reason':str(error)})
            continue
        score=float(gap['effective_gap_minutes'])
        tier=next(t for t,lo,hi in TIERS if lo<=score<hi)
        rows.append({'tier':tier,'source_run':str(source),'theory':theory,
            'target_contract_id':target,'pre_commit_path':str(before_path),'pre_commit_id':before_meta['commit_id'],
            'success_commit_path':str(after_path),'success_commit_id':after_meta['commit_id'],
            'historical_effective_gap_minutes':score,
            'historical_calendar_gap_minutes':float(gap['calendar_gap_minutes']),
            'historical_stage_start_unix':float(previous['at']),
            'historical_target_green_unix':end,'historical_new_helper_ids':helpers,
            'checkpoint_theories':selection})
    return rows,rejected


def prepare(repository, analysis, output, seed):
    repository,analysis,output=map(lambda p:Path(p).resolve(),(repository,analysis,output))
    if output.exists():raise ValueError('sampling output must be new')
    rows,rejected=population(repository,analysis)
    chosen=sample(rows,seed)
    output.mkdir(parents=True)
    manifest={'seed':seed,'sampling_unit':'single-target successful stage within the same historical run',
        'eligibility':'target previously unproved; stage accepted new helpers; sealed imports recoverable',
        'difficulty_proxy':'retrospectively reconstructed effective gap between consecutive contract GREEN events',
        'threshold_minutes':{'easy':[0,15],'medium':[15,60],'hard':[60,None]},
        'population':rows,'rejected':rejected,'selected':chosen,
        'model':'gpt-5.6-sol','reasoning_effort':'medium','variant_strategy':'sketch-guided-v1',
        'budget_seconds_per_case':7200,'model_calls':0,'future_proof_hints_imported':False,
        'sources':{str(analysis/name):sha(analysis/name) for name in ('green-events.csv','theory-green-gaps.csv')},
        'limitations':['success-conditioned selection; two samples per stratum',
            'historical A model differs from new model; not a causal strategy ablation',
            'effective gap includes shared theory work; not exclusive target computation',
            'fresh model conversation versus historical accumulated conversation']}
    durable(output/'selection.json',manifest,immutable=True)
    cases=[]
    for row in chosen:
        source=Path(row['source_run']);root=source/'theories'/row['theory']
        pre=Path(row['pre_commit_path']);_,before=projection(pre)
        checkpoint=output/'checkpoints'/row['name']/'checkpoint.json'
        durable(checkpoint,{'source_run':str(source),'theories':row['checkpoint_theories']},immutable=True)
        accepted=set(before);events=[]
        for path in sorted((root/'acceptance/commits').glob('*.json')):
            if int(path.stem)<=int(pre.stem):continue
            info,proofs=projection(path);delta=sorted(set(proofs)-accepted)
            if delta:events.append({'commit_id':info['commit_id'],'commit_path':str(path),
                'publication_unix_from_file_metadata':path.stat().st_mtime,'new_ids':delta})
            accepted=set(proofs)
        history={**row,'inherited_accepted_ids':sorted(before),'acceptance_events':events,
            'target_success_at_end':row['target_contract_id'] in accepted,
            'input_sha256':{str(pre):sha(pre),row['success_commit_path']:sha(row['success_commit_path'])},
            'time_limitation':'Continuous wall windows use commit publication mtime; effective strata are retrospective estimates.'}
        historical=checkpoint.with_name('historical-A.json');durable(historical,history,immutable=True)
        case={'name':row['name'],'tier':row['tier'],'theory':row['theory'],
              'target_contract_id':row['target_contract_id'],'checkpoint':str(checkpoint),
              'checkpoint_sha256':sha(checkpoint),'historical_A':str(historical)}
        generated=root/'generated';heaps=root/'heaps'
        if heaps.is_dir() and generated.is_dir():
            providers={p.name:sha(p) for p in generated.glob('*.thy')}
            # warm_provider_heaps validates exact sealed bytes again after the full fork.
            if providers:
                heap_manifest=checkpoint.with_name('provider-heap.json')
                durable(heap_manifest,{'source_heaps':str(heaps),'imports':read(root/'imports.json'),
                    'exact_generated_provider_sha256':providers,
                    'provider_session_receipt_copied':False,'standard_native_build_gate_preserved':True,
                    'scope':'sealed provider session only; generated imports verified; target proofs excluded'},immutable=True)
                case.update(native_provider_heap_manifest=str(heap_manifest),
                            native_provider_heap_manifest_sha256=sha(heap_manifest))
        cases.append(case)
    authorization=('用户要求从成功历史 step 分层随机抽取简单/中等/困难各两例，'
        '使用新策略；每例两小时；模型 gpt-5.6-sol medium；允许自行清理磁盘缓存。'
        '沿用已授权的 OpenAI 数据通道处理恢复点私有目标、语义定义、GREEN 证明和诊断。')
    for tier,_,_ in TIERS:
        config={'cases':[c for c in cases if c['tier']==tier],'model':'gpt-5.6-sol','reasoning_effort':'medium',
            'variant_strategy':'sketch-guided-v1','variant_only':True,'incremental_checkpoint_window':True,
            'wall_budget':True,'budget_effective_seconds':7200,'wall_limit_seconds':7200,
            'case_workers':2,'native_memory_mb':12288,'min_free_disk_bytes':3*1024**3,
            'independent_case_failures':True,'authorization':authorization,'future_proof_hints_imported':False,
            'selection_manifest':str(output/'selection.json'),'selection_manifest_sha256':sha(output/'selection.json')}
        durable(output/('config-'+tier+'.json'),config,immutable=True)
    return {'selected':[{k:r[k] for k in ('name','theory','target_contract_id',
        'historical_effective_gap_minutes','historical_calendar_gap_minutes')} for r in chosen],
        'population_counts':{tier:sum(r['tier']==tier for r in rows) for tier,_,_ in TIERS}}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository',type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument('--analysis',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--seed',type=int,required=True)
    args=parser.parse_args()
    print(json.dumps(prepare(args.repository,args.analysis,args.output,args.seed),ensure_ascii=False,indent=2))
