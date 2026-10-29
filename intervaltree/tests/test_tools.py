"""Small integrity tests for the preparation tooling, not proof substitutes."""
import importlib.util
from pathlib import Path
import re
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("intervaltree_audit", ROOT / "tools/audit.py")
audit = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(audit)
build_spec = importlib.util.spec_from_file_location("intervaltree_build", ROOT / "tools/build.py")
build = importlib.util.module_from_spec(build_spec)
assert build_spec.loader is not None
build_spec.loader.exec_module(build)


class AuditMaskingTests(unittest.TestCase):
    def test_nested_comments_and_prose_do_not_hide_later_command(self):
        source = '(* outer (* sorry *) comment *)\ntext \\<open>axioms\\<close>\nlemma x: "True"\nsorry\n'
        masked = audit.strip_prose(source)
        self.assertEqual(source.count("\n"), masked.count("\n"))
        self.assertEqual(re.findall(r"\b(sorry|axioms)\b", masked), ["sorry"])

    def test_quoted_escapes_and_unicode_cartouches(self):
        source = 'text ‹unproved sorry ‹nested axioms››\nlemma x: "P \\\"sorry\\\"" by simp\n'
        masked = audit.strip_prose(source)
        self.assertNotRegex(masked, r"\b(sorry|axioms)\b")
        self.assertIn("by simp", masked)

    def test_real_axiomatization_is_retained(self):
        self.assertRegex(audit.strip_prose('axiomatization bad where bad: "False"'),
                         r"\baxiomatization\b")


class SourceClosureTests(unittest.TestCase):
    def test_sanitized_theory_shadows_reference_proofs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"
            (root / "theories").mkdir(parents=True)
            clean = root / "sanitized"
            clean.mkdir()
            (root / "theories/Interval_Model.thy").write_text("theory Interval_Model imports Main begin lemma secret: True by simp end")
            (clean / "Interval_Model.thy").write_text("theory Interval_Model imports Main begin end")
            (clean / "Specs.thy").write_text("theory Specs imports Interval_Model begin end")
            (clean / "ROOT").write_text("session Clean = HOL + theories Specs")
            old = build.PROJECT
            try:
                build.PROJECT = root
                paths = build.source_hashes(clean)
            finally:
                build.PROJECT = old
            self.assertTrue(paths)
            self.assertTrue(all(Path(p).is_relative_to(clean) for p in paths))

    def test_external_probe_binds_its_project_dependency(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"
            (root / "theories").mkdir(parents=True)
            probe = Path(temporary) / "probe"
            probe.mkdir()
            reference = root / "theories/Interval_Model.thy"
            reference.write_text("theory Interval_Model imports Main begin end")
            (probe / "Probe.thy").write_text("theory Probe imports Interval_Model begin end")
            (probe / "ROOT").write_text("session Probe = HOL + theories Probe")
            old = build.PROJECT
            try:
                build.PROJECT = root
                paths = build.source_hashes(probe)
            finally:
                build.PROJECT = old
            self.assertIn(str(reference.resolve()), paths)


if __name__ == "__main__":
    unittest.main()
