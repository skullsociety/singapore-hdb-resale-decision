"""Database checks for the Phase 5 presentation views."""

import argparse
import unittest
from pathlib import Path

import duckdb


EXPECTED_VIEWS = {
    "dashboard_market_overview", "dashboard_current_listings",
    "dashboard_listing_details", "dashboard_listing_changes",
    "dashboard_comparables", "dashboard_data_quality",
    "dashboard_model_performance",
}
EXPECTED_CATEGORIES = {
    "strong_candidate", "fairly_priced", "negotiation_candidate",
    "likely_expensive", "insufficient_evidence", "does_not_match",
}


class DashboardViewTests(unittest.TestCase):
    database: Path

    @classmethod
    def setUpClass(cls):
        cls.connection = duckdb.connect(str(cls.database), read_only=True)

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()

    def test_expected_views_exist(self):
        found = {row[0] for row in self.connection.execute(
            "SELECT table_name FROM information_schema.views WHERE table_name LIKE 'dashboard_%'"
        ).fetchall()}
        self.assertTrue(EXPECTED_VIEWS <= found)

    def test_current_view_has_one_row_per_ranking(self):
        ranking_count = self.connection.execute("SELECT COUNT(*) FROM market_watch_rankings").fetchone()[0]
        dashboard_count = self.connection.execute("SELECT COUNT(*) FROM dashboard_current_listings").fetchone()[0]
        self.assertEqual(ranking_count, dashboard_count)
        duplicate_count = self.connection.execute("""
            SELECT COUNT(*) FROM (
                SELECT profile_id, source_site, run_id, listing_id, COUNT(*) n
                FROM dashboard_current_listings GROUP BY 1,2,3,4 HAVING n > 1)
        """).fetchone()[0]
        self.assertEqual(0, duplicate_count)

    def test_categories_are_supported(self):
        categories = {row[0] for row in self.connection.execute(
            "SELECT DISTINCT stakeholder_category FROM dashboard_current_listings"
        ).fetchall()}
        self.assertTrue(categories <= EXPECTED_CATEGORIES)
        unsupported_positive = self.connection.execute("""
            SELECT COUNT(*) FROM dashboard_current_listings
            WHERE stakeholder_category IN ('strong_candidate', 'fairly_priced',
                  'negotiation_candidate', 'likely_expensive')
              AND (valuation_status <> 'estimated' OR minimum_comparable_count < 3)
        """).fetchone()[0]
        self.assertEqual(0, unsupported_positive)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    args, remaining = parser.parse_known_args()
    DashboardViewTests.database = args.database.resolve()
    unittest.main(argv=[__file__, *remaining])


if __name__ == "__main__":
    main()
