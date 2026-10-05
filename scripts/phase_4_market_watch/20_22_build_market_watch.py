"""Build Phase 4 saved-search rankings, listing history, and an HTML market watch."""

from __future__ import annotations

import argparse
import html
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb


PHASE = "phase_4_market_watch"
DEFAULT_PROFILE = {
    "profile_id": "singapore_hdb_value_watch_default",
    "profile_name": "Singapore HDB value watch",
    "source_site": "propertyguru",
    "town": "ALL",
    "maximum_price_sgd": None,
    "minimum_floor_area_sqm": None,
    "flat_types": [],
    "minimum_comparable_count": 3,
    "maximum_listing_age_days": 14,
    "require_estimate": True,
}


def read_rows(connection: duckdb.DuckDBPyConnection, query: str, parameters: list | None = None) -> list[dict]:
    cursor = connection.execute(query, parameters or [])
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, values)) for values in cursor.fetchall()]


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, value))


def validate_profile(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Preferences must be a JSON object")
    profile = {**DEFAULT_PROFILE, **raw}
    for field in ("profile_id", "profile_name", "source_site", "town"):
        if not isinstance(profile[field], str) or not profile[field].strip():
            raise ValueError(f"{field} must be a non-empty string")
        profile[field] = profile[field].strip()
    for field in ("maximum_price_sgd", "minimum_floor_area_sqm"):
        value = profile[field]
        if value is not None and (not isinstance(value, (int, float)) or value <= 0):
            raise ValueError(f"{field} must be a positive number or null")
    for field in ("minimum_comparable_count", "maximum_listing_age_days"):
        value = profile[field]
        if not isinstance(value, int) or value < 0:
            raise ValueError(f"{field} must be a whole number of zero or more")
    if not isinstance(profile["flat_types"], list) or not all(isinstance(item, str) for item in profile["flat_types"]):
        raise ValueError("flat_types must be a list of text values")
    profile["flat_types"] = sorted({item.strip().upper() for item in profile["flat_types"] if item.strip()})
    if not isinstance(profile["require_estimate"], bool):
        raise ValueError("require_estimate must be true or false")
    return profile


def load_profile(path: Path | None) -> dict[str, Any]:
    if path is None:
        return validate_profile(DEFAULT_PROFILE)
    try:
        return validate_profile(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError as error:
        raise ValueError(f"Cannot read preferences JSON: {error}") from error


def suitability_score(row: dict, profile: dict) -> tuple[float, bool, list[str]]:
    scores: list[float] = []
    eligible = True
    notes: list[str] = []
    maximum_price = profile["maximum_price_sgd"]
    if maximum_price is not None:
        asking = row.get("asking_price_sgd")
        if asking is None:
            scores.append(0.0)
            eligible = False
            notes.append("asking price missing")
        elif asking <= maximum_price:
            scores.append(100.0)
        else:
            scores.append(clamp(100 - ((asking - maximum_price) / maximum_price) * 250))
            eligible = False
            notes.append("over budget")
    minimum_area = profile["minimum_floor_area_sqm"]
    if minimum_area is not None:
        area = row.get("floor_area_sqm")
        if area is None:
            scores.append(0.0)
            eligible = False
            notes.append("floor area missing")
        elif area >= minimum_area:
            scores.append(100.0)
        else:
            scores.append(clamp(area / minimum_area * 100))
            eligible = False
            notes.append("below preferred size")
    types = profile["flat_types"]
    if types:
        if row.get("inferred_flat_type") in types:
            scores.append(100.0)
        else:
            scores.append(0.0)
            eligible = False
            notes.append("outside preferred flat types")
    return (sum(scores) / len(scores) if scores else 100.0), eligible, notes


def freshness_score(last_seen_utc: str | None, maximum_days: int, now: datetime) -> tuple[float, int | None, bool]:
    observed = parse_utc(last_seen_utc)
    if observed is None:
        return 0.0, None, False
    age = max(0, math.floor((now - observed.astimezone(timezone.utc)).total_seconds() / 86400))
    if maximum_days == 0:
        return (100.0 if age == 0 else 0.0), age, age == 0
    return clamp(100 * (1 - age / maximum_days)), age, age <= maximum_days


def rank_listing(row: dict, profile: dict, now: datetime) -> dict:
    suitability, eligible, notes = suitability_score(row, profile)
    freshness, age_days, fresh = freshness_score(row.get("last_seen_utc"), profile["maximum_listing_age_days"], now)
    if not fresh:
        eligible = False
        notes.append("stale snapshot")
    has_estimate = row.get("valuation_status") == "estimated"
    if profile["require_estimate"] and not has_estimate:
        eligible = False
        notes.append("no supported estimate")
    premium = row.get("asking_premium_discount_pct")
    value = clamp(50 - premium * 2.5) if premium is not None else 0.0
    comparable_count = row.get("minimum_comparable_count") or 0
    evidence = clamp(comparable_count / max(1, profile["minimum_comparable_count"]) * 100)
    if comparable_count < profile["minimum_comparable_count"]:
        eligible = False
        notes.append("too few comparable sales")
    if row.get("confidence_label") != "scenario_range":
        evidence *= 0.6
        notes.append("lower estimate confidence")
    total = 0.50 * value + 0.25 * suitability + 0.15 * evidence + 0.10 * freshness
    opportunity = (
        "investigate" if eligible and has_estimate and premium is not None and premium <= 0
        else "watch" if eligible and has_estimate else "not_ranked"
    )
    if opportunity == "investigate":
        notes.insert(0, "asking price is at or below the model point estimate")
    return {
        **row,
        "value_score": round(value, 2), "suitability_score": round(suitability, 2),
        "evidence_score": round(evidence, 2), "freshness_score": round(freshness, 2),
        "listing_age_days": age_days, "eligible": eligible, "opportunity_label": opportunity,
        "ranking_score": round(total, 2) if has_estimate else None,
        "ranking_notes": "; ".join(dict.fromkeys(notes)) or None,
    }


def snapshot_history(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    by_source: dict[str, dict[str, list[dict]]] = {}
    for row in rows:
        by_source.setdefault(row["source_site"], {}).setdefault(row["run_id"], []).append(row)
    history: list[dict] = []
    summaries: list[dict] = []
    for source_site, runs in by_source.items():
        ordered_runs = sorted(runs.items(), key=lambda item: (max(item[1], key=lambda row: row["last_seen_utc"])["last_seen_utc"], item[0]))
        previous: dict[str, dict] = {}
        for sequence, (run_id, records) in enumerate(ordered_runs, start=1):
            current = {record["listing_id"]: record for record in records}
            new_count = reduced_count = increased_count = unchanged_count = 0
            for listing_id, record in current.items():
                earlier = previous.get(listing_id)
                change = "new" if earlier is None else "unchanged"
                previous_price = earlier.get("asking_price_sgd") if earlier else None
                current_price = record.get("asking_price_sgd")
                if earlier is None:
                    new_count += 1
                elif previous_price is not None and current_price is not None and current_price < previous_price:
                    change = "price_reduced"
                    reduced_count += 1
                elif previous_price is not None and current_price is not None and current_price > previous_price:
                    change = "price_increased"
                    increased_count += 1
                else:
                    unchanged_count += 1
                difference = None if previous_price is None or current_price is None else current_price - previous_price
                history.append({
                    "source_site": source_site, "run_id": run_id, "listing_id": listing_id,
                    "snapshot_sequence": sequence, "listing_url": record["listing_url"],
                    "observed_at_utc": record["last_seen_utc"], "asking_price_sgd": current_price,
                    "previous_asking_price_sgd": previous_price, "price_change_sgd": difference,
                    "change_type": change,
                })
            disappeared = len(set(previous) - set(current))
            observed = max(record["last_seen_utc"] for record in records)
            summaries.append({
                "source_site": source_site, "run_id": run_id, "snapshot_sequence": sequence,
                "observed_at_utc": observed, "listing_count": len(records), "new_count": new_count,
                "price_reduced_count": reduced_count, "price_increased_count": increased_count,
                "unchanged_count": unchanged_count, "disappeared_from_previous_count": disappeared,
            })
            previous = current
    return history, summaries


def create_history_tables(connection: duckdb.DuckDBPyConnection, history: list[dict], summaries: list[dict], built_at: str) -> None:
    connection.execute("DROP TABLE IF EXISTS listing_snapshot_history")
    connection.execute("DROP TABLE IF EXISTS listing_snapshot_runs")
    connection.execute("""
        CREATE TABLE listing_snapshot_history (
            source_site VARCHAR, run_id VARCHAR, listing_id VARCHAR, snapshot_sequence INTEGER,
            listing_url VARCHAR, observed_at_utc VARCHAR, asking_price_sgd BIGINT,
            previous_asking_price_sgd BIGINT, price_change_sgd BIGINT, change_type VARCHAR,
            built_at_utc VARCHAR, PRIMARY KEY(source_site, run_id, listing_id))
    """)
    connection.execute("""
        CREATE TABLE listing_snapshot_runs (
            source_site VARCHAR, run_id VARCHAR, snapshot_sequence INTEGER, observed_at_utc VARCHAR,
            listing_count INTEGER, new_count INTEGER, price_reduced_count INTEGER,
            price_increased_count INTEGER, unchanged_count INTEGER,
            disappeared_from_previous_count INTEGER, built_at_utc VARCHAR,
            PRIMARY KEY(source_site, run_id))
    """)
    connection.executemany("INSERT INTO listing_snapshot_history VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
        [row[key] for key in ("source_site", "run_id", "listing_id", "snapshot_sequence", "listing_url", "observed_at_utc", "asking_price_sgd", "previous_asking_price_sgd", "price_change_sgd", "change_type")] + [built_at]
        for row in history
    ])
    connection.executemany("INSERT INTO listing_snapshot_runs VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
        [row[key] for key in ("source_site", "run_id", "snapshot_sequence", "observed_at_utc", "listing_count", "new_count", "price_reduced_count", "price_increased_count", "unchanged_count", "disappeared_from_previous_count")] + [built_at]
        for row in summaries
    ])


def create_preference_and_ranking_tables(connection: duckdb.DuckDBPyConnection, profile: dict, rows: list[dict], built_at: str) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS market_watch_preferences (
            profile_id VARCHAR PRIMARY KEY, profile_name VARCHAR, source_site VARCHAR, town VARCHAR,
            maximum_price_sgd DOUBLE, minimum_floor_area_sqm DOUBLE, flat_types_json VARCHAR,
            minimum_comparable_count INTEGER, maximum_listing_age_days INTEGER, require_estimate BOOLEAN,
            updated_at_utc VARCHAR)
    """)
    connection.execute("DELETE FROM market_watch_preferences WHERE profile_id = ?", [profile["profile_id"]])
    connection.execute("INSERT INTO market_watch_preferences VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
        profile["profile_id"], profile["profile_name"], profile["source_site"], profile["town"],
        profile["maximum_price_sgd"], profile["minimum_floor_area_sqm"], json.dumps(profile["flat_types"]),
        profile["minimum_comparable_count"], profile["maximum_listing_age_days"], profile["require_estimate"], built_at,
    ])
    connection.execute("DROP TABLE IF EXISTS market_watch_rankings")
    connection.execute("""
        CREATE TABLE market_watch_rankings (
            profile_id VARCHAR, source_site VARCHAR, run_id VARCHAR, listing_id VARCHAR,
            listing_url VARCHAR, title VARCHAR, address VARCHAR, inferred_flat_type VARCHAR,
            floor_area_sqm DOUBLE, asking_price_sgd BIGINT, lower_estimate_sgd DOUBLE,
            point_estimate_sgd DOUBLE, upper_estimate_sgd DOUBLE, asking_premium_discount_sgd DOUBLE,
            asking_premium_discount_pct DOUBLE, minimum_comparable_count INTEGER,
            confidence_label VARCHAR, listing_age_days INTEGER, value_score DOUBLE,
            suitability_score DOUBLE, evidence_score DOUBLE, freshness_score DOUBLE,
            ranking_score DOUBLE, eligible BOOLEAN, opportunity_label VARCHAR,
            ranking_notes VARCHAR, change_type VARCHAR, built_at_utc VARCHAR,
            PRIMARY KEY(profile_id, source_site, run_id, listing_id))
    """)
    keys = [column[0] for column in connection.execute("DESCRIBE market_watch_rankings").fetchall() if column[0] != "built_at_utc"]
    marks = ",".join("?" for _ in range(len(keys) + 1))
    connection.executemany(f"INSERT INTO market_watch_rankings VALUES ({marks})", [
        [row.get(key) for key in keys] + [built_at] for row in rows
    ])


def money(value: float | int | None) -> str:
    return "—" if value is None else f"S${value:,.0f}"


def html_report(profile: dict, ranked: list[dict], snapshots: list[dict], generated: str) -> str:
    eligible = [row for row in ranked if row["opportunity_label"] in {"investigate", "watch"}]
    eligible.sort(key=lambda row: (row["opportunity_label"] != "investigate", -(row["ranking_score"] or -1), row["listing_id"]))
    snapshot = snapshots[-1] if snapshots else None
    rows = []
    for row in eligible[:50]:
        delta = row["asking_premium_discount_pct"]
        difference = "—" if delta is None else f"{delta:+.1f}% vs point estimate"
        notes = html.escape(row["ranking_notes"] or "")
        rows.append(f"""<tr><td><a href=\"{html.escape(row['listing_url'], quote=True)}\" target=\"_blank\">{html.escape(row['title'] or row['listing_id'])}</a><small>{html.escape(row['address'] or '')}</small></td><td>{html.escape(row['inferred_flat_type'] or 'Unknown')}<small>{row['floor_area_sqm'] or '—'} sqm</small></td><td>{money(row['asking_price_sgd'])}</td><td>{money(row['lower_estimate_sgd'])}–{money(row['upper_estimate_sgd'])}<small>{difference}</small></td><td>{row['minimum_comparable_count'] or 0}<small>{html.escape(row['confidence_label'] or '')}</small></td><td><strong>{html.escape(row['opportunity_label'])}</strong><small>score {row['ranking_score']:.1f}; {notes}</small></td></tr>""")
    table = "\n".join(rows) or "<tr><td colspan=\"6\">No listings currently meet this profile. Adjust preferences or collect a newer snapshot.</td></tr>"
    snapshot_text = "No complete snapshot found" if snapshot is None else (
        f"Snapshot {snapshot['snapshot_sequence']}: {snapshot['listing_count']} listings; "
        f"{snapshot['new_count']} new; {snapshot['price_reduced_count']} price reductions; "
        f"observed {html.escape(snapshot['observed_at_utc'])}."
    )
    preference_text = []
    if profile["maximum_price_sgd"] is not None:
        preference_text.append(f"maximum price {money(profile['maximum_price_sgd'])}")
    if profile["minimum_floor_area_sqm"] is not None:
        preference_text.append(f"minimum size {profile['minimum_floor_area_sqm']} sqm")
    if profile["flat_types"]:
        preference_text.append("flat types " + ", ".join(profile["flat_types"]))
    preferences = "; ".join(preference_text) or "no budget, size or flat-type filters"
    return f"""<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>{html.escape(profile['profile_name'])}</title><style>body{{font-family:Arial,sans-serif;max-width:1200px;margin:36px auto;padding:0 18px;color:#142432}}h1{{margin-bottom:4px}}.muted,small{{display:block;color:#5b6b78;margin-top:5px}}.notice{{background:#f1f7f5;border-left:4px solid #157a62;padding:14px;margin:20px 0}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{border-bottom:1px solid #d7e0e3;padding:12px 8px;text-align:left;vertical-align:top}}th{{background:#f5f8f9}}a{{color:#086c5d}}strong{{text-transform:capitalize}}.limits{{margin:8px 0 22px}}</style></head><body><h1>{html.escape(profile['profile_name'])}</h1><p class=\"muted\">Generated {html.escape(generated)}. {snapshot_text}</p><p class=\"limits\"><strong>Profile:</strong> {html.escape(preferences)}. Listings need an estimate, at least {profile['minimum_comparable_count']} comparable sales, and a snapshot no older than {profile['maximum_listing_age_days']} days.</p><div class=\"notice\"><strong>How to use this:</strong> “Investigate” means the asking price is at or below this research estimate and the listing matches the current profile. It is a prompt to verify the unit, not an official valuation or a guarantee of a good deal. Price ranges rely on inferred floor and flat-model assumptions.</div><table><thead><tr><th>Listing</th><th>Inferred unit</th><th>Asking price</th><th>Research range</th><th>Evidence</th><th>Watch status</th></tr></thead><tbody>{table}</tbody></table></body></html>"""


