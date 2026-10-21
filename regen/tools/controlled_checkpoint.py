"""Freeze a predeclared historical commit, never the latest/future proof state."""
import argparse
import hashlib
from pathlib import Path

from regen.execution.proof_commits import durable
from regen.execution.proof_session_runner import generated_checkpoint_theory
from regen.infrastructure.incremental_log import read_artifact
from regen.tools.proof_idle_watch import projection


def prepare(source, output, *, theory, sequence, commit_id, target):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists() or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('checkpoint output must be new and independent')
    entries = {e['theory']: e for e in read_artifact(source/'execution-input.json')['files']}
    entry = entries[theory]
    directory = source/'theories'/Path(entry['file_slot']).stem
    path = directory/'acceptance/commits'/f'{sequence:06d}.json'
    record = read_artifact(path)
    if record['commit_id'] != commit_id or record['sequence'] != sequence:
        raise ValueError('predeclared checkpoint differs')
    state = record['state']
    if target not in {n['id'] for n in state['graph']['nodes']} or target in state['proofs']:
        raise ValueError('target absent or already GREEN')
    draft = state['document_source_sha256']
    draft_path = directory/'document/versions'/draft/'source.json'
    text = read_artifact(draft_path)['source']
    if hashlib.sha256(text.encode()).hexdigest() != draft:
        raise ValueError('accepted draft identity differs')
    cutoff = path.stat().st_mtime
    selected = {theory: {'commit_id': commit_id, 'draft_version': draft, 'artifacts': {}}}
    evidence = [{'theory': theory, 'commit_id': commit_id, 'commit_path': str(path),
                 'publication_unix': cutoff}]
    by_import = {generated_checkpoint_theory(e['file_slot']): e for e in entries.values()}
    visiting = set()

    def provider(name):
        row = by_import[name]
        title = row['theory']
        if title in selected:
            return
        if name in visiting:
            raise ValueError('cyclic checkpoint imports')
        visiting.add(name)
        root = source/'theories'/Path(row['file_slot']).stem
        eligible = []
        for p in sorted((root/'acceptance/commits').glob('*.json')):
            if p.stat().st_mtime <= cutoff:
                info, _ = projection(p)
                if info['event'] == 'seal':
                    eligible.append((p, info))
        if not eligible:
            raise ValueError('no temporally eligible sealed provider: '+name)
        p, info = eligible[-1]
        for dependency in read_artifact(root/'imports.json'):
            provider(dependency)
        selected[title] = {'commit_id': info['commit_id']}
        evidence.append({'theory': title, 'commit_id': info['commit_id'],
                         'commit_path': str(p), 'publication_unix': p.stat().st_mtime})
        visiting.remove(name)

    for name in read_artifact(directory/'imports.json'):
        provider(name)
    output.mkdir(parents=True, exist_ok=False)
    checkpoint = {'source_run': str(source), 'theories': selected}
    durable(output/'checkpoint.json', checkpoint, immutable=True)
    durable(output/'provenance.json', {
        'schema': 'controlled-checkpoint-v1', 'theory': theory, 'target_contract_id': target,
        'selection_rule': 'previous contract acceptance before studied generation interval',
        'cutoff_unix': cutoff, 'draft_version': draft, 'selected_commits': evidence,
        'inherited_requests': state.get('batch_requests'),
        'inherited_contracts': sum(k.startswith('contract:') for k in state['proofs']),
        'inherited_helpers': sum(k.startswith('helper:') for k in state['proofs']),
        'future_proofs_supplied': False, 'original_human_proofs_accessed': False,
        'authority': 'selection only; full fork verification remains mandatory',
    }, immutable=True)
    return {'checkpoint': str(output/'checkpoint.json'), 'theories': sorted(selected)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--theory', required=True)
    parser.add_argument('--sequence', type=int, required=True)
    parser.add_argument('--commit-id', required=True)
    parser.add_argument('--target', required=True)
    args = vars(parser.parse_args())
    print(prepare(**args))


if __name__ == '__main__':
    main()
