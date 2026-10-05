"""Build Phase 4 block matches, enriched listing features, and scenario valuations."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import joblib
import numpy as np


PHASE = "phase_4_market_watch"
MODEL_ID = "NATIONAL_RIDGE_COMPARABLE_BLEND_V1"
STREET_WORDS = {
    "AVE": "AVENUE", "AV": "AVENUE", "CRES": "CRESCENT", "DR": "DRIVE",
    "LN": "LANE", "RD": "ROAD", "ST": "STREET", "NTH": "NORTH",
    "STH": "SOUTH", "CTRL": "CENTRAL", "JLN": "JALAN", "LOR": "LORONG",
    "BT": "BUKIT", "UPP": "UPPER", "CL": "CLOSE", "PL": "PLACE",
}


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalise(value: str | None) -> str:
    text = re.sub(r"[^A-Z0-9 ]", " ", (value or "").upper())
    return " ".join(STREET_WORDS.get(word, word) for word in text.split())


def month_number(value: date) -> int:
    return value.year * 12 + value.month


def read_rows(connection, query: str, parameters=None) -> list[dict]:
    cursor = connection.execute(query, parameters or [])
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def latest_complete_runs(connection) -> list[tuple[str, str]]:
    rows = read_rows(connection, """
        SELECT source_site, run_id, MAX(last_seen_utc) AS observed,
               MAX(total_pages) AS expected_pages,
               COUNT(DISTINCT CAST(page AS INTEGER)) AS saved_pages
        FROM listings, UNNEST(CAST(json_extract(pages_seen_json, '$') AS INTEGER[])) AS p(page)
        GROUP BY source_site, run_id
        HAVING saved_pages = expected_pages
        QUALIFY ROW_NUMBER() OVER (PARTITION BY source_site ORDER BY observed DESC, run_id DESC) = 1
    """)
    if not rows:
        raise ValueError("No complete listing collection run is available")
    return [(row["source_site"], row["run_id"]) for row in rows]


def property_reference(property_db: Path) -> tuple[dict, dict, list[dict], dict]:
    with duckdb.connect(str(property_db), read_only=True) as con:
        blocks = read_rows(con, """
            SELECT b.block_id, b.block, b.street, b.match_status AS geocode_match_status,
                   b.matched_address, b.postal_code, b.latitude, b.longitude,
                   p.max_floor_lvl, p.year_completed, p.residential, p.commercial,
                   p.market_hawker, p.multistorey_carpark, p.precinct_pavilion,
                   p.bldg_contract_town, p.total_dwelling_units,
                   l.* EXCLUDE (block_id)
            FROM hdb_blocks b
            JOIN hdb_properties p USING (block_id)
            JOIN block_location_features l USING (block_id)
            WHERE p.residential
        """)
        transactions = read_rows(con, """
            SELECT * FROM transaction_features
            ORDER BY transaction_month
        """)
        run_id = con.execute("SELECT run_id FROM pipeline_runs ORDER BY built_at_utc DESC LIMIT 1").fetchone()[0]
        latest_month = con.execute("SELECT MAX(transaction_month) FROM resale_transactions").fetchone()[0]
    town_counts = defaultdict(Counter)
    for transaction in transactions:
        town_counts[transaction["block_id"]][transaction["town"]] += 1
    for block in blocks:
        counts = town_counts.get(block["block_id"])
        block["town"] = counts.most_common(1)[0][0] if counts else None
    exact = defaultdict(list)
    by_block = defaultdict(list)
    for block in blocks:
        exact[(normalise(block["block"]), normalise(block["street"]))].append(block)
        by_block[normalise(block["block"])].append(block)
    return exact, by_block, transactions, {"pipeline_run_id": run_id, "latest_month": latest_month}


def match_listing(listing: dict, exact: dict, by_block: dict) -> dict:
    block = normalise(listing.get("block"))
    street = normalise(listing.get("street"))
    candidates = exact.get((block, street), []) if block and street else []
    method = "exact_normalised_block_street"
    confidence = "high"
    if not candidates and block:
        block_candidates = by_block.get(block, [])
        if len(block_candidates) == 1:
            candidates = block_candidates
            method = "unique_block_number"
            confidence = "medium"
    if len(candidates) == 1:
        match = candidates[0]
        return {"block_id": match["block_id"], "match_status": "matched",
                "match_method": method, "match_confidence": confidence,
                "candidate_count": 1, "matched_block": match["block"],
                "matched_street": match["street"], "review_note": None}
    note = "Missing usable block or street" if not block else (
        "No HDB block matched the normalised address" if not candidates else
        "More than one HDB block matched the address"
    )
    return {"block_id": None, "match_status": "unmatched" if not candidates else "ambiguous",
            "match_method": None, "match_confidence": "none", "candidate_count": len(candidates),
            "matched_block": None, "matched_street": None, "review_note": note}


def infer_flat_type(area: float | None, block_id: str, town: str | None,
                    transactions: list[dict]) -> tuple[str | None, str, float | None]:
    if not area:
        return None, "missing_floor_area", None
    same_block = [row for row in transactions if row["block_id"] == block_id]
    same_town = [row for row in transactions if town and row["town"] == town]
    pool = same_block or same_town or transactions
    grouped = defaultdict(list)
    for row in pool:
        grouped[row["flat_type"]].append(float(row["floor_area_sqm"]))
    choices = []
    for flat_type, values in grouped.items():
        median = float(np.median(values))
        gap = abs(area - median)
        choices.append((gap, flat_type, median))
    if not choices:
        return None, "no_transaction_history", None
    gap, flat_type, median = min(choices)
    tolerance = max(8.0, median * 0.12)
    if gap > tolerance:
        return None, "area_outside_observed_types", gap
    method = "same_block_area" if same_block else "same_town_area" if same_town else "national_area"
    return flat_type, method, gap


def scenario_inputs(block: dict, flat_type: str, area: float, transactions: list[dict], month: date) -> tuple[list[dict], dict]:
    history = [row for row in transactions if row["block_id"] == block["block_id"]
               and row["flat_type"] == flat_type and row["transaction_month"] < month]
    if not history:
        return [], {"reason": "no_same_block_flat_type_history"}
    flat_model = Counter(row["flat_model"] for row in history if row["flat_model"]).most_common(1)[0][0]
    lease_year = Counter(int(row["lease_commence_year"]) for row in history
                         if row["lease_commence_year"]).most_common(1)[0][0]
    ranges = {}
    for row in history:
        midpoint = float(row["storey_midpoint"])
        if midpoint <= int(block["max_floor_lvl"]):
            ranges[row["storey_range"]] = midpoint
    ordered = sorted(ranges.items(), key=lambda item: item[1])
    if not ordered:
        return [], {"reason": "no_valid_storey_history"}
    indices = sorted({0, len(ordered) // 2, len(ordered) - 1})
    scenarios = [{"scenario": ("low", "middle", "high")[position],
                  "storey_range": ordered[index][0], "storey_midpoint": ordered[index][1],
                  "flat_model": flat_model, "lease_commence_year": lease_year}
                 for position, index in enumerate(indices)]
    return scenarios, {"flat_model": flat_model, "lease_commence_year": lease_year,
                       "scenario_basis": "observed same-block flat-type transactions"}


def valuation_rows(root: Path, features: list[dict], block_lookup: dict, transactions: list[dict], metadata: dict) -> list[dict]:
    phase1 = root / "scripts/phase_1_resale_model"
    trainer = load_module(phase1 / "06_train_resale_models.py", "phase4_models")
    baseline = load_module(phase1 / "04_build_baselines.py", "phase4_baseline")
    release = json.loads((root / "reports/phase_1_resale_model/09_blend_release.json").read_text(encoding="utf-8"))
    if release["source_build_run_id"] != metadata["pipeline_run_id"]:
        raise ValueError("property.duckdb changed after model calibration; rerun Phase 1 steps 04–09")
    month = date.today().replace(day=1)
    if month_number(month) - month_number(metadata["latest_month"]) > 12:
        raise ValueError("Transaction data is over 12 months old; refresh Phase 1 before valuation")
    artifact = joblib.load(root / release["ridge_artifact"])
    flat_types = sorted({row["flat_type"] for row in transactions})
    block_names = {row["block_id"]: (row["block"], row["street"]) for row in block_lookup.values()}
    prepared = []
    failures = {}
    for feature in features:
        if feature["match_status"] != "matched" or not feature.get("inferred_flat_type"):
            failures[feature["listing_key"]] = "missing_matched_block_or_flat_type"
            continue
        block = block_lookup[feature["block_id"]]
        scenarios, assumptions = scenario_inputs(block, feature["inferred_flat_type"],
                                                  float(feature["floor_area_sqm"]), transactions, month)
        if not scenarios:
            failures[feature["listing_key"]] = assumptions["reason"]
            continue
        remaining_months = 99 * 12 - (month_number(month) - month_number(date(assumptions["lease_commence_year"], 1, 1)))
        if remaining_months <= 0:
            failures[feature["listing_key"]] = "invalid_inferred_remaining_lease"
            continue
        for scenario in scenarios:
            source = {
                "transaction_id": feature["listing_key"] + ":" + scenario["scenario"],
                "block_id": block["block_id"], "transaction_month": month,
                "dataset_split": "listing_scenario", "town": block["town"],
                "flat_type": feature["inferred_flat_type"], "flat_model": scenario["flat_model"],
                "floor_area_sqm": float(feature["floor_area_sqm"]),
                "storey_range": scenario["storey_range"], "storey_midpoint": scenario["storey_midpoint"],
                "lease_commence_year": scenario["lease_commence_year"],
                "remaining_lease_months": remaining_months,
                "building_age_at_transaction": month.year - int(block["year_completed"]),
                **block,
            }
            prepared.append((feature, scenario, assumptions, source))
    if prepared:
        names, vectors = trainer.feature_records([item[3] for item in prepared], flat_types)
        if names != artifact["feature_columns"]:
            raise ValueError("Feature schema differs from the saved Ridge model")
        ridge_prices = artifact["pipeline"].predict(np.asarray(vectors, dtype=object))
    else:
        ridge_prices = []
    sales = [row for row in transactions if row["transaction_month"] < month]
    scenarios_by_listing = defaultdict(list)
    for (feature, scenario, assumptions, source), ridge_raw in zip(prepared, ridge_prices):
        candidates, tier = baseline.select_comparables(
            source, [row for row in sales if row["flat_type"] == source["flat_type"]]
        )
        comparable_count = len(candidates)
        result = {"scenario": scenario["scenario"], "storey_range": scenario["storey_range"],
                  "ridge_price": round(max(1.0, float(ridge_raw)), 2),
                  "comparable_count": comparable_count, "comparable_tier": tier,
                  "comparable_price": None, "point": None, "lower": None, "upper": None,
                  "comparables": []}
        if comparable_count >= 3:
            comparable_price = float(baseline.median_prediction(candidates)[0])
            point = release["ridge_weight"] * result["ridge_price"] + release["comparable_weight"] * comparable_price
            level = "97.5" if comparable_count < 5 else "95"
            low_offset, high_offset = release["residual_offsets_sgd"][level]
            ranked = []
            for sale in candidates:
                distance = baseline.distance_m(source, sale)
                area_gap = abs(float(source["floor_area_sqm"]) - float(sale["floor_area_sqm"]))
                storey_gap = abs(float(source["storey_midpoint"]) - float(sale["storey_midpoint"]))
                months = month_number(month) - month_number(sale["transaction_month"])
                score = distance / 1000 + area_gap / 10 + storey_gap / 6 + months / 24
                ranked.append((score, sale, distance))
            ranked.sort(key=lambda item: (item[0], item[1]["transaction_id"]))
            result.update({
                "comparable_price": round(comparable_price, 2), "point": round(point, 2),
                "lower": round(max(1.0, point + low_offset), 2),
                "upper": round(point + high_offset, 2),
                "comparables": [{"transaction_id": sale["transaction_id"],
                                  "block": block_names.get(sale["block_id"], (None, None))[0],
                                  "street": block_names.get(sale["block_id"], (None, None))[1],
                                  "sale_month": sale["transaction_month"].isoformat(),
                                  "sale_price_sgd": sale["resale_price"],
                                  "floor_area_sqm": sale["floor_area_sqm"],
                                  "storey_range": sale["storey_range"],
                                  "distance_m": round(distance, 1)}
                                 for _, sale, distance in ranked[:5]],
            })
        scenarios_by_listing[feature["listing_key"]].append(result)
    output = []
    for feature in features:
        key = feature["listing_key"]
        scenarios = scenarios_by_listing.get(key, [])
        valid = [row for row in scenarios if row["point"] is not None]
        if not valid:
            output.append({**{field: feature[field] for field in ("source_site", "run_id", "listing_id")},
                           "valuation_status": "not_valued", "model_id": MODEL_ID,
                           "valuation_month": month, "assumption_method": None,
                           "scenario_count": len(scenarios), "point_estimate_sgd": None,
                           "lower_estimate_sgd": None, "upper_estimate_sgd": None,
                           "asking_price_sgd": feature.get("asking_price_sgd"),
                           "asking_premium_discount_sgd": None, "asking_premium_discount_pct": None,
                           "minimum_comparable_count": min((s["comparable_count"] for s in scenarios), default=0),
                           "confidence_label": "not_available", "scenarios_json": json.dumps(scenarios),
                           "valuation_note": failures.get(key, "insufficient_comparable_evidence")})
            continue
        points = sorted(row["point"] for row in valid)
        point = float(np.median(points))
        low = min(row["lower"] for row in valid)
        high = max(row["upper"] for row in valid)
        asking = feature.get("asking_price_sgd")
        delta = asking - point if asking is not None else None
        pct = delta / point * 100 if delta is not None and point else None
        confidence = "scenario_range" if len(valid) >= 2 else "single_scenario_low_confidence"
        output.append({**{field: feature[field] for field in ("source_site", "run_id", "listing_id")},
                       "valuation_status": "estimated", "model_id": release["model_id"],
                       "valuation_month": month, "assumption_method": "same-block history; low/middle/high observed storeys",
                       "scenario_count": len(valid), "point_estimate_sgd": round(point, 2),
                       "lower_estimate_sgd": round(low, 2), "upper_estimate_sgd": round(high, 2),
                       "asking_price_sgd": asking,
                       "asking_premium_discount_sgd": round(delta, 2) if delta is not None else None,
                       "asking_premium_discount_pct": round(pct, 2) if pct is not None else None,
                       "minimum_comparable_count": min(row["comparable_count"] for row in valid),
                       "confidence_label": confidence, "scenarios_json": json.dumps(scenarios),
                       "valuation_note": "Floor and flat model are inferred; verify unit details before relying on the estimate."})
    return output


def create_tables(connection, matches: list[dict], features: list[dict], valuations: list[dict], built_at: str) -> None:
    def sql_type(values) -> str:
        value = next((value for value in values if value is not None), None)
        if isinstance(value, bool):
            return "BOOLEAN"
        if isinstance(value, int):
            return "BIGINT"
        if isinstance(value, float):
            return "DOUBLE"
        if isinstance(value, date):
            return "DATE"
        return "VARCHAR"

    connection.execute("BEGIN TRANSACTION")
    try:
        for table in ("listing_block_matches", "listing_features", "listing_valuations"):
            connection.execute(f"DROP TABLE IF EXISTS {table}")
        connection.execute("""
            CREATE TABLE listing_block_matches (
                source_site VARCHAR, run_id VARCHAR, listing_id VARCHAR, block_id VARCHAR,
                match_status VARCHAR, match_method VARCHAR, match_confidence VARCHAR,
                candidate_count INTEGER, matched_block VARCHAR, matched_street VARCHAR,
                review_note VARCHAR, built_at_utc VARCHAR,
                PRIMARY KEY(source_site, run_id, listing_id))
        """)
        connection.executemany("INSERT INTO listing_block_matches VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
            [row.get(key) for key in ("source_site", "run_id", "listing_id", "block_id", "match_status",
             "match_method", "match_confidence", "candidate_count", "matched_block", "matched_street",
             "review_note")] + [built_at] for row in matches])
        if features:
            keys = list(dict.fromkeys(key for row in features for key in row if key != "listing_key"))
            definition = ", ".join(
                f'"{key}" {sql_type(row.get(key) for row in features)}' for key in keys
            )
            connection.execute(f"CREATE TABLE listing_features ({definition}, built_at_utc VARCHAR, PRIMARY KEY(source_site, run_id, listing_id))")
            marks = ",".join("?" for _ in range(len(keys) + 1))
            connection.executemany(f"INSERT INTO listing_features VALUES ({marks})", [[row.get(k) for k in keys] + [built_at] for row in features])
        if valuations:
            keys = list(valuations[0])
            types = {"scenario_count": "INTEGER", "minimum_comparable_count": "INTEGER",
                     "point_estimate_sgd": "DOUBLE", "lower_estimate_sgd": "DOUBLE", "upper_estimate_sgd": "DOUBLE",
                     "asking_price_sgd": "DOUBLE", "asking_premium_discount_sgd": "DOUBLE",
                     "asking_premium_discount_pct": "DOUBLE", "valuation_month": "DATE"}
            definition = ", ".join(f'"{key}" {types.get(key, "VARCHAR")}' for key in keys)
            connection.execute(f"CREATE TABLE listing_valuations ({definition}, built_at_utc VARCHAR, PRIMARY KEY(source_site, run_id, listing_id))")
            marks = ",".join("?" for _ in range(len(keys) + 1))
            connection.executemany(f"INSERT INTO listing_valuations VALUES ({marks})", [[row.get(k) for k in keys] + [built_at] for row in valuations])
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise


def build(root: Path) -> dict:
    listing_db = root / "data/listings.db"
    property_db = root / "data/property.duckdb"
    if not listing_db.is_file() or not property_db.is_file():
        raise ValueError("Both data/listings.db and data/property.duckdb are required")
    exact, by_block, transactions, metadata = property_reference(property_db)
    block_lookup = {row["block_id"]: row for rows in exact.values() for row in rows}
    with duckdb.connect(str(listing_db)) as con:
        runs = latest_complete_runs(con)
        clauses = " OR ".join("(source_site = ? AND run_id = ?)" for _ in runs)
        parameters = [value for pair in runs for value in pair]
        listings = read_rows(con, f"SELECT * FROM listings WHERE {clauses}", parameters)
        matches = []
        features = []
        for listing in listings:
            match = match_listing(listing, exact, by_block)
            matches.append({"source_site": listing["source_site"], "run_id": listing["run_id"],
                            "listing_id": listing["listing_id"], **match})
            block = block_lookup.get(match["block_id"])
            inferred_type, type_method, area_gap = infer_flat_type(
                listing.get("floor_area_sqm"), match["block_id"], block.get("town"), transactions
            ) if block else (None, "unmatched_block", None)
            feature = {
                "source_site": listing["source_site"], "run_id": listing["run_id"],
                "listing_id": listing["listing_id"], "listing_url": listing["listing_url"],
                "asking_price_sgd": listing["asking_price_sgd"], "floor_area_sqm": listing["floor_area_sqm"],
                "bedrooms": listing["bedrooms"], "bathrooms": listing["bathrooms"],
                "listed_date": listing["listed_date"], "first_seen_utc": listing["first_seen_utc"],
                "last_seen_utc": listing["last_seen_utc"], **match,
                "inferred_flat_type": inferred_type, "flat_type_inference_method": type_method,
                "flat_type_area_gap_sqm": area_gap,
            }
            if block:
                feature.update({key: value for key, value in block.items() if key not in feature})
            feature["listing_key"] = f'{listing["source_site"]}:{listing["run_id"]}:{listing["listing_id"]}'
            features.append(feature)
        valuations = valuation_rows(root, features, block_lookup, transactions, metadata)
        built_at = datetime.now(timezone.utc).isoformat()
        create_tables(con, matches, features, valuations, built_at)
    counts = {
        "listings_in_latest_complete_runs": len(listings),
        "block_matches": dict(Counter(row["match_status"] for row in matches)),
        "flat_type_inference": dict(Counter(row["flat_type_inference_method"] for row in features)),
        "valuations": dict(Counter(row["valuation_status"] for row in valuations)),
        "valuation_confidence": dict(Counter(row["confidence_label"] for row in valuations)),
    }
    summary = {"status": "success", "built_at_utc": built_at,
               "source_runs": [{"source_site": site, "run_id": run} for site, run in runs],
               "source_property_pipeline_run_id": metadata["pipeline_run_id"],
               "model_id": MODEL_ID, "counts": counts,
               "tables": ["listing_block_matches", "listing_features", "listing_valuations"],
               "limitations": [
                   "Listing cards do not provide exact floor or verified flat model.",
                   "Valuations use observed low, middle, and high same-block storey scenarios.",
                   "Unmatched listings and listings without adequate same-block history are not valued.",
                   "These are research estimates, not official HDB or professional valuations.",
               ]}
    report = root / "reports" / PHASE / "19_listing_analysis_summary.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    temporary = report.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    temporary.replace(report)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.project_root.resolve()), indent=2))
    except duckdb.IOException as error:
        parser.exit(1, f"Cannot open a database. Disconnect listings.db in DBeaver and retry. {error}\n")
    except ValueError as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
