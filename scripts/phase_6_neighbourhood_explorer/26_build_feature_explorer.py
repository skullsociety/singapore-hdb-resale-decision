"""Build the Phase 6 neighbourhood and feature-explorer data layer."""

from __future__ import annotations

import argparse
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb


def load_feature_analysis(project_root: Path):
    path = project_root / "scripts" / "phase_1_resale_model" / "05_analyse_feature_price_correlations.py"
    spec = importlib.util.spec_from_file_location("phase1_feature_analysis", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load the feature-analysis code: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def create_explorer_layer(
    connection: duckdb.DuckDBPyConnection,
    property_database: Path,
    associations: list[dict],
    built_at: str,
) -> dict:
    connection.execute(f"ATTACH '{sql_path(property_database)}' AS property_source (READ_ONLY)")
    connection.execute("BEGIN TRANSACTION")
    try:
        for name in (
            "dashboard_explorer_transactions", "dashboard_explorer_monthly_trends",
            "dashboard_explorer_blocks", "dashboard_feature_associations",
        ):
            connection.execute(f"DROP VIEW IF EXISTS {name}")
        connection.execute("DROP TABLE IF EXISTS explorer_transactions")
        connection.execute("DROP TABLE IF EXISTS explorer_feature_associations")
        connection.execute("DROP TABLE IF EXISTS explorer_build_metadata")

        connection.execute("""
            CREATE TABLE explorer_transactions AS
            SELECT transaction_id, block_id, transaction_month, town, flat_type,
                   floor_area_sqm, flat_model, storey_range, storey_midpoint,
                   remaining_lease_months, building_age_at_transaction,
                   latitude, longitude,
                   nearest_primary_schools_m, primary_schools_within_1km,
                   nearest_childcare_centres_m, childcare_centres_within_1km,
                   nearest_healthcare_clinics_m, healthcare_clinics_within_1km,
                   nearest_polyclinics_m, polyclinics_within_1km,
                   nearest_hospitals_m, hospitals_within_1km,
                   nearest_community_clubs_m, community_clubs_within_1km,
                   nearest_hawker_centres_m, hawker_centres_within_1km,
                   nearest_mrt_lrt_stations_m, mrt_lrt_stations_within_1km,
                   nearest_parks_m, parks_within_1km,
                   nearest_public_libraries_m, public_libraries_within_1km,
                   dataset_split, resale_price,
                   resale_price / NULLIF(floor_area_sqm, 0) AS price_per_sqm
            FROM property_source.transaction_features
        """)
        connection.execute(
            "CREATE UNIQUE INDEX explorer_transactions_key ON explorer_transactions(transaction_id)"
        )

        connection.execute("""
            CREATE TABLE explorer_feature_associations (
                column_name VARCHAR PRIMARY KEY,
                data_type VARCHAR,
                analysed_rows BIGINT,
                missing_rows BIGINT,
                distinct_values BIGINT,
                association_method VARCHAR,
                pearson_r DOUBLE,
                spearman_rho DOUBLE,
                within_flat_type_pearson_r DOUBLE,
                eta_squared DOUBLE,
                association_strength VARCHAR,
                direction VARCHAR,
                interpretation VARCHAR,
                built_at_utc VARCHAR)
        """)
        if associations:
            connection.executemany(
                "INSERT INTO explorer_feature_associations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        row["column"], row["data_type"], row["rows"], row["missing_rows"],
                        row["distinct_values"], row["association_method"], row["pearson_r"],
                        row["spearman_rho"], row["within_flat_type_pearson_r"],
                        row["eta_squared"], row["association_strength"], row["direction"],
                        row["interpretation"], built_at,
                    )
                    for row in associations
                ],
            )

        connection.execute("""
            CREATE TABLE explorer_build_metadata AS
            SELECT ?::VARCHAR AS built_at_utc,
                   COUNT(*)::BIGINT AS transaction_count,
                   COUNT(DISTINCT block_id)::BIGINT AS block_count,
                   MIN(transaction_month) AS earliest_transaction_month,
                   MAX(transaction_month) AS latest_transaction_month,
                   COUNT(DISTINCT town)::BIGINT AS town_count,
                   COUNT(DISTINCT flat_type)::BIGINT AS flat_type_count
            FROM explorer_transactions
        """, [built_at])

        connection.execute("""
            CREATE VIEW dashboard_explorer_transactions AS
            SELECT *, remaining_lease_months / 12.0 AS remaining_lease_years
            FROM explorer_transactions
        """)
        connection.execute("""
            CREATE VIEW dashboard_explorer_monthly_trends AS
            SELECT transaction_month, town, flat_type,
                   COUNT(*) AS transaction_count,
                   MEDIAN(resale_price) AS median_resale_price_sgd,
                   MEDIAN(price_per_sqm) AS median_price_per_sqm
            FROM explorer_transactions
            GROUP BY transaction_month, town, flat_type
        """)
        connection.execute("""
            CREATE VIEW dashboard_explorer_blocks AS
            SELECT block_id, town, flat_type,
                   MEDIAN(latitude) AS latitude,
                   MEDIAN(longitude) AS longitude,
                   COUNT(*) AS transaction_count,
                   MIN(transaction_month) AS earliest_transaction_month,
                   MAX(transaction_month) AS latest_transaction_month,
                   MEDIAN(resale_price) AS median_resale_price_sgd,
                   MEDIAN(price_per_sqm) AS median_price_per_sqm,
                   MEDIAN(floor_area_sqm) AS median_floor_area_sqm,
                   MEDIAN(remaining_lease_months) / 12.0 AS median_remaining_lease_years,
                   MEDIAN(nearest_mrt_lrt_stations_m) AS nearest_mrt_lrt_stations_m,
                   MEDIAN(nearest_primary_schools_m) AS nearest_primary_schools_m,
                   MEDIAN(nearest_parks_m) AS nearest_parks_m
            FROM explorer_transactions
            GROUP BY block_id, town, flat_type
        """)
        connection.execute("""
            CREATE VIEW dashboard_feature_associations AS
            SELECT * FROM explorer_feature_associations
            WHERE column_name NOT IN ('resale_price', 'transaction_id', 'block_id', 'dataset_split')
        """)
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.execute("DETACH property_source")

    metadata = connection.execute("SELECT * FROM explorer_build_metadata").fetchone()
    return {
        "built_at_utc": metadata[0],
        "transaction_count": metadata[1],
        "block_count": metadata[2],
        "earliest_transaction_month": str(metadata[3]),
        "latest_transaction_month": str(metadata[4]),
        "town_count": metadata[5],
        "flat_type_count": metadata[6],
        "association_count": connection.execute(
            "SELECT COUNT(*) FROM dashboard_feature_associations"
        ).fetchone()[0],
    }


def build(project_root: Path) -> dict:
    property_database = project_root / "data" / "property.duckdb"
    listing_database = project_root / "data" / "listings.db"
    if not property_database.is_file() or not listing_database.is_file():
        raise ValueError("Run the earlier database and listing steps first; both DuckDB files are required")

    analysis = load_feature_analysis(project_root)
    associations, association_metadata = analysis.analyse(project_root)
    built_at = datetime.now(timezone.utc).isoformat()
    with duckdb.connect(str(listing_database)) as connection:
        summary = create_explorer_layer(
            connection, property_database, associations, built_at
        )
    summary["status"] = "success"
    summary["association_scope"] = association_metadata["analysis_population"]
    summary["database"] = str(listing_database)

    report_path = (
        project_root / "reports" / "phase_6_neighbourhood_explorer"
        / "26_feature_explorer_summary.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    summary["summary_report"] = str(report_path)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root", type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.project_root.resolve()), indent=2))
    except (ValueError, RuntimeError, duckdb.Error) as error:
        raise SystemExit(f"Feature explorer build failed: {error}") from error


if __name__ == "__main__":
    main()
