"""Integrity checks for the Phase 6 explorer data layer."""

import argparse
import unittest
from pathlib import Path

import duckdb


EXPECTED_VIEWS = {
    "dashboard_explorer_transactions", "dashboard_explorer_monthly_trends",
    "dashboard_explorer_blocks", "dashboard_feature_associations",
}


class FeatureExplorerDataTests(unittest.TestCase):
    database: Path

    @classmethod
    def setUpClass(cls):
        cls.connection = duckdb.connect(str(cls.database), read_only=True)

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()

    def test_expected_views_exist(self):
        views = {row[0] for row in self.connection.execute(
            "SELECT table_name FROM information_schema.views WHERE table_name LIKE 'dashboard_%'"
        ).fetchall()}
        self.assertTrue(EXPECTED_VIEWS <= views)

    def test_transactions_are_unique_and_valid(self):
        total, unique, invalid = self.connection.execute("""
            SELECT COUNT(*), COUNT(DISTINCT transaction_id),
                   COUNT(*) FILTER (
                       WHERE resale_price <= 0 OR floor_area_sqm <= 0
                          OR latitude IS NULL OR longitude IS NULL)
            FROM dashboard_explorer_transactions
        """).fetchone()
        self.assertGreater(total, 0)
        self.assertEqual(total, unique)
        self.assertEqual(0, invalid)

    def test_associations_use_training_rows_only(self):
        analysed_counts = {row[0] for row in self.connection.execute(
            "SELECT DISTINCT analysed_rows FROM dashboard_feature_associations"
        ).fetchall()}
        training_count = self.connection.execute(
            "SELECT COUNT(*) FROM explorer_transactions WHERE dataset_split = 'train'"
        ).fetchone()[0]
        self.assertEqual({training_count}, analysed_counts)

    def test_block_summary_does_not_duplicate_group_keys(self):
        duplicates = self.connection.execute("""
            SELECT COUNT(*) FROM (
                SELECT block_id, town, flat_type, COUNT(*) AS n
                FROM dashboard_explorer_blocks GROUP BY 1,2,3 HAVING n > 1)
        """).fetchone()[0]
        self.assertEqual(0, duplicates)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    args, remaining = parser.parse_known_args()
    FeatureExplorerDataTests.database = args.database.resolve()
    unittest.main(argv=[__file__, *remaining])


if __name__ == "__main__":
    main()
