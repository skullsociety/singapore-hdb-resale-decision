"""Audit listing rows and optionally apply corrections backed by saved card data."""

from __future__ import annotations

import argparse
import importlib.util
import json
from collections import Counter
from pathlib import Path

import duckdb


def load_cards():
    path = Path(__file__).with_name("15_propertyguru_cards.py")
    spec = importlib.util.spec_from_file_location("propertyguru_cards", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cards = load_cards()
SELECT_FIELDS = (
    "source_site", "run_id", "listing_id", "listing_url", "title", "project_name",
    "address", "block", "street", "asking_price_sgd", "floor_area_sqft", "raw_card_json",
)


def inspect_listing(row: dict) -> tuple[dict[str, str], set[str]]:
    """Return safe field changes and issues that need a person's review."""
    changes: dict[str, str] = {}
    issues: set[str] = set()
    listing_url = row.get("listing_url") or ""
    if row["source_site"] == "propertyguru":
        if cards.listing_id_from_url(listing_url) != row["listing_id"]:
            issues.add("listing_url_id_mismatch")
    elif not listing_url:
        issues.add("missing_listing_url")
    if row.get("asking_price_sgd") is None or row["asking_price_sgd"] <= 0:
        issues.add("missing_or_invalid_price")
    if row.get("floor_area_sqft") is None or row["floor_area_sqft"] <= 0:
        issues.add("missing_or_invalid_area")

    if row["source_site"] != "propertyguru":
        return changes, issues

    title = (row.get("title") or "").strip()
    if title.isdigit():
        changes["title"] = ""

    try:
        raw_card = json.loads(row.get("raw_card_json") or "")
    except (json.JSONDecodeError, TypeError):
        raw_card = {}
        issues.add("invalid_raw_card_json")
    if not isinstance(raw_card, dict):
        raw_card = {}
        issues.add("invalid_raw_card_json")
    heading = raw_card.get("heading") or row.get("project_name") or ""
    address = (row.get("address") or "").strip()
    completed, used_heading = cards.complete_address(address, heading)
    address_match = cards.BLOCK_RE.match(completed)
    if address_match:
        block, street = address_match.group(1).upper(), address_match.group(2).strip().upper()
        if completed != address:
            changes["address"] = completed
        if (row.get("block") or "") != block:
            changes["block"] = block
        if (row.get("street") or "") != street:
            changes["street"] = street
        if used_heading and (row.get("project_name") or "").strip() == heading.strip():
            changes["project_name"] = ""
    else:
        issues.add("unresolved_address")
    return changes, issues


def audit_database(database: Path, apply: bool = False) -> dict:
    if not database.is_file():
        raise RuntimeError(f"Database file not found: {database}")
    try:
        con = duckdb.connect(str(database), read_only=not apply)
    except duckdb.IOException as error:
        raise RuntimeError(
            "Cannot open listings.db. Disconnect it in DBeaver, then rerun the audit."
        ) from error
    try:
        rows = con.execute(f"SELECT {', '.join(SELECT_FIELDS)} FROM listings").fetchall()
        corrections = []
        issues = Counter()
        samples = []
        for values in rows:
            row = dict(zip(SELECT_FIELDS, values))
            changes, row_issues = inspect_listing(row)
            issues.update(row_issues)
            if changes:
                corrections.append((row, changes))
                if len(samples) < 10:
                    samples.append({"listing_id": row["listing_id"], "changes": changes})
        if apply and corrections:
            con.execute("BEGIN TRANSACTION")
            try:
                for row, changes in corrections:
                    assignments = ", ".join(f"{name} = ?" for name in changes)
                    con.execute(
                        f"UPDATE listings SET {assignments} "
                        "WHERE source_site = ? AND run_id = ? AND listing_id = ?",
                        [*changes.values(), row["source_site"], row["run_id"], row["listing_id"]],
                    )
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
        return {
            "mode": "applied" if apply else "preview",
            "rows_checked": len(rows),
            "rows_to_fix" if not apply else "rows_fixed": len(corrections),
            "fields_to_fix": dict(Counter(field for _, changes in corrections for field in changes)),
            "issues_for_review": dict(issues),
            "examples": samples,
        }
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path(__file__).resolve().parents[2] / "data/listings.db")
    parser.add_argument("--apply", action="store_true", help="Write the supported corrections")
    args = parser.parse_args()
    try:
        result = audit_database(args.database, args.apply)
    except RuntimeError as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
