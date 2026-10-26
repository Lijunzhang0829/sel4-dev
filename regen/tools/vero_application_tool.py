"""Minimal unprivileged VERO client; all authority stays in the host service."""
import argparse
import json
import socket


MAX_RESPONSE_BYTES = 64 * 1024 * 1024


def read_json_frame(stream, max_bytes=MAX_RESPONSE_BYTES):
    """Parse one complete newline-framed response, never a truncated prefix.

The host returns bounded previews and pages.  This larger transport guard also
permits older responses beyond the former 16 MiB truncation point, while an
unexpectedly huge response fails explicitly without unbounded allocation.
"""
    raw = stream.readline(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError('VERO response exceeds ' + str(max_bytes)
                         + ' bytes; use bounded diagnostic pages')
    if not raw.endswith(b'\n'):
        raise ValueError('incomplete VERO response: missing newline frame terminator')
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('invalid VERO JSON response: ' + str(error)) from error
    if not isinstance(value, dict):
        raise ValueError('invalid VERO response: expected a JSON object')
    return value


def argument_parser():
    parser=argparse.ArgumentParser()
    commands=parser.add_subparsers(dest='cmd',required=True)
    commands.add_parser('check').add_argument('module')
    commands.add_parser('begin-seal').add_argument('module')
    abort=commands.add_parser('abort-seal')
    abort.add_argument('module')
    abort.add_argument('transaction_id')
    search=commands.add_parser('search')
    search.add_argument('query')
    search.add_argument('--limit',type=int,default=20)
    diagnostic=commands.add_parser('diagnostic', help='read the full native log in bounded pages')
    diagnostic.add_argument('request_id',help='request_id from check diagnostics_metadata')
    diagnostic.add_argument('module',help='module from check diagnostics_metadata')
    diagnostic.add_argument('--offset',type=int,default=0,help='byte offset; follow next_offset until eof')
    diagnostic.add_argument('--limit',type=int,default=65536,help='requested bytes, 1 through 1048576')
    commands.add_parser('progress')
    scope=commands.add_parser('scope')
    scope.add_argument('target_ids',nargs='*')
    scope.add_argument('--theory',action='append',dest='theories')
    scope.add_argument('--phase',choices=['planning','proof','helper','retrieval','seal','integration','analysis'],default='proof')
    return parser


def main(argv=None):
    args=vars(argument_parser().parse_args(argv))
    try:
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as peer:
            peer.connect('/vero-tools/checker.sock')
            peer.sendall(json.dumps(args).encode()+b'\n')
            with peer.makefile('rb') as stream:
                value=read_json_frame(stream)
    except (OSError, ValueError) as error:
        # Keep stdout a valid JSON object even when a shell pipeline wraps us.
        value={'error':str(error),'type':type(error).__name__,'stage':'tool_transport'}
    print(json.dumps(value,ensure_ascii=False,indent=2))
    raise SystemExit(1 if value.get('error') else 0)


if __name__=='__main__':main()
