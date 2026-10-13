"""Export current application code, including untracked additions, not results."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile


def export(repository, output):
    repository, output = Path(repository).resolve(), Path(output).resolve()
    if any(output.is_relative_to(repository/name) for name in ('regen','tools')):
        raise ValueError('release output must be outside source roots')
    output.mkdir(parents=True,exist_ok=False)
    files = {}
    for folder in ('regen','tools'):
        for path in sorted((repository/folder).rglob('*')):
            if '__pycache__' in path.parts or path.suffix not in {'.py','.ML','.sh','.json','.md','.thy','.toml','.txt'}:
                continue
            if path.is_symlink():
                raise ValueError('release source cannot be a symlink: '+str(path))
            if path.is_file():
                raw=path.read_bytes()
                files[str(path.relative_to(repository))] = {
                    'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
    archive=output/'application.tar.gz'
    with tarfile.open(archive,'w:gz') as bundle:
        for name in files:
            bundle.add(repository/name,arcname=name,recursive=False)
    for name, row in files.items():
        if hashlib.sha256((repository/name).read_bytes()).hexdigest()!=row['sha256']:
            raise ValueError('source changed during release export: '+name)
    manifest={'format':'harness-application-release-v1','files':files,
        'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
        'includes_uncommitted_and_untracked_code':True,
        'excludes':['reports','proof results','heaps','credentials','.git'],
        'machine_b_accepted':False,'vero_adapter_accepted':False}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
    return {'files':len(files),'archive_bytes':archive.stat().st_size,
            'archive_sha256':manifest['archive_sha256']}


def verify(repository, manifest):
    repository=Path(repository).resolve()
    data=json.loads(Path(manifest).read_text())
    for name,row in data['files'].items():
        path=repository/name
        if not path.resolve().is_relative_to(repository) or path.is_symlink():
            raise ValueError('release path escapes root')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:
            raise ValueError('release file mismatch: '+name)
    return {'verified_files':len(data['files'])}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('operation',choices=['export','verify'])
    parser.add_argument('--repository',type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output',type=Path)
    parser.add_argument('--manifest',type=Path)
    args=parser.parse_args()
    if args.operation=='export':
        if args.output is None:parser.error('--output required')
        result=export(args.repository,args.output)
    else:
        if args.manifest is None:parser.error('--manifest required')
        result=verify(args.repository,args.manifest)
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
