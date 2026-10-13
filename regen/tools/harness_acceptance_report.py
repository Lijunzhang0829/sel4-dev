"""Collect small acceptance receipts and verify the protected run stayed intact.

This records evidence; it never turns a partial or failed test into a release.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from regen.tools.harness_acceptance_baseline import (
    command, production_hashes, protected_snapshot,
)


def immutable_json(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--receipt', action='append', default=[],
                        help='Relative JSON receipt path, repeatable')
    parser.add_argument('--source', action='append', default=[],
                        help='Additional repo-relative implementation/test source to archive')
    parser.add_argument('--pre-repair-root', type=Path,
                        help='Preserved baseline tree, for explicit before/after comparison')
    parser.add_argument('--baseline-directory', default='baseline',
                        help='Relative regression directory whose manifest freezes the final implementation')
    args = parser.parse_args()
    root = args.root.resolve()
    repo = Path(__file__).resolve().parents[2]
    baseline_path = (root/args.baseline_directory/'manifest.json').resolve()
    if not baseline_path.is_relative_to(root):
        parser.error('baseline manifest must be inside validation root')
    baseline = json.loads(baseline_path.read_text())
    protected = Path(baseline['protected_before']['root'])
    if root.is_relative_to(protected):
        parser.error('report output may not be inside the protected experiment')
    final = protected_snapshot(protected)
    unchanged = baseline['production_sha256'] == production_hashes(repo)
    receipts = []
    for name in args.receipt:
        path = (root/name).resolve()
        if not path.is_relative_to(root):
            parser.error('receipt must be inside validation root')
        raw = path.read_bytes()
        value = json.loads(raw)
        receipts.append({'path': name, 'sha256': hashlib.sha256(raw).hexdigest(),
                         'result': value})
    archive = root/'validation-source'
    archive.mkdir(exist_ok=False)
    sources = {}
    source_paths = {source for folder in ['regen/tools', 'regen/tests/current']
                    for source in (repo/folder).glob('*acceptance*.py')}
    for name in args.source:
        source = (repo/name).resolve()
        if not source.is_relative_to(repo) or not source.is_file():
            parser.error('additional source must be an existing file inside the repo')
        source_paths.add(source)
    for source in sorted(source_paths):
        relative = source.relative_to(repo)
        destination = archive/relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        sources[str(relative)] = hashlib.sha256(destination.read_bytes()).hexdigest()
    comparison = None
    if args.pre_repair_root is not None:
        original = json.loads((args.pre_repair_root/'baseline/manifest.json').read_text())
        current = production_hashes(repo)
        comparison = {
            'source': str(args.pre_repair_root.resolve()),
            'protected_unchanged_since_pre_repair': original['protected_before'] == final,
            'intentionally_changed_production': [name for name, digest in current.items()
                if original['production_sha256'].get(name) != digest]}
    result = {'kind': 'small-scale-acceptance-evidence-index', 'model_calls': 0,
              'baseline_manifest': str(baseline_path.relative_to(root)),
              'experiment_resumed': False, 'production_unchanged': unchanged,
              'protected_unchanged': baseline['protected_before'] == final,
              'protected_final': final, 'receipts': receipts,
              'validation_source_sha256': sources,
              'pre_repair_comparison': comparison,
              'docker_final': command('docker', 'ps', '--format', '{{.Names}} {{.Status}}'),
              'docker_final_all': command('docker', 'ps', '-a', '--format', '{{.Names}} {{.Status}}'),
              'disk_free_bytes_final': shutil.disk_usage(root).free,
              'validation_logical_bytes': sum(p.stat().st_size for p in root.rglob('*')
                                               if p.is_file()),
              'release_ready': False,
              'notice': 'Small-scale validation only. See receipt failures and coverage limitations; '
                        'no real-model, long-theory, or repository acceptance inferred.'}
    immutable_json(root/'summary.json', result)
    print(json.dumps({key: result[key] for key in (
        'production_unchanged', 'protected_unchanged', 'model_calls',
        'release_ready', 'validation_logical_bytes', 'disk_free_bytes_final')}, indent=2))
    return 0 if (unchanged and result['protected_unchanged']
                 and (comparison is None or comparison['protected_unchanged_since_pre_repair'])) else 1


if __name__ == '__main__':
    raise SystemExit(main())
