"""Calibrate and backtest the user-accepted Ridge/comparable pilot estimate."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np


MODEL_ID = "PILOT_RIDGE_COMPARABLE_BLEND_V1"
COMPARABLE_ID = "BASELINE_COMPARABLE_SALES_V1"
RIDGE_ID = "ML_RIDGE_RESALE_PRICE_V1"


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load {path}")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def csv_rows(path: Path) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(f"Missing required workflow output: {path}")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def bounds(residuals: list[float], level: float, method: str, diagnostics) -> tuple[float, float]:
    if method == "symmetric_absolute_residual":
        radius = diagnostics.finite_quantile([abs(value) for value in residuals], level)
        return -radius, radius
    if method == "asymmetric_residual":
        return diagnostics.residual_bounds(residuals, level)
    raise ValueError(f"Unknown calibration method: {method}")


def interval(point: float, residuals: list[float], level: float, method: str, diagnostics) -> tuple[float, float]:
    low_offset, high_offset = bounds(residuals, level, method, diagnostics)
    return round(max(1.0, min(point, point + low_offset)), 2), round(max(point, point + high_offset), 2)


def group_metrics(predictions: list[dict], train_rows: list[dict], lookup: dict[str, dict], models) -> list[dict]:
    prices = [float(row["resale_price"]) for row in train_rows]
    cutoffs = tuple(float(np.quantile(prices, q)) for q in (.25, .5, .75))
    output = models.error_analysis(predictions, lookup, cutoffs)
    for entry in output:
        entry["minimum_group_size"] = 30
        entry["interpretation"] = "too_few_to_judge" if entry["prediction_count"] < 30 else "review"
    return output


def run(root: Path) -> dict:
    scripts = root / "scripts/phase_1_resale_model"
    models = module(scripts / "06_train_resale_models.py", "resale_models_09")
    diagnostics = module(scripts / "07_phase1_diagnostics.py", "diagnostics_09")
    selection = json.loads((root / "reports/phase_1_resale_model/08_hybrid_selection.json").read_text(encoding="utf-8"))
    ridge_weight = float(selection["ridge_weight"])
    if not 0 <= ridge_weight <= 1:
        raise ValueError("Invalid saved blend weight")
    rows = models.read_transactions(root / "data/property.duckdb")
    by_id = {row["transaction_id"]: row for row in rows}
    train = [row for row in rows if row["dataset_split"] == "train"]
    validation = [row for row in rows if row["dataset_split"] == "validation"]
    test = [row for row in rows if row["dataset_split"] == "test"]
    saved = csv_rows(root / "data/model_ready/08_hybrid_predictions.csv")
    blend = {
        (row["period"], row["transaction_id"]): row
        for row in saved if row["candidate"] == "ridge_comparable_blend" and row["period"] in {"validation", "test"}
    }
    expected = {(split, row["transaction_id"]) for split, records in (("validation", validation), ("test", test)) for row in records}
    if set(blend) != expected:
        raise ValueError("Blend predictions do not match the current validation/test transaction table")
    for (split, transaction_id), prediction in blend.items():
        if abs(float(prediction["actual_price"]) - float(by_id[transaction_id]["resale_price"])) > .01:
            raise ValueError(f"Source data changed since step 08 for {transaction_id}; rerun steps 04–09")
    baseline_rows = csv_rows(root / "data/model_ready/baseline_predictions.csv")
    comparable = {
        (row["dataset_split"], row["transaction_id"]): row
        for row in baseline_rows if row["model_id"] == COMPARABLE_ID and row["dataset_split"] in {"validation", "test"}
    }
    if set(comparable) != expected:
        raise ValueError("Comparable predictions do not match validation/test transactions")

    validation_months = sorted({row["transaction_month"] for row in validation})
    early_months = set(validation_months[:len(validation_months) // 2])
    early = [row for row in validation if row["transaction_month"] in early_months]
    late = [row for row in validation if row["transaction_month"] not in early_months]
    early_residuals = [
        float(row["resale_price"]) - float(blend["validation", row["transaction_id"]]["predicted_price"])
        for row in early
    ]
    calibration_check = []
    for method in ("asymmetric_residual", "symmetric_absolute_residual"):
        for level in (.95, .975):
            covered = []
            widths = []
            for row in late:
                point = float(blend["validation", row["transaction_id"]]["predicted_price"])
                low, high = interval(point, early_residuals, level, method, diagnostics)
                covered.append(low <= float(row["resale_price"]) <= high)
                widths.append(high - low)
            calibration_check.append({
                "calibration_method": method, "nominal_coverage_pct": level * 100,
                "calibration_start_month": min(early_months).isoformat(),
                "calibration_end_month": max(early_months).isoformat(),
                "assessment_start_month": min(row["transaction_month"] for row in late).isoformat(),
                "assessment_end_month": max(row["transaction_month"] for row in late).isoformat(),
                "calibration_rows": len(early), "assessment_rows": len(late),
                "observed_coverage_pct": round(statistics.mean(covered) * 100, 2),
                "mean_interval_width": round(statistics.mean(widths), 2),
            })
    methods = {row["calibration_method"] for row in calibration_check}
    selected_method = min(methods, key=lambda name: (
        sum(abs(row["observed_coverage_pct"] - row["nominal_coverage_pct"])
            for row in calibration_check if row["calibration_method"] == name),
        sum(row["mean_interval_width"] for row in calibration_check if row["calibration_method"] == name),
    ))

    residuals = [
        float(row["resale_price"]) - float(blend["validation", row["transaction_id"]]["predicted_price"])
        for row in validation
    ]
    if len(residuals) < 100:
        raise ValueError("Too few validation residuals for price range calibration")
    intervals = {}
    range_rows = []
    for row in test:
        transaction_id = row["transaction_id"]
        point = float(blend["test", transaction_id]["predicted_price"])
        count = int(comparable["test", transaction_id]["comparable_count"])
        level = .975 if count < 5 else .95
        low, high = interval(point, residuals, level, selected_method, diagnostics)
        confidence = "insufficient_comparable_evidence" if count < 3 else "low_wide_range" if count < 5 else "moderate" if count < 20 else "supported_by_comparables"
        result = {
            "transaction_id": transaction_id, "model_id": MODEL_ID, "dataset_split": "test",
            "valuation_month": row["transaction_month"].isoformat(), "town": row["town"],
            "flat_type": row["flat_type"], "point_estimate": round(point, 2),
            "lower_estimate": low, "upper_estimate": high, "interval_width": round(high - low, 2),
            "nominal_coverage_pct": level * 100, "comparable_count": count,
            "comparable_tier": comparable["test", transaction_id]["comparable_tier"],
            "confidence_label": confidence, "actual_price_backtest_only": float(row["resale_price"]),
            "interval_covered": low <= float(row["resale_price"]) <= high,
        }
        intervals[transaction_id] = result
        range_rows.append(result)

    metric_inputs = []
    group_inputs = []
    for split, source in (("validation", validation), ("test", test)):
        for row in source:
            transaction_id = row["transaction_id"]
            actual = float(row["resale_price"])
            for model_id, predicted in (
                (MODEL_ID, float(blend[split, transaction_id]["predicted_price"])),
                (COMPARABLE_ID, float(comparable[split, transaction_id]["predicted_price"])),
            ):
                range_result = intervals.get(transaction_id) if model_id == MODEL_ID and split == "test" else None
                metric_inputs.append({
                    "model_id": model_id, "dataset_split": split, "transaction_id": transaction_id,
                    "predicted_price": predicted, "actual_price": actual,
                    "lower_estimate": range_result["lower_estimate"] if range_result else None,
                    "upper_estimate": range_result["upper_estimate"] if range_result else None,
                })
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in metric_inputs:
        grouped[(row["model_id"], row["dataset_split"])].append(row)
        if row["dataset_split"] == "test":
            group_inputs.append(row)
    metric_rows = [
        {"model_id": model_id, "dataset_split": split,
         **{key: round(value, 4) if isinstance(value, float) else value for key, value in models.metrics(entries).items()}}
        for (model_id, split), entries in sorted(grouped.items())
    ]
    segment_rows = group_metrics(group_inputs, train, by_id, models)
    blend_test_metric = next(row for row in metric_rows if row["model_id"] == MODEL_ID and row["dataset_split"] == "test")
    mean_nominal = statistics.mean(row["nominal_coverage_pct"] for row in range_rows)
    observed = float(blend_test_metric["interval_coverage_pct"])
    range_status = "pilot_tolerance_pass" if abs(observed - mean_nominal) <= 5 else "coverage_needs_review"
    last_source_month = max(row["transaction_month"] for row in rows)
    source_run = json.loads((root / "reports/phase_1_resale_model/03_duckdb_build_summary.json").read_text(encoding="utf-8"))
    release = {
        "status": "accepted_sengkang_pilot", "model_id": MODEL_ID,
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "ridge_weight": ridge_weight, "comparable_weight": round(1 - ridge_weight, 2),
        "ridge_artifact": "models/phase_1_resale_model/06_ridge_resale_price.joblib",
        "comparable_method": "BASELINE_COMPARABLE_SALES_V1",
        "source_build_run_id": source_run["run_id"],
        "latest_source_transaction_month": last_source_month.isoformat(),
        "calibration_period": [min(row["transaction_month"] for row in validation).isoformat(), max(row["transaction_month"] for row in validation).isoformat()],
        "calibration_rows": len(validation),
        "calibration_method": selected_method,
        "residual_offsets_sgd": {
            "95": [round(value, 2) for value in bounds(residuals, .95, selected_method, diagnostics)],
            "97.5": [round(value, 2) for value in bounds(residuals, .975, selected_method, diagnostics)],
        },
        "calibration_check": calibration_check,
        "range_status": range_status,
        "test_observed_coverage_pct": round(observed, 2),
        "test_mean_nominal_coverage_pct": round(mean_nominal, 2),
        "test_mean_interval_width_sgd": round(statistics.mean(row["interval_width"] for row in range_rows), 2),
        "test_mae_sgd": blend_test_metric["mae"],
        "test_rows": len(test),
        "limitation": "Sengkang pilot. Test period and prior model results were inspected during development. Ranges do not reflect unobserved renovation, view, exact floor, or transaction-specific negotiation.",
        "outputs": {
            "ranges": "reports/phase_1_resale_model/09_blend_price_ranges.csv",
            "metrics": "reports/phase_1_resale_model/09_blend_backtest_metrics.csv",
            "segments": "reports/phase_1_resale_model/09_blend_error_by_segment.csv",
            "calibration_check": "reports/phase_1_resale_model/09_blend_calibration_check.csv",
        },
    }
    reports = root / "reports/phase_1_resale_model"
    reports.mkdir(parents=True, exist_ok=True)
    paths = {"ranges": reports / "09_blend_price_ranges.csv",
             "metrics": reports / "09_blend_backtest_metrics.csv",
             "segments": reports / "09_blend_error_by_segment.csv",
             "calibration_check": reports / "09_blend_calibration_check.csv"}
    for key, records in (("ranges", range_rows), ("metrics", metric_rows),
                         ("segments", segment_rows), ("calibration_check", calibration_check)):
        diagnostics.write_csv(paths[key], records)
    (reports / "09_blend_release.json").write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
    with duckdb.connect(str(root / "data/property.duckdb")) as connection:
        connection.execute("BEGIN TRANSACTION")
        try:
            for table, path in (("pilot_blend_price_ranges", paths["ranges"]),
                                ("pilot_blend_backtest_metrics", paths["metrics"]),
                                ("pilot_blend_error_by_segment", paths["segments"]),
                                ("pilot_blend_calibration_check", paths["calibration_check"])):
                connection.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM read_csv_auto(?)", [str(path)])
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
    return release


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    try:
        result = run(args.project_root.resolve())
    except (ValueError, OSError, duckdb.Error) as error:
        print(f"Blend calibration failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps({key: result[key] for key in (
        "status", "model_id", "test_mae_sgd", "range_status", "calibration_method", "test_observed_coverage_pct",
        "test_mean_nominal_coverage_pct", "test_mean_interval_width_sgd", "test_rows", "outputs")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
