"""Verify, replay and export versioned incident fixtures without a live run.

Usage: python3 -m regen.tools.replay_incidents {list,verify,run,export,compare}
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import tempfile
import time
import traceback


DEFAULT_FIXTURES = Path(__file__).resolve().parents[1] / 'tests/fixtures/crefine_incidents_v1'
MODULES = ('transport', 'codex', 'state', 'history')


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fixture_path(root, relative):
    """Fixture paths are data, never executable commands or host run paths."""
    root = Path(root).resolve()
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('fixture path must stay inside the package: ' + str(relative))
    path = root / path
    if any(p.is_symlink() for p in (path, *path.parents) if p != root.parent):
        raise ValueError('symlink fixture is not supported: ' + str(relative))
    if not path.resolve().is_relative_to(root):
        raise ValueError('fixture path escaped package')
    return path


def load_cases(root):
    cases = []
    for path in sorted((Path(root) / 'cases').glob('*.json')):
        case = json.loads(fixture_path(root, str(path.relative_to(root))).read_text())
        required = {'schema_version', 'id', 'title', 'component', 'category', 'status',
                    'source_steps', 'evidence', 'replays', 'limitations'}
        if not required <= case.keys() or case['schema_version'] != 1:
            raise ValueError('invalid incident case: ' + str(path))
        if not case['source_steps'] or not case['evidence'] or not case['replays']:
            raise ValueError('case needs a source, extracted evidence and a replay: ' + case['id'])
        for entry in case['evidence']:
            p = fixture_path(root, entry['path'])
            if sha256(p) != entry['sha256']:
                raise ValueError('extracted evidence changed: ' + entry['path'])
        for replay in case['replays']:
            if not {'id', 'handler', 'kind', 'oracle'} <= replay.keys():
                raise ValueError('replay lacks an independent oracle: ' + case['id'])
            if not replay['oracle']:
                raise ValueError('empty replay oracle: ' + case['id'])
        if len({r['id'] for r in case['replays']}) != len(case['replays']):
            raise ValueError('duplicate replay id: ' + case['id'])
        cases.append(case)
    if not cases or len({case['id'] for case in cases}) != len(cases):
        raise ValueError('missing cases or duplicate incident id')
    return cases


def verify(root):
    root = Path(root).resolve()
    manifest = json.loads(fixture_path(root, 'manifest.json').read_text())
    if manifest.get('schema_version') != 1:
        raise ValueError('unsupported fixture manifest')
    expected = manifest['files']
    actual = {str(p.relative_to(root)) for p in root.rglob('*')
              if (p.is_file() or p.is_symlink()) and p != root / 'manifest.json'}
    if actual != set(expected):
        raise ValueError('fixture file set changed; review and update manifest explicitly')
    for name, digest in expected.items():
        if sha256(fixture_path(root, name)) != digest:
            raise ValueError('fixture checksum mismatch: ' + name)
    cases = load_cases(root)
    if sorted(case['id'] for case in cases) != manifest['case_ids']:
        raise ValueError('manifest case ids differ')
    return cases


def handlers():
    result = {}
    for name in MODULES:
        module = importlib.import_module('regen.replay.incident_checks_' + name)
        for key, function in module.HANDLERS.items():
            if key in result:
                raise ValueError('duplicate replay handler: ' + key)
            result[key] = function
    return result


def implementation_identity():
    """Make before/after reports attributable to the implementation tested."""
    root = Path(__file__).resolve().parents[1]
    files = {str(p.relative_to(root)): sha256(p) for p in sorted(root.rglob('*.py'))
             if 'tests' not in p.relative_to(root).parts and '__pycache__' not in p.parts}
    native = {str(p.relative_to(root)): sha256(p) for p in sorted(root.rglob('*.ML'))
              if 'tests' not in p.relative_to(root).parts}
    vero = {str(p.relative_to(root.parent)): sha256(p)
            for p in sorted((root.parent / 'vero-isabelle/src').rglob('*.py'))}
    digest = hashlib.sha256(json.dumps({'python': files, 'ML': native, 'vero': vero}, sort_keys=True).encode()).hexdigest()
    return {'sha256': digest, 'python_files': files, 'native_ml_files': native,
            'vero_python_files': vero}


def run_cases(root, cases):
    if not __debug__:
        raise ValueError('replay assertions require Python without -O or PYTHONOPTIMIZE')
    registry = handlers()
    rows = []
    for case in cases:
        for replay in case['replays']:
            started = time.monotonic()
            row = {'case_id': case['id'], 'replay_id': replay['id'],
                   'kind': replay['kind'], 'issue_status': case['status'],
                   'oracle': replay['oracle']}
            try:
                function = registry[replay['handler']]
                payload = deepcopy(case)
                payload['_replay'] = deepcopy(replay)
                with tempfile.TemporaryDirectory(prefix='crefine-incident-replay-') as temp:
                    measurements = function(payload, Path(root), Path(temp))
                    if not isinstance(measurements, dict):
                        raise TypeError('replay handler must return a measurement object')
                    json.dumps(measurements, allow_nan=False)
                    row['measurements'] = measurements
                row['status'] = 'passed'
            except Exception as error:
                row.update(status='failed', error_type=type(error).__name__,
                           error=str(error), traceback=traceback.format_exc(limit=8))
            row['elapsed_seconds'] = time.monotonic() - started
            rows.append(row)
    return {'schema_version': 1, 'observed_at': datetime.now(timezone.utc).isoformat(),
        'fixture_manifest_sha256': sha256(Path(root) / 'manifest.json'),
        'implementation': implementation_identity(),
        'case_count': len(cases), 'replay_count': len(rows),
        'passed': sum(row['status'] == 'passed' for row in rows),
        'failed': sum(row['status'] == 'failed' for row in rows), 'results': rows,
        'scope': 'offline fixtures; no model, SSH, Docker, live ledger or native Isabelle',
        'interpretation': 'Passing an observational replay does not mark an open issue fixed; '
                          'synthetic native outcomes are not new GREEN evidence.'}


def compare(before, after):
    def keyed(report):
        rows = report['results']
        result = {(r['case_id'], r['replay_id']): r for r in rows}
        if len(result) != len(rows):
            raise ValueError('duplicate result identity')
        return result
    old, new = keyed(before), keyed(after)
    keys = sorted(set(old) | set(new))
    return {'same_fixture_manifest': before['fixture_manifest_sha256'] == after['fixture_manifest_sha256'],
        'before_implementation': before['implementation']['sha256'],
        'after_implementation': after['implementation']['sha256'],
        'regressions': [list(key) for key in keys
                        if old.get(key, {}).get('status') == 'passed'
                        and new.get(key, {}).get('status') != 'passed'],
        'results': [{'case_id': key[0], 'replay_id': key[1],
                     'before_status': old.get(key, {}).get('status', 'missing'),
                     'after_status': new.get(key, {}).get('status', 'missing'),
                     'before_measurements': old.get(key, {}).get('measurements'),
                     'after_measurements': new.get(key, {}).get('measurements')}
                    for key in keys]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, default=DEFAULT_FIXTURES)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list')
    sub.add_parser('verify')
    run = sub.add_parser('run')
    run.add_argument('--case', action='append', default=[])
    run.add_argument('--output', type=Path)
    export = sub.add_parser('export')
    export.add_argument('case_id')
    export.add_argument('--output', type=Path, required=True)
    comparison = sub.add_parser('compare')
    comparison.add_argument('before', type=Path)
    comparison.add_argument('after', type=Path)
    args = parser.parse_args()
    if args.command == 'compare':
        result = compare(json.loads(args.before.read_text()), json.loads(args.after.read_text()))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if not result['same_fixture_manifest'] else int(bool(result['regressions']))
    root = args.fixtures.resolve()
    cases = verify(root)
    if args.command == 'verify':
        print(json.dumps({'verified': True, 'cases': len(cases),
                          'manifest_sha256': sha256(root / 'manifest.json')}))
    elif args.command == 'list':
        print(json.dumps([{key: c[key] for key in ('id', 'title', 'category', 'status', 'source_steps')}
                          for c in cases], ensure_ascii=False, indent=2))
    elif args.command == 'export':
        selected = [c for c in cases if c['id'] == args.case_id]
        if not selected:
            parser.error('unknown case: ' + args.case_id)
        case = selected[0]
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / 'cases').mkdir()
        (args.output / 'cases/case.json').write_text(json.dumps(case, ensure_ascii=False, indent=2) + '\n')
        for evidence in case['evidence']:
            dest = args.output / evidence['path']
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(fixture_path(root, evidence['path']), dest)
        exported = {str(p.relative_to(args.output)): sha256(p)
                    for p in sorted(args.output.rglob('*')) if p.is_file()}
        (args.output / 'manifest.json').write_text(json.dumps({
            'schema_version': 1, 'case_ids': [case['id']], 'files': exported,
            'exported_from_manifest_sha256': sha256(root / 'manifest.json'),
            'scope': 'standalone offline data; requires the repository replay implementation'
        }, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'exported': case['id'], 'output': str(args.output),
                          'native_replay': 'See case limitations and frozen-context provenance; '
                                           'this export does not launch an experiment.'}))
    else:
        wanted = set(args.case)
        if wanted - {case['id'] for case in cases}:
            parser.error('unknown cases: ' + ', '.join(sorted(wanted - {c['id'] for c in cases})))
        selected = [case for case in cases if not wanted or case['id'] in wanted]
        result = run_cases(root, selected)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({k: result[k] for k in ('case_count', 'replay_count', 'passed', 'failed')},
                         ensure_ascii=False))
        for row in result['results']:
            print(row['status'].upper(), row['case_id'], row['replay_id'], row.get('error', ''))
        return int(bool(result['failed']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
