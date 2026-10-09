"""Release the stronger eligible price method and calibrate its range."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import numpy as np


COMPARABLE_ID = "BASELINE_COMPARABLE_SALES_V1"
MIN_RECENT_TOWN_FLAT_SALES = 100


def recent_local_training_counts(train_rows: list[dict], training_start: date) -> tuple[date, date, Counter]:
    """Count the same town/flat-type sales eligible for model fitting."""
    training_end = max(row["transaction_month"] for row in train_rows)
    counts = Counter(
        (row["town"], row["flat_type"]) for row in train_rows
        if row["transaction_month"] >= training_start
    )
    return training_start, training_end, counts


def comparable_release_gate(
    selected_id: str, selected_predictions: dict, comparable_predictions: dict,
    comparable_id: str = COMPARABLE_ID,
) -> dict:
    """A trained model must beat the same-sale comparable baseline in both later splits."""
    splits = {}
    for split in ("validation", "test"):
        keys = [key for key in selected_predictions if key[0] == split]
        if not keys or any(key not in comparable_predictions for key in keys):
            raise ValueError(f"Comparable predictions are missing for the {split} release check")
        model_mae = statistics.mean(
            abs(float(selected_predictions[key]["predicted_price"]) - float(selected_predictions[key]["actual_price"]))
            for key in keys
        )
        baseline_mae = statistics.mean(
            abs(float(comparable_predictions[key]["predicted_price"]) - float(selected_predictions[key]["actual_price"]))
            for key in keys
        )
        splits[split] = {
            "sales": len(keys), "model_mae_sgd": round(model_mae, 2),
            "comparable_mae_sgd": round(baseline_mae, 2),
            "model_beats_comparable": model_mae < baseline_mae,
        }
    return {
        "model_id": selected_id, "baseline_id": comparable_id,
        "passed": all(item["model_beats_comparable"] for item in splits.values()),
        "splits": splits,
    }


def choose_release_method(gate: dict, selected_id: str, comparable_id: str) -> tuple[str, str]:
    if gate["passed"]:
        return selected_id, (
            f"Selected ML model {selected_id} beat comparable baseline {comparable_id} on validation and test MAE."
        )
    return comparable_id, (
        f"Selected comparable baseline {comparable_id}: ML model {selected_id} did not beat it on both validation and test MAE."
    )


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
    selection = json.loads((root / "reports/phase_1_resale_model/06_model_selection.json").read_text(encoding="utf-8"))
    selected_model = selection.get("selected_model")
    if not isinstance(selected_model, dict):
        raise ValueError("Step 06 selection report is incomplete; rerun model training")
    selected_id = selected_model["model_id"]
    comparable_id = selection.get("comparable_model_id", COMPARABLE_ID)
    if selected_id not in models.MODEL_SPECS:
        raise ValueError("Step 06 selected model is not a fitted machine-learning model; rerun step 06")
    if selected_id not in selection.get("artifacts", {}) or not selection.get("feature_columns"):
        raise ValueError("Step 06 selection report is missing the model artifact or feature schema; rerun model training")
    rows = models.read_transactions(root / "data/property.duckdb")
    by_id = {row["transaction_id"]: row for row in rows}
    train = [row for row in rows if row["dataset_split"] == "train"]
    validation = [row for row in rows if row["dataset_split"] == "validation"]
    test = [row for row in rows if row["dataset_split"] == "test"]
    saved = csv_rows(root / "data/model_ready/06_ml_predictions.csv")
    selected_predictions = {
        (row["dataset_split"], row["transaction_id"]): row
        for row in saved if row["model_id"] == selected_id and row["dataset_split"] in {"validation", "test"}
    }
    expected = {(split, row["transaction_id"]) for split, records in (("validation", validation), ("test", test)) for row in records}
    if set(selected_predictions) != expected:
        raise ValueError("Selected-model predictions do not match validation/test transactions; rerun step 06")
    for (split, transaction_id), prediction in selected_predictions.items():
        if abs(float(prediction["actual_price"]) - float(by_id[transaction_id]["resale_price"])) > .01:
            raise ValueError(f"Source data changed since model training for {transaction_id}; rerun steps 04–06")
    baseline_rows = csv_rows(root / "data/model_ready/baseline_predictions.csv")
    comparable = {
        (row["dataset_split"], row["transaction_id"]): row
        for row in baseline_rows if row["model_id"] == comparable_id and row["dataset_split"] in {"validation", "test"}
    }
    if set(comparable) != expected:
        raise ValueError("Comparable predictions do not match validation/test transactions")
    for (_, transaction_id), prediction in comparable.items():
        if abs(float(prediction["actual_price"]) - float(by_id[transaction_id]["resale_price"])) > .01:
            raise ValueError(f"Comparable predictions are stale for {transaction_id}; rerun step 04")
    gate = comparable_release_gate(selected_id, selected_predictions, comparable, comparable_id)
    released_id, selection_reason = choose_release_method(gate, selected_id, comparable_id)
    use_ml = released_id == selected_id
    released_predictions = selected_predictions if use_ml else comparable
    print(selection_reason, flush=True)

    validation_months = sorted({row["transaction_month"] for row in validation})
    early_months = set(validation_months[:len(validation_months) // 2])
    early = [row for row in validation if row["transaction_month"] in early_months]
    late = [row for row in validation if row["transaction_month"] not in early_months]
    early_residuals = [
        float(row["resale_price"]) - float(released_predictions["validation", row["transaction_id"]]["predicted_price"])
        for row in early
    ]
    calibration_check = []
    for method in ("asymmetric_residual", "symmetric_absolute_residual"):
        for level in (.95, .975):
            covered = []
            widths = []
            for row in late:
                point = float(released_predictions["validation", row["transaction_id"]]["predicted_price"])
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
        float(row["resale_price"]) - float(released_predictions["validation", row["transaction_id"]]["predicted_price"])
        for row in validation
    ]
    if len(residuals) < 100:
        raise ValueError("Too few validation residuals for price range calibration")
    if not selection.get("training_start_month"):
        raise ValueError("Step 06 model report lacks the training start month; rerun Step 06")
    recent_start, training_end, local_counts = recent_local_training_counts(
        train, date.fromisoformat(selection["training_start_month"]),
    )
    def low_local_evidence(row: dict) -> bool:
        return local_counts[row["town"], row["flat_type"]] < MIN_RECENT_TOWN_FLAT_SALES

    low_validation = [row for row in validation if low_local_evidence(row)]
    if len(low_validation) < 100:
        raise ValueError("Too few low-local-evidence validation sales to calibrate a wider range")
    low_residuals = [
        float(row["resale_price"]) - float(released_predictions["validation", row["transaction_id"]]["predicted_price"])
        for row in low_validation
    ]
    low_offsets = bounds(low_residuals, .975, selected_method, diagnostics)
    # The sparse-group interval must never be narrower than the usual wide interval.
    national_wide = bounds(residuals, .975, selected_method, diagnostics)
    low_offsets = (min(low_offsets[0], national_wide[0]), max(low_offsets[1], national_wide[1]))
    early_low = [row for row in early if low_local_evidence(row)]
    late_low = [row for row in late if low_local_evidence(row)]
    if len(early_low) < 100 or len(late_low) < 100:
        raise ValueError("Too few time-separated low-local-evidence sales to check the wider range")
    early_low_residuals = [
        float(row["resale_price"]) - float(released_predictions["validation", row["transaction_id"]]["predicted_price"])
        for row in early_low
    ]
    early_low_offsets = bounds(early_low_residuals, .975, selected_method, diagnostics)
    early_national_wide = bounds(early_residuals, .975, selected_method, diagnostics)
    early_low_offsets = (min(early_low_offsets[0], early_national_wide[0]),
                         max(early_low_offsets[1], early_national_wide[1]))
    low_holdout_coverage = statistics.mean(
        max(1.0, point + early_low_offsets[0]) <= float(row["resale_price"]) <= point + early_low_offsets[1]
        for row in late_low
        for point in [float(released_predictions["validation", row["transaction_id"]]["predicted_price"])]
    ) * 100
    intervals = {}
    range_rows = []
    for row in test:
        transaction_id = row["transaction_id"]
        point = float(released_predictions["test", transaction_id]["predicted_price"])
        count = int(comparable["test", transaction_id]["comparable_count"])
        local_count = local_counts[row["town"], row["flat_type"]]
        low_local = local_count < MIN_RECENT_TOWN_FLAT_SALES
        level = .975 if low_local or count < 5 else .95
        if low_local:
            low = round(max(1.0, min(point, point + low_offsets[0])), 2)
            high = round(max(point, point + low_offsets[1]), 2)
        else:
            low, high = interval(point, residuals, level, selected_method, diagnostics)
        confidence = ("insufficient_comparable_evidence" if count < 3 else
                      "limited_local_training_data" if low_local else
                      "low_wide_range" if count < 5 else "moderate" if count < 20 else "supported_by_comparables")
        result = {
            "transaction_id": transaction_id, "model_id": released_id, "dataset_split": "test",
            "valuation_month": row["transaction_month"].isoformat(), "town": row["town"],
            "flat_type": row["flat_type"], "point_estimate": round(point, 2),
            "lower_estimate": low, "upper_estimate": high, "interval_width": round(high - low, 2),
            "nominal_coverage_pct": level * 100, "comparable_count": count,
            "recent_town_flat_training_sales": local_count,
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
                (selected_id, float(selected_predictions[split, transaction_id]["predicted_price"])),
                (comparable_id, float(comparable[split, transaction_id]["predicted_price"])),
            ):
                range_result = intervals.get(transaction_id) if model_id == released_id and split == "test" else None
                comparable_result = comparable[split, transaction_id]
                if range_result:
                    lower_estimate = range_result["lower_estimate"]
                    upper_estimate = range_result["upper_estimate"]
                elif model_id == comparable_id and not range_result:
                    lower_estimate = float(comparable_result["lower_estimate"])
                    upper_estimate = float(comparable_result["upper_estimate"])
                else:
                    lower_estimate = upper_estimate = None
                metric_inputs.append({
                    "model_id": model_id, "dataset_split": split, "transaction_id": transaction_id,
                    "predicted_price": predicted, "actual_price": actual,
                    "lower_estimate": lower_estimate, "upper_estimate": upper_estimate,
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
    released_test_metric = next(row for row in metric_rows if row["model_id"] == released_id and row["dataset_split"] == "test")
    mean_nominal = statistics.mean(row["nominal_coverage_pct"] for row in range_rows)
    observed = float(released_test_metric["interval_coverage_pct"])
    range_status = "pilot_tolerance_pass" if abs(observed - mean_nominal) <= 5 else "coverage_needs_review"
    low_test = [row for row in range_rows if row["recent_town_flat_training_sales"] < MIN_RECENT_TOWN_FLAT_SALES]
    low_test_coverage = statistics.mean(row["interval_covered"] for row in low_test) * 100 if low_test else None
    last_source_month = max(row["transaction_month"] for row in rows)
    source_run = json.loads((root / "reports/phase_1_resale_model/03_duckdb_build_summary.json").read_text(encoding="utf-8"))
    release = {
        "status": "national_candidate", "model_id": released_id,
        "release_type": "machine_learning" if use_ml else "comparable_baseline",
        "selection_reason": selection_reason,
        "baseline_gate": gate,
        "model_name": selected_model["model_name"] if use_ml else models.BASELINE_NAMES[comparable_id],
        "model_training_start_month": selection["training_start_month"],
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_artifact": selection["artifacts"][selected_id] if use_ml else None,
        "feature_columns": selection["feature_columns"] if use_ml else [],
        "comparable_method": comparable_id,
        "source_build_run_id": source_run["run_id"],
        "latest_source_transaction_month": last_source_month.isoformat(),
        "calibration_period": [min(row["transaction_month"] for row in validation).isoformat(), max(row["transaction_month"] for row in validation).isoformat()],
        "calibration_rows": len(validation),
        "calibration_method": selected_method,
        "residual_offsets_sgd": {
            "95": [round(value, 2) for value in bounds(residuals, .95, selected_method, diagnostics)],
            "97.5": [round(value, 2) for value in bounds(residuals, .975, selected_method, diagnostics)],
        },
        "local_evidence": {
            "minimum_training_sales": MIN_RECENT_TOWN_FLAT_SALES,
            "training_start_month": recent_start.isoformat(),
            "training_end_month": training_end.isoformat(),
            "nominal_coverage_pct": 97.5,
            "residual_offsets_sgd": [round(value, 2) for value in low_offsets],
            "validation_sales": len(low_validation),
            "validation_later_half_sales": len(late_low),
            "validation_later_half_coverage_pct": round(low_holdout_coverage, 2),
            "test_sales": len(low_test),
            "test_observed_coverage_pct": round(low_test_coverage, 2) if low_test_coverage is not None else None,
        },
        "calibration_check": calibration_check,
        "range_status": range_status,
        "test_observed_coverage_pct": round(observed, 2),
        "test_mean_nominal_coverage_pct": round(mean_nominal, 2),
        "test_mean_interval_width_sgd": round(statistics.mean(row["interval_width"] for row in range_rows), 2),
        "test_mae_sgd": released_test_metric["mae"],
        "test_rows": len(test),
        "limitation": "The price method uses validation and test comparison results for this release decision, while its residual range is calibrated on validation; test and prior results were inspected during development. Confirm on later untouched sales. Review town-level errors; ranges omit renovation, view, exact floor, and transaction-specific negotiation.",
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
        print(f"Price-method calibration failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps({key: result[key] for key in (
        "status", "model_id", "model_name", "release_type", "selection_reason", "baseline_gate", "test_mae_sgd", "range_status", "calibration_method", "test_observed_coverage_pct",
        "test_mean_nominal_coverage_pct", "test_mean_interval_width_sgd", "test_rows", "outputs")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
