"""Legacy raw-process protocol on Isabelle2026's supported theory processor.

This runs inside the network-disabled native container. Each request has a
private temporary session, leaving the bound parent heaps read-only.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['process'])
    parser.add_argument('-l', required=True)
    parser.add_argument('-d', action='append', default=[])
    parser.add_argument('-o', action='append', default=[])
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('-T')
    action.add_argument('-f')
    action.add_argument('-e')
    args = parser.parse_args()
    command = ['/workspace/verification/isabelle/bin/isabelle', 'process_theories', '-O', '-l', args.l]
    for directory in args.d: command.extend(['-d', directory])
    for option in args.o: command.extend(['-o', option])
    with tempfile.TemporaryDirectory(prefix='regen-calg-process-') as temp:
        if args.T:
            source = Path(args.T)
            command.extend(['-D', str(source.parent), source.name])
        else:
            source = Path(temp) / 'Regen_Process.thy'
            body = ('ML_file ' + json.dumps(args.f) if args.f else 'ML \\<open>\n' + args.e + '\n\\<close>')
            source.write_text('theory Regen_Process imports "Calg_Project_Base.Project_Input" begin\n' + body + '\nend\n')
            command.extend(['-D', temp, 'Regen_Process'])
        return subprocess.run(command).returncode


if __name__ == '__main__':
    raise SystemExit(main())
