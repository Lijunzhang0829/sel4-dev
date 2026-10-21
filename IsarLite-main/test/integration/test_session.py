"""
Integration tests: prover session lifecycle and protocol edge cases.
"""

from __future__ import annotations

import pytest
from isarlite.base import IsarYXMLUtils, MarkupKind, YXMLElemModel
from isarlite.cli import PIDESession
from test.integration.conftest import ISABELLE_HOME, requires_pure


@pytest.mark.integration
class TestSessionLifecycle:
    """
    Prover lifecycle and basic communication.
    """

    @requires_pure
    def test_connect_and_echo(self) -> None:
        """
        Connect to Pure, send echo, verify writeln response.
        """
        with PIDESession(ISABELLE_HOME) as session:
            session.connect()
            assert session.prover is not None
            session.prover.protocol_command("Prover.echo", "hello pytest")
            msgs = session.read_until(timeout=5.0)
            kinds = [m.name for m in msgs]
            assert MarkupKind.WRITELN in kinds, f"got kinds: {kinds}"

    @requires_pure
    def test_clean_shutdown(self) -> None:
        """
        Disconnect should not raise.
        """
        session = PIDESession(ISABELLE_HOME)
        session.connect()
        session.disconnect()
        assert session.prover is None


@pytest.mark.integration
class TestProtocolEdgeCases:
    """
    Protocol-level edge cases not covered by ``load_theory``'s happy path.
    """

    @requires_pure
    def test_empty_document_update(self) -> None:
        """
        Document.update with no edits should be accepted without failure.
        """
        with PIDESession(ISABELLE_HOME) as session:
            session.connect()
            assert session.prover is not None
            chunks = IsarYXMLUtils.encode_document_update("0", "1", [], consolidate=[])
            session.prover.send_command_raw("Document.update", chunks[1:])
            msgs = session.read_until(timeout=5.0)
            for m in msgs:
                body = YXMLElemModel.content_of(m.body) if m.body else ""
                if "failure" in body:
                    pytest.fail(f"unexpected failure: {body}")

    @requires_pure
    def test_echo_after_empty_update(self) -> None:
        """
        Prover stays alive and responds after an empty Document.update.
        """
        with PIDESession(ISABELLE_HOME) as session:
            session.connect()
            assert session.prover is not None
            chunks = IsarYXMLUtils.encode_document_update("0", "1", [], consolidate=[])
            session.prover.send_command_raw("Document.update", chunks[1:])
            session.read_until(timeout=3.0)
            session.prover.send_command("Prover.echo", ["still_alive"])
            msgs = session.read_until(timeout=5.0)
            assert any(m.name == MarkupKind.WRITELN for m in msgs), (
                f"no writeln in {[m.name for m in msgs]}"
            )

    @requires_pure
    def test_bad_command_does_not_kill_prover(self) -> None:
        """
        A malformed protocol command should not crash the prover.
        """
        with PIDESession(ISABELLE_HOME) as session:
            session.connect()
            assert session.prover is not None
            session.prover.send_command_raw(
                "Document.define_command", [b"1", b"theory"]
            )
            session.read_until(timeout=3.0)
            session.prover.send_command("Prover.echo", ["after_error"])
            msgs = session.read_until(timeout=5.0)
            assert any(m.name == MarkupKind.WRITELN for m in msgs), (
                f"prover did not respond: {[m.name for m in msgs]}"
            )
