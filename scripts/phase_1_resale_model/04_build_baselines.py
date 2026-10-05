"""Create repeatable, time-safe comparable-sales baselines from property.duckdb.

The script evaluates only validation and test transactions. A prediction for a
month can use transactions from earlier months, never the same or future month.
It creates two non-machine-learning baselines:

* recent_flat_type_median_24m: the median recent price for the same town and flat type.
* comparable_sales_v1: a hierarchy of recent, similar sales, starting with the
  same block and widening to nearby and town-wide comparables when needed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import statistics
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

try:
    import duckdb
except ImportError as error:
    raise SystemExit("DuckDB is required. Run the DuckDB build launcher first.") from error


MODEL_DEFINITIONS = {
    "BASELINE_RECENT_FLAT_TYPE_24M_V1": {
        "name": "recent_flat_type_median_24m",
        "description": "Median price of earlier same-town, same-flat-type sales in the prior 24 months.",
    },
    "BASELINE_COMPARABLE_SALES_V1": {
        "name": "comparable_sales_v1",
        "description": "Median of earlier comparable sales selected through a documented local-to-town hierarchy.",
    },
}
LOOKBACK_MONTHS = 24


def month_number(value: date) -> int:
    return value.year * 12 + value.month


def stable_id(prefix: str, *parts: object) -> str:
    raw = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), default=str)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        raise ValueError("Cannot calculate a percentile from no values")
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile_value
    lower, upper = math.floor(index), math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def median_prediction(records: list[dict]) -> tuple[float, float, float]:
    prices = [record["resale_price"] for record in records]
    return statistics.median(prices), percentile(prices, 0.25), percentile(prices, 0.75)


def distance_m(left: dict, right: dict) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (left["latitude"], left["longitude"], right["latitude"], right["longitude"]))
    d_lat, d_lon = lat2 - lat1, lon2 - lon1
    a = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    return 12_742_000 * math.asin(min(1.0, math.sqrt(a)))


def recent_flat_type_candidates(target: dict, history: list[dict]) -> list[dict]:
    minimum_month = month_number(target["transaction_month"]) - LOOKBACK_MONTHS
    matches = [
        record for record in history
        if month_number(record["transaction_month"]) >= minimum_month
        and record["town"] == target["town"]
        and record["flat_type"] == target["flat_type"]
    ]
    if matches:
        return matches
    return [
        record for record in history
        if record["town"] == target["town"] and record["flat_type"] == target["flat_type"]
    ]


def select_comparables(target: dict, history: list[dict]) -> tuple[list[dict], str]:
    """Return the first comparison tier with enough earlier transactions."""
    recent_cutoff = month_number(target["transaction_month"]) - LOOKBACK_MONTHS
    recent = [record for record in history if month_number(record["transaction_month"]) >= recent_cutoff]

    def similar(record: dict, *, area: float, storey: float, require_model: bool = False) -> bool:
        return (
            record["town"] == target["town"]
            and record["flat_type"] == target["flat_type"]
            and abs(record["floor_area_sqm"] - target["floor_area_sqm"]) <= area
            and abs(record["storey_midpoint"] - target["storey_midpoint"]) <= storey
            and (not require_model or record["flat_model"] == target["flat_model"])
        )

    tiers = [
        (
            "same_block_similar_24m",
            3,
            [
                record for record in recent
                if record["block_id"] == target["block_id"]
                and similar(record, area=5, storey=3, require_model=True)
            ],
        ),
        (
            "nearby_similar_24m",
            5,
            [
                record for record in recent
                if similar(record, area=10, storey=6, require_model=True)
                and distance_m(target, record) <= 1_000
            ],
        ),
        (
            "town_similar_model_24m",
            8,
            [record for record in recent if similar(record, area=15, storey=6, require_model=True)],
        ),
        (
            "town_similar_24m",
            10,
            [record for record in recent if similar(record, area=20, storey=9)],
        ),
    ]
    for tier, minimum_count, records in tiers:
        if len(records) >= minimum_count:
            return records, tier
    fallback = recent_flat_type_candidates(target, history)
    return fallback, "town_flat_type_fallback"


def load_records(connection: duckdb.DuckDBPyConnection) -> list[dict]:
    columns = (
        "transaction_id, block_id, transaction_month, dataset_split, town, flat_type, flat_model, "
        "floor_area_sqm, storey_midpoint, latitude, longitude, resale_price"
    )
    cursor = connection.execute(f"SELECT {columns} FROM transaction_features ORDER BY transaction_month, transaction_id")
    names = [description[0] for description in cursor.description]
    records = []
    for values in cursor.fetchall():
        record = dict(zip(names, values))
        record["transaction_month"] = record["transaction_month"]
        record["floor_area_sqm"] = float(record["floor_area_sqm"])
        record["storey_midpoint"] = float(record["storey_midpoint"])
        record["latitude"] = float(record["latitude"])
        record["longitude"] = float(record["longitude"])
        record["resale_price"] = float(record["resale_price"])
        records.append(record)
    if not records:
        raise ValueError("transaction_features is empty. Run the DuckDB build first.")
    return records


def generate_predictions(records: list[dict]) -> list[dict]:
    by_month: dict[date, list[dict]] = defaultdict(list)
    for record in records:
        by_month[record["transaction_month"]].append(record)

    history: list[dict] = []
    predictions: list[dict] = []
    for month in sorted(by_month):
        for target in by_month[month]:
            if target["dataset_split"] not in {"validation", "test"}:
                continue
            broad = recent_flat_type_candidates(target, history)
            if not broad:
                raise ValueError(f"No earlier broad baseline comparables for {target['transaction_id']}")
            comparable, tier = select_comparables(target, history)
            if not comparable:
                raise ValueError(f"No earlier comparable-sales records for {target['transaction_id']}")

            for model_id, candidate_records, candidate_tier in (
                ("BASELINE_RECENT_FLAT_TYPE_24M_V1", broad, "town_flat_type_24m"),
                ("BASELINE_COMPARABLE_SALES_V1", comparable, tier),
            ):
                predicted, lower, upper = median_prediction(candidate_records)
                actual = target["resale_price"]
                absolute_error = abs(predicted - actual)
                predictions.append({
                    "prediction_id": stable_id("PRED", model_id, target["transaction_id"]),
                    "model_id": model_id,
                    "transaction_id": target["transaction_id"],
                    "dataset_split": target["dataset_split"],
                    "prediction_type": "historical_evaluation",
                    "predicted_price": round(predicted, 2),
                    "actual_price": actual,
                    "absolute_error": round(absolute_error, 2),
                    "absolute_percentage_error": round(absolute_error / actual * 100, 4),
                    "lower_estimate": round(lower, 2),
                    "upper_estimate": round(upper, 2),
                    "comparable_count": len(candidate_records),
                    "comparable_tier": candidate_tier,
                    "valuation_month": target["transaction_month"].isoformat(),
                })
        # All sales in a month enter history together. The input only identifies the
        # month, so no sale may use another sale from the same month as evidence.
        history.extend(by_month[month])
    return predictions


def calculate_metrics(predictions: Iterable[dict]) -> list[dict]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for prediction in predictions:
        grouped[(prediction["model_id"], prediction["dataset_split"])].append(prediction)

    rows = []
    for (model_id, split), items in sorted(grouped.items()):
        errors = [item["absolute_error"] for item in items]
        percent_errors = [item["absolute_percentage_error"] for item in items]
        coverage = sum(item["lower_estimate"] <= item["actual_price"] <= item["upper_estimate"] for item in items) / len(items) * 100
        rows.append({
            "model_id": model_id,
            "dataset_split": split,
            "prediction_count": len(items),
            "mean_absolute_error": round(statistics.mean(errors), 2),
            "median_absolute_error": round(statistics.median(errors), 2),
            "mean_absolute_percentage_error": round(statistics.mean(percent_errors), 4),
            "median_absolute_percentage_error": round(statistics.median(percent_errors), 4),
            "interquartile_range_coverage_pct": round(coverage, 4),
        })
    return rows


def write_csv_atomically(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, suffix=".tmp", delete=False) as handle:
        temporary_path = Path(handle.name)
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    try:
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def create_or_replace_results(connection: duckdb.DuckDBPyConnection, predictions: list[dict], metrics: list[dict], built_at: str) -> None:
    model_rows = [
        (model_id, details["name"], "baseline", "v1", details["description"], built_at)
        for model_id, details in MODEL_DEFINITIONS.items()
    ]
    prediction_rows = [tuple(item.values()) for item in predictions]
    metric_rows = [tuple(item.values()) for item in metrics]
    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS model_registry (
                model_id VARCHAR PRIMARY KEY,
                model_name VARCHAR,
                model_type VARCHAR,
                model_version VARCHAR,
                description VARCHAR,
                created_at_utc VARCHAR
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS model_predictions (
                prediction_id VARCHAR PRIMARY KEY,
                model_id VARCHAR,
                transaction_id VARCHAR,
                dataset_split VARCHAR,
                prediction_type VARCHAR,
                predicted_price DOUBLE,
                actual_price DOUBLE,
                absolute_error DOUBLE,
                absolute_percentage_error DOUBLE,
                lower_estimate DOUBLE,
                upper_estimate DOUBLE,
                comparable_count INTEGER,
                comparable_tier VARCHAR,
                valuation_month DATE
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS baseline_metrics (
                model_id VARCHAR,
                dataset_split VARCHAR,
                prediction_count INTEGER,
                mean_absolute_error DOUBLE,
                median_absolute_error DOUBLE,
                mean_absolute_percentage_error DOUBLE,
                median_absolute_percentage_error DOUBLE,
                interquartile_range_coverage_pct DOUBLE,
                PRIMARY KEY (model_id, dataset_split)
            )
        """)
        model_ids = tuple(MODEL_DEFINITIONS)
        placeholders = ", ".join("?" for _ in model_ids)
        connection.execute(f"DELETE FROM model_predictions WHERE model_id IN ({placeholders})", model_ids)
        connection.execute(f"DELETE FROM baseline_metrics WHERE model_id IN ({placeholders})", model_ids)
        connection.execute(f"DELETE FROM model_registry WHERE model_id IN ({placeholders})", model_ids)
        connection.executemany("INSERT INTO model_registry VALUES (?, ?, ?, ?, ?, ?)", model_rows)
        connection.executemany("INSERT INTO model_predictions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", prediction_rows)
        connection.executemany("INSERT INTO baseline_metrics VALUES (?, ?, ?, ?, ?, ?, ?, ?)", metric_rows)
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise


