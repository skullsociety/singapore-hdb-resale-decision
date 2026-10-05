"""Focused tests for Phase 4 ranking and snapshot history."""

import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path


PATH = Path(__file__).with_name("20_22_build_market_watch.py")
SPEC = importlib.util.spec_from_file_location("market_watch", PATH)
MARKET_WATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MARKET_WATCH)


class MarketWatchTests(unittest.TestCase):
    def test_profile_rejects_invalid_limits(self):
        with self.assertRaises(ValueError):
            MARKET_WATCH.validate_profile({"maximum_price_sgd": -1})

    def test_rank_marks_supported_discount_as_investigate(self):
        now = datetime(2026, 10, 2, tzinfo=timezone.utc)
        row = {
            "asking_premium_discount_pct": -4.0, "valuation_status": "estimated",
            "minimum_comparable_count": 5, "confidence_label": "scenario_range",
            "last_seen_utc": "2026-10-02T08:00:00+00:00", "asking_price_sgd": 600000,
            "floor_area_sqm": 90.0, "inferred_flat_type": "4 ROOM",
        }
        result = MARKET_WATCH.rank_listing(row, MARKET_WATCH.validate_profile({}), now)
        self.assertTrue(result["eligible"])
        self.assertEqual("investigate", result["opportunity_label"])

    def test_history_detects_price_reduction(self):
        rows = [
            {"source_site": "propertyguru", "run_id": "one", "listing_id": "x", "listing_url": "u", "asking_price_sgd": 700000, "last_seen_utc": "2026-10-01T01:00:00+00:00"},
            {"source_site": "propertyguru", "run_id": "two", "listing_id": "x", "listing_url": "u", "asking_price_sgd": 680000, "last_seen_utc": "2026-10-02T01:00:00+00:00"},
        ]
        history, summaries = MARKET_WATCH.snapshot_history(rows)
        self.assertEqual("new", history[0]["change_type"])
        self.assertEqual("price_reduced", history[1]["change_type"])
        self.assertEqual(-20000, history[1]["price_change_sgd"])
        self.assertEqual(2, len(summaries))


if __name__ == "__main__":
    unittest.main()
