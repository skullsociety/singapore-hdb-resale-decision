"""Streamlit page for exploring HDB resale transactions and feature associations."""

from __future__ import annotations

import math

import altair as alt
import pandas as pd
import streamlit as st


FEATURE_LABELS = {
    "floor_area_sqm": "Floor area (sqm)",
    "storey_midpoint": "Storey midpoint",
    "remaining_lease_years": "Remaining lease (years)",
    "building_age_at_transaction": "Building age at sale (years)",
    "nearest_mrt_lrt_stations_m": "Distance to MRT/LRT (m)",
    "nearest_primary_schools_m": "Distance to primary school (m)",
    "nearest_childcare_centres_m": "Distance to childcare (m)",
    "nearest_healthcare_clinics_m": "Distance to clinic (m)",
    "nearest_polyclinics_m": "Distance to polyclinic (m)",
    "nearest_hospitals_m": "Distance to hospital (m)",
    "nearest_community_clubs_m": "Distance to community club (m)",
    "nearest_hawker_centres_m": "Distance to hawker centre (m)",
    "nearest_parks_m": "Distance to park (m)",
    "nearest_public_libraries_m": "Distance to public library (m)",
}


def format_money(value: float) -> str:
    return f"S${float(value):,.0f}"


def relationship_value(row: dict, key: str) -> str:
    value = row.get(key)
    return "Not measured" if value is None or pd.isna(value) else f"{float(value):+.3f}"


def padded_numeric_domain(values: pd.Series) -> list[float]:
    """Return a data-driven chart domain with a small visible edge buffer."""
    minimum = float(values.min())
    maximum = float(values.max())
    span = maximum - minimum
    padding = max(span * 0.04, abs(maximum) * 0.01, 1.0)
    return [minimum - padding, maximum + padding]


