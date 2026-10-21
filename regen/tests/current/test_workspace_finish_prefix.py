"""Seal audits a durable callback prefix while later transcript bytes append.

Synthetic protocol/executable identities only; no provider or native calls.
The production transcript auditor remains active; proof execution is not run.
"""
import unittest
from unittest.mock import patch

from regen.infrastructure.workspace_transcript import audit_workspace_session
from regen.tests.current import test_proof_workspace_agent as workspace_fixtures
from regen.tests.current import test_workspace_transcript as transcript_fixtures


class WorkspaceFinishPrefixTests(unittest.TestCase):
    def setUp(self):
        self.workspace = workspace_fixtures.ProofWorkspaceAgentTest()
        self.workspace.setUp()
        self.addCleanup(self.workspace.doCleanups)
        self.controller = self.workspace.controller(theories=['Target'])
        self.transcript = transcript_fixtures.WorkspaceTranscriptTest()
        self.transcript.setUp()
        self.addCleanup(self.transcript.doCleanups)
        self.transcript.request['params']['arguments'] = {
            'theory':'Target', 'proposal':{'action':'finish'}}
        self.transcript.events[-1] = self.transcript.event('in', self.transcript.request)
        self.transcript.write_events()

    def finish(self, metadata=None):
        return self.controller.proof_action(
            {'theory':'Target', 'proposal':{'action':'finish'}},
            self.transcript.metadata if metadata is None else metadata)

    def test_partial_append_after_boundary_does_not_reject_clean_finish_prefix(self):
        offset = self.transcript.metadata['transcript_offset']
        with self.transcript.path.open('ab') as stream:
            stream.write(b'{"time_unix":1,"direction":"in","message":')
        with self.assertRaisesRegex(ValueError, 'incomplete workspace transcript boundary'):
            audit_workspace_session(self.transcript.path, require_terminal=False)
        with patch('regen.planning.proof_workspace_agent._run_batch_file') as execute, patch(
                'regen.infrastructure.workspace_transcript.audit_workspace_session',
                wraps=audit_workspace_session) as audit:
            self.finish()
        execute.assert_called_once()
        audit.assert_called_once_with(str(self.transcript.path), require_terminal=False,
                                       require_real=True, offset=offset)

    def test_unsafe_event_inside_bound_prefix_rejects_before_proof_dispatch(self):
        self.transcript.events.insert(-1, self.transcript.event('in', {
            'method':'item/started', 'params':{'item':{'type':'commandExecution'}}}))
        self.transcript.write_events()
        with patch('regen.planning.proof_workspace_agent._run_batch_file') as execute:
            with self.assertRaisesRegex(ValueError, 'built-in tool'):
                self.finish()
        execute.assert_not_called()

    def test_missing_or_invalid_boundary_never_falls_back_to_whole_file(self):
        for value in (None, 0, -1, True, '1'):
            metadata = dict(self.transcript.metadata)
            if value is None:
                metadata.pop('transcript_offset')
            else:
                metadata['transcript_offset'] = value
            with self.subTest(offset=value), patch(
                    'regen.planning.proof_workspace_agent._run_batch_file') as execute:
                with self.assertRaisesRegex(ValueError, 'durable workspace transcript offset'):
                    self.finish(metadata)
                execute.assert_not_called()

    def test_non_line_boundary_and_truncated_source_are_rejected(self):
        for offset in (1, self.transcript.metadata['transcript_offset'] + 10):
            with self.subTest(offset=offset), patch(
                    'regen.planning.proof_workspace_agent._run_batch_file') as execute:
                with self.assertRaises(ValueError):
                    self.finish({**self.transcript.metadata, 'transcript_offset':offset})
                execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
