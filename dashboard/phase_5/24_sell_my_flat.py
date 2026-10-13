"""Seller-facing form for the released single-flat valuation method."""

from __future__ import annotations

import argparse
import importlib.util
import json
from datetime import date
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


def autofill_postal() -> None:
    postal = st.session_state.get("seller_postal", "").strip()
    st.session_state.pop("seller_lookup_note", None)
    st.session_state.pop("seller_lookup_error", None)
    if st.session_state.pop("seller_type_inferred", False):
        st.session_state["seller_flat_type"] = "Not sure"
    if st.session_state.pop("seller_area_inferred", False):
        st.session_state["seller_area"] = ""
    if not postal:
        return
    try:
        with duckdb.connect(str(BUNDLE_PATH), read_only=True) as connection:
            block_number, street = PREDICTOR.lookup_postal(connection, postal)
            block = PREDICTOR.block_record(connection, block_number, street)
            room_types = sorted(
                ((flat_type, int(block.get(column) or 0))
                 for flat_type, column in PREDICTOR.ROOM_COUNTS.items()),
                key=lambda item: item[1], reverse=True,
            )
            st.session_state["seller_block"] = block_number
            st.session_state["seller_street"] = street
            st.session_state["seller_town"] = block["town"]
            note = f"Address: Blk {block_number} {street}; town: {block['town']}."
            if room_types[0][1] > 0:
                flat_type = room_types[0][0]
                st.session_state["seller_flat_type"] = flat_type
                st.session_state["seller_type_inferred"] = True
                area, _, _, scope = PREDICTOR.typical_area(
                    connection, block, flat_type, date.today().replace(day=1)
                )
                st.session_state["seller_area"] = f"{area:g}"
                st.session_state["seller_area_inferred"] = True
                note += (
                    f" Suggested {flat_type} from the block's registered flat mix; "
                    f"{area:g} sqm is a median from {scope} sales, not this unit's confirmed size."
                )
            st.session_state["seller_lookup_note"] = note
    except (ValueError, duckdb.Error) as error:
        st.session_state["seller_lookup_error"] = str(error)


def autofill_area_for_type() -> None:
    st.session_state["seller_type_inferred"] = False
    flat_type = st.session_state.get("seller_flat_type")
    block_number = st.session_state.get("seller_block")
    street = st.session_state.get("seller_street")
    if not flat_type or flat_type == "Not sure" or not block_number or not street:
        return
    try:
        with duckdb.connect(str(BUNDLE_PATH), read_only=True) as connection:
            block = PREDICTOR.block_record(connection, block_number, street)
            area, _, _, scope = PREDICTOR.typical_area(
                connection, block, flat_type, date.today().replace(day=1)
            )
        st.session_state["seller_area"] = f"{area:g}"
        st.session_state["seller_area_inferred"] = True
        st.session_state["seller_lookup_note"] = (
            f"Suggested {area:g} sqm for {flat_type} from {scope} sales. "
            "This is not the unit's confirmed size."
        )
    except (ValueError, duckdb.Error) as error:
        st.session_state["seller_lookup_error"] = str(error)


def mark_area_edited() -> None:
    st.session_state["seller_area_inferred"] = False


def render() -> None:
    st.header("Estimate my flat's selling price")
    st.write(
        "Enter your HDB flat's details to estimate its resale price from earlier sales "
        "and the currently released valuation method."
    )
    if not BUNDLE_PATH.is_file() or not RELEASE_PATH.is_file():
        st.error("The valuation data is unavailable. Build and publish the Step 10 dashboard valuation bundle.")
        return

    st.text_input(
        "Postal code (optional)", key="seller_postal", max_chars=6,
        placeholder="e.g. 510144", on_change=autofill_postal,
    )
    if st.session_state.get("seller_lookup_error"):
        st.warning(st.session_state["seller_lookup_error"])
    if st.session_state.get("seller_lookup_note"):
        st.info(st.session_state["seller_lookup_note"])
        st.caption("Suggested type and area are treated as uncertain until you edit them. Exact floor cannot be derived from a postal code.")
    st.selectbox(
        "Flat type (optional)",
        ["Not sure", "1 ROOM", "2 ROOM", "3 ROOM", "4 ROOM", "5 ROOM", "EXECUTIVE", "MULTI-GENERATION"],
        key="seller_flat_type", on_change=autofill_area_for_type,
    )
    floor_area = st.text_input(
        "Floor area in sqm (optional)", key="seller_area",
        help="A postal-code suggestion is a median for similar flats, not your unit's measured size.",
        on_change=mark_area_edited,
    )
    with st.form("seller_flat_valuation"):
        address = st.columns(3)
        block = address[0].text_input("Block number (optional)", key="seller_block")
        street = address[1].text_input("Street name (optional)", key="seller_street")
        town = address[2].text_input(
            "Town (optional)", key="seller_town",
            help="Used for a broad market reference when no postal code or full address is supplied.",
        )
        storey_range = st.selectbox(
            "Storey range (optional)",
            ["Not sure", *[f"{floor:02d} TO {floor + 2:02d}" for floor in range(1, 52, 3)]],
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
        try:
            year = int(lease_year) if lease_year.strip() else None
            if year is not None and not 1900 <= year <= 2100:
                raise ValueError("Enter a valid lease commencement year, or leave it blank.")
            area = float(floor_area) if floor_area.strip() else None
            if area is not None and not 1 <= area <= 500:
                raise ValueError("Floor area must be between 1 and 500 sqm, or left blank.")
            arguments = argparse.Namespace(
                postal_code=st.session_state.get("seller_postal", "").strip() or None,
                block=block.strip() or None, street=street.strip() or None,
                town=town.strip() or None,
                flat_type=(st.session_state["seller_flat_type"]
                           if st.session_state["seller_flat_type"] != "Not sure"
                           and not st.session_state.get("seller_type_inferred") else None),
                floor_area_sqm=area if not st.session_state.get("seller_area_inferred") else None,
                storey_range=storey_range if storey_range != "Not sure" else None,
                flat_model=flat_model.strip() or None, lease_commence_year=year,
                remaining_lease_years=None, valuation_month=None,
            )
            with st.spinner("Checking earlier sales and pricing this flat…"):
                st.session_state["seller_flat_estimate"] = PREDICTOR.predict_partial(ROOT, arguments)
        except (ValueError, FileNotFoundError, OSError, duckdb.Error) as error:
            st.error(f"Could not estimate this flat: {error}")

    estimate = st.session_state.get("seller_flat_estimate")
    if not estimate:
        return
    subject = estimate["subject"]
    if subject["block"]:
        st.subheader(f"{subject['block']} {subject['street']}")
    else:
        st.subheader("Broad market reference")
    details = [subject.get("flat_type"),
               f"{subject['floor_area_sqm']:g} sqm" if subject.get("floor_area_sqm") else None,
               subject.get("storey_range"),
               f"valuation month {estimate['valuation_month'][:7]}"]
    st.caption(" · ".join(str(value) for value in details if value))
    with st.container(horizontal=True):
        st.metric(
            "Recent-sale median" if estimate["release_type"] == "market_reference" else "Estimated selling price",
            money(estimate["price_estimate_sgd"]), border=True,
        )
        st.metric("Lower estimate", money(estimate["lower_estimate_sgd"]), border=True)
        st.metric("Upper estimate", money(estimate["upper_estimate_sgd"]), border=True)
    st.caption(
        f"Method: {estimate['model_name']} · {estimate['comparable_count']:,} supporting earlier sales · "
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
