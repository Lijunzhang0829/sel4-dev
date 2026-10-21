"""Recoverable active wall-clock budget for a whole proof experiment.

Count every live experiment phase, including planning, native preparation,
checking, seal and integration. Only a confirmed transport interruption or
explicit experiment pause closes the gate. Offline time is never charged.
"""
import math

from regen.infrastructure.effective_time_budget import EffectiveTimeBudget

POLICY = 'global-active-wall-budget-v1'
LIMIT_REASON = 'active_wall_budget_reached'
INTERRUPTIONS = frozenset({
    'provider_recovery', 'stream_retry', 'provider_stream_retry',
    'experiment_pause',
})


class ActiveWallBudget(EffectiveTimeBudget):
    policy = POLICY
    limit_reason = LIMIT_REASON
    monitor_error_reason = 'active_wall_monitor_error'
    persistence_error_reason = 'active_wall_budget_persistence_error'

    def __init__(self, *args, **kwargs):
        # Startup is normal active runtime. The initial provider_recovery phase
        # opened by GenerationActivityBridge is not an interrupted run.
        self._provider_started_this_process = False
        super().__init__(*args, **kwargs)

    def _open_workers(self):
        if self.event.is_set() or self.closed.is_set() or self.finished:
            return []
        interrupted = any(
            item['reason'] in INTERRUPTIONS
            and (item['reason'] != 'provider_recovery'
                 or self._provider_started_this_process)
            for item in self.exclusions.values()
        )
        return [] if interrupted else ['wall']

    def _settle(self):
        now = self.monotonic()
        if not math.isfinite(now) or now < self._last_tick:
            self.event.set()
            self.reason = 'active_wall_clock_error'
            raise ValueError('active wall monotonic clock moved backwards')
        elapsed = now - self._last_tick
        if self._open_workers():
            self.effective_seconds += elapsed
            self.worker_seconds += elapsed
            theory = self.active_theory
            if theory in self.theories:
                self._theory_seconds[theory] = self._theory_seconds.get(theory, 0.) + elapsed
        self._last_tick = now

    def note_provider_started(self):
        with self.lock:
            self._settle()
            self._provider_started_this_process = True

    def note_provider_failure(self):
        # A failure before the first successful model event still begins an
        # interruption; only subsequent recovery/backoff is excluded.
        self.note_provider_started()

    def _cancel_locked(self, reason):
        """The configured wall limit is terminal; operational pauses are resumable."""
        if reason != self.limit_reason:
            return super()._cancel_locked(reason)
        if self.event.is_set():
            return
        self.reason = reason
        self.event.set()
        self._append('termination', reason=reason, disposition='stop',
            activity_policy=self.policy, effective_seconds=self.effective_seconds,
            worker_seconds=self.worker_seconds,
            theory_seconds=dict(self._theory_seconds), seconds=self.seconds,
            token_limit=None)
        self._append('time_budget_finished', reason=reason,
            effective_seconds=self.effective_seconds)
        self.finished = True
        self.finish_reason = reason
        self._write_state('finished')

    def _compact(self):
        state = super()._compact()
        state['clock_basis'] = 'active_wall'
        state['active_wall_seconds'] = state['effective_seconds']
        return state

    def snapshot(self):
        value = super().snapshot()
        value['clock_basis'] = 'active_wall'
        value['active_wall_seconds'] = value['effective_seconds']
        value['active_wall_seconds_limit'] = value['seconds']
        return value
