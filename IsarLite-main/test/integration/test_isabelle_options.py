"""
Integration test: roundtrip PIDEInteractiveOptions against Isabelle defaults.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from isarlite.base import PIDEInteractiveOptions
from test.integration.conftest import ISABELLE_BIN, ISABELLE_HOME, requires_isabelle


def _dump_options_yxml() -> str:
    """Run ``isabelle options -x FILE`` and return the YXML content."""
    with tempfile.NamedTemporaryFile(suffix=".yxml", delete=False) as tmp:
        dump_path = tmp.name
    try:
        result = subprocess.run(
            [str(ISABELLE_BIN), "options", "-x", dump_path],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "ISABELLE_HOME": str(ISABELLE_HOME)},
        )
        result.check_returncode()
        return Path(dump_path).read_text(encoding="utf-8")
    finally:
        Path(dump_path).unlink(missing_ok=True)


@pytest.mark.integration
@requires_isabelle
def test_roundtrip_isabelle_defaults() -> None:
    """PIDEInteractiveOptions roundtrips Isabelle's actual option defaults.

    1. Shells out to ``isabelle options -x`` for the full defaults YXML
    2. Parses into ``PIDEInteractiveOptions``
    3. Re-encodes via ``.encode_yxml()``
    4. Parses the re-encoded output
    5. Verifies every modeled field matches the original
    """
    yxml_input = _dump_options_yxml()
    assert len(yxml_input) > 0, "isabelle options -x produced empty output"

    # Parse → re-encode → parse again
    opts = PIDEInteractiveOptions.from_yxml(yxml_input)
    yxml_output = opts.encode_yxml()
    opts2 = PIDEInteractiveOptions.from_yxml(yxml_output)

    # Every modeled field should roundtrip identically
    for field_name in PIDEInteractiveOptions.model_fields:
        v1 = getattr(opts, field_name)
        v2 = getattr(opts2, field_name)
        assert v1 == v2, f"field {field_name!r} roundtrip mismatch: {v1!r} != {v2!r}"


@pytest.mark.integration
@requires_isabelle
def test_isabelle_dump_contains_known_options() -> None:
    """The ``isabelle options -x`` output contains expected option names.

    Verifies the raw YXML input actually has our modeled fields, proving
    from_yxml has data to parse (vs. returning defaults for everything).
    """
    yxml = _dump_options_yxml()
    assert len(yxml) > 0

    # Spot-check that known option names appear in the raw YXML output
    for name in (
        "threads",
        "editor_output_state",
        "show_types",
        "goals_limit",
        "completion_limit",
        "ML_print_depth",
    ):
        assert name in yxml, f"expected option {name!r} in Isabelle options dump"
