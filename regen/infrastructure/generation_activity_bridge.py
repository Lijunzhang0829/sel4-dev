"""Trusted shared harness events -> the serial experiment's effective clock."""
import threading
from contextlib import contextmanager
from regen.infrastructure.theory_activity import EXCLUDED_PHASES


class GenerationActivityBridge:
    def __init__(self, stage):
        self.stage = stage
        self.lock = threading.RLock()
        self.phases = {}
        self.recovery = None

    @contextmanager
    def acceptance(self, record):
        """Linearize first GREEN with journal publication, before HEAD/feedback.

        The journal invokes this only after validating the complete evidence.
        Native cancellation remains governed by its existing acceptance guard.
        """
        state=record['state'];theory=state['graph']['theory']
        with self.stage.lock:
            if theory not in self.stage.theories:
                raise ValueError('proof publication outside the frozen activity plan')
            fixed=self.stage.theories[theory]['contract_ids']
            newly_green=(fixed & set(state['proofs'])) - self.stage.theories[theory]['green_contract_ids']
            self.stage.check_publication(theory,newly_green)
            yield
            # This executes only after the immutable commit was published.
            # It must run before releasing the lock to the inactivity watchdog.
            fixed=self.stage.theories[theory]['contract_ids']
            newly_green=(fixed & set(state['proofs'])) - self.stage.theories[theory]['green_contract_ids']
            for uid in sorted(newly_green):
                self.stage.observe(uid,{'status':'green','source_commit':record['commit_id']},
                    kind='validation',theory=theory)
            if state['status']=='sealed':
                self.stage.mark_sealed(theory)

    def recovering(self):
        with self.lock:
            if self.recovery is None:
                self.recovery = self.stage.begin_exclusion('provider_recovery')

    def handle(self, event, fields):
        with self.lock:
            if event == 'generation_clock_untrusted':
                # Missing provider boundaries do not constitute proof failure.
                # Preserve charged work and GREEN commits; block publication.
                self.stage.ledger.append('generation_clock_untrusted',
                    evidence={key: fields[key] for key in (
                        'reason', 'generation_id', 'thread_id', 'turn_id',
                        'item_ids', 'model_lifecycle_policy', 'theory') if key in fields},
                    disposition='pause_measurement_invalid', evidence_trusted=False)
                self.stage.cancel('effective_clock_evidence_error')
            elif event == 'generation_provider_http_failure':
                self.stage.ledger.append(event,
                    evidence={key: fields[key] for key in (
                        'generation_id', 'thread_id', 'turn_id', 'failure',
                        'active_request_id', 'callback_executing', 'protocol_source',
                        'provider_request_id', 'retry_policy', 'theory') if key in fields},
                    disposition='bounded_new_thread_retry', refund_seconds=0)
            elif event == 'generation_items_interrupted':
                self.stage.ledger.append(event,
                    evidence={key: fields[key] for key in (
                        'generation_id', 'thread_id', 'turn_id', 'reason',
                        'item_ids', 'theory') if key in fields},
                    disposition='interrupted_not_completed', refund_seconds=0)
            elif event == 'generation_exclusion_started':
                reason, identity = fields['category'], fields['phase_id']
                if reason not in EXCLUDED_PHASES:
                    raise ValueError('unrecognized effective clock exclusion: '+reason)
                if identity in self.phases:
                    raise ValueError('duplicate effective clock phase start')
                self.phases[identity] = self.stage.begin_exclusion(reason)
            elif event == 'generation_exclusion_finished':
                self.stage.end_exclusion(self.phases.pop(fields['phase_id']))
            elif event == 'theory_generation_started':
                if hasattr(self.stage, 'note_provider_started'):
                    self.stage.note_provider_started()
                if self.recovery is not None:
                    handle, self.recovery = self.recovery, None
                    self.stage.end_exclusion(handle)
            elif event == 'theory_generation_stopped':
                if hasattr(self.stage, 'note_provider_failure'):
                    self.stage.note_provider_failure()
                # Starts at confirmed failure/close, never retroactively at the
                # last token: unexplained silent waiting remains charged.
                if not self.stage.event.is_set():
                    self.recovering()
            elif event in ('model_work_observed', 'model_work_observation_finished'):
                if hasattr(self.stage, 'set_model_overlap'):
                    self.stage.set_model_overlap(event == 'model_work_observed')

    def close(self):
        with self.lock:
            if self.phases:
                self.stage.ledger.append('generation_clock_unclosed_phases',
                    phase_ids=sorted(self.phases), disposition='pause_no_silent_recovery')
                self.stage.cancel('unclosed_harness_phase')
                for handle in list(self.phases.values()):
                    self.stage.end_exclusion(handle)
                self.phases.clear()
            if self.recovery is not None:
                handle, self.recovery = self.recovery, None
                self.stage.end_exclusion(handle)
