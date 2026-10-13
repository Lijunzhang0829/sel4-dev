"""Read-only saved-state benchmark; never migrate an authority journal in place."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import time

from regen.execution.proof_commits import digest, durable
from regen.planning.attempt_memory import compact_target_memory, expand_target_memory
from regen.planning.batch_file_planner import planner_input
from regen.tools.harness_acceptance_baseline import protected_snapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--theory', required=True)
    parser.add_argument('--sequence', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root, output = args.run.resolve(), args.output.resolve()
    if output.exists() or output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError('output must be new and outside the source run')
    before = protected_snapshot(root)
    path = root/'theories'/args.theory/'acceptance/commits'/f'{args.sequence:06d}.json'
    start = time.monotonic()
    original = json.loads(path.read_text())
    decode_seconds = time.monotonic()-start
    state = original['state']
    start = time.monotonic()
    compact = compact_target_memory(deepcopy(state))
    normalize_seconds = time.monotonic()-start
    restored = expand_target_memory(compact)
    package = json.loads((root/'packages'/(args.theory+'.thy.json')).read_text())
    equal = restored == state
    prompt_equal = planner_input(package,state) == planner_input(package,compact)
    # This is an isolated measurement, NOT a valid migrated acceptance commit:
    # its old commit_id would no longer bind the changed representation.
    sample = {**original, 'state':compact}
    output.mkdir(parents=True)
    start = time.monotonic()
    durable(output/'sample-not-authoritative.json',sample,immutable=True)
    write_seconds = time.monotonic()-start
    size = (output/'sample-not-authoritative.json').stat().st_size
    after = protected_snapshot(root)
    report = dict(source=str(path), old_bytes=path.stat().st_size, new_bytes=size,
        reduction_percent=100*(1-size/path.stat().st_size),
        state_equal=equal, planner_input_equal=prompt_equal,
        state_sha256=digest(state), reconstructed_state_sha256=digest(restored),
        unique_attempts=len(compact.get('batch_attempt_objects',{})),
        target_count=len(compact.get('batch_target_memory',{})),
        legacy_decode_seconds=decode_seconds, normalization_seconds=normalize_seconds,
        compact_write_seconds=write_seconds, protected_unchanged=before==after,
        experiment_resumed=False, native_checks=0, model_calls=0)
    durable(output/'report.json',report,immutable=True)
    print(json.dumps(report,indent=2))
    if not (equal and prompt_equal and before==after):
        raise ValueError('saved-state equivalence failed')


if __name__ == '__main__':
    main()
