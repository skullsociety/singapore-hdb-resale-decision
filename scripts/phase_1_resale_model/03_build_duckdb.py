"""Build the Singapore HDB analytical DuckDB from processed official extracts.

This is an offline, repeatable build. Extraction and model fitting are separate
steps. The previous database is kept when a source or join check fails.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

try:
    import duckdb
except ImportError as error:
    raise SystemExit("DuckDB is required. Run: python -m pip install -r requirements.txt") from error


SOURCES = {
    "transactions": "hdb_resale_transactions.csv",
    "properties": "hdb_property_information.csv",
    "blocks": "hdb_blocks_with_coordinates.csv",
    "schools": "primary_schools_with_coordinates.csv",
    "childcare": "childcare_centres_with_coordinates.csv",
    "healthcare": "healthcare_facilities_with_coordinates.csv",
    "amenities": "onemap_amenities_with_coordinates.csv",
}
AMENITY_SOURCES = {
    "community_clubs": "Community Club / PAssion WaVe Outlet",
    "hawker_centres": "Hawker Centres",
    "mrt_lrt_stations": "LTA MRT Station Exit (GEOJSON)",
    "parks": "Parks",
    "public_libraries": "Libraries",
}
NEARBY_CATEGORIES = (
    "primary_schools", "childcare_centres", "healthcare_clinics", "polyclinics",
    "hospitals", "community_clubs", "hawker_centres", "mrt_lrt_stations",
    "parks", "public_libraries",
)
AMENITY_COLUMNS = (
    "amenity_category", "source_type", "source_name", "source_query", "name",
    "description", "address", "postal_code", "latitude", "longitude",
    "geometry_type", "coordinate_method", "source_properties_json",
)


def normalise(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().upper())


def block_key(block: str, street: str) -> str:
    return f"{normalise(block)}|{normalise(street)}"


def stable_id(prefix: str, *parts: object) -> str:
    raw = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def read_csv(path: Path, required: set[str]) -> list[dict[str, str]]:
    if not path.is_file():
        raise ValueError(f"Required source file is missing: {path}")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path.name} is missing columns: {', '.join(sorted(missing))}")
        rows = list(reader)
    if not rows:
        raise ValueError(f"{path.name} has no rows")
    return rows


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def coordinate(row: dict[str, str], *, required: bool = False) -> tuple[float, float] | None:
    if not row["latitude"].strip() or not row["longitude"].strip():
        if required:
            raise ValueError(f"Missing coordinate for {row.get('name', row.get('block', 'record'))}")
        return None
    try:
        lat, lon = float(row["latitude"]), float(row["longitude"])
    except ValueError as error:
        raise ValueError(f"Invalid coordinate: {row}") from error
    if not (1.1 <= lat <= 1.6 and 103.5 <= lon <= 104.2):
        raise ValueError(f"Coordinate outside Singapore bounds: {lat}, {lon}")
    return lat, lon


def parse_lease(value: str) -> int:
    match = re.fullmatch(r"\s*(\d+)\s+years?(?:\s+(\d+)\s+months?)?\s*", value)
    if not match:
        raise ValueError(f"Unexpected remaining_lease format: {value!r}")
    years = int(match.group(1))
    months = int(match.group(2) or 0)
    if months > 11:
        raise ValueError(f"Invalid remaining_lease months: {value!r}")
    return 12 * years + months


def parse_storey(value: str) -> float:
    match = re.fullmatch(r"\s*(\d+)\s+TO\s+(\d+)\s*", value)
    if not match:
        raise ValueError(f"Unexpected storey_range format: {value!r}")
    low, high = map(int, match.groups())
    if high < low:
        raise ValueError(f"Invalid storey_range: {value!r}")
    return (low + high) / 2


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    delta_lat, delta_lon = lat2 - lat1, lon2 - lon1
    a = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    return 12_742_000 * math.asin(min(1.0, math.sqrt(a)))


def create_table(con: duckdb.DuckDBPyConnection, name: str, columns: list[tuple[str, str]], rows: list[tuple]) -> None:
    definition = ", ".join(f'"{column}" {kind}' for column, kind in columns)
    con.execute(f'CREATE TABLE "{name}" ({definition})')
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", suffix=".csv", delete=False) as handle:
        temporary_csv = Path(handle.name)
        csv.writer(handle).writerows(rows)
    try:
        path_sql = str(temporary_csv).replace("'", "''")
        con.execute(f'COPY "{name}" FROM \'{path_sql}\' (HEADER FALSE)')
    finally:
        temporary_csv.unlink(missing_ok=True)


def load_amenities(rows: list[dict[str, str]], category: str | None = None) -> tuple[list[tuple], dict[str, list[tuple[float, float]]]]:
    output = []
    locations: dict[str, list[tuple[float, float]]] = {name: [] for name in NEARBY_CATEGORIES}
    seen_ids = set()
    for row in rows:
        cat = row["amenity_category"]
        if category is not None and cat != category:
            continue
        if category is None and AMENITY_SOURCES.get(cat) != row["source_name"]:
            continue
        point = coordinate(row)
        if point is None:
            continue
        properties = json.loads(row["source_properties_json"])
        if cat == "childcare_centres":
            code = str(properties.get("centre_code", "")).strip()
            identifier = f"ECDA:{code}" if code and code.lower() != "na" else stable_id("ECDA", row["name"], row["address"])
        elif cat == "healthcare_clinics":
            identifier = f"CHAS:{properties['Attributes HCI_CODE']}"
        elif cat == "primary_schools":
            identifier = stable_id("SCHOOL", normalise(row["name"]), row["postal_code"])
        elif cat == "polyclinics":
            identifier = stable_id("POLY", normalise(row["name"]))
        elif cat == "hospitals":
            identifier = stable_id("HOSP", normalise(row["name"]), *point)
        else:
            identifier = stable_id("AMENITY", cat, row["source_name"], normalise(row["name"]), *point)
        if identifier in seen_ids:
            raise ValueError(f"Duplicate facility identifier: {identifier}")
        seen_ids.add(identifier)
        fields = [row[column] for column in AMENITY_COLUMNS]
        fields[8], fields[9] = point
        output.append((identifier, *fields))
        locations[cat].append(point)
    return output, locations


def build(project_root: Path) -> dict:
    processed = project_root / "data" / "processed"
    paths = {name: processed / filename for name, filename in SOURCES.items()}
    checksums = {name: file_sha256(path) for name, path in paths.items() if path.is_file()}
    if len(checksums) != len(SOURCES):
        missing = [name for name, path in paths.items() if not path.is_file()]
        raise ValueError(f"Missing processed sources: {', '.join(missing)}")
    source_rows = {
        "transactions": read_csv(paths["transactions"], {"month", "town", "flat_type", "block", "street_name", "storey_range", "floor_area_sqm", "flat_model", "lease_commence_date", "remaining_lease", "resale_price"}),
        "properties": read_csv(paths["properties"], {"blk_no", "street", "year_completed", "max_floor_lvl", "total_dwelling_units", "bldg_contract_town", "residential", "commercial", "market_hawker", "miscellaneous", "multistorey_carpark", "precinct_pavilion", "1room_sold", "2room_sold", "3room_sold", "4room_sold", "5room_sold", "exec_sold", "multigen_sold", "studio_apartment_sold", "1room_rental", "2room_rental", "3room_rental", "other_room_rental"}),
        "blocks": read_csv(paths["blocks"], {"block", "street", "query", "match_status", "matched_address", "postal_code", "latitude", "longitude", "retrieved_at_utc"}),
        "schools": read_csv(paths["schools"], set(AMENITY_COLUMNS)),
        "childcare": read_csv(paths["childcare"], set(AMENITY_COLUMNS)),
        "healthcare": read_csv(paths["healthcare"], set(AMENITY_COLUMNS)),
        "amenities": read_csv(paths["amenities"], set(AMENITY_COLUMNS)),
    }
    source_counts = {name: len(rows) for name, rows in source_rows.items()}
    run_id = stable_id("BUILD", *[checksums[name] for name in sorted(checksums)])
    now = datetime.now(timezone.utc).isoformat()
    data_quality_issues: list[dict[str, object]] = []

    block_rows, block_ids, block_coords, seen_block_keys = [], {}, {}, set()
    blocks_without_coordinates = 0
    for row_number, row in enumerate(source_rows["blocks"], start=2):
        key = block_key(row["block"], row["street"])
        if key in seen_block_keys:
            data_quality_issues.append({
                "source": "hdb_blocks_with_coordinates.csv",
                "row_number": row_number,
                "block_key": key,
                "reason": "Duplicate block address; duplicate row was not used.",
            })
            continue
        seen_block_keys.add(key)
        coordinate_error = ""
        try:
            point = coordinate(row)
        except ValueError as error:
            point = None
            coordinate_error = str(error)
        identifier = stable_id("BLOCK", key)
        block_ids[key] = identifier
        if point is None:
            blocks_without_coordinates += 1
            latitude, longitude = None, None
            reason = coordinate_error or (
                f"No coordinates returned by OneMap (match status: {row['match_status']}); "
                "block retained with blank location features."
            )
            data_quality_issues.append({
                "source": "hdb_blocks_with_coordinates.csv",
                "row_number": row_number,
                "block_key": key,
                "reason": reason,
            })
        else:
            block_coords[identifier] = point
            latitude, longitude = point
        block_rows.append((identifier, row["block"], row["street"], row["query"], row["match_status"], row["matched_address"], row["postal_code"], latitude, longitude, row["retrieved_at_utc"]))

    property_rows, property_ids = [], set()
    count_columns = (
        "1room_sold", "2room_sold", "3room_sold", "4room_sold", "5room_sold", "exec_sold",
        "multigen_sold", "studio_apartment_sold", "1room_rental", "2room_rental",
        "3room_rental", "other_room_rental",
    )
    flag_columns = ("residential", "commercial", "market_hawker", "miscellaneous", "multistorey_carpark", "precinct_pavilion")
    for row_number, row in enumerate(source_rows["properties"], start=2):
        key = block_key(row["blk_no"], row["street"])
        if key not in block_ids:
            data_quality_issues.append({
                "source": "hdb_property_information.csv",
                "row_number": row_number,
                "block_key": key,
                "reason": "No matching HDB block record; property row was not used.",
            })
            continue
        identifier = block_ids[key]
        if identifier in property_ids:
            data_quality_issues.append({
                "source": "hdb_property_information.csv",
                "row_number": row_number,
                "block_key": key,
                "reason": "Duplicate property block; duplicate row was not used.",
            })
            continue
        flags = tuple(row[field].strip().upper() == "Y" for field in flag_columns)
        try:
            max_floor_lvl = int(row["max_floor_lvl"])
            year_completed = int(row["year_completed"])
            total_dwelling_units = int(row["total_dwelling_units"])
            counts = tuple(int(row[field]) for field in count_columns)
        except (TypeError, ValueError) as error:
            data_quality_issues.append({
                "source": "hdb_property_information.csv",
                "row_number": row_number,
                "block_key": key,
                "reason": f"Invalid property numeric value; row was not used ({error}).",
            })
            continue
        property_ids.add(identifier)
        property_rows.append((identifier, row["blk_no"], row["street"], max_floor_lvl, year_completed, *flags, row["bldg_contract_town"], total_dwelling_units, *counts))

    transaction_rows, occurrence_counts = [], Counter()
    transaction_fields = tuple(source_rows["transactions"][0].keys())
    for row_number, row in enumerate(source_rows["transactions"], start=2):
        key = block_key(row["block"], row["street_name"])
        identifier = block_ids.get(key)
        if identifier is None or identifier not in property_ids:
            data_quality_issues.append({
                "source": "hdb_resale_transactions.csv",
                "row_number": row_number,
                "transaction_month": row["month"],
                "town": row["town"],
                "block_key": key,
                "reason": "No matching HDB block and property records; transaction was not used.",
            })
            continue
        source_tuple = tuple(row[field] for field in transaction_fields)
        try:
            price = float(row["resale_price"])
            area = float(row["floor_area_sqm"])
            storey_midpoint = parse_storey(row["storey_range"])
            remaining_lease_months = parse_lease(row["remaining_lease"])
            lease_commence_year = int(row["lease_commence_date"])
        except (TypeError, ValueError) as error:
            data_quality_issues.append({
                "source": "hdb_resale_transactions.csv",
                "row_number": row_number,
                "transaction_month": row["month"],
                "town": row["town"],
                "block_key": key,
                "reason": f"Invalid transaction value; row was not used ({error}).",
            })
            continue
        if price <= 0 or area <= 0:
            data_quality_issues.append({
                "source": "hdb_resale_transactions.csv",
                "row_number": row_number,
                "transaction_month": row["month"],
                "town": row["town"],
                "block_key": key,
                "reason": "Price or floor area is not positive; transaction was not used.",
            })
            continue
        occurrence_counts[source_tuple] += 1
        transaction_id = stable_id("TX", *source_tuple, occurrence_counts[source_tuple])
        transaction_rows.append((transaction_id, identifier, f'{row["month"]}-01', row["town"], row["flat_type"], row["block"], row["street_name"], row["storey_range"], storey_midpoint, area, row["flat_model"], lease_commence_year, row["remaining_lease"], remaining_lease_months, price))

    schools, school_locations = load_amenities(source_rows["schools"], "primary_schools")
    childcare, childcare_locations = load_amenities(source_rows["childcare"], "childcare_centres")
    healthcare = []
    locations = {name: [] for name in NEARBY_CATEGORIES}
    for category in ("healthcare_clinics", "hospitals", "polyclinics"):
        entries, points = load_amenities(source_rows["healthcare"], category)
        healthcare.extend(entries)
        locations[category].extend(points[category])
    amenities, amenity_locations = load_amenities(source_rows["amenities"])
    for result in (school_locations, childcare_locations, amenity_locations):
        for category, points in result.items():
            locations[category].extend(points)
    if any(not locations[category] for category in NEARBY_CATEGORIES):
        raise ValueError("One or more trusted amenity categories have no locations")

    feature_rows = []
    for identifier in sorted(property_ids):
        point = block_coords.get(identifier)
        if point is None:
            feature_rows.append((identifier, *([None, None] * len(NEARBY_CATEGORIES))))
            continue
        lat, lon = point
        values = []
        for category in NEARBY_CATEGORIES:
            distances = [haversine_m(lat, lon, other_lat, other_lon) for other_lat, other_lon in locations[category]]
            values.extend((round(min(distances), 1), sum(distance <= 1000 for distance in distances)))
        feature_rows.append((identifier, *values))

    data_dir = project_root / "data"
    model_dir = data_dir / "model_ready"
    report_dir = project_root / "reports/phase_1_resale_model"
    model_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    database = data_dir / "property.duckdb"
    parquet = model_dir / "hdb_transaction_features.parquet"
    report = report_dir / "03_duckdb_build_summary.json"
    suffix = uuid.uuid4().hex[:8]
    temporary_database = data_dir / f"property.{suffix}.tmp.duckdb"
    temporary_parquet = model_dir / f"hdb_transaction_features.{suffix}.tmp.parquet"

    def amenity_schema(key_name: str) -> list[tuple[str, str]]:
        return [(key_name, "VARCHAR PRIMARY KEY")] + [
            (name, "DOUBLE" if name in {"latitude", "longitude"} else "VARCHAR")
            for name in AMENITY_COLUMNS
        ]
    try:
        con = duckdb.connect(str(temporary_database))
        try:
            create_table(con, "hdb_blocks", [("block_id", "VARCHAR PRIMARY KEY"), ("block", "VARCHAR"), ("street", "VARCHAR"), ("query", "VARCHAR"), ("match_status", "VARCHAR"), ("matched_address", "VARCHAR"), ("postal_code", "VARCHAR"), ("latitude", "DOUBLE"), ("longitude", "DOUBLE"), ("retrieved_at_utc", "VARCHAR")], block_rows)
            property_schema = [("block_id", "VARCHAR PRIMARY KEY"), ("blk_no", "VARCHAR"), ("street", "VARCHAR"), ("max_floor_lvl", "INTEGER"), ("year_completed", "INTEGER")]
            property_schema += [(name, "BOOLEAN") for name in flag_columns]
            property_schema += [("bldg_contract_town", "VARCHAR"), ("total_dwelling_units", "INTEGER")]
            property_schema += [(name, "INTEGER") for name in count_columns]
            create_table(con, "hdb_properties", property_schema, property_rows)
            create_table(con, "resale_transactions", [("transaction_id", "VARCHAR PRIMARY KEY"), ("block_id", "VARCHAR"), ("transaction_month", "DATE"), ("town", "VARCHAR"), ("flat_type", "VARCHAR"), ("block", "VARCHAR"), ("street_name", "VARCHAR"), ("storey_range", "VARCHAR"), ("storey_midpoint", "DOUBLE"), ("floor_area_sqm", "DOUBLE"), ("flat_model", "VARCHAR"), ("lease_commence_year", "INTEGER"), ("remaining_lease", "VARCHAR"), ("remaining_lease_months", "INTEGER"), ("resale_price", "DOUBLE")], transaction_rows)
            for table, key_name, rows in (
                ("primary_schools", "school_id", schools),
                ("childcare_centres", "centre_id", childcare),
                ("healthcare_facilities", "facility_id", healthcare),
                ("amenities", "amenity_id", amenities),
            ):
                create_table(con, table, amenity_schema(key_name), rows)
            feature_schema = [("block_id", "VARCHAR PRIMARY KEY")]
            for category in NEARBY_CATEGORIES:
                feature_schema += [(f"nearest_{category}_m", "DOUBLE"), (f"{category}_within_1km", "INTEGER")]
            create_table(con, "block_location_features", feature_schema, feature_rows)
            con.execute("""
                CREATE TABLE transaction_features AS
                SELECT t.transaction_id, t.block_id, t.transaction_month, t.town,
                       t.flat_type, t.floor_area_sqm, t.flat_model, t.storey_range,
                       t.storey_midpoint, t.lease_commence_year,
                       t.remaining_lease_months, p.year_completed,
                       YEAR(t.transaction_month) - p.year_completed AS building_age_at_transaction,
                       p.max_floor_lvl, p.total_dwelling_units, p.commercial,
                       p.market_hawker, p.multistorey_carpark, p.precinct_pavilion,
                       b.latitude, b.longitude,
                       l.* EXCLUDE (block_id),
                       YEAR(t.transaction_month) AS transaction_year,
                       MONTH(t.transaction_month) AS transaction_month_number,
                       QUARTER(t.transaction_month) AS transaction_quarter,
                       CASE
                           WHEN t.transaction_month >= (SELECT MAX(transaction_month) - INTERVAL 11 MONTH FROM resale_transactions) THEN 'test'
                           WHEN t.transaction_month >= (SELECT MAX(transaction_month) - INTERVAL 23 MONTH FROM resale_transactions) THEN 'validation'
                           ELSE 'train'
                       END AS dataset_split,
                       t.resale_price
                FROM resale_transactions t
                JOIN hdb_blocks b USING (block_id)
                JOIN hdb_properties p USING (block_id)
                JOIN block_location_features l USING (block_id)
            """)
            con.execute("CREATE TABLE pipeline_runs (run_id VARCHAR PRIMARY KEY, built_at_utc VARCHAR, source_checksums_json VARCHAR, transaction_count INTEGER)")
            if database.is_file():
                with duckdb.connect(str(database), read_only=True) as old:
                    try:
                        old_runs = old.execute("SELECT run_id, built_at_utc, source_checksums_json, transaction_count FROM pipeline_runs").fetchall()
                    except duckdb.Error:
                        old_runs = []
                prior_runs = [run for run in old_runs if run[0] != run_id]
                if prior_runs:
                    con.executemany("INSERT INTO pipeline_runs VALUES (?, ?, ?, ?)", prior_runs)
            con.execute("INSERT INTO pipeline_runs VALUES (?, ?, ?, ?)", [run_id, now, json.dumps(checksums, sort_keys=True), len(transaction_rows)])
            counts = {
                table: con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                for table in ("hdb_blocks", "hdb_properties", "resale_transactions", "primary_schools", "childcare_centres", "healthcare_facilities", "amenities", "block_location_features", "transaction_features")
            }
            if counts["transaction_features"] != counts["resale_transactions"]:
                raise ValueError("Transaction feature join changed the number of transactions")
            if counts["block_location_features"] != counts["hdb_properties"]:
                raise ValueError("Block location feature count does not match HDB properties")
            null_check = con.execute("SELECT COUNT(*) FROM transaction_features WHERE transaction_id IS NULL OR block_id IS NULL OR resale_price IS NULL").fetchone()[0]
            if null_check:
                raise ValueError(f"Model-ready table has {null_check} missing critical fields")
            duplicate_check = con.execute("SELECT COUNT(*) - COUNT(DISTINCT transaction_id) FROM transaction_features").fetchone()[0]
            if duplicate_check:
                raise ValueError(f"Model-ready table has {duplicate_check} duplicate transaction IDs")
            split_counts = dict(con.execute("SELECT dataset_split, COUNT(*) FROM transaction_features GROUP BY dataset_split").fetchall())
            if any(split_counts.get(name, 0) == 0 for name in ("train", "validation", "test")):
                raise ValueError("A time-based dataset split is empty")
            con.execute("COPY transaction_features TO ? (FORMAT PARQUET)", [str(temporary_parquet)])
        finally:
            con.close()
        # Check that extraction did not change a source during this build.
        if any(file_sha256(paths[name]) != checksum for name, checksum in checksums.items()):
            raise ValueError("A source file changed during the database build; rerun after extraction finishes")
        os.replace(temporary_parquet, parquet)
        os.replace(temporary_database, database)
        result = {
            "status": "success_with_data_issues" if data_quality_issues else "success",
            "run_id": run_id,
            "built_at_utc": now,
            "source_rows": source_counts,
            "table_rows": counts,
            "dataset_split_rows": split_counts,
            "quality": {"hdb_blocks_with_missing_coordinates": blocks_without_coordinates},
            "data_quality_issue_count": len(data_quality_issues),
            "data_quality_issues": data_quality_issues,
            "excluded": {
                "healthcare_without_coordinates": len(source_rows["healthcare"]) - len(healthcare),
                "untrusted_or_missing_amenities": len(source_rows["amenities"]) - len(amenities),
            },
            "database": str(database),
            "model_ready_parquet": str(parquet),
        }
        report.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
    finally:
        temporary_parquet.unlink(missing_ok=True)
        temporary_database.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    try:
        result = build(args.project_root.resolve())
    except (ValueError, OSError, duckdb.Error) as error:
        print(f"DuckDB build failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
