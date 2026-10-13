"""Publish the released single-flat valuation inputs for the Streamlit dashboard."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[2]
SOURCE_DB = ROOT / "data/property.duckdb"
SOURCE_RELEASE = ROOT / "reports/phase_1_resale_model/09_blend_release.json"
BUNDLE_DB = ROOT / "data/phase_1_resale_model/valuation.duckdb"
BUNDLE_RELEASE = ROOT / "reports/phase_1_resale_model/09_dashboard_release.json"

TABLE_COLUMNS = {
    "hdb_blocks": "block_id, block, street, match_status, latitude, longitude",
    "hdb_properties": (
        "block_id, bldg_contract_town, year_completed, max_floor_lvl, "
        "total_dwelling_units, commercial, market_hawker, multistorey_carpark, precinct_pavilion"
    ),
    "block_location_features": "*",
    "resale_transactions": (
        "block_id, transaction_month, town, flat_type, flat_model, lease_commence_year"
    ),
    "transaction_features": (
        "transaction_id, block_id, transaction_month, dataset_split, town, flat_type, "
        "flat_model, floor_area_sqm, storey_midpoint, lease_commence_year, "
        "latitude, longitude, resale_price"
    ),
    "pipeline_runs": "run_id, built_at_utc",
}


def build() -> None:
    release = json.loads(SOURCE_RELEASE.read_text(encoding="utf-8"))
    if release.get("status") != "national_candidate":
        raise ValueError("Step 09 has no released valuation method")
    if not SOURCE_DB.is_file():
        raise FileNotFoundError(SOURCE_DB)
    model_path = Path(release["model_artifact"]) if release.get("model_artifact") else None
    if model_path and not model_path.is_file():
        raise FileNotFoundError(model_path)
    with duckdb.connect(str(SOURCE_DB), read_only=True) as source:
        latest_run = source.execute(
            "SELECT run_id FROM pipeline_runs ORDER BY built_at_utc DESC LIMIT 1"
        ).fetchone()
        if not latest_run or latest_run[0] != release.get("source_build_run_id"):
            raise ValueError("Step 09 release and source database differ; rerun steps 04–09")

    BUNDLE_DB.parent.mkdir(parents=True, exist_ok=True)
    temporary_db = BUNDLE_DB.with_suffix(".building.duckdb")
    temporary_db.unlink(missing_ok=True)
    try:
        with duckdb.connect(str(temporary_db)) as bundle:
            source_path = str(SOURCE_DB).replace("'", "''")
            bundle.execute(f"ATTACH '{source_path}' AS source_db (READ_ONLY)")
            for table, columns in TABLE_COLUMNS.items():
                bundle.execute(f"CREATE TABLE {table} AS SELECT {columns} FROM source_db.{table}")
            bundle.execute("DETACH source_db")
        os.replace(temporary_db, BUNDLE_DB)
    finally:
        temporary_db.unlink(missing_ok=True)

    public_release = dict(release)
    if model_path:
        destination = ROOT / "models/phase_1_resale_model" / model_path.name
        if model_path.resolve() != destination.resolve():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(model_path, destination)
        public_release["model_artifact"] = str(destination.relative_to(ROOT)).replace("\\", "/")
    BUNDLE_RELEASE.write_text(json.dumps(public_release, indent=2) + "\n", encoding="utf-8")
    print(f"Valuation bundle: {BUNDLE_DB} ({BUNDLE_DB.stat().st_size / 1024**2:.1f} MiB)")
    print(f"Release manifest: {BUNDLE_RELEASE}")


if __name__ == "__main__":
    build()
