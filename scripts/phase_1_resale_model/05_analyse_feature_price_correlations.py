"""Profile each transaction feature's association with HDB resale price.

Uses the training split only. This is exploratory association analysis, not a
causal or multivariable feature-importance estimate.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

try:
    import duckdb
except ImportError as error:
    raise SystemExit("DuckDB is required. Run .\\scripts\\phase_1_resale_model\\03_run_duckdb_build.ps1 first.") from error


TARGET = "resale_price"
IDENTIFIER_COLUMNS = {"transaction_id", "block_id"}
METADATA_COLUMNS = {"dataset_split"}
TIME_COLUMNS = {"transaction_month", "transaction_year", "transaction_month_number", "transaction_quarter"}


def pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    mean_left, mean_right = statistics.fmean(left), statistics.fmean(right)
    centered_left = [value - mean_left for value in left]
    centered_right = [value - mean_right for value in right]
    sum_left = sum(value * value for value in centered_left)
    sum_right = sum(value * value for value in centered_right)
    if sum_left == 0 or sum_right == 0:
        return None
    return sum(a * b for a, b in zip(centered_left, centered_right)) / math.sqrt(sum_left * sum_right)


def average_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and values[order[end]] == values[order[cursor]]:
            end += 1
        rank = (cursor + 1 + end) / 2
        for index in order[cursor:end]:
            ranks[index] = rank
        cursor = end
    return ranks


def eta_squared(groups: list[str], target: list[float]) -> float | None:
    if len(groups) != len(target) or len(target) < 2:
        return None
    values: dict[str, list[float]] = defaultdict(list)
    for group, price in zip(groups, target):
        values[group].append(price)
    total_mean = statistics.fmean(target)
    total_ss = sum((price - total_mean) ** 2 for price in target)
    if total_ss == 0:
        return None
    between_ss = sum(len(prices) * (statistics.fmean(prices) - total_mean) ** 2 for prices in values.values())
    return between_ss / total_ss


def within_group_pearson(feature: list[float], target: list[float], groups: list[str]) -> float | None:
    feature_sums: dict[str, list[float]] = defaultdict(list)
    target_sums: dict[str, list[float]] = defaultdict(list)
    for group, x, y in zip(groups, feature, target):
        feature_sums[group].append(x)
        target_sums[group].append(y)
    residual_x, residual_y = [], []
    for group, x, y in zip(groups, feature, target):
        residual_x.append(x - statistics.fmean(feature_sums[group]))
        residual_y.append(y - statistics.fmean(target_sums[group]))
    return pearson(residual_x, residual_y)


def time_number(value: date | datetime) -> float:
    return float(value.year * 12 + value.month)


def strength_for_correlation(value: float | None) -> str:
    if value is None:
        return "not measurable"
    absolute = abs(value)
    if absolute >= 0.50:
        return "strong univariate association"
    if absolute >= 0.30:
        return "moderate univariate association"
    if absolute >= 0.10:
        return "weak univariate association"
    return "very weak univariate association"


def strength_for_eta(value: float | None) -> str:
    if value is None:
        return "not measurable"
    if value >= 0.25:
        return "large group association"
    if value >= 0.09:
        return "medium group association"
    if value >= 0.01:
        return "small group association"
    return "very small group association"


def field_roles(name: str, distinct: int, count: int) -> tuple[str, str]:
    if name == TARGET:
        return "target", "Outcome to predict; never include as an input feature."
    if name in IDENTIFIER_COLUMNS:
        if name == "block_id":
            return "identifier", "Raw key; do not feed as text. Use coordinates or past-only block price history instead."
        return "identifier", "Unique record key; exclude from model inputs."
    if name in METADATA_COLUMNS:
        return "evaluation metadata", "Train/validation/test label; exclude from model inputs."
    if name == "town":
        return "categorical", "Only Sengkang is present, so this column cannot distinguish prices in the current dataset."
    if name == "transaction_month":
        return "date/time", "Calendar trend feature; association does not prove a causal time effect."
    if name in {"transaction_year", "transaction_month_number", "transaction_quarter"}:
        return "date/time", "Calendar encoding; overlaps other time columns and can miss market cycles."
    if name == "latitude":
        return "location", "Spatial coordinate; a linear correlation can miss neighbourhood patterns."
    if name == "longitude":
        return "location", "Spatial coordinate; a linear correlation can miss neighbourhood patterns."
    if name in {"year_completed", "building_age_at_transaction"}:
        return "building", "Building age/completion information; overlaps with the other age column."
    if name in {"lease_commence_year", "remaining_lease_months"}:
        return "lease", "Lease-related feature; partially determined by the other lease field and transaction date."
    if name in {"storey_range", "storey_midpoint"}:
        return ("categorical" if name == "storey_range" else "floor level"), "Storey band and its numeric midpoint represent overlapping information."
    if name in {"nearest_mrt_lrt_stations_m", "mrt_lrt_stations_within_1km"}:
        return "transport access", "MRT/LRT proximity or count; effects may be nonlinear and overlap each other."
    if name.startswith("nearest_") or name.endswith("_within_1km"):
        return "amenity access", "Amenity proximity/count; current facility locations may not match historical sale dates."
    if name in {"commercial", "market_hawker", "multistorey_carpark", "precinct_pavilion"}:
        return "block feature", "Block-level flag; may represent very few blocks and should be checked for sample size."
    if name in {"flat_type", "flat_model"}:
        return "flat category", "Categorical housing attribute; effect can vary with size, lease, and location."
    return "numeric feature", "Association is descriptive, not an independent or causal effect."


def analyse(project_root: Path) -> tuple[list[dict], dict]:
    database = project_root / "data" / "property.duckdb"
    if not database.is_file():
        raise ValueError(f"DuckDB database not found: {database}")
    with duckdb.connect(str(database), read_only=True) as connection:
        description = connection.execute("DESCRIBE transaction_features").fetchall()
        columns = [row[0] for row in description]
        if TARGET not in columns or "dataset_split" not in columns or "flat_type" not in columns:
            raise ValueError("transaction_features is missing required target or split columns")
        query_columns = ", ".join('"' + column.replace('"', '""') + '"' for column in columns)
        cursor = connection.execute(f"SELECT {query_columns} FROM transaction_features WHERE dataset_split = 'train'")
        records = [dict(zip(columns, row)) for row in cursor.fetchall()]

    if len(records) < 2:
        raise ValueError("Fewer than two training records are available")
    prices = [float(record[TARGET]) for record in records]
    flat_types = [str(record["flat_type"]) for record in records]
    rows: list[dict] = []
    for name in columns:
        values = [record[name] for record in records]
        non_missing = [value for value in values if value is not None]
        distinct = len(set(non_missing))
        kind, note = field_roles(name, distinct, len(records))
        result = {
            "column": name,
            "data_type": next(row[1] for row in description if row[0] == name),
            "rows": len(records),
            "missing_rows": len(records) - len(non_missing),
            "distinct_values": distinct,
            "association_method": "excluded",
            "pearson_r": None,
            "spearman_rho": None,
            "within_flat_type_pearson_r": None,
            "eta_squared": None,
            "association_strength": "not scored",
            "direction": "n/a",
            "interpretation": note,
        }
        if name == TARGET:
            result["interpretation"] = "Prediction target; self-correlation is not meaningful and this column must be excluded from model inputs."
        elif kind == "identifier":
            result["interpretation"] = note + (f" It has {distinct:,} distinct values in {len(records):,} training rows." if name == "block_id" else "")
        elif kind == "evaluation metadata":
            result["interpretation"] = note
        elif distinct <= 1:
            result["association_method"] = "constant column"
            result["association_strength"] = "no variation"
            observed = "missing only" if not non_missing else f"only value: {non_missing[0]}"
            result["interpretation"] = f"{note} No price association can be estimated because the training split has {observed}."
        elif kind in {"categorical", "flat category"}:
            # High-cardinality categories can appear powerful by memorising groups.
            if distinct > 30:
                result["association_method"] = "high-cardinality identifier-like category"
                result["association_strength"] = "not scored"
                result["interpretation"] = f"{distinct:,} categories. Group association would be prone to memorising sparse categories; use stable, past-only encodings or spatial features instead."
            else:
                categories = ["<missing>" if value is None else str(value) for value in values]
                eta = eta_squared(categories, prices)
                result["association_method"] = "correlation ratio (eta-squared)"
                result["eta_squared"] = round(eta, 6) if eta is not None else None
                result["association_strength"] = strength_for_eta(eta)
                group_counts = Counter(categories)
                smallest = min(group_counts.values())
                largest = max(group_counts.values())
                result["interpretation"] = f"{note} In-sample groups explain {eta:.1%} of raw price variance." if eta is not None else note
                result["interpretation"] += f" Category counts range from {smallest:,} to {largest:,}; small groups need shrinkage or grouping."
        else:
            if name in TIME_COLUMNS:
                numeric = [time_number(value) if isinstance(value, (date, datetime)) else float(value) for value in values]
            elif all(isinstance(value, bool) or value is None for value in values):
                numeric = [float(value) if value is not None else float("nan") for value in values]
            else:
                numeric = [float(value) if value is not None else float("nan") for value in values]
            valid = [(x, y, group) for x, y, group in zip(numeric, prices, flat_types) if math.isfinite(x) and math.isfinite(y)]
            xs, ys, groups = ([item[index] for item in valid] for index in range(3))
            r = pearson(xs, ys)
            rho = pearson(average_ranks(xs), average_ranks(ys)) if len(xs) > 1 else None
            within = within_group_pearson(xs, ys, groups)
            result["association_method"] = "Pearson r and Spearman rho" + (" (binary point-biserial)" if all(value in (0.0, 1.0) for value in set(xs)) else "")
            result["pearson_r"] = round(r, 6) if r is not None else None
            result["spearman_rho"] = round(rho, 6) if rho is not None else None
            result["within_flat_type_pearson_r"] = round(within, 6) if within is not None else None
            chosen = rho if rho is not None else r
            result["association_strength"] = strength_for_correlation(chosen)
            result["direction"] = "positive" if chosen is not None and chosen > 0 else "negative" if chosen is not None and chosen < 0 else "none / unavailable"
            if name in TIME_COLUMNS:
                result["interpretation"] = note + (f" Training-only time trend: Spearman rho {rho:+.3f}. Market-cycle and policy effects may be nonlinear." if rho is not None else note)
            else:
                result["interpretation"] = note + (f" Within-flat-type r={within:+.3f} helps distinguish a pooled relationship from differences between flat types." if within is not None else note)
        rows.append(result)

    # Sort by absolute univariate association for analysis columns, keeping excluded fields at the end.
    def sort_key(row: dict) -> tuple:
        value = row["spearman_rho"] if row["spearman_rho"] is not None else row["pearson_r"]
        if value is not None:
            return (0, -abs(value), row["column"])
        eta = row["eta_squared"]
        return (1 if eta is not None else 2, -(eta or 0), row["column"])

    rows.sort(key=sort_key)
    meta = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "database": str(database),
        "table": "transaction_features",
        "analysis_population": "Training split only; validation and test prices are not used for feature screening.",
        "training_rows": len(records),
        "target": TARGET,
        "columns_profiled": len(columns),
        "method_note": "Pearson measures linear numeric association; Spearman measures monotonic association; eta-squared measures unadjusted group mean association; within-flat-type Pearson demeans numeric values within flat type. None implies causation or independent predictive value.",
    }
    return rows, meta


def render_report(rows: list[dict], meta: dict) -> str:
    lines = [
        "# Feature association with HDB resale price",
        "",
        f"Generated: {meta['generated_at_utc']}",
        f"Source: `{meta['table']}` in `data/property.duckdb`",
        f"Population: {meta['training_rows']:,} training transactions, 2017–2024 based on the current split.",
        f"Columns profiled: {meta['columns_profiled']}",
        "",
        "## How to read this",
        "",
        "Numeric fields use Pearson r (linear relationship with raw price), Spearman rho (monotonic relationship with raw price), and Pearson r after subtracting each flat type's mean from both the feature and price. Categorical fields use eta-squared, the share of raw price variance associated with differences between category means. This is a one-column-at-a-time screen. It does not show causal effects or whether a feature adds value after all other features are controlled.",
        "",
        "Features were screened using training data only. `resale_price` is the target and is not an input. Identifier and evaluation-only columns are not scored. High-cardinality block IDs are not given a group association score because that would reward memorisation.",
        "",
        "## Column-by-column results",
        "",
        "| Column | Type | Method | Pearson r | Spearman rho | Within-flat-type r | Eta-squared | Association | Notes |",
        "|---|---|---|---:|---:|---:|---:|---|---|",
    ]
    for row in rows:
        fmt = lambda value: "—" if value is None else f"{value:.3f}"
        note = row["interpretation"].replace("|", "\\|")
        lines.append(f"| `{row['column']}` | {row['data_type']} | {row['association_method']} | {fmt(row['pearson_r'])} | {fmt(row['spearman_rho'])} | {fmt(row['within_flat_type_pearson_r'])} | {fmt(row['eta_squared'])} | {row['association_strength']} | {note} |")
    lines.extend([
        "",
        "## Interpretation limits",
        "",
        "- Strong raw correlation can come from confounding. For example, larger flats can cost more partly because flat types differ in both area and price.",
        "- A weak pooled correlation can hide a meaningful relationship within each flat type, or vice versa. Compare pooled and within-flat-type results.",
        "- Amenity distances are computed from current facility locations. Some schools, centres, or other amenities may not have existed at older transaction dates.",
        "- The training period is 2017–2024 under the current split. Results can change with data refreshes and split definitions.",
        "- The baseline models already use comparable selection rules; correlation rankings do not replace time-based out-of-sample evaluation.",
        "",
        "## Feature engineering candidates",
        "",
        "1. **Flat-type-normalised area:** `floor_area_sqm` plus interactions by `flat_type`, or size relative to the median for that flat type. Area and flat type should be interpreted together.",
        "2. **Nonlinear lease effect:** remaining-lease bands, a spline, or a squared term. Add the policy-relevant lease bands if supported by adequate sample counts. Avoid retaining redundant lease-start and remaining-lease encodings without regularisation.",
        "3. **Storey relative to block height:** `storey_midpoint / max_floor_lvl`, storey bands, and a top-floor indicator. The source only has 3-storey ranges, so do not imply exact floor precision.",
        "4. **Past-only price trend:** rolling town × flat-type median price per sqm over the previous 3, 6, and 12 months. Exclude the target month and use no later transactions to prevent leakage.",
        "5. **Past-only local price history:** prior block or nearby-area price index with minimum-count rules and shrinkage toward town × flat-type averages. Do not use a block ID alone as a model feature.",
        "6. **Nonlinear access features:** MRT distance bands (for example, within 400 m and 800 m), capped distance transforms, or log-distance. Try access counts and nearest distance separately because they overlap.",
        "7. **Time features:** month-of-year indicators and a past-only market index. Avoid treating calendar month number 1–12 as a continuous numeric scale.",
        "8. **Interaction features:** flat type × area, flat type × lease, storey × block height, and MRT access × flat type. Compare on validation first, preserve the test split for final comparison.",
        "9. **Quality/context data if legally and reliably obtainable:** exact floor, unit/stack, renovation or condition, view/aspect, and building/estate attributes. These may explain substantial variation that this table cannot observe.",
        "",
        "Before adding engineered fields, compare a regularised regression and a tree-based model against both baselines using the same time split. Accept a feature only when it improves validation and final test performance in dollars, not just its correlation with price.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    try:
        rows, meta = analyse(args.project_root.resolve())
        report_dir = args.project_root.resolve() / "reports/phase_1_resale_model"
        report_dir.mkdir(parents=True, exist_ok=True)
        csv_path = report_dir / "05_feature_price_associations.csv"
        md_path = report_dir / "05_feature_price_associations.md"
        with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        md_path.write_text(render_report(rows, meta), encoding="utf-8")
        print(json.dumps({"status": "success", **meta, "csv": str(csv_path), "report": str(md_path)}, indent=2))
        return 0
    except (ValueError, OSError, duckdb.Error) as error:
        print(f"Feature-price analysis failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
