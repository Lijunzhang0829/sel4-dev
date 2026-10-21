"""Evidence-based, per-theory inactivity clocks (no proof/model side effects).

Only newly appended progress-event bytes are read. Persisted state keeps a
compact phase timeline, not model transcripts or proof bodies. Re-integrating
that timeline handles late-visible commits, overlapping exclusions and events
printed out of timestamp order without guessing away historical work.
"""
from datetime import datetime
import hashlib
import json
from pathlib import Path


MODE = 'theory_effective_v1'
PREFIX = b'[regen-event] '
EVENTS = {
    'parallel_generation_clock_started', 'theory_generation_started',
    'theory_generation_stopped', 'generation_exclusion_started',
    'generation_exclusion_finished', 'model_work_observed',
    'model_work_observation_finished', 'native_queued', 'native_slot_acquired',
    'native_slot_released', 'isabelle_build_started', 'isabelle_build_finished',
    'isabelle_batch_wait_started', 'isabelle_batch_wait_finished',
    'isabelle_started', 'isabelle_finished', 'check_task_started',
    'check_task_result_ready', 'check_task_stopped', 'runner_monitor_stopped',
    'isabelle_observation_started', 'isabelle_observation_finished',
    'isabelle_observation_failed',
    'generation_clock_untrusted',
}
FIELDS = {'event', 'theory', 'task_id', 'generation_id', 'clock_schema',
          'phase_id', 'category', 'thread_id', 'turn_id', 'worker',
          'waiting_for', 'label', 'session', 'container', 'kind', 'checker',
          'reason', 'item_ids', 'model_lifecycle_policy'}


class ClockEvidenceError(ValueError):
    """Missing evidence prevents a trustworthy budget decision."""


def clock_state(state):
    return state.setdefault('effective_clock', {
        'schema': MODE, 'logs': {}, 'events': [], 'progress': [],
    })


def stamp(value):
    return datetime.fromisoformat(value).timestamp()


def merged(intervals):
    out = []
    for a, b in sorted(intervals):
        if b <= a:
            continue
        if out and a <= out[-1][1]:
            out[-1][1] = max(b, out[-1][1])
        else:
            out.append([a, b])
    return out


def subtract(intervals, removed):
    removed = merged(removed)
    out = []
    for a, b in merged(intervals):
        position = a
        for x, y in removed:
            if y <= position:
                continue
            if x >= b:
                break
            if x > position:
                out.append([position, min(x, b)])
            position = max(position, y)
            if position >= b:
                break
        if position < b:
            out.append([position, b])
    return out


def measure(intervals, start=float('-inf'), end=float('inf')):
    return sum(max(0, min(b, end)-max(a, start)) for a, b in intervals)


def crossing(intervals, start, seconds):
    for a, b in intervals:
        a = max(a, start)
        if b <= a:
            continue
        if b-a >= seconds:
            return a+seconds
        seconds -= b-a
    return None


def ingest(run_dirs, state):
    """Tail complete event lines; verify the already-read append boundary."""
    clock = clock_state(state)
    if clock['schema'] != MODE:
        raise ClockEvidenceError('unknown effective-clock state schema')
    for root in map(Path, run_dirs):
        path = root/'service-runner.log'
        receipt = root/'service-launch.json'
        if receipt.exists():
            clock.setdefault('launches', {})[str(root)] = stamp(json.loads(receipt.read_text())['created_at'])
        elif str(root) in clock.get('launches', {}):
            raise ClockEvidenceError('previously observed service receipt disappeared: '+str(root))
        if not path.exists():
            if str(path) in clock['logs']:
                raise ClockEvidenceError('previously observed event log disappeared: '+str(path))
            if receipt.exists():
                clock.setdefault('missing_logs', {})[str(root)] = True
            continue
        clock.setdefault('missing_logs', {}).pop(str(root), None)
        cursor = clock['logs'].setdefault(str(path), {'offset': 0})
        stat = path.stat()
        identity = [stat.st_dev, stat.st_ino]
        if cursor.get('identity', identity) != identity or stat.st_size < cursor['offset']:
            raise ClockEvidenceError('effective-clock event log replaced or truncated: '+str(path))
        with path.open('rb') as stream:
            offset = cursor['offset']
            stream.seek(max(0, offset-4096))
            boundary = stream.read(min(4096, offset))
            if cursor.get('boundary_sha256') not in (None, hashlib.sha256(boundary).hexdigest()):
                raise ClockEvidenceError('effective-clock event log append boundary changed')
            stream.seek(offset)
            while True:
                line = stream.readline()
                if not line or not line.endswith(b'\n'):
                    break  # A partial write is retried, never discarded.
                offset = stream.tell()
                if not line.startswith(PREFIX):
                    continue
                try:
                    event = json.loads(line[len(PREFIX):])
                    when = stamp(event['time'])
                except (ValueError, KeyError, TypeError) as error:
                    raise ClockEvidenceError('malformed progress event in '+str(path)) from error
                cursor['saw_event'] = True
                if event.get('event') not in EVENTS:
                    continue
                clock['events'].append({**{k: event[k] for k in FIELDS if k in event},
                                        'at': when, 'run_dir': str(root)})
            cursor.update(offset=offset, identity=identity)
            stream.seek(max(0, offset-4096))
            cursor['boundary_sha256'] = hashlib.sha256(stream.read(min(4096, offset))).hexdigest()


