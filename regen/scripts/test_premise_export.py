import tempfile
import unittest
from pathlib import Path

from regen.premise_export import export_build_options, parse_fact_records, prepare_export_heaps, semantic_theory_names


def enc(value: str) -> str:
    return value.encode().hex()


class PremiseExportTest(unittest.TestCase):
    def test_export_session_is_forced_to_rebuild(self):
        self.assertEqual(export_build_options(), ["build", "-c", "-b"])

    def test_export_heap_clone_does_not_modify_sealed_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sealed-heaps"
            source.mkdir()
            (source / "heap").write_text("sealed")
            clone = prepare_export_heaps(source, root / "export-heaps")
            (clone / "heap").write_text("export-mutated")
            self.assertEqual((source / "heap").read_text(), "sealed")

    def test_semantic_theory_names_accepts_build_result_and_bare_manifest(self):
        theories = [{"theory": "Semantic_Base.KHeap_R"}, {"theory": "Bits_R"}]
        expected = {"KHeap_R", "Bits_R"}
        self.assertEqual(semantic_theory_names({"theories": theories}), expected)
        self.assertEqual(semantic_theory_names({"workspace": {"theories": theories}}), expected)

    def test_parse_records_classifies_semantic_exports_and_automation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            facts = root / "facts.tsv"
            pools = root / "pools.tsv"
            facts.write_text("\n".join([
                "\t".join(["FACT", enc("regen_def"), "0", enc("Semantic_Base.KHeap_R"), enc("True"), enc("regen_def"), enc("Semantic_Base.KHeap_R.regen_def")]),
                "\t".join(["FACT", enc("TrueI"), "0", enc("HOL.HOL"), enc("True"), enc("TrueI"), enc("HOL.TrueI")]),
            ]) + "\n")
            pools.write_text("\t".join([
                "POOL", "simp", enc("Semantic_Base.KHeap_R"), enc("True"),
                enc("regen_def"), enc("Semantic_Base.KHeap_R.regen_def"),
            ]) + "\n")
            records = parse_fact_records(
                facts, pools, campaign_id="campaign", seal_id="seal",
                semantic_theories={"KHeap_R"},
            )
        self.assertEqual(
            [item["record_id"] for item in records],
            sorted(item["record_id"] for item in records),
        )
        by_name = {item["canonical_name"]: item for item in records}
        self.assertEqual(by_name["regen_def"]["source_class"], "retained_semantic_export")
        self.assertEqual(by_name["regen_def"]["attributes"], ["simp"])
        self.assertEqual(by_name["TrueI"]["source_class"], "frozen_parent")
        self.assertEqual(by_name["TrueI"]["attributes"], [])
        self.assertEqual(by_name["regen_def"]["provenance"]["semantic_base_seal_id"], "seal")

    def test_malformed_fact_export_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            facts = root / "facts.tsv"
            pools = root / "pools.tsv"
            facts.write_text("FACT\tbroken\n")
            pools.write_text("")
            with self.assertRaisesRegex(ValueError, "malformed fact"):
                parse_fact_records(
                    facts, pools, campaign_id="campaign", seal_id="seal",
                    semantic_theories=set(),
                )


if __name__ == "__main__":
    unittest.main()