def run(project_root: Path) -> dict:
    database = project_root / "data" / "property.duckdb"
    if not database.is_file():
        raise ValueError(f"Database is missing: {database}. Run 03_run_duckdb_build.ps1 first.")
    built_at = datetime.now(timezone.utc).isoformat()
    with duckdb.connect(str(database)) as connection:
        records = load_records(connection)
        predictions = generate_predictions(records)
        metrics = calculate_metrics(predictions)
        expected = sum(record["dataset_split"] in {"validation", "test"} for record in records) * len(MODEL_DEFINITIONS)
        if len(predictions) != expected:
            raise ValueError(f"Expected {expected} baseline predictions; generated {len(predictions)}")
        if any(item["predicted_price"] <= 0 or item["actual_price"] <= 0 for item in predictions):
            raise ValueError("Baseline predictions contain a nonpositive price")
        create_or_replace_results(connection, predictions, metrics, built_at)

    prediction_path = project_root / "data" / "model_ready" / "baseline_predictions.csv"
    metric_csv_path = project_root / "reports/phase_1_resale_model" / "04_baseline_metrics.csv"
    metric_json_path = project_root / "reports/phase_1_resale_model" / "04_baseline_metrics.json"
    write_csv_atomically(prediction_path, predictions)
    write_csv_atomically(metric_csv_path, metrics)
    summary = {
        "status": "success",
        "built_at_utc": built_at,
        "prediction_count": len(predictions),
        "metrics": metrics,
        "prediction_file": str(prediction_path),
        "metrics_file": str(metric_csv_path),
    }
    metric_json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    try:
        result = run(args.project_root.resolve())
    except (ValueError, OSError, duckdb.Error) as error:
        print(f"Baseline build failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
