"""Price one Singapore HDB flat with the current Ridge/comparable release."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import duckdb
import joblib
import numpy as np


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load {path}")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def normalise(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().upper())


def parse_month(value: str | None) -> date:
    if value is None:
        return date.today().replace(day=1)
    if not re.fullmatch(r"\d{4}-\d{2}", value):
        raise ValueError("Valuation month must use YYYY-MM, for example 2026-09")
    return date.fromisoformat(value + "-01")


def month_number(value: date) -> int:
    return value.year * 12 + value.month


def block_record(connection, block: str, street: str) -> dict:
    cursor = connection.execute("""
        SELECT b.block_id, b.block, b.street, b.match_status, b.latitude, b.longitude,
               p.bldg_contract_town, p.year_completed, p.max_floor_lvl,
               p.total_dwelling_units, p.commercial, p.market_hawker,
               p.multistorey_carpark, p.precinct_pavilion,
               l.* EXCLUDE (block_id),
               (SELECT mode(t.town) FROM resale_transactions t
                WHERE t.block_id = b.block_id) AS town
        FROM hdb_blocks b
        JOIN hdb_properties p USING (block_id)
        JOIN block_location_features l USING (block_id)
    """)
    names = [item[0] for item in cursor.description]
    matches = [dict(zip(names, values)) for values in cursor.fetchall()
               if normalise(str(values[1])) == normalise(block) and normalise(str(values[2])) == normalise(street)]
    if len(matches) != 1:
        raise ValueError(f"Expected one known HDB block for {block} {street}; found {len(matches)}")
    if not matches[0]["town"]:
        raise ValueError("This block has no registered resale history from which to determine its town")
    return matches[0]


def prior_block_values(connection, block_id: str, flat_type: str, month: date) -> list[dict]:
    cursor = connection.execute("""
        SELECT flat_model, lease_commence_year, transaction_month
        FROM resale_transactions
        WHERE block_id = ? AND flat_type = ? AND transaction_month < ?
        ORDER BY transaction_month DESC
    """, [block_id, flat_type, month])
    names = [item[0] for item in cursor.description]
    return [dict(zip(names, values)) for values in cursor.fetchall()]


def resolve_flat_model(value: str | None, history: list[dict], train_models: set[str]) -> tuple[str, bool]:
    if value:
        matches = [name for name in train_models if name.casefold() == value.strip().casefold()]
        if len(matches) != 1:
            raise ValueError(f"Unknown flat model {value!r}; use a model name present in the training data")
        return matches[0], False
    candidates = [row["flat_model"] for row in history if row["flat_model"] in train_models]
    if not candidates:
        raise ValueError("Flat model is required because this block has no earlier matching transaction")
    return Counter(candidates).most_common(1)[0][0], True


def remaining_lease_months(
    given_years: float | None, commencement_year: int | None,
    history: list[dict], month: date,
) -> tuple[int, int | None, bool]:
    inferred = False
    if given_years is not None:
        remaining = round(given_years * 12)
    else:
        if commencement_year is None:
            years = [int(row["lease_commence_year"]) for row in history if row["lease_commence_year"]]
            if not years:
                raise ValueError("Supply --remaining-lease-years or --lease-commence-year")
            commencement_year = Counter(years).most_common(1)[0][0]
            inferred = True
        remaining = 99 * 12 - (month_number(month) - month_number(date(commencement_year, 1, 1)))
    if remaining <= 0 or remaining > 99 * 12:
        raise ValueError("Remaining lease must be between 0 and 99 years")
    return remaining, commencement_year, inferred


def ridge_contributions(pipeline, vector: np.ndarray, feature_columns: list[str]) -> tuple[float, dict]:
    transformed = pipeline.named_steps["features"].transform(vector)
    regressor = pipeline.named_steps["model"]
    names = pipeline.named_steps["features"].get_feature_names_out()
    values = np.asarray(transformed[0], dtype=float) * np.asarray(regressor.coef_, dtype=float).reshape(-1)
    intercept = float(np.asarray(regressor.intercept_).reshape(-1)[0])
    ridge_price = max(1.0, float(pipeline.predict(vector)[0]))
    if abs((intercept + float(values.sum())) - ridge_price) > .02:
        raise ValueError("Ridge explanation does not reconstruct its estimate")
    order = sorted(range(len(values)), key=lambda i: abs(values[i]), reverse=True)
    def label(raw: str) -> str:
        match = re.match(r"(?:numeric|categorical)__x(\d+)(?:_(.*))?$", raw)
        if match:
            index = int(match.group(1))
            if index < len(feature_columns):
                category = match.group(2)
                return feature_columns[index] + (f" = {category}" if category else "")
        return raw

    positive = [{"feature": label(str(names[i])), "ridge_contribution_sgd": round(float(values[i]), 2)}
                for i in order if values[i] > 0][:5]
    negative = [{"feature": label(str(names[i])), "ridge_contribution_sgd": round(float(values[i]), 2)}
                for i in order if values[i] < 0][:5]
    return ridge_price, {"ridge_intercept_sgd": round(intercept, 2),
                         "top_positive": positive, "top_negative": negative,
                         "note": "Ridge contributions are model associations, not causal price adjustments."}


def rank_comparables(target: dict, selected: list[dict], baseline, blocks: dict[str, tuple[str, str]]) -> list[dict]:
    ranked = []
    for sale in selected:
        distance = baseline.distance_m(target, sale)
        month_gap = month_number(target["transaction_month"]) - month_number(sale["transaction_month"])
        area_gap = abs(float(target["floor_area_sqm"]) - float(sale["floor_area_sqm"]))
        storey_gap = abs(float(target["storey_midpoint"]) - float(sale["storey_midpoint"]))
        score = distance / 1000 + area_gap / 10 + storey_gap / 6 + month_gap / 24
        ranked.append((score, sale, distance, month_gap))
    ranked.sort(key=lambda item: (item[0], item[1]["transaction_id"]))
    output = []
    for rank, (_, sale, distance, month_gap) in enumerate(ranked[:10], 1):
        block, street = blocks.get(sale["block_id"], ("Unknown", "Unknown"))
        output.append({"rank": rank, "transaction_id": sale["transaction_id"],
                       "block": block, "street": street,
                       "sale_month": sale["transaction_month"].isoformat(),
                       "sale_price_sgd": round(float(sale["resale_price"]), 2),
                       "flat_type": sale["flat_type"], "flat_model": sale["flat_model"],
                       "floor_area_sqm": sale["floor_area_sqm"],
                       "storey_midpoint": sale["storey_midpoint"],
                       "distance_m": round(distance, 1), "months_before_valuation": month_gap})
    return output


def predict(root: Path, args: argparse.Namespace) -> dict:
    scripts = root / "scripts/phase_1_resale_model"
    trainer = module(scripts / "06_train_resale_models.py", "resale_model_10")
    baseline = module(scripts / "04_build_baselines.py", "baseline_model_10")
    builder = module(scripts / "03_build_duckdb.py", "database_builder_10")
    release = json.loads((root / "reports/phase_1_resale_model/09_blend_release.json").read_text(encoding="utf-8"))
    if release["status"] != "national_candidate":
        raise ValueError("Current national model configuration is missing; run step 09")
    month = parse_month(args.valuation_month)
    flat_type = normalise(args.flat_type)
    storey_range = normalise(args.storey_range)
    midpoint = builder.parse_storey(storey_range)
    if args.floor_area_sqm <= 0:
        raise ValueError("Floor area must be positive")
    with duckdb.connect(str(root / "data/property.duckdb"), read_only=True) as connection:
        current_run = connection.execute("SELECT run_id FROM pipeline_runs ORDER BY built_at_utc DESC LIMIT 1").fetchone()
        if current_run is None or current_run[0] != release["source_build_run_id"]:
            raise ValueError("Database changed after step 09; rerun steps 04–09 before pricing a flat")
        training_end = connection.execute("SELECT MAX(transaction_month) FROM transaction_features WHERE dataset_split='train'").fetchone()[0]
        latest_sale_month = connection.execute("SELECT MAX(transaction_month) FROM resale_transactions").fetchone()[0]
        if month <= training_end:
            raise ValueError(f"Valuation month must be after the model training period ending {training_end}")
        if month_number(month) - month_number(latest_sale_month) > 12:
            raise ValueError("Transaction data is over 12 months old for the requested valuation month; refresh the project")
        block = block_record(connection, args.block, args.street)
        if midpoint > int(block["max_floor_lvl"]):
            raise ValueError("Storey range exceeds the block's recorded maximum floor")
        train_cursor = connection.execute("""
            SELECT flat_model, flat_type, MIN(floor_area_sqm), MAX(floor_area_sqm)
            FROM transaction_features WHERE dataset_split = 'train'
            GROUP BY flat_model, flat_type
        """)
        train_info = train_cursor.fetchall()
        train_models = {row[0] for row in train_info}
        areas = [row for row in train_info if row[1] == flat_type]
        if not areas:
            raise ValueError(f"Flat type {flat_type!r} is outside the pilot training data")
        min_area, max_area = min(float(row[2]) for row in areas), max(float(row[3]) for row in areas)
        if not min_area <= args.floor_area_sqm <= max_area:
            raise ValueError(f"Floor area for {flat_type} is outside training range {min_area:.1f}–{max_area:.1f} sqm")
        history = prior_block_values(connection, block["block_id"], flat_type, month)
        flat_model, inferred_model = resolve_flat_model(args.flat_model, history, train_models)
        lease_months, lease_year, inferred_lease = remaining_lease_months(
            args.remaining_lease_years, args.lease_commence_year, history, month,
        )
        source = {
            "transaction_id": "USER_REQUEST", "block_id": block["block_id"],
            "transaction_month": month, "dataset_split": "request", "town": block["town"],
            "flat_type": flat_type, "flat_model": flat_model,
            "floor_area_sqm": float(args.floor_area_sqm), "storey_range": storey_range,
            "storey_midpoint": midpoint, "lease_commence_year": lease_year,
            "remaining_lease_months": lease_months,
            "building_age_at_transaction": month.year - int(block["year_completed"]),
            **block,
        }
        # The project model expects the same names as the transaction feature table.
        artifact = joblib.load(root / release["ridge_artifact"])
        flat_types = sorted({row[1] for row in train_info})
        names, vectors = trainer.feature_records([source], flat_types)
        if names != artifact["feature_columns"]:
            raise ValueError("Feature schema differs from the trained Ridge artifact; rerun step 06")
        ridge_price, explanation = ridge_contributions(artifact["pipeline"], np.asarray(vectors, dtype=object), names)
        all_sales = baseline.load_records(connection)
        prior_sales = [row for row in all_sales if row["transaction_month"] < month
                       and row["town"] == block["town"] and row["flat_type"] == flat_type]
        block_names = {row[0]: (row[1], row[2]) for row in connection.execute("SELECT block_id, block, street FROM hdb_blocks").fetchall()}
    candidates, tier = baseline.select_comparables(source, prior_sales)
    warnings = ["National candidate estimate; review town-level validation before relying on it.",
                "Unit condition, renovation, exact floor, view, and seller circumstances are unavailable."]
    if inferred_model:
        warnings.append("Flat model was inferred from earlier same-block sales; verify it for this unit.")
    if inferred_lease:
        warnings.append("Lease commencement year was inferred from earlier same-block sales; verify it for this unit.")
    if block["match_status"] != "exact":
        warnings.append(f"Block geocoding match is {block['match_status']}; verify the address and coordinates.")
    if month_number(month) - month_number(latest_sale_month) > 3:
        warnings.append("Latest available transaction is more than three months before this valuation month.")
    if flat_type == "2 ROOM":
        warnings.append("Two-room flats had weaker blended-model backtest accuracy; review comparables closely.")
    result = {
        "status": "estimate" if len(candidates) >= 3 else "insufficient_comparable_evidence",
        "model_id": release["model_id"], "valuation_month": month.isoformat(),
        "source_latest_transaction_month": latest_sale_month.isoformat(),
        "subject": {"block": block["block"], "street": block["street"],
                    "flat_type": flat_type, "flat_model": flat_model,
                    "floor_area_sqm": args.floor_area_sqm, "storey_range": storey_range,
                    "remaining_lease_years_approx": round(lease_months / 12, 2),
                    "lease_commence_year": lease_year},
        "ridge_estimate_sgd": round(ridge_price, 2),
        "comparable_sales_estimate_sgd": None,
        "weights": {"ridge": release["ridge_weight"], "comparable_sales": release["comparable_weight"]},
        "price_estimate_sgd": None, "lower_estimate_sgd": None, "upper_estimate_sgd": None,
        "range_method": release["calibration_method"],
        "nominal_range_coverage_pct": None,
        "pilot_test_observed_range_coverage_pct": release["test_observed_coverage_pct"],
        "pilot_test_mae_sgd": release["test_mae_sgd"],
        "confidence_label": "insufficient_comparable_evidence" if len(candidates) < 3 else None,
        "comparable_count": len(candidates), "comparable_tier": tier,
        "comparable_sales": rank_comparables(source, candidates, baseline, block_names),
        "ridge_explanation": explanation,
        "warnings": warnings,
    }
    if len(candidates) < 3:
        result["warnings"].append("Fewer than three earlier comparable sales were found; no blended price is issued.")
        return result
    comparable_price = float(baseline.median_prediction(candidates)[0])
    point = round(release["ridge_weight"] * ridge_price + release["comparable_weight"] * comparable_price, 2)
    level = "97.5" if len(candidates) < 5 else "95"
    low_offset, high_offset = release["residual_offsets_sgd"][level]
    low = round(max(1.0, min(point, point + low_offset)), 2)
    high = round(max(point, point + high_offset), 2)
    result.update({
        "comparable_sales_estimate_sgd": round(comparable_price, 2),
        "price_estimate_sgd": point, "lower_estimate_sgd": low, "upper_estimate_sgd": high,
        "nominal_range_coverage_pct": float(level),
        "confidence_label": "low_wide_range" if len(candidates) < 5 else "moderate" if len(candidates) < 20 else "supported_by_comparables",
    })
    if len(candidates) < 5:
        result["warnings"].append("Fewer than five comparables were found; a wider range is used.")
    if release["range_status"] != "pilot_tolerance_pass":
        result["warnings"].append("Historical range coverage missed its stated level; treat the bounds as exploratory.")
    else:
        result["warnings"].append("The range is broad; its measured coverage is an overall national backtest and may differ by town.")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--block", required=True)
    parser.add_argument("--street", required=True)
    parser.add_argument("--flat-type", required=True)
    parser.add_argument("--floor-area-sqm", type=float, required=True)
    parser.add_argument("--storey-range", required=True)
    parser.add_argument("--flat-model")
    parser.add_argument("--lease-commence-year", type=int)
    parser.add_argument("--remaining-lease-years", type=float)
    parser.add_argument("--valuation-month", help="YYYY-MM; defaults to the current month")
    parser.add_argument("--output", type=Path, help="Optional local JSON output path")
    args = parser.parse_args()
    try:
        result = predict(args.project_root.resolve(), args)
        if args.output:
            output = args.output if args.output.is_absolute() else args.project_root.resolve() / args.output
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    except (ValueError, FileNotFoundError, OSError, duckdb.Error) as error:
        print(f"Flat valuation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
