"""One recoverable planning clock, with a checked handoff to proof generation.

Only trusted harness stdout is observed. Provider stdout stays in its separate
transcripts; model prose cannot grant an exclusion from the experiment budget.
"""
from contextlib import suppress
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import threading

from regen.execution.proof_commits import durable
from regen.infrastructure.effective_time_budget import EffectiveTimeBudget, POLICY
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.generation_activity_bridge import GenerationActivityBridge


STOP_GRACE_SECONDS = 10
READER_JOIN_SECONDS = 5


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ReviewedPlanningBudget:
    def __init__(self, root, *, seconds, binding):
        self.root = Path(root)
        self.binding = dict(binding)
        self.ledger = Ledger(self.root/'budget', self.root.name)
        if self.ledger.rows:
            raise ValueError('reviewed planning requires a fresh budget; never reset charged work')
        self.ledger.append('reviewed_planning_budget_binding', **self.binding,
                           seconds=seconds, include_planning=True, policy=POLICY)
        self.stage = EffectiveTimeBudget(self.ledger, seconds=seconds, include_planning=True)
        self.process = None
        self.process_lock = threading.RLock()

    def start(self):
        self.stage.begin_planning()
        self.stage.start(abort=self.interrupt)

    def interrupt(self):
        with self.process_lock:
            process = self.process
            if process is not None and process.poll() is None:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGINT)

    @staticmethod
    def stop(process):
        # The group can outlive its leader: a grandchild may still hold stdout
        # or a native resource after the direct child exits.
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGINT)
        try:
            process.wait(timeout=STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        finally:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=STOP_GRACE_SECONDS)

    def run(self, command, log):
        """Charge live child activity, including all schema-repair attempts."""
        self.stage.check()
        bridge = GenerationActivityBridge(self.stage)
        errors = []
        process = subprocess.Popen(command, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
            start_new_session=True)
        with self.process_lock:
            self.process = process

        def observe():
            try:
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    if line.startswith('[regen-event] '):
                        event = json.loads(line[len('[regen-event] '):])
                        if not isinstance(event, dict) or not isinstance(event.get('event'), str):
                            raise ValueError('malformed trusted planning event')
                        bridge.handle(event['event'], event)
            except BaseException as error:
                errors.append(error)
                try:
                    self.stage.cancel('planning_clock_event_error')
                finally:
                    self.interrupt()

        reader = threading.Thread(target=observe, name='planning-budget-events', daemon=True)
        reader.start()
        try:
            while process.poll() is None:
                self.stage.event.wait(.1)
                self.stage.check()
            reader.join(timeout=READER_JOIN_SECONDS)
            if reader.is_alive():
                raise RuntimeError('planning child left an open output stream')
            if errors:
                raise errors[0]
            bridge.close()
            self.stage.check()
            if process.returncode:
                raise subprocess.CalledProcessError(process.returncode, command)
        except BaseException:
            # A failed stage cannot later be frozen if a caller catches its
            # exception. Existing timeout/audit reasons are retained by cancel.
            self.stage.cancel('reviewed_planning_child_failed')
            raise
        finally:
            self.stop(process)
            reader.join(timeout=READER_JOIN_SECONDS)
            with self.process_lock:
                self.process = None
            bridge.close()
            if not reader.is_alive():
                process.stdout.close()

    def complete(self, frozen):
        """Publish only a complete plan whose total planning cost fits the cap."""
        # Resolve and hash both references before publishing any freeze marker.
        # A missing/cross-run plan must not leave a misleading frozen-plan.json.
        if frozen.get('state') != 'frozen':
            raise ValueError('planning budget requires a frozen final plan')
        paths = {}
        for key in ('plan', 'file_generation_list'):
            path = Path(frozen[key])
            path = (path if path.is_absolute() else self.root/path).resolve()
            if not path.is_relative_to(self.root.resolve()):
                raise ValueError('planning budget plan artifact escapes its planning directory')
            paths[key] = path
        plan_hash = file_hash(paths['plan'])
        listing_hash = file_hash(paths['file_generation_list'])
        if frozen.get('plan_sha256') != plan_hash:
            raise ValueError('planning budget final plan hash mismatch')
        with self.process_lock:
            if self.process is not None:
                raise ValueError('planning child must exit before freezing')
        with self.stage.lock:
            self.stage.check()
            if self.stage.exclusions:
                raise ValueError('planning clock has unfinished exclusions')
            self.stage.end_planning()
            self.stage.check()
        self.stage.close()
        snapshot = self.stage.snapshot()
        if (snapshot['paused'] or snapshot['reason'] is not None
                or snapshot['planning_active'] or snapshot['gate_open']
                or not snapshot['accounting_complete']):
            raise RuntimeError('planning clock did not close cleanly; cannot freeze')
        frozen_path = self.root/'frozen-plan.json'
        durable(frozen_path, frozen, immutable=True)
        receipt = {
            'schema_version': 1, 'state': 'complete', 'policy': POLICY,
            'include_planning': True, 'seconds': self.stage.seconds,
            **{k: self.binding[k] for k in ('input_sha256', 'model', 'reasoning_effort')},
            'frozen_plan_sha256': file_hash(frozen_path),
            'repository_plan_sha256': plan_hash,
            'file_generation_list_sha256': listing_hash,
            'ledger_run_id': self.ledger.run_id,
            'ledger_tail_event_id': self.ledger.rows[-1]['event_id'],
            'planning_seconds': snapshot['planning_seconds'],
            'effective_seconds': snapshot['effective_seconds'],
            'remaining_seconds': snapshot['remaining_seconds'],
        }
        durable(self.root/'budget-receipt.json', receipt, immutable=True)
        return receipt

    def close(self, *, failed=False):
        self.interrupt()
        if self.stage.closed.is_set():
            return
        if failed:
            self.stage.cancel('reviewed_planning_failed')
        if self.stage.planning_active:
            self.stage.end_planning()
        self.stage.close()
