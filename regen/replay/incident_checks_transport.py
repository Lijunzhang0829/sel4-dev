"""Offline infrastructure replays for the CRefine VERO transport incidents.

Historical evidence is immutable input. Generated payloads and all mutations live
under the caller's scratch directory; no provider, Docker, SSH or prover is used.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch


def _load(root, name):
    return json.loads((root / 'evidence' / name).read_text(encoding='utf-8'))


def _reject(call, expected=(ValueError,)):
    try:
        call()
    except expected as error:
        return type(error).__name__
    raise AssertionError('invalid input was accepted')


def frame_truncation(case, fixture_root, scratch):
    """Evaluate the original extracted reader expression on a generated frame."""
    from regen.tools.vero_application_tool import read_json_frame
    evidence = _load(fixture_root, 'vero_transport_step1792.json')
    assert evidence['tool_event']['exit_code'] == 1
    assert evidence['tool_event']['item_id'] == 'item_1792'
    old_source = (fixture_root / 'evidence' / 'vero_transport_original_client.py.txt').read_text()
    assert hashlib.sha256(old_source.encode()).hexdigest() == evidence['old_client']['sha256']
    candidates = [node for node in ast.walk(ast.parse(old_source))
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and isinstance(node.func.value, ast.Name)
                  and node.func.value.id == 'json' and node.func.attr == 'loads']
    assert len(candidates) == 1
    reader = compile(ast.Expression(candidates[0]), '<extracted-old-client-reader>', 'eval')
    size = case['_replay']['parameters']['diagnostic_bytes']
    payload = {'diagnostics': {'Vero_Capability_Core': 'x' * size},
               'modules': {'Vero_Capability_Core': {'status': 'build_error'}}}
    raw = json.dumps(payload, separators=(',', ':')).encode() + b'\n'
    negative = _reject(lambda: eval(reader, {'__builtins__': {}, 'json': json},
                                    {'stream': io.BytesIO(raw)}), (json.JSONDecodeError,))
    restored = read_json_frame(io.BytesIO(raw))
    assert restored == payload
    missing_terminator = _reject(lambda: read_json_frame(io.BytesIO(b'{"ok":true}')))
    over_guard = _reject(lambda: read_json_frame(io.BytesIO(raw), max_bytes=16 * 1024 * 1024))
    return {'generated_frame_bytes': len(raw), 'original_reader_negative_control': negative,
            'current_reader': 'complete_object', 'unterminated_frame': missing_terminator,
            'explicit_oversize_rejection': over_guard,
            'historical_request_id': evidence['request_id']}


def diagnostic_paging(case, fixture_root, scratch):
    from regen.tools.vero_tool_protocol import preview_diagnostic, read_diagnostic_chunk
    historical = _load(fixture_root, 'vero_transport_live_pages.json')
    logical_bytes = historical['original_log_bytes']
    parameters = case['_replay']['parameters']
    assert parameters['logical_log_bytes'] == logical_bytes
    assert parameters['page_bytes'] == 65536
    head = (b'generated native feedback\n' * 3000)[:65536]
    tail = (b'generated failed proof tail\n' * 3000)[:65536]
    log = scratch / 'sparse-diagnostic.log'
    with log.open('wb') as stream:
        stream.write(head)
        stream.seek(logical_bytes - len(tail))
        stream.write(tail)
    before = log.stat()
    text, metadata = preview_diagnostic(log)
    assert metadata['total_bytes'] == logical_bytes
    assert metadata['truncated'] is True
    assert metadata['head_bytes'] + metadata['tail_bytes'] <= 65536
    assert 'bytes omitted' in text
    wire_bytes = len(json.dumps({'diagnostics': {'Vero_Capability_Core': text},
                                 'diagnostics_metadata': metadata}).encode())
    assert wire_bytes < 400000
    observed = []
    for archived_page in historical['pages']:
        page = read_diagnostic_chunk(log, archived_page['offset'], 65536)
        expected = head if archived_page['offset'] == 0 else tail
        assert page['text'].encode() == expected
        for key in ('offset', 'next_offset', 'returned_bytes', 'total_bytes', 'eof'):
            assert page[key] == archived_page[key]
        observed.append({key: page[key] for key in ('offset', 'next_offset', 'eof')})
    after = log.stat()
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
    unicode_log = scratch / 'unicode-boundaries.log'
    original = 'prefix 中🦊 e\u0301\nend\n'
    unicode_log.write_text(original, encoding='utf-8')
    chunks, offset = [], 0
    while True:
        page = read_diagnostic_chunk(unicode_log, offset, 1)
        chunks.append(page['text'])
        assert page['next_offset'] > offset
        if page['eof']:
            break
        offset = page['next_offset']
    assert ''.join(chunks) == original
    invalid = _reject(lambda: read_diagnostic_chunk(log, logical_bytes + 1))
    # The original service embedded the complete log in diagnostics. Its source
    # bytes alone exceed the old 16 MiB reader; no 785 MB allocation is necessary.
    assert logical_bytes > 16 * 1024 * 1024
    service_wire = _check_response_bounds(scratch)
    return {'logical_log_bytes': logical_bytes, 'allocated_bytes': after.st_blocks * 512,
            'bounded_wire_bytes': wire_bytes, 'pages': observed,
            'utf8_roundtrip': True, 'invalid_offset': invalid,
            'actual_check_dispatch_wire_bytes': service_wire,
            'old_unbounded_response_lower_bound_bytes': logical_bytes}


def _check_response_bounds(scratch):
    """Exercise the production check response assembly, not only its helpers."""
    from regen.tools.vero_serial_application import Service, frozen_plan_binding
    vero_source = Path(__file__).resolve().parents[2] / 'vero-isabelle' / 'src'
    sys.path.insert(0, str(vero_source))
    try:
        from vero.isabelle.benchmark import prepare, render, extract
        from vero.isabelle.planning import compile_plan
    finally:
        sys.path.remove(str(vero_source))
    root = scratch / 'check-route'
    root.mkdir()
    inventory = root / 'contracts.jsonl'
    inventory.write_text(json.dumps({'contract_id': 'fixture-c0001', 'name': 'tiny',
        'owner_theory': 'A', 'exact_statement': 'lemma tiny: "True"'}) + '\n')
    source = prepare(inventory, root / 'benchmark', base_import='Main')
    plan = {'version': 1, 'source_benchmark_id': source['benchmark_id'],
        'modules': [{'name': 'Vero_A', 'contracts': ['c0001'], 'imports': [],
                     'rationale': 'generated infrastructure fixture'}], 'execution_order': ['Vero_A']}
    bench = compile_plan(source, plan)
    workspace = root / 'workspace'
    workspace.mkdir()
    render(bench, workspace, {'c0001:prove': 'by simp'})
    service = object.__new__(Service)
    service.bench, service.workspace, service.dataset = bench, workspace, root
    service.output, service.modules = root / 'requests', {'Vero_A'}
    service.frozen_plan, service.extract = frozen_plan_binding(bench, plan), extract
    service.checker = SimpleNamespace(observer=None)
    verdict = {'contracts': {'c0001': {'status': 'build_error', 'polarity': 'prove'}},
        'modules': {'Vero_A': {'status': 'build_error', 'returncode': 1, 'audited': []}},
        'complete': False, 'proved_specs': 0, 'integration': None}
    log_path = []
    def evaluate(_bench, _artifact, output, _checker):
        output.mkdir()
        log = output / 'Vero_A.log'
        with log.open('wb') as stream:
            for _ in range(17):
                stream.write(b'x' * (1024 * 1024))
            stream.write(b'\nEND generated failed proof\n')
        log_path.append(log)
        return deepcopy(verdict)
    with patch('vero.isabelle.checker.evaluate', side_effect=evaluate) as mocked:
        result = service.dispatch({'cmd': 'check', 'module': 'Vero_A'})
    mocked.assert_called_once()
    assert all(result[key] == value for key, value in verdict.items())
    metadata = result['diagnostics_metadata']['Vero_A']
    assert metadata['truncated'] and metadata['total_bytes'] == log_path[0].stat().st_size
    wire_bytes = len(json.dumps(result).encode())
    assert wire_bytes < 400000
    assert (log_path[0].parent.parent / 'response.json').stat().st_size < 400000
    tail_bytes = len(b'END generated failed proof\n')
    page = service.dispatch({'cmd': 'diagnostic', 'request_id': metadata['request_id'],
        'module': 'Vero_A', 'offset': metadata['total_bytes'] - tail_bytes, 'limit': 65536})
    assert page['eof'] and page['text'].endswith('END generated failed proof\n')
    return wire_bytes


def archived_completion(case, fixture_root, scratch):
    from regen.tools.vero_serial_application import Service
    receipt = _load(fixture_root, 'vero_transport_archive_receipt.json')
    request_id = Path(receipt['source']).parent.name
    module = 'Vero_Capability_Core'
    service = object.__new__(Service)
    service.output = scratch / 'requests'
    service.modules = {module}
    forbidden = Mock(side_effect=AssertionError('diagnostic read invoked proof authority'))
    service.checker = SimpleNamespace(observer=SimpleNamespace(
        stop=SimpleNamespace(check=Mock()), reconcile_contracts=forbidden,
        select_theory=forbidden, ledger=forbidden), check=forbidden)
    service.verify_plan = forbidden
    service.extract = forbidden
    attempt = service.output / request_id
    (attempt / 'evaluation').mkdir(parents=True)
    log = attempt / 'evaluation' / (module + '.log')
    text = 'generated archived feedback 中🦊\n'
    log.write_text(text, encoding='utf-8')
    request = {'cmd': 'diagnostic', 'request_id': request_id, 'module': module}
    incomplete = _reject(lambda: service.dispatch(request))
    archive = attempt / 'response.json.gz'
    archive.write_bytes(gzip.compress(b'{"complete":false}\n', mtime=0))
    assert not (attempt / 'response.json').exists()  # old plain-only completion predicate fails
    before = {p: p.read_bytes() for p in (archive, log)}
    with patch('regen.tools.vero_serial_application.snapshot', forbidden):
        page = service.dispatch(request)
    assert page['text'] == text and page['eof']
    assert before == {p: p.read_bytes() for p in (archive, log)}
    forbidden.assert_not_called()
    archive.unlink()
    archive.symlink_to(log)
    symlink = _reject(lambda: service.dispatch(request), (ValueError, OSError))
    return {'historical_request_id': request_id, 'historical_archive_bytes': receipt['archive_bytes'],
            'plain_only_negative_control': 'completion_not_found', 'gzip_marker': 'readable',
            'no_completion_marker': incomplete, 'symlink_marker': symlink,
            'proof_state_mutations': 0}


def runtime_binding(case, fixture_root, scratch):
    from regen.tools.vero_serial_application import runtime_agent_environment
    evidence = _load(fixture_root, 'vero_runtime_stale_mount.json')
    repaired = _load(fixture_root, 'vero_runtime_container_probe.json')
    assert evidence['help_probe']['exit_code'] == 2
    assert repaired['diagnostic_help_exit_code'] == 0
    assert evidence['mount']['sha256'] != repaired['mounted_client_sha256']
    old = scratch / 'old-release'
    package = old / 'regen' / 'tools'
    package.mkdir(parents=True)
    (old / 'regen' / '__init__.py').write_text('')
    (package / '__init__.py').write_text('')
    (package / 'vero_isolated_exec.py').write_text('OLD_TRANSPORT = True\n')
    (package / 'vero_application_tool.py').write_text('# generated stale mount sentinel\n')
    workspace = scratch / 'workspace'
    workspace.mkdir()
    config = {'env': {'PYTHONPATH': str(old), 'SENTINEL': 'preserved'}}
    frozen = deepcopy(config)
    probe = ('import json, regen.tools.vero_isolated_exec as t; '
             'from pathlib import Path; print(json.dumps({"transport": t.__file__, '
             '"client": str(Path(t.__file__).with_name("vero_application_tool.py").resolve())}))')
    def resolve(env):
        return json.loads(subprocess.check_output([sys.executable, '-c', probe],
            cwd=workspace, env={**os.environ, **env}, timeout=20))
    old_result = resolve(config['env'])
    runtime = runtime_agent_environment(config)
    result = resolve(runtime)
    current_tools = Path(__file__).resolve().parents[1] / 'tools'
    assert Path(old_result['transport']).parent == package
    assert Path(result['transport']) == current_tools / 'vero_isolated_exec.py'
    assert Path(result['client']) == current_tools / 'vero_application_tool.py'
    assert runtime['SENTINEL'] == 'preserved' and config == frozen
    return {'old_runtime_negative_control': 'selected_stale_release',
            'current_runtime': 'selected_current_transport_and_client',
            'frozen_config_unchanged': True,
            'historical_frozen_pythonpath': repaired['frozen_pythonpath'],
            'actual_container_probe_included': True,
            'offline_replay_requires_docker': False}


def migration_chain(case, fixture_root, scratch):
    from regen.execution.proof_commits import digest
    from regen.infrastructure.experiment_ledger import Ledger
    from regen.tools.vero_serial_application import (
        EFFECTIVE_BUDGET_POLICY, PREFIX_REVALIDATION_MIGRATION_FROM,
        TOOL_TRANSPORT_MIGRATION_FROM, TOOL_RUNTIME_MIGRATION_FROM,
        expected_runtime_config, frozen_plan_binding, prior_tool_transport_migration,
        record_prefix_revalidation_migration, record_tool_transport_migration,
        record_tool_runtime_migration, tool_runtime_migration_binding,
        validate_resume_artifacts,
    )
    source = _load(fixture_root, 'vero_runtime_migration_chain.json')
    planned = {'benchmark_id': 'offline-replay', 'contracts': [{'id': 'c1', 'module': 'Vero_A'}]}
    plan = {'modules': [{'name': 'Vero_A', 'contracts': ['c1'], 'imports': []}],
            'execution_order': ['Vero_A']}
    frozen = frozen_plan_binding(planned, plan)
    base = {'client_path': '/offline/codex', 'model': 'gpt-5.6-sol',
            'reasoning_effort': 'xhigh', 'budget_mode': 'effective-time',
            'effective_seconds_limit': 172800, 'include_planning_time': True,
            'activity_policy': EFFECTIVE_BUDGET_POLICY, 'native_slots': 1,
            'shared_host_slots': 2, 'application': '/original/regen/tools/server.py',
            'application_sha256': PREFIX_REVALIDATION_MIGRATION_FROM,
            'application_dependencies': {'execution/vero_checker_adapter.py': 'a' * 64,
                'infrastructure/vero_experiment_observer.py': 'b' * 64,
                'infrastructure/effective_time_budget.py': 'c' * 64}}
    old = {**base, 'frozen_plan': frozen}
    prefix_binding = {**base, 'application': '/prefix/regen/tools/server.py',
        'application_sha256': TOOL_TRANSPORT_MIGRATION_FROM,
        'application_dependencies': {**base['application_dependencies'],
            'execution/vero_checker_adapter.py': 'd' * 64,
            'infrastructure/vero_experiment_observer.py': 'e' * 64,
            'execution/vero_prefix_revalidation.py': 'f' * 64}}
    transport_binding = {**prefix_binding, 'application': '/transport/regen/tools/server.py',
        'application_sha256': TOOL_RUNTIME_MIGRATION_FROM,
        'application_dependencies': {**prefix_binding['application_dependencies'],
            'tools/vero_application_tool.py': source['transport']['new_application_dependencies']['tools/vero_application_tool.py'],
            'tools/vero_tool_protocol.py': source['transport']['new_application_dependencies']['tools/vero_tool_protocol.py']}}
    new = {**transport_binding, 'application': '/runtime/regen/tools/server.py',
           'application_sha256': source['runtime']['new_application_sha256']}
    for directory in ('workspace', 'run'):
        (scratch / directory).mkdir()
    artifacts = {'binding.json': old, 'accepted-plan.json': plan,
        'planned-benchmark.json': planned, 'config.json': expected_runtime_config(scratch, old, frozen)}
    for name, value in artifacts.items():
        (scratch / name).write_text(json.dumps(value))
    ledger = Ledger(scratch, scratch.name)
    record_prefix_revalidation_migration(ledger, old, prefix_binding)
    missing = _reject(lambda: record_tool_runtime_migration(ledger, old, new))
    record_tool_transport_migration(ledger, old, transport_binding)
    prefix, transport = prior_tool_transport_migration(ledger)
    expected = tool_runtime_migration_binding(old, new, prefix, transport)
    assert expected['predecessor_migration_sha256'] == digest(transport)
    assert expected['clock_and_usage_preserved'] and expected['frozen_inputs_and_plan_preserved']
    assert source['runtime']['predecessor_migration_sha256'] == digest(source['transport'])
    snapshots = {name: (scratch / name).read_bytes() for name in artifacts}
    no_flag = _reject(lambda: validate_resume_artifacts(scratch, new))
    validate_resume_artifacts(scratch, new, tool_runtime_migration=True)
    first = record_tool_runtime_migration(ledger, old, new)
    assert record_tool_runtime_migration(ledger, old, new)['event_id'] == first['event_id']
    rejected = []
    for field, value in [('effective_seconds_limit', 200000), ('include_planning_time', False),
                          ('model', 'changed'), ('reasoning_effort', 'medium'), ('native_slots', 2)]:
        _reject(lambda field=field, value=value: validate_resume_artifacts(
            scratch, {**new, field: value}, tool_runtime_migration=True))
        rejected.append(field)
    for field in ('migration_id', 'predecessor_migration_sha256', 'new_application_sha256'):
        _reject(lambda field=field: tool_runtime_migration_binding(
            old, new, prefix, {**transport, field: 'unaudited-predecessor'}))
        rejected.append('transport.' + field)
    changed = deepcopy(new)
    changed['application_dependencies']['infrastructure/effective_time_budget.py'] = '9' * 64
    _reject(lambda: tool_runtime_migration_binding(old, changed, prefix, transport))
    reversed_ledger = SimpleNamespace(rows=deepcopy(ledger.rows[:2]))
    reversed_ledger.rows[0]['sequence'], reversed_ledger.rows[1]['sequence'] = 2, 1
    _reject(lambda: prior_tool_transport_migration(reversed_ledger))
    assert snapshots == {name: (scratch / name).read_bytes() for name in artifacts}
    assert len(ledger.rows) == 3
    return {'accepted_migration_events': len(ledger.rows), 'idempotent_record': True,
            'missing_predecessor': missing, 'missing_explicit_flag': no_flag,
            'rejected_mutations': rejected + ['budget_dependency', 'reversed_predecessor_order'],
            'frozen_artifacts_unchanged': True,
            'historical_predecessor_digest_matches': True}


HANDLERS = {
    'transport.frame_truncation': frame_truncation,
    'transport.diagnostic_paging': diagnostic_paging,
    'transport.archived_completion': archived_completion,
    'transport.runtime_binding': runtime_binding,
    'transport.migration_chain': migration_chain,
}