def build(root: Path, preferences_path: Path | None = None) -> dict:
    database = root / "data" / "listings.db"
    if not database.is_file():
        raise ValueError("data/listings.db is required")
    profile = load_profile(preferences_path)
    now = datetime.now(timezone.utc)
    built_at = now.isoformat()
    with duckdb.connect(str(database)) as connection:
        required = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        missing = {"listings", "listing_features", "listing_valuations"} - required
        if missing:
            raise ValueError("Run Steps 17–19 before Steps 20–22; missing " + ", ".join(sorted(missing)))
        source_rows = read_rows(connection, "SELECT * FROM listings WHERE source_site = ?", [profile["source_site"]])
        history, snapshots = snapshot_history(source_rows)
        create_history_tables(connection, history, snapshots, built_at)
        latest_run = next((row["run_id"] for row in reversed(snapshots) if row["source_site"] == profile["source_site"]), None)
        if latest_run is None:
            raise ValueError(f"No saved listings found for {profile['source_site']}")
        listing_rows = read_rows(connection, """
            SELECT f.*, l.title, l.address, v.valuation_status, v.lower_estimate_sgd, v.point_estimate_sgd,
                   v.upper_estimate_sgd, v.asking_premium_discount_sgd,
                   v.asking_premium_discount_pct, v.minimum_comparable_count,
                   v.confidence_label, h.change_type
            FROM listing_features f
            JOIN listings l USING (source_site, run_id, listing_id)
            JOIN listing_valuations v USING (source_site, run_id, listing_id)
            JOIN listing_snapshot_history h USING (source_site, run_id, listing_id)
            WHERE f.source_site = ? AND f.run_id = ?
        """, [profile["source_site"], latest_run])
        ranked = [{"profile_id": profile["profile_id"], **rank_listing(row, profile, now)} for row in listing_rows]
        create_preference_and_ranking_tables(connection, profile, ranked, built_at)
    ranked.sort(key=lambda row: (row["opportunity_label"] != "investigate", -(row["ranking_score"] or -1), row["listing_id"]))
    report_dir = root / "reports" / PHASE
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "success", "built_at_utc": built_at, "profile": profile,
        "latest_run_id": latest_run, "snapshot_runs": snapshots,
        "counts": {
            "ranked_listings": len(ranked),
            "investigate": sum(row["opportunity_label"] == "investigate" for row in ranked),
            "watch": sum(row["opportunity_label"] == "watch" for row in ranked),
            "not_ranked": sum(row["opportunity_label"] == "not_ranked" for row in ranked),
        },
        "tables": ["market_watch_preferences", "market_watch_rankings", "listing_snapshot_history", "listing_snapshot_runs"],
        "limitations": [
            "A change history needs at least two complete collection runs before it can identify price changes.",
            "Ranking uses listing-card information and the Step 19 research estimates.",
            "The report is not an official valuation or financial advice.",
        ],
    }
    (report_dir / "22_market_watch_summary.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (report_dir / "22_market_watch.html").write_text(html_report(profile, ranked, snapshots, built_at), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--preferences", type=Path, help="Optional saved-search preferences JSON file")
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.project_root.resolve(), args.preferences), indent=2))
    except duckdb.IOException as error:
        parser.exit(1, f"Cannot open listings.db. Disconnect it in DBeaver and retry. {error}\n")
    except ValueError as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