def _root_timeline(events, root, now, running):
    """Build measured generation/exclusion/model lanes for one service birth."""
    rows = sorted((e for e in events if e['run_dir'] == root), key=lambda e: e['at'])
    supported = any(e['event'] in {'parallel_generation_clock_started', 'theory_generation_started'}
                    and e.get('clock_schema') == MODE for e in rows)
    if rows and not supported:
        raise ClockEvidenceError('unsupported legacy event log; audited clock migration required: '+root)
    generations, phases, queues, leases, model = {}, {}, {}, {}, {}
    normal, excluded, overrides = {}, {}, {}
    unresolved = {}
    active = set()

    def add(target, theory, a, b):
        if b > a:
            target.setdefault(theory, []).append([a, b])

    def close_phase(key, at):
        row = phases.pop(key)
        add(excluded, row['theory'], row['at'], at)

    def end_task(theory, task, at):
        for key, row in list(phases.items()):
            if row['theory'] == theory and row.get('task_id') == task:
                close_phase(key, at)
        key = (theory, task)
        queues.pop(key, None)
        leases.pop(key, None)

    def prep(event, identity):
        theory, task = event['theory'], event.get('task_id')
        key = (theory, task)
        lease = leases.get(key)
        if lease is not None and not lease['prep']:
            add(excluded, theory, lease['queued'], lease['acquired'])
            lease['prep'] = True
        phases[(theory, *identity)] = event

    for event in rows:
        kind, at = event['event'], event['at']
        if at > now+1:
            raise ClockEvidenceError('event timestamp is ahead of observer clock')
        theory, task = event.get('theory'), event.get('task_id')
        if not theory:
            continue
        key = (theory, task)
        if kind == 'theory_generation_started':
            if event.get('clock_schema') != MODE or not event.get('generation_id'):
                raise ClockEvidenceError('generation start lacks clock schema/identity')
            if theory in generations:
                raise ClockEvidenceError('overlapping generation attempts for '+theory)
            generations[theory] = event
        elif kind == 'theory_generation_stopped':
            origin = generations.pop(theory, None)
            if origin is None or origin.get('generation_id') != event.get('generation_id'):
                raise ClockEvidenceError('unmatched generation stop for '+theory)
            add(normal, theory, origin['at'], at)
            # Session failure ends all in-flight setup intervals. Recovery until
            # the next start is outside the generation lane, not a timer reset.
            for phase_key, row in list(phases.items()):
                if row['theory'] == theory:
                    close_phase(phase_key, at)
            for queue_key in list(queues):
                if queue_key[0] == theory:
                    queues.pop(queue_key)
            for lease_key in list(leases):
                if lease_key[0] == theory:
                    leases.pop(lease_key)
            if theory in model:
                add(overrides, theory, model.pop(theory), at)
        elif kind == 'generation_exclusion_started':
            if not event.get('phase_id'):
                raise ClockEvidenceError('exclusion lacks phase identity')
            # Only audited infrastructure categories may remove generation.
            if event.get('category') not in {'runtime_setup', 'heap_preparation', 'provider_stream_retry',
                                             'prefix_preparation', 'statement_preflight', 'provider_build', 'seal'}:
                raise ClockEvidenceError('unsupported exclusion category')
            phases[(theory, 'explicit', event['phase_id'])] = event
        elif kind == 'generation_exclusion_finished':
            phase = (theory, 'explicit', event.get('phase_id'))
            if phase in phases:
                close_phase(phase, at)
        elif kind == 'model_work_observed':
            model.setdefault(theory, at)
        elif kind == 'model_work_observation_finished':
            if theory in model:
                add(overrides, theory, model.pop(theory), at)
        elif kind == 'native_queued':
            if key in queues or key in leases:
                raise ClockEvidenceError('overlapping native leases for one task')
            queues[key] = at
        elif kind == 'native_slot_acquired':
            if key not in queues:
                raise ClockEvidenceError('native slot lacks matching queue event')
            leases[key] = {'queued': queues.pop(key), 'acquired': at, 'prep': False}
        elif kind == 'native_slot_released':
            lease = leases.pop(key, None)
            if lease is not None:
                for phase_key, row in list(phases.items()):
                    if (row['theory'], row.get('task_id')) == key and phase_key[1] in {'build', 'ready', 'declaration'}:
                        close_phase(phase_key, at)
        elif kind == 'isabelle_build_started':
            prep(event, ('build', event.get('session')))
        elif kind == 'isabelle_batch_wait_started' and event.get('waiting_for') == 'ready':
            prep(event, ('ready', event.get('worker')))
        elif kind == 'isabelle_started' and event.get('label', '').startswith(('Declaration_Check', 'Graph_Check')):
            prep(event, ('declaration', event['label']))
        elif kind == 'isabelle_observation_started' and event.get('label', '').startswith(('Declaration_Check', 'Graph_Check')):
            prep(event, ('declaration', event['label']))
        elif kind == 'check_task_started' and event.get('kind') == 'seal':
            prep(event, ('seal', task))
        elif kind == 'isabelle_build_finished':
            phase = (theory, 'build', event.get('session'))
            if phase in phases:
                close_phase(phase, at)
        elif kind == 'isabelle_batch_wait_finished' and event.get('waiting_for') == 'ready':
            phase = (theory, 'ready', event.get('worker'))
            if phase in phases:
                close_phase(phase, at)
        elif kind in {'isabelle_finished', 'isabelle_observation_finished', 'isabelle_observation_failed'}:
            phase = (theory, 'declaration', event.get('label'))
            if phase in phases:
                close_phase(phase, at)
        elif kind in {'check_task_result_ready', 'check_task_stopped'} or (kind == 'runner_monitor_stopped' and task):
            end_task(theory, task, at)

    if generations and not running:
        # No last-poll approximation: a lost stop may hide arbitrarily long
        # downtime across an observer restart. Require explicit reconciliation.
        raise ClockEvidenceError('service stopped without generation-stop evidence: '+root)
    for theory, origin in generations.items():
        active.add(theory)
        add(normal, theory, origin['at'], now)
    for row in phases.values():
        add(excluded, row['theory'], row['at'], now)
    for theory, start in model.items():
        add(overrides, theory, start, now)
    # Until a lease reveals whether it performs preparation, its admission
    # queue cannot be classified. Defer a deadline decision, not the charged
    # clock itself. Ordinary candidate queues remain counted on release.
    for key, start in list(queues.items())+[(k, v['queued']) for k, v in leases.items() if not v['prep']]:
        unresolved[key[0]] = min(start, unresolved.get(key[0], start))
    effective = {theory: subtract(windows, subtract(excluded.get(theory, []), overrides.get(theory, [])))
                 for theory, windows in normal.items()}
    return effective, active, unresolved, supported


