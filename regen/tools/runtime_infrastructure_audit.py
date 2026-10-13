"""Produce a reviewable proposal; this command NEVER applies a time correction."""
import argparse
import json
from pathlib import Path

from regen.execution.proof_commits import durable
from regen.infrastructure.runtime_budget_correction import plan_terminal_runtime_correction


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    proposal = plan_terminal_runtime_correction(args.source)
    if args.output:
        if args.output.resolve().is_relative_to(args.source.resolve()):
            parser.error('report must be outside the immutable source run')
        durable(args.output, proposal, immutable=True)
        print(json.dumps({'report': str(args.output.resolve()), 'correction_id': proposal['correction_id'],
            'refund_effective_seconds': proposal['adjustment']['effective_seconds'],
            'refund_worker_seconds': proposal['adjustment']['worker_seconds']}))
    else:
        print(json.dumps(proposal, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
