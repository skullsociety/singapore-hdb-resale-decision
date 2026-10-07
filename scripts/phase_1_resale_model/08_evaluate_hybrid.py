"""Evaluate a Ridge/comparable blend and prior-month price features.

Blend weights use only the first two rolling training folds. A third training
fold, validation, and test are reported in time order. The test period never
chooses a weight or candidate. This is an experiment; step 06 remains the
registered production candidate until its explanation and range workflow can
support a promoted hybrid.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import joblib
import numpy as np


FOLDS = (
    ("fold_1", date(2021, 10, 1), date(2022, 9, 1)),
    ("fold_2", date(2022, 10, 1), date(2023, 9, 1)),
    ("fold_3", date(2023, 10, 1), date(2024, 9, 1)),
)
TREND_NAMES = (
    "recent_12m_price_anchor",
    "recent_12m_price_change_pct",
    "recent_12m_sale_count",
)
CANDIDATES = (
    "comparable_sales", "ridge", "ridge_recent_trend",
    "ridge_comparable_blend", "trend_comparable_blend",
)


def load_script(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def month_number(value: date) -> int:
    return value.year * 12 + value.month


def recent_price_features(rows: list[dict]) -> dict[str, tuple[float, float, int]]:
    """Use only earlier transaction months, including for historical train rows."""
    by_month: dict[date, list[dict]] = defaultdict(list)
    for row in rows:
        by_month[row["transaction_month"]].append(row)
    history: dict[tuple[str, str], list[tuple[int, float]]] = defaultdict(list)
    features = {}
    for month in sorted(by_month):
        current = month_number(month)
        groups = {(str(row["town"]), str(row["flat_type"])) for row in by_month[month]}
        group_features = {}
        for group in groups:
            prices = history[group]
            recent = [price for m, price in prices if current - 12 <= m < current]
            previous = [price for m, price in prices if current - 24 <= m < current - 12]
            recent_median = statistics.median(recent) if len(recent) >= 10 else math.nan
            previous_median = statistics.median(previous) if len(previous) >= 10 else math.nan
            change = (
                100 * (recent_median / previous_median - 1)
                if math.isfinite(recent_median) and math.isfinite(previous_median)
                and previous_median > 0 else math.nan
            )
            group_features[group] = (recent_median, change, len(recent))
        for row in by_month[month]:
            group = (str(row["town"]), str(row["flat_type"]))
            price_per_sqm, change, count = group_features[group]
            anchor = price_per_sqm * float(row["floor_area_sqm"]) if math.isfinite(price_per_sqm) else math.nan
            features[row["transaction_id"]] = (anchor, change, count)
        # Add the complete month only after every row in that month is encoded.
        for row in by_month[month]:
            group = (str(row["town"]), str(row["flat_type"]))
            history[group].append((current, float(row["resale_price"]) / float(row["floor_area_sqm"])))
    return features


def matrices(rows: list[dict], models) -> tuple[np.ndarray, np.ndarray, list[str], list[str]]:
    flat_types = sorted({str(row["flat_type"]) for row in rows if row["dataset_split"] == "train"})
    names, records = models.feature_records(rows, flat_types)
    base = np.asarray(records, dtype=object)
    trend = recent_price_features(rows)
    trend_values = np.asarray([trend[row["transaction_id"]] for row in rows], dtype=object)
    numeric_count = len(names) - len(models.CAT_COLUMNS)
    enhanced = np.concatenate((base[:, :numeric_count], trend_values, base[:, numeric_count:]), axis=1)
    enhanced_names = [*names[:numeric_count], *TREND_NAMES, *names[numeric_count:]]
    return base, enhanced, names, enhanced_names


def fit_ridge(models, matrix: np.ndarray, rows: list[dict], train_indices: list[int], eval_indices: list[int], names: list[str]):
    numeric_indices = list(range(len(names) - len(models.CAT_COLUMNS)))
    categorical_indices = list(range(len(names) - len(models.CAT_COLUMNS), len(names)))
    estimator = models.build_pipeline("ridge", numeric_indices, categorical_indices)
    target = np.asarray([float(rows[i]["resale_price"]) for i in train_indices], dtype=float)
    estimator.fit(matrix[train_indices], target)
    values = estimator.predict(matrix[eval_indices])
    return estimator, {
        rows[index]["transaction_id"]: round(max(1.0, float(value)), 2)
        for index, value in zip(eval_indices, values)
    }


def training_comparables(rows: list[dict], target_ids: set[str], baselines) -> tuple[dict[str, float], set[str]]:
    by_month: dict[date, list[dict]] = defaultdict(list)
    for row in rows:
        if row["dataset_split"] == "train":
            by_month[row["transaction_month"]].append(row)
    history = baselines.ComparableHistory()
    result = {}
    without_history = set()
    processed = 0
    progress_interval = max(1, len(target_ids) // 10)
    for month in sorted(by_month):
        month_rows = by_month[month]
        for row in month_rows:
            transaction_id = row["transaction_id"]
            if transaction_id not in target_ids:
                continue
            processed += 1
            selected, _ = baselines.select_comparables(row, history)
            if not selected:
                without_history.add(transaction_id)
            else:
                result[transaction_id] = round(baselines.median_prediction(selected)[0], 2)
            if processed % progress_interval == 0 or processed == len(target_ids):
                print(f"Rolling comparables: {processed:,}/{len(target_ids):,} fold sales checked.", flush=True)
        # Current-month transactions cannot become comparables until the next month.
        history.add(month_rows)
    if (set(result) | without_history) != target_ids:
        raise ValueError("Rolling comparable predictions do not cover every training-fold target")
    return result, without_history


def read_prediction_file(path: Path, model_id: str) -> dict[str, float]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["model_id"] == model_id]
    return {row["transaction_id"]: float(row["predicted_price"]) for row in rows}


def mae(ids: list[str], predictions: dict[str, float], actual: dict[str, float]) -> float:
    return statistics.mean(abs(predictions[transaction_id] - actual[transaction_id]) for transaction_id in ids)


def choose_weight(ids: list[str], ridge: dict[str, float], comparable: dict[str, float], actual: dict[str, float]) -> tuple[float, float]:
    scores = []
    for step in range(21):
        weight = step / 20
        blended = {key: weight * ridge[key] + (1 - weight) * comparable[key] for key in ids}
        scores.append((mae(ids, blended, actual), abs(weight - 0.5), weight))
    best = min(scores)
    return best[2], best[0]


def describe(ids: list[str], predicted: dict[str, float], actual: dict[str, float]) -> dict:
    error = [predicted[key] - actual[key] for key in ids]
    absolute = [abs(value) for value in error]
    return {
        "count": len(ids),
        "mae": round(statistics.mean(absolute), 2),
        "median_absolute_error": round(statistics.median(absolute), 2),
        "rmse": round(math.sqrt(statistics.mean(value * value for value in error)), 2),
        "mean_bias": round(statistics.mean(error), 2),
    }


def run(project_root: Path) -> dict:
    script_dir = Path(__file__).resolve().parent
    models = load_script(script_dir / "06_train_resale_models.py", "resale_model_module")
    baselines = load_script(script_dir / "04_build_baselines.py", "baseline_module")
    rows = models.read_transactions(project_root / "data" / "property.duckdb")
    for row in rows:
        row["transaction_month"] = models.as_date(row["transaction_month"])
    actual = {row["transaction_id"]: float(row["resale_price"]) for row in rows}
    row_by_id = {row["transaction_id"]: row for row in rows}
    base_matrix, trend_matrix, base_names, trend_names = matrices(rows, models)

    folds = []
    oof_ridge, oof_trend = {}, {}
    for label, start, end in FOLDS:
        fit_indices = [i for i, row in enumerate(rows) if row["dataset_split"] == "train" and row["transaction_month"] < start]
        eval_indices = [i for i, row in enumerate(rows) if row["dataset_split"] == "train" and start <= row["transaction_month"] <= end]
        if len(fit_indices) < 3000 or not eval_indices:
            raise ValueError(f"Insufficient history for {label}")
        _, base_predictions = fit_ridge(models, base_matrix, rows, fit_indices, eval_indices, base_names)
        _, trend_predictions = fit_ridge(models, trend_matrix, rows, fit_indices, eval_indices, trend_names)
        oof_ridge.update(base_predictions)
        oof_trend.update(trend_predictions)
        folds.append({"label": label, "start": start.isoformat(), "end": end.isoformat(),
                      "training_rows": len(fit_indices), "prediction_rows": len(eval_indices),
                      "transaction_ids": sorted(base_predictions)})
        print(f"Completed {label}: {len(fit_indices):,} earlier sales, {len(eval_indices):,} predictions.", flush=True)

    oof_comparable, without_history = training_comparables(rows, set(oof_ridge), baselines)
    for fold in folds:
        fold["comparable_prediction_rows"] = sum(key in oof_comparable for key in fold["transaction_ids"])
        fold["excluded_no_prior_comparables"] = len(fold["transaction_ids"]) - fold["comparable_prediction_rows"]
        print(
            f"{fold['label']} comparable coverage: {fold['comparable_prediction_rows']:,}/"
            f"{fold['prediction_rows']:,}; {fold['excluded_no_prior_comparables']:,} excluded.",
            flush=True,
        )
    tune_ids = [key for fold in folds[:2] for key in fold["transaction_ids"] if key in oof_comparable]
    if not tune_ids or not any(key in oof_comparable for key in folds[2]["transaction_ids"]):
        raise ValueError("Too few training-fold sales with earlier comparables for a paired hybrid evaluation")
    ridge_weight, ridge_tune_mae = choose_weight(tune_ids, oof_ridge, oof_comparable, actual)
    trend_weight, trend_tune_mae = choose_weight(tune_ids, oof_trend, oof_comparable, actual)

    val_test = [i for i, row in enumerate(rows) if row["dataset_split"] in {"validation", "test"}]
    train_indices = [i for i, row in enumerate(rows) if row["dataset_split"] == "train"]
    trend_model, later_trend = fit_ridge(models, trend_matrix, rows, train_indices, val_test, trend_names)
    existing_ridge = read_prediction_file(project_root / "data/model_ready/06_ml_predictions.csv", "ML_RIDGE_RESALE_PRICE_V1")
    existing_comparable = read_prediction_file(project_root / "data/model_ready/baseline_predictions.csv", "BASELINE_COMPARABLE_SALES_V1")
    later_ids = {rows[i]["transaction_id"] for i in val_test}
    if set(existing_ridge) != later_ids or set(existing_comparable) != later_ids or set(later_trend) != later_ids:
        raise ValueError("Existing Ridge, comparable, and new trend predictions must cover identical validation/test transactions")

    ridge = {**oof_ridge, **existing_ridge}
    trend = {**oof_trend, **later_trend}
    comparable = {**oof_comparable, **existing_comparable}
    ids_by_period = {
        "weight_tuning": tune_ids,
        "rolling_check": [key for key in folds[2]["transaction_ids"] if key in oof_comparable],
        "validation": [row["transaction_id"] for row in rows if row["dataset_split"] == "validation"],
        "test": [row["transaction_id"] for row in rows if row["dataset_split"] == "test"],
    }
    def candidate_values(key: str) -> dict[str, float]:
        if key == "comparable_sales": return comparable
        if key == "ridge": return ridge
        if key == "ridge_recent_trend": return trend
        weight = ridge_weight if key == "ridge_comparable_blend" else trend_weight
        source = ridge if key == "ridge_comparable_blend" else trend
        return {transaction_id: round(weight * source[transaction_id] + (1 - weight) * comparable[transaction_id], 2)
                for transaction_id in source if transaction_id in comparable}

    values = {candidate: candidate_values(candidate) for candidate in CANDIDATES}
    metrics = []
    predictions = []
    for period, ids in ids_by_period.items():
        for candidate in CANDIDATES:
            estimates = values[candidate]
            metrics.append({"period": period, "candidate": candidate, **describe(ids, estimates, actual)})
            for transaction_id in ids:
                source = row_by_id[transaction_id]
                prediction = estimates[transaction_id]
                predictions.append({
                    "period": period, "candidate": candidate, "transaction_id": transaction_id,
                    "valuation_month": source["transaction_month"].isoformat(),
                    "flat_type": source["flat_type"], "actual_price": actual[transaction_id],
                    "predicted_price": prediction, "absolute_error": round(abs(prediction - actual[transaction_id]), 2),
                })

    validation_ranking = sorted(
        [row for row in metrics if row["period"] == "validation"],
        key=lambda row: (row["mae"], row["rmse"]),
    )
    selected = validation_ranking[0]["candidate"]
    selected_test = next(row for row in metrics if row["period"] == "test" and row["candidate"] == selected)
    comparable_test = next(row for row in metrics if row["period"] == "test" and row["candidate"] == "comparable_sales")
    blend_test = next(row for row in metrics if row["period"] == "test" and row["candidate"] == "ridge_comparable_blend")
    paired_ids = ids_by_period["test"]
    paired_error_difference = np.asarray([
        abs(values["ridge_comparable_blend"][key] - actual[key])
        - abs(values["comparable_sales"][key] - actual[key])
        for key in paired_ids
    ])
    random = np.random.default_rng(42)
    # Batch draws to avoid holding the full bootstrap matrix in memory.
    bootstrap_means = np.concatenate([
        random.choice(paired_error_difference, size=(100, len(paired_error_difference)), replace=True).mean(axis=1)
        for _ in range(50)
    ])
    blend_vs_comparable = {
        "test_mae_difference": round(blend_test["mae"] - comparable_test["mae"], 2),
        "paired_bootstrap_95pct_interval": [round(float(x), 2) for x in np.quantile(bootstrap_means, (.025, .975))],
        "test_transactions_blend_better": int((paired_error_difference < 0).sum()),
        "test_transactions_comparable_better": int((paired_error_difference > 0).sum()),
    }
    promotion_status = (
        "hold_selected_candidate_failed_test"
        if selected_test["mae"] >= comparable_test["mae"]
        else "test_passed_future_confirmation_required"
    )
    segments = []
    for period in ("validation", "test"):
        for flat_type in sorted({row_by_id[transaction_id]["flat_type"] for transaction_id in ids_by_period[period]}):
            ids = [transaction_id for transaction_id in ids_by_period[period] if row_by_id[transaction_id]["flat_type"] == flat_type]
            for candidate in CANDIDATES:
                segments.append({"period": period, "flat_type": flat_type, "candidate": candidate,
                                 **describe(ids, values[candidate], actual)})

    reports = project_root / "reports/phase_1_resale_model"
    report_paths = {
        "metrics": reports / "08_hybrid_model_comparison.csv",
        "segments": reports / "08_hybrid_flat_type_errors.csv",
        "predictions": project_root / "data/model_ready/08_hybrid_predictions.csv",
        "selection": reports / "08_hybrid_selection.json",
        "trend_model": project_root / "models/phase_1_resale_model/08_ridge_recent_trend.joblib",
    }
    models.atomic_csv(report_paths["metrics"], metrics)
    models.atomic_csv(report_paths["segments"], segments)
    models.atomic_csv(report_paths["predictions"], predictions)
    joblib.dump({"pipeline": trend_model, "feature_columns": trend_names,
                 "trend_definition": "Prior 12-month same-town/flat-type median price per sqm times subject area; prior 12 versus previous 12-month percentage change; prior 12-month sale count. Same-month and future sales excluded."}, report_paths["trend_model"])
    summary = {
        "status": "success", "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "weight_tuning_period": [folds[0]["start"], folds[1]["end"]],
        "ridge_weight": ridge_weight, "trend_ridge_weight": trend_weight,
        "weight_tuning_mae": {"ridge_blend": round(ridge_tune_mae, 2), "trend_blend": round(trend_tune_mae, 2)},
        "folds": [{key: value for key, value in fold.items() if key != "transaction_ids"} for fold in folds],
        "selection_rule": "Weights from sales with earlier comparables in the first two rolling training folds; candidate with lowest validation MAE; test report-only.",
        "excluded_training_fold_sales_without_prior_comparables": len(without_history),
        "selected_candidate": selected, "selected_candidate_test_metrics": selected_test,
        "comparable_sales_test_metrics": comparable_test,
        "ridge_comparable_blend_test_metrics": blend_test,
        "blend_vs_comparable_test": blend_vs_comparable,
        "promotion_status": promotion_status,
        "metrics": metrics, "outputs": {key: str(value) for key, value in report_paths.items()},
        "limitation": "Existing validation and test summaries were inspected during earlier development. Treat this as an exploratory extension; confirm any promotion on future transactions.",
    }
    report_paths["selection"].write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    try:
        summary = run(args.project_root.resolve())
    except (ValueError, OSError, RuntimeError) as error:
        print(f"Hybrid experiment failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps({key: summary[key] for key in (
        "status", "ridge_weight", "trend_ridge_weight", "selected_candidate",
        "selected_candidate_test_metrics", "blend_vs_comparable_test",
        "promotion_status", "outputs")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
