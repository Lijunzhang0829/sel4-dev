"""Direct repository planning launcher with one persisted retry per timeout pair."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import time

from regen.infrastructure.llm_transcript import (
    artifact_paths, allocate_artifact_prefix, reconcile_transcript_family,
)
from regen.preparation.refine_campaign import write_immutable_json


def call_with_retry(call, prompt, prefix, *, retry_gap=10, **options):
    prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
    failed = []
    for item in reconcile_transcript_family(prefix):
        paths = artifact_paths(Path(item['prefix']))
        if not paths['meta'].is_file():
            continue
        meta = json.loads(paths['meta'].read_text())
        if (meta.get('prompt_sha256') == prompt_hash
                and meta.get('model') == options['model']
                and meta.get('stage') == options['stage']):
            if meta.get('timed_out'):
                failed.append((paths, meta))
            else:
                failed = []
    if len(failed) >= 2:
        raise RuntimeError('repository planning paused after two consecutive timeouts')
    while len(failed) < 2:
        if failed:
            time.sleep(retry_gap)
        actual = allocate_artifact_prefix(prefix)
        try:
            final = call(prompt, actual, **options)
        except RuntimeError:
            paths = artifact_paths(actual)
            if not paths['meta'].exists():
                raise
            meta = json.loads(paths['meta'].read_text())
            if not meta.get('timed_out'):
                raise
            failed.append((paths, meta))
            continue
        # Failed partial responses must never become input to the successful call.
        # Keep minimal timing/usage records while discarding their response text.
        for paths, meta in failed:
            usage = []
            for line in paths['raw'].read_text().splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get('usage'):
                    usage.append(event['usage'])
            record = {key: meta.get(key) for key in (
                'model', 'stage', 'started_at', 'finished_at', 'wall_ms',
                'returncode', 'timed_out', 'prompt_sha256',
            )}
            write_immutable_json(Path(str(paths['meta']) + '.timeout-summary.json'),
                                 {**record, 'usage': usage})
            for key in ('raw', 'final', 'stderr'):
                paths[key].write_text('')
                meta[key + '_sha256'] = hashlib.sha256(b'').hexdigest()
            meta['partial_response_discarded'] = True
            paths['meta'].write_text(json.dumps(meta, indent=2, sort_keys=True) + '\n')
        return final, actual
    raise RuntimeError('repository planning paused after two consecutive timeouts')


def main():
    from regen.planning.repository_planner import main as plan
    return plan()


if __name__ == '__main__':
    raise SystemExit(main())
