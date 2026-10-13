"""
Common fixtures for IsarLite tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from isarlite.base import KeywordTable, Tokenizer

PROJECT_ROOT: Path = Path(__file__).parent.parent
ISABELLE_HOME: Path = PROJECT_ROOT / "refs" / "Isabelle2025"


class THYS:
    """
    Paths to .thy test fixtures.
    """

    Ackermann: Path = ISABELLE_HOME / "src/HOL/Examples/Ackermann.thy"
    Adhoc_Overloading: Path = ISABELLE_HOME / "src/HOL/Examples/Adhoc_Overloading.thy"


def discover_isabelle() -> Path | None:
    """
    Check if ISABELLE_HOME is usable.
    """

    if (ISABELLE_HOME / "bin" / "isabelle").is_file():
        return ISABELLE_HOME
    return None


# ---- fixtures ----


@pytest.fixture
def ackermann_source() -> str:
    return THYS.Ackermann.read_text(encoding="utf-8")


@pytest.fixture
def adhoc_overloading_source() -> str:
    return THYS.Adhoc_Overloading.read_text(encoding="utf-8")


@pytest.fixture
def bootstrap_tokenizer() -> Tokenizer:
    return Tokenizer(KeywordTable.bootstrap(ISABELLE_HOME))


@pytest.fixture(scope="session")
def isabelle_home() -> Path | None:
    return discover_isabelle()
