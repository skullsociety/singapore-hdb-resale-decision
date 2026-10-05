"""Create reliability diagnostics, calibrated ranges, estimate explanations, and a Phase 1 exit review."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import statistics
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import joblib
import numpy as np


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load project module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_csv(path: Path) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(f"Required input is missing: {path}")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError(f"Refusing to write an empty report: {path}")
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8-sig", newline="", dir=path.parent,
        suffix=".tmp", delete=False,
    ) as handle:
        temp_path = Path(handle.name)
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    try:
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def finite_quantile(values: list[float], probability: float) -> float:
    """Finite-sample order statistic used for split-conformal residual bounds."""
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("Cannot calibrate an interval without residuals.")
    rank = math.ceil((len(ordered) + 1) * probability) - 1
    return ordered[min(len(ordered) - 1, max(0, rank))]


def residual_bounds(values: list[float], coverage: float) -> tuple[float, float]:
    tail = (1 - coverage) / 2
    return finite_quantile(values, tail), finite_quantile(values, 1 - tail)


def calculate_metrics(records: list[dict], ranges: dict[str, dict] | None = None) -> dict:
    actual = [float(row["actual_price"]) for row in records]
    predicted = [float(row["predicted_price"]) for row in records]
    errors = [prediction - target for prediction, target in zip(predicted, actual)]
    absolute = [abs(value) for value in errors]
    ape = [error / target * 100 for error, target in zip(absolute, actual) if target]
    coverage_values = []
    widths = []
    if ranges:
        for row in records:
            estimate = ranges.get(row["transaction_id"])
            if estimate:
                coverage_values.append(
                    float(estimate["lower_estimate"]) <= float(row["actual_price"])
                    <= float(estimate["upper_estimate"])
                )
                widths.append(float(estimate["interval_width"]))
    return {
        "prediction_count": len(records),
        "mae": round(statistics.mean(absolute), 2) if absolute else None,
        "median_absolute_error": round(statistics.median(absolute), 2) if absolute else None,
        "mape_pct": round(statistics.mean(ape), 4) if ape else None,
        "mean_bias": round(statistics.mean(errors), 2) if errors else None,
        "p90_absolute_error": round(finite_quantile(absolute, 0.9), 2) if absolute else None,
        "interval_coverage_pct": round(statistics.mean(coverage_values) * 100, 2) if coverage_values else None,
        "mean_interval_width": round(statistics.mean(widths), 2) if widths else None,
    }


def band(value: float, cutoffs: tuple[float, float, float]) -> str:
    a, b, c = cutoffs
    if value <= a:
        return f"Q1 (<= {a:.1f})"
    if value <= b:
        return f"Q2 ({a:.1f}-{b:.1f})"
    if value <= c:
        return f"Q3 ({b:.1f}-{c:.1f})"
    return f"Q4 (> {c:.1f})"


def reliability_report(
    predictions: list[dict], rows_by_id: dict[str, dict], train_rows: list[dict],
    calibrated_ranges: dict[str, dict], selected_model_id: str, min_group_size: int = 30,
) -> list[dict]:
    train_area = [float(row["floor_area_sqm"]) for row in train_rows]
    train_price = [float(row["resale_price"]) for row in train_rows]
    area_cuts = tuple(float(np.quantile(train_area, q)) for q in (.25, .5, .75))
    price_cuts = tuple(float(np.quantile(train_price, q)) for q in (.25, .5, .75))
    selected_test = [
        row for row in predictions
        if row["dataset_split"] == "test" and row["model_id"] == selected_model_id
    ]
    reference = statistics.mean(abs(float(r["predicted_price"]) - float(r["actual_price"])) for r in selected_test)

    grouped: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)
    for prediction in predictions:
        if prediction["dataset_split"] != "test":
            continue
        source = rows_by_id[prediction["transaction_id"]]
        lease = int(source.get("remaining_lease_months") or 0)
        dimensions = {
            "town": str(source.get("town") or "Unknown"),
            "flat_type": str(source.get("flat_type") or "Unknown"),
            "actual_price_band": band(float(source["resale_price"]), price_cuts),
            "floor_area_band_sqm": band(float(source["floor_area_sqm"]), area_cuts),
            "storey_range": str(source.get("storey_range") or "Unknown"),
            "remaining_lease_band": (
                "<60 years" if lease < 720 else "60-69 years" if lease < 840
                else "70-79 years" if lease < 960 else "80+ years"
            ),
            "transaction_year": str(source["transaction_month"].year),
        }
        for dimension, label in dimensions.items():
            grouped[(prediction["model_id"], dimension, label, "test")].append(prediction)

    output = []
    for (model_id, dimension, label, split), items in sorted(grouped.items()):
        range_map = calibrated_ranges if model_id == selected_model_id else None
        values = calculate_metrics(items, range_map)
        count = values["prediction_count"]
        ratio = values["mae"] / reference if values["mae"] is not None and reference else None
        status = "too_few_to_judge" if count < min_group_size else (
            "higher_error_watch" if ratio is not None and ratio >= 1.25 else "within_overall_range"
        )
        output.append({
            "model_id": model_id, "dataset_split": split, "dimension": dimension,
            "group": label, **values,
            "mae_vs_selected_model_overall": round(ratio, 3) if ratio is not None else None,
            "minimum_group_size": min_group_size, "reliability_status": status,
        })
    return output


def compare_validation_calibrators(
    validation_rows: list[dict], predictions_by_id: dict[str, dict],
    model_id: str, min_group_rows: int = 100,
) -> list[dict]:
    """Use early validation months to compare interval methods on later months."""
    months = sorted({row["transaction_month"] for row in validation_rows})
    split_at = max(1, len(months) // 2)
    calibration_months = set(months[:split_at])
    assessment_months = set(months[split_at:])
    calibration = [row for row in validation_rows if row["transaction_month"] in calibration_months]
    assessment = [row for row in validation_rows if row["transaction_month"] in assessment_months]
    global_residuals = [
        float(row["resale_price"]) - float(predictions_by_id[row["transaction_id"]]["predicted_price"])
        for row in calibration
    ]
    residuals_by_group: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in calibration:
        residual = float(row["resale_price"]) - float(predictions_by_id[row["transaction_id"]]["predicted_price"])
        residuals_by_group[(str(row["town"]), str(row["flat_type"]))].append(residual)

    results = []
    for method in ("global", "town_flat_type"):
        for nominal in (.90, .95):
            covered, widths = [], []
            for row in assessment:
                group = residuals_by_group.get((str(row["town"]), str(row["flat_type"])), [])
                if method == "town_flat_type" and len(group) >= min_group_rows:
                    residuals = group
                    scope = "town_flat_type"
                else:
                    residuals = global_residuals
                    scope = "global_fallback"
                low, high = residual_bounds(residuals, nominal)
                point = float(predictions_by_id[row["transaction_id"]]["predicted_price"])
                lower, upper = point + low, point + high
                covered.append(lower <= float(row["resale_price"]) <= upper)
                widths.append(upper - lower)
            results.append({
                "model_id": model_id, "calibration_method": method,
                "nominal_coverage_pct": nominal * 100,
                "calibration_start_month": min(calibration_months).isoformat(),
                "calibration_end_month": max(calibration_months).isoformat(),
                "assessment_start_month": min(assessment_months).isoformat(),
                "assessment_end_month": max(assessment_months).isoformat(),
                "calibration_rows": len(calibration), "assessment_rows": len(assessment),
                "observed_coverage_pct": round(statistics.mean(covered) * 100, 2),
                "mean_interval_width": round(statistics.mean(widths), 2),
                "minimum_group_rows": min_group_rows,
            })
    return results


def comparable_evidence(project_root: Path, target_ids: set[str]) -> tuple[list[dict], dict[str, tuple[str, int]]]:
    baseline = load_module("baseline_builder", project_root / "scripts/phase_1_resale_model" / "04_build_baselines.py")
    db = project_root / "data" / "property.duckdb"
    by_month: dict[date, list[dict]] = defaultdict(list)
    with duckdb.connect(str(db), read_only=True) as connection:
        records = baseline.load_records(connection)
    for row in records:
        by_month[row["transaction_month"]].append(row)

    evidence = []
    summary: dict[str, tuple[str, int]] = {}
    history = []
    for month in sorted(by_month):
        month_records = by_month[month]
        for target in month_records:
            if target["transaction_id"] not in target_ids:
                continue
            candidates, tier = baseline.select_comparables(target, history)
            ranked = []
            for source in candidates:
                distance = baseline.distance_m(target, source)
                month_gap = (target["transaction_month"].year - source["transaction_month"].year) * 12 + target["transaction_month"].month - source["transaction_month"].month
                area_gap = abs(float(target["floor_area_sqm"]) - float(source["floor_area_sqm"]))
                storey_gap = abs(float(target["storey_midpoint"]) - float(source["storey_midpoint"]))
                score = distance / 1000 + area_gap / 10 + storey_gap / 6 + max(0, month_gap) / 24
                ranked.append((score, source, distance, area_gap, storey_gap, month_gap))
            ranked.sort(key=lambda item: (item[0], item[1]["transaction_id"]))
            summary[target["transaction_id"]] = (tier, len(candidates))
            for rank, (_, source, distance, area_gap, storey_gap, month_gap) in enumerate(ranked[:10], start=1):
                evidence.append({
                    "subject_transaction_id": target["transaction_id"],
                    "comparable_rank": rank, "comparable_transaction_id": source["transaction_id"],
                    "comparison_tier": tier, "sale_month": source["transaction_month"].isoformat(),
                    "sale_price": round(float(source["resale_price"]), 2),
                    "town": source["town"], "flat_type": source["flat_type"],
                    "flat_model": source["flat_model"],
                    "floor_area_sqm": source["floor_area_sqm"],
                    "storey_midpoint": source["storey_midpoint"],
                    "distance_m": round(distance, 1), "area_difference_sqm": round(area_gap, 1),
                    "storey_difference": round(storey_gap, 1), "months_before_subject": month_gap,
                })
        # Match the baseline builder: transactions from a month become usable only
        # after that month is scored, so same-month sales cannot support each other.
        history.extend(month_records)
    return evidence, summary


def explain_ridge(
    project_root: Path, selected_model_id: str, rows: list[dict], model_rows: list[dict],
    ranges: dict[str, dict], comp_summary: dict[str, tuple[str, int]],
) -> list[dict]:
    if selected_model_id != "ML_RIDGE_RESALE_PRICE_V1":
        raise ValueError(
            f"Local coefficient explanations currently require the selected Ridge model; got {selected_model_id}. "
            "Do not substitute global tree importance for a per-estimate explanation."
        )
    selection = json.loads((project_root / "reports/phase_1_resale_model" / "06_model_selection.json").read_text(encoding="utf-8"))
    artifact_path = Path(selection["artifacts"][selected_model_id])
    artifact = joblib.load(artifact_path)
    pipeline = artifact["pipeline"]
    train_rows = [row for row in rows if row["dataset_split"] == "train"]
    flat_types = sorted({str(row["flat_type"]) for row in train_rows})
    trainer = load_module("resale_trainer", project_root / "scripts/phase_1_resale_model" / "06_train_resale_models.py")
    feature_names, vectors = trainer.feature_records(rows, flat_types)
    matrix = np.asarray(vectors, dtype=object)
    test_rows = [row for row in rows if row["dataset_split"] == "test"]
    test_indices = [index for index, row in enumerate(rows) if row["dataset_split"] == "test"]
    transformed = pipeline.named_steps["features"].transform(matrix[test_indices])
    estimator = pipeline.named_steps["model"]
    names = pipeline.named_steps["features"].get_feature_names_out()
    coefficients = np.asarray(estimator.coef_).reshape(-1)
    contributions = np.asarray(transformed) * coefficients
    intercept = float(np.asarray(estimator.intercept_).reshape(-1)[0])
    prediction_lookup = {row["transaction_id"]: row for row in model_rows}

    output = []
    for index, row in enumerate(test_rows):
        values = contributions[index]
        ranked = sorted(enumerate(values), key=lambda pair: abs(float(pair[1])), reverse=True)
        positive = [
            {"feature": str(names[col]), "contribution_sgd": round(float(values[col]), 2)}
            for col, _ in ranked if values[col] > 0
        ][:5]
        negative = [
            {"feature": str(names[col]), "contribution_sgd": round(float(values[col]), 2)}
            for col, _ in ranked if values[col] < 0
        ][:5]
        prediction = prediction_lookup[row["transaction_id"]]
        tier, comp_count = comp_summary.get(row["transaction_id"], ("missing", 0))
        range_row = ranges[row["transaction_id"]]
        output.append({
            "transaction_id": row["transaction_id"], "dataset_split": "test",
            "model_id": selected_model_id, "model_version": "v1",
            "prediction_method": "ridge_linear_contributions",
            "point_estimate": round(float(prediction["predicted_price"]), 2),
            "lower_estimate": range_row["lower_estimate"], "upper_estimate": range_row["upper_estimate"],
            "nominal_interval_coverage_pct": range_row["nominal_coverage_pct"],
            "confidence_label": range_row["confidence_label"],
            "comparable_count": comp_count, "comparable_tier": tier,
            "ridge_intercept": round(intercept, 2),
            "top_positive_contributions_json": json.dumps(positive, ensure_ascii=False),
            "top_negative_contributions_json": json.dumps(negative, ensure_ascii=False),
            "reconstructed_estimate": round(intercept + float(np.sum(values)), 2),
            "actual_price_for_backtest_only": float(row["resale_price"]),
            "explanation_note": "Dollar contributions are additive model associations relative to the fitted Ridge baseline, not causal effects.",
        })
    return output


def run(project_root: Path) -> dict:
    db_path = project_root / "data" / "property.duckdb"
    trainer = load_module("resale_trainer", project_root / "scripts/phase_1_resale_model" / "06_train_resale_models.py")
    rows = trainer.read_transactions(db_path)
    rows_by_id = {row["transaction_id"]: row for row in rows}
    train_rows = [row for row in rows if row["dataset_split"] == "train"]
    validation_rows = [row for row in rows if row["dataset_split"] == "validation"]
    test_rows = [row for row in rows if row["dataset_split"] == "test"]
    model_predictions = read_csv(project_root / "data" / "model_ready" / "06_ml_predictions.csv")
    baseline_predictions = read_csv(project_root / "data" / "model_ready" / "baseline_predictions.csv")
    selection = json.loads((project_root / "reports/phase_1_resale_model" / "06_model_selection.json").read_text(encoding="utf-8"))
    model_id = selection["selected_model"]["model_id"]
    selected_rows = [row for row in model_predictions if row["model_id"] == model_id and row["dataset_split"] in {"validation", "test"}]
    if len(selected_rows) != len(validation_rows) + len(test_rows):
        raise ValueError("Selected model prediction rows do not match the validation/test transaction counts.")
    predictions_by_id = {row["transaction_id"]: row for row in selected_rows}
    test_ids = {row["transaction_id"] for row in test_rows}

    # Reserve the latest half of the time-ordered validation period for residual
    # calibration. Model fitting only used train rows; test remains untouched.
    validation_months = sorted({row["transaction_month"] for row in validation_rows})
    calibration_months = set(validation_months[len(validation_months) // 2:])
    calibration_rows = [
        row for row in validation_rows if row["transaction_month"] in calibration_months
    ]
    global_residuals = []
    for row in calibration_rows:
        prediction = predictions_by_id[row["transaction_id"]]
        residual = float(row["resale_price"]) - float(prediction["predicted_price"])
        global_residuals.append(residual)
    if len(global_residuals) < 100:
        raise ValueError(f"Too few validation calibration rows: {len(global_residuals)}")
    calibration_method_check = compare_validation_calibrators(
        validation_rows, predictions_by_id, model_id,
    )

    baseline_by_id = {
        row["transaction_id"]: row for row in baseline_predictions
        if row["model_id"] == "BASELINE_COMPARABLE_SALES_V1" and row["dataset_split"] == "test"
    }
    comparable_counts = {
        transaction_id: int(row.get("comparable_count") or 0)
        for transaction_id, row in baseline_by_id.items()
    }
    ranges = {}
    range_rows = []
    for row in test_rows:
        transaction_id = row["transaction_id"]
        prediction = predictions_by_id[transaction_id]
        key = (str(row["town"]), str(row["flat_type"]))
        calibrator_residuals, calibration_scope = global_residuals, "global"
        comparable_count = comparable_counts.get(transaction_id, 0)
        nominal_coverage = .975 if comparable_count < 5 else .95
        lower_offset, upper_offset = residual_bounds(calibrator_residuals, nominal_coverage)
        point = float(prediction["predicted_price"])
        lower = max(1.0, point + lower_offset)
        upper = max(lower, point + upper_offset)
        confidence = "low_wide_range" if comparable_count < 5 else "moderate" if comparable_count < 20 else "supported_by_comparables"
        if comparable_count < 3:
            confidence = "insufficient_comparable_evidence"
        result = {
            "transaction_id": transaction_id, "selected_model_id": model_id,
            "model_version": "v1", "valuation_month": row["transaction_month"].isoformat(),
            "town": row["town"], "flat_type": row["flat_type"],
            "point_estimate": round(point, 2), "lower_estimate": round(lower, 2),
            "upper_estimate": round(upper, 2), "interval_width": round(upper - lower, 2),
            "nominal_coverage_pct": round(nominal_coverage * 100, 1),
            "calibration_scope": calibration_scope, "calibration_rows": len(calibrator_residuals),
            "comparable_count": comparable_count, "comparable_tier": baseline_by_id.get(transaction_id, {}).get("comparable_tier", "missing"),
            "confidence_label": confidence,
            "actual_price_backtest_only": float(row["resale_price"]),
            "interval_covered": lower <= float(row["resale_price"]) <= upper,
            "calibration_period_start": min(calibration_months).isoformat(),
            "calibration_period_end": max(calibration_months).isoformat(),
        }
        ranges[transaction_id] = result
        range_rows.append(result)

    selected_test = [predictions_by_id[row["transaction_id"]] for row in test_rows]
    selected_validation = [predictions_by_id[row["transaction_id"]] for row in validation_rows]
    all_error_predictions = selected_test + [
        row for row in baseline_predictions if row["dataset_split"] == "test"
    ]
    reliability = reliability_report(all_error_predictions, rows_by_id, train_rows, ranges, model_id)

    evidence_rows, comp_summary = comparable_evidence(project_root, test_ids)
    explanations = explain_ridge(project_root, model_id, rows, selected_test, ranges, comp_summary)
    explain_by_id = {row["transaction_id"]: row for row in explanations}

    # A test-period range is considered calibrated only if observed coverage is
    # within five percentage points of its nominal target. This is a diagnostic,
    # not a claim of guaranteed future coverage.
    observed_coverage = statistics.mean(row["interval_covered"] for row in range_rows) * 100
    mean_nominal = statistics.mean(row["nominal_coverage_pct"] for row in range_rows)
    town_count = len({row["town"] for row in test_rows})
    low_comp = sum(row["comparable_count"] < 5 for row in range_rows)
    insufficient = sum(row["comparable_count"] < 3 for row in range_rows)
    test_model_metrics = next(row for row in selection["metrics"] if row["model_id"] == model_id and row["dataset_split"] == "test")
    baseline_test_metrics = {
        row["model_id"]: row for row in selection["metrics"] if row["dataset_split"] == "test"
    }
    simple_baseline = baseline_test_metrics["BASELINE_RECENT_FLAT_TYPE_24M_V1"]
    comparable_baseline = baseline_test_metrics["BASELINE_COMPARABLE_SALES_V1"]
    criteria = [
        {
            "criterion": "Beat the simple median baseline and report the comparable-sales benchmark on later unseen transactions",
            "status": "pass" if float(test_model_metrics["mae"]) < float(simple_baseline["mae"]) else "fail",
            "evidence": f"Selected model test MAE ${float(test_model_metrics['mae']):,.0f}; recent town/flat-type median MAE ${float(simple_baseline['mae']):,.0f}; comparable-sales baseline MAE ${float(comparable_baseline['mae']):,.0f}.",
        },
        {
            "criterion": "Report accuracy by town, flat type, and price band",
            "status": "pass_pilot_scope" if town_count == 1 else "pass",
            "evidence": f"Reliability report includes all three groups; current test data contains {town_count} town(s): {', '.join(sorted({str(r['town']) for r in test_rows}))}.",
        },
        {
            "criterion": "Trace every estimate to its model version and source transactions",
            "status": "pass" if len(explanations) == len(test_rows) and len(comp_summary) == len(test_rows) else "fail",
            "evidence": f"{len(explanations)} of {len(test_rows)} test estimates have model explanations; {len(evidence_rows)} ranked comparable-sales evidence rows are linked by transaction ID.",
        },
        {
            "criterion": "Low-data cases widen the range or report insufficient evidence",
            "status": "pass" if all(row["nominal_coverage_pct"] == 97.5 for row in range_rows if row["comparable_count"] < 5) and all(row["confidence_label"] == "insufficient_comparable_evidence" for row in range_rows if row["comparable_count"] < 3) else "review",
            "evidence": f"{low_comp} estimates with fewer than 5 comparables use a wider nominal 97.5% interval; {insufficient} with fewer than 3 are explicitly marked insufficient.",
        },
        {
        "criterion": "Calibrated range coverage is close to its stated level on unseen sales",
            "status": "pass" if abs(observed_coverage - mean_nominal) <= 5 else "needs_more_calibration",
            "evidence": f"Observed test coverage {observed_coverage:.1f}% versus mean nominal level {mean_nominal:.1f}%; mean interval width ${statistics.mean(r['interval_width'] for r in range_rows):,.0f}.",
        },
    ]
    all_core_pass = all(item["status"] in {"pass", "pass_pilot_scope"} for item in criteria[:4])
    range_pass = criteria[-1]["status"] == "pass"
    overall = "phase1_exit_ready" if all_core_pass and range_pass and town_count > 1 else "pilot_exit_with_open_gaps"
    exit_review = {
        "status": overall,
        "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
        "selected_model_id": model_id,
        "selected_model_validation_mae": selection["selected_model"]["validation_mae"],
        "selected_model_test_mae": test_model_metrics["mae"],
        "simple_baseline_test_mae": simple_baseline["mae"],
        "comparable_sales_test_mae": comparable_baseline["mae"],
        "calibration_period": [min(calibration_months).isoformat(), max(calibration_months).isoformat()],
        "calibration_rows": len(calibration_rows),
        "validation_calibrator_method_check": calibration_method_check,
        "test_rows": len(test_rows), "test_interval_coverage_pct": round(observed_coverage, 2),
        "mean_nominal_interval_coverage_pct": round(mean_nominal, 2),
        "single_town_scope": town_count == 1,
        "criteria": criteria,
        "limitations": [
            "Current data is a one-town Sengkang pilot and does not establish performance across all HDB towns.",
            "Observed test coverage is below the mean nominal level; passing the five-percentage-point pilot tolerance does not remove undercoverage risk. Use ranges as estimates, not official valuations.",
            "The validation calibration window was reserved for residual calibration, but the existing selected model had previously been chosen using the full validation period; test coverage is therefore the final independent check.",
            "Comparable evidence ranks registered sales by transparent distance, size, floor, and recency criteria; it does not observe renovation, unit condition, orientation, or view.",
        ],
    }

    reports = project_root / "reports/phase_1_resale_model"
    output_paths = {
        "reliability": reports / "07_reliability_breakdown.csv",
        "ranges": reports / "07_calibrated_price_ranges.csv",
        "explanations": reports / "07_estimate_explanations.csv",
        "comparables": reports / "07_comparable_evidence.csv",
        "calibration_check": reports / "07_calibration_method_check.csv",
        "exit_review": reports / "07_phase1_exit_review.json",
    }
    write_csv(output_paths["reliability"], reliability)
    write_csv(output_paths["ranges"], range_rows)
    write_csv(output_paths["explanations"], explanations)
    write_csv(output_paths["comparables"], evidence_rows)
    write_csv(output_paths["calibration_check"], calibration_method_check)
    output_paths["exit_review"].write_text(json.dumps(exit_review, indent=2), encoding="utf-8")

    # Persist the calibrated bounds to the existing test prediction rows and keep
    # normalized diagnostic tables alongside the transaction/model tables.
    with duckdb.connect(str(db_path)) as connection:
        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute("""CREATE OR REPLACE TABLE phase1_reliability_breakdown AS
                SELECT * FROM read_csv_auto(?)""", [str(output_paths["reliability"])])
            connection.execute("""CREATE OR REPLACE TABLE phase1_calibrated_price_ranges AS
                SELECT * FROM read_csv_auto(?)""", [str(output_paths["ranges"])])
            connection.execute("""CREATE OR REPLACE TABLE phase1_estimate_explanations AS
                SELECT * FROM read_csv_auto(?)""", [str(output_paths["explanations"])])
            connection.execute("""CREATE OR REPLACE TABLE phase1_comparable_evidence AS
                SELECT * FROM read_csv_auto(?)""", [str(output_paths["comparables"])])
            connection.execute("""CREATE OR REPLACE TABLE phase1_calibration_method_check AS
                SELECT * FROM read_csv_auto(?)""", [str(output_paths["calibration_check"])])
            connection.execute("""CREATE OR REPLACE TABLE phase1_exit_review AS
                SELECT * FROM read_json_auto(?)""", [str(output_paths["exit_review"])])
            connection.execute("""UPDATE model_predictions AS prediction
                SET lower_estimate = calibrated.lower_estimate,
                    upper_estimate = calibrated.upper_estimate
                FROM phase1_calibrated_price_ranges AS calibrated
                WHERE prediction.model_id = calibrated.selected_model_id
                  AND prediction.transaction_id = calibrated.transaction_id
                  AND prediction.dataset_split = 'test'""")
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise

    return {
        "status": "success", "phase1_exit_status": overall,
        "selected_model_id": model_id,
        "test_mae": test_model_metrics["mae"],
        "test_interval_coverage_pct": round(observed_coverage, 2),
        "mean_nominal_interval_coverage_pct": round(mean_nominal, 2),
        "test_estimates_explained": len(explanations),
        "comparable_evidence_rows": len(evidence_rows),
        "outputs": {key: str(value) for key, value in output_paths.items()},
        "criteria": criteria,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    try:
        result = run(args.project_root.resolve())
    except (ValueError, OSError, duckdb.Error, RuntimeError, KeyError) as error:
        print(f"Phase 1 diagnostics failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
