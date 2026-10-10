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

    def test_town_comes_from_hdb_block_even_without_resale_history(self):
        block = {"bldg_contract_town": "PG"}
        self.assertEqual(analysis.resolve_block_town(block, analysis.Counter()), "PUNGGOL")

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
        indexed = analysis.TransactionHistory(rows)
        self.assertEqual(
            analysis.infer_flat_type(93.0, "B1", "SENGKANG", indexed),
            (flat_type, method, gap),
        )
        self.assertEqual(
            analysis.scenario_inputs(self.block, flat_type, 93.0, indexed, date(2026, 10, 1)),
            (scenarios, assumptions),
        )
        self.assertEqual(
            analysis.infer_flat_type(93.0, "OTHER", "SENGKANG", indexed),
            analysis.infer_flat_type(93.0, "OTHER", "SENGKANG", rows),
        )

    def test_duplicate_advertisements_share_only_identical_valuation_inputs(self):
        source = {
            "transaction_id": "ad-1:middle", "block_id": "B1",
            "transaction_month": date(2026, 10, 1), "town": "SENGKANG",
            "flat_type": "4 ROOM", "flat_model": "Model A",
            "floor_area_sqm": 93.0, "storey_range": "07 TO 09",
            "lease_commence_year": 2001,
        }
        self.assertEqual(analysis.valuation_input_key(source),
                         analysis.valuation_input_key({**source, "transaction_id": "ad-2:middle"}))
        self.assertNotEqual(analysis.valuation_input_key(source),
                            analysis.valuation_input_key({**source, "floor_area_sqm": 94.0}))

    def test_unchanged_listing_reuses_estimate_and_refreshes_asking_difference(self):
        feature = {
            "source_site": "propertyguru", "run_id": "new", "listing_id": "123",
            "listing_key": "propertyguru:new:123", "match_status": "matched",
            "block_id": "B1", "inferred_flat_type": "4 ROOM", "floor_area_sqm": 93.0,
            "asking_price_sgd": 520000,
        }
        previous_feature = {**feature, "run_id": "old", "asking_price_sgd": 510000}
        previous_valuation = {
            "source_site": "propertyguru", "run_id": "old", "listing_id": "123",
            "model_id": "MODEL", "valuation_month": date(2026, 10, 1),
            "valuation_status": "estimated", "point_estimate_sgd": 500000.0,
            "asking_price_sgd": 510000.0, "asking_premium_discount_sgd": 10000.0,
            "asking_premium_discount_pct": 2.0, "built_at_utc": "old",
        }
        reused = analysis.reusable_previous_valuation(
            feature, previous_feature, previous_valuation, "MODEL", date(2026, 10, 1)
        )
        self.assertEqual((reused["run_id"], reused["asking_premium_discount_sgd"],
                          reused["asking_premium_discount_pct"]), ("new", 20000.0, 4.0))
        self.assertIsNone(analysis.reusable_previous_valuation(
            {**feature, "floor_area_sqm": 94.0}, previous_feature, previous_valuation,
            "MODEL", date(2026, 10, 1),
        ))

    def test_sparse_local_sales_use_calibrated_wider_range(self):
        release = {
            "residual_offsets_sgd": {"95": [-50, 50], "97.5": [-80, 80]},
            "local_evidence": {"minimum_training_sales": 100,
                               "residual_offsets_sgd": [-120, 120]},
        }
        self.assertEqual(analysis.scenario_price_range(500, 20, 99, release),
                         (380, 620, True))
        self.assertEqual(analysis.scenario_price_range(500, 20, 100, release),
                         (450, 550, False))
        self.assertEqual(analysis.scenario_price_range(500, 4, 100, release),
                         (420, 580, False))


if __name__ == "__main__":
    unittest.main()
