from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from regen.refine_campaign import sha256_json
from regen.refine_m3_premises import (
    create_database,
    dump_theory_text,
    initial_support_owner_names,
    parse_fact_records,
    published_identity,
    read_database_records,
    verify_records_snapshot,
)


def enc(value: str) -> str:
    return value.encode().hex()


class RefineM3PremisesTest(unittest.TestCase):
    def test_dump_uses_pure_ml_hex_encoding(self):
        theory = dump_theory_text()
        self.assertIn("String.translate hex_char text", theory)
        self.assertNotIn("Base64.encode", theory)
        self.assertIn("TextIO.output", theory)
        self.assertNotIn("maps fact_lines", theory)
        self.assertIn("Config.put show_markup false ctxt", theory)
        self.assertIn("Print_Mode.setmp []", theory)

    def test_support_validation_names_publish_under_refine_identity(self):
        self.assertEqual(
            published_identity(
                "Corres.corres_underlying_def", "Corres", {"Corres"},
            ),
            (
                "Corres.corres_underlying_def",
                "M2_Frozen_Support.Corres",
                "frozen_support",
            ),
        )

    def test_support_owner_names_come_from_certified_eligible_plan(self):
        manifest = {
            "initial_support_plan": {
                "eligible_theories": [{"theory": "Refine.Corres"}]
            }
        }
        self.assertEqual(initial_support_owner_names(manifest), {"Corres"})

    def test_fact_records_attach_effective_automation_attributes(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = Path(temp)
            proposition = "Trueprop P"
            (root / "facts.tsv").write_text(
                "\t".join([
                    "FACT", enc("Lib.demo"), "0", enc("Lib.Demo"), enc(proposition),
                    enc("Lib.demo"), enc("Lib.demo"),
                ]) + "\n"
            )
            (root / "pools.tsv").write_text(
                "\t".join([
                    "POOL", "simp", enc("Lib.Demo"), enc(proposition),
                    enc("Lib.demo"), enc("Lib.demo"),
                ]) + "\n"
            )
            records = parse_fact_records(
                root / "facts.tsv", root / "pools.tsv",
                campaign_id="campaign", m2_gate_sha256="a" * 64,
            )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["attributes"], ["simp"])
        self.assertEqual(records[0]["source_class"], "frozen_parent")
        self.assertEqual(len(records[0]["normalized_statement_sha256"]), 64)

    def make_record(self, root: Path) -> list[dict]:
        proposition = "Trueprop P"
        (root / "facts.tsv").write_text(
            "\t".join([
                "FACT", enc("Lib.demo"), "0", enc("Lib.Demo"), enc(proposition),
                enc("Lib.demo"), enc("Lib.demo"),
            ]) + "\n",
            encoding="utf-8",
        )
        (root / "pools.tsv").write_text(
            "\t".join([
                "POOL", "simp", enc("Lib.Demo"), enc(proposition),
                enc("Lib.demo"), enc("Lib.demo"),
            ]) + "\n",
            encoding="utf-8",
        )
        return parse_fact_records(
            root / "facts.tsv",
            root / "pools.tsv",
            campaign_id="campaign",
            m2_gate_sha256="a" * 64,
        )

    def test_automation_attributes_use_theorem_identity_not_proposition(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = Path(temp)
            proposition = "Trueprop P"
            facts = [
                ["FACT", enc("Lib.first"), "0", enc("Lib.Demo"), enc(proposition),
                 enc("Lib.first"), enc("Lib.first")],
                ["FACT", enc("Lib.second"), "0", enc("Lib.Demo"), enc(proposition),
                 enc("Lib.second"), enc("Lib.second")],
            ]
            pool = [
                "POOL", "simp", enc("Lib.Demo"), enc(proposition),
                enc("Lib.first"), enc("Lib.first"),
            ]
            (root / "facts.tsv").write_text(
                "\n".join("\t".join(item) for item in facts) + "\n",
                encoding="utf-8",
            )
            (root / "pools.tsv").write_text(
                "\t".join(pool) + "\n", encoding="utf-8",
            )
            records = parse_fact_records(
                root / "facts.tsv", root / "pools.tsv",
                campaign_id="campaign", m2_gate_sha256="a" * 64,
            )
            by_name = {item["canonical_name"]: item for item in records}
            self.assertEqual(by_name["Lib.first"]["attributes"], ["simp"])
            self.assertEqual(by_name["Lib.second"]["attributes"], [])

    def test_database_round_trip_matches_reconstructed_records(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = Path(temp)
            records = self.make_record(root)
            database = root / "premises.sqlite3"
            create_database(database, records)
            self.assertEqual(read_database_records(database), records)

    def test_snapshot_id_must_derive_from_records_payload(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            records = self.make_record(Path(temp))
            logical_hash = sha256_json(records)
            snapshot = {
                "records_payload_sha256": logical_hash,
                "snapshot_id": f"m3-{logical_hash[:20]}",
                "record_count": len(records),
                "source_class_counts": {
                    "frozen_parent": 1,
                    "frozen_support": 0,
                },
            }
            self.assertEqual(verify_records_snapshot(snapshot, records)[0], logical_hash)
            snapshot["snapshot_id"] = "m3-forged"
            with self.assertRaisesRegex(ValueError, "snapshot ID"):
                verify_records_snapshot(snapshot, records)

    def test_snapshot_rejects_rehashed_metadata_without_records_hash(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            records = self.make_record(Path(temp))
            snapshot = {
                "records_payload_sha256": "0" * 64,
                "snapshot_id": "m3-" + "0" * 20,
                "record_count": 1,
                "source_class_counts": {"frozen_parent": 1, "frozen_support": 0},
            }
            with self.assertRaisesRegex(ValueError, "records payload"):
                verify_records_snapshot(snapshot, records)


if __name__ == "__main__":
    unittest.main()