def evaluate(state, observations, now, timeout, *, budget_policy='no_new_contract'):
    if budget_policy not in {'no_new_contract', 'fixed_budget'}:
        raise ValueError('unknown effective generation budget policy')
    clock = clock_state(state)
    # A durable uncertainty marker survives observer restarts and continuations.
    # Never refund a failed step or return an apparently verified deadline.
    uncertain = [event for event in clock['events']
                 if event['event'] == 'generation_clock_untrusted']
    safe_generations = {(event['run_dir'], event.get('theory')) for event in clock['events']
                        if event['event'] == 'theory_generation_started'
                        and event.get('model_lifecycle_policy') in (2, 3)}
    legacy_overlap = [event for event in clock['events']
                      if event['event'] == 'model_work_observed'
                      and (event.get('model_lifecycle_policy') not in (2, 3)
                           or (event['run_dir'], event.get('theory')) not in safe_generations)]
    if uncertain or legacy_overlap:
        reasons = uncertain + [{**event, 'reason': 'legacy_model_overlap_requires_audited_migration'}
                               for event in legacy_overlap]
        previous = clock.get('snapshot', {})
        clock['snapshot'] = {**previous, 'clock_mode': MODE, 'budget_policy': budget_policy,
                             'observed_at_unix': now, 'evidence_trusted': False,
                             'measurement_status': 'untrusted', 'uncertainty': reasons,
                             'expired_theories': [], 'first_crossing_at': None}
        raise ClockEvidenceError('untrusted model lifecycle evidence; budget conclusion refused')
    if now < clock.get('snapshot', {}).get('observed_at_unix', now):
        raise ClockEvidenceError('observer wall clock moved backwards; reconcile against monotonic evidence')
    if any(p['at'] > now+1 for p in clock['progress']):
        raise ClockEvidenceError('accepted commit publication is ahead of observer clock')
    missing_roots = {event['run_dir'] for event in clock['events']}-set(observations)
    if missing_roots:
        raise ClockEvidenceError('historical service identity observations missing: '+repr(sorted(missing_roots)))
    effective, active, unresolved, awaiting_schema = {}, set(), {}, []
    for root, row in observations.items():
        intervals, live, pending, supported = _root_timeline(
            clock['events'], root, now, row['running'])
        if not supported:
            # A service birth may precede Python's first schema marker. Permit
            # only a finite grace tied to its immutable launch receipt, never
            # an observer restart that silently grants a fresh grace period.
            path = str(Path(root)/'service-runner.log')
            if clock['logs'].get(path, {}).get('saw_event'):
                raise ClockEvidenceError('running service has no effective-clock schema marker')
            launch = clock.get('launches', {}).get(root)
            if row['running'] and launch is not None and 0 <= now-launch <= 60:
                awaiting_schema.append(root)
            else:
                raise ClockEvidenceError('service event log/schema unavailable; audited migration required: '+root)
        for theory, windows in intervals.items():
            effective.setdefault(theory, []).extend(windows)
        active.update(live)
        for theory, at in pending.items():
            unresolved[theory] = min(at, unresolved.get(theory, at))
    result = {'clock_mode': MODE, 'observed_at_unix': now, 'theories': {}, 'expired_theories': [],
              'budget_policy': budget_policy, 'evidence_trusted': True,
              'measurement_status': 'trusted',
              'unresolved_preparation_queues': sorted(unresolved),
              'awaiting_schema_bootstrap_grace': awaiting_schema,
              'unknown_model_silence': 'retained; no retroactive whole-step exclusion',
              'deadline_tie_policy': 'new contract accepted exactly at deadline wins',
              'sampling_policy': 'detect historical crossings even if a later commit arrived between polls'}
    for theory, windows in sorted(effective.items()):
        windows = merged(windows)
        progress = sorted((p for p in clock['progress'] if p['theory'] == theory and p['at'] <= now), key=lambda p: p['at'])
        previous, first = float('-inf'), None
        for event in progress if budget_policy == 'no_new_contract' else ():
            amount = measure(windows, previous, event['at'])
            if first is None and amount > timeout:
                first = crossing(windows, previous, timeout)
            previous = event['at']
        current = measure(windows, previous, now)
        if first is None and current >= timeout:
            first = crossing(windows, previous, timeout)
        # A later uncertain queue cannot erase an already certain crossing.
        deferred = theory in unresolved and (first is None or first > unresolved[theory])
        last_progress = progress[-1]['at'] if progress else None
        row = {'effective_seconds': measure(windows),
               'idle_effective_seconds': measure(windows, last_progress if last_progress is not None else float('-inf'), now),
               'budget_effective_seconds': current,
               'active': theory in active, 'first_crossing_at': first,
               'deadline_decision_deferred': deferred,
               'last_new_contract_at': last_progress,
               'new_contract_count': sum(len(p['proof_ids']) for p in progress)}
        result['theories'][theory] = row
        if first is not None and not deferred:
            result['expired_theories'].append(theory)
    crossings = [row['first_crossing_at'] for row in result['theories'].values() if row['first_crossing_at'] is not None]
    result['first_crossing_at'] = min(crossings) if crossings else None
    clock['snapshot'] = result
    return result


def update(run_dirs, state, observations, now, timeout, *, budget_policy='no_new_contract'):
    ingest(run_dirs, state)
    return evaluate(state, observations, now, timeout, budget_policy=budget_policy)
