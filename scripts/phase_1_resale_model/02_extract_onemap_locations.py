"""Create HDB block and public-amenity coordinate tables from OneMap.

The bearer token is read only from the ONEMAP_TOKEN environment variable. It is
never written to the project, logs, reports, or error messages.
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import os
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ONEMAP_ROOT = "https://www.onemap.gov.sg"
DATA_GOV_ROOT = "https://api-open.data.gov.sg"
LTA_MRT_EXIT_DATASET_ID = "d_b39d3a0871985372d7e1637193335da5"
MOE_SCHOOL_DATASET_ID = "d_688b934f82c1059ed0a6993d2a829089"
ECDA_CENTRE_DATASET_ID = "d_696c994c50745b079b3684f0e90ffc53"
CHAS_CLINIC_DATASET_ID = "d_548c33ea2d99e29ec63a7cc9edcccedc"
USER_AGENT = "PropertyProject/0.1 (HDB location research)"
SINGAPORE_LATITUDE = (1.15, 1.50)
SINGAPORE_LONGITUDE = (103.55, 104.05)

AMENITY_KEYWORDS = {
    "community_clubs": ("community club",),
    "public_libraries": ("library", "libraries"),
    "hawker_centres": ("hawker",),
    "hospitals": ("hospital",),
    "parks": ("park",),
}

# Broad text matching is unsafe for parks because the catalogue also contains
# car parks, parking zones, business parks, buildings, and drone-control areas.
# The `nationalparks` theme provides one record per named park, while the
# `nparks_parks` geometry layer repeats many parks across multiple features.
AMENITY_THEME_QUERY_ALLOWLIST = {
    "hospitals": ("moh_hospitals",),
    "parks": ("nationalparks",),
}

POLYCLINIC_NAMES = (
    "Ang Mo Kio Polyclinic",
    "Bedok Polyclinic",
    "Bukit Batok Polyclinic",
    "Bukit Merah Polyclinic",
    "Bukit Panjang Polyclinic",
    "Choa Chu Kang Polyclinic",
    "Clementi Polyclinic",
    "Eunos Polyclinic",
    "Geylang Polyclinic",
    "Hougang Polyclinic",
    "Jurong Polyclinic",
    "Kallang Polyclinic",
    "Khatib Polyclinic",
    "Marine Parade Polyclinic",
    "Outram Polyclinic",
    "Pasir Ris Polyclinic",
    "Pioneer Polyclinic",
    "Punggol Polyclinic",
    "Queenstown Polyclinic",
    "Sembawang Polyclinic",
    "Sengkang Polyclinic",
    "Serangoon Polyclinic",
    "Tampines North Polyclinic",
    "Tampines Polyclinic",
    "Tengah Polyclinic",
    "Toa Payoh Polyclinic",
    "Woodlands Polyclinic",
    "Yishun Polyclinic",
)

# These categories do not exist in the current OneMap theme catalogue. Search
# terms are intentionally explicit so every returned row records how it was found.
ONEMAP_SEARCH_QUERIES = {
    "shopping_malls": ("shopping mall", "shopping centre"),
    "supermarkets": (
        "supermarket",
        "FairPrice",
        "Sheng Siong",
        "Cold Storage",
        "Giant Supermarket",
        "Prime Supermarket",
        "CS Fresh",
    ),
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text(value: str) -> str:
    replacements = {
        " AVE ": " AVENUE ",
        " RD ": " ROAD ",
        " ST ": " STREET ",
        " DR ": " DRIVE ",
        " CRES ": " CRESCENT ",
        " CTRL ": " CENTRAL ",
        " UPP ": " UPPER ",
        " JLN ": " JALAN ",
        " LOR ": " LORONG ",
    }
    text = f" {re.sub(r'[^A-Z0-9]+', ' ', value.upper()).strip()} "
    for old, new in replacements.items():
        text = text.replace(old, new)
    return " ".join(text.split())


def address_key(block: str, street: str) -> str:
    return f"{normalize_text(block)}|{normalize_text(street)}"


class RateLimiter:
    def __init__(self, requests_per_minute: int) -> None:
        if not 1 <= requests_per_minute <= 300:
            raise ValueError("requests_per_minute must be between 1 and 300")
        self.interval = 60 / requests_per_minute
        self.next_time = 0.0
        self.lock = threading.Lock()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            delay = max(0.0, self.next_time - now)
            self.next_time = max(now, self.next_time) + self.interval
        if delay:
            time.sleep(delay)


class OneMapClient:
    def __init__(self, token: str, requests_per_minute: int) -> None:
        self.token = token
        self.rate_limiter = RateLimiter(requests_per_minute)

    def get_json(
        self,
        path: str,
        parameters: dict[str, str] | None = None,
        bearer_token: bool = False,
    ) -> dict[str, Any]:
        query = f"?{urlencode(parameters)}" if parameters else ""
        url = f"{ONEMAP_ROOT}{path}{query}"
        for attempt in range(5):
            self.rate_limiter.wait()
            authorization = f"Bearer {self.token}" if bearer_token else self.token
            request = Request(
                url,
                headers={
                    "Authorization": authorization,
                    "Accept": "application/json",
                    "User-Agent": USER_AGENT,
                },
            )
            try:
                with urlopen(request, timeout=60) as response:
                    return json.load(response)
            except HTTPError as error:
                if error.code not in {429, 500, 502, 503, 504} or attempt == 4:
                    raise RuntimeError(f"OneMap request failed with HTTP {error.code}") from error
                time.sleep(max(2**attempt, int(error.headers.get("Retry-After", "0") or 0)))
            except URLError as error:
                if attempt == 4:
                    raise RuntimeError("OneMap request could not be completed") from error
                time.sleep(2**attempt)
        raise RuntimeError("OneMap request could not be completed")

    def search_address(self, query: str) -> dict[str, Any]:
        return self.get_json(
            "/api/common/elastic/search",
            {"searchVal": query, "returnGeom": "Y", "getAddrDetails": "Y", "pageNum": "1"},
        )

    def search_all_pages(self, query: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        page_number = 1
        while True:
            response = self.get_json(
                "/api/common/elastic/search",
                {
                    "searchVal": query,
                    "returnGeom": "Y",
                    "getAddrDetails": "Y",
                    "pageNum": str(page_number),
                },
            )
            page_results = response.get("results", [])
            if isinstance(page_results, list):
                results.extend(item for item in page_results if isinstance(item, dict))
            try:
                total_pages = int(response.get("totalNumPages", page_number))
            except (TypeError, ValueError):
                total_pages = page_number
            if page_number >= total_pages:
                return results
            page_number += 1

    def get_all_themes(self) -> dict[str, Any]:
        return self.get_json(
            "/api/public/themesvc/getAllThemesInfo",
            {"moreInfo": "Y"},
            bearer_token=True,
        )

    def retrieve_theme(self, query_name: str) -> dict[str, Any]:
        return self.get_json(
            "/api/public/themesvc/retrieveTheme",
            {"queryName": query_name},
            bearer_token=True,
        )


def score_search_result(result: dict[str, Any], block: str, street: str) -> tuple[int, str]:
    score = 0
    status = "heuristic"
    if normalize_text(str(result.get("BLK_NO", ""))) == normalize_text(block):
        score += 100
    if normalize_text(str(result.get("ROAD_NAME", ""))) == normalize_text(street):
        score += 50
    if score == 150:
        status = "exact"
    if str(result.get("LATITUDE", "")).strip() and str(result.get("LONGITUDE", "")).strip():
        score += 1
    return score, status


def geocode_block(client: OneMapClient, block: str, street: str) -> dict[str, Any]:
    query = f"{block} {street} SINGAPORE"
    response = client.search_address(query)
    results = response.get("results", [])
    if not isinstance(results, list) or not results:
        return {
            "block": block,
            "street": street,
            "query": query,
            "match_status": "not_found",
            "matched_address": "",
            "postal_code": "",
            "latitude": "",
            "longitude": "",
        }

    ranked = sorted(
        ((score_search_result(result, block, street), result) for result in results if isinstance(result, dict)),
        key=lambda item: item[0][0],
        reverse=True,
    )
    if not ranked:
        return {
            "block": block,
            "street": street,
            "query": query,
            "match_status": "not_found",
            "matched_address": "",
            "postal_code": "",
            "latitude": "",
            "longitude": "",
        }
    (score, status), chosen = ranked[0]
    return {
        "block": block,
        "street": street,
        "query": query,
        "match_status": status if score >= 100 else "heuristic",
        "matched_address": str(chosen.get("ADDRESS", "")),
        "postal_code": str(chosen.get("POSTAL", "")),
        "latitude": str(chosen.get("LATITUDE", "")),
        "longitude": str(chosen.get("LONGITUDE", "")),
    }


def load_cache(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    cache: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            record = json.loads(line)
            cache[str(record["key"])] = record
    return cache


def append_cache(path: Path, record: dict[str, Any], lock: threading.Lock) -> None:
    with lock:
        with path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, separators=(",", ":")) + "\n")


def read_blocks(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        required = {"blk_no", "street"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("HDB Property Information must include blk_no and street")
        blocks: dict[str, dict[str, str]] = {}
        for row in reader:
            block = str(row["blk_no"]).strip()
            street = str(row["street"]).strip()
            if block and street:
                blocks[address_key(block, street)] = {"block": block, "street": street}
    return [blocks[key] for key in sorted(blocks)]


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def get_public_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    for attempt in range(5):
        try:
            with urlopen(request, timeout=60) as response:
                payload = json.load(response)
            if not isinstance(payload, dict):
                raise ValueError("Expected a JSON object")
            return payload
        except HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt == 4:
                raise RuntimeError(f"data.gov.sg request failed with HTTP {error.code}") from error
            time.sleep(max(2**attempt, int(error.headers.get("Retry-After", "0") or 0)))
        except URLError as error:
            if attempt == 4:
                raise RuntimeError("data.gov.sg request could not be completed") from error
            time.sleep(2**attempt)
    raise RuntimeError("data.gov.sg request could not be completed")


def get_public_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(5):
        try:
            with urlopen(request, timeout=180) as response:
                return response.read()
        except HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt == 4:
                raise RuntimeError(f"data.gov.sg download failed with HTTP {error.code}") from error
            time.sleep(max(2**attempt, int(error.headers.get("Retry-After", "0") or 0)))
        except URLError as error:
            if attempt == 4:
                raise RuntimeError("data.gov.sg download could not be completed") from error
            time.sleep(2**attempt)
    raise RuntimeError("data.gov.sg download could not be completed")


def data_gov_download_url(dataset_id: str, initiate: bool) -> str:
    if initiate:
        initiate_url = f"{DATA_GOV_ROOT}/v1/public/api/datasets/{dataset_id}/initiate-download"
        initiated = get_public_json(initiate_url)
        data = initiated.get("data", {})
        if isinstance(data, dict) and data.get("url"):
            return str(data["url"])
    poll_url = f"{DATA_GOV_ROOT}/v1/public/api/datasets/{dataset_id}/poll-download"
    for attempt in range(6):
        if attempt:
            time.sleep(12)
        polled = get_public_json(poll_url)
        if polled.get("code") != 0:
            raise RuntimeError(f"data.gov.sg download failed: {polled.get('errMsg', 'unknown error')}")
        data = polled.get("data", {})
        if isinstance(data, dict) and data.get("url"):
            return str(data["url"])
        if isinstance(data, dict) and str(data.get("status", "")).lower() in {"failed", "error"}:
            break
    raise RuntimeError(f"data.gov.sg did not return a download URL for {dataset_id}")


def download_data_gov_geojson(dataset_id: str) -> dict[str, Any]:
    download_url = data_gov_download_url(dataset_id, initiate=False)
    return get_public_json(download_url)


def download_data_gov_csv(dataset_id: str) -> bytes:
    return get_public_bytes(data_gov_download_url(dataset_id, initiate=True))


def coordinate_from_value(value: Any) -> tuple[str, str, str] | None:
    if isinstance(value, str):
        numbers = [float(item) for item in re.findall(r"-?\d+(?:\.\d+)?", value)]
    elif isinstance(value, (list, tuple)):
        numbers = []
        stack = list(value)
        while stack:
            item = stack.pop(0)
            if isinstance(item, (list, tuple)):
                stack[0:0] = list(item)
            elif isinstance(item, (float, int)):
                numbers.append(float(item))
    else:
        return None
    for first, second in zip(numbers[::2], numbers[1::2]):
        if SINGAPORE_LONGITUDE[0] <= first <= SINGAPORE_LONGITUDE[1] and SINGAPORE_LATITUDE[0] <= second <= SINGAPORE_LATITUDE[1]:
            return f"{second:.8f}", f"{first:.8f}", "longitude_latitude"
        if SINGAPORE_LATITUDE[0] <= first <= SINGAPORE_LATITUDE[1] and SINGAPORE_LONGITUDE[0] <= second <= SINGAPORE_LONGITUDE[1]:
            return f"{first:.8f}", f"{second:.8f}", "latitude_longitude"
    return None


def extract_theme_coordinate(item: dict[str, Any]) -> tuple[str, str, str, str]:
    geojson = item.get("GeoJSON")
    if isinstance(geojson, dict):
        geometry = geojson.get("geometry")
        if isinstance(geometry, dict):
            coordinates = coordinate_from_value(geometry.get("coordinates"))
            if coordinates:
                latitude, longitude, order = coordinates
                return latitude, longitude, str(geometry.get("type", "")), f"geojson_{order}"
    coordinates = coordinate_from_value(item.get("LatLng"))
    if coordinates:
        latitude, longitude, order = coordinates
        return latitude, longitude, str(item.get("Type", "")), f"latlng_{order}"
    return "", "", str(item.get("Type", "")), "missing"


def amenity_search_key(category: str, item: dict[str, Any]) -> str:
    name = normalize_text(str(item.get("SEARCHVAL", item.get("BUILDING", ""))))
    postal = normalize_text(str(item.get("POSTAL", "")))
    latitude = str(item.get("LATITUDE", "")).strip()
    longitude = str(item.get("LONGITUDE", "")).strip()
    location = postal if postal and postal != "NIL" else f"{latitude}|{longitude}"
    return f"{category}|{name}|{location}"


def extract_search_amenities(
    client: OneMapClient,
    raw_dir: Path,
    project_root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    amenities_by_key: dict[str, dict[str, Any]] = {}
    summary: list[dict[str, Any]] = []
    for category, queries in ONEMAP_SEARCH_QUERIES.items():
        for query in queries:
            candidates = client.search_all_pages(query)
            slug = re.sub(r"[^a-z0-9]+", "_", query.lower()).strip("_")
            raw_path = raw_dir / f"search_{category}_{slug}.json"
            raw_path.write_text(json.dumps(candidates, indent=2) + "\n", encoding="utf-8")
            with_coordinates = 0
            for item in candidates:
                latitude = str(item.get("LATITUDE", "")).strip()
                longitude = str(item.get("LONGITUDE", "")).strip()
                if latitude and longitude:
                    with_coordinates += 1
                key = amenity_search_key(category, item)
                existing = amenities_by_key.get(key)
                if existing:
                    matched_queries = set(str(existing["source_query"]).split(" | "))
                    matched_queries.add(query)
                    existing["source_query"] = " | ".join(sorted(matched_queries))
                    continue
                amenities_by_key[key] = {
                    "amenity_category": category,
                    "source_type": "onemap_search",
                    "source_name": "OneMap Search API",
                    "source_query": query,
                    "name": str(item.get("SEARCHVAL", item.get("BUILDING", ""))),
                    "description": str(item.get("BUILDING", "")),
                    "address": str(item.get("ADDRESS", "")),
                    "postal_code": str(item.get("POSTAL", "")),
                    "latitude": latitude,
                    "longitude": longitude,
                    "geometry_type": "Point" if latitude and longitude else "",
                    "coordinate_method": "onemap_search_result" if latitude and longitude else "missing",
                    "source_properties_json": json.dumps(item, separators=(",", ":"), sort_keys=True),
                }
            summary.append(
                {
                    "amenity_category": category,
                    "source_type": "onemap_search",
                    "source_name": "OneMap Search API",
                    "source_query": query,
                    "records": len(candidates),
                    "records_with_coordinates": with_coordinates,
                    "raw_file": raw_path.relative_to(project_root).as_posix(),
                }
            )
    return list(amenities_by_key.values()), summary


def extract_rail_stations(raw_dir: Path, project_root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    geojson = download_data_gov_geojson(LTA_MRT_EXIT_DATASET_ID)
    raw_path = raw_dir / "lta_mrt_lrt_station_exits.geojson"
    raw_path.write_text(json.dumps(geojson, indent=2) + "\n", encoding="utf-8")
    grouped: dict[str, list[tuple[float, float, str]]] = {}
    features = geojson.get("features", [])
    if not isinstance(features, list):
        raise ValueError("LTA station exit dataset has no feature list")
    for feature in features:
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties", {})
        geometry = feature.get("geometry", {})
        if not isinstance(properties, dict) or not isinstance(geometry, dict):
            continue
        station_name = str(properties.get("STATION_NA", "")).strip()
        coordinates = geometry.get("coordinates")
        if not station_name or not isinstance(coordinates, list) or len(coordinates) < 2:
            continue
        try:
            longitude, latitude = float(coordinates[0]), float(coordinates[1])
        except (TypeError, ValueError):
            continue
        if not (SINGAPORE_LATITUDE[0] <= latitude <= SINGAPORE_LATITUDE[1] and SINGAPORE_LONGITUDE[0] <= longitude <= SINGAPORE_LONGITUDE[1]):
            continue
        grouped.setdefault(station_name, []).append((latitude, longitude, str(properties.get("EXIT_CODE", ""))))

    amenities: list[dict[str, Any]] = []
    for station_name, exits in sorted(grouped.items()):
        latitude = sum(item[0] for item in exits) / len(exits)
        longitude = sum(item[1] for item in exits) / len(exits)
        rail_type = "LRT" if " LRT " in f" {station_name.upper()} " else "MRT"
        source_details = {"station_name": station_name, "rail_type": rail_type, "exit_count": len(exits), "exit_codes": sorted({item[2] for item in exits if item[2]})}
        amenities.append(
            {
                "amenity_category": "mrt_lrt_stations",
                "source_type": "data_gov_sg",
                "source_name": "LTA MRT Station Exit (GEOJSON)",
                "source_query": LTA_MRT_EXIT_DATASET_ID,
                "name": station_name,
                "description": f"{rail_type} station; coordinate is the centroid of {len(exits)} exit location(s)",
                "address": "",
                "postal_code": "",
                "latitude": f"{latitude:.8f}",
                "longitude": f"{longitude:.8f}",
                "geometry_type": "Point",
                "coordinate_method": "centroid_of_station_exits",
                "source_properties_json": json.dumps(source_details, separators=(",", ":"), sort_keys=True),
            }
        )
    summary = {
        "amenity_category": "mrt_lrt_stations",
        "source_type": "data_gov_sg",
        "source_name": "LTA MRT Station Exit (GEOJSON)",
        "source_query": LTA_MRT_EXIT_DATASET_ID,
        "records": len(amenities),
        "records_with_coordinates": len(amenities),
        "raw_file": raw_path.relative_to(project_root).as_posix(),
    }
    return amenities, summary


def best_facility_geocode(client: OneMapClient, facility: dict[str, Any]) -> dict[str, str]:
    name = str(facility["name"])
    address = str(facility.get("address", ""))
    postal_code = str(facility.get("postal_code", ""))
    queries = [value for value in (postal_code, address, name) if value and value.lower() != "na"]
    candidates: list[dict[str, Any]] = []
    query_used = ""
    for query in queries:
        response = client.search_address(query)
        results = response.get("results", [])
        if isinstance(results, list) and results:
            candidates = [item for item in results if isinstance(item, dict)]
            query_used = query
            break
    if not candidates:
        return {
            "query": queries[0] if queries else name,
            "match_status": "not_found",
            "matched_address": "",
            "postal_code": postal_code,
            "latitude": "",
            "longitude": "",
        }

    normalized_name = normalize_text(name)
    normalized_postal = normalize_text(postal_code)

    def score(item: dict[str, Any]) -> int:
        item_name = normalize_text(str(item.get("SEARCHVAL", item.get("BUILDING", ""))))
        item_postal = normalize_text(str(item.get("POSTAL", "")))
        value = 0
        if normalized_postal and item_postal == normalized_postal:
            value += 200
        if item_name == normalized_name:
            value += 100
        elif normalized_name and normalized_name in item_name:
            value += 50
        if str(item.get("LATITUDE", "")).strip() and str(item.get("LONGITUDE", "")).strip():
            value += 1
        return value

    chosen = max(candidates, key=score)
    match_status = "exact_postal" if normalized_postal and normalize_text(str(chosen.get("POSTAL", ""))) == normalized_postal else "name_or_address"
    return {
        "query": query_used,
        "match_status": match_status,
        "matched_address": str(chosen.get("ADDRESS", "")),
        "postal_code": str(chosen.get("POSTAL", postal_code)),
        "latitude": str(chosen.get("LATITUDE", "")),
        "longitude": str(chosen.get("LONGITUDE", "")),
    }


def geocode_official_facilities(
    client: OneMapClient,
    facilities: list[dict[str, Any]],
    project_root: Path,
    workers: int,
) -> list[dict[str, Any]]:
    cache_path = project_root / "data" / "interim" / "onemap_facility_geocode_cache.jsonl"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache = load_cache(cache_path)
    cache_lock = threading.Lock()

    def cache_key(facility: dict[str, Any]) -> str:
        location_version = facility.get("postal_code") or facility.get("address") or facility.get("name")
        return (
            f"{facility['amenity_category']}|"
            f"{normalize_text(str(facility['source_id']))}|"
            f"{normalize_text(str(location_version))}"
        )

    missing = [facility for facility in facilities if cache_key(facility) not in cache]

    def lookup(facility: dict[str, Any]) -> dict[str, Any]:
        result = best_facility_geocode(client, facility)
        record = {"key": cache_key(facility), "retrieved_at_utc": now_utc(), **result}
        append_cache(cache_path, record, cache_lock)
        return record

    if missing:
        print(f"Geocoding {len(missing):,} official facilities; {len(cache):,} cached facility results will be reused.")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(lookup, facility) for facility in missing]
            for completed, future in enumerate(as_completed(futures), start=1):
                record = future.result()
                cache[str(record["key"])] = record
                if completed % 100 == 0 or completed == len(missing):
                    print(f"Geocoded {completed:,}/{len(missing):,} missing official facilities.")

    amenities: list[dict[str, Any]] = []
    for facility in facilities:
        result = cache[cache_key(facility)]
        amenities.append(
            {
                "amenity_category": facility["amenity_category"],
                "source_type": "official_directory_geocoded_by_onemap",
                "source_name": facility["source_name"],
                "source_query": facility["source_query"],
                "name": facility["name"],
                "description": facility.get("description", ""),
                "address": facility.get("address", "") or result.get("matched_address", ""),
                "postal_code": facility.get("postal_code", "") or result.get("postal_code", ""),
                "latitude": result.get("latitude", ""),
                "longitude": result.get("longitude", ""),
                "geometry_type": "Point" if result.get("latitude") and result.get("longitude") else "",
                "coordinate_method": f"onemap_{result.get('match_status', 'unknown')}",
                "source_properties_json": json.dumps(facility.get("source_properties", {}), separators=(",", ":"), sort_keys=True),
            }
        )
    return amenities


def read_csv_bytes(content: bytes) -> list[dict[str, str]]:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    return [{str(key).strip(): str(value or "").strip() for key, value in row.items()} for row in reader]


def parse_html_attributes(description: str) -> dict[str, str]:
    decoded = html.unescape(description)
    pairs = re.findall(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", decoded, flags=re.IGNORECASE | re.DOTALL)
    attributes: dict[str, str] = {}
    for key, value in pairs:
        clean_key = re.sub(r"<[^>]+>", "", key).strip()
        clean_value = re.sub(r"<[^>]+>", "", value).strip()
        attributes[clean_key] = clean_value
    return attributes


def geojson_point(feature: dict[str, Any]) -> tuple[str, str]:
    geometry = feature.get("geometry", {})
    coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        return "", ""
    try:
        longitude, latitude = float(coordinates[0]), float(coordinates[1])
    except (TypeError, ValueError):
        return "", ""
    if not (SINGAPORE_LATITUDE[0] <= latitude <= SINGAPORE_LATITUDE[1] and SINGAPORE_LONGITUDE[0] <= longitude <= SINGAPORE_LONGITUDE[1]):
        return "", ""
    return f"{latitude:.8f}", f"{longitude:.8f}"


def extract_official_facilities(
    client: OneMapClient,
    project_root: Path,
    workers: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_dir = project_root / "data" / "raw" / "data_gov_sg"
    raw_dir.mkdir(parents=True, exist_ok=True)

    school_content = download_data_gov_csv(MOE_SCHOOL_DATASET_ID)
    school_path = raw_dir / "moe_general_information_of_schools.csv"
    school_path.write_bytes(school_content)
    school_rows = read_csv_bytes(school_content)

    centre_content = download_data_gov_csv(ECDA_CENTRE_DATASET_ID)
    centre_path = raw_dir / "ecda_listing_of_centres.csv"
    centre_path.write_bytes(centre_content)
    centre_rows = read_csv_bytes(centre_content)

    facilities: list[dict[str, Any]] = []
    for row in school_rows:
        if row.get("mainlevel_code", "").upper() != "PRIMARY":
            continue
        facilities.append(
            {
                "amenity_category": "primary_schools",
                "source_id": row.get("school_name", ""),
                "source_name": "MOE General information of schools",
                "source_query": MOE_SCHOOL_DATASET_ID,
                "name": row.get("school_name", ""),
                "description": row.get("type_code", ""),
                "address": row.get("address", ""),
                "postal_code": row.get("postal_code", ""),
                "source_properties": row,
            }
        )
    for row in centre_rows:
        if row.get("service_model", "").upper() != "CC":
            continue
        facilities.append(
            {
                "amenity_category": "childcare_centres",
                "source_id": row.get("centre_code", row.get("centre_name", "")),
                "source_name": "ECDA Listing of Centres",
                "source_query": ECDA_CENTRE_DATASET_ID,
                "name": row.get("centre_name", ""),
                "description": row.get("organisation_description", ""),
                "address": row.get("centre_address", ""),
                "postal_code": row.get("postal_code", ""),
                "source_properties": row,
            }
        )
    for name in POLYCLINIC_NAMES:
        facilities.append(
            {
                "amenity_category": "polyclinics",
                "source_id": name,
                "source_name": "MOH/HealthHub official polyclinic list",
                "source_query": "https://support.healthhub.sg/hc/en-us/articles/59231470113689",
                "name": name,
                "description": "Public polyclinic",
                "address": "",
                "postal_code": "",
                "source_properties": {"official_name": name},
            }
        )

    geocoded = geocode_official_facilities(client, facilities, project_root, workers)
    amenities = list(geocoded)
    summary: list[dict[str, Any]] = []
    for category, source_name, source_query, raw_path in (
        ("primary_schools", "MOE General information of schools", MOE_SCHOOL_DATASET_ID, school_path),
        ("childcare_centres", "ECDA Listing of Centres", ECDA_CENTRE_DATASET_ID, centre_path),
        ("polyclinics", "MOH/HealthHub official polyclinic list", "HealthHub official list", None),
    ):
        category_rows = [row for row in geocoded if row["amenity_category"] == category]
        summary.append(
            {
                "amenity_category": category,
                "source_type": "official_directory_geocoded_by_onemap",
                "source_name": source_name,
                "source_query": source_query,
                "records": len(category_rows),
                "records_with_coordinates": sum(bool(row["latitude"] and row["longitude"]) for row in category_rows),
                "raw_file": raw_path.relative_to(project_root).as_posix() if raw_path else "",
            }
        )

    chas_geojson = download_data_gov_geojson(CHAS_CLINIC_DATASET_ID)
    chas_path = raw_dir / "moh_chas_clinics.geojson"
    chas_path.write_text(json.dumps(chas_geojson, indent=2) + "\n", encoding="utf-8")
    chas_rows: list[dict[str, Any]] = []
    for feature in chas_geojson.get("features", []):
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties", {})
        if not isinstance(properties, dict):
            continue
        attributes = parse_html_attributes(str(properties.get("Description", "")))
        latitude, longitude = geojson_point(feature)
        unit = "-".join(value for value in (attributes.get("FLOOR_NO", ""), attributes.get("UNIT_NO", "")) if value)
        address_parts = [
            " ".join(value for value in (attributes.get("BLK_HSE_NO", ""), attributes.get("STREET_NAME", "")) if value),
            f"#{unit}" if unit else "",
            attributes.get("BUILDING_NAME", ""),
        ]
        address = ", ".join(part for part in address_parts if part)
        postal_code = attributes.get("POSTAL_CD", "")
        if postal_code:
            address = f"{address}, Singapore {postal_code}" if address else f"Singapore {postal_code}"
        chas_rows.append(
            {
                "amenity_category": "healthcare_clinics",
                "source_type": "data_gov_sg_geojson",
                "source_name": "MOH CHAS Clinics",
                "source_query": CHAS_CLINIC_DATASET_ID,
                "name": attributes.get("HCI_NAME", ""),
                "description": f"Licence type: {attributes.get('LICENCE_TYPE', '')}; programmes: {attributes.get('CLINIC_PROGRAMME_CODE', '')}",
                "address": address,
                "postal_code": postal_code,
                "latitude": latitude,
                "longitude": longitude,
                "geometry_type": "Point" if latitude and longitude else "",
                "coordinate_method": "source_geojson" if latitude and longitude else "missing",
                "source_properties_json": json.dumps(attributes, separators=(",", ":"), sort_keys=True),
            }
        )
    amenities.extend(chas_rows)
    summary.append(
        {
            "amenity_category": "healthcare_clinics",
            "source_type": "data_gov_sg_geojson",
            "source_name": "MOH CHAS Clinics",
            "source_query": CHAS_CLINIC_DATASET_ID,
            "records": len(chas_rows),
            "records_with_coordinates": sum(bool(row["latitude"] and row["longitude"]) for row in chas_rows),
            "raw_file": chas_path.relative_to(project_root).as_posix(),
        }
    )
    return amenities, summary


def select_amenity_themes(catalog: dict[str, Any]) -> list[dict[str, str]]:
    selections: list[dict[str, str]] = []
    themes = catalog.get("Theme_Names", [])
    if not isinstance(themes, list):
        raise ValueError("OneMap returned an invalid theme catalog")
    for category, keywords in AMENITY_KEYWORDS.items():
        matches = []
        allowed_query_names = set(AMENITY_THEME_QUERY_ALLOWLIST.get(category, ()))
        for theme in themes:
            if not isinstance(theme, dict):
                continue
            name = str(theme.get("THEMENAME", ""))
            query_name = str(theme.get("QUERYNAME", ""))
            normalized_name = normalize_text(name).lower()
            is_match = (
                query_name in allowed_query_names
                if allowed_query_names
                else any(keyword in normalized_name for keyword in keywords)
            )
            if query_name and is_match:
                matches.append({"category": category, "theme_name": name, "query_name": query_name})
        if matches:
            selections.extend(matches)
        else:
            print(f"Warning: no OneMap theme matched amenity category '{category}'", file=sys.stderr)
    unique: dict[tuple[str, str], dict[str, str]] = {}
    for selection in selections:
        unique[(selection["category"], selection["query_name"])] = selection
    return sorted(unique.values(), key=lambda item: (item["category"], item["query_name"]))


def extract_amenities(client: OneMapClient, project_root: Path, workers: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_dir = project_root / "data" / "raw" / "onemap"
    processed_dir = project_root / "data" / "processed"
    raw_dir.mkdir(parents=True, exist_ok=True)
    catalog = client.get_all_themes()
    (raw_dir / "theme_catalog.json").write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")

    amenities: list[dict[str, Any]] = []
    summary: list[dict[str, Any]] = []
    for selection in select_amenity_themes(catalog):
        response = client.retrieve_theme(selection["query_name"])
        raw_path = raw_dir / f"theme_{selection['query_name']}.json"
        raw_path.write_text(json.dumps(response, indent=2) + "\n", encoding="utf-8")
        candidates = response.get("SrchResults", [])
        count = 0
        with_coordinates = 0
        if isinstance(candidates, list):
            for item in candidates:
                if not isinstance(item, dict) or "FeatCount" in item:
                    continue
                latitude, longitude, geometry_type, coordinate_method = extract_theme_coordinate(item)
                if latitude and longitude:
                    with_coordinates += 1
                amenities.append(
                    {
                        "amenity_category": selection["category"],
                        "source_type": "onemap_theme",
                        "source_name": selection["theme_name"],
                        "source_query": selection["query_name"],
                        "name": str(item.get("NAME", item.get("MAPTIP", ""))),
                        "description": str(item.get("DESCRIPTION", "")),
                        "address": str(item.get("ADDRESS", "")),
                        "postal_code": str(item.get("POSTAL", "")),
                        "latitude": latitude,
                        "longitude": longitude,
                        "geometry_type": geometry_type,
                        "coordinate_method": coordinate_method,
                        "source_properties_json": json.dumps(item, separators=(",", ":"), sort_keys=True),
                    }
                )
                count += 1
        summary.append(
            {
                "amenity_category": selection["category"],
                "source_type": "onemap_theme",
                "source_name": selection["theme_name"],
                "source_query": selection["query_name"],
                "records": count,
                "records_with_coordinates": with_coordinates,
                "raw_file": raw_path.relative_to(project_root).as_posix(),
            }
        )

    search_amenities, search_summary = extract_search_amenities(client, raw_dir, project_root)
    rail_amenities, rail_summary = extract_rail_stations(raw_dir, project_root)
    official_amenities, official_summary = extract_official_facilities(client, project_root, workers)
    amenities.extend(search_amenities)
    amenities.extend(rail_amenities)
    amenities.extend(official_amenities)
    summary.extend(search_summary)
    summary.append(rail_summary)
    summary.extend(official_summary)
    amenities.sort(key=lambda row: (str(row["amenity_category"]), normalize_text(str(row["name"])), str(row["address"])))

    amenity_fields = [
        "amenity_category",
        "source_type",
        "source_name",
        "source_query",
        "name",
        "description",
        "address",
        "postal_code",
        "latitude",
        "longitude",
        "geometry_type",
        "coordinate_method",
        "source_properties_json",
    ]
    write_csv(
        processed_dir / "onemap_amenities_with_coordinates.csv",
        amenities,
        amenity_fields,
    )
    write_csv(
        processed_dir / "primary_schools_with_coordinates.csv",
        [row for row in amenities if row["amenity_category"] == "primary_schools"],
        amenity_fields,
    )
    write_csv(
        processed_dir / "childcare_centres_with_coordinates.csv",
        [row for row in amenities if row["amenity_category"] == "childcare_centres"],
        amenity_fields,
    )
    write_csv(
        processed_dir / "healthcare_facilities_with_coordinates.csv",
        [row for row in amenities if row["amenity_category"] in {"healthcare_clinics", "hospitals", "polyclinics"}],
        amenity_fields,
    )
    write_csv(
        processed_dir / "onemap_amenity_category_summary.csv",
        summary,
        ["amenity_category", "source_type", "source_name", "source_query", "records", "records_with_coordinates", "raw_file"],
    )
    return amenities, summary


def extract_block_coordinates(
    client: OneMapClient,
    project_root: Path,
    workers: int,
) -> dict[str, int]:
    source_path = project_root / "data" / "raw" / "hdb_property_information.csv"
    cache_path = project_root / "data" / "interim" / "onemap_block_geocode_cache.jsonl"
    output_path = project_root / "data" / "processed" / "hdb_blocks_with_coordinates.csv"
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    blocks = read_blocks(source_path)
    cache = load_cache(cache_path)
    missing = [block for block in blocks if address_key(block["block"], block["street"]) not in cache]
    cache_lock = threading.Lock()

    def lookup(block: dict[str, str]) -> dict[str, Any]:
        result = geocode_block(client, block["block"], block["street"])
        record = {"key": address_key(block["block"], block["street"]), "retrieved_at_utc": now_utc(), **result}
        append_cache(cache_path, record, cache_lock)
        return record

    if missing:
        print(f"Geocoding {len(missing):,} HDB blocks; {len(cache):,} cached results will be reused.")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(lookup, block) for block in missing]
            for completed, future in enumerate(as_completed(futures), start=1):
                record = future.result()
                cache[str(record["key"])] = record
                if completed % 100 == 0 or completed == len(missing):
                    print(f"Geocoded {completed:,}/{len(missing):,} missing blocks.")

    rows = []
    for block in blocks:
        record = cache.get(address_key(block["block"], block["street"]))
        if not record:
            raise RuntimeError(f"No coordinate record available for {block['block']} {block['street']}")
        rows.append(record)
    fields = ["block", "street", "query", "match_status", "matched_address", "postal_code", "latitude", "longitude", "retrieved_at_utc"]
    write_csv(output_path, rows, fields)
    return Counter(str(row["match_status"]) for row in rows)


def run(args: argparse.Namespace) -> None:
    token = os.environ.get("ONEMAP_TOKEN", "").strip()
    if not token:
        raise ValueError("ONEMAP_TOKEN is required. Set it in the process environment before running this script.")
    project_root = args.project_root.resolve()
    required = project_root / "data" / "raw" / "hdb_property_information.csv"
    if not required.exists():
        raise FileNotFoundError(f"Missing source dataset: {required}")

    client = OneMapClient(token, args.requests_per_minute)
    block_statuses = extract_block_coordinates(client, project_root, args.workers)
    _, amenity_summary = extract_amenities(client, project_root, args.workers)
    report = {
        "generated_at_utc": now_utc(),
        "hdb_blocks": {"total": sum(block_statuses.values()), "match_statuses": dict(sorted(block_statuses.items()))},
        "amenity_sources": amenity_summary,
        "api": {"providers": ["OneMap", "data.gov.sg"], "requests_per_minute": args.requests_per_minute},
    }
    report_path = project_root / "reports/phase_1_resale_model" / "02_onemap_extraction_manifest.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {sum(block_statuses.values()):,} HDB block coordinate records.")
    print(f"Saved {len(amenity_summary):,} amenity source summaries.")
    print("Outputs: HDB blocks, amenities, primary schools, childcare centres, and healthcare facility coordinate tables in data/processed/.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--requests-per-minute", type=int, default=240, help="Maximum OneMap request rate; 240 is below the documented 300/minute limit.")
    parser.add_argument("--workers", type=int, default=8, help="Concurrent address lookups.")
    return parser.parse_args()


def main() -> int:
    try:
        run(parse_args())
    except (FileNotFoundError, HTTPError, OSError, RuntimeError, URLError, ValueError) as error:
        print(f"OneMap extraction failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
