"""Local-first stakeholder dashboard for the HDB resale decision project."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import altair as alt
import duckdb
import streamlit as st


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parents[1]
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "listings.db"
DATABASE = Path(os.environ.get("PROPERTY_DASHBOARD_DB", str(DEFAULT_DATABASE))).resolve()


def load_report_module():
    path = APP_DIR / "25_agent_report.py"
    spec = importlib.util.spec_from_file_location("phase5_agent_report", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load report generator: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REPORT = load_report_module()


def load_feature_explorer_module():
    path = PROJECT_ROOT / "dashboard" / "phase_6" / "27_feature_explorer.py"
    spec = importlib.util.spec_from_file_location("phase6_feature_explorer", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load feature explorer: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FEATURE_EXPLORER = load_feature_explorer_module()


def load_future_scenario_module():
    path = PROJECT_ROOT / "dashboard" / "phase_7" / "29_future_scenario_explorer.py"
    spec = importlib.util.spec_from_file_location("phase7_future_scenarios", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load future scenario explorer: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FUTURE_SCENARIOS = load_future_scenario_module()


def load_decision_planner_module():
    path = APP_DIR / "24_decision_planner.py"
    spec = importlib.util.spec_from_file_location("phase5_decision_planner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DECISION_PLANNER = load_decision_planner_module()


@st.cache_data(ttl=60, show_spinner=False)
def query(sql: str, parameters: tuple = ()):
    with duckdb.connect(str(DATABASE), read_only=True) as connection:
        return connection.execute(sql, list(parameters)).fetchdf()


def records(sql: str, parameters: tuple = ()) -> list[dict]:
    return query(sql, parameters).to_dict("records")


def money(value) -> str:
    return "—" if value is None else f"S${float(value):,.0f}"


def require_database() -> None:
    if not DATABASE.is_file():
        st.error(f"Dashboard database not found: {DATABASE}")
        st.info("Run Step 23 locally, or set PROPERTY_DASHBOARD_DB to an approved dashboard database.")
        st.stop()
    try:
        names = {row["table_name"] for row in records(
            "SELECT table_name FROM information_schema.views WHERE table_name LIKE 'dashboard_%'"
        )}
    except duckdb.Error as error:
        st.error(f"Cannot read the dashboard database: {error}")
        st.stop()
    if "dashboard_current_listings" not in names:
        st.error("Dashboard views are missing. Run Step 23 before opening the dashboard.")
        st.stop()


def apply_filters(frame):
    towns = sorted(frame["town"].dropna().unique().tolist())
    categories = sorted(frame["stakeholder_category"].dropna().unique().tolist())
    flat_types = sorted(frame["inferred_flat_type"].dropna().unique().tolist())
    selected_towns = st.multiselect("Town", towns, default=towns)
    selected_categories = st.multiselect("Category", categories, default=categories)
    selected_types = st.multiselect("Flat type", flat_types, default=flat_types)
    maximum = int(frame["asking_price_sgd"].max()) if not frame.empty else 0
    budget = st.number_input(
        "Maximum asking price (S$); 0 means no limit",
        min_value=0, max_value=max(maximum, 0), value=0, step=10000,
    )
    result = frame[
        frame["town"].isin(selected_towns)
        & frame["stakeholder_category"].isin(selected_categories)
        & frame["inferred_flat_type"].isin(selected_types)
    ]
    return result if budget == 0 else result[result["asking_price_sgd"] <= budget]


def overview_page() -> None:
    st.header("Market overview")
    rows = query("SELECT * FROM dashboard_current_listings")
    counts = rows["stakeholder_category"].value_counts()
    columns = st.columns(4)
    columns[0].metric("Current listings", f"{len(rows):,}")
    columns[1].metric("Supported estimates", f"{int((rows['valuation_status'] == 'estimated').sum()):,}")
    columns[2].metric("Strong candidates", f"{int(counts.get('strong_candidate', 0)):,}")
    columns[3].metric("Needs more evidence", f"{int(counts.get('insufficient_evidence', 0)):,}")
    st.caption("Current scope is the available pilot data. Categories are screening aids, not confirmed bargains.")
    chart = counts.rename_axis("Category").reset_index(name="Listings")
    chart["Category"] = chart["Category"].str.replace("_", " ").str.title()
    category_chart = (
        alt.Chart(chart)
        .mark_bar()
        .encode(
            y=alt.Y(
                "Category:N",
                sort="-x",
                title=None,
                axis=alt.Axis(labelLimit=240),
            ),
            x=alt.X("Listings:Q", title="Number of listings"),
            tooltip=[alt.Tooltip("Category:N"), alt.Tooltip("Listings:Q", format=",")],
        )
        .properties(height=max(220, len(chart) * 42))
    )
    st.altair_chart(category_chart)

    rules = records("SELECT * FROM dashboard_category_rules LIMIT 1")[0]
    margin = rules["negotiation_margin_above_upper_pct"]
    st.markdown("**What each category means**")
    st.table([
        {"Category": "Strong candidate", "Definition": "Supported estimate, matches the active profile, and asking price is at or below the point estimate."},
        {"Category": "Fairly priced", "Definition": "Asking price is above the point estimate but remains within the estimated price range."},
        {"Category": "Negotiation candidate", "Definition": f"Asking price is above the estimated range, but no more than {margin:.0f}% above its upper end."},
        {"Category": "Likely expensive", "Definition": f"Asking price is more than {margin:.0f}% above the upper end of the estimated range."},
        {"Category": "Insufficient evidence", "Definition": "No supported estimate is available, or there are too few comparable transactions."},
        {"Category": "Does not match", "Definition": "An estimate exists, but the listing does not meet the active search profile."},
    ])

    st.subheader("Listings by flat type")
    st.caption(
        "One row for each inferred HDB flat type in the current listing snapshot. "
        "The result cards do not consistently state flat type, so it is inferred from floor area."
    )
    summary = query("SELECT * FROM dashboard_market_overview ORDER BY town, flat_type")
    summary["latest_observation_utc"] = summary["latest_observation_utc"].astype(str).str[:10]
    summary = summary[[
        "flat_type", "listing_count", "valued_count", "strong_candidate_count",
        "fairly_priced_count", "negotiation_candidate_count", "likely_expensive_count",
        "insufficient_evidence_count", "median_asking_price_sgd",
        "median_premium_discount_pct", "latest_observation_utc",
    ]].rename(columns={
        "flat_type": "Flat type (inferred)",
        "listing_count": "Listings",
        "valued_count": "With estimate",
        "strong_candidate_count": "Strong candidates",
        "fairly_priced_count": "Fairly priced",
        "negotiation_candidate_count": "Negotiation candidates",
        "likely_expensive_count": "Likely expensive",
        "insufficient_evidence_count": "Insufficient evidence",
        "median_asking_price_sgd": "Median asking price",
        "median_premium_discount_pct": "Median difference (%)",
        "latest_observation_utc": "Last observed",
    })
    st.dataframe(
        summary, hide_index=True, width="stretch",
        column_config={
            "Median asking price": st.column_config.NumberColumn(format="S$ %,.0f"),
            "Median difference (%)": st.column_config.NumberColumn(format="%.1f%%"),
        },
    )


def opportunity_page() -> None:
    st.header("Opportunity finder")
    frame = query("SELECT * FROM dashboard_current_listings ORDER BY ranking_score DESC NULLS LAST")
    selected = apply_filters(frame)
    st.caption(f"Showing {len(selected):,} of {len(frame):,} listings")
    columns = [
        "stakeholder_category", "town", "title", "inferred_flat_type", "floor_area_sqm",
        "asking_price_sgd", "lower_estimate_sgd", "point_estimate_sgd", "upper_estimate_sgd",
        "asking_premium_discount_pct", "minimum_comparable_count", "confidence_label",
        "change_type", "listing_url",
    ]
    st.dataframe(selected[columns], hide_index=True, width="stretch", column_config={
        "listing_url": st.column_config.LinkColumn("Original listing", display_text="Open"),
        "asking_price_sgd": st.column_config.NumberColumn("Asking price", format="S$ %,.0f"),
        "lower_estimate_sgd": st.column_config.NumberColumn("Estimate low", format="S$ %,.0f"),
        "point_estimate_sgd": st.column_config.NumberColumn("Estimate point", format="S$ %,.0f"),
        "upper_estimate_sgd": st.column_config.NumberColumn("Estimate high", format="S$ %,.0f"),
        "asking_premium_discount_pct": st.column_config.NumberColumn("Difference", format="%.1f%%"),
    })


def listing_choices() -> dict[str, dict]:
    rows = records("SELECT * FROM dashboard_listing_details ORDER BY title, listing_id")
    return {
        (
            f"{row.get('title') or row['listing_id']} · {money(row.get('asking_price_sgd'))} · "
            f"{row['source_site']}/{row['listing_id']}"
        ): row
        for row in rows
    }


def listing_detail_page() -> None:
    st.header("Listing details")
    choices = listing_choices()
    label = st.selectbox("Choose a listing", list(choices))
    listing = choices[label]
    with st.container(horizontal=True):
        st.metric("Asking price", money(listing.get("asking_price_sgd")), border=True)
        st.metric("Lower estimate", money(listing.get("lower_estimate_sgd")), border=True)
        st.metric("Point estimate", money(listing.get("point_estimate_sgd")), border=True)
        st.metric("Upper estimate", money(listing.get("upper_estimate_sgd")), border=True)
        st.metric(
            "Difference from point estimate",
            "—" if listing.get("asking_premium_discount_pct") is None
            else f"{listing['asking_premium_discount_pct']:+.1f}%",
            border=True,
        )
    st.subheader(str(listing.get("stakeholder_category", "")).replace("_", " ").title())
    st.write(listing.get("category_reason") or "")
    if listing.get("verify_low_price"):
        st.warning("The asking price is unusually far below the research range. Verify the listing and unit details.")
    st.write({
        "Address": listing.get("address"),
        "Inferred flat type": listing.get("inferred_flat_type"),
        "Floor area": listing.get("floor_area_sqm"),
        "Confidence": listing.get("confidence_label"),
        "Minimum comparable count": listing.get("minimum_comparable_count"),
        "Valuation note": listing.get("valuation_note"),
    })
    st.subheader("Nearby amenities")
    amenities = {
        "MRT/LRT": listing.get("nearest_mrt_lrt_stations_m"),
        "Primary school": listing.get("nearest_primary_schools_m"),
        "Childcare": listing.get("nearest_childcare_centres_m"),
        "Clinic": listing.get("nearest_healthcare_clinics_m"),
        "Park": listing.get("nearest_parks_m"),
        "Hawker centre": listing.get("nearest_hawker_centres_m"),
    }
    st.dataframe(
        [{"Amenity": key, "Nearest distance (m)": value} for key, value in amenities.items()],
        hide_index=True, width="stretch",
    )
    if listing.get("latitude") is not None and listing.get("longitude") is not None:
        st.map({"lat": [listing["latitude"]], "lon": [listing["longitude"]]})
    st.subheader("Supporting comparable sales")
    st.write(
        "These are earlier registered resale transactions used as evidence for the comparable-sales "
        "part of the estimate. The accepted estimate combines 45% Ridge model and 55% comparable-sales evidence."
    )
    with st.expander("How comparable sales are selected"):
        st.markdown("""
