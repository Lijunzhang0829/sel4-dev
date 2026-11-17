"""Verify the retained dataset, or materialize sources in an external work area.

Verification revalidates historical kernel receipts; it does not run Isabelle.
Default materialization copies only fixed C, definitions and unproved contracts.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

BASE = Path(__file__).resolve().parents[1]
DATASET = BASE / 'dataset'


def read(path):
    return json.loads(path.read_text())


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_input(root=DATASET):
    root = Path(root)
    manifest = read(root / 'input/manifest.json')
    files = {str(p.relative_to(root / 'input')): sha(p)
             for p in (root / 'input').rglob('*')
             if p.is_file() and p.name != 'manifest.json'}
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if files != manifest['input_files_sha256'] or digest != manifest['dataset_binding']:
        raise ValueError('frozen input binding changed')
    selection = read(root / 'input/contract-selection.json')
    entries = read(root / 'input/specs.json')['specs']
    targets = read(root / 'input/targets.json')['targets']
    background = read(root / 'input/background.json')['helpers']
    if (len(entries) != 100 or len(targets) != 91 or len(background) != 9
            or [t for t in entries if t['generation_required']] != targets
            or [t for t in entries if not t['generation_required']] != background
            or selection['active_target_ids'] != [t['id'] for t in targets]
            or selection['background_declaration_ids'] != [t['id'] for t in background]
            or selection['disabled_target_ids'] != ['0054']
            or any(t['available_as_premise'] for t in entries)):
        raise ValueError('active target/background selection changed')
    return manifest


def extract_checked(root, destination):
    root = Path(root)
    meta = read(root / 'acceptance/manifest.json')
    if meta['dataset_binding'] != verify_input(root)['dataset_binding']:
        raise ValueError('acceptance bundle has a different dataset binding')
    if meta['archive'] != 'checked-evidence.tar.gz':
        raise ValueError('unexpected acceptance archive path')
    archive = root / 'acceptance' / meta['archive']
    if sha(archive) != meta['archive_sha256']:
        raise ValueError('acceptance archive hash changed')
    destination = Path(destination).resolve()
    with tarfile.open(archive) as bundle:
        members = bundle.getmembers()
        if len(members) != meta['files'] or sum(m.size for m in members) != meta['uncompressed_bytes']:
            raise ValueError('acceptance archive inventory changed')
        names = set()
        for member in members:
            path = Path(member.name)
            if (not member.isfile() or path.is_absolute() or '..' in path.parts
                    or not path.parts or path.parts[0] not in ('dataset', 'dataset-revised-003')
                    or member.name in names
                    or not (destination / path).resolve().is_relative_to(destination)):
                raise ValueError('unsafe acceptance archive entry')
            names.add(member.name)
        bundle.extractall(destination, filter='data')
    if read(destination / 'dataset/input/manifest.json') != read(root / 'input/manifest.json'):
        raise ValueError('archived and current manifest differ')
    return meta


def verify(root=DATASET):
    root = Path(root)
    manifest = verify_input(root)
    with tempfile.TemporaryDirectory(prefix='binomial-retained-receipts-') as tmp:
        extract_checked(root, tmp)
        proc = subprocess.run([sys.executable, '-B',
            str(Path(tmp) / 'dataset/tools/publish.py'), '--verify-only'],
            cwd=tmp, capture_output=True, text=True, timeout=120,
            env={**os.environ, 'PYTHONPATH': '', 'PYTHONDONTWRITEBYTECODE': '1'})
        if proc.returncode:
            raise ValueError('archived publication check failed: ' + proc.stderr[-3000:])
        result = json.loads(proc.stdout)
    if (result['targets'], result['positive'], result['relative_negative'], result['unresolved']) != (91, 83, 8, []):
        raise ValueError('retained acceptance outcome changed')
    return dict(status='pass', dataset_binding=manifest['dataset_binding'], targets=91,
        positive=83, context_refuted=8, disabled=['0054'],
        result_scope='owned-context-outcomes-v1',
        verification='historical native receipt/source revalidation',
        new_native_run=False, model_calls=0, runtime_ready=False,
        runtime_requirement='external pinned Isabelle2026, AFP, and rebuilt parent heaps')


def materialize(output, *, root=DATASET, with_acceptance=False, isabelle=None, afp=None):
    root = Path(root).resolve()
    output = Path(output).resolve()
    if output.is_relative_to(BASE.parent.resolve()):
        raise ValueError('build output must be outside c-algorithms')
    manifest = verify_input(root)
    if with_acceptance:
        verify(root)
    # Dependencies are external; no missing paths are silently substituted.
    if (isabelle is None) != (afp is None):
        raise ValueError('provide both --isabelle and --afp, or neither for source-only staging')
    if isabelle is not None:
        isabelle, afp = Path(isabelle).resolve(), Path(afp).resolve()
        if not (isabelle / 'bin/isabelle').is_file() or not (afp / 'AutoCorres2/ROOT').is_file():
            raise ValueError('external Isabelle/AFP installation is missing')
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(root / 'input/parent', output / 'parent')
    shutil.copytree(root / 'input/isabelle', output / 'theories')
    for name in ('targets.json', 'background.json', 'environment.json', 'manifest.json'):
        shutil.copyfile(root / 'input' / name, output / name)
    targets = read(root / 'input/targets.json')['targets']
    records = [dict(contract_id=t['id'], name=t['name'], exact_statement=t['statement'],
        owner_theory='Binomial', contract_owned_context=[] if t['context'] == 'global'
        else ['context ' + t['context'] + ' begin']) for t in targets]
    (output / 'contracts.jsonl').write_text(''.join(json.dumps(c) + '\n' for c in records))
    if with_acceptance:
        private = output / 'private-acceptance'
        private.mkdir()
        meta = extract_checked(root, private)
        source = private / meta['cold_replay_source']
        (source / 'ROOT').write_text(
            'session Binomial_Checked_Receipts = Calg_Project_Base +\n'
            ' options [document=false, timeout=1500]\n'
            ' theories Current_Target_Receipts\n')
    commands = []
    if isabelle is not None:
        common = [str(isabelle / 'bin/isabelle'), 'build', '-b', '-j', '1',
                  '-o', 'threads=4', '-d', str(afp), '-d', str(output / 'parent')]
        commands.append(common + ['-D', str(output / 'theories'), 'Binomial_Current_Dataset'])
        if with_acceptance:
            commands.append(common + ['-D', str(source), 'Binomial_Checked_Receipts'])
    result = dict(status='sources_materialized', dataset_binding=manifest['dataset_binding'],
        output=str(output), generation_targets=91, background_declarations=9,
        target_proofs_supplied=with_acceptance, model_calls=0, new_native_run=False,
        runtime_ready=False, commands=commands,
        note='Run builds in the pinned environment; successful source staging is not a native preflight.')
    (output / 'materialization.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['verify', 'materialize'])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--with-acceptance', action='store_true', help='trusted preparation only; exports known proofs')
    parser.add_argument('--isabelle', type=Path, help='external pinned installation root')
    parser.add_argument('--afp', type=Path, help='external pinned AFP thys directory')
    args = parser.parse_args()
    if args.action == 'verify':
        if args.output or args.with_acceptance or args.isabelle or args.afp:
            parser.error('verify takes no materialization options')
        value = verify()
    else:
        if args.output is None:
            parser.error('materialize requires --output')
        value = materialize(args.output, with_acceptance=args.with_acceptance,
                            isabelle=args.isabelle, afp=args.afp)
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
