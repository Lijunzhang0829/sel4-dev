"""Prepare an explicitly authorized unresolved-policy fork; never start a service."""
import argparse
import json
from pathlib import Path

from regen.execution.repository_execution import fork_unresolved_policy_upgrade
from regen.infrastructure.incremental_log import read_artifact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run', type=Path, required=True,
                        help='Stopped source proof workspace with a clean paused budget')
    parser.add_argument('--run-dir', type=Path, required=True,
                        help='Fresh destination proof workspace; the source is never rewritten')
    parser.add_argument('--run-id', help='New identity, defaulting to destination directory name')
    parser.add_argument('--authorization', required=True,
                        help='Explicit authorization to change outcome policy and reopen old UNKNOWN work')
    args = parser.parse_args()
    try:
        fork_unresolved_policy_upgrade(args.source_run,args.run_dir,
            authorization=args.authorization,run_id=args.run_id)
        print(json.dumps(read_artifact(args.run_dir/'outcome-policy-upgrade.json'),indent=2))
    except (ValueError, OSError) as error:
        parser.exit(2, str(error)+'\n')


if __name__ == '__main__':
    main()
