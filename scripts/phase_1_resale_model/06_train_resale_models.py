"""Train resale-price models, compare them with baselines, and select one.

Models are fitted using training rows only. Recent validation MAE selects the winner;
the test split is evaluated after selection and never drives that choice.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import os
import statistics
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

try:
    import duckdb
    import joblib
    import numpy as np
    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import GradientBoostingRegressor, HistGradientBoostingRegressor, RandomForestRegressor
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler
except ImportError as error:
    raise SystemExit(
        "Required packages are missing. Run .\\scripts\\phase_1_resale_model\\06_run_resale_models.ps1 to install them."
    ) from error

try:
    from catboost import CatBoostRegressor
    CATBOOST_IMPORT_ERROR = None
except (ImportError, OSError) as error:
    CatBoostRegressor = None
    CATBOOST_IMPORT_ERROR = f"{type(error).__name__}: {error}"


MODEL_SPECS = {
    "ML_RIDGE_RESALE_PRICE_V1": {
        "name": "ridge_resale_price_v1",
        "description": "Ridge regression with one-hot categories and engineered property features.",
        "artifact": "06_ridge_resale_price.joblib",
    },
    "ML_GRADIENT_BOOSTING_RESALE_PRICE_V1": {
        "name": "gradient_boosting_resale_price_v1",
        "description": "Huber gradient boosting regression with the same engineered features.",
        "artifact": "06_gradient_boosting_resale_price.joblib",
    },
    "ML_SKLEARN_QUANTILE_P50_V1": {
        "name": "sklearn_quantile_p50_v1",
        "description": "Three histogram-based gradient boosting quantile regressors with early stopping; P50 is the point estimate and P10/P90 form an estimated range.",
        "artifact": "06_sklearn_quantiles_resale_price.joblib",
    },
    "ML_RANDOM_FOREST_SQUARED_ERROR_V1": {
        "name": "random_forest_squared_error_v1",
        "description": "Random forest using squared-error splits and mean leaf predictions, with 80 trees; evaluated by validation MAE.",
        "artifact": "06_random_forest_squared_error_resale_price.joblib",
    },
}
if CatBoostRegressor is not None:
    MODEL_SPECS.update({
        "ML_CATBOOST_RESALE_PRICE_V1": {
            "name": "catboost_resale_price_v1",
            "description": "CatBoost regression with native categorical feature handling and MAE loss.",
            "artifact": "06_catboost_resale_price.cbm",
        },
        "ML_CATBOOST_QUANTILE_P50_V1": {
            "name": "catboost_quantile_p50_v1",
            "description": "CatBoost multi-quantile model; P50 is the point estimate and P10/P90 form an estimated range.",
            "artifact": "06_catboost_quantiles_resale_price.cbm",
        },
    })
KNOWN_ML_MODEL_IDS = (
    "ML_RIDGE_RESALE_PRICE_V1",
    "ML_GRADIENT_BOOSTING_RESALE_PRICE_V1",
    "ML_SKLEARN_QUANTILE_P50_V1",
    "ML_RANDOM_FOREST_SQUARED_ERROR_V1",
    "ML_CATBOOST_RESALE_PRICE_V1",
    "ML_CATBOOST_QUANTILE_P50_V1",
)
OBSOLETE_MODEL_IDS = ("ML_RANDOM_FOREST_ABSOLUTE_ERROR_V1",)
BASELINE_NAMES = {
    "BASELINE_RECENT_FLAT_TYPE_24M_V1": "recent_flat_type_median_24m",
    "BASELINE_COMPARABLE_SALES_V1": "comparable_sales_v1",
    "BASELINE_COMPARABLE_RECENCY_WEIGHTED_V1": "comparable_sales_recency_weighted_v1",
}
PREDICTION_COLUMNS = [
    "prediction_id", "model_id", "transaction_id", "dataset_split", "prediction_type",
    "predicted_price", "actual_price", "absolute_error", "absolute_percentage_error",
    "lower_estimate", "upper_estimate", "comparable_count", "comparable_tier", "valuation_month",
]
CAT_COLUMNS = ["town", "flat_type", "flat_model"]
CATBOOST_EARLY_STOPPING_ROUNDS = 50
AMENITIES = [
    "primary_schools", "childcare_centres", "healthcare_clinics", "polyclinics",
    "hospitals", "community_clubs", "hawker_centres", "mrt_lrt_stations",
    "parks", "public_libraries",
]
CORE_NUMERIC = [
    "floor_area_sqm", "storey_midpoint", "remaining_lease_months",
    "building_age_at_transaction", "max_floor_lvl", "total_dwelling_units",
    "commercial", "market_hawker", "multistorey_carpark", "precinct_pavilion",
    "latitude", "longitude", "transaction_year", "relative_storey",
    "building_age_squared", "log_nearest_mrt_m", "mrt_within_400m",
    "mrt_within_800m", "month_sin", "month_cos",
]
for amenity in AMENITIES:
    CORE_NUMERIC += [f"nearest_{amenity}_m", f"{amenity}_within_1km"]


def stable_id(prefix: str, *parts: object) -> str:
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), default=str)
    return f"{prefix}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:20]}"


def as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def read_transactions(database: Path) -> list[dict]:
    with duckdb.connect(str(database), read_only=True) as connection:
        cursor = connection.execute("SELECT * FROM transaction_features ORDER BY transaction_month, transaction_id")
        names = [item[0] for item in cursor.description]
        rows = [dict(zip(names, row)) for row in cursor.fetchall()]
    if not rows:
        raise ValueError("transaction_features is empty; run the DuckDB build first.")
    engineered_columns = {
        "relative_storey", "building_age_squared", "log_nearest_mrt_m",
        "mrt_within_400m", "mrt_within_800m", "month_sin", "month_cos",
    }
    needed = (set(CORE_NUMERIC) - engineered_columns) | set(CAT_COLUMNS) | {
        "transaction_id", "block_id", "transaction_month", "dataset_split",
        "resale_price", "storey_range",
    }
    missing = needed - set(rows[0])
    if missing:
        raise ValueError(f"transaction_features is missing required columns: {', '.join(sorted(missing))}")
    return rows


def feature_records(rows: list[dict], train_flat_types: list[str]) -> tuple[list[str], list[list[object]]]:
    numeric_names = CORE_NUMERIC + [
        *[f"area_x_{flat_type}" for flat_type in train_flat_types],
        *[f"lease_x_{flat_type}" for flat_type in train_flat_types],
    ]
    output = []
    for row in rows:
        month = as_date(row["transaction_month"])
        max_floor = float(row["max_floor_lvl"] or 0)
        storey = float(row["storey_midpoint"] or 0)
        nearest_mrt_value = row["nearest_mrt_lrt_stations_m"]
        nearest_mrt = float(nearest_mrt_value) if nearest_mrt_value is not None else float("nan")
        vector: list[object] = []
        for name in CORE_NUMERIC:
            if name == "transaction_year":
                value = month.year
            elif name == "relative_storey":
                value = storey / max_floor if max_floor > 0 else 0.0
            elif name == "building_age_squared":
                age = float(row["building_age_at_transaction"] or 0)
                value = age * age
            elif name == "log_nearest_mrt_m":
                value = math.log1p(max(0.0, nearest_mrt)) if math.isfinite(nearest_mrt) else float("nan")
            elif name == "mrt_within_400m":
                value = float(nearest_mrt <= 400) if math.isfinite(nearest_mrt) else float("nan")
            elif name == "mrt_within_800m":
                value = float(nearest_mrt <= 800) if math.isfinite(nearest_mrt) else float("nan")
            elif name == "month_sin":
                value = math.sin(2 * math.pi * month.month / 12)
            elif name == "month_cos":
                value = math.cos(2 * math.pi * month.month / 12)
            else:
                raw = row[name]
                value = float(raw) if raw is not None else float("nan")
                if name in {"commercial", "market_hawker", "multistorey_carpark", "precinct_pavilion"}:
                    value = float(bool(raw))
            vector.append(value)
        area = float(row["floor_area_sqm"] or 0)
        vector.extend(area if row["flat_type"] == flat_type else 0.0 for flat_type in train_flat_types)
        lease = float(row["remaining_lease_months"] or 0)
        vector.extend(lease if row["flat_type"] == flat_type else 0.0 for flat_type in train_flat_types)
        vector.extend(str(row[name] or "Unknown") for name in CAT_COLUMNS)
        output.append(vector)
    return numeric_names + CAT_COLUMNS, output


COMPARABLE_FEATURE = "comparable_price_sgd"


def baseline_aware_features(rows: list[dict], train_flat_types: list[str]) -> tuple[list[str], list[list[object]]]:
    """Add the earlier-sales estimate to the ordinary property features."""
    names, vectors = feature_records(rows, train_flat_types)
    insert_at = len(names) - len(CAT_COLUMNS)
    for row, vector in zip(rows, vectors):
        anchor = row.get(COMPARABLE_FEATURE)
        vector.insert(insert_at, float(anchor) if anchor is not None else float("nan"))
    names.insert(insert_at, COMPARABLE_FEATURE)
    return names, vectors


def historical_training_anchors(
    project_root: Path, train: list[dict], comparable_model_id: str, *, progress: bool = False,
) -> dict[str, float]:
    """Use only earlier months, including for training transactions."""
    path = project_root / "scripts/phase_1_resale_model/04_build_baselines.py"
    spec = importlib.util.spec_from_file_location("training_baselines", path)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    by_month = defaultdict(list)
    for row in train:
        by_month[as_date(row["transaction_month"])].append(row)
    history = baseline.ComparableHistory()
    anchors = {}
    months = sorted(by_month)
    processed = 0
    for month_index, month in enumerate(months, start=1):
        for row in by_month[month]:
            candidates, _ = baseline.select_comparables(row, history)
            if candidates:
                anchors[row["transaction_id"]] = float(baseline.comparable_price_prediction(
                    comparable_model_id, as_date(row["transaction_month"]), candidates,
                )[0])
        history.add(by_month[month])
        processed += len(by_month[month])
        if progress and (month_index % 12 == 0 or month_index == len(months)):
            print(f"Comparable anchor progress: {processed:,}/{len(train):,} training sales.", flush=True)
    return anchors


def predict_with_artifact(artifact_path: Path, model_id: str, feature_columns: list[str], matrix: np.ndarray) -> np.ndarray:
    """Run the selected trained model using the artifact format saved in step 06."""
    if COMPARABLE_FEATURE not in feature_columns:
        raise ValueError("Model predates comparable-aware training; rerun Phase 1 steps 06–09")
    anchor = np.asarray(matrix[:, feature_columns.index(COMPARABLE_FEATURE)], dtype=float)
    if not np.isfinite(anchor).all() or (anchor <= 0).any():
        raise ValueError("An earlier comparable-sales estimate is required for every prediction")
    if model_id.startswith("ML_CATBOOST"):
        if CatBoostRegressor is None:
            raise RuntimeError("CatBoost is required to load the released model; install project requirements and rerun step 06")
        estimator = CatBoostRegressor()
        estimator.load_model(str(artifact_path))
        raw_values = np.asarray(estimator.predict(matrix), dtype=float)
        if "QUANTILE_P50" in model_id:
            if raw_values.ndim != 2 or raw_values.shape[1] != 3:
                raise ValueError("CatBoost quantile artifact did not return P10/P50/P90 predictions")
            values = np.sort(raw_values, axis=1)[:, 1]
        else:
            values = raw_values.reshape(-1)
    else:
        artifact = joblib.load(artifact_path)
        if artifact.get("feature_columns") != feature_columns:
            raise ValueError("Feature schema differs from the selected model artifact; rerun step 06")
        if "models" in artifact:
            values = np.sort(np.column_stack([
                np.asarray(artifact["models"][label].predict(matrix), dtype=float).reshape(-1)
                for label in ("p10", "p50", "p90")
            ]), axis=1)[:, 1]
        else:
            values = np.asarray(artifact["pipeline"].predict(matrix), dtype=float).reshape(-1)
    if len(values) != len(matrix) or not np.isfinite(values).all():
        raise ValueError(f"Selected model {model_id} returned invalid predictions")
    return np.maximum(anchor + values, 1.0)


def build_pipeline(model_kind: str, numeric_indices: list[int], categorical_indices: list[int]) -> Pipeline:
    numeric = Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
    ])
    categorical = Pipeline([
        ("impute", SimpleImputer(strategy="most_frequent")),
        ("onehot", OneHotEncoder(handle_unknown="ignore", min_frequency=10, sparse_output=False)),
    ])
    prep = ColumnTransformer(
        [("numeric", numeric, numeric_indices), ("categorical", categorical, categorical_indices)],
        remainder="drop",
        sparse_threshold=0.0,
    )
    if model_kind == "ridge":
        estimator = Ridge(alpha=20.0)
    elif model_kind == "random_forest_squared_error":
        estimator = RandomForestRegressor(
            n_estimators=80, criterion="squared_error", max_features=0.6,
            min_samples_leaf=20, max_depth=16, max_samples=0.8,
            n_jobs=5, random_state=42, verbose=1,
        )
    elif model_kind.startswith("quantile_"):
        alpha = float(model_kind.removeprefix("quantile_"))
        estimator = HistGradientBoostingRegressor(
            loss="quantile", quantile=alpha, max_iter=220, learning_rate=0.04,
            max_leaf_nodes=8, min_samples_leaf=15, random_state=42,
            early_stopping=True, validation_fraction=0.1,
            n_iter_no_change=15, tol=1e-4, verbose=2,
        )
    else:
        estimator = GradientBoostingRegressor(
            loss="huber", n_estimators=220, learning_rate=0.04, max_depth=3,
            min_samples_leaf=15, random_state=42, verbose=1,
        )
    return Pipeline([("features", prep), ("model", estimator)])


def metrics(predictions: list[dict]) -> dict:
    actual = [float(row["actual_price"]) for row in predictions]
    predicted = [float(row["predicted_price"]) for row in predictions]
    errors = [prediction - target for prediction, target in zip(predicted, actual)]
    absolute = [abs(error) for error in errors]
    ape = [value / target * 100 for value, target in zip(absolute, actual)]
    mean_actual = statistics.mean(actual)
    total = sum((value - mean_actual) ** 2 for value in actual)
    residual = sum(error * error for error in errors)
    bounded = [row for row in predictions if row.get("lower_estimate") is not None and row.get("upper_estimate") is not None]
    return {
        "prediction_count": len(predictions),
        "mae": statistics.mean(absolute),
        "median_absolute_error": statistics.median(absolute),
        "mape_pct": statistics.mean(ape),
        "median_ape_pct": statistics.median(ape),
        "rmse": math.sqrt(statistics.mean(error * error for error in errors)),
        "r_squared": 1 - residual / total if total else None,
        "mean_bias": statistics.mean(errors),
        "interval_coverage_pct": (
            sum(float(row["lower_estimate"]) <= float(row["actual_price"]) <= float(row["upper_estimate"]) for row in bounded)
            / len(bounded) * 100 if bounded else None
        ),
        "mean_interval_width": (
            statistics.mean(float(row["upper_estimate"]) - float(row["lower_estimate"]) for row in bounded)
            if bounded else None
        ),
    }


def prediction_rows(
    model_id: str, rows: list[dict], values: list[float],
    lower_values: list[float] | None = None, upper_values: list[float] | None = None,
) -> list[dict]:
    output = []
    if lower_values is None:
        lower_values = [None] * len(rows)
    if upper_values is None:
        upper_values = [None] * len(rows)
    for row, value, lower, upper in zip(rows, values, lower_values, upper_values):
        actual = float(row["resale_price"])
        predicted = max(1.0, float(value))
        absolute_error = abs(predicted - actual)
        output.append({
            "prediction_id": stable_id("PRED", model_id, row["transaction_id"]),
            "model_id": model_id,
            "transaction_id": row["transaction_id"],
            "dataset_split": row["dataset_split"],
            "prediction_type": "historical_evaluation",
            "predicted_price": round(predicted, 2),
            "actual_price": actual,
            "absolute_error": round(absolute_error, 2),
            "absolute_percentage_error": round(absolute_error / actual * 100, 4),
            "lower_estimate": round(max(1.0, float(lower)), 2) if lower is not None else None,
            "upper_estimate": round(max(predicted, float(upper)), 2) if upper is not None else None,
            "comparable_count": None,
            "comparable_tier": None,
            "valuation_month": as_date(row["transaction_month"]),
        })
    return output


def band_price(value: float, cut_points: tuple[float, float, float]) -> str:
    first, second, third = cut_points
    if value <= first:
        return f"<= ${first:,.0f} (training Q1)"
    if value <= second:
        return f"${first:,.0f}–${second:,.0f} (training Q2)"
    if value <= third:
        return f"${second:,.0f}–${third:,.0f} (training Q3)"
    return f"> ${third:,.0f} (training Q4)"


def error_analysis(all_predictions: list[dict], transaction_lookup: dict[str, dict], cut_points: tuple[float, float, float]) -> list[dict]:
    groups: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)
    for prediction in all_predictions:
        transaction = transaction_lookup[prediction["transaction_id"]]
        lease = int(transaction["remaining_lease_months"])
        lease_band = "<60 years" if lease < 720 else "60–69 years" if lease < 840 else "70–79 years" if lease < 960 else "80+ years"
        dimensions = {
            "flat_type": str(transaction["flat_type"]),
            "actual_price_band": band_price(float(transaction["resale_price"]), cut_points),
            "transaction_year": str(as_date(transaction["transaction_month"]).year),
            "storey_range": str(transaction["storey_range"]),
            "remaining_lease_band": lease_band,
        }
        for dimension, group in dimensions.items():
            groups[(prediction["model_id"], prediction["dataset_split"], dimension, group)].append(prediction)
    rows = []
    for (model_id, split, dimension, group), predictions in sorted(groups.items()):
        calculated = metrics(predictions)
        rows.append({
            "model_id": model_id,
            "dataset_split": split,
            "dimension": dimension,
            "group": group,
            **{key: round(value, 4) if value is not None else None for key, value in calculated.items()},
        })
    return rows


def read_baselines(path: Path) -> list[dict]:
    if not path.is_file():
        raise ValueError(f"Baseline predictions are missing: {path}. Run 04_run_baselines.ps1 first.")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows = [row for row in rows if row["dataset_split"] in {"validation", "test"}]
    if not rows:
        raise ValueError("Baseline predictions file contains no validation/test rows.")
    for row in rows:
        for numeric in ("predicted_price", "actual_price", "absolute_error", "absolute_percentage_error"):
            row[numeric] = float(row[numeric])
        for bound in ("lower_estimate", "upper_estimate"):
            value = row.get(bound)
            row[bound] = float(value) if value not in (None, "", "None") else None
        row["comparable_count"] = int(row["comparable_count"]) if row.get("comparable_count") not in (None, "", "None") else None
        row["valuation_month"] = as_date(row["valuation_month"])
    return rows


def select_validation_model(model_predictions: list[dict], metric_rows: list[dict], artifacts: dict[str, str]) -> dict:
    """Select on recent validation sales, where market drift matters most."""
    validation = [row for row in model_predictions if row["dataset_split"] == "validation"]
    months = sorted({as_date(row["valuation_month"]) for row in validation})
    if len(months) < 2:
        raise ValueError("At least two validation months are required for recent-period model selection")
    recent_start = months[len(months) // 2]
    recent = defaultdict(list)
    recent_ids = defaultdict(set)
    for row in validation:
        if as_date(row["valuation_month"]) >= recent_start:
            recent[row["model_id"]].append(abs(float(row["predicted_price"]) - float(row["actual_price"])))
            recent_ids[row["model_id"]].add(row["transaction_id"])
    overall = {
        row["model_id"]: row for row in metric_rows
        if row["dataset_split"] == "validation" and row["model_id"] in artifacts
    }
    if set(recent) != set(artifacts) or set(overall) != set(artifacts):
        raise ValueError("Saved validation predictions or metrics do not cover every fitted model")
    if len({frozenset(ids) for ids in recent_ids.values()}) != 1 or any(
        len(recent[model_id]) != len(recent_ids[model_id]) for model_id in artifacts
    ):
        raise ValueError("Fitted models have different or duplicate recent-validation sales")
    selected_id = min(artifacts, key=lambda model_id: (
        statistics.mean(recent[model_id]), float(overall[model_id]["mae"]), model_id,
    ))
    selected = overall[selected_id]
    recent_mae = statistics.mean(recent[selected_id])
    return {
        "model_id": selected_id, "model_name": selected["model_name"],
        "validation_mae": float(selected["mae"]), "validation_rmse": float(selected["rmse"]),
        "recent_validation_mae": round(recent_mae, 4),
        "recent_validation_start_month": recent_start.isoformat(),
        "selection_metric": "recent_validation_mae",
        "artifact_path": artifacts[selected_id],
        "selection_reason": (
            "Lowest MAE in the latest half of validation months; overall validation MAE breaks ties. "
            "Test data was not used by the selection rule."
        ),
    }


def atomic_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8-sig", newline="", dir=path.parent, suffix=".tmp", delete=False) as handle:
        temp = Path(handle.name)
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def upsert_database(
    database: Path, registry_rows: list[tuple], predictions: list[dict], metric_rows: list[dict],
    error_rows: list[dict], quantile_rows: list[dict], selection: dict, selected_at: str,
) -> None:
    model_ids = tuple([*KNOWN_ML_MODEL_IDS, *OBSOLETE_MODEL_IDS, *BASELINE_NAMES])
    marks = ",".join("?" for _ in model_ids)
    ml_model_ids = (*KNOWN_ML_MODEL_IDS, *OBSOLETE_MODEL_IDS)
    ml_marks = ",".join("?" for _ in ml_model_ids)
    prediction_values = [tuple(row[column] for column in PREDICTION_COLUMNS) for row in predictions]
    metric_values = [(
        row["model_id"], row["dataset_split"], row["prediction_count"], row["mae"],
        row["median_absolute_error"], row["mape_pct"], row["median_ape_pct"], row["rmse"],
        row["r_squared"], row["mean_bias"], row["interval_coverage_pct"], row["mean_interval_width"],
    ) for row in metric_rows]
    error_values = [tuple(row.values()) for row in error_rows]
    with duckdb.connect(str(database)) as connection:
        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute("""CREATE TABLE IF NOT EXISTS model_evaluation_metrics (
                model_id VARCHAR, dataset_split VARCHAR, prediction_count INTEGER, mae DOUBLE,
                median_absolute_error DOUBLE, mape_pct DOUBLE, median_ape_pct DOUBLE,
                rmse DOUBLE, r_squared DOUBLE, mean_bias DOUBLE,
                interval_coverage_pct DOUBLE, mean_interval_width DOUBLE,
                PRIMARY KEY (model_id, dataset_split))""")
            connection.execute("ALTER TABLE model_evaluation_metrics ADD COLUMN IF NOT EXISTS interval_coverage_pct DOUBLE")
            connection.execute("ALTER TABLE model_evaluation_metrics ADD COLUMN IF NOT EXISTS mean_interval_width DOUBLE")
            connection.execute("""CREATE TABLE IF NOT EXISTS model_error_analysis (
                model_id VARCHAR, dataset_split VARCHAR, dimension VARCHAR, group_label VARCHAR,
                prediction_count INTEGER, mae DOUBLE, median_absolute_error DOUBLE,
                mape_pct DOUBLE, median_ape_pct DOUBLE, rmse DOUBLE, r_squared DOUBLE,
                mean_bias DOUBLE, interval_coverage_pct DOUBLE, mean_interval_width DOUBLE,
                PRIMARY KEY (model_id, dataset_split, dimension, group_label))""")
            connection.execute("ALTER TABLE model_error_analysis ADD COLUMN IF NOT EXISTS interval_coverage_pct DOUBLE")
            connection.execute("ALTER TABLE model_error_analysis ADD COLUMN IF NOT EXISTS mean_interval_width DOUBLE")
            connection.execute("""CREATE TABLE IF NOT EXISTS quantile_price_ranges (
                transaction_id VARCHAR, dataset_split VARCHAR, model_id VARCHAR,
                lower_price_p10 DOUBLE, median_price_p50 DOUBLE, upper_price_p90 DOUBLE,
                actual_price DOUBLE, interval_covered BOOLEAN, interval_width DOUBLE,
                valuation_month DATE, PRIMARY KEY (transaction_id, model_id))""")
            connection.execute("""CREATE TABLE IF NOT EXISTS selected_model (
                selection_id VARCHAR PRIMARY KEY, selected_model_id VARCHAR, selection_metric VARCHAR,
                validation_mae DOUBLE, validation_rmse DOUBLE, selected_at_utc VARCHAR,
                artifact_path VARCHAR, selection_reason VARCHAR)""")
            connection.execute(f"DELETE FROM model_predictions WHERE model_id IN ({marks})", model_ids)
            connection.execute(f"DELETE FROM model_registry WHERE model_id IN ({ml_marks})", ml_model_ids)
            connection.execute(f"DELETE FROM model_evaluation_metrics WHERE model_id IN ({marks})", model_ids)
            connection.execute(f"DELETE FROM model_error_analysis WHERE model_id IN ({marks})", model_ids)
            connection.execute(f"DELETE FROM quantile_price_ranges WHERE model_id IN ({marks})", model_ids)
            connection.execute("DELETE FROM selected_model")
            connection.executemany("INSERT INTO model_registry VALUES (?, ?, ?, ?, ?, ?)", registry_rows)
            connection.executemany("INSERT INTO model_predictions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", prediction_values)
            connection.executemany("""INSERT INTO model_evaluation_metrics (
                model_id, dataset_split, prediction_count, mae, median_absolute_error,
                mape_pct, median_ape_pct, rmse, r_squared, mean_bias,
                interval_coverage_pct, mean_interval_width
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", metric_values)
            connection.executemany("""INSERT INTO model_error_analysis (
                model_id, dataset_split, dimension, group_label, prediction_count, mae,
                median_absolute_error, mape_pct, median_ape_pct, rmse, r_squared,
                mean_bias, interval_coverage_pct, mean_interval_width
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", error_values)
            connection.executemany("""INSERT INTO quantile_price_ranges VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )""", [tuple(row.values()) for row in quantile_rows])
            connection.execute("INSERT INTO selected_model VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
                stable_id("SELECTED", selected_at), selection["model_id"], selection["selection_metric"],
                selection["validation_mae"], selection["validation_rmse"], selected_at,
                selection["artifact_path"], selection["selection_reason"],
            ])
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise


def reselect_saved_models(project_root: Path) -> dict:
    """Reapply the validation-only selection rule without fitting models again."""
    report_path = project_root / "reports/phase_1_resale_model/06_model_selection.json"
    prediction_path = project_root / "data/model_ready/06_ml_predictions.csv"
    if not report_path.is_file() or not prediction_path.is_file():
        raise ValueError("Saved Step 06 results are missing; run full model training first")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("target") != "residual_to_earlier_comparable_price":
        raise ValueError("Saved models use an older target; run full model training first")
    artifacts = report.get("artifacts", {})
    if not artifacts or any(not Path(path).is_file() for path in artifacts.values()):
        raise ValueError("A fitted model artifact is missing; run full model training first")
    with prediction_path.open(encoding="utf-8-sig", newline="") as handle:
        predictions = list(csv.DictReader(handle))
    selection = select_validation_model(predictions, report["metrics"], artifacts)
    report["selected_model"] = selection
    report["selected_model_test_metrics"] = next(
        row for row in report["metrics"]
        if row["model_id"] == selection["model_id"] and row["dataset_split"] == "test"
    )
    report["selection_rule"] = "lowest MAE in latest half of validation months; overall validation MAE tie-breaker; test is report-only"
    selected_at = datetime.now(timezone.utc).isoformat()
    database = project_root / "data/property.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute("DELETE FROM selected_model")
            connection.execute("INSERT INTO selected_model VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
                stable_id("SELECTED", selected_at), selection["model_id"], selection["selection_metric"],
                selection["validation_mae"], selection["validation_rmse"], selected_at,
                selection["artifact_path"], selection["selection_reason"],
            ])
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Selected {selection['model_id']} from recent validation MAE, without retraining.", flush=True)
    return report


def run(project_root: Path, skip_catboost: bool = False) -> dict:
    database = project_root / "data" / "property.duckdb"
    print("Loading transactions and baseline predictions...", flush=True)
    rows = read_transactions(database)
    train = [row for row in rows if row["dataset_split"] == "train"]
    validation = [row for row in rows if row["dataset_split"] == "validation"]
    test = [row for row in rows if row["dataset_split"] == "test"]
    if not train or not validation or not test:
        raise ValueError("Training, validation, and test splits are all required.")
    baseline_predictions = read_baselines(project_root / "data" / "model_ready" / "baseline_predictions.csv")
    baseline_report_path = project_root / "reports/phase_1_resale_model/04_baseline_metrics.json"
    baseline_report = json.loads(baseline_report_path.read_text(encoding="utf-8"))
    comparable_model_id = baseline_report["selected_comparable_model_id"]
    if comparable_model_id not in BASELINE_NAMES or comparable_model_id == "BASELINE_RECENT_FLAT_TYPE_24M_V1":
        raise ValueError("Step 04 did not select a supported comparable-sales method")
    print(
        f"Loaded {len(rows):,} transactions: {len(train):,} train, "
        f"{len(validation):,} validation, {len(test):,} test.",
        flush=True,
    )
    print("Calculating earlier-month comparable prices for training...", flush=True)
    print(f"Using comparable price method: {comparable_model_id}", flush=True)
    anchors = historical_training_anchors(project_root, train, comparable_model_id, progress=True)
    baseline_anchors = {
        row["transaction_id"]: row for row in baseline_predictions
        if row["model_id"] == comparable_model_id
    }
    for split, split_transactions in (("validation", validation), ("test", test)):
        if {row["transaction_id"] for row in split_transactions} != {
            key for key, prediction in baseline_anchors.items() if prediction["dataset_split"] == split
        }:
            raise ValueError(f"Comparable baseline does not cover the {split} split exactly; rerun step 04")
        for row in split_transactions:
            saved = baseline_anchors[row["transaction_id"]]
            if abs(saved["actual_price"] - float(row["resale_price"])) > .01 or saved["valuation_month"] != as_date(row["transaction_month"]):
                raise ValueError("Comparable baseline is stale; rerun step 04")
            anchors[row["transaction_id"]] = saved["predicted_price"]
    for row in rows:
        row[COMPARABLE_FEATURE] = anchors.get(row["transaction_id"])
    eligible_train = [row for row in train if row[COMPARABLE_FEATURE] is not None]
    if not eligible_train:
        raise ValueError("No training transactions have earlier comparable sales")
    print(f"Comparable anchors available for {len(eligible_train):,}/{len(train):,} training sales.", flush=True)
    y_train = np.asarray([float(row["resale_price"]) - row[COMPARABLE_FEATURE] for row in eligible_train], dtype=float)
    y_validation = np.asarray([float(row["resale_price"]) - row[COMPARABLE_FEATURE] for row in validation], dtype=float)
    flat_types = sorted({str(row["flat_type"]) for row in train})
    print("Building model features...", flush=True)
    column_names, all_x = baseline_aware_features(rows, flat_types)
    matrix = np.asarray(all_x, dtype=object)
    print(f"Prepared {len(column_names):,} features for {len(rows):,} transactions.", flush=True)
    name_to_index = {name: index for index, name in enumerate(column_names)}
    numeric_indices = [name_to_index[name] for name in column_names if name not in CAT_COLUMNS]
    categorical_indices = [name_to_index[name] for name in CAT_COLUMNS]
    row_by_id = {row["transaction_id"]: row for row in rows}
    split_rows = {"validation": validation, "test": test}
    model_predictions: list[dict] = []
    quantile_rows: list[dict] = []
    artifact_dir = project_root / "models/phase_1_resale_model"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    built_at = datetime.now(timezone.utc).isoformat()
    registry_rows = []
    artifacts = {}
    active_model_specs = {
        model_id: spec for model_id, spec in MODEL_SPECS.items()
        if not (skip_catboost and model_id.startswith("ML_CATBOOST"))
    }

    train_indices = [index for index, row in enumerate(rows) if row["dataset_split"] == "train" and row[COMPARABLE_FEATURE] is not None]
    validation_indices = [index for index, row in enumerate(rows) if row["dataset_split"] == "validation"]
    eval_indices = [index for index, row in enumerate(rows) if row["dataset_split"] in {"validation", "test"}]
    eval_matrix = matrix[eval_indices]
    eval_anchors = np.asarray([rows[index][COMPARABLE_FEATURE] for index in eval_indices], dtype=float)
    total_models = len(active_model_specs)
    for model_index, (model_id, spec) in enumerate(active_model_specs.items(), start=1):
        is_catboost = model_id.startswith("ML_CATBOOST")
        is_quantile = "QUANTILE_P50" in model_id
        print(
            f"Model {model_index}/{total_models}: fitting {spec['name']} "
            f"({len(eligible_train):,} training rows)...",
            flush=True,
        )
        if is_catboost:
            print(
                f"  CatBoost: validation-based early stopping after "
                f"{CATBOOST_EARLY_STOPPING_ROUNDS} rounds without improvement "
                "(650-iteration maximum).",
                flush=True,
            )
            estimator = CatBoostRegressor(
                iterations=650, depth=6, learning_rate=0.04, l2_leaf_reg=5,
                loss_function=("MultiQuantile:alpha=0.1,0.5,0.9" if is_quantile else "MAE"),
                random_seed=42, thread_count=5, verbose=100, allow_writing_files=False,
            )
            estimator.fit(
                matrix[train_indices], y_train,
                cat_features=categorical_indices,
                eval_set=(matrix[validation_indices], y_validation),
                early_stopping_rounds=CATBOOST_EARLY_STOPPING_ROUNDS,
                use_best_model=True,
            )
            best_iteration = estimator.get_best_iteration()
            if best_iteration >= 0:
                print(
                    f"  CatBoost: retained {estimator.tree_count_} trees; "
                    f"best validation iteration was {best_iteration + 1}.",
                    flush=True,
                )
            raw_prediction = np.asarray(estimator.predict(eval_matrix))
            artifact_path = artifact_dir / spec["artifact"]
            estimator.save_model(str(artifact_path))
            if is_quantile:
                if raw_prediction.ndim != 2 or raw_prediction.shape[1] != 3:
                    raise ValueError(f"Expected three quantiles from CatBoost, got shape {raw_prediction.shape}")
                sorted_quantiles = np.sort(raw_prediction, axis=1)
                lower_values, all_values, upper_values = sorted_quantiles.T + eval_anchors
                bounds = (lower_values, upper_values)
            else:
                all_values = raw_prediction.reshape(-1) + eval_anchors
                bounds = None
        elif is_quantile:
            quantile_models = {}
            quantile_values = {}
            for label, alpha in (("p10", 0.1), ("p50", 0.5), ("p90", 0.9)):
                print(f"  Quantile {label}: fitting ({alpha:.0%})...", flush=True)
                quantile_model = build_pipeline(f"quantile_{alpha}", numeric_indices, categorical_indices)
                quantile_model.fit(matrix[train_indices], y_train)
                quantile_models[label] = quantile_model
                quantile_values[label] = quantile_model.predict(eval_matrix) + eval_anchors
                print(f"  Quantile {label}: fit and prediction complete.", flush=True)
            ordered_quantiles = np.sort(np.column_stack((
                quantile_values["p10"], quantile_values["p50"], quantile_values["p90"],
            )), axis=1)
            bounds = (ordered_quantiles[:, 0], ordered_quantiles[:, 2])
            all_values = ordered_quantiles[:, 1]
            artifact_path = artifact_dir / spec["artifact"]
            joblib.dump({"models": quantile_models, "feature_columns": column_names, "model_id": model_id,
                         "target": "residual_to_comparable"}, artifact_path)
        else:
            if model_id.startswith("ML_RIDGE"):
                kind = "ridge"
            elif model_id == "ML_RANDOM_FOREST_SQUARED_ERROR_V1":
                kind = "random_forest_squared_error"
            else:
                kind = "gradient_boosting"
            estimator = build_pipeline(kind, numeric_indices, categorical_indices)
            estimator.fit(matrix[train_indices], y_train)
            all_values = estimator.predict(eval_matrix) + eval_anchors
            bounds = None
            artifact_path = artifact_dir / spec["artifact"]
            joblib.dump({"pipeline": estimator, "feature_columns": column_names, "model_id": model_id,
                         "target": "residual_to_comparable"}, artifact_path)
        artifacts[model_id] = str(artifact_path)
        print(f"Model {model_index}/{total_models}: fit complete; evaluating validation and test rows...", flush=True)
        pred_pos = 0
        for split in ("validation", "test"):
            count = len(split_rows[split])
            these_rows = split_rows[split]
            these_values = all_values[pred_pos:pred_pos + count]
            if bounds is not None:
                these_lower = bounds[0][pred_pos:pred_pos + count]
                these_upper = bounds[1][pred_pos:pred_pos + count]
            else:
                these_lower = these_upper = None
            new_predictions = prediction_rows(model_id, these_rows, these_values, these_lower, these_upper)
            model_predictions.extend(new_predictions)
            if is_quantile:
                for predicted in new_predictions:
                    covered = predicted["lower_estimate"] <= predicted["actual_price"] <= predicted["upper_estimate"]
                    quantile_rows.append({
                        "transaction_id": predicted["transaction_id"],
                        "dataset_split": predicted["dataset_split"],
                        "model_id": model_id,
                        "lower_price_p10": predicted["lower_estimate"],
                        "median_price_p50": predicted["predicted_price"],
                        "upper_price_p90": predicted["upper_estimate"],
                        "actual_price": predicted["actual_price"],
                        "interval_covered": covered,
                        "interval_width": round(predicted["upper_estimate"] - predicted["lower_estimate"], 2),
                        "valuation_month": predicted["valuation_month"],
                    })
            pred_pos += count
        registry_rows.append((
            model_id, spec["name"], "machine_learning", "v1",
            f"{spec['description']} Artifact: models/phase_1_resale_model/{spec['artifact']}", built_at,
        ))
        print(f"Model {model_index}/{total_models}: predictions and artifact saved.", flush=True)

    print("Comparing model results and selecting by recent validation MAE...", flush=True)
    all_predictions = baseline_predictions + model_predictions
    prediction_ids = {prediction["transaction_id"] for prediction in baseline_predictions}
    for split, expected_rows in split_rows.items():
        actual_ids = {row["transaction_id"] for row in expected_rows}
        baseline_ids = {row["transaction_id"] for row in baseline_predictions if row["dataset_split"] == split}
        if baseline_ids != actual_ids:
            raise ValueError(f"Baseline predictions do not cover the {split} transaction set exactly.")
    del prediction_ids

    model_name = {**BASELINE_NAMES, **{key: value["name"] for key, value in MODEL_SPECS.items()}}
    # Consistent comparison metrics across both prior baselines and newly trained models.
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for prediction in all_predictions:
        groups[(prediction["model_id"], prediction["dataset_split"])].append(prediction)
    metric_rows = []
    for (model_id, split), predictions in sorted(groups.items()):
        result = metrics(predictions)
        metric_rows.append({"model_id": model_id, "model_name": model_name[model_id], "dataset_split": split,
                            **{key: round(value, 4) if value is not None else None for key, value in result.items()}})
    selection = select_validation_model(model_predictions, metric_rows, artifacts)
    selected_id = selection["model_id"]
    train_prices = [float(row["resale_price"]) for row in train]
    cut_points = tuple(float(np.quantile(train_prices, q)) for q in (0.25, 0.5, 0.75))
    error_rows = error_analysis(all_predictions, row_by_id, cut_points)
    selected_at = datetime.now(timezone.utc).isoformat()
    print("Saving predictions, metrics, and selected model to the database...", flush=True)
    upsert_database(database, registry_rows, all_predictions, metric_rows, error_rows, quantile_rows, selection, selected_at)

    model_prediction_path = project_root / "data" / "model_ready" / "06_ml_predictions.csv"
    metric_path = project_root / "reports/phase_1_resale_model" / "06_model_performance.csv"
    error_path = project_root / "reports/phase_1_resale_model" / "06_error_analysis.csv"
    report_path = project_root / "reports/phase_1_resale_model" / "06_model_selection.json"
    quantile_path = project_root / "reports/phase_1_resale_model" / "06_quantile_price_ranges.csv"
    atomic_csv(model_prediction_path, model_predictions)
    atomic_csv(metric_path, metric_rows)
    atomic_csv(error_path, error_rows)
    atomic_csv(quantile_path, quantile_rows)
    summary = {
        "status": "success", "built_at_utc": built_at,
        "target": "residual_to_earlier_comparable_price", "comparable_model_id": comparable_model_id,
        "training_rows": len(eligible_train),
        "training_rows_without_earlier_comparables": len(train) - len(eligible_train),
        "validation_rows": len(validation), "test_rows": len(test),
        "feature_count": len(column_names), "feature_columns": column_names,
        "metrics": metric_rows, "selected_model": selection,
        "selected_model_test_metrics": next(row for row in metric_rows if row["model_id"] == selected_id and row["dataset_split"] == "test"),
        "baseline_comparison_included": True,
        "error_analysis_dimensions": ["flat_type", "actual_price_band", "transaction_year", "storey_range", "remaining_lease_band"],
        "artifacts": artifacts,
        "prediction_file": str(model_prediction_path),
        "performance_file": str(metric_path),
        "error_analysis_file": str(error_path),
        "quantile_range_file": str(quantile_path),
        "quantile_interval_metrics": [row for row in metric_rows if "QUANTILE_P50" in row["model_id"]],
        "catboost_available": CatBoostRegressor is not None,
        "catboost_unavailable_reason": CATBOOST_IMPORT_ERROR,
        "catboost_skipped": bool(skip_catboost and CatBoostRegressor is not None),
        "selection_rule": "lowest MAE in latest half of validation months; overall validation MAE tie-breaker; test is report-only",
    }
    report_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Training complete. Selected model: {selection['model_name']}.", flush=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--skip-catboost", action="store_true", help="Skip optional CatBoost fits for a quicker scikit-learn comparison.")
    parser.add_argument("--reselect-only", action="store_true", help="Apply the current validation selection rule to saved Step 06 predictions without retraining.")
    args = parser.parse_args()
    try:
        result = (reselect_saved_models(args.project_root.resolve()) if args.reselect_only
                  else run(args.project_root.resolve(), skip_catboost=args.skip_catboost))
    except (ValueError, OSError, duckdb.Error, RuntimeError) as error:
        print(f"Resale model workflow failed: {error}", file=sys.stderr)
        return 1
    if args.reselect_only:
        print(json.dumps({"selected_model": result["selected_model"],
                          "selected_model_test_metrics": result["selected_model_test_metrics"]}, indent=2))
    else:
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
