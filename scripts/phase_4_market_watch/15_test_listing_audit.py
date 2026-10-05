"""Focused checks for preview, safe repair, and repeatability of the listing audit."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import duckdb


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit = load("listing_audit", "15_audit_listing_data.py")
receiver = load("listing_receiver", "15_receive_propertyguru_browser.py")


class ListingAuditTests(unittest.TestCase):
    def test_preview_then_repair_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "listings.db"
            with duckdb.connect(str(database)) as con:
                con.execute(receiver.CREATE_TABLE)
                records = [
                    ("500261756", "blk 327B anchorvale road sengkang", "anchorvale road", "5"),
                    ("500268628", "121 Sengkang East Way", "Sengkang East Way", ""),
                    ("500271666", "Sengkang HDB 3Room", "Fernvale Link", "1"),
                ]
                for listing_id, heading, address, title in records:
                    values = {
                        "source_site": "propertyguru", "run_id": "test-run", "listing_id": listing_id,
                        "listing_url": f"https://www.propertyguru.com.sg/listing/hdb-for-sale-test-{listing_id}",
                        "search_url": "https://www.propertyguru.com.sg/hdb-for-sale/in-sengkang",
                        "pages_seen_json": "[1]", "total_pages": 1, "advertised_count": 3,
                        "title": title, "project_name": heading, "address": address,
                        "block": "", "street": "", "asking_price_sgd": 705000,
                        "floor_area_sqft": 990, "raw_card_json": json.dumps({"heading": heading}),
                        "first_seen_utc": "2026-10-02", "last_seen_utc": "2026-10-02",
                    }
                    con.execute(receiver.INSERT, [values.get(key) for key in receiver.COLUMNS])

            preview = audit.audit_database(database)
            self.assertEqual(preview["rows_to_fix"], 3)
            self.assertEqual(preview["issues_for_review"], {"unresolved_address": 1})
            with duckdb.connect(str(database), read_only=True) as con:
                self.assertEqual(con.execute("SELECT block FROM listings WHERE listing_id = '500261756'").fetchone()[0], "")

            applied = audit.audit_database(database, apply=True)
            self.assertEqual(applied["rows_fixed"], 3)
            with duckdb.connect(str(database), read_only=True) as con:
                first = con.execute(
                    "SELECT address, block, street, title, project_name FROM listings "
                    "WHERE listing_id = '500261756'"
                ).fetchone()
                second = con.execute(
                    "SELECT address, block FROM listings WHERE listing_id = '500268628'"
                ).fetchone()
                unresolved = con.execute(
                    "SELECT block, title FROM listings WHERE listing_id = '500271666'"
                ).fetchone()
            self.assertEqual(first, ("327B anchorvale road", "327B", "ANCHORVALE ROAD", "", ""))
            self.assertEqual(second, ("121 Sengkang East Way", "121"))
            self.assertEqual(unresolved, ("", ""))
            self.assertEqual(audit.audit_database(database)["rows_to_fix"], 0)


if __name__ == "__main__":
    unittest.main()
