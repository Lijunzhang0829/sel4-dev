"""Read-only Codex rollout progress checks, independent of a running agent.

This observes local structured events, not network health or hidden model work.
No messages, reasoning bodies, arguments, tool outputs, or credentials are emitted.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time
from uuid import UUID, uuid4

MAX_LINE_BYTES = 16 * 1024 * 1024
CALL_TYPES = {'function_call', 'custom_tool_call'}
OUTPUT_TYPES = {'function_call_output', 'custom_tool_call_output'}
TOOL_EVENTS = {'exec_command_begin', 'exec_command_end', 'web_search_begin',
               'web_search_end', 'mcp_tool_call_begin', 'mcp_tool_call_end',
               'patch_apply_begin', 'patch_apply_end'}
TOOL_ITEM_TYPES = {'CommandExecution', 'McpToolCall', 'WebSearch', 'WebSearchCall',
                   'FunctionCall', 'CustomToolCall', 'ToolCall'}
CHILD_FINISHED = {'completed', 'finished', 'stopped', 'errored', 'failed', 'interrupted'}


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


def iso(value):
    return value.isoformat(timespec='milliseconds').replace('+00:00', 'Z') if value else None


def safe_identifier(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_./:-]{1,180}', value) else None


class ProgressState:
    """Keep metadata only; body text never enters monitor state."""
    def __init__(self, thread_id=None):
        self.thread_id = thread_id
        self.turn_id = None
        self.started_at = None
        self.terminal = None
        self.terminal_at = None
        self.last_main = None
        self.last_main_kind = None
        self.main_event_count = 0
        self.children = {}
        self.pending_tools = {}
        self.last_tool_result = None
        self.events_seen = 0
        self.invalid_timestamps = 0
        self.ignored_foreign_events = 0

    def begin(self, turn_id, at):
        self.turn_id = safe_identifier(turn_id)
        self.started_at = at
        self.terminal = self.terminal_at = None
        self.last_main = self.last_main_kind = None
        self.main_event_count = 0
        self.pending_tools = {}
        self.last_tool_result = None

    def main(self, at, kind):
        if self.started_at is None or self.terminal or at < self.started_at:
            return
        self.main_event_count += 1
        if self.last_main is None or at >= self.last_main:
            self.last_main = at
            self.last_main_kind = kind

    def child(self, payload, at):
        agent_path = safe_identifier(payload.get('agent_path')) or safe_identifier(payload.get('author'))
        identity = safe_identifier(payload.get('agent_thread_id'))
        if not identity and agent_path:
            identity = next((key for key, item in self.children.items()
                             if item.get('agent_path') == agent_path), agent_path)
        if not identity:
            return
        kind = payload.get('kind')
        if isinstance(kind, dict):
            kind = kind.get('type')
        kind = kind if kind in CHILD_FINISHED | {'started', 'running', 'message'} else 'activity'
        old = self.children.get(identity)
        if old is None and agent_path and agent_path in self.children:
            old = self.children.pop(agent_path)
        if old and timestamp(old['last_activity_at']) > at:
            return
        entry = {'agent_id': identity, 'agent_path': agent_path or (old.get('agent_path') if old else None),
                 'last_activity_at': iso(at),
                 'last_event_kind': kind, 'observed_in_turn': self.turn_id,
                 'state': old.get('state', 'unknown') if old else 'unknown'}
        if kind in CHILD_FINISHED:
            entry['state'] = 'finished'
        elif kind in {'started', 'running'}:
            entry['state'] = 'running'
        self.children[identity] = entry
        if len(self.children) > 64:
            earliest = min(self.children, key=lambda key: self.children[key]['last_activity_at'])
            del self.children[earliest]

    def consume(self, event, *, as_of=None):
        if not isinstance(event, dict):
            return
        at = timestamp(event.get('timestamp'))
        if at is None:
            self.invalid_timestamps += 1
            return
        if as_of is not None and at > as_of:
            return
        self.events_seen += 1
        kind = event.get('type')
        data = event.get('payload')
        if not isinstance(data, dict):
            return
        if kind == 'session_meta':
            identity = safe_identifier(data.get('id'))
            if identity and self.thread_id and identity != self.thread_id:
                raise ValueError('session metadata does not match requested thread')
            self.thread_id = identity or self.thread_id
            return
        if data.get('thread_id') and self.thread_id and data['thread_id'] != self.thread_id:
            self.ignored_foreign_events += 1
            return
        event_type = data.get('type')
        if kind == 'event_msg' and event_type == 'task_started':
            if self.started_at is None or at >= self.started_at:
                self.begin(data.get('turn_id'), at)
            return
        if kind == 'turn_context':
            # Fallback for exports without task_started; repeated context is not
            # new progress and never restarts the same completed turn.
            if self.started_at is None or (data.get('turn_id') != self.turn_id
                                          and at >= self.started_at):
                self.begin(data.get('turn_id'), at)
            return
        if data.get('turn_id') and self.turn_id and data['turn_id'] != self.turn_id:
            self.ignored_foreign_events += 1
            return
        if kind == 'event_msg' and event_type in {'task_complete', 'turn_aborted'}:
            if self.started_at is not None and at >= self.started_at:
                self.terminal = event_type
                self.terminal_at = at
                self.pending_tools = {}
            return
        if kind == 'event_msg' and event_type == 'sub_agent_activity':
            self.child(data, at)
            return
        if kind == 'event_msg' and event_type in {'item_started', 'item_completed'}:
            item = data.get('item', {})
            if not isinstance(item, dict):
                return
            if item.get('type') == 'SubAgentActivity':
                self.child(item, at)
            elif item.get('type') in TOOL_ITEM_TYPES:
                self.main(at, 'event_msg.'+event_type)
            return
        if kind == 'response_item' and event_type == 'agent_message':
            # This is inter-agent delivery, not the root assistant's response.
            author = safe_identifier(data.get('author'))
            if author and author != '/root':
                self.child({**data, 'kind': 'message'}, at)
            return
        if kind == 'response_item':
            if event_type == 'reasoning':
                self.main(at, 'response_item.reasoning')
            elif event_type == 'message' and data.get('role') == 'assistant':
                self.main(at, 'response_item.assistant_message')
            elif event_type in CALL_TYPES | OUTPUT_TYPES:
                self.main(at, 'response_item.'+event_type)
                call_id = safe_identifier(data.get('call_id'))
                if self.started_at is None or self.terminal or at < self.started_at or not call_id:
                    return
                if event_type in CALL_TYPES:
                    self.pending_tools[call_id] = {'call_id': call_id, 'started_at': iso(at),
                                                    'tool_name': safe_identifier(data.get('name'))}
                else:
                    tool = self.pending_tools.pop(call_id, {})
                    self.last_tool_result = {'call_id': call_id, 'tool_name': tool.get('tool_name'), 'returned_at': iso(at)}
            return
        if kind == 'event_msg' and event_type in TOOL_EVENTS:
            self.main(at, 'event_msg.'+event_type)

    def snapshot(self, now, threshold_seconds):
        baseline = self.last_main or self.started_at
        age = max(0.0, (now-baseline).total_seconds()) if baseline else None
        if self.terminal:
            status = self.terminal
        elif self.started_at is None:
            status = 'no_active_turn'
        elif age >= threshold_seconds:
            status = 'no_observable_main_progress'
        else:
            status = 'active'
        children = []
        for entry in sorted(self.children.values(), key=lambda x: x['agent_id']):
            if entry['observed_in_turn'] != self.turn_id:
                continue
            child_age = max(0.0, (now-timestamp(entry['last_activity_at'])).total_seconds())
            children.append({**entry, 'seconds_since_activity': round(child_age, 3),
                             'recent_activity': child_age < threshold_seconds})
        return {'schema_version': 1, 'observed_at': iso(now), 'thread_id': self.thread_id,
                'turn_id': self.turn_id, 'status': status,
                'threshold_seconds': threshold_seconds, 'turn_started_at': iso(self.started_at),
                'last_main_event_at': iso(self.last_main), 'last_main_event_kind': self.last_main_kind,
                'seconds_without_main_event': round(age, 3) if age is not None else None,
                'main_events_in_turn': self.main_event_count,
                'terminal_at': iso(self.terminal_at),
                'last_tool_result': self.last_tool_result,
                'diagnostic_boundary': ('terminal' if self.terminal else
                    'tool_result_pending' if self.pending_tools else
                    'tool_returned_no_later_event' if self.last_tool_result and iso(self.last_main) == self.last_tool_result['returned_at'] else
                    'awaiting_next_observable_event'),
                'request_sent': 'unknown', 'remote_response_received': 'unknown',
                'frontend_rendered': 'unknown',
                'pending_tools': list(self.pending_tools.values()), 'subagents': children,
                'recent_subagent_activity': any(x['recent_activity'] for x in children),
                'events_seen': self.events_seen, 'invalid_timestamps': self.invalid_timestamps,
                'ignored_foreign_events': self.ignored_foreign_events,
                'interpretation': ('Only local observable events are measured. Silence is not proof of a '
                    'network failure or of stopped model computation. Subagent activity does not reset '
                    'the main timer. A pending tool may still be working, and a returned tool may have '
                    'launched background work; this monitor does not inspect those processes.')}


class RolloutReader:
    """Scan once, then consume only newly appended complete JSONL lines."""
    def __init__(self, path, thread_id=None):
        self.path = Path(path)
        self.expected_thread = thread_id
        self.state = ProgressState(thread_id)
        self.identity = None
        self.offset = 0
        self.invalid_lines = 0
        self.oversized_lines = 0
        self.resets = 0
        self.bytes_read = 0
        self.partial = b''
        self.discarding = False

    def poll(self, *, as_of=None):
        stat = self.path.stat()
        identity = (stat.st_dev, stat.st_ino)
        if self.identity is not None and (identity != self.identity or stat.st_size < self.offset):
            self.state = ProgressState(self.expected_thread)
            self.offset = 0
            self.partial = b''
            self.discarding = False
            self.resets += 1
        self.identity = identity
        before = self.bytes_read
        with self.path.open('rb') as stream:
            stream.seek(self.offset)
            while chunk := stream.readline(MAX_LINE_BYTES + 1):
                self.offset += len(chunk)
                self.bytes_read += len(chunk)
                complete = chunk.endswith(b'\n')
                if self.discarding:
                    self.discarding = not complete
                    continue
                data = self.partial + chunk
                self.partial = b''
                if len(data) > MAX_LINE_BYTES:
                    self.oversized_lines += 1
                    self.discarding = not complete
                    continue
                if not complete:
                    self.partial = data
                    continue
                try:
                    event = json.loads(data)
                except (ValueError, UnicodeDecodeError):
                    self.invalid_lines += 1
                    continue
                self.state.consume(event, as_of=as_of)
        return self.bytes_read-before

    def snapshot(self, now, threshold_seconds):
        result = self.state.snapshot(now, threshold_seconds)
        result['source'] = {'session_file': str(self.path), 'bytes_read': self.bytes_read,
                            'offset': self.offset, 'invalid_json_lines': self.invalid_lines,
                            'oversized_lines': self.oversized_lines, 'file_resets': self.resets,
                            'partial_line_bytes': len(self.partial)}
        return result


def find_session(thread_id, sessions_root):
    UUID(thread_id)  # Prevent glob metacharacters and ambiguous partial matches.
    matches = list(Path(sessions_root).rglob('rollout-*'+thread_id+'.jsonl'))
    if len(matches) != 1:
        raise ValueError('expected one rollout for thread; found '+str(len(matches)))
    return matches[0]


def write_status(path, result):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+'.tmp-'+uuid4().hex)
    try:
        temp.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def client_diagnostics(path, turn_id, *, as_of=None, utc_offset='-04:00'):
    """Bounded metadata-only extraction; never copy arbitrary client log text.

    Only explicit matching turn IDs establish attribution. This is not wire tracing.
    """
    result = {'source': str(path), 'matched_events': [], 'coverage': 'last_2_MiB',
              'request_sent': 'unknown', 'remote_response_received': 'unknown',
              'frontend_rendered': 'unknown'}
    if not turn_id:
        return result
    with Path(path).open('rb') as stream:
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(max(0, size - 2*1024*1024))
        if size > 2*1024*1024:
            stream.readline()
        lines = stream.read().decode('utf-8', errors='replace').splitlines()
    patterns = (
        ('idle timeout waiting for websocket', 'websocket_idle_timeout'),
        ('idle timeout waiting for SSE', 'sse_idle_timeout'),
        ('Broken pipe', 'broken_pipe'),
        ('websocket closed by server', 'websocket_closed'),
        ('failed to send websocket request', 'websocket_send_failed'),
        ('Reasoning summary item completed', 'client_response_item_completed'),
    )
    for line in lines:
        ids = re.findall(r'(?:turn_id|turnId)[=":\s]+([A-Za-z0-9-]+)', line)
        if turn_id not in ids:
            continue
        at = timestamp(line[:23].replace(' ', 'T') + utc_offset)
        if at is None or (as_of and at > as_of):
            continue
        for needle, category in patterns:
            if needle in line:
                entry = {'time': iso(at), 'turn_id': turn_id, 'category': category}
                retry = re.search(r'retries=(\d+)', line)
                if retry:
                    entry['retry'] = int(retry.group(1))
                if not result['matched_events'] or result['matched_events'][-1] != entry:
                    result['matched_events'].append(entry)
                break
    result['matched_events'] = result['matched_events'][-64:]
    return result


def diagnostic_signature(result):
    """Do not write repeated snapshots merely because the clock advanced."""
    return json.dumps({k: result.get(k) for k in (
        'turn_id', 'status', 'last_main_event_at', 'terminal_at',
        'pending_tools', 'subagents', 'client_diagnostics')}, sort_keys=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--thread-id')
    source.add_argument('--session-file', type=Path)
    parser.add_argument('--sessions-root', type=Path, default=Path.home()/'.codex/sessions')
    parser.add_argument('--threshold-seconds', type=float, default=300)
    parser.add_argument('--as-of', help='UTC/offset ISO timestamp for read-only historical replay')
    parser.add_argument('--watch', action='store_true', help='foreground incremental monitoring only')
    parser.add_argument('--interval', type=float, default=30)
    parser.add_argument('--status-file', type=Path)
    parser.add_argument('--events-file', type=Path, help='append diagnostics only on observed changes')
    parser.add_argument('--client-log', type=Path, help='optional Codex extension log; no raw text copied')
    parser.add_argument('--client-utc-offset', default='-04:00', help='offset of naive client log timestamps')
    args = parser.parse_args(argv)
    if args.threshold_seconds <= 0 or args.interval <= 0:
        parser.error('threshold and interval must be positive')
    as_of = timestamp(args.as_of) if args.as_of else None
    if args.as_of and as_of is None:
        parser.error('--as-of requires an ISO timestamp with a timezone')
    if args.watch and as_of:
        parser.error('--as-of and --watch cannot be combined')
    try:
        path = args.session_file or find_session(args.thread_id, args.sessions_root)
        sources = {path.resolve()}
        if args.client_log:
            sources.add(args.client_log.resolve())
        outputs = [p.resolve() for p in (args.status_file, args.events_file) if p]
        if any(p in sources for p in outputs) or len(outputs) != len(set(outputs)):
            raise ValueError('diagnostic outputs must be distinct from each other and input logs')
        if timestamp('2026-01-01T00:00:00'+args.client_utc_offset) is None:
            raise ValueError('invalid client UTC offset')
        reader = RolloutReader(path, args.thread_id)
        previous = None
        while True:
            reader.poll(as_of=as_of)
            result = reader.snapshot(as_of or datetime.now(timezone.utc), args.threshold_seconds)
            if args.client_log:
                result['client_diagnostics'] = client_diagnostics(args.client_log, result['turn_id'],
                    as_of=as_of, utc_offset=args.client_utc_offset)
            signature = diagnostic_signature(result)
            if signature != previous:
                if args.status_file:
                    write_status(args.status_file, result)
                if args.events_file:
                    args.events_file.parent.mkdir(parents=True, exist_ok=True)
                    with args.events_file.open('a') as stream:
                        stream.write(json.dumps(result, ensure_ascii=False)+'\n')
                print(json.dumps(result, ensure_ascii=False), flush=True)
                previous = signature
            if not args.watch:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError) as error:
        # OSError text can contain paths but never copied rollout contents.
        print(json.dumps({'status': 'monitor_error', 'error_type': type(error).__name__,
                          'diagnostic': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
