"""Minimal unprivileged VERO client; all authority stays in the host service."""
import argparse
import json
import socket


def main():
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
    commands.add_parser('progress')
    scope=commands.add_parser('scope')
    scope.add_argument('target_ids',nargs='*')
    scope.add_argument('--theory',action='append',dest='theories')
    scope.add_argument('--phase',choices=['planning','proof','helper','retrieval','seal','integration','analysis'],default='proof')
    args=vars(parser.parse_args())
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as peer:
        peer.connect('/vero-tools/checker.sock')
        peer.sendall(json.dumps(args).encode()+b'\n')
        with peer.makefile('rb') as stream:
            value=json.loads(stream.readline(16*1024*1024))
    print(json.dumps(value,ensure_ascii=False,indent=2))
    raise SystemExit(1 if value.get('error') else 0)


if __name__=='__main__':main()
