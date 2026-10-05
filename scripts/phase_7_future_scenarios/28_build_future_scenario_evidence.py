"""Build historical evidence and backtests for the Phase 7 scenario explorer."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd


HORIZONS = (1, 3, 5)
TEST_CUTOFF = pd.Timestamp("2024-01-01")
LEASE_NUMERIC_CONTROLS = [
    "remaining_lease_years", "floor_area_sqm", "storey_midpoint", "latitude",
    "longitude", "nearest_mrt_lrt_stations_m", "nearest_primary_schools_m",
    "nearest_parks_m",
]
LEASE_FIXED_EFFECTS = ["flat_type", "flat_model", "sale_period"]
MAX_FIXED_EFFECT_ITERATIONS = 50
FIXED_EFFECT_TOLERANCE = 1e-10


def absorb_fixed_effects(
    frame: pd.DataFrame,
    value_columns: list[str],
    group_columns: list[str],
) -> pd.DataFrame:
    """Remove several categorical group means by alternating demeaning."""
    residuals = frame.copy()
    for _ in range(MAX_FIXED_EFFECT_ITERATIONS):
        previous = residuals[value_columns].to_numpy(copy=True)
        for group in group_columns:
            group_means = residuals.groupby(group)[value_columns].transform("mean")
            residuals[value_columns] = residuals[value_columns] - group_means
        largest_change = np.max(np.abs(residuals[value_columns].to_numpy() - previous))
        if float(largest_change) < FIXED_EFFECT_TOLERANCE:
            break
    return residuals


def estimate_lease_association(transactions: pd.DataFrame) -> dict:
    """Estimate the price association of one fewer lease year, controlling observed factors."""
    required = [
        "resale_price", "floor_area_sqm", "storey_midpoint", "remaining_lease_months",
        "flat_type", "flat_model", "transaction_month", "latitude", "longitude",
        "nearest_mrt_lrt_stations_m", "nearest_primary_schools_m", "nearest_parks_m",
    ]
    frame = transactions[required].dropna().copy()
    frame = frame[(frame.resale_price > 0) & (frame.remaining_lease_months > 0)]
    frame["remaining_lease_years"] = frame["remaining_lease_months"] / 12.0
    transaction_dates = pd.to_datetime(frame["transaction_month"])
    frame["sale_period"] = transaction_dates.dt.to_period("Q").astype(str)
    working = frame[LEASE_FIXED_EFFECTS].copy()
    working["log_price"] = np.log(frame["resale_price"])
    for column in LEASE_NUMERIC_CONTROLS:
        working[column] = frame[column].astype(float)

    # Alternating group demeaning absorbs several categorical fixed effects without
    # building a large one-hot matrix or relying on compiled SciPy extensions.
    value_columns = ["log_price", *LEASE_NUMERIC_CONTROLS]
    residuals = absorb_fixed_effects(working, value_columns, LEASE_FIXED_EFFECTS)
    design = residuals[LEASE_NUMERIC_CONTROLS].to_numpy()
    target = residuals["log_price"].to_numpy()
    coefficients = np.linalg.lstsq(design, target, rcond=None)[0]
    coefficient = float(
        coefficients[LEASE_NUMERIC_CONTROLS.index("remaining_lease_years")]
    )
    annual_effect = math.exp(-coefficient) - 1.0
    if not -0.05 <= annual_effect <= 0:
        raise ValueError(
            "Lease association failed the direction/range sanity gate: "
            f"{annual_effect:.3%} for one fewer year"
        )
    return {
        "method": (
            "Log-price regression with quarter and flat type/model fixed effects "
            "plus area, storey, coordinates and amenity controls"
        ),
        "analysed_rows": int(len(frame)),
        "log_price_coefficient_per_remaining_year": coefficient,
        "estimated_price_change_for_one_fewer_lease_year": annual_effect,
        "interpretation": (
            "Observed association, not a statutory depreciation schedule or proof of causation. "
            "One fewer remaining lease year is applied as a separate annual scenario component."
        ),
    }


def build_pairs(connection: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return connection.execute("""
        WITH horizons(horizon_years) AS (VALUES (1), (3), (5)),
        candidates AS (
            SELECT b.transaction_id AS base_id, f.transaction_id AS future_id,
                   h.horizon_years, b.transaction_month AS base_month,
                   f.transaction_month AS future_month,
                   b.resale_price AS base_price, f.resale_price AS future_price,
                   ABS(date_diff('month', b.transaction_month, f.transaction_month)
                       - h.horizon_years * 12) AS month_gap,
                   ABS(b.floor_area_sqm - f.floor_area_sqm) AS area_gap,
                   ABS(b.storey_midpoint - f.storey_midpoint) AS storey_gap
            FROM explorer_transactions b
            JOIN explorer_transactions f
              ON f.block_id = b.block_id
             AND f.flat_type = b.flat_type
             AND f.flat_model = b.flat_model
             AND f.transaction_month > b.transaction_month
             AND ABS(f.floor_area_sqm - b.floor_area_sqm) <= 5
             AND ABS(f.storey_midpoint - b.storey_midpoint) <= 3
            CROSS JOIN horizons h
            WHERE ABS(date_diff('month', b.transaction_month, f.transaction_month)
                      - h.horizon_years * 12) <= 3
              AND b.resale_price > 0 AND f.resale_price > 0
        )
        SELECT * EXCLUDE (month_gap, area_gap, storey_gap)
        FROM candidates
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY base_id, horizon_years
            ORDER BY month_gap, area_gap, storey_gap, future_month, future_id
        ) = 1
    """).fetchdf()


def calculate_backtests(pairs: pd.DataFrame) -> list[dict]:
    results: list[dict] = []
    for horizon in HORIZONS:
        group = pairs[pairs.horizon_years == horizon].copy()
        group["log_change"] = np.log(group.future_price / group.base_price)
        calibration = group[pd.to_datetime(group.future_month) < TEST_CUTOFF]
        test = group[pd.to_datetime(group.future_month) >= TEST_CUTOFF]
        if len(calibration) < 30 or test.empty:
            raise ValueError(f"Insufficient {horizon}-year comparable pairs for a dated backtest")
        median_change = float(calibration.log_change.median())
        residuals = calibration.log_change - median_change
        lower_residual = float(residuals.quantile(0.10))
        upper_residual = float(residuals.quantile(0.90))
        predicted = test.base_price * math.exp(median_change)
        lower = predicted * math.exp(lower_residual)
        upper = predicted * math.exp(upper_residual)
        errors = test.future_price - predicted
        results.append({
            "horizon_years": horizon,
            "calibration_pair_count": int(len(calibration)),
            "test_pair_count": int(len(test)),
            "calibration_median_total_change": math.exp(median_change) - 1.0,
            "calibration_annualized_median_change": math.exp(median_change / horizon) - 1.0,
            "lower_log_residual": lower_residual,
            "upper_log_residual": upper_residual,
            "test_mae_sgd": float(np.abs(errors).mean()),
            "test_mape": float((np.abs(errors) / test.future_price).mean()),
            "test_range_coverage": float(((test.future_price >= lower) & (test.future_price <= upper)).mean()),
            "mean_test_range_width_sgd": float((upper - lower).mean()),
            "calibration_end_before": str(TEST_CUTOFF.date()),
        })
    return results


def build(project_root: Path) -> dict:
    database = project_root / "data" / "listings.db"
    if not database.is_file():
        raise ValueError(f"Dashboard database not found: {database}")
    built_at = datetime.now(timezone.utc).isoformat()
    with duckdb.connect(str(database)) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()}
        if "explorer_transactions" not in tables:
            raise ValueError("Run Step 26 before building Phase 7 evidence")
        training = connection.execute(
            "SELECT * FROM explorer_transactions WHERE dataset_split = 'train'"
        ).fetchdf()
        lease = estimate_lease_association(training)
        pairs = build_pairs(connection)
        backtests = calculate_backtests(pairs)

        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute("DROP VIEW IF EXISTS dashboard_future_scenario_evidence")
            connection.execute("DROP VIEW IF EXISTS dashboard_future_scenario_backtests")
            connection.execute("DROP TABLE IF EXISTS future_scenario_lease_evidence")
            connection.execute("DROP TABLE IF EXISTS future_scenario_backtest_metrics")
            connection.execute("""
                CREATE TABLE future_scenario_lease_evidence (
                    built_at_utc VARCHAR, method VARCHAR, analysed_rows BIGINT,
                    log_price_coefficient_per_remaining_year DOUBLE,
                    annual_effect_one_fewer_lease_year DOUBLE,
                    interpretation VARCHAR)
            """)
            connection.execute(
                "INSERT INTO future_scenario_lease_evidence VALUES (?,?,?,?,?,?)",
                [built_at, lease["method"], lease["analysed_rows"],
                 lease["log_price_coefficient_per_remaining_year"],
                 lease["estimated_price_change_for_one_fewer_lease_year"],
                 lease["interpretation"]],
            )
            connection.execute("""
                CREATE TABLE future_scenario_backtest_metrics (
                    built_at_utc VARCHAR, horizon_years INTEGER,
                    calibration_pair_count BIGINT, test_pair_count BIGINT,
                    calibration_median_total_change DOUBLE,
                    calibration_annualized_median_change DOUBLE,
                    lower_log_residual DOUBLE, upper_log_residual DOUBLE,
                    test_mae_sgd DOUBLE, test_mape DOUBLE,
                    test_range_coverage DOUBLE, mean_test_range_width_sgd DOUBLE,
                    calibration_end_before DATE)
            """)
            connection.executemany(
                "INSERT INTO future_scenario_backtest_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [[built_at, row["horizon_years"], row["calibration_pair_count"],
                  row["test_pair_count"], row["calibration_median_total_change"],
                  row["calibration_annualized_median_change"], row["lower_log_residual"],
                  row["upper_log_residual"], row["test_mae_sgd"], row["test_mape"],
                  row["test_range_coverage"], row["mean_test_range_width_sgd"],
                  row["calibration_end_before"]] for row in backtests],
            )
            connection.execute(
                "CREATE VIEW dashboard_future_scenario_evidence AS SELECT * FROM future_scenario_lease_evidence"
            )
            connection.execute(
                "CREATE VIEW dashboard_future_scenario_backtests AS SELECT * FROM future_scenario_backtest_metrics"
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise

    summary = {
        "status": "success", "built_at_utc": built_at, "database": str(database),
        "lease_evidence": lease, "backtests": backtests, "comparable_pair_count": int(len(pairs)),
    }
    report = project_root / "reports" / "phase_7_future_scenarios" / "28_future_scenario_evidence.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    summary["summary_report"] = str(report)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.project_root.resolve()), indent=2))
    except (ValueError, duckdb.Error) as error:
        raise SystemExit(f"Phase 7 evidence build failed: {error}") from error


if __name__ == "__main__":
    main()
