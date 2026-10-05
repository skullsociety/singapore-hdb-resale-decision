"""Focused tests for Phase 4 matching and inference rules."""

import importlib.util
import unittest
from datetime import date
from pathlib import Path


path = Path(__file__).with_name("17_19_build_listing_analysis.py")
spec = importlib.util.spec_from_file_location("listing_analysis", path)
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


class ListingAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.block = {
            "block_id": "B1", "block": "327B", "street": "ANCHORVALE RD",
            "max_floor_lvl": 18, "year_completed": 2001, "town": "SENGKANG",
        }

    def test_normalises_common_street_abbreviations(self):
        self.assertEqual(analysis.normalise("Anchorvale Rd"), "ANCHORVALE ROAD")
        self.assertEqual(analysis.normalise("Sengkang Nth Ave"), "SENGKANG NORTH AVENUE")

    def test_exact_and_unique_block_matching_are_distinct(self):
        exact = {("327B", "ANCHORVALE ROAD"): [self.block]}
        by_block = {"327B": [self.block]}
        exact_match = analysis.match_listing(
            {"block": "327B", "street": "Anchorvale Road"}, exact, by_block
        )
        self.assertEqual(exact_match["match_method"], "exact_normalised_block_street")
        fallback = analysis.match_listing(
            {"block": "327B", "street": "Wrong Street"}, exact, by_block
        )
        self.assertEqual(fallback["match_method"], "unique_block_number")
        self.assertEqual(fallback["match_confidence"], "medium")

    def test_infers_flat_type_and_observed_storey_scenarios(self):
        rows = [
            {"block_id": "B1", "town": "SENGKANG", "flat_type": "4 ROOM", "floor_area_sqm": 92.0,
             "flat_model": "Model A", "lease_commence_year": 2001,
             "transaction_month": date(2024, 1, 1), "storey_range": "01 TO 03", "storey_midpoint": 2.0},
            {"block_id": "B1", "town": "SENGKANG", "flat_type": "4 ROOM", "floor_area_sqm": 93.0,
             "flat_model": "Model A", "lease_commence_year": 2001,
             "transaction_month": date(2024, 2, 1), "storey_range": "07 TO 09", "storey_midpoint": 8.0},
            {"block_id": "B1", "town": "SENGKANG", "flat_type": "4 ROOM", "floor_area_sqm": 94.0,
             "flat_model": "Model A", "lease_commence_year": 2001,
             "transaction_month": date(2024, 3, 1), "storey_range": "16 TO 18", "storey_midpoint": 17.0},
            {"block_id": "B1", "town": "SENGKANG", "flat_type": "5 ROOM", "floor_area_sqm": 120.0,
             "flat_model": "Improved", "lease_commence_year": 2001,
             "transaction_month": date(2024, 3, 1), "storey_range": "04 TO 06", "storey_midpoint": 5.0},
        ]
        flat_type, method, gap = analysis.infer_flat_type(93.0, "B1", "SENGKANG", rows)
        self.assertEqual((flat_type, method, gap), ("4 ROOM", "same_block_area", 0.0))
        scenarios, assumptions = analysis.scenario_inputs(
            self.block, flat_type, 93.0, rows, date(2026, 10, 1)
        )
        self.assertEqual([row["storey_range"] for row in scenarios],
                         ["01 TO 03", "07 TO 09", "16 TO 18"])
        self.assertEqual(assumptions["flat_model"], "Model A")


if __name__ == "__main__":
    unittest.main()