def render(query) -> None:
    st.header("Neighbourhood and feature explorer")
    st.caption(
        "Explore registered Sengkang resale transactions. Relationships are descriptive associations, "
        "not proof that a feature caused a price difference."
    )
    available = set(query(
        "SELECT table_name FROM information_schema.views WHERE table_name LIKE 'dashboard_explorer_%' "
        "OR table_name = 'dashboard_feature_associations'"
    )["table_name"].tolist())
    required = {
        "dashboard_explorer_transactions", "dashboard_explorer_monthly_trends",
        "dashboard_explorer_blocks", "dashboard_feature_associations",
    }
    if not required <= available:
        st.error("The feature-explorer data is missing. Run Step 26, then reload this page.")
        return

    coverage = query("""
        SELECT MIN(EXTRACT(year FROM transaction_month))::INTEGER AS first_year,
               MAX(EXTRACT(year FROM transaction_month))::INTEGER AS last_year
        FROM dashboard_explorer_transactions
    """).iloc[0]
    flat_types = query(
        "SELECT DISTINCT flat_type FROM dashboard_explorer_transactions ORDER BY flat_type"
    )["flat_type"].dropna().tolist()
    first_year = int(coverage["first_year"])
    last_year = int(coverage["last_year"])
    available_years = list(range(first_year, last_year + 1))
    with st.form("feature_explorer_filters", border=False):
        chosen_types = st.multiselect("Flat type", flat_types, default=flat_types)
        year_columns = st.columns(2)
        start_year = year_columns[0].selectbox("From year", available_years, index=0)
        end_year = year_columns[1].selectbox(
            "To year", available_years, index=len(available_years) - 1
        )
        st.form_submit_button("Apply filters", type="primary")
    if start_year > end_year:
        st.warning("The start year must be earlier than or equal to the end year.")
        return
    chosen_years = (start_year, end_year)
    if not chosen_types:
        st.info("Choose at least one flat type.")
        return

    placeholders = ",".join("?" for _ in chosen_types)
    parameters = tuple(chosen_types) + tuple(chosen_years)
    transactions = query(
        f"""
        SELECT * FROM dashboard_explorer_transactions
        WHERE flat_type IN ({placeholders})
          AND EXTRACT(year FROM transaction_month) BETWEEN ? AND ?
        ORDER BY transaction_month
        """,
        parameters,
    )
    if transactions.empty:
        st.info("No transactions match these filters.")
        return

    metrics = st.columns(4)
    metrics[0].metric("Transactions", f"{len(transactions):,}")
    metrics[1].metric("Blocks", f"{transactions['block_id'].nunique():,}")
    metrics[2].metric("Median price", format_money(transactions["resale_price"].median()))
    metrics[3].metric("Median price per sqm", format_money(transactions["price_per_sqm"].median()))

    st.subheader("Price trend")
    monthly = (
        transactions.groupby("transaction_month", as_index=False)
        .agg(median_resale_price_sgd=("resale_price", "median"), transaction_count=("transaction_id", "count"))
    )
    monthly["Month"] = pd.to_datetime(monthly["transaction_month"])
    trend_chart = (
        alt.Chart(monthly)
        .mark_line(point=True)
        .encode(
            x=alt.X("Month:T", title="Transaction month", axis=alt.Axis(format="%b %Y")),
            y=alt.Y("median_resale_price_sgd:Q", title="Median resale price (S$)", scale=alt.Scale(zero=False)),
            tooltip=[
                alt.Tooltip("Month:T", title="Month", format="%b %Y"),
                alt.Tooltip("median_resale_price_sgd:Q", title="Median price", format="$,.0f"),
                alt.Tooltip("transaction_count:Q", title="Transactions", format=","),
            ],
        )
    )
    st.altair_chart(trend_chart)
    st.caption("Monthly median for the selected comparison group; small months can be volatile.")

    left, right = st.columns(2)
    with left:
        st.subheader("Price distribution")
        band_size = 100_000
        prices = transactions["resale_price"]
        lower = math.floor(float(prices.min()) / band_size) * band_size
        upper = math.ceil(float(prices.max()) / band_size) * band_size + band_size
        bins = list(range(lower, upper + 1, band_size))
        labels = [f"{start / 1_000:,.0f}–{end / 1_000:,.0f}" for start, end in zip(bins, bins[1:])]
        bands = pd.cut(prices, bins=bins, labels=labels, right=False)
        distribution = bands.value_counts(sort=False).rename_axis("Price band").reset_index(name="Transactions")
        distribution["Price band"] = distribution["Price band"].astype(str)
        bars = (
            alt.Chart(distribution)
            .mark_bar()
            .encode(
                x=alt.X("Price band:N", sort=labels, title="Resale price band (S$'000)", axis=alt.Axis(labelAngle=-35)),
                y=alt.Y(
                    "Transactions:Q",
                    title="Transactions",
                    scale=alt.Scale(
                        domain=[0, max(1, float(distribution["Transactions"].max()) * 1.12)]
                    ),
                ),
                tooltip=[alt.Tooltip("Price band:N"), alt.Tooltip("Transactions:Q", format=",")],
            )
        )
        values = bars.mark_text(dy=-8, fontSize=12).encode(
            text=alt.Text("Transactions:Q", format=",")
        )
        st.altair_chart((bars + values).properties(padding={"top": 18}))
        st.caption(
            "Each band covers SGD 100,000. For example, 1,000–1,100 means "
            "SGD 1.0m to under SGD 1.1m."
        )
    with right:
        st.subheader("Blocks with registered sales")
        transaction_dates = pd.to_datetime(transactions["transaction_month"])
        map_minimum = transaction_dates.min().date()
        map_maximum = transaction_dates.max().date()
        map_default_start = max(
            pd.Timestamp(map_minimum), pd.Timestamp(map_maximum) - pd.DateOffset(years=2)
        ).date()
        chosen_map_dates = st.date_input(
            "Map transaction dates",
            value=(map_default_start, map_maximum),
            min_value=map_minimum,
            max_value=map_maximum,
            key="feature_explorer_map_dates",
        )
        if isinstance(chosen_map_dates, (tuple, list)) and len(chosen_map_dates) == 2:
            map_start, map_end = chosen_map_dates
        else:
            map_start = map_end = chosen_map_dates
        map_transactions = transactions[
            transaction_dates.dt.date.between(map_start, map_end)
        ]
        blocks = (
            map_transactions.dropna(subset=["latitude", "longitude"])
            .groupby(["block_id", "latitude", "longitude"], as_index=False)
            .agg(transaction_count=("transaction_id", "count"))
        )
        st.map(blocks, latitude="latitude", longitude="longitude", size="transaction_count")
        st.caption(
            f"{len(map_transactions):,} transactions from {map_start:%b %Y} to {map_end:%b %Y}; "
            "larger points represent more registered sales."
        )

    st.subheader("Explore one feature")
    label = st.selectbox("Feature", list(FEATURE_LABELS.values()))
    feature = next(key for key, value in FEATURE_LABELS.items() if value == label)
    association_column = "remaining_lease_months" if feature == "remaining_lease_years" else feature
    association_rows = query(
        "SELECT * FROM dashboard_feature_associations WHERE column_name = ?",
        (association_column,),
    ).to_dict("records")
    if association_rows:
        association = association_rows[0]
        association_metrics = st.columns(3)
        association_metrics[0].metric("Pooled Spearman", relationship_value(association, "spearman_rho"))
        association_metrics[1].metric(
            "Within flat type Pearson",
            relationship_value(association, "within_flat_type_pearson_r"),
        )
        association_metrics[2].metric(
            "Association", str(association.get("association_strength") or "Not scored")
        )
        st.write(association.get("interpretation") or "")
        st.caption(
            "A positive number means higher feature values tended to accompany higher prices; "
            "a negative number means they tended to accompany lower prices. It does not isolate causation."
        )
    scatter = transactions[[feature, "resale_price", "flat_type", "transaction_month", "block_id"]].dropna()
    feature_domain = padded_numeric_domain(scatter[feature])
    transaction_points = (
        alt.Chart(scatter)
        .mark_circle(opacity=0.32, size=32)
        .encode(
            x=alt.X(
                f"{feature}:Q",
                title=FEATURE_LABELS[feature],
                scale=alt.Scale(domain=feature_domain),
            ),
            y=alt.Y("resale_price:Q", title="Resale price (S$)", scale=alt.Scale(zero=False)),
            color=alt.Color("flat_type:N", title="Flat type", legend=None),
            tooltip=[
                alt.Tooltip("block_id:N", title="Block ID"),
                alt.Tooltip("flat_type:N", title="Flat type"),
                alt.Tooltip("transaction_month:T", title="Sale month", format="%b %Y"),
                alt.Tooltip(f"{feature}:Q", title=FEATURE_LABELS[feature], format=",.1f"),
                alt.Tooltip("resale_price:Q", title="Resale price", format="$,.0f"),
            ],
        )
    )
    average_by_feature = (
        scatter.groupby(feature, as_index=False)
        .agg(
            average_resale_price=("resale_price", "mean"),
            transaction_count=("resale_price", "size"),
        )
        .sort_values(feature)
    )
    average_line = (
        alt.Chart(average_by_feature)
        .mark_line(color="#f0a202", size=3, point=alt.OverlayMarkDef(size=60, filled=True))
        .encode(
            x=alt.X(
                f"{feature}:Q",
                title=FEATURE_LABELS[feature],
                scale=alt.Scale(domain=feature_domain),
            ),
            y=alt.Y(
                "average_resale_price:Q",
                title="Resale price (S$)",
                scale=alt.Scale(zero=False),
            ),
            tooltip=[
                alt.Tooltip(f"{feature}:Q", title=FEATURE_LABELS[feature], format=",.1f"),
                alt.Tooltip("average_resale_price:Q", title="Average resale price", format="$,.0f"),
                alt.Tooltip("transaction_count:Q", title="Transactions", format=","),
            ],
        )
    )
    st.altair_chart(transaction_points + average_line)
    st.caption(
        "Faint coloured points are individual resale transactions. The orange points and connecting line show "
        "the average resale price for each exact feature value in the selected group; the line is not a price forecast."
    )
    if feature == "floor_area_sqm":
        st.subheader("Average cost per sqm by flat type")
        cost_per_sqm = (
            transactions.groupby("flat_type", as_index=False)
            .agg(
                average_cost_per_sqm=("price_per_sqm", "mean"),
                transaction_count=("transaction_id", "count"),
            )
            .sort_values("flat_type")
            .rename(columns={
                "flat_type": "Flat type",
                "average_cost_per_sqm": "Average cost per sqm",
                "transaction_count": "Transactions",
            })
        )
        st.dataframe(
            cost_per_sqm,
            hide_index=True,
            width="stretch",
            column_config={
                "Average cost per sqm": st.column_config.NumberColumn(format="S$ %,.0f"),
                "Transactions": st.column_config.NumberColumn(format="%,d"),
            },
        )
        st.caption(
            "Average cost per sqm is the average of each transaction's sale price per square metre for that flat type."
        )

    st.subheader("Selected comparison group")
    display = transactions.sort_values("transaction_month", ascending=False).head(500)
    display = display.assign(
        transaction_month=pd.to_datetime(display["transaction_month"]).dt.strftime("%b %Y")
    )
    display = display[[
        "transaction_month", "block_id", "flat_type", "flat_model", "storey_range",
        "floor_area_sqm", "remaining_lease_years", "resale_price", "price_per_sqm",
    ]].rename(columns={
        "transaction_month": "Transaction month",
        "block_id": "Block ID",
        "flat_type": "Flat type",
        "flat_model": "Flat model",
        "storey_range": "Storey range",
        "floor_area_sqm": "Floor area (sqm)",
        "remaining_lease_years": "Remaining lease (years)",
        "resale_price": "Resale price",
        "price_per_sqm": "Price per sqm",
    })
    st.dataframe(
        display,
        hide_index=True,
        width="stretch",
        column_config={
            "Resale price": st.column_config.NumberColumn(format="S$ %,.0f"),
            "Price per sqm": st.column_config.NumberColumn(format="S$ %,.0f"),
            "Floor area (sqm)": st.column_config.NumberColumn(format="%.1f"),
            "Remaining lease (years)": st.column_config.NumberColumn(format="%.1f"),
        },
    )
    if len(transactions) > 500:
        st.caption("The table shows the latest 500 matching transactions; charts use the full selection.")
