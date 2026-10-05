"""Download and validate the official HDB datasets used by Phase 1.

Uses only the Python standard library. Raw files are kept unchanged after
download; smaller Sengkang subsets and a machine-readable manifest are derived
from them.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_ROOT = "https://api-open.data.gov.sg/v1/public/api/datasets"
USER_AGENT = "PropertyProject/0.1 (HDB research; data.gov.sg public API)"

DATASETS = {
    "hdb_resale_transactions_2017_onwards": {
        "dataset_id": "d_8b84c4ee58e3cfc0ece0d773c8ca6abc",
        "filename": "hdb_resale_transactions_2017_onwards.csv",
        "source_page": (
            "https://data.gov.sg/datasets/"
            "d_8b84c4ee58e3cfc0ece0d773c8ca6abc/view"
        ),
        "expected_columns": {
            "month",
            "town",
            "flat_type",
            "block",
            "street_name",
            "storey_range",
            "floor_area_sqm",
            "flat_model",
            "lease_commence_date",
            "remaining_lease",
            "resale_price",
        },
    },
    "hdb_property_information": {
        "dataset_id": "d_17f5382f26140b1fdae0ba2ef6239d2f",
        "filename": "hdb_property_information.csv",
        "source_page": (
            "https://data.gov.sg/datasets/"
            "d_17f5382f26140b1fdae0ba2ef6239d2f/view"
        ),
        "expected_columns": {
            "blk_no",
            "street",
            "max_floor_lvl",
            "year_completed",
            "residential",
            "commercial",
            "market_hawker",
            "miscellaneous",
            "multistorey_carpark",
            "precinct_pavilion",
            "bldg_contract_town",
            "total_dwelling_units",
        },
    },
}


def request_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=60) as response:
        return json.load(response)


def get_download_url(dataset_id: str) -> str:
    initiate_url = f"{API_ROOT}/{dataset_id}/initiate-download"
    payload = request_json(initiate_url)
    direct_url = payload.get("data", {}).get("url")
    if direct_url:
        return str(direct_url)

    poll_url = f"{API_ROOT}/{dataset_id}/poll-download"
    for attempt in range(6):
        if attempt:
            time.sleep(12)  # Stay within the documented five-request/minute quota.
        payload = request_json(poll_url)
        data = payload.get("data", {})
        if data.get("url"):
            return str(data["url"])
        if str(data.get("status", "")).lower() in {"failed", "error"}:
            break

    raise RuntimeError(f"data.gov.sg did not produce a download URL for {dataset_id}")


def download_file(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=180) as response, partial.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_headers(headers: list[str] | None) -> list[str]:
    return [(header or "").strip().lstrip("\ufeff") for header in (headers or [])]


def inspect_csv(path: Path, expected_columns: set[str]) -> dict[str, Any]:
    row_count = 0
    blank_counts: Counter[str] = Counter()
    month_min: str | None = None
    month_max: str | None = None
    town_counts: Counter[str] = Counter()

    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        headers = normalize_headers(reader.fieldnames)
        if not headers:
            raise ValueError(f"No CSV header found in {path}")
        missing_columns = sorted(expected_columns - set(headers))
        if missing_columns:
            raise ValueError(f"{path.name} is missing columns: {', '.join(missing_columns)}")

        for row in reader:
            row_count += 1
            for header in headers:
                if not str(row.get(header, "")).strip():
                    blank_counts[header] += 1
            month = str(row.get("month", "")).strip()
            if month:
                month_min = month if month_min is None else min(month_min, month)
                month_max = month if month_max is None else max(month_max, month)
            town = str(row.get("town", "")).strip()
            if town:
                town_counts[town] += 1

    if row_count == 0:
        raise ValueError(f"{path.name} contains no data rows")

    result: dict[str, Any] = {
        "row_count": row_count,
        "column_count": len(headers),
        "columns": headers,
        "blank_counts": dict(sorted(blank_counts.items())),
    }
    if month_min:
        result["month_min"] = month_min
        result["month_max"] = month_max
    if town_counts:
        result["town_counts"] = dict(sorted(town_counts.items()))
    return result


def filter_csv(
    source_path: Path,
    destination_path: Path,
    predicate: Any,
) -> int:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    row_count = 0
    with source_path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames:
            raise ValueError(f"No CSV header found in {source_path}")
        with destination_path.open("w", encoding="utf-8", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=reader.fieldnames)
            writer.writeheader()
            for row in reader:
                if predicate(row):
                    writer.writerow(row)
                    row_count += 1
    return row_count


def write_markdown_summary(manifest: dict[str, Any], destination: Path) -> None:
    resale = manifest["datasets"]["hdb_resale_transactions_2017_onwards"]
    properties = manifest["datasets"]["hdb_property_information"]
    derived = manifest["derived_files"]
    lines = [
        "# Phase 1 data extraction summary",
        "",
        f"Extracted at: `{manifest['extracted_at_utc']}`",
        "",
        "| Dataset | Dataset ID | Rows | Columns | Coverage | Blank cells |",
        "|---|---|---:|---:|---|---:|",
        (
            "| HDB resale transactions | "
            f"`{resale['dataset_id']}` | {resale['row_count']:,} | "
            f"{resale['column_count']} | {resale['month_min']} to {resale['month_max']} | "
            f"{sum(resale['blank_counts'].values()):,} |"
        ),
        (
            "| HDB Property Information | "
            f"`{properties['dataset_id']}` | {properties['row_count']:,} | "
            f"{properties['column_count']} | Current published snapshot | "
            f"{sum(properties['blank_counts'].values()):,} |"
        ),
        "",
        "## Pilot subsets",
        "",
        "| File | Rows | Selection rule |",
        "|---|---:|---|",
        (
            "| `data/processed/sengkang_resale_transactions.csv` | "
            f"{derived['sengkang_resale_transactions']['row_count']:,} | `town = SENGKANG` |"
        ),
        (
            "| `data/processed/fernvale_resale_transactions.csv` | "
            f"{derived['fernvale_resale_transactions']['row_count']:,} | "
            "Sengkang transaction and street name contains `FERNVALE` |"
        ),
        (
            "| `data/processed/sengkang_hdb_property_information.csv` | "
            f"{derived['sengkang_hdb_property_information']['row_count']:,} | "
            "`bldg_contract_town = SK` |"
        ),
        "",
        "## Validation performed",
        "",
        "- Confirmed each source CSV contains data rows.",
        "- Confirmed the expected source columns are present.",
        "- Counted blank cells by column.",
        "- Recorded SHA-256 checksums so later runs can detect source changes.",
        "- Confirmed each derived pilot subset contains rows.",
        "",
        "## Current boundary",
        "",
        "These files are extracted and structurally validated. Address normalization, "
        "joining transactions to blocks, geocoding, comparable selection, and model "
        "evaluation remain the next Phase 1 steps.",
        "",
    ]
    destination.write_text("\n".join(lines), encoding="utf-8")


def extract(project_root: Path, force: bool) -> dict[str, Any]:
    raw_dir = project_root / "data" / "raw"
    processed_dir = project_root / "data" / "processed"
    reports_dir = project_root / "reports/phase_1_resale_model"
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "extracted_at_utc": datetime.now(timezone.utc).isoformat(),
        "api_root": API_ROOT,
        "datasets": {},
        "derived_files": {},
    }

    for name, config in DATASETS.items():
        raw_path = raw_dir / str(config["filename"])
        if force or not raw_path.exists():
            download_url = get_download_url(str(config["dataset_id"]))
            download_file(download_url, raw_path)

        inspection = inspect_csv(raw_path, set(config["expected_columns"]))
        manifest["datasets"][name] = {
            "dataset_id": config["dataset_id"],
            "source_page": config["source_page"],
            "local_path": raw_path.relative_to(project_root).as_posix(),
            "bytes": raw_path.stat().st_size,
            "sha256": sha256_file(raw_path),
            **inspection,
        }

    resale_path = raw_dir / str(DATASETS["hdb_resale_transactions_2017_onwards"]["filename"])
    property_path = raw_dir / str(DATASETS["hdb_property_information"]["filename"])

    sengkang_resale_path = processed_dir / "sengkang_resale_transactions.csv"
    sengkang_count = filter_csv(
        resale_path,
        sengkang_resale_path,
        lambda row: str(row.get("town", "")).strip().upper() == "SENGKANG",
    )
    fernvale_resale_path = processed_dir / "fernvale_resale_transactions.csv"
    fernvale_count = filter_csv(
        resale_path,
        fernvale_resale_path,
        lambda row: str(row.get("town", "")).strip().upper() == "SENGKANG"
        and "FERNVALE" in str(row.get("street_name", "")).upper(),
    )
    sengkang_property_path = processed_dir / "sengkang_hdb_property_information.csv"
    sengkang_property_count = filter_csv(
        property_path,
        sengkang_property_path,
        lambda row: str(row.get("bldg_contract_town", "")).strip().upper() == "SK",
    )

    for key, path, rows in (
        ("sengkang_resale_transactions", sengkang_resale_path, sengkang_count),
        ("fernvale_resale_transactions", fernvale_resale_path, fernvale_count),
        ("sengkang_hdb_property_information", sengkang_property_path, sengkang_property_count),
    ):
        if rows == 0:
            raise ValueError(f"Derived file {path.name} contains no rows")
        manifest["derived_files"][key] = {
            "local_path": path.relative_to(project_root).as_posix(),
            "row_count": rows,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }

    manifest_path = reports_dir / "01_data_extraction_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    write_markdown_summary(manifest, reports_dir / "01_data_extraction_summary.md")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Project directory; defaults to the parent of scripts/.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Download fresh copies even if raw files already exist.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        manifest = extract(args.project_root.resolve(), args.force)
    except (HTTPError, URLError, OSError, RuntimeError, ValueError) as error:
        print(f"Extraction failed: {error}", file=sys.stderr)
        return 1

    for name, details in manifest["datasets"].items():
        print(f"{name}: {details['row_count']:,} rows, {details['bytes']:,} bytes")
    for name, details in manifest["derived_files"].items():
        print(f"{name}: {details['row_count']:,} rows")
    print("Reports: reports/phase_1_resale_model/01_data_extraction_manifest.json, reports/phase_1_resale_model/01_data_extraction_summary.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
