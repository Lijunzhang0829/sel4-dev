"""Opt-in command-boundary diagnostics, never proof acceptance evidence.

Disjoint intervals require native option parallel_proofs=0. With proof futures,
the goal marker runs in a child task and can race the outer theory transition.
The profiling driver disables futures for BOTH baseline and instrumented trials.
"""
import re

BOUNDARIES = ('begin', 'context_ready', 'goal_ready', 'proof_returned', 'proof_joined', 'end')
PHASES = ('context_open', 'statement', 'proof_command', 'proof_join', 'context_close')
ARCH_BOUNDARIES = ('begin', 'scope_ready', 'interpretation_goal_ready',
                   'context_ready', 'goal_ready', 'proof_returned', 'proof_joined', 'end')
ARCH_PHASES = ('scope_open', 'interpretation_prepare', 'interpretation_finish',
              'statement', 'proof_command', 'proof_join', 'context_close')


def arch_state(node, label):
    token = node['id'].encode().hex()
    return ('ML \\<open>let val ctxt = @{context}; val thy = Proof_Context.theory_of ctxt; '
            'val loc = Locale.intern thy "Arch"; '
            'val local_regs = length (Locale.registrations_of (Context.Proof ctxt) loc); '
            'val theory_regs = length (Locale.registrations_of (Context.Theory thy) loc) '
            f'in writeln ("REGEN_ARCH_STATE {token} {label} " ^ '
            'string_of_int local_regs ^ " " ^ string_of_int theory_regs ^ " " ^ '
            'string_of_int (Local_Theory.level ctxt)) end\\<close>')


def ml_string(value):
    return value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r')


def timed_declaration(node, proof, *, split_arch=False):
    if split_arch and node.get('context') != ['context begin interpretation Arch .']:
        raise ValueError('Arch profiling requires the exact single saved Arch context')
    token = node['id'].encode().hex()
    def mark(boundary):
        # Diagnostic commands (ML_command/ML_val) can be scheduled asynchronously.
        # Use transitions in the proper theory/proof state, not presentation tasks.
        command = 'ML_prf' if boundary in ('goal_ready', 'interpretation_goal_ready') else 'ML'
        prefix = 'REGEN_ARCH_PHASE' if split_arch else 'REGEN_PHASE'
        return (f'{command} \\<open>writeln ("{prefix} {token} {boundary} " ^ '
                'Time.toString (Time.now ()))\\<close>')
    opening = [mark('begin'), *node.get('context', []), mark('context_ready')]
    if split_arch:
        opening = [arch_state(node, 'before'), mark('begin'), 'context begin',
                   mark('scope_ready'), arch_state(node, 'anonymous'), 'interpretation Arch',
                   mark('interpretation_goal_ready'), '.', mark('context_ready'),
                   arch_state(node, 'interpreted')]
    return '\n'.join([
        *opening,
        node['statement'], mark('goal_ready'), proof.strip(), mark('proof_returned'),
        'ML \\<open>Thm.consolidate (Proof_Context.get_thms @{context} "'
        + ml_string(node['name']) + '")\\<close>',
        mark('proof_joined'), *(['end'] * len(node.get('context', []))), mark('end'),
        *([arch_state(node, 'closed')] if split_arch else []),
    ])


def parse_phase_timings(log):
    rows = {}
    for style, token, boundary, stamp in re.findall(
            r'^REGEN_(ARCH_PHASE|PHASE) ([0-9a-f]+) (\w+) ([0-9.]+)$', log, re.M):
        try:
            node = bytes.fromhex(token).decode()
            stamp = float(stamp)
        except (ValueError, UnicodeError):
            continue
        row = rows.setdefault(node, {'events': [], 'errors': [], 'style': style})
        boundaries = ARCH_BOUNDARIES if style == 'ARCH_PHASE' else BOUNDARIES
        if row['style'] != style:
            row['errors'].append('mixed timing schemas')
        expected = len(row['events'])
        if expected >= len(boundaries) or boundary != boundaries[expected]:
            row['errors'].append('out-of-order or duplicate boundary: ' + boundary)
        if row['events'] and stamp < row['events'][-1]['time_unix']:
            row['errors'].append('clock moved backwards')
        row['events'].append({'boundary': boundary, 'time_unix': stamp})
    for row in rows.values():
        events = row['events']
        boundaries = ARCH_BOUNDARIES if row['style'] == 'ARCH_PHASE' else BOUNDARIES
        phases = ARCH_PHASES if row['style'] == 'ARCH_PHASE' else PHASES
        row['complete'] = not row['errors'] and len(events) == len(boundaries)
        row['phase_wall_ms'] = {}
        if not row['errors']:
            row['phase_wall_ms'] = {phases[i]: round((b['time_unix']-a['time_unix'])*1000)
                                    for i, (a, b) in enumerate(zip(events, events[1:]))}
        row['total_wall_ms'] = (round((events[-1]['time_unix']-events[0]['time_unix'])*1000)
                                if row['complete'] else None)
    return {'schema_version': 1, 'nodes': rows,
            'measurement': 'Command-boundary wall time including instrumentation; proof_join '
                           'forces theorem consolidation. Requires parallel_proofs=0 for disjoint phases. '
                           'Not pure tactic CPU time or acceptance evidence.'}


def parse_arch_states(log):
    result = {}
    for token, stage, local, theory, level in re.findall(
            r'^REGEN_ARCH_STATE ([0-9a-f]+) (\w+) (\d+) (\d+) (\d+)$', log, re.M):
        node = bytes.fromhex(token).decode()
        rows = result.setdefault(node, [])
        rows.append({'stage': stage, 'local_registrations': int(local),
                     'theory_registrations': int(theory), 'local_theory_level': int(level)})
    if any([r['stage'] for r in rows] != ['before', 'anonymous', 'interpreted', 'closed']
           for rows in result.values()):
        raise ValueError('incomplete or unordered Arch state observations')
    return result
