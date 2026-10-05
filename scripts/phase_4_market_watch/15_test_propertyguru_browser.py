"""Local receiver tests; no requests are sent to property websites."""

import importlib.util
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import duckdb


spec = importlib.util.spec_from_file_location(
    "receiver", Path(__file__).with_name("15_receive_propertyguru_browser.py")
)
receiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receiver)


def card(number):
    return {
        "url": f"https://www.propertyguru.com.sg/listing/hdb-for-sale-test-{number}",
        "heading": "100 Sengkang East Way",
        "text": "Agent\nSpacious flat\nS$ 600,000\n100 Sengkang East Way\n"
                "100 Sengkang East Way, Sengkang\n3\n2\n1,000 sqft\nHDB Flat\nBuilt: 2000",
    }


class ReceiverTests(unittest.TestCase):
    def test_incomplete_card_address_and_numeric_title(self):
        cases = [
            ("500261756", "blk 327B anchorvale road sengkang", "anchorvale road",
             "327B", "ANCHORVALE ROAD"),
            ("500268628", "121 Sengkang East Way", "Sengkang East Way",
             "121", "SENGKANG EAST WAY"),
        ]
        for listing_id, heading, card_address, block, street in cases:
            with self.subTest(listing_id=listing_id):
                url = f"https://www.propertyguru.com.sg/listing/hdb-for-sale-test-{listing_id}"
                text = f"{heading}\n{card_address}\n3\n2\n990 sqft\nHDB Flat\n5\nS$ 705,000"
                parsed = receiver.propertyguru.parse_card_text(url, heading, text, "2026-10-02")
                self.assertEqual(parsed["block"], block)
                self.assertEqual(parsed["street"], street)
                self.assertEqual(parsed["project_name"], "")
                self.assertEqual(parsed["title"], "")

    def test_single_table_keeps_sources_runs_and_deduplicates_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            receiver.Receiver.project_root = Path(directory)
            receiver.initialize_database(Path(directory))
            server = ThreadingHTTPServer(("127.0.0.1", 0), receiver.Receiver)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            endpoint = f"http://127.0.0.1:{server.server_port}/page"
            first_run = "12345678-1234-1234-1234-123456789abc"
            second_run = "12345678-1234-1234-1234-123456789abd"

            def post(source, run_id, page, cards, origin="chrome-extension://" + "a" * 32):
                source_url = receiver.SOURCES[source]["search_url"]
                body = json.dumps({
                    "source_site": source, "run_id": run_id,
                    "page_number": page, "total_pages": 2 if source == "propertyguru" else 1,
                    "advertised_count": 3 if source == "propertyguru" else 1,
                    "source_url": source_url + (f"/{page}" if page > 1 else ""),
                    "cards": cards,
                }).encode()
                request = Request(endpoint, body, headers={"Origin": origin,
                                  "Content-Type": "application/json"}, method="POST")
                with urlopen(request) as response:
                    return json.load(response)

            receiver.SOURCES["example"] = {
                "search_url": "https://example.org/search",
                "listing_host": "example.org",
                "matches_page": lambda url, number: url == "https://example.org/search",
                "canonical_url": lambda url: url if url.startswith("https://example.org/listing/") else None,
                "parse_card": lambda url, heading, text, observed: {
                    "listing_id": "100", "source_url": url, "title": heading,
                    "asking_price_sgd": 650000,
                    "first_seen_utc": observed, "last_seen_utc": observed,
                },
            }
            try:
                first = post("propertyguru", first_run, 1, [card(100), card(101)])
                self.assertEqual(first["status"], "partial")
                self.assertEqual(first["missing_pages"], [2])
                with self.assertRaises(HTTPError) as denied:
                    post("propertyguru", first_run, 2, [card(102)], origin="https://example.org")
                self.assertEqual(denied.exception.code, 403)
                denied.exception.close()
                second = post("propertyguru", first_run, 2, [card(101), card(102)])
                self.assertEqual(second["status"], "complete")
                self.assertEqual(second["unique_listing_ids"], 3)
                self.assertEqual(post("propertyguru", first_run, 2, [card(101), card(102)])
                                 ["unique_listing_ids"], 3)
                post("propertyguru", second_run, 1, [card(100)])
                example = post("example", first_run, 1, [{
                    "url": "https://example.org/listing/100", "heading": "Other home", "text": "Other home"
                }])
                self.assertEqual(example["status"], "complete")

                database = Path(directory) / "data/listings.db"
                with duckdb.connect(str(database), read_only=True) as con:
                    tables = con.execute("SHOW TABLES").fetchall()
                    rows = con.execute(
                        "SELECT source_site, run_id, listing_id, pages_seen_json "
                        "FROM listings ORDER BY source_site, run_id, listing_id"
                    ).fetchall()
                self.assertEqual(tables, [("listings",)])
                self.assertEqual(len(rows), 5)  # 3 PG + 1 later PG run + 1 other site
                self.assertIn(("propertyguru", first_run, "101", "[1, 2]"), rows)
                self.assertIn(("example", first_run, "100", "[1]"), rows)
            finally:
                del receiver.SOURCES["example"]
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
