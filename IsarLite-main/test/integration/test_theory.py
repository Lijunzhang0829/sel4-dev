"""
Integration tests: theory loading and API-level state queries.
"""

from __future__ import annotations

import pytest

from returns.maybe import Nothing, Some
from returns.result import Failure, Success

from isarlite.cli import PIDESession
from test.conftest import THYS
from test.integration.conftest import (
    HOL_HEAP_PATH,
    ISABELLE_HOME,
    PURE_HEAP_PATH,
    requires_hol,
)


@pytest.mark.integration
class TestLoadTheory:
    """
    Theory loading and state queries (require HOL heap).
    """

    @requires_hol
    def test_load_ackermann_drains_messages(self) -> None:
        """
        Loading Ackermann.thy should produce exec assignments.
        """
        assert PURE_HEAP_PATH is not None and HOL_HEAP_PATH is not None
        with PIDESession(ISABELLE_HOME) as session:
            session.connect(heap=(PURE_HEAP_PATH, HOL_HEAP_PATH))
            session.load_theory(THYS.Ackermann)
            assert len(session._assignments) > 0, "no assignments from load"

    @requires_hol
    def test_state_at_returns_goal(self) -> None:
        """state_at(76, 1) returns the open proof goal."""
        assert PURE_HEAP_PATH is not None and HOL_HEAP_PATH is not None
        with PIDESession(ISABELLE_HOME) as session:
            session.connect(heap=(PURE_HEAP_PATH, HOL_HEAP_PATH))
            session.load_theory(THYS.Ackermann, visible_through=(77, 0))
            result = session.state_at(76, 1)
            assert isinstance(result, Success), f"state_at returned failure: {result}"
            state, _ = result.unwrap()
            assert isinstance(state, Some), "expected state, got Nothing"
            text = state.unwrap()
            assert "proof (prove)" in text, f"expected open proof state, got:\n{text}"
            assert "ackloop_dom" in text
            assert "boolackloop_dom" not in text, (
                f"type annotation noise present in:\n{text}"
            )
            assert "ackloop_dom (ack" in text, (
                f"expected goal with spacing, got:\n{text}"
            )

    @requires_hol
    def test_state_at_53_1_both_nothing(self) -> None:
        """
        state_at(53, 1) — preceding span has neither STATE nor INFORMATION.
        """
        assert PURE_HEAP_PATH is not None and HOL_HEAP_PATH is not None
        with PIDESession(ISABELLE_HOME) as session:
            session.connect(heap=(PURE_HEAP_PATH, HOL_HEAP_PATH))
            session.load_theory(THYS.Ackermann)
            result = session.state_at(53, 1)
            assert isinstance(result, Success), f"expected Success, got {result}"
            state, info = result.unwrap()
            assert state == Nothing
            assert info == Nothing

    @requires_hol
    def test_state_at_60_1_both_present(self) -> None:
        """
        state_at(60, 1) — preceding span has both STATE and INFORMATION.
        """
        assert PURE_HEAP_PATH is not None and HOL_HEAP_PATH is not None
        with PIDESession(ISABELLE_HOME) as session:
            session.connect(heap=(PURE_HEAP_PATH, HOL_HEAP_PATH))
            session.load_theory(THYS.Ackermann)
            result = session.state_at(60, 1)
            assert isinstance(result, Success), f"expected Success, got {result}"
            state, info = result.unwrap()
            assert isinstance(state, Some), "expected state, got Nothing"
            assert isinstance(info, Some), "expected info, got Nothing"
            assert "goal" in state.unwrap()
            assert "Proof outline" in info.unwrap()

    @requires_hol
    def test_state_at_61_1_state_only(self) -> None:
        """
        state_at(61, 1) — preceding span has STATE but no INFORMATION.
        """
        assert PURE_HEAP_PATH is not None and HOL_HEAP_PATH is not None
        with PIDESession(ISABELLE_HOME) as session:
            session.connect(heap=(PURE_HEAP_PATH, HOL_HEAP_PATH))
            session.load_theory(THYS.Ackermann)
            result = session.state_at(61, 1)
            assert isinstance(result, Success), f"expected Success, got {result}"
            state, info = result.unwrap()
            assert isinstance(state, Some), "expected state, got Nothing"
            assert info == Nothing

    @requires_hol
    def test_state_at_null_on_empty_position(self) -> None:
        """
        A position with no command (e.g. end of file) returns Failure.
        """
        assert PURE_HEAP_PATH is not None and HOL_HEAP_PATH is not None
        with PIDESession(ISABELLE_HOME) as session:
            session.connect(heap=(PURE_HEAP_PATH, HOL_HEAP_PATH))
            session.load_theory(THYS.Ackermann)
            result = session.state_at(999, 1)
            assert result == Failure("position overflow")
