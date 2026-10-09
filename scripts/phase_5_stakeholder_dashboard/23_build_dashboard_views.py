"""Create stable Phase 5 presentation views in the listing DuckDB database."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb


REQUIRED_TABLES = {
    "listings", "listing_features", "listing_valuations", "listing_snapshot_history",
    "listing_snapshot_runs", "market_watch_preferences", "market_watch_rankings",
}


def model_metrics(property_database: Path) -> list[tuple]:
    project_root = property_database.resolve().parent.parent
    release_path = project_root / "reports/phase_1_resale_model/09_blend_release.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    if release.get("status") != "national_candidate":
        raise ValueError("No approved model release is available; review the Step 09 release report")
    released_model_id = release["model_id"]
    with duckdb.connect(str(property_database), read_only=True) as connection:
        return connection.execute("""
            SELECT m.model_id, COALESCE(r.model_name, m.model_id) AS model_name,
                   COALESCE(r.model_type, 'accepted_blend') AS model_type,
                   m.dataset_split, m.prediction_count, m.mae,
                   m.median_absolute_error, m.mape_pct, m.median_ape_pct,
                   m.rmse, m.r_squared, m.mean_bias, m.interval_coverage_pct,
                   m.mean_interval_width
            FROM (
                SELECT * FROM model_evaluation_metrics WHERE model_id <> ?
                UNION ALL
                SELECT p.* FROM pilot_blend_backtest_metrics p
                WHERE p.model_id = ?
            ) m
            LEFT JOIN model_registry r USING (model_id)
            ORDER BY m.model_id, m.dataset_split
        """, [released_model_id, released_model_id]).fetchall()


def create_views(connection: duckdb.DuckDBPyConnection, metrics: list[tuple], built_at: str) -> None:
    available = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
    missing = REQUIRED_TABLES - available
    if missing:
        raise ValueError("Run Steps 17–22 first; missing tables: " + ", ".join(sorted(missing)))

    view_names = (
        "dashboard_market_overview", "dashboard_current_listings",
        "dashboard_listing_details", "dashboard_listing_changes",
        "dashboard_comparables", "dashboard_data_quality",
        "dashboard_model_performance",
    )
    connection.execute("BEGIN TRANSACTION")
    try:
        for name in view_names:
            connection.execute(f"DROP VIEW IF EXISTS {name}")
        connection.execute("DROP TABLE IF EXISTS dashboard_category_rules")
        connection.execute("DROP TABLE IF EXISTS dashboard_model_metrics")
        connection.execute("""
            CREATE TABLE dashboard_category_rules (
                ruleset_id VARCHAR PRIMARY KEY,
                negotiation_margin_above_upper_pct DOUBLE,
                unusually_low_below_lower_pct DOUBLE,
                description VARCHAR,
                built_at_utc VARCHAR)
        """)
        connection.execute("INSERT INTO dashboard_category_rules VALUES (?,?,?,?,?)", [
            "STAKEHOLDER_CATEGORIES_V1", 8.0, 5.0,
            "Configurable presentation labels. They screen listings and do not declare a bargain.",
            built_at,
        ])
        connection.execute("""
            CREATE TABLE dashboard_model_metrics (
                model_id VARCHAR, model_name VARCHAR, model_type VARCHAR,
                dataset_split VARCHAR, prediction_count BIGINT, mae DOUBLE,
                median_absolute_error DOUBLE, mape_pct DOUBLE,
                median_ape_pct DOUBLE, rmse DOUBLE, r_squared DOUBLE,
                mean_bias DOUBLE, interval_coverage_pct DOUBLE,
                mean_interval_width DOUBLE, copied_at_utc VARCHAR,
                PRIMARY KEY(model_id, dataset_split))
        """)
        if metrics:
            connection.executemany(
                "INSERT INTO dashboard_model_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [list(row) + [built_at] for row in metrics],
            )

        connection.execute("""
            CREATE VIEW dashboard_current_listings AS
            SELECT r.profile_id, p.profile_name, p.town,
                   r.source_site, r.run_id, r.listing_id, r.listing_url,
                   r.title, r.address, f.block_id, f.matched_block AS block,
                   f.matched_street AS street, f.postal_code,
                   r.inferred_flat_type, r.floor_area_sqm, f.bedrooms, f.bathrooms,
                   r.asking_price_sgd, r.lower_estimate_sgd,
                   r.point_estimate_sgd, r.upper_estimate_sgd,
                   r.asking_premium_discount_sgd,
                   r.asking_premium_discount_pct,
                   r.minimum_comparable_count, r.confidence_label,
                   v.recent_town_flat_training_sales, v.minimum_recent_training_sales,
                   r.listing_age_days, r.ranking_score, r.eligible,
                   r.ranking_notes, r.change_type, f.first_seen_utc,
                   f.last_seen_utc, v.valuation_status, v.model_id,
                   CASE
                       WHEN v.valuation_status <> 'estimated'
                            OR r.point_estimate_sgd IS NULL
                            OR r.minimum_comparable_count < p.minimum_comparable_count
                           THEN 'insufficient_evidence'
                       WHEN NOT r.eligible THEN 'does_not_match'
                       WHEN r.asking_price_sgd <= r.point_estimate_sgd
                           THEN 'strong_candidate'
                       WHEN r.asking_price_sgd <= r.upper_estimate_sgd
                           THEN 'fairly_priced'
                       WHEN r.asking_price_sgd <= r.upper_estimate_sgd *
                            (1 + rules.negotiation_margin_above_upper_pct / 100)
                           THEN 'negotiation_candidate'
                       ELSE 'likely_expensive'
                   END AS stakeholder_category,
                   CASE
                       WHEN r.lower_estimate_sgd IS NOT NULL
                            AND r.asking_price_sgd < r.lower_estimate_sgd *
                                (1 - rules.unusually_low_below_lower_pct / 100)
                           THEN TRUE ELSE FALSE
                   END AS verify_low_price,
                   CASE
                       WHEN v.valuation_status <> 'estimated' THEN v.valuation_note
                       WHEN NOT r.eligible THEN COALESCE(r.ranking_notes, 'Does not meet required preferences')
                       WHEN r.asking_price_sgd <= r.point_estimate_sgd
                           THEN 'Asking price is at or below the research point estimate.'
                       WHEN r.asking_price_sgd <= r.upper_estimate_sgd
                           THEN 'Asking price is above the point estimate but within the research range.'
                       WHEN r.asking_price_sgd <= r.upper_estimate_sgd *
                            (1 + rules.negotiation_margin_above_upper_pct / 100)
                           THEN 'Asking price is moderately above the research range.'
                       ELSE 'Asking price is materially above the research range.'
                   END AS category_reason
            FROM market_watch_rankings r
            JOIN market_watch_preferences p ON p.profile_id = r.profile_id
            JOIN listing_features f
              ON f.source_site = r.source_site AND f.run_id = r.run_id
             AND f.listing_id = r.listing_id
            JOIN listing_valuations v
              ON v.source_site = r.source_site AND v.run_id = r.run_id
             AND v.listing_id = r.listing_id
            CROSS JOIN dashboard_category_rules rules
        """)

        connection.execute("""
            CREATE VIEW dashboard_listing_details AS
            SELECT d.*, f.latitude, f.longitude, f.year_completed, f.max_floor_lvl,
                   f.total_dwelling_units, f.flat_type_inference_method,
                   f.flat_type_area_gap_sqm, f.match_confidence,
                   f.nearest_primary_schools_m, f.primary_schools_within_1km,
                   f.nearest_childcare_centres_m, f.childcare_centres_within_1km,
                   f.nearest_healthcare_clinics_m, f.healthcare_clinics_within_1km,
                   f.nearest_polyclinics_m, f.polyclinics_within_1km,
                   f.nearest_hospitals_m, f.hospitals_within_1km,
                   f.nearest_community_clubs_m, f.community_clubs_within_1km,
                   f.nearest_hawker_centres_m, f.hawker_centres_within_1km,
                   f.nearest_mrt_lrt_stations_m, f.mrt_lrt_stations_within_1km,
                   f.nearest_parks_m, f.parks_within_1km,
                   f.nearest_public_libraries_m, f.public_libraries_within_1km,
                   v.assumption_method, v.scenario_count, v.scenarios_json,
                   v.valuation_note, v.valuation_month
            FROM dashboard_current_listings d
            JOIN listing_features f USING (source_site, run_id, listing_id)
            JOIN listing_valuations v USING (source_site, run_id, listing_id)
        """)

        connection.execute("""
            CREATE VIEW dashboard_market_overview AS
            SELECT profile_id, profile_name, source_site, run_id, town,
                   COALESCE(inferred_flat_type, 'Unknown') AS flat_type,
                   COUNT(*) AS listing_count,
                   COUNT(*) FILTER (WHERE valuation_status = 'estimated') AS valued_count,
                   COUNT(*) FILTER (WHERE stakeholder_category = 'strong_candidate') AS strong_candidate_count,
                   COUNT(*) FILTER (WHERE stakeholder_category = 'fairly_priced') AS fairly_priced_count,
                   COUNT(*) FILTER (WHERE stakeholder_category = 'negotiation_candidate') AS negotiation_candidate_count,
                   COUNT(*) FILTER (WHERE stakeholder_category = 'likely_expensive') AS likely_expensive_count,
                   COUNT(*) FILTER (WHERE stakeholder_category = 'insufficient_evidence') AS insufficient_evidence_count,
                   MEDIAN(asking_price_sgd) AS median_asking_price_sgd,
                   MEDIAN(asking_premium_discount_pct) AS median_premium_discount_pct,
                   MAX(last_seen_utc) AS latest_observation_utc
            FROM dashboard_current_listings
            GROUP BY profile_id, profile_name, source_site, run_id, town,
                     COALESCE(inferred_flat_type, 'Unknown')
        """)

        connection.execute("""
            CREATE VIEW dashboard_listing_changes AS
            SELECT h.source_site, h.run_id, h.listing_id, h.snapshot_sequence,
                   l.title, l.address, l.block, l.street, l.floor_area_sqm,
                   h.listing_url, h.observed_at_utc, h.asking_price_sgd,
                   h.previous_asking_price_sgd, h.price_change_sgd, h.change_type
            FROM listing_snapshot_history h
            JOIN listings l USING (source_site, run_id, listing_id)
        """)

        connection.execute("""
            CREATE VIEW dashboard_comparables AS
            SELECT v.source_site, v.run_id, v.listing_id,
                   json_extract_string(s.value, '$.scenario') AS scenario,
                   json_extract_string(s.value, '$.storey_range') AS assumed_storey_range,
                   CAST(json_extract(s.value, '$.point') AS DOUBLE) AS scenario_point_estimate_sgd,
                   json_extract_string(c.value, '$.transaction_id') AS comparable_transaction_id,
                   json_extract_string(c.value, '$.block') AS comparable_block,
                   json_extract_string(c.value, '$.street') AS comparable_street,
                   json_extract_string(c.value, '$.sale_month') AS sale_month,
                   CAST(json_extract(c.value, '$.sale_price_sgd') AS DOUBLE) AS sale_price_sgd,
                   CAST(json_extract(c.value, '$.floor_area_sqm') AS DOUBLE) AS floor_area_sqm,
                   json_extract_string(c.value, '$.storey_range') AS storey_range,
                   CAST(json_extract(c.value, '$.distance_m') AS DOUBLE) AS distance_m
            FROM listing_valuations v,
                 json_each(v.scenarios_json) s,
                 json_each(json_extract(s.value, '$.comparables')) c
        """)

        connection.execute("""
            CREATE VIEW dashboard_data_quality AS
            WITH totals AS (
                SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE block_id IS NOT NULL) AS matched,
                       COUNT(*) FILTER (WHERE valuation_status = 'estimated') AS valued,
                       COUNT(*) FILTER (WHERE listing_url IS NOT NULL) AS linked,
                       COUNT(*) FILTER (WHERE floor_area_sqm IS NOT NULL) AS with_area,
                       COUNT(*) FILTER (WHERE verify_low_price) AS low_price_flags
                FROM dashboard_current_listings
            )
            SELECT 'current_listing_count' AS check_name, total::DOUBLE AS observed_value,
                   'count' AS unit, 'informational' AS status FROM totals
            UNION ALL SELECT 'block_match_coverage', 100.0 * matched / NULLIF(total, 0), 'percent',
                   CASE WHEN 100.0 * matched / NULLIF(total, 0) >= 95 THEN 'pass' ELSE 'review' END FROM totals
            UNION ALL SELECT 'valuation_coverage', 100.0 * valued / NULLIF(total, 0), 'percent',
                   CASE WHEN 100.0 * valued / NULLIF(total, 0) >= 90 THEN 'pass' ELSE 'review' END FROM totals
            UNION ALL SELECT 'listing_url_coverage', 100.0 * linked / NULLIF(total, 0), 'percent',
                   CASE WHEN linked = total THEN 'pass' ELSE 'review' END FROM totals
            UNION ALL SELECT 'floor_area_coverage', 100.0 * with_area / NULLIF(total, 0), 'percent',
                   CASE WHEN 100.0 * with_area / NULLIF(total, 0) >= 95 THEN 'pass' ELSE 'review' END FROM totals
            UNION ALL SELECT 'unusually_low_price_flags', low_price_flags::DOUBLE, 'count',
                   CASE WHEN low_price_flags = 0 THEN 'pass' ELSE 'review' END FROM totals
            UNION ALL SELECT 'complete_snapshot_count', COUNT(*)::DOUBLE, 'count',
                   CASE WHEN COUNT(*) >= 2 THEN 'pass' ELSE 'pending' END FROM listing_snapshot_runs
        """)
        connection.execute("CREATE VIEW dashboard_model_performance AS SELECT * FROM dashboard_model_metrics")
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise


def build(project_root: Path) -> dict:
    listing_database = project_root / "data" / "listings.db"
    property_database = project_root / "data" / "property.duckdb"
    if not listing_database.is_file() or not property_database.is_file():
        raise ValueError("Both data/listings.db and data/property.duckdb are required")
    built_at = datetime.now(timezone.utc).isoformat()
    metrics = model_metrics(property_database)
    with duckdb.connect(str(listing_database)) as connection:
        create_views(connection, metrics, built_at)
        views = [row[0] for row in connection.execute(
            "SELECT table_name FROM information_schema.views WHERE table_name LIKE 'dashboard_%' ORDER BY 1"
        ).fetchall()]
        counts = {
            "current_listings": connection.execute("SELECT COUNT(*) FROM dashboard_current_listings").fetchone()[0],
            "comparables": connection.execute("SELECT COUNT(*) FROM dashboard_comparables").fetchone()[0],
            "quality_checks": connection.execute("SELECT COUNT(*) FROM dashboard_data_quality").fetchone()[0],
            "model_metrics": connection.execute("SELECT COUNT(*) FROM dashboard_model_performance").fetchone()[0],
        }
        categories = dict(connection.execute(
            "SELECT stakeholder_category, COUNT(*) FROM dashboard_current_listings GROUP BY 1 ORDER BY 1"
        ).fetchall())
    result = {
        "status": "success", "built_at_utc": built_at, "database": "data/listings.db",
        "views": views, "counts": counts, "categories": categories,
        "notes": [
            "Views store SQL definitions rather than duplicate listing rows.",
            "dashboard_model_metrics is a small copied table so the dashboard needs only one database file.",
            "The dashboard is read-only and category labels are screening aids, not official valuations.",
        ],
    }
    report = project_root / "reports" / "phase_5_stakeholder_dashboard" / "23_dashboard_views_summary.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.project_root.resolve()), indent=2))
    except (ValueError, duckdb.Error) as error:
        parser.exit(1, f"Dashboard view build failed: {error}\n")


if __name__ == "__main__":
    main()
