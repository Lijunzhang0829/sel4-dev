"""Native revalidation of certified VERO prefixes before draft reconciliation."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4

from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.proof_commits import digest, durable


def cached_import_rejection(adapter, theory, receipts):
    """Reuse only a content-verified rejection of this exact import context."""
    if not receipts:
        return None
    context = next(iter(receipts.values()))['context_identity']
    records = getattr(adapter, '_failed_import_evidence', {})
    key = (theory, context, adapter.timeout)
    record = records.get(key)
    if not record:
        return None
    try:
        path = Path(record['result'])
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != record['result_sha256']:
            raise ValueError('cached import rejection changed')
        result = json.loads(path.read_text())
        version = result['source_version']
        manifest = Path(version['source_manifest'])
        if (manifest.is_symlink() or hashlib.sha256(manifest.read_bytes()).hexdigest()
                != version['source_manifest_sha256']):
            raise ValueError('cached import manifest changed')
        for name, expected in json.loads(manifest.read_text()).items():
            source = manifest.parent / name
            if source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
                raise ValueError('cached import source changed')
        prefix = Path(version['source'])
        evidence = result['native_evidence']
        log = Path(evidence['log'])
        if (prefix.is_symlink() or hashlib.sha256(prefix.read_bytes()).hexdigest()
                != version['source_sha256'] or log.is_symlink()
                or hashlib.sha256(log.read_bytes()).hexdigest() != record['log_sha256']
                or evidence['returncode'] != 1):
            raise ValueError('cached import evidence changed')
        return {'returncode': 1, 'verdict': 'red', 'wall_ms': 0,
            'source': evidence['source'], 'log': str(log),
            'check_scope': 'blocked-import-native-rejection',
            'cached_failure_origin': str(path), 'failed_imports': record['failed_imports'],
            'origin_native_evidence': evidence}
    except (OSError, ValueError, KeyError, TypeError):
        records.pop(key, None)
        failures = getattr(adapter, '_prefix_revalidation_failures', set())
        failures.discard(('imports', theory, context, adapter.timeout))
        failures.discard(record.get('failure_key'))
        return None


def mask_original_open_slots(source, slots):
    """Restore only proof slots that were OPEN in the certified version."""
    for slot in slots:
        if not re.fullmatch(r'[ch]\d{4}:(?:prove|disprove)', slot):
            raise ValueError('invalid certified OPEN proof slot')
        uid, polarity = slot.split(':')
        start = f'(* VERO START {uid} {polarity} *)'.encode()
        end = f'(* VERO END {uid} {polarity} *)'.encode()
        if source.count(start) != 1 or source.count(end) != 1:
            raise ValueError('certified OPEN proof slot is absent or ambiguous')
        begin = source.index(start) + len(start)
        finish = source.find(end, begin)
        if finish < begin:
            raise ValueError('malformed certified OPEN proof slot')
        source = source[:begin] + b'\noops\n' + source[finish:]
    return source


def revalidate_accepted_prefixes(adapter, requested_theory, snapshot, imports, *,
                                 checker_factory=IsabelleChecker):
    """Recheck changed historical versions; never accept failed log markers.

    A changed prefix can contain an unused removed helper or a genuinely broken
    dependency. Isabelle decides which case applies. Work proceeds from the
    latest affected boundary backwards so a failed later proof cannot revoke
    an independently valid earlier proof. Success replaces receipts before the
    normal reconciliation; failed checks leave its conservative rules intact.
    """
    observer = adapter.observer
    history = getattr(observer, 'certified_receipts', None)
    if not isinstance(history, dict):
        return
    failures = getattr(adapter, '_prefix_revalidation_failures', None)
    if failures is None:
        failures = adapter._prefix_revalidation_failures = set()
    for theory in sorted(imports):
        if theory in observer.seal_transactions or theory + '.thy' not in snapshot:
            continue
        candidates = adapter._contract_receipts(theory, snapshot, imports)
        pending = {}
        for uid, candidate in candidates.items():
            old = history.get(uid)
            current = observer.accepted_receipts.get(uid)
            if (not isinstance(old, dict) or not observer._valid_receipt(candidate)
                    or any(candidate.get(k) != old.get(k) for k in
                        ('polarity', 'filled', 'proof_body_sha256'))
                    or (current is not None and observer._same_accepted_proof(current, candidate))):
                continue
            pending[uid] = deepcopy(old)
        if pending and ('imports', theory,
                candidates[next(iter(pending))]['context_identity'], adapter.timeout) in failures:
            if cached_import_rejection(adapter, theory, candidates) is not None:
                continue
        full = snapshot[theory + '.thy']
        while pending:
            endpoint = max(pending, key=lambda uid: full.index(
                f'(* VERO START {uid} prove *)'.encode()))
            old = pending[endpoint]
            masked = mask_original_open_slots(full, old.get('open_prefix_slots', []))
            isolated = {**snapshot, theory + '.thy': masked}
            receipts = adapter._contract_receipts(theory, isolated, imports)
            prefix, is_prefix = adapter._candidate_check_source(
                theory, isolated, {endpoint: receipts[endpoint]})
            if not is_prefix:
                # Legacy/nonstandard rendering has no trusted truncation rule.
                pending.pop(endpoint)
                continue
            end_marker = f'(* VERO END {endpoint} prove *)'.encode()
            boundary = masked.index(end_marker) + len(end_marker)
            selected = {uid: receipts[uid] for uid in pending
                if receipts[uid].get('filled') and
                receipts[uid]['proof_body_sha256'] == pending[uid]['proof_body_sha256']
                and masked.index(f'(* VERO END {uid} prove *)'.encode()) < boundary}
            # A partial revalidation must never stand in for the ordinary
            # complete-theory check that admits seal edits.
            identity = digest({'scope': 'native-independent-prefix-revalidation-v1',
                'prefix_sha256': hashlib.sha256(prefix).hexdigest(),
                'context_identity': receipts[endpoint]['context_identity']})
            key = (receipts[endpoint]['context_identity'], hashlib.sha256(prefix).hexdigest(), adapter.timeout)
            if key in failures:
                pending.pop(endpoint)
                continue
            request = adapter.root / 'prefix-revalidations' / uuid4().hex
            request.mkdir(parents=True)
            for name, data in isolated.items():
                (request / name).write_bytes(data)
            manifest = {name: hashlib.sha256(data).hexdigest() for name, data in isolated.items()}
            durable(request / 'source-manifest.json', manifest)
            work = request / 'prefix'
            work.mkdir()
            for name, data in isolated.items():
                (work / name).write_bytes(data)
            c = adapter.context
            checker = checker_factory(adapter.root, work, image=c['image'], generated_dir=request,
                heaps_dir=adapter.root / 'heaps', l4v_dir=c['l4v'],
                semantic_workspace=c['semantic_workspace'], isabelle_dir=c['isabelle'],
                base_session=c['base_session'], base_import=c['base_import'],
                base_catalog=c['base_catalog'], timeout=adapter.timeout)
            checker.checker_context_id = c['checker_context_id']
            observer.stop.check()
            adapter._assert_workspace_current_or_reconcile(observer, request, requested_theory,
                observer.bench, imports, snapshot, reason='source_changed_before_prefix_revalidation')
            from regen.execution.check_executor import CURRENT_RESOURCES
            from regen.execution.check_tasks import CURRENT_TASK, Cancellation
            token = CURRENT_RESOURCES.set(adapter.resources)
            cancellation = CURRENT_TASK.set(Cancellation(event=observer.stop.event))
            try:
                evidence = checker._check(prefix.decode('utf-8'), theory, timeout=adapter.timeout)
            finally:
                checker.close_prefix()
                CURRENT_TASK.reset(cancellation)
                CURRENT_RESOURCES.reset(token)
            if any((request / name).read_bytes() != data for name, data in isolated.items()):
                raise ValueError('independent revalidation snapshot mutated during check')
            if (work / (theory + '.thy')).read_bytes() != prefix:
                raise ValueError('independent revalidation prefix mutated during check')
            adapter._assert_workspace_current_or_reconcile(observer, request, requested_theory,
                observer.bench, imports, snapshot, reason='source_changed_during_prefix_revalidation')
            observer.stop.check()
            log = Path(evidence['log']).read_text()
            audited = {uid + ':' + polarity for uid, polarity in re.findall(
                r'^VERO_RESULT ([ch]\d{4}) (prove|disprove) clean$', log, re.M)}
            success = (evidence['returncode'] == 0
                and {uid + ':prove' for uid in selected} <= audited)
            result = {'status': 'ok' if success else 'build_error',
                'audited': sorted(audited) if success else [],
                'contract_receipts': selected, 'native_evidence': evidence,
                'source_identity': identity, 'elapsed_seconds': evidence['wall_ms'] / 1000,
                'source_version': {'source': str(work / (theory + '.thy')),
                    'source_sha256': hashlib.sha256(prefix).hexdigest(),
                    'source_manifest': str(request / 'source-manifest.json'),
                    'source_manifest_sha256': hashlib.sha256(
                        (request / 'source-manifest.json').read_bytes()).hexdigest(),
                    'original_open_slots': old.get('open_prefix_slots', [])}}
            durable(request / 'result.json', result)
            if success:
                adapter._assert_workspace_current_or_reconcile(observer, request, requested_theory,
                    observer.bench, imports, snapshot,
                    reason='source_changed_before_prefix_revalidation_publication')
                observer.revalidated_contracts(theory, result)
                for uid in selected:
                    pending.pop(uid, None)
            else:
                parents = set()
                todo = list(imports.get(theory, ()))
                while todo:
                    parent = todo.pop()
                    if parent in parents:
                        continue
                    parents.add(parent)
                    todo.extend(imports.get(parent, ()))
                error_theories = set(re.findall(
                    r'\*\*\* At command[^\n]*"/(?:candidate|generated)/([A-Za-z][A-Za-z0-9_]*)\.thy"', log))
                failed_imports = sorted(parents & error_theories)
                observer.ledger.append('prefix_revalidation_failed', theory=theory,
                    endpoint=endpoint, target_ids=sorted(selected),
                    failed_imports=failed_imports,
                    evidence=str(request / 'result.json'),
                    native_elapsed_seconds=evidence['wall_ms'] / 1000)
                # Only a definite completed rejection is reusable. Timeouts and
                # transport failures are retried normally, with no attempt cap.
                if evidence['returncode'] == 1 and evidence.get('timeout_scope') is None:
                    failures.add(key)
                    if failed_imports:
                        # Every boundary loads these exact imports. Do not
                        # repeat the same known failure once per old contract.
                        # A changed import closure changes context_identity and
                        # permits a fresh attempt; this never stops the agent.
                        failures.add(('imports', theory,
                            receipts[endpoint]['context_identity'], adapter.timeout))
                        records = getattr(adapter, '_failed_import_evidence', None)
                        if records is None:
                            records = adapter._failed_import_evidence = {}
                        records[(theory, receipts[endpoint]['context_identity'], adapter.timeout)] = {
                            'failure_key': key,
                            'result': str(request / 'result.json'),
                            'result_sha256': hashlib.sha256((request / 'result.json').read_bytes()).hexdigest(),
                            'log_sha256': hashlib.sha256(Path(evidence['log']).read_bytes()).hexdigest(),
                            'failed_imports': failed_imports}
                        pending.clear()
                        continue
                pending.pop(endpoint)
