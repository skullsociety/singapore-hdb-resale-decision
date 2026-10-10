"""Check that comparable sales prefer a similar lease cohort."""

import importlib.util
import unittest
from datetime import date
from pathlib import Path


script = Path(__file__).with_name("04_build_baselines.py")
spec = importlib.util.spec_from_file_location("phase1_baselines_test", script)
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)


class ComparableSelectionTests(unittest.TestCase):
    def setUp(self):
        self.target = {
            "block_id": "target", "transaction_month": date(2026, 10, 1),
            "town": "SENGKANG", "flat_type": "5 ROOM", "flat_model": "Premium Apartment",
            "floor_area_sqm": 110.0, "storey_midpoint": 8.0,
            "lease_commence_year": 2003, "latitude": 1.3931, "longitude": 103.8895,
        }
        self.sales = [
            {
                **self.target, "transaction_id": f"old-{index}", "block_id": f"old-{index}",
                "transaction_month": date(2026, 8, 1), "resale_price": 650000.0,
            }
            for index in range(6)
        ] + [
            {
                **self.target, "transaction_id": f"new-{index}", "block_id": f"new-{index}",
                "transaction_month": date(2026, 8, 1), "lease_commence_year": 2017,
                "floor_area_sqm": 112.0, "resale_price": 950000.0,
            }
            for index in range(6)
        ]

    def test_prefers_similar_lease_before_mixing_newer_flats(self):
        for history in (self.sales, self.indexed_history()):
            selected, tier = baseline.select_comparables(self.target, history)
            self.assertEqual(6, len(selected))
            self.assertTrue(all(sale["lease_commence_year"] == 2003 for sale in selected))
            self.assertEqual("nearby_similar_24m_similar_lease", tier)

    def test_sparse_lease_cohort_uses_labelled_fallback(self):
        target = {**self.target, "lease_commence_year": 1980}
        selected, tier = baseline.select_comparables(target, self.sales)
        self.assertEqual(12, len(selected))
        self.assertEqual("nearby_similar_24m_lease_relaxed", tier)

    def indexed_history(self):
        history = baseline.ComparableHistory()
        history.add(self.sales)
        return history


if __name__ == "__main__":
    unittest.main()
