"""Interactive, assumption-led future-price scenarios with historical backtest context."""

from __future__ import annotations

import math

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st


DEFAULT_SCENARIOS = {
    "Conservative": {"market_growth": -0.02},
    "Baseline": {"market_growth": 0.02},
    "Optimistic": {"market_growth": 0.04},
}

SCENARIO_VALUE_COLUMNS = [
    "Scenario", "Central estimate", "Lower range", "Upper range", "Total change",
]
BACKTEST_DISPLAY_COLUMNS = [
    "Horizon", "calibration_pair_count", "test_pair_count", "test_mae_sgd",
    "test_mape", "test_range_coverage", "mean_test_range_width_sgd",
]


def monthly_payment(principal: float, annual_rate: float, years: int) -> float:
    if principal <= 0 or years <= 0:
        return 0.0
    months = years * 12
    rate = annual_rate / 12
    if rate == 0:
        return principal / months
    return principal * rate * (1 + rate) ** months / ((1 + rate) ** months - 1)


def scenario_paths(
    start_value: float,
    horizon: int,
    scenarios: dict[str, dict[str, float]],
    lease_effect: float,
    flat_adjustment: float,
    uncertainty: pd.DataFrame,
) -> pd.DataFrame:
    adjusted_start = start_value * (1 + flat_adjustment)
    known_years = np.array([0, *uncertainty["horizon_years"].astype(int).tolist()])
    lower_values = np.array([0.0, *uncertainty["lower_log_residual"].astype(float).tolist()])
    upper_values = np.array([0.0, *uncertainty["upper_log_residual"].astype(float).tolist()])
    rows = []
    for name, assumptions in scenarios.items():
        annual_factor = (
            (1 + assumptions["market_growth"])
            * (1 + lease_effect)
        )
        if annual_factor <= 0:
            raise ValueError(f"{name} assumptions produce an invalid annual price factor")
        for year in range(horizon + 1):
            central = adjusted_start * annual_factor ** year
            low_residual = float(np.interp(year, known_years, lower_values))
            high_residual = float(np.interp(year, known_years, upper_values))
            rows.append({
                "Scenario": name, "Year": year, "Central estimate": central,
                "Lower range": central * math.exp(low_residual),
                "Upper range": central * math.exp(high_residual),
            })
    return pd.DataFrame(rows)


def build_scenario_chart(paths: pd.DataFrame, horizon: int) -> alt.LayerChart:
    """Create a layered central-path and uncertainty-band chart."""
    year_axis = alt.Axis(values=list(range(horizon + 1)))
    selection = alt.selection_point(fields=["Scenario"], bind="legend")
    band = alt.Chart(paths).mark_area(opacity=0.12).encode(
        x=alt.X(
            "Year:Q", title="Years from now",
            scale=alt.Scale(domain=[0, horizon]), axis=year_axis,
        ),
        y=alt.Y(
            "Lower range:Q", title="Scenario value (S$)",
            scale=alt.Scale(zero=False),
        ),
        y2="Upper range:Q",
        color=alt.Color("Scenario:N"),
        opacity=alt.condition(selection, alt.value(0.18), alt.value(0.03)),
    )
    line = alt.Chart(paths).mark_line(point=True, strokeWidth=3).encode(
        x=alt.X("Year:Q", title="Years from now", axis=year_axis),
        y=alt.Y("Central estimate:Q", title="Scenario value (S$)"),
        color=alt.Color("Scenario:N"),
        opacity=alt.condition(selection, alt.value(1), alt.value(0.15)),
        tooltip=[
            "Scenario:N", "Year:Q",
            alt.Tooltip("Central estimate:Q", format="$,.0f"),
            alt.Tooltip("Lower range:Q", format="$,.0f"),
            alt.Tooltip("Upper range:Q", format="$,.0f"),
        ],
    ).add_params(selection)
    return band + line


