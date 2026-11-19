"""Baseline fresh-process final integration uses a full session build command."""
import sys
import unittest
from pathlib import Path

VERO = Path(__file__).resolve().parents[3] / "vero-isabelle" / "src"
sys.path.insert(0, str(VERO))

from vero.isabelle.checker import Checker


class BaselineBuildCommandTest(unittest.TestCase):
    def test_build_command_is_full_session_build(self):
        checker = Checker({
            "isabelle": "/workspace/verification/isabelle/bin/isabelle",
            "session_dirs": ["/sel4-project/verification/l4v", "/semantic-base"],
            "threads": 4,
            "command_prefix": ["runuser", "-u", "checker", "--"],
        })
        argv = checker.build_command("/sandbox", "Generated_Refine")
        self.assertEqual(argv, [
            "runuser", "-u", "checker", "--",
            "/workspace/verification/isabelle/bin/isabelle",
            "build", "-b", "-o", "threads=4",
            "-d", "/sel4-project/verification/l4v",
            "-d", "/semantic-base",
            "Generated_Refine",
        ])


if __name__ == "__main__":
    unittest.main()
