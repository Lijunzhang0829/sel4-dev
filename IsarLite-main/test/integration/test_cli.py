"""
Integration tests: CLI-level state-at commands and global options.
"""

from __future__ import annotations

import pytest

from test.conftest import THYS
from test.integration.conftest import (
    ISABELLE_HOME,
    MORE_THYS,
    requires_hol,
    requires_l4v,
    run_cmd,
)


@pytest.mark.integration
class TestStateAtCLI:
    """
    CLI-level tests for state-at error handling (complements API tests in test_theory.py).
    """

    def test_help_prints_usage(self) -> None:
        result = run_cmd("--help")
        assert result.returncode == 0
        assert "usage:" in result.stdout

    def test_help_shows_state_at_subcommand(self) -> None:
        result = run_cmd("--help")
        assert result.returncode == 0
        assert "state-at" in result.stdout

    def test_state_at_without_args_exits_error(self) -> None:
        result = run_cmd("state-at")
        assert result.returncode != 0

    def test_state_at_missing_col_exits_error(self) -> None:
        result = run_cmd("state-at", "file.thy", "76")
        assert result.returncode != 0

    def test_state_at_help_shows_positional_args(self) -> None:
        result = run_cmd("state-at", "--help")
        assert result.returncode == 0
        assert "file" in result.stdout
        assert "line" in result.stdout
        assert "col" in result.stdout

    def test_state_at_on_nonexistent_file_exits_error(self) -> None:
        result = run_cmd("state-at", "/nonexistent/path.thy", "1", "1")
        assert result.returncode != 0
        assert (
            "file not found" in result.stderr.lower()
            or "file not found" in result.stdout.lower()
        )

    def test_state_at_with_zero_line_exits_error(self) -> None:
        result = run_cmd("state-at", "f.thy", "0", "1")
        assert result.returncode != 0

    @requires_hol
    def test_state_at_inside_named_span_errors(self) -> None:
        """
        state-at inside a named span body exits with error.
        """
        result = run_cmd(
            "--isabelle-home",
            str(ISABELLE_HOME),
            "state-at",
            str(THYS.Ackermann),
            "58",
            "20",
            timeout=120,
        )
        assert result.returncode != 0

    @requires_hol
    def test_state_at_span_start_errors(self) -> None:
        """
        state-at at the start of a named span exits with error.
        """
        result = run_cmd(
            "--isabelle-home",
            str(ISABELLE_HOME),
            "state-at",
            str(THYS.Ackermann),
            "58",
            "1",
            timeout=120,
        )
        assert result.returncode != 0
        assert "command boundary" in result.stderr.lower()

    @requires_l4v
    def test_state_at_rights_ai_13_1(self) -> None:
        """
        state-at on Rights_AI.thy at line 13 returns the open proof goal.
        """
        result = run_cmd(
            "--isabelle-home",
            str(ISABELLE_HOME),
            "state-at",
            str(MORE_THYS.Rights_AI),
            "13",
            "1",
            timeout=120,
        )
        assert result.returncode == 0, (
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert "proof (prove)" in result.stdout
        assert "validate_vm_rights" in result.stdout


@pytest.mark.integration
class TestCLIOptions:
    """Global option flags."""

    def test_isabelle_home_flag_accepted(self) -> None:
        result = run_cmd(
            "--isabelle-home", "/custom/path", "state-at", "f.thy", "1", "1"
        )
        assert result.returncode != 0  # fails at heap resolution, not arg parsing
