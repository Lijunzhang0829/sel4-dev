"""Read-only inspection of inline and incremental experiment artifacts."""
import argparse
import json
from pathlib import Path
from regen.infrastructure.incremental_log import read_artifact, artifact_fingerprint


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation',choices=['show','events','verify-storage','summary'])
    p.add_argument('path',type=Path)
    args=p.parse_args()
    if args.operation=='show':
        print(json.dumps(read_artifact(args.path),ensure_ascii=False,indent=2))
    elif args.operation=='verify-storage':
        refs=artifact_fingerprint(args.path)
        read_artifact(args.path)
        print(json.dumps({'readable':True,'reachable_objects':len(refs),
                          'notice':'Storage integrity only, not proof acceptance.'}))
    elif args.operation=='summary':
        from regen.infrastructure.experiment_ledger import Ledger
        from regen.infrastructure.experiment_activity import experiment_summary
        paths=sorted((args.path/'events').glob('*.json'))
        if not paths:p.error('no experiment ledger events')
        run_id=read_artifact(paths[0])['run_id']
        ledger=Ledger(args.path,run_id)
        print(json.dumps(experiment_summary(ledger),ensure_ascii=False,indent=2))
    else:
        # JSONL is an exported view. Atomic numbered files remain authoritative.
        for path in sorted(args.path.glob('*.json')):
            value=read_artifact(path)
            row={'artifact':str(path)}
            row.update({k:value[k] for k in ('sequence','event','commit_id','parent','call_number','event_id','kind','run_id','time_unix','data') if k in value})
            if 'request' in value:row['request']=value['request'].get('action') if isinstance(value['request'],dict) else None
            print(json.dumps(row,ensure_ascii=False))


if __name__=='__main__':main()
