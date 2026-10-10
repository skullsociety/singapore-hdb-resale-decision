"""Streamlit page for exploring HDB resale transactions and feature associations."""

from __future__ import annotations

from datetime import date

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
SCATTER_POINT_LIMIT = 8_000
MAP_POINT_LIMIT = 3_000


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


def transaction_filter(flat_types: list[str], start: date, end: date) -> tuple[str, tuple]:
    placeholders = ",".join("?" for _ in flat_types)
    return (
        f"flat_type IN ({placeholders}) AND transaction_month BETWEEN ? AND ?",
        tuple(flat_types) + (start, end),
    )


def render(query) -> None:
    st.header("Neighbourhood and feature explorer")
    st.caption(
        "Explore registered Singapore HDB resale transactions. Relationships are descriptive associations, "
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
        SELECT MIN(transaction_month) AS first_month,
               MAX(transaction_month) AS last_month
        FROM dashboard_explorer_transactions
    """).iloc[0]
    flat_types = query(
        "SELECT DISTINCT flat_type FROM dashboard_explorer_transactions ORDER BY flat_type"
    )["flat_type"].dropna().tolist()
    first_month = pd.Timestamp(coverage["first_month"]).date()
    last_month = pd.Timestamp(coverage["last_month"]).date()
    first_year = first_month.year
    last_year = last_month.year
    available_years = list(range(first_year, last_year + 1))
    recent_start = max(pd.Timestamp(first_month), pd.Timestamp(last_month) - pd.DateOffset(years=2)).date()
    with st.form("feature_explorer_filters", border=False):
        chosen_types = st.multiselect("Flat type", flat_types, default=flat_types)
        year_columns = st.columns(2)
        start_year = year_columns[0].selectbox("From year", available_years, index=0)
        end_year = year_columns[1].selectbox(
            "To year", available_years, index=len(available_years) - 1
        )
        label = st.selectbox("Feature", list(FEATURE_LABELS.values()))
        chosen_chart_dates = st.date_input(
            "Map and scatter transaction dates",
            value=(recent_start, last_month),
            min_value=first_month,
            max_value=last_month,
        )
        st.form_submit_button("Apply filters", type="primary")
    if start_year > end_year:
        st.warning("The start year must be earlier than or equal to the end year.")
        return
    if not chosen_types:
        st.info("Choose at least one flat type.")
        return
    if isinstance(chosen_chart_dates, (tuple, list)) and len(chosen_chart_dates) == 2:
        chart_start, chart_end = chosen_chart_dates
    else:
        chart_start = chart_end = chosen_chart_dates
    if chart_start > chart_end:
        st.warning("The map and scatter start date must be earlier than or equal to the end date.")
        return

    summary_where, summary_parameters = transaction_filter(
        chosen_types, date(start_year, 1, 1), date(end_year, 12, 31)
    )
    chart_where, chart_parameters = transaction_filter(chosen_types, chart_start, chart_end)
    summary = query(
        f"""
        SELECT COUNT(*)::BIGINT AS transaction_count,
               COUNT(DISTINCT block_id)::BIGINT AS block_count,
               MEDIAN(resale_price) AS median_resale_price_sgd,
               MEDIAN(price_per_sqm) AS median_price_per_sqm
        FROM dashboard_explorer_transactions
        WHERE {summary_where}
        """,
        summary_parameters,
    ).iloc[0]
    if int(summary["transaction_count"]) == 0:
        st.info("No transactions match these filters.")
        return

    metrics = st.columns(4)
    metrics[0].metric("Transactions", f"{int(summary['transaction_count']):,}")
    metrics[1].metric("Blocks", f"{int(summary['block_count']):,}")
    metrics[2].metric("Median price", format_money(summary["median_resale_price_sgd"]))
    metrics[3].metric("Median price per sqm", format_money(summary["median_price_per_sqm"]))

    st.subheader("Price trend")
    monthly = query(
        f"""
        SELECT transaction_month AS Month,
               MEDIAN(resale_price) AS median_resale_price_sgd,
               COUNT(*)::BIGINT AS transaction_count
        FROM dashboard_explorer_transactions
        WHERE {summary_where}
        GROUP BY transaction_month
        ORDER BY transaction_month
        """,
        summary_parameters,
    )
    monthly["Month"] = pd.to_datetime(monthly["Month"])
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
        distribution_rows = query(
            f"""
            SELECT FLOOR(resale_price / {band_size})::BIGINT * {band_size} AS lower_bound,
                   COUNT(*)::BIGINT AS Transactions
            FROM dashboard_explorer_transactions
            WHERE {summary_where}
            GROUP BY lower_bound
            ORDER BY lower_bound
            """,
            summary_parameters,
        )
        lower = int(distribution_rows["lower_bound"].min())
        upper = int(distribution_rows["lower_bound"].max()) + band_size
        bins = list(range(lower, upper + 1, band_size))
        labels = [f"{start / 1_000:,.0f}–{end / 1_000:,.0f}" for start, end in zip(bins, bins[1:])]
        counts = dict(zip(distribution_rows["lower_bound"].astype(int), distribution_rows["Transactions"]))
        distribution = pd.DataFrame({
            "Price band": labels,
            "Transactions": [int(counts.get(start, 0)) for start in bins[:-1]],
        })
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
        blocks = query(
            f"""
            WITH filtered AS (
                SELECT block_id, town, flat_type, COUNT(*)::BIGINT AS transaction_count
                FROM dashboard_explorer_transactions
                WHERE {chart_where}
                GROUP BY block_id, town, flat_type
            )
            , block_points AS (
                SELECT b.block_id, b.latitude, b.longitude,
                       SUM(f.transaction_count)::BIGINT AS transaction_count
                FROM filtered f
                JOIN dashboard_explorer_blocks b USING (block_id, town, flat_type)
                WHERE b.latitude IS NOT NULL AND b.longitude IS NOT NULL
                GROUP BY b.block_id, b.latitude, b.longitude
            )
            SELECT *, COUNT(*) OVER ()::BIGINT AS total_block_count,
                   SUM(transaction_count) OVER ()::BIGINT AS total_transaction_count
            FROM block_points
            ORDER BY transaction_count DESC, block_id
            LIMIT {MAP_POINT_LIMIT}
            """,
            chart_parameters,
        )
        if blocks.empty:
            st.info("No mapped blocks match the chart and map dates.")
        else:
            st.map(blocks, latitude="latitude", longitude="longitude", size="transaction_count")
            total_blocks = int(blocks["total_block_count"].iloc[0])
            total_transactions = int(blocks["total_transaction_count"].iloc[0])
            display_note = (
                f" Showing the {len(blocks):,} most active blocks out of {total_blocks:,}."
                if total_blocks > len(blocks) else ""
            )
            st.caption(
                f"{total_transactions:,} transactions from {chart_start:%b %Y} to {chart_end:%b %Y}; "
                f"larger points represent more registered sales.{display_note}"
            )

    st.subheader("Explore one feature")
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
    scatter = query(
        f"""
        WITH filtered AS (
            SELECT {feature}, resale_price, flat_type, transaction_month, block_id, transaction_id
            FROM dashboard_explorer_transactions
            WHERE {chart_where} AND {feature} IS NOT NULL
        )
        SELECT {feature}, resale_price, flat_type, transaction_month, block_id,
               MIN({feature}) OVER () AS feature_min,
               MAX({feature}) OVER () AS feature_max
        FROM filtered
        ORDER BY HASH(transaction_id)
        LIMIT {SCATTER_POINT_LIMIT}
        """,
        chart_parameters,
    )
    if scatter.empty:
        st.info("No feature values match the chart and map dates.")
        return
    feature_domain = padded_numeric_domain(pd.Series([
        scatter["feature_min"].iloc[0], scatter["feature_max"].iloc[0]
    ]))
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
    average_by_feature = query(
        f"""
        WITH filtered AS (
            SELECT {feature} AS feature_value, resale_price
            FROM dashboard_explorer_transactions
            WHERE {chart_where} AND {feature} IS NOT NULL
        ), bounds AS (
            SELECT MIN(feature_value) AS minimum, MAX(feature_value) AS maximum
            FROM filtered
        ), binned AS (
            SELECT feature_value, resale_price,
                   CASE WHEN maximum = minimum THEN 0 ELSE
                       LEAST(49, FLOOR((feature_value - minimum) / (maximum - minimum) * 50)::INTEGER)
                   END AS feature_bin
            FROM filtered CROSS JOIN bounds
        )
        SELECT AVG(feature_value) AS {feature},
               AVG(resale_price) AS average_resale_price,
               COUNT(*)::BIGINT AS transaction_count
        FROM binned
        GROUP BY feature_bin
        ORDER BY feature_bin
        """,
        chart_parameters,
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
        f"Faint coloured points are a deterministic display sample of up to {SCATTER_POINT_LIMIT:,} "
        f"transactions from {chart_start:%b %Y} to {chart_end:%b %Y}. The orange line uses all matching "
        "transactions, grouped into up to 50 feature bands; it is not a price forecast."
    )
    if feature == "floor_area_sqm":
        st.subheader("Average cost per sqm by flat type")
        cost_per_sqm = query(
            f"""
            SELECT flat_type AS "Flat type",
                   AVG(price_per_sqm) AS "Average cost per sqm",
                   COUNT(*)::BIGINT AS "Transactions"
            FROM dashboard_explorer_transactions
            WHERE {summary_where}
            GROUP BY flat_type
            ORDER BY flat_type
            """,
            summary_parameters,
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
    display = query(
        f"""
        SELECT transaction_month, block_id, flat_type, flat_model, storey_range,
               floor_area_sqm, remaining_lease_years, resale_price, price_per_sqm
        FROM dashboard_explorer_transactions
        WHERE {summary_where}
        ORDER BY transaction_month DESC, transaction_id
        LIMIT 500
        """,
        summary_parameters,
    )
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
    if int(summary["transaction_count"]) > 500:
        st.caption("The table shows the latest 500 matching transactions; charts use the full selection.")
