"""Stdlib-only client for the trusted planning service."""
import argparse
import json
import socket
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['contracts','definitions','search','inspect','related','validate','preflight','pilot','submit'])
    p.add_argument('values', nargs='*')
    p.add_argument('--limit', type=int, default=20)
    p.add_argument('--offset', type=int, default=0, help='pagination for definitions/related')
    p.add_argument('--pilot-review', help='Explain final plan decision and remaining pilot risks')
    a = p.parse_args()
    request = {'action':a.action, 'values':a.values, 'limit':a.limit, 'offset':a.offset}
    if a.action in {'validate','preflight','pilot','submit'}:
        if len(a.values) != 1:
            p.error('provide one plan JSON file')
        try:
            request['proposal'] = json.loads(Path(a.values[0]).read_text())
        except (OSError, ValueError) as e:
            print(json.dumps({'valid':False,'errors':[str(e)]})); return 1
    if a.pilot_review:
        request['pilot_review'] = a.pilot_review
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect('/api/planner.sock')
        client.sendall(json.dumps(request).encode()+b'\n')
        client.shutdown(socket.SHUT_WR)
        with client.makefile('rb') as stream:
            response = json.loads(stream.readline(8_000_001))
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 1 if response.get('valid') is False or response.get('error') else 0


if __name__ == '__main__':
    sys.exit(main())
