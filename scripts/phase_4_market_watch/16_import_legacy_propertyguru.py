"""Import a completed legacy page-JSON run into the listings DuckDB table."""

from __future__ import annotations

import argparse
import importlib.util
import json
from collections import defaultdict
from pathlib import Path

import duckdb


def load_receiver(script_dir: Path):
    spec = importlib.util.spec_from_file_location(
        "listing_receiver", script_dir / "15_receive_propertyguru_browser.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--advertised-count", type=int, required=True)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    pages = sorted(run_dir.glob("page_*.json"))
    if not pages:
        raise SystemExit("No page JSON files found")
    receiver = load_receiver(Path(__file__).resolve().parent)
    run_id = run_dir.name
    if not receiver.RUN_ID.fullmatch(run_id):
        raise SystemExit("Run directory name is not a valid run ID")

    listings = {}
    pages_seen = defaultdict(set)
    search_urls = {}
    page_numbers = set()
    for path in pages:
        page = json.loads(path.read_text(encoding="utf-8"))
        number = page["page_number"]
        if number in page_numbers:
            raise SystemExit(f"Duplicate page number: {number}")
        page_numbers.add(number)
        for record in page["records"]:
            listing_id = str(record["listing_id"])
            listings[listing_id] = record
            pages_seen[listing_id].add(number)
            search_urls.setdefault(listing_id, page["source_url"])
    total_pages = max(page_numbers)
    if page_numbers != set(range(1, total_pages + 1)):
        raise SystemExit("Run has missing pages; import only complete runs")

    database = run_dir.parents[2] / "listings.db"
    con = duckdb.connect(str(database))
    in_transaction = False
    try:
        con.execute(receiver.CREATE_TABLE)
        existing = con.execute(
            "SELECT COUNT(*) FROM listings WHERE source_site = 'propertyguru' AND run_id = ?",
            [run_id],
        ).fetchone()[0]
        if existing:
            raise SystemExit(f"Run already has {existing} database rows; no changes made")
        con.execute("BEGIN TRANSACTION")
        in_transaction = True
        for listing_id, record in listings.items():
            values = {
                **record,
                "source_site": "propertyguru",
                "run_id": run_id,
                "listing_id": listing_id,
                "listing_url": record["source_url"],
                "search_url": search_urls[listing_id],
                "pages_seen_json": json.dumps(sorted(pages_seen[listing_id])),
                "total_pages": total_pages,
                "advertised_count": args.advertised_count,
                "raw_card_json": json.dumps(record, ensure_ascii=False),
            }
            con.execute(receiver.INSERT, [values.get(key) for key in receiver.COLUMNS])
        con.execute("COMMIT")
        in_transaction = False
    except Exception:
        if in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()
    print(f"Imported {len(listings)} unique listings from {total_pages} pages into {database}")


if __name__ == "__main__":
    main()
