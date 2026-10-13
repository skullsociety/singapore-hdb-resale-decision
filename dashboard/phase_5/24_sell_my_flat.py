"""Seller-facing form for the released single-flat valuation method."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import duckdb
import streamlit as st


ROOT = Path(__file__).resolve().parents[2]
PREDICTOR_PATH = ROOT / "scripts/phase_1_resale_model/10_predict_flat.py"
BUNDLE_PATH = ROOT / "data/phase_1_resale_model/valuation.duckdb"
RELEASE_PATH = ROOT / "reports/phase_1_resale_model/09_dashboard_release.json"


def load_predictor():
    spec = importlib.util.spec_from_file_location("phase1_single_flat_predictor", PREDICTOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load valuation script: {PREDICTOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PREDICTOR = load_predictor()


def money(value: float) -> str:
    return f"S${value:,.0f}"


def render() -> None:
    st.header("Estimate my flat's selling price")
    st.write(
        "Enter your HDB flat's details to estimate its resale price from earlier sales "
        "and the currently released valuation method."
    )
    if not BUNDLE_PATH.is_file() or not RELEASE_PATH.is_file():
        st.error("The valuation data is unavailable. Build and publish the Step 10 dashboard valuation bundle.")
        return

    with st.form("seller_flat_valuation"):
        address = st.columns(2)
        block = address[0].text_input("Block number", placeholder="e.g. 123A")
        street = address[1].text_input("Street name", placeholder="e.g. SENGKANG EAST WAY")
        details = st.columns(3)
        flat_type = details[0].selectbox(
            "Flat type", ["2 ROOM", "3 ROOM", "4 ROOM", "5 ROOM", "EXECUTIVE", "MULTI-GENERATION", "1 ROOM"]
        )
        floor_area = details[1].number_input("Floor area (sqm)", min_value=1.0, max_value=500.0, value=90.0, step=1.0)
        storey_range = details[2].selectbox(
            "Storey range", [f"{floor:02d} TO {floor + 2:02d}" for floor in range(1, 52, 3)]
        )
        optional = st.columns(2)
        flat_model = optional[0].text_input(
            "Flat model (optional)", help="If blank, use the most common model in earlier same-block sales for this flat type."
        )
        lease_year = optional[1].text_input(
            "Lease commencement year (optional)",
            help="If blank, use the most common commencement year in earlier same-block sales for this flat type.",
        )
        submitted = st.form_submit_button("Estimate selling price", type="primary")

    if submitted:
        st.session_state.pop("seller_flat_estimate", None)
        if not block.strip() or not street.strip():
            st.error("Enter both the block number and street name.")
        else:
            try:
                year = int(lease_year) if lease_year.strip() else None
                if year is not None and not 1900 <= year <= 2100:
                    raise ValueError("Enter a valid lease commencement year, or leave it blank.")
                arguments = argparse.Namespace(
                    block=block.strip(), street=street.strip(), flat_type=flat_type,
                    floor_area_sqm=float(floor_area), storey_range=storey_range,
                    flat_model=flat_model.strip() or None, lease_commence_year=year,
                    remaining_lease_years=None, valuation_month=None,
                )
                with st.spinner("Checking earlier sales and pricing this flat…"):
                    st.session_state["seller_flat_estimate"] = PREDICTOR.predict(
                        ROOT, arguments, use_dashboard_bundle=True
                    )
            except (ValueError, FileNotFoundError, OSError, duckdb.Error) as error:
                st.error(f"Could not estimate this flat: {error}")

    estimate = st.session_state.get("seller_flat_estimate")
    if not estimate:
        return
    subject = estimate["subject"]
    st.subheader(f"{subject['block']} {subject['street']}")
    st.caption(
        f"{subject['flat_type']} · {subject['floor_area_sqm']:g} sqm · {subject['storey_range']} · "
        f"valuation month {estimate['valuation_month'][:7]}"
    )
    with st.container(horizontal=True):
        st.metric("Estimated selling price", money(estimate["price_estimate_sgd"]), border=True)
        st.metric("Lower estimate", money(estimate["lower_estimate_sgd"]), border=True)
        st.metric("Upper estimate", money(estimate["upper_estimate_sgd"]), border=True)
    st.caption(
        f"Method: {estimate['model_name']} ({estimate['release_type'].replace('_', ' ')}) · "
        f"{estimate['comparable_count']:,} earlier comparable sales · "
        f"latest available transaction: {estimate['source_latest_transaction_month'][:7]}. "
        "This is a research estimate, not an official valuation or a guaranteed selling price."
    )
    if estimate["warnings"]:
        with st.expander("Important limits and evidence notes", expanded=True):
            for warning in estimate["warnings"]:
                st.write(f"• {warning}")
    if estimate["comparable_sales"]:
        st.subheader("Similar earlier sales")
        st.dataframe(estimate["comparable_sales"], hide_index=True)
        st.caption(
            "These sales support the comparable price used by the model. Exact unit condition, "
            "renovation and view are not in the transaction data."
        )
    st.download_button(
        "Download estimate for the sell-planning page",
        json.dumps(estimate, indent=2) + "\n",
        file_name="my_flat_estimate.json",
        mime="application/json",
    )
