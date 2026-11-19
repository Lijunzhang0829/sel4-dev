"""Helper use-site observations aggregate into used/citation counts."""
import unittest
from pathlib import Path
import tempfile
from types import SimpleNamespace

from regen.infrastructure.experiment_activity import helper_usage
from regen.planning.helper_use_sites import record_observations


class HelperUsageTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(helper_usage([]), {
            "used_helpers": 0,
            "helper_citations": 0,
            "cited_helper_ids": [],
        })

    def test_counts_distinct_helper_consumer_edges(self):
        rows = [
            {"kind": "helper_use_observed", "data": {"helper_id": "h1", "consumer_id": "c1"}},
            {"kind": "helper_use_observed", "data": {"helper_id": "h1", "consumer_id": "c2"}},
            {"kind": "helper_use_observed", "data": {"helper_id": "h2", "consumer_id": "c1"}},
            {"kind": "helper_use_observed", "data": {"helper_id": "h1", "consumer_id": "c1"}},  # duplicate
            {"kind": "other", "data": {"helper_id": "h3", "consumer_id": "c3"}},
        ]
        result = helper_usage(rows)
        self.assertEqual(result["used_helpers"], 2)
        self.assertEqual(result["helper_citations"], 3)
        self.assertEqual(result["cited_helper_ids"], ["h1", "h2"])


    def test_record_observations_appends_to_run_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            document = run_dir / "theories" / "T" / "document"
            document.mkdir(parents=True)
            store = SimpleNamespace(root=document, state={"graph": {"run_id": "run-1"}})
            uses = [
                {"helper_id": "h1", "consumer_line": 7, "fact": "h1"},
                {"helper_id": "h1", "consumer_line": 9, "fact": "h1"},
                {"helper_id": "h2", "consumer_line": 11, "fact": "h2"},
            ]
            record_observations(store, uses)
            from regen.infrastructure.experiment_ledger import Ledger
            rows = Ledger(run_dir / "effective-generation-budget", "run-1").rows
            observed = [r for r in rows if r["kind"] == "helper_use_observed"]
            self.assertEqual(len(observed), 3)


if __name__ == "__main__":
    unittest.main()