- Only sales before the listing valuation month are considered, normally from the previous 24 months.
- Every comparison uses the same town and flat type.
- The search first tries the same block, flat model, a floor-area difference within 5 sqm, and a storey-midpoint difference within three floors.
- If there are too few sales, it widens in stages: within 1 km, then the same town with broader area and storey tolerances.
- Candidates are ranked using geographic distance, floor-area difference, storey difference, and recency. Lower combined difference ranks first.
- Up to five of the closest matches are displayed for each assumed low, middle, or high storey scenario.

The listing cards do not reliably provide exact storey, flat model, or lease commencement year. The valuation therefore tests observed same-block storey scenarios and records those assumptions.
        """)
    comps = query(
        "SELECT * FROM dashboard_comparables WHERE source_site = ? AND run_id = ? AND listing_id = ? ORDER BY sale_month DESC, distance_m",
        (listing["source_site"], listing["run_id"], listing["listing_id"]),
    )
    comps["sale_month"] = comps["sale_month"].astype(str).str[:7]
    comparable_display = comps[[
        "scenario", "assumed_storey_range", "sale_month", "comparable_block",
        "comparable_street", "sale_price_sgd", "floor_area_sqm", "storey_range", "distance_m",
    ]].rename(columns={
        "scenario": "Scenario",
        "assumed_storey_range": "Assumed listing storey",
        "sale_month": "Sale month",
        "comparable_block": "Block",
        "comparable_street": "Street",
        "sale_price_sgd": "Sale price",
        "floor_area_sqm": "Floor area (sqm)",
        "storey_range": "Comparable storey",
        "distance_m": "Distance (m)",
    })
    st.dataframe(
        comparable_display, hide_index=True, width="stretch",
        column_config={
            "Sale price": st.column_config.NumberColumn(format="S$ %,.0f"),
            "Floor area (sqm)": st.column_config.NumberColumn(format="%.1f"),
            "Distance (m)": st.column_config.NumberColumn(format="%,.0f"),
        },
    )
    st.link_button("Open original listing", listing["listing_url"])


def changes_page() -> None:
    st.header("Market changes")
    runs = query("SELECT * FROM listing_snapshot_runs ORDER BY source_site, snapshot_sequence")
    st.dataframe(runs, hide_index=True, width="stretch")
    if len(runs) < 2:
        st.info("One complete snapshot is available. Collect another complete snapshot to display real market changes.")
    changes = query("SELECT * FROM dashboard_listing_changes ORDER BY snapshot_sequence DESC, change_type, title")
    types = sorted(changes["change_type"].dropna().unique())
    selected = st.multiselect("Change type", types, default=types)
    st.dataframe(changes[changes["change_type"].isin(selected)], hide_index=True, width="stretch")


def quality_page() -> None:
    st.header("Data and model quality")
    st.subheader("Current data checks")
    checks = query("SELECT * FROM dashboard_data_quality")
    check_labels = {
        "current_listing_count": ("Current listings", "Number of listings in the latest complete collection."),
        "block_match_coverage": ("Block match coverage", "Share of listings matched to a known HDB block, which enables location features."),
        "valuation_coverage": ("Valuation coverage", "Share of listings with a supportable price estimate."),
        "listing_url_coverage": ("Listing link coverage", "Share of listings with an original source link for verification."),
        "floor_area_coverage": ("Floor area coverage", "Share of listings with floor area, which is needed for comparison and valuation."),
        "unusually_low_price_flags": ("Unusually low price flags", "Listings whose asking price is far below the research range and needs manual verification."),
        "complete_snapshot_count": ("Complete snapshots", "Number of full listing collections available for measuring market changes over time."),
    }
    checks["Check"] = checks["check_name"].map(lambda value: check_labels[value][0])
    checks["What it checks"] = checks["check_name"].map(lambda value: check_labels[value][1])
    checks["Status"] = checks["status"].str.title()
    checks["Observed value"] = checks.apply(
        lambda row: f"{row['observed_value']:.1f}%" if row["unit"] == "percent"
        else f"{row['observed_value']:,.0f}",
        axis=1,
    )
    st.dataframe(
        checks[["Check", "Observed value", "Status", "What it checks"]],
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "Pass means the current threshold is met. Review means the data needs attention. "
        "Informational is a count with no pass threshold. Pending means the check cannot yet be judged; "
        "two complete snapshots are needed before real listing changes can be measured."
    )
    st.subheader("Model evaluation")
    st.dataframe(
        query("SELECT * FROM dashboard_model_performance ORDER BY model_id, dataset_split"),
        hide_index=True, width="stretch",
    )
    st.caption("Historical held-out performance does not guarantee the accuracy of one listing estimate.")


def report_page() -> None:
    st.header("Create a comparison report")
    choices = listing_choices()
    selected_labels = st.multiselect("Select one to four listings", list(choices), max_selections=4)
    report_reference = st.text_input("Report reference (optional)")
    notes = st.text_area("Comparison notes (optional)")
    upload = st.file_uploader("Attach a prior buyer or seller plan JSON (optional)", type=["json"])
    if not selected_labels:
        st.info("Choose at least one listing to prepare a report.")
        return
    selected = [choices[label] for label in selected_labels]
    where = " OR ".join(
        "(source_site = ? AND run_id = ? AND listing_id = ?)" for _ in selected
    )
    parameters = tuple(
        value
        for row in selected
        for value in (row["source_site"], row["run_id"], row["listing_id"])
    )
    comp_rows = records(
        f"SELECT * FROM dashboard_comparables WHERE {where} "
        "ORDER BY source_site, run_id, listing_id, sale_month DESC, distance_m",
        parameters,
    )
    grouped: dict[str, list[dict]] = {}
    for row in comp_rows:
        key = REPORT.listing_key(row)
        grouped.setdefault(key, []).append(row)
    try:
        plan = REPORT.parse_planning_upload(upload.getvalue() if upload else None)
        if plan is None:
            plan = st.session_state.get("seller_plan") or st.session_state.get("buyer_plan")
        document = REPORT.build_report(
            selected, grouped, report_reference=report_reference,
            comparison_notes=notes, planning_result=plan,
        )
        st.download_button(
            "Download printable report", document,
            file_name="hdb_comparison_report.html", mime="text/html",
        )
        st.caption("The report is created in memory and saved only when you click Download.")
    except ValueError as error:
        st.error(str(error))


st.set_page_config(page_title="HDB market dashboard", layout="wide")
require_database()
st.title("Singapore HDB resale decision dashboard")
page = st.sidebar.radio(
    "Page",
    [
        "Market overview", "Opportunity finder", "Listing details",
        "Neighbourhood and features", "Market changes",
        "Future-price scenarios", "Buy and sell planning", "Data and model quality", "Comparison report",
    ],
)
st.sidebar.caption(f"Database: {DATABASE.name}")
{
    "Market overview": overview_page,
    "Opportunity finder": opportunity_page,
    "Listing details": listing_detail_page,
    "Neighbourhood and features": lambda: FEATURE_EXPLORER.render(query),
    "Future-price scenarios": lambda: FUTURE_SCENARIOS.render(query),
    "Buy and sell planning": DECISION_PLANNER.render,
    "Market changes": changes_page,
    "Data and model quality": quality_page,
    "Comparison report": report_page,
}[page]()
