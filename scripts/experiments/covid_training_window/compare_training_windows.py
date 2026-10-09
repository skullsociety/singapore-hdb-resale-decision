"""Test whether older training sales worsen today's resale-price estimates.

This is an isolated experiment. It reads the existing research data and baseline
predictions, but never replaces the released model or dashboard database.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[3]
TRAIN_SCRIPT = PROJECT_ROOT / "scripts/phase_1_resale_model/06_train_resale_models.py"
REPORT_DIR = PROJECT_ROOT / "reports/experiments/covid_training_window"
WINDOWS = (("all_2017_onward", date(2017, 1, 1)),
           ("2020_onward", date(2020, 1, 1)),
           ("2023_onward", date(2023, 1, 1)))


def load_training_code():
    spec = importlib.util.spec_from_file_location("resale_training", TRAIN_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if module.CatBoostRegressor is None:
        raise RuntimeError("CatBoost is unavailable; install the project requirements first")
    return module


def fixed_baseline_anchors(module, train: list[dict], evaluation: list[dict]) -> tuple[str, dict[str, float]]:
    selection = json.loads((PROJECT_ROOT / "reports/phase_1_resale_model/04_baseline_metrics.json").read_text(encoding="utf-8"))
    baseline_id = selection["selected_comparable_model_id"]
    anchors = module.historical_training_anchors(PROJECT_ROOT, train, baseline_id, progress=True)
    saved = {
        row["transaction_id"]: row
        for row in module.read_baselines(PROJECT_ROOT / "data/model_ready/baseline_predictions.csv")
        if row["model_id"] == baseline_id
    }
    if set(saved) != {row["transaction_id"] for row in evaluation}:
        raise ValueError("Saved baseline does not match validation/test sales; rerun Step 4")
    for row in evaluation:
        prior = saved[row["transaction_id"]]
        if (prior["dataset_split"] != row["dataset_split"]
                or prior["valuation_month"] != module.as_date(row["transaction_month"])
                or abs(prior["actual_price"] - float(row["resale_price"])) > 0.01):
            raise ValueError("Saved baseline is stale; rerun Step 4")
        anchors[row["transaction_id"]] = prior["predicted_price"]
    return baseline_id, anchors


def summary(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    error = predicted - actual
    return {
        "mae_sgd": round(float(np.mean(np.abs(error))), 2),
        "median_absolute_error_sgd": round(float(np.median(np.abs(error))), 2),
        "mean_bias_sgd": round(float(np.mean(error)), 2),
        "rmse_sgd": round(float(np.sqrt(np.mean(error ** 2))), 2),
        "mape_pct": round(float(np.mean(np.abs(error) / actual) * 100), 3),
    }


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def paired_breakdowns(sales: list[dict]) -> list[dict]:
    """Compare equal sales, so a town's mix cannot differ between models."""
    output = []
    cutoffs = {}
    for split in ("validation", "test"):
        prices = sorted(row["actual_price_sgd"] for row in sales if row["evaluation_split"] == split)
        cutoffs[split] = prices[int(0.9 * len(prices))]
    for candidate in ("2020_onward", "2023_onward"):
        groups = defaultdict(list)
        for row in sales:
            split = row["evaluation_split"]
            groups[(split, "overall", "ALL")].append(row)
            groups[(split, "month", row["transaction_month"])].append(row)
            groups[(split, "town", row["town"])].append(row)
            price_group = "top_10_pct_price" if row["actual_price_sgd"] >= cutoffs[split] else "other_90_pct_price"
            groups[(split, "actual_price_band", price_group)].append(row)
        for (split, group_type, group_name), group in sorted(groups.items()):
            actual = np.asarray([row["actual_price_sgd"] for row in group], dtype=float)
            old = np.asarray([row["all_2017_onward"] for row in group], dtype=float)
            new = np.asarray([row[candidate] for row in group], dtype=float)
            gain = np.abs(old - actual) - np.abs(new - actual)
            output.append({
                "candidate": candidate, "evaluation_split": split,
                "group_type": group_type, "group_name": group_name,
                "sales": len(group), "limited_evidence": len(group) < 100,
                "full_history_mae_sgd": round(float(np.mean(np.abs(old - actual))), 2),
                "candidate_mae_sgd": round(float(np.mean(np.abs(new - actual))), 2),
                "mae_improvement_sgd": round(float(np.mean(gain)), 2),
                "full_history_bias_sgd": round(float(np.mean(old - actual)), 2),
                "candidate_bias_sgd": round(float(np.mean(new - actual)), 2),
                "candidate_wins": int(np.sum(gain > 0.005)),
                "full_history_wins": int(np.sum(gain < -0.005)),
                "ties": int(np.sum(np.abs(gain) <= 0.005)),
            })
    return output


