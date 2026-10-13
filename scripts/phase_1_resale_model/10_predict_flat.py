"""Price one Singapore HDB flat with the currently released model."""

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


def normalise_block(value: str) -> str:
    """Accept common ways people write a block number without changing its identity."""
    return re.sub(r"^(?:BLK|BLOCK)\.?\s*#?\s*(?=\d)", "", normalise(value))


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
        SELECT b.block_id, b.block, b.street, b.postal_code, b.match_status, b.latitude, b.longitude,
               p.bldg_contract_town, p.year_completed, p.max_floor_lvl,
               p.total_dwelling_units, p.commercial, p.market_hawker,
               p.multistorey_carpark, p.precinct_pavilion,
               p."1room_sold", p."2room_sold", p."3room_sold", p."4room_sold",
               p."5room_sold", p.exec_sold, p.multigen_sold,
               l.* EXCLUDE (block_id),
               (SELECT mode(t.town) FROM resale_transactions t
                WHERE t.block_id = b.block_id) AS town
        FROM hdb_blocks b
        JOIN hdb_properties p USING (block_id)
        JOIN block_location_features l USING (block_id)
        WHERE upper(trim(b.block)) = ? AND upper(trim(b.street)) = ?
    """, [normalise_block(block), normalise(street)])
    names = [item[0] for item in cursor.description]
    matches = [dict(zip(names, values)) for values in cursor.fetchall()
               if normalise_block(str(values[1])) == normalise_block(block)
               and normalise(str(values[2])) == normalise(street)]
    if len(matches) != 1:
        raise ValueError(f"Expected one known HDB block for {block} {street}; found {len(matches)}")
    if not matches[0]["town"]:
        matches[0]["town"] = connection.execute("""
            SELECT mode(t.town)
            FROM resale_transactions t JOIN hdb_properties p USING (block_id)
            WHERE p.bldg_contract_town = ?
        """, [matches[0]["bldg_contract_town"]]).fetchone()[0]
        if not matches[0]["town"] and matches[0]["bldg_contract_town"] == "TG":
            matches[0]["town"] = "TENGAH"
    if not matches[0]["town"]:
        raise ValueError("This block's town could not be established from the available data")
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
    ridge_adjustment = float(pipeline.predict(vector)[0])
    if abs((intercept + float(values.sum())) - ridge_adjustment) > .02:
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
    return ridge_adjustment, {"ridge_intercept_sgd": round(intercept, 2),
                         "top_positive": positive, "top_negative": negative,
                         "note": "Add the Ridge adjustment to the earlier comparable price. Contributions are model associations, not causal price adjustments."}


def rank_comparables(target: dict, selected: list[dict], baseline, blocks: dict[str, tuple[str, str]]) -> list[dict]:
    ranked = []
    for sale in selected:
        has_distance = baseline.has_coordinates(target) and baseline.has_coordinates(sale)
        distance = baseline.distance_m(target, sale) if has_distance else None
        month_gap = month_number(target["transaction_month"]) - month_number(sale["transaction_month"])
        area_gap = abs(float(target["floor_area_sqm"]) - float(sale["floor_area_sqm"]))
        storey_gap = abs(float(target["storey_midpoint"]) - float(sale["storey_midpoint"]))
        score = area_gap / 10 + storey_gap / 6 + month_gap / 24
        if distance is not None:
            score += distance / 1000
        ranked.append((not has_distance, score, sale, distance, month_gap))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]["transaction_id"]))
    output = []
    for rank, (_, _, sale, distance, month_gap) in enumerate(ranked[:10], 1):
        block, street = blocks.get(sale["block_id"], ("Unknown", "Unknown"))
        output.append({"rank": rank, "transaction_id": sale["transaction_id"],
                       "block": block, "street": street,
                       "sale_month": sale["transaction_month"].isoformat(),
                       "sale_price_sgd": round(float(sale["resale_price"]), 2),
                       "flat_type": sale["flat_type"], "flat_model": sale["flat_model"],
                       "floor_area_sqm": sale["floor_area_sqm"],
                       "storey_midpoint": sale["storey_midpoint"],
                       "distance_m": round(distance, 1) if distance is not None else None,
                       "months_before_valuation": month_gap})
    return output


ROOM_COUNTS = {
    "1 ROOM": "1room_sold", "2 ROOM": "2room_sold", "3 ROOM": "3room_sold",
    "4 ROOM": "4room_sold", "5 ROOM": "5room_sold", "EXECUTIVE": "exec_sold",
    "MULTI-GENERATION": "multigen_sold",
}


def lookup_postal(connection, postal_code: str) -> tuple[str, str]:
    if not re.fullmatch(r"\d{6}", postal_code):
        raise ValueError("Postal code must contain six digits")
    rows = connection.execute(
        "SELECT block, street FROM hdb_blocks WHERE postal_code = ?", [postal_code]
    ).fetchall()
    if len(rows) != 1:
        raise ValueError(f"Postal code {postal_code} matches {len(rows)} known blocks; enter the block and street instead")
    return rows[0]


def typical_area(connection, block: dict, flat_type: str, month: date) -> tuple[float, float, float, str]:
    for scope, clause, parameters in (
        ("same block", "block_id = ?", [block["block_id"]]),
        ("same town", "town = ?", [block["town"]]),
        ("nationwide", "TRUE", []),
    ):
        row = connection.execute(f"""
            SELECT median(floor_area_sqm), quantile_cont(floor_area_sqm, 0.25),
                   quantile_cont(floor_area_sqm, 0.75)
            FROM transaction_features
            WHERE {clause} AND flat_type = ? AND transaction_month < ?
        """, [*parameters, flat_type, month]).fetchone()
        if row[0] is not None:
            return float(row[0]), float(row[1]), float(row[2]), scope
    raise ValueError(f"No earlier sales of {flat_type} flats can support an area estimate")


def storey_range_for_floor(floor: int) -> str:
    start = 1 + 3 * max(0, (floor - 1) // 3)
    return f"{start:02d} TO {start + 2:02d}"


def market_reference(connection, args: argparse.Namespace, month: date) -> dict:
    """Broad observed-sale reference when no specific block is identified."""
    town = normalise(args.town) if args.town else None
    flat_type = normalise(args.flat_type) if args.flat_type else None
    criteria = {}
    if town:
        criteria["town"] = ("town = ?", [town])
    if flat_type:
        criteria["flat type"] = ("flat_type = ?", [flat_type])
    if args.floor_area_sqm is not None:
        criteria["area"] = (
            "floor_area_sqm BETWEEN ? AND ?",
            [args.floor_area_sqm * .85, args.floor_area_sqm * 1.15],
        )
    if args.storey_range:
        storey = int(re.match(r"\d+", args.storey_range).group())
        criteria["storey"] = ("storey_midpoint BETWEEN ? AND ?", [storey - 1, storey + 3])
    if args.flat_model:
        criteria["flat model"] = ("upper(flat_model) = ?", [normalise(args.flat_model)])
    if args.lease_commence_year:
        criteria["lease year"] = (
            "lease_commence_year BETWEEN ? AND ?",
            [args.lease_commence_year - 5, args.lease_commence_year + 5],
        )
    levels = [
        set(criteria),
        set(criteria) - {"flat model", "lease year"},
        set(criteria) - {"flat model", "lease year", "area", "storey"},
        set(criteria) - {"flat model", "lease year", "area", "storey", "town"},
        set(),
    ]
    for retained in levels:
        filters = ["transaction_month < ?", "transaction_month >= ?"]
        parameters = [month, date(month.year - 2, month.month, 1)]
        for name in criteria:
            if name in retained:
                clause, values = criteria[name]
                filters.append(clause)
                parameters.extend(values)
        where = " AND ".join(filters)
        count, point, low, high = connection.execute(f"""
            SELECT count(*), median(resale_price), quantile_cont(resale_price, 0.1),
                   quantile_cont(resale_price, 0.9)
            FROM transaction_features WHERE {where}
        """, parameters).fetchone()
        if count >= 5:
            break
    else:
        raise ValueError("Too few recent sales are available for a market reference")
    warnings = [
        "No specific block was identified, so this is a broad recent-sales reference, not a model valuation of your flat.",
        "The range spans the middle 80% of matching sale prices; it is not a calibrated price interval.",
    ]
    relaxed = set(criteria) - retained
    if relaxed:
        warnings.append(
            "Some details were relaxed because fewer than five recent sales matched: "
            + ", ".join(sorted(relaxed)) + "."
        )
    return {
        "status": "market_reference", "release_type": "market_reference",
        "model_name": "Recent-sale median", "model_id": None,
        "valuation_month": month.isoformat(),
        "subject": {"block": None, "street": None, "town": town,
                    "flat_type": flat_type, "floor_area_sqm": args.floor_area_sqm,
                    "storey_range": args.storey_range},
        "price_estimate_sgd": round(float(point), 2),
        "lower_estimate_sgd": round(float(low), 2),
        "upper_estimate_sgd": round(float(high), 2),
        "comparable_count": count, "comparable_sales": [],
        "source_latest_transaction_month": connection.execute(
            "SELECT max(transaction_month) FROM resale_transactions"
        ).fetchone()[0].isoformat(),
        "input_sources": {},
        "warnings": warnings,
    }


def predict_partial(root: Path, args: argparse.Namespace, *, use_dashboard_bundle: bool = True) -> dict:
    """Use observed or explicitly labelled inferred inputs before calling the released model."""
    database = root / (
        "data/phase_1_resale_model/valuation.duckdb" if use_dashboard_bundle else "data/property.duckdb"
    )
    month = parse_month(args.valuation_month)
    with duckdb.connect(str(database), read_only=True) as connection:
        if args.postal_code:
            block_number, street = lookup_postal(connection, args.postal_code.strip())
            if args.block and args.street and (
                normalise_block(args.block) != normalise_block(block_number)
                or normalise(args.street) != normalise(street)
            ):
                raise ValueError("Postal code and entered block/street disagree; correct one of them")
        elif args.block and args.street:
            block_number, street = args.block, args.street
        else:
            return market_reference(connection, args, month)
        block = block_record(connection, block_number, street)
        room_types = sorted(
            ((flat_type, int(block.get(column) or 0)) for flat_type, column in ROOM_COUNTS.items()),
            key=lambda item: item[1], reverse=True,
        )
        inferred = {}
        if args.flat_type:
            flat_type = normalise(args.flat_type)
        elif room_types[0][1] > 0:
            flat_type = room_types[0][0]
            inferred["flat_type"] = "most common registered flat type in this block"
        else:
            row = connection.execute("""
                SELECT mode(flat_type) FROM transaction_features
                WHERE town = ? AND transaction_month < ?
            """, [block["town"], month]).fetchone()
            if row[0] is None:
                raise ValueError("Flat type cannot be inferred for this block; please select it")
            flat_type = row[0]
            inferred["flat_type"] = "most common earlier flat type in this town"
        area_median, area_low, area_high, area_scope = typical_area(connection, block, flat_type, month)
        area = args.floor_area_sqm
        if area is None:
            area = area_median
            inferred["floor_area_sqm"] = f"median {flat_type} floor area from {area_scope} sales"
        storey_range = args.storey_range
        if not storey_range:
            middle_floor = max(2, int(block["max_floor_lvl"] or 3) // 2)
            storey_range = storey_range_for_floor(middle_floor)
            inferred["storey_range"] = "middle storey range of this block; actual floor unknown"

    selected = argparse.Namespace(
        block=block_number, street=street, flat_type=flat_type,
        floor_area_sqm=float(area), storey_range=storey_range,
        flat_model=args.flat_model, lease_commence_year=args.lease_commence_year,
        remaining_lease_years=args.remaining_lease_years,
        valuation_month=args.valuation_month,
    )
    result = predict(root, selected, use_dashboard_bundle=use_dashboard_bundle)
    scenario_args = []
    if "floor_area_sqm" in inferred:
        scenario_args += [dict(floor_area_sqm=value) for value in (area_low, area_high)]
    if "storey_range" in inferred:
        top_floor = max(2, int(block["max_floor_lvl"] or 3) - 1)
        scenario_args += [
            dict(storey_range=storey_range_for_floor(floor)) for floor in (2, top_floor)
        ]
    if "flat_type" in inferred:
        with duckdb.connect(str(database), read_only=True) as connection:
            for other_type, count in room_types[1:3]:
                if count <= 0:
                    continue
                other_area = typical_area(connection, block, other_type, month)[0]
                scenario_args.append(dict(flat_type=other_type, floor_area_sqm=other_area))
    for changes in scenario_args:
        scenario = argparse.Namespace(**{**vars(selected), **changes})
        try:
            alternative = predict(root, scenario, use_dashboard_bundle=use_dashboard_bundle)
        except ValueError:
            continue
        result["lower_estimate_sgd"] = min(result["lower_estimate_sgd"], alternative["lower_estimate_sgd"])
        result["upper_estimate_sgd"] = max(result["upper_estimate_sgd"], alternative["upper_estimate_sgd"])
    result["input_sources"] = inferred
    if inferred:
        for field, source in inferred.items():
            result["warnings"].append(f"{field.replace('_', ' ').title()} was estimated from {source}; correct it if you know the actual value.")
        result["range_method"] = (
            "exploratory_no_block_sales_with_missing_detail_scenarios"
            if result["block_sales_count"] == 0 else "calibrated_range_plus_missing_detail_scenarios"
        )
        result["nominal_range_coverage_pct"] = None
        result["warnings"].append("The widened range includes plausible missing-detail scenarios, but its coverage has not been backtested.")
    return result


def predict(root: Path, args: argparse.Namespace, *, use_dashboard_bundle: bool = False) -> dict:
    scripts = root / "scripts/phase_1_resale_model"
    trainer = module(scripts / "06_train_resale_models.py", "resale_model_10")
    baseline = module(scripts / "04_build_baselines.py", "baseline_model_10")
    builder = module(scripts / "03_build_duckdb.py", "database_builder_10")
    full_database = root / "data/property.duckdb"
    use_bundle = use_dashboard_bundle or not full_database.is_file()
    database = root / "data/phase_1_resale_model/valuation.duckdb" if use_bundle else full_database
    release_file = root / "reports/phase_1_resale_model" / (
        "09_dashboard_release.json" if use_bundle else "09_blend_release.json"
    )
    release = json.loads(release_file.read_text(encoding="utf-8"))
    if release["status"] != "national_candidate":
        raise ValueError("No approved model release is available; review the Step 09 release report")
    required_release_fields = {"model_id", "comparable_method", "residual_offsets_sgd"}
    if not required_release_fields.issubset(release):
        raise ValueError("Model release is outdated; rerun steps 06–09")
    baseline_release = release["model_id"] == release["comparable_method"]
    if not baseline_release and (not release.get("model_artifact") or not release.get("feature_columns")):
        raise ValueError("Model release is missing its artifact or feature schema; rerun steps 06–09")
    local_policy = release.get("local_evidence")
    if not local_policy:
        raise ValueError("Model release lacks local-evidence calibration; rerun step 09")
    month = parse_month(args.valuation_month)
    flat_type = normalise(args.flat_type)
    storey_range = normalise(args.storey_range)
    midpoint = builder.parse_storey(storey_range)
    if args.floor_area_sqm <= 0:
        raise ValueError("Floor area must be positive")
    with duckdb.connect(str(database), read_only=True) as connection:
        current_run = connection.execute("SELECT run_id FROM pipeline_runs ORDER BY built_at_utc DESC LIMIT 1").fetchone()
        if current_run is None or current_run[0] != release["source_build_run_id"]:
            raise ValueError("Database changed after step 09; rerun steps 04–09 before pricing a flat")
        training_end = connection.execute("SELECT MAX(transaction_month) FROM transaction_features WHERE dataset_split='train'").fetchone()[0]
        if training_end.isoformat() != local_policy["training_end_month"]:
            raise ValueError("Local-evidence counts are stale; rerun step 09")
        latest_sale_month = connection.execute("SELECT MAX(transaction_month) FROM resale_transactions").fetchone()[0]
        if month <= training_end:
            raise ValueError(f"Valuation month must be after the model training period ending {training_end}")
        if month_number(month) - month_number(latest_sale_month) > 12:
            raise ValueError("Transaction data is over 12 months old for the requested valuation month; refresh the project")
        block = block_record(connection, args.block, args.street)
        recent_local_sales = connection.execute("""
            SELECT COUNT(*) FROM transaction_features
            WHERE dataset_split = 'train' AND town = ? AND flat_type = ?
              AND transaction_month >= ?
        """, [block["town"], flat_type, date.fromisoformat(local_policy["training_start_month"])]).fetchone()[0]
        limited_local = recent_local_sales < local_policy["minimum_training_sales"]
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
        block_sales_count = connection.execute("""
            SELECT count(*) FROM resale_transactions
            WHERE block_id = ? AND transaction_month < ?
        """, [block["block_id"], month]).fetchone()[0]
        model_history = history
        model_history_source = "same-block"
        if not args.flat_model and not history:
            model_history_source = "same-town"
            model_history = [
                {"flat_model": row[0]} for row in connection.execute("""
                    SELECT flat_model FROM resale_transactions
                    WHERE town = ? AND flat_type = ? AND transaction_month < ?
                    ORDER BY transaction_month DESC LIMIT 500
                """, [block["town"], flat_type, month]).fetchall()
            ]
            if not model_history:
                model_history_source = "nationwide"
                model_history = [
                    {"flat_model": row[0]} for row in connection.execute("""
                        SELECT flat_model FROM resale_transactions
                        WHERE flat_type = ? AND transaction_month < ?
                        ORDER BY transaction_month DESC LIMIT 500
                    """, [flat_type, month]).fetchall()
                ]
        flat_model, inferred_model = resolve_flat_model(args.flat_model, model_history, train_models)
        lease_history = history
        if args.lease_commence_year is None and args.remaining_lease_years is None and not history:
            lease_history = [
                {"lease_commence_year": row[0]} for row in connection.execute("""
                    SELECT lease_commence_year FROM resale_transactions
                    WHERE block_id = ? AND transaction_month < ?
                """, [block["block_id"], month]).fetchall()
            ]
            if not lease_history:
                lease_history = [{"lease_commence_year": block["year_completed"]}]
        lease_months, lease_year, inferred_lease = remaining_lease_months(
            args.remaining_lease_years, args.lease_commence_year, lease_history, month,
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
        comparison_town = block["town"]
        has_town_sales = connection.execute("""
            SELECT EXISTS(SELECT 1 FROM transaction_features
                          WHERE town = ? AND flat_type = ? AND transaction_month < ?)
        """, [comparison_town, flat_type, month]).fetchone()[0]
        if not has_town_sales:
            comparison_town = connection.execute("""
                SELECT town FROM transaction_features
                WHERE flat_type = ? AND transaction_month < ?
                GROUP BY town
                ORDER BY min(power(latitude - ?, 2) + power(longitude - ?, 2)) NULLS LAST,
                         count(*) DESC LIMIT 1
            """, [flat_type, month, block["latitude"], block["longitude"]]).fetchone()
            if comparison_town is None:
                raise ValueError("No earlier sales of this flat type are available")
            comparison_town = comparison_town[0]
        prior_sales = baseline.load_records(
            connection, town=comparison_town, flat_type=flat_type, before=month
        )
        comparable_history = baseline.ComparableHistory()
        comparable_history.add(prior_sales)
        comparison_target = dict(source, town=comparison_town)
        candidates, tier = baseline.select_comparables(comparison_target, comparable_history)
        if not candidates:
            raise ValueError("No earlier comparable sales are available for this flat; a baseline-aware model cannot estimate its price")
        comparable_price = float(baseline.comparable_price_prediction(
            release.get("comparable_method", "BASELINE_COMPARABLE_SALES_V1"), month, candidates,
        )[0])
        model_id = release["model_id"]
        explanation = None
        if baseline_release:
            model_price = comparable_price
        else:
            source[trainer.COMPARABLE_FEATURE] = comparable_price
            flat_types = sorted({row[1] for row in train_info})
            names, vectors = trainer.baseline_aware_features([source], flat_types)
            if names != release["feature_columns"]:
                raise ValueError("Feature schema differs from the released model; rerun steps 06–09")
            artifact_path = Path(release["model_artifact"])
            if not artifact_path.is_absolute():
                artifact_path = root / artifact_path
            model_price = float(trainer.predict_with_artifact(
                artifact_path, model_id, names, np.asarray(vectors, dtype=object),
            )[0])
            if model_id == "ML_RIDGE_RESALE_PRICE_V1":
                artifact = joblib.load(artifact_path)
                _, explanation = ridge_contributions(artifact["pipeline"], np.asarray(vectors, dtype=object), names)
        block_names = {row[0]: (row[1], row[2]) for row in connection.execute("SELECT block_id, block, street FROM hdb_blocks").fetchall()}
    warnings = ["National candidate estimate; review town-level validation before relying on it.",
                "Unit condition, renovation, exact floor, view, and seller circumstances are unavailable."]
    if inferred_model:
        warnings.append(f"Flat model was inferred from earlier {model_history_source} sales; verify it for this unit.")
    if inferred_lease:
        source_note = "earlier same-block sales" if history else "the block's completion year"
        warnings.append(f"Lease commencement year was inferred from {source_note}; verify it for this unit.")
    if not history:
        warnings.append("This block has no earlier sales of the selected flat type; the estimate relies on sales from other blocks.")
    if block_sales_count == 0:
        warnings.append("No resale has been recorded for this block. Historical model accuracy and range coverage have not been checked on blocks with no sales.")
    if comparison_town != block["town"]:
        warnings.append(f"No earlier {flat_type} sales exist in {block['town']}; comparable sales from {comparison_town} were used instead.")
    if block["year_completed"] and month.year - int(block["year_completed"]) < 5:
        warnings.append("This building is recent; check whether its minimum occupation period allows a resale now. This estimate does not establish sale eligibility.")
    if block["match_status"] != "exact":
        warnings.append(f"Block geocoding match is {block['match_status']}; verify the address and coordinates.")
    if not baseline.has_coordinates(block):
        warnings.append("This block has no coordinates; comparable distances are unavailable, so comparisons use flat and transaction details.")
    if month_number(month) - month_number(latest_sale_month) > 3:
        warnings.append("Latest available transaction is more than three months before this valuation month.")
    if flat_type == "2 ROOM":
        warnings.append("Two-room estimates should be reviewed alongside the available comparable sales.")
    if limited_local:
        warnings.append(
            f"Only {recent_local_sales} recent training sales match this town and flat type; "
            "local evidence is limited and a wider research range is used."
        )
    result = {
        "status": "estimate", "model_id": model_id,
        "model_name": release.get("model_name", model_id), "valuation_month": month.isoformat(),
        "release_type": "comparable_baseline" if baseline_release else "machine_learning",
        "selection_reason": release.get("selection_reason"),
        "source_latest_transaction_month": latest_sale_month.isoformat(),
        "subject": {"block": block["block"], "street": block["street"],
                    "flat_type": flat_type, "flat_model": flat_model,
                    "floor_area_sqm": args.floor_area_sqm, "storey_range": storey_range,
                    "remaining_lease_years_approx": round(lease_months / 12, 2),
                    "lease_commence_year": lease_year},
        "model_estimate_sgd": round(model_price, 2),
        "comparable_sales_estimate_sgd": round(comparable_price, 2) if comparable_price is not None else None,
        "weights": {"selected_model": 0.0 if baseline_release else 1.0,
                    "comparable_sales": 1.0 if baseline_release else 0.0},
        "price_estimate_sgd": round(model_price, 2), "lower_estimate_sgd": None, "upper_estimate_sgd": None,
        "range_method": release["calibration_method"],
        "nominal_range_coverage_pct": None,
        "pilot_test_observed_range_coverage_pct": release["test_observed_coverage_pct"],
        "pilot_test_mae_sgd": release["test_mae_sgd"],
        "confidence_label": "limited_comparable_evidence" if len(candidates) < 3 else None,
        "comparable_count": len(candidates), "comparable_tier": tier,
        "comparable_town": comparison_town,
        "block_sales_count": block_sales_count,
        "recent_town_flat_training_sales": recent_local_sales,
        "recent_local_training_start_month": local_policy["training_start_month"],
        "comparable_sales": rank_comparables(source, candidates, baseline, block_names),
        "model_explanation": explanation,
        "warnings": warnings,
    }
    if len(candidates) < 3:
        result["warnings"].append("Fewer than three earlier comparable sales were found; this estimate has limited local sales evidence.")
    level = "97.5" if limited_local or len(candidates) < 5 else "95"
    low_offset, high_offset = (local_policy["residual_offsets_sgd"] if limited_local
                               else release["residual_offsets_sgd"][level])
    low = round(max(1.0, min(model_price, model_price + low_offset)), 2)
    high = round(max(model_price, model_price + high_offset), 2)
    result.update({
        "price_estimate_sgd": round(model_price, 2), "lower_estimate_sgd": low, "upper_estimate_sgd": high,
        "nominal_range_coverage_pct": float(level),
        "confidence_label": "limited_comparable_evidence" if len(candidates) < 3 else "limited_local_training_data" if limited_local else "low_wide_range" if len(candidates) < 5 else "moderate" if len(candidates) < 20 else "supported_by_comparables",
    })
    if limited_local:
        result["pilot_test_observed_range_coverage_pct"] = local_policy["test_observed_coverage_pct"]
    if len(candidates) < 5:
        result["warnings"].append("Fewer than five comparables were found; a wider range is used.")
    if block_sales_count == 0:
        result["nominal_range_coverage_pct"] = None
        result["range_method"] = "exploratory_no_block_sales"
    elif release["range_status"] != "pilot_tolerance_pass":
        result["warnings"].append("Historical range coverage missed its stated level; treat the bounds as exploratory.")
    elif limited_local:
        result["warnings"].append(
            f"The wider range covered {local_policy['test_observed_coverage_pct']:.2f}% of "
            "low-local-evidence test sales overall; coverage for this town may differ."
        )
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
