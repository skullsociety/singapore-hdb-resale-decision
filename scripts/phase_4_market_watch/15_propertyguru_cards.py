"""Parse HDB listing cards received from the Chrome extension."""

from __future__ import annotations

import csv
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


SEARCH_URL = "https://www.propertyguru.com.sg/hdb-for-sale/in-sengkang"
LISTING_ID_RE = re.compile(r"/listing/(?:hdb-)?for-sale-[^/?#]+-(\d+)$", re.I)
MONEY_RE = re.compile(r"S\$\s*([\d,]+)")
AREA_RE = re.compile(r"\b([\d,]+)\s*sqft\b", re.I)
BLOCK_RE = re.compile(r"\b(\d{1,4}[A-Z]?)\s+(.+)$", re.I)
HEADING_ADDRESS_RE = re.compile(r"^(?:BLK\s+)?(\d{1,4}[A-Z]?)\s+(.+)$", re.I)
CSV_FIELDS = (
    "listing_id", "source_url", "area_query", "title", "project_name", "address",
    "block", "street", "asking_price_sgd", "floor_area_sqft", "land_area_sqft",
    "floor_area_sqm", "bedrooms", "bathrooms", "property_type",
    "furnishing", "built_year", "lease", "listed_date",
    "description", "first_seen_utc", "last_seen_utc",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def listing_id_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.hostname not in {"www.propertyguru.com.sg", "propertyguru.com.sg"}:
        return None
    match = LISTING_ID_RE.search(parsed.path.rstrip("/"))
    return match.group(1) if match else None


def canonical_listing_url(url: str) -> str | None:
    listing_id = listing_id_from_url(url)
    if not listing_id:
        return None
    parsed = urlparse(url)
    return f"https://www.propertyguru.com.sg{parsed.path.rstrip('/')}"


def first_group(pattern: str, value: str, flags: int = 0) -> str:
    match = re.search(pattern, value, flags)
    return match.group(1).strip() if match else ""


def complete_address(address: str, heading: str) -> tuple[str, bool]:
    """Use a displayed heading only when it supplies a missing block for this address."""
    if BLOCK_RE.match(address):
        return address, False
    match = HEADING_ADDRESS_RE.match(heading.strip())
    if not match:
        return address, False
    block, heading_street = match.groups()
    if address.casefold() == block.casefold():
        return f"{block} {heading_street}", True
    if heading_street.casefold().startswith(address.casefold()):
        return f"{block} {address}", True
    return address, False


def parse_card_text(url: str, heading: str, card_text: str, seen_at: str) -> dict[str, object]:
    """Extract fields displayed on one HDB result card without inferring a unit."""
    listing_id = listing_id_from_url(url)
    if not listing_id:
        raise ValueError(f"Invalid PropertyGuru listing URL: {url}")
    lines = [line.strip() for line in card_text.splitlines() if line.strip()]
    price_line_index = next((i for i, line in enumerate(lines) if MONEY_RE.fullmatch(line)), -1)
    price = MONEY_RE.search(lines[price_line_index]) if price_line_index >= 0 else None
    area = AREA_RE.search(card_text)
    area_sqft = int(area.group(1).replace(",", "")) if area else None
    address_index = next((i for i, line in enumerate(lines) if line == heading.strip()), -1)
    after_address = lines[address_index + 1:] if address_index >= 0 else []
    address = after_address[0].split(",")[0].strip() if after_address else heading.strip()
    address, heading_is_address = complete_address(address, heading)
    block_match = BLOCK_RE.match(address.strip())
    count_values = [line for line in after_address[:4] if line.isdigit()]
    area_index = next((i for i, line in enumerate(after_address) if AREA_RE.search(line)), -1)
    flat_type = after_address[area_index + 1] if area_index >= 0 and len(after_address) > area_index + 1 else ""
    marketed_title = lines[price_line_index - 1] if price_line_index > 0 else ""
    if marketed_title.isdigit():
        marketed_title = ""  # Card counters sometimes appear immediately before the price.
    land_area = first_group(r"\b([\d,]+)\s*sqft\s*\(land\)", card_text, re.I)

    return {
        "listing_id": listing_id,
        "source_url": canonical_listing_url(url),
        "area_query": "Sengkang",
        "title": marketed_title,
        "project_name": heading.strip() if not heading_is_address and heading.strip() != address else "",
        "address": address.strip(),
        "block": block_match.group(1).upper() if block_match else "",
        "street": block_match.group(2).strip().upper() if block_match else "",
        "asking_price_sgd": int(price.group(1).replace(",", "")) if price else None,
        "floor_area_sqft": area_sqft,
        "land_area_sqft": int(land_area.replace(",", "")) if land_area else None,
        "floor_area_sqm": round(area_sqft * 0.09290304, 2) if area_sqft else None,
        "bedrooms": count_values[0] if len(count_values) >= 1 else "",
        "bathrooms": count_values[1] if len(count_values) >= 2 else "",
        "property_type": flat_type,
        "furnishing": "",
        "built_year": first_group(r"\bBuilt:\s*(\d{4})\b", card_text, re.I),
        "lease": first_group(r"\b(\d+-year Leasehold|Freehold|Prefer Not to Say)\b", card_text, re.I),
        "listed_date": first_group(r"\bListed on\s+(\w+\s+\d{1,2},\s+\d{4})\b", card_text, re.I),
        "description": "",
        "first_seen_utc": seen_at,
        "last_seen_utc": seen_at,
    }


def save_csv(path: Path, rows: dict[str, dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for listing_id in sorted(rows, key=int):
            writer.writerow({field: rows[listing_id].get(field, "") for field in CSV_FIELDS})
    temporary.replace(path)