def render(query) -> None:
    st.header("Future-price scenario explorer")
    st.warning(
        "These are conditional planning scenarios, not forecasts or guaranteed returns. "
        "They show what happens when your assumptions are applied to a present-value estimate."
    )
    available = set(query(
        "SELECT table_name FROM information_schema.views "
        "WHERE table_name LIKE 'dashboard_future_scenario_%'"
    )["table_name"].tolist())
    required = {"dashboard_future_scenario_evidence", "dashboard_future_scenario_backtests"}
    if not required <= available:
        st.error("Phase 7 evidence is missing. Run Step 28, then reload this page.")
        return

    evidence = query("SELECT * FROM dashboard_future_scenario_evidence").iloc[0]
    backtests = query(
        "SELECT * FROM dashboard_future_scenario_backtests ORDER BY horizon_years"
    )
    listings = query("""
        SELECT listing_id, title, address, point_estimate_sgd, asking_price_sgd,
               year_completed, inferred_flat_type
        FROM dashboard_listing_details
        WHERE valuation_status = 'estimated' AND point_estimate_sgd IS NOT NULL
        ORDER BY address, listing_id
    """)
    labels = ["Enter a value manually"] + [
        f"{row.address} · {row.inferred_flat_type} · S${row.point_estimate_sgd:,.0f} · ID {row.listing_id}"
        for row in listings.itertuples()
    ]

    st.subheader("1. Starting point and holding period")
    selected_label = st.selectbox("Starting price estimate", labels)
    selected = None if selected_label == labels[0] else listings.iloc[labels.index(selected_label) - 1]
    start_default = 600_000.0 if selected is None else float(selected.point_estimate_sgd)
    selection_key = "manual" if selected is None else str(selected.listing_id)
    with st.form("future_scenario_inputs"):
        start_value = st.number_input(
            "Current estimated value (S$)", min_value=50_000.0, max_value=5_000_000.0,
            value=start_default, step=10_000.0, key=f"scenario_start_{selection_key}",
        )
        horizon = st.segmented_control("Projection period", [1, 3, 5], default=5)
        current_year = pd.Timestamp.now().year
        lease_default = 75.0
        if selected is not None and pd.notna(selected.year_completed):
            lease_default = float(max(1, 99 - (current_year - int(selected.year_completed))))
        remaining_lease = st.number_input(
            "Remaining lease now (years)", min_value=1.0, max_value=99.0,
            value=lease_default, step=1.0,
            help="Used for context and warnings. The evidence-based annual lease effect below reflects one fewer lease year each year.",
        )

        st.subheader("2. Assumptions for each scenario")
        st.caption(
            "Annual market growth is the combined market assumption. It can reflect expected demand, supply, policy and economic conditions."
        )
        columns = st.columns(3)
        scenarios: dict[str, dict[str, float]] = {}
        for column, (name, defaults) in zip(columns, DEFAULT_SCENARIOS.items()):
            with column:
                st.markdown(f"**{name}**")
                market = st.number_input(
                    "Annual market growth (%)", value=defaults["market_growth"] * 100,
                    min_value=-20.0, max_value=20.0, step=0.5, key=f"market_{name}",
                ) / 100
                scenarios[name] = {"market_growth": market}

        lease_default_pct = float(evidence.annual_effect_one_fewer_lease_year) * 100
        lease_effect_pct = st.number_input(
            "Annual lease effect (%)", value=lease_default_pct, min_value=-10.0,
            max_value=5.0, step=0.1,
            help="Historical association after controlling for sale quarter, block, flat type/model, area and storey. It is not an official depreciation rule.",
        )
        flat_adjustment_pct = st.number_input(
            "Verified flat-specific adjustment to starting value (%)", value=0.0,
            min_value=-20.0, max_value=20.0, step=0.5,
            help="Use only for a documented feature omitted from the starting estimate and supported by comparable-sale evidence.",
        )

        st.subheader("3. Financing sensitivity")
        st.caption("This changes borrowing cost only. It does not automatically change the projected property price.")
        finance = st.columns(3)
        loan_amount = finance[0].number_input(
            "Loan amount (S$)", min_value=0.0, max_value=5_000_000.0,
            value=float(round(start_value * 0.75, -3)), step=10_000.0,
        )
        annual_interest = finance[1].number_input(
            "Loan interest rate (%)", min_value=0.0, max_value=15.0, value=2.6, step=0.1,
        ) / 100
        loan_years = finance[2].number_input(
            "Loan term (years)", min_value=1, max_value=35, value=25, step=1,
        )
        st.form_submit_button("Update scenarios", type="primary")

    if remaining_lease <= horizon:
        st.error("The selected holding period reaches or exceeds the remaining lease. Choose a shorter period.")
        return
    try:
        paths = scenario_paths(
            start_value, int(horizon), scenarios, lease_effect_pct / 100,
            flat_adjustment_pct / 100, backtests,
        )
    except ValueError as error:
        st.error(str(error))
        return

    st.subheader("Scenario price paths")
    chart_view = st.selectbox(
        "Lines to display", ["All scenarios", *DEFAULT_SCENARIOS],
        help="Choose one scenario for a clearer chart or display all three for comparison.",
    )
    chart_paths = paths if chart_view == "All scenarios" else paths[paths["Scenario"] == chart_view]
    st.altair_chart(build_scenario_chart(chart_paths, int(horizon)), width="stretch")
    st.caption(
        "Each line is the central result under one set of assumptions. Its shaded range represents historical variation among similar repeat sales. "
        "A conservative scenario is an assumption set; it is not a statistical lower bound."
    )
    with st.expander("How the shaded range is calculated"):
        st.markdown("""
1. Match an earlier and later registered sale in the same block, flat type and model, within 5 sqm and three storeys.
2. Keep matches within three months of the selected one-, three- or five-year horizon.
3. From pairs ending before 2024, calculate the median log price change and each pair's residual around that median.
4. Use the 10th and 90th percentile residuals as lower and upper multipliers around each scenario line.
5. Interpolate those residual bounds for the years between the tested horizons.

This is an empirical historical band. It is not a probability guarantee and does not include every possible future event.
""")

    terminal = paths[paths["Year"] == int(horizon)].copy()
    terminal["Total change"] = terminal["Central estimate"] / (start_value * (1 + flat_adjustment_pct / 100)) - 1
    st.dataframe(
        terminal[SCENARIO_VALUE_COLUMNS],
        width="stretch", hide_index=True,
        column_config={
            "Central estimate": st.column_config.NumberColumn(format="dollar"),
            "Lower range": st.column_config.NumberColumn(format="dollar"),
            "Upper range": st.column_config.NumberColumn(format="dollar"),
            "Total change": st.column_config.NumberColumn(format="percent"),
        },
    )

    st.subheader("Financing impact, shown separately")
    payment = monthly_payment(loan_amount, annual_interest, int(loan_years))
    total_interest = payment * int(loan_years) * 12 - loan_amount
    finance_metrics = st.columns(3)
    finance_metrics[0].metric("Monthly repayment", f"S${payment:,.0f}")
    finance_metrics[1].metric("Total scheduled interest", f"S${total_interest:,.0f}")
    finance_metrics[2].metric("Loan term", f"{int(loan_years)} years")
    st.caption("Indicative amortisation using the entered constant rate and term; lender rules, repricing and fees are excluded.")

    with st.expander("How lease and flat-specific adjustments are defined"):
        st.markdown(f"""
**Lease decay** means the historical price association with losing one remaining lease year. The current evidence is
**{lease_default_pct:+.2f}% per year**, estimated from {int(evidence.analysed_rows):,} training transactions while controlling
for sale quarter, flat type/model, floor area, storey, location coordinates and selected amenity distances. It remains an
association and can change across lease ages. The current version applies the same percentage for each lost lease year;
it does not yet estimate separate effects for young, middle-aged and older leases.

**Supply is included indirectly in the market-growth assumption.** The pilot has no dependable forward series for BTO
completions, homes reaching MOP, active seller stock and household demand, so it does not calculate a separate supply rate.

Evidence-based flat adjustments can include a verified storey, flat-model or block premium supported by closely matched
registered sales. Renovation quality, view, orientation and corner position are not measured in the current data, so the tool
does not assign them an automatic percentage.
""")

    st.subheader("Historical backtest")
    display = backtests.copy()
    display["Horizon"] = display.horizon_years.map(lambda value: f"{value} year" if value == 1 else f"{value} years")
    st.dataframe(
        display[BACKTEST_DISPLAY_COLUMNS],
        width="stretch", hide_index=True,
        column_config={
            "calibration_pair_count": "Earlier matched pairs",
            "test_pair_count": "Later test pairs",
            "test_mae_sgd": st.column_config.NumberColumn("Test MAE", format="dollar"),
            "test_mape": st.column_config.NumberColumn("Test MAPE", format="percent"),
            "test_range_coverage": st.column_config.NumberColumn("Range coverage", format="percent"),
            "mean_test_range_width_sgd": st.column_config.NumberColumn("Average range width", format="dollar"),
        },
    )
    st.caption(
        "Backtests match sales in the same block, flat type and model within 5 sqm and three storeys. "
        "Earlier pairs calibrate the historical centre and range; pairs ending from 2024 onward test them."
    )
    selected_backtest = backtests[backtests.horizon_years == int(horizon)].iloc[0]
    if float(selected_backtest.test_range_coverage) < 0.70:
        st.warning(
            f"The {int(horizon)}-year historical range covered only "
            f"{float(selected_backtest.test_range_coverage):.1%} of later matched outcomes. "
            "Treat this range as unreliable planning context, not a calibrated prediction interval."
        )

    st.info(
        "Main limitations: results can differ by town; ranges can overlap; user assumptions can dominate the result; "
        "the starting estimate already has uncertainty; lease evidence is observational and currently constant across lease ages; "
        "and supply is represented only through the user's overall market-growth assumption."
    )
