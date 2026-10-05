"""Tests for the Phase 7 historical evidence layer."""

import argparse
import importlib.util
import unittest
from pathlib import Path

import duckdb
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]


def load_dashboard_module():
    path = ROOT / "dashboard" / "phase_7" / "29_future_scenario_explorer.py"
    spec = importlib.util.spec_from_file_location("future_scenarios_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCENARIOS = load_dashboard_module()


class FutureScenarioEvidenceTests(unittest.TestCase):
    database: Path

    @classmethod
    def setUpClass(cls):
        cls.connection = duckdb.connect(str(cls.database), read_only=True)

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()

    def test_evidence_views_exist_and_have_expected_horizons(self):
        views = {row[0] for row in self.connection.execute(
            "SELECT table_name FROM information_schema.views"
        ).fetchall()}
        self.assertIn("dashboard_future_scenario_evidence", views)
        self.assertIn("dashboard_future_scenario_backtests", views)
        horizons = {row[0] for row in self.connection.execute(
            "SELECT horizon_years FROM dashboard_future_scenario_backtests"
        ).fetchall()}
        self.assertEqual({1, 3, 5}, horizons)

    def test_backtest_values_are_valid(self):
        invalid = self.connection.execute("""
            SELECT COUNT(*) FROM dashboard_future_scenario_backtests
            WHERE calibration_pair_count <= 0 OR test_pair_count <= 0
               OR test_mae_sgd < 0 OR test_mape < 0
               OR test_range_coverage NOT BETWEEN 0 AND 1
               OR mean_test_range_width_sgd <= 0
        """).fetchone()[0]
        self.assertEqual(0, invalid)

    def test_scenario_formula_keeps_scenario_and_range_distinct(self):
        uncertainty = pd.DataFrame({
            "horizon_years": [1, 3, 5],
            "lower_log_residual": [-0.1, -0.2, -0.3],
            "upper_log_residual": [0.1, 0.2, 0.3],
        })
        paths = SCENARIOS.scenario_paths(
            500_000, 1,
            {"Baseline": {"market_growth": 0.02}},
            -0.01, 0.0, uncertainty,
        )
        terminal = paths.iloc[-1]
        self.assertAlmostEqual(500_000 * 1.02 * 0.99, terminal["Central estimate"])
        self.assertLess(terminal["Lower range"], terminal["Central estimate"])
        self.assertGreater(terminal["Upper range"], terminal["Central estimate"])

    def test_zero_interest_payment(self):
        self.assertAlmostEqual(1_000, SCENARIOS.monthly_payment(120_000, 0, 10))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    args, remaining = parser.parse_known_args()
    FutureScenarioEvidenceTests.database = args.database.resolve()
    unittest.main(argv=[__file__, *remaining])


if __name__ == "__main__":
    main()
