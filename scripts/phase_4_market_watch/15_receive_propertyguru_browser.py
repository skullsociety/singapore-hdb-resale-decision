"""Receive extension cards and store every listing snapshot in one DuckDB table."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import duckdb


def load_parser():
    path = Path(__file__).with_name("15_propertyguru_cards.py")
    spec = importlib.util.spec_from_file_location("propertyguru_cards", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


propertyguru = load_parser()


def propertyguru_page_matches(url: str, number: int) -> bool:
    expected = propertyguru.SEARCH_URL if number == 1 else f"{propertyguru.SEARCH_URL}/{number}"
    actual_url, expected_url = urlparse(url), urlparse(expected)
    return (actual_url.scheme, actual_url.netloc, actual_url.path.rstrip("/")) == (
        expected_url.scheme, expected_url.netloc, expected_url.path.rstrip("/")
    )


# Add an approved site here and a matching adapter in the extension. No table change is needed.
SOURCES = {
    "propertyguru": {
        "search_url": propertyguru.SEARCH_URL,
        "listing_host": "www.propertyguru.com.sg",
        "matches_page": propertyguru_page_matches,
        "canonical_url": propertyguru.canonical_listing_url,
        "parse_card": propertyguru.parse_card_text,
    },
}
RUN_ID = re.compile(r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$", re.I)
MAX_BODY = 256_000
LOCK = threading.Lock()
DB_NAME = "listings.db"
TABLE = "listings"

# One row per source + collection run + listing. Repeated cards in a run merge.
COLUMNS = {
    "source_site": "VARCHAR NOT NULL",
    "run_id": "VARCHAR NOT NULL",
    "listing_id": "VARCHAR NOT NULL",
    "listing_url": "VARCHAR NOT NULL",
    "search_url": "VARCHAR NOT NULL",
    "pages_seen_json": "VARCHAR NOT NULL",
    "total_pages": "INTEGER NOT NULL",
    "advertised_count": "INTEGER NOT NULL",
    "title": "VARCHAR", "project_name": "VARCHAR", "address": "VARCHAR",
    "block": "VARCHAR", "street": "VARCHAR", "asking_price_sgd": "BIGINT",
    "floor_area_sqft": "DOUBLE", "land_area_sqft": "DOUBLE",
    "floor_area_sqm": "DOUBLE", "bedrooms": "VARCHAR", "bathrooms": "VARCHAR",
    "property_type": "VARCHAR", "furnishing": "VARCHAR", "built_year": "VARCHAR",
    "lease": "VARCHAR", "listed_date": "VARCHAR", "description": "VARCHAR",
    "raw_card_json": "VARCHAR NOT NULL",
    "first_seen_utc": "VARCHAR NOT NULL", "last_seen_utc": "VARCHAR NOT NULL",
}
CREATE_TABLE = (
    "CREATE TABLE IF NOT EXISTS listings ("
    + ", ".join(f"{name} {kind}" for name, kind in COLUMNS.items())
    + ", PRIMARY KEY (source_site, run_id, listing_id))"
)
INSERT = (
    "INSERT INTO listings (" + ", ".join(COLUMNS) + ") VALUES ("
    + ", ".join("?" for _ in COLUMNS) + ")"
)


def initialize_database(project_root: Path) -> Path:
    database = project_root / "data" / DB_NAME
    database.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(database))
    try:
        con.execute(CREATE_TABLE)
    finally:
        con.close()
    return database


def validate_payload(payload: dict) -> tuple[str, dict, list[tuple[dict, dict]]]:
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object")
    run_id, number, total = (payload.get(key) for key in
                             ("run_id", "page_number", "total_pages"))
    count, cards = payload.get("advertised_count"), payload.get("cards")
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise ValueError("Invalid run ID")
    if type(number) is not int or type(total) is not int or not (1 <= number <= total <= 1000):
        raise ValueError("Invalid page number or total")
    if type(count) is not int or not (1 <= count <= 50000):
        raise ValueError("Invalid advertised count")
    source_site = payload.get("source_site")
    source = SOURCES.get(source_site)
    if source is None:
        raise ValueError("Unsupported listing source")
    if not source["matches_page"](payload.get("source_url", ""), number):
        raise ValueError("Unexpected results page URL")
    if not isinstance(cards, list) or not (1 <= len(cards) <= 50):
        raise ValueError("Expected 1 to 50 result cards")
    observed = propertyguru.utc_now()
    accepted = []
    for card in cards:
        if not isinstance(card, dict) or any(
            not isinstance(card.get(field), str) or len(card[field]) > limit
            for field, limit in (("url", 500), ("heading", 500), ("text", 5000))
        ):
            raise ValueError("Invalid card fields")
        url = source["canonical_url"](card["url"])
        if not url or urlparse(url).netloc != source["listing_host"]:
            continue
        accepted.append((source["parse_card"](
            url, card["heading"], card["text"], observed), card
        ))
    if not accepted:
        raise ValueError("No supported listings found on page")
    return source_site, source, accepted


def ingest_page(project_root: Path, payload: dict) -> dict:
    source_site, _source, accepted = validate_payload(payload)
    run_id, number, total = (payload[key] for key in
                             ("run_id", "page_number", "total_pages"))
    count = payload["advertised_count"]
    database = project_root / "data" / DB_NAME
    database.parent.mkdir(parents=True, exist_ok=True)
    with LOCK:
        con = duckdb.connect(str(database))
        in_transaction = False
        try:
            con.execute(CREATE_TABLE)
            con.execute("BEGIN TRANSACTION")
            in_transaction = True
            for record, card in accepted:
                listing_id = str(record["listing_id"])
                existing = con.execute(
                    "SELECT pages_seen_json, first_seen_utc FROM listings "
                    "WHERE source_site = ? AND run_id = ? AND listing_id = ?",
                    [source_site, run_id, listing_id],
                ).fetchone()
                pages = set(json.loads(existing[0])) if existing else set()
                pages.add(number)
                values = {
                    "source_site": source_site, "run_id": run_id,
                    "listing_id": listing_id, "listing_url": record["source_url"],
                    "search_url": payload["source_url"],
                    "pages_seen_json": json.dumps(sorted(pages)),
                    "total_pages": total, "advertised_count": count,
                    **{key: record.get(key) for key in COLUMNS if key in record},
                    "raw_card_json": json.dumps(card, ensure_ascii=False),
                    "first_seen_utc": existing[1] if existing else record["first_seen_utc"],
                    "last_seen_utc": record["last_seen_utc"],
                }
                con.execute(
                    "DELETE FROM listings WHERE source_site = ? AND run_id = ? AND listing_id = ?",
                    [source_site, run_id, listing_id],
                )
                con.execute(INSERT, [values.get(key) for key in COLUMNS])
            run_rows = con.execute(
                "SELECT pages_seen_json FROM listings WHERE source_site = ? AND run_id = ?",
                [source_site, run_id],
            ).fetchall()
            saved_pages = sorted({page for (raw,) in run_rows for page in json.loads(raw)})
            unique_count = len(run_rows)
            con.execute("COMMIT")
            in_transaction = False
        except Exception:
            if in_transaction:
                con.execute("ROLLBACK")
            raise
        finally:
            con.close()
    missing = [page for page in range(1, total + 1) if page not in saved_pages]
    return {
        "status": "complete" if not missing else "partial",
        "source_site": source_site, "run_id": run_id,
        "expected_pages": total, "saved_pages": saved_pages,
        "missing_pages": missing, "unique_listing_ids": unique_count,
        "count_difference": count - unique_count, "latest_page": number,
        "database": str(database), "table": TABLE,
    }


class Receiver(BaseHTTPRequestHandler):
    project_root: Path

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"status": "ready", "database": DB_NAME, "table": TABLE})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/page":
            self._send(404, {"error": "not found"})
            return
        if not re.fullmatch(r"chrome-extension://[a-p]{32}", self.headers.get("Origin", "")):
            self._send(403, {"error": "Chrome extension origin required"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not (0 < length <= MAX_BODY):
                raise ValueError("Invalid request size")
            result = ingest_page(self.project_root, json.loads(self.rfile.read(length)))
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self._send(400, {"error": str(error)})
            return
        print(f"Saved {result['source_site']} page {result['latest_page']}/"
              f"{result['expected_pages']}; {result['unique_listing_ids']} unique listings",
              flush=True)
        self._send(200, result)

    def log_message(self, format: str, *args) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--port", type=int, default=8771)
    args = parser.parse_args()
    Receiver.project_root = args.project_root.resolve()
    initialize_database(Receiver.project_root)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Receiver)
    print(f"Ready for Chrome on http://127.0.0.1:{args.port}; "
          f"saving to data/{DB_NAME}, table {TABLE}.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
