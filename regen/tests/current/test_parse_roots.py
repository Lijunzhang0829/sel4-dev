from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.lemma_inventory.parse_roots import parse_root_file


class ParseRootsTests(unittest.TestCase):
    def test_preserves_quoted_and_bare_list_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ROOT"
            root.write_text(
                '''session Demo in "proof/$L4V_ARCH" = HOL +
  sessions
    "HOL-Library"
    Lib
  directories
    "$L4V_ARCH"
    shared
  theories
    Plain
    "$L4V_ARCH/Arch_Theory"
''',
                encoding="utf-8",
            )
            parsed = parse_root_file(
                root, Path(tmp), {"L4V_ARCH": "ARM", "L4V_HOME": tmp, "ISABELLE_HOME": ""},
            )

        self.assertEqual(len(parsed), 1)
        session = parsed[0]
        self.assertEqual(session["imported_sessions"], ["HOL-Library", "Lib"])
        self.assertEqual(session["unconditional_theories"], ["Plain", "ARM/Arch_Theory"])
        self.assertTrue(session["session_dir"].endswith("proof/ARM"))
        self.assertTrue(session["directories"][0].endswith("proof/ARM/ARM"))
        self.assertTrue(session["directories"][1].endswith("proof/ARM/shared"))


if __name__ == "__main__":
    unittest.main()
