"""Freeze an exact historical checkpoint for an isolated diagnostic comparison."""
import argparse
import hashlib
from pathlib import Path

from regen.execution.proof_commits import durable
from regen.execution.proof_session_runner import generated_checkpoint_theory
from regen.infrastructure.incremental_log import read_artifact
from regen.tools.proof_idle_watch import projection


def prepare(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    theory = 'Scheduler_Dispatch'
    path = source/'theories'/theory/'acceptance/commits/000039.json'
    expected = '95fa7388e2c79d41edf8825061ccf70d74532795f9778113a81030aef2fb63eb'
    meta, _ = projection(path)
    if meta['commit_id'] != expected or meta['event'] != 'document':
        raise ValueError('historical checkpoint identity mismatch')
    cutoff = path.stat().st_mtime
    draft = '0195ac7ecc741fc5982ef383a66046beb91816fa861d96ebc053edd1a0675d31'
    text = read_artifact(source/'theories'/theory/'document/versions'/draft/'source.json')['source']
    if hashlib.sha256(text.encode()).hexdigest() != draft:
        raise ValueError('checkpoint source identity mismatch')
    entries = {generated_checkpoint_theory(e['file_slot']):e for e in
               read_artifact(source/'execution-input.json')['files']}
    selected = {theory:{'commit_id':expected, 'draft_version':draft, 'artifacts':{}}}
    evidence = [{'theory':theory, 'commit_path':str(path), 'commit_id':expected,
                 'publication_unix':cutoff}]
    visiting = set()
    def provider(name):
        entry = entries[name]
        title = entry['theory']
        if title in selected:
            return
        if name in visiting:
            raise ValueError('cyclic frozen imports')
        visiting.add(name)
        directory=source/'theories'/Path(entry['file_slot']).stem
        eligible=[]
        for commit in sorted((directory/'acceptance/commits').glob('*.json')):
            if commit.stat().st_mtime <= cutoff:
                info, _ = projection(commit)
                if info['event']=='seal':
                    eligible.append((commit,info))
        if not eligible:
            raise ValueError('no temporally eligible sealed provider: '+name)
        commit,info=eligible[-1]
        for dependency in read_artifact(directory/'imports.json'):
            provider(dependency)
        selected[title]={'commit_id':info['commit_id']}
        evidence.append({'theory':title, 'commit_path':str(commit),
                         'commit_id':info['commit_id'], 'publication_unix':commit.stat().st_mtime})
        visiting.remove(name)
    for name in read_artifact(source/'theories'/theory/'imports.json'):
        provider(name)
    checkpoint={'source_run':str(source),'theories':selected}
    output.mkdir(parents=True,exist_ok=False)
    durable(output/'checkpoint.json', checkpoint, immutable=True)
    durable(output/'provenance.json', {
        'purpose':'Fresh isolated A/B diagnostic windows, not a production resume or cost reset',
        'model':'gpt-6-astra','reasoning_effort':'medium','theory':theory,
        'counterfactual_window_effective_seconds':3600,
        'terminal':'First newly accepted contract or total effective generation window, whichever occurs first',
        'cutoff_unix':cutoff,'draft_version':draft,'selected_commits':evidence,
        'proof_authority':'fork_workspace_continuation must validate full chains; this manifest alone is not proof authority',
        'future_proof_hints_imported':False,'production_resumed':False,
    }, immutable=True)
    return checkpoint


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    value=prepare(args.source,args.output)
    print({'theories':sorted(value['theories']), 'checkpoint':str(args.output/'checkpoint.json')})