def run(*, prepare_only: bool = False) -> list[dict]:
    module = load_training_code()
    rows = module.read_transactions(PROJECT_ROOT / "data/property.duckdb")
    train = [row for row in rows if row["dataset_split"] == "train"]
    evaluation = [row for row in rows if row["dataset_split"] in {"validation", "test"}]
    if not train or {row["dataset_split"] for row in evaluation} != {"validation", "test"}:
        raise ValueError("Train, validation, and test sales are required")
    baseline_id, anchors = fixed_baseline_anchors(module, train, evaluation)
    for row in rows:
        row[module.COMPARABLE_FEATURE] = anchors.get(row["transaction_id"])
    eligible_train = [row for row in train if row[module.COMPARABLE_FEATURE] is not None]
    if not eligible_train:
        raise ValueError("No training sales have an earlier comparable price")
    flat_types = sorted({str(row["flat_type"]) for row in train})
    columns, vectors = module.baseline_aware_features(rows, flat_types)
    matrix = np.asarray(vectors, dtype=object)
    categorical_indices = [i for i, name in enumerate(columns) if name in module.CAT_COLUMNS]
    indices = {row["transaction_id"]: index for index, row in enumerate(rows)}
    split_rows = {split: [row for row in evaluation if row["dataset_split"] == split]
                  for split in ("validation", "test")}
    split_indices = {split: [indices[row["transaction_id"]] for row in group]
                     for split, group in split_rows.items()}
    report: list[dict] = []
    sale_predictions = {
        row["transaction_id"]: {
            "transaction_id": row["transaction_id"],
            "evaluation_split": row["dataset_split"],
            "transaction_month": module.as_date(row["transaction_month"]).strftime("%Y-%m"),
            "town": row["town"], "flat_type": row["flat_type"],
            "actual_price_sgd": float(row["resale_price"]),
            "comparable_price_sgd": row[module.COMPARABLE_FEATURE],
        } for row in evaluation
    }

    for label, start_date in WINDOWS:
        subset = [row for row in eligible_train if module.as_date(row["transaction_month"]) >= start_date]
        if not subset:
            raise ValueError(f"No eligible training sales for {label}")
        print(f"{label}: {len(subset):,} training sales", flush=True)
        if prepare_only:
            continue
        subset_indices = [indices[row["transaction_id"]] for row in subset]
        target = np.asarray([float(row["resale_price"]) - row[module.COMPARABLE_FEATURE]
                             for row in subset], dtype=float)
        validation_target = np.asarray([
            float(row["resale_price"]) - row[module.COMPARABLE_FEATURE]
            for row in split_rows["validation"]], dtype=float)
        estimator = module.CatBoostRegressor(
            iterations=650, depth=6, learning_rate=0.04, l2_leaf_reg=5,
            loss_function="MultiQuantile:alpha=0.1,0.5,0.9",
            random_seed=42, thread_count=5, verbose=100, allow_writing_files=False,
        )
        estimator.fit(matrix[subset_indices], target, cat_features=categorical_indices,
                      eval_set=(matrix[split_indices["validation"]], validation_target),
                      early_stopping_rounds=module.CATBOOST_EARLY_STOPPING_ROUNDS,
                      use_best_model=True)
        for split in ("validation", "test"):
            group = split_rows[split]
            raw = np.asarray(estimator.predict(matrix[split_indices[split]]), dtype=float)
            if raw.shape != (len(group), 3):
                raise ValueError(f"Unexpected quantile predictions: {raw.shape}")
            anchors_for_split = np.asarray([row[module.COMPARABLE_FEATURE] for row in group], dtype=float)
            predicted = np.maximum(anchors_for_split + np.sort(raw, axis=1)[:, 1], 1.0)
            actual = np.asarray([float(row["resale_price"]) for row in group], dtype=float)
            result = {"training_window": label, "training_start": start_date.isoformat(),
                      "training_rows": len(subset), "evaluation_split": split,
                      "evaluation_rows": len(group), "trees_used": estimator.tree_count_,
                      "baseline_id": baseline_id, **summary(actual, predicted)}
            report.append(result)
            for row, value in zip(group, predicted):
                sale_predictions[row["transaction_id"]][label] = round(float(value), 2)
            print(f"  {split}: MAE S${result['mae_sgd']:,.2f}; bias S${result['mean_bias_sgd']:,.2f}", flush=True)

    if not prepare_only:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        sales = list(sale_predictions.values())
        if any(any(label not in row for label, _ in WINDOWS) for row in sales):
            raise ValueError("Missing sale-level predictions for a training window")
        write_csv(REPORT_DIR / "training_window_comparison.csv", report)
        write_csv(REPORT_DIR / "sale_level_predictions.csv", sales)
        write_csv(REPORT_DIR / "paired_breakdowns.csv", paired_breakdowns(sales))
        print(f"Saved comparison, sale predictions, and paired breakdowns in {REPORT_DIR}", flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true", help="Check data and print window sizes without fitting models")
    args = parser.parse_args()
    run(prepare_only=args.prepare_only)


if __name__ == "__main__":
    main()
