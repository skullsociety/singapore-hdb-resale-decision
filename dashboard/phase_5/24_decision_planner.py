"""Interactive buyer and seller planning page for the Streamlit dashboard."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUYER = load_module(
    PROJECT_ROOT / "scripts" / "phase_2_buyer_planner" / "11_buyer_cost_planner.py",
    "dashboard_buyer_planner",
)
SELLER = load_module(
    PROJECT_ROOT / "scripts" / "phase_3_seller_planner" / "13_seller_proceeds_planner.py",
    "dashboard_seller_planner",
)


def money(value: float | int | None) -> str:
    return "—" if value is None else f"S${float(value):,.0f}"


def add_if_positive(config: dict, key: str, value: float) -> None:
    if value > 0:
        config[key] = value


def parse_price_list(value: str) -> list[float]:
    if not value.strip():
        return []
    try:
        prices = [float(item.strip().replace(",", "")) for item in value.split(",")]
    except ValueError as error:
        raise ValueError("Additional comparison prices must be comma-separated numbers") from error
    if any(price <= 0 for price in prices):
        raise ValueError("Additional comparison prices must be positive")
    return prices


def imported_estimate_bounds(uploaded_file) -> tuple[float, float] | None:
    if uploaded_file is None:
        return None
    try:
        report = json.loads(uploaded_file.getvalue())
        lower, upper = report["lower_estimate_sgd"], report["upper_estimate_sgd"]
        if lower is None or upper is None or float(lower) <= 0 or float(lower) > float(upper):
            raise ValueError
        return float(lower), float(upper)
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        raise ValueError("Choose a Step 10 estimate JSON with a valid price range") from error


def friendly_buyer_error(error: ValueError) -> str:
    """Use the dashboard labels rather than calculator field names in messages."""
    message = str(error)
    translations = {
        "asking_price_sgd and hdb_value_sgd must be positive": (
            "Enter an amount above S$0 in **Asking price** and **HDB value used for planning**. "
            "If you do not have HDB's Request for Value, select the planning-assumption checkbox."
        ),
        "absd_rate_pct must be between 0 and 100": "Enter an ABSD rate between 0% and 100%.",
        "loan_years is required for a loan": "Enter a housing-loan repayment period when you choose HDB or bank financing.",
        "option_deposit_sgd exceeds the cash portion of the purchase price": (
            "The **Option deposit** cannot be more than the calculated cash portion of the purchase price."
        ),
        "Supply an ordered fair_value_lower_sgd and fair_value_upper_sgd pair": (
            "Enter both **Research range** amounts, with the lower amount no higher than the upper amount."
        ),
    }
    return translations.get(message, message.replace("_sgd", "").replace("_pct", " (%)").replace("_", " "))


def buyer_inputs() -> None:
    st.subheader("Buy a resale flat")
    st.caption("Enter planning assumptions. The calculation runs locally when you select Calculate buyer plan.")
    with st.form("buyer_planner"):
        left, right = st.columns(2)
        with left:
            asking_price = st.number_input(
                "Asking price (S$)", min_value=0.0, step=10_000.0,
                help="The current price shown by the seller, agent or property listing.",
            )
            use_asking_as_value = st.checkbox("I do not have HDB's Request for Value — use the asking price as a planning assumption")
            hdb_value = st.number_input(
                "HDB value used for planning (S$)", min_value=0.0, step=10_000.0,
                disabled=use_asking_as_value,
                help="Use HDB's Request for Value when available. It affects loan, CPF and Cash Over Valuation planning.",
            )
            st.markdown(f"[What is HDB's Request for Value? ↗]({BUYER.HDB_VALUE})")
            value_status = st.selectbox(
                "HDB value status",
                ["assumed", "official"],
                format_func=lambda value: "Assumption" if value == "assumed" else "Official Request for Value",
                disabled=use_asking_as_value,
            )
            proposed_offer = st.number_input("Proposed offer (optional, S$)", min_value=0.0, step=10_000.0)
            fair_low = st.number_input("Research range lower end (optional, S$)", min_value=0.0, step=10_000.0)
            fair_high = st.number_input("Research range upper end (optional, S$)", min_value=0.0, step=10_000.0)
            estimate_upload = st.file_uploader(
                "Import a Step 10 property price-estimate file (optional)", type=["json"],
                help="Choose the .json file saved by Step 10, Estimate one flat. It fills the research price range only; it is not HDB's official value.",
            )
            st.caption("Step 10 estimate file: an optional .json file from the model's **Estimate one flat** output. It fills the lower and upper research range, not the HDB value or loan approval.")
            comparison_prices = st.text_input("Additional comparison prices (optional)", placeholder="e.g. 580000, 620000")
        with right:
            loan_type = st.selectbox("Financing choice", ["hdb", "bank", "none"], format_func=lambda value: {"hdb": "HDB housing loan", "bank": "Bank loan", "none": "No loan"}[value])
            absd_rate = st.number_input(
                "Additional Buyer's Stamp Duty (ABSD) rate (%)", min_value=0.0, max_value=100.0, value=0.0, step=0.1,
                help="ABSD is an extra property tax. The rate depends on buyer residency, number of properties owned, joint buyers and possible remission conditions.",
            )
            st.markdown(f"[How to find your ABSD rate on the IRAS website ↗]({BUYER.IRAS_ABSD})")
            interest_rate = st.number_input("Annual housing-loan interest rate (%)", min_value=0.0, max_value=100.0, value=2.6, step=0.05, disabled=loan_type == "none")
            loan_years = st.number_input("Housing-loan repayment period (years)", min_value=1, max_value=30, value=25, step=1, disabled=loan_type == "none")
            cash_available = st.number_input("Cash available for purchase (S$)", min_value=0.0, step=10_000.0)
            cpf_available = st.number_input("CPF Ordinary Account available (S$)", min_value=0.0, step=10_000.0)
            household_income = st.number_input("Monthly household income (S$)", min_value=0.0, step=500.0)

        with st.expander("Loan, cash and CPF assumptions"):
            first, second, third = st.columns(3)
            with first:
                loan_amount = st.number_input("Known loan amount (optional, S$)", min_value=0.0, step=10_000.0)
                ltv_limit = st.number_input("LTV ceiling (optional, %)", min_value=0.0, max_value=75.0, step=0.5)
                bank_cash = st.number_input("Bank minimum cash (optional, %)", min_value=0.0, max_value=100.0, step=0.5, disabled=loan_type != "bank")
            with second:
                cpf_limit = st.number_input("CPF eligible limit (optional, S$)", min_value=0.0, step=10_000.0)
                monthly_budget = st.number_input("Monthly housing budget (optional, S$)", min_value=0.0, step=100.0)
                other_debt = st.number_input("Other monthly debt payments — bank loan only (S$)", min_value=0.0, step=100.0, disabled=loan_type != "bank")
            with third:
                option_deposit = st.number_input("Option deposit (S$)", min_value=0.0, max_value=5000.0, step=100.0)
                renovation = st.number_input("Renovation budget (S$)", min_value=0.0, step=1_000.0)
                legal = st.number_input("Legal costs (S$)", min_value=0.0, step=500.0)
                valuation_fee = st.number_input("Valuation fee (S$)", min_value=0.0, step=100.0)

        with st.expander("Other planning costs"):
            first, second, third = st.columns(3)
            with first:
                insurance = st.number_input("Insurance (S$)", min_value=0.0, step=100.0)
            with second:
                moving = st.number_input("Moving costs (S$)", min_value=0.0, step=100.0)
            with third:
                other_upfront = st.number_input("Other upfront costs (S$)", min_value=0.0, step=100.0)
            other_monthly = st.number_input("Other monthly housing-related costs (S$)", min_value=0.0, step=100.0)

        submitted = st.form_submit_button("Calculate buyer plan", type="primary")

    if not submitted:
        if result := st.session_state.get("buyer_plan"):
            buyer_result(result)
        return
    config = {
        "asking_price_sgd": asking_price,
        "hdb_value_sgd": asking_price if use_asking_as_value else hdb_value,
        "value_status": "assumed" if use_asking_as_value else value_status,
        "loan_type": loan_type,
        "absd_rate_pct": absd_rate,
        "cash_available_sgd": cash_available,
        "cpf_oa_available_sgd": cpf_available,
        "monthly_household_income_sgd": household_income,
        "option_deposit_sgd": option_deposit,
        "renovation_sgd": renovation,
        "legal_sgd": legal,
        "valuation_fee_sgd": valuation_fee,
        "insurance_sgd": insurance,
        "moving_sgd": moving,
        "other_upfront_sgd": other_upfront,
        "other_monthly_sgd": other_monthly,
    }
    if loan_type != "none":
        config.update({"annual_interest_rate_pct": interest_rate, "loan_years": loan_years})
    try:
        imported_bounds = imported_estimate_bounds(estimate_upload)
        comparison_values = parse_price_list(comparison_prices)
    except ValueError as error:
        st.error(friendly_buyer_error(error))
        return
    if imported_bounds:
        fair_low = fair_low or imported_bounds[0]
        fair_high = fair_high or imported_bounds[1]
    for key, value in (
        ("proposed_offer_sgd", proposed_offer), ("fair_value_lower_sgd", fair_low),
        ("fair_value_upper_sgd", fair_high), ("loan_amount_sgd", loan_amount),
        ("ltv_limit_pct", ltv_limit), ("bank_minimum_cash_pct", bank_cash),
        ("cpf_eligible_limit_sgd", cpf_limit), ("monthly_housing_budget_sgd", monthly_budget),
        ("monthly_other_debt_sgd", other_debt),
    ):
        add_if_positive(config, key, value)
    if comparison_values:
        config["comparison_prices_sgd"] = comparison_values
    try:
        result = BUYER.build(config)
    except ValueError as error:
        st.error(str(error))
        return
    st.session_state["buyer_plan"] = result
    buyer_result(result)


def buyer_result(result: dict) -> None:
    asking = result["scenarios"]["asking"]
    st.success("Buyer plan calculated. The seller planner can use this plan as the next-home comparison.")
    columns = st.columns(4)
    columns[0].metric("Initial cash needed", money(asking["total_initial_cash_needed_sgd"]))
    columns[1].metric("CPF used for price", money(asking["cpf_oa_used_for_price_sgd"]))
    columns[2].metric("Monthly housing outflow", money(asking["monthly_housing_outflow_sgd"]))
    columns[3].metric("Cash shortfall", money(asking["cash_shortfall_sgd"]))
    st.download_button(
        "Download buyer plan JSON", json.dumps(result, indent=2),
        file_name="buyer_plan.json", mime="application/json",
    )
    st.subheader("Purchase breakdown")
    st.dataframe([{
        "Purchase price": asking["purchase_price_sgd"],
        "Planning loan": asking["loan_used_sgd"],
        "CPF used for price": asking["cpf_oa_used_for_price_sgd"],
        "Cash for price": asking["cash_for_price_sgd"],
        "BSD": asking["buyers_stamp_duty_sgd"],
        "ABSD": asking["additional_buyers_stamp_duty_sgd"],
        "Initial cash": asking["total_initial_cash_needed_sgd"],
    }], hide_index=True, width="stretch", column_config={
        "Purchase price": st.column_config.NumberColumn(format="S$ %,.0f"),
        "Planning loan": st.column_config.NumberColumn(format="S$ %,.0f"),
        "CPF used for price": st.column_config.NumberColumn(format="S$ %,.0f"),
        "Cash for price": st.column_config.NumberColumn(format="S$ %,.0f"),
        "BSD": st.column_config.NumberColumn(format="S$ %,.0f"),
        "ABSD": st.column_config.NumberColumn(format="S$ %,.0f"),
        "Initial cash": st.column_config.NumberColumn(format="S$ %,.0f"),
    })
    st.subheader("Price scenarios")
    scenarios = pd.DataFrame(result["scenarios"]).T.reset_index().rename(columns={"index": "Scenario"})
    displayed = ["Scenario", "purchase_price_sgd", "loan_used_sgd", "cash_for_price_sgd", "total_initial_cash_needed_sgd", "monthly_housing_outflow_sgd", "cash_shortfall_sgd"]
    st.dataframe(
        scenarios[displayed],
        hide_index=True, width="stretch",
        column_config={column: st.column_config.NumberColumn(format="S$ %,.0f") for column in displayed if column.endswith("_sgd")},
    )
    if result["affordability_screen"]:
        st.info(f"Indicative affordability ceiling: {money(result['affordability_screen']['indicative_max_price_sgd'])}. This assumes HDB value equals price.")
    st.subheader("Sensitivity at asking price")
    sensitivity = pd.DataFrame(result["sensitivity_at_asking"])
    if not sensitivity.empty:
        st.dataframe(sensitivity, hide_index=True, width="stretch", column_config={
            "initial_cash_sgd": st.column_config.NumberColumn("Initial cash", format="S$ %,.0f"),
            "monthly_housing_outflow_sgd": st.column_config.NumberColumn("Monthly housing outflow", format="S$ %,.0f"),
            "cash_shortfall_sgd": st.column_config.NumberColumn("Cash shortfall", format="S$ %,.0f"),
        })
    with st.expander("Compare the asking scenario with checked documents"):
        st.caption("Enter figures from an HFE letter, bank offer, Request for Value, CPF record or completion document. This does not change the plan.")
        document_loan = st.number_input("Approved housing loan in your document (S$)", min_value=0.0, step=10_000.0, key="document_loan")
        document_monthly = st.number_input("Monthly repayment in your document (S$)", min_value=0.0, step=100.0, key="document_monthly")
        document_cash = st.number_input("Initial cash in your document (S$)", min_value=0.0, step=10_000.0, key="document_cash")
        comparisons = [
            {"Figure": "Approved loan", "Plan": asking["loan_used_sgd"], "Document": document_loan or None},
            {"Figure": "Monthly repayment", "Plan": asking["monthly_loan_payment_sgd"], "Document": document_monthly or None},
            {"Figure": "Initial cash", "Plan": asking["total_initial_cash_needed_sgd"], "Document": document_cash or None},
        ]
        for row in comparisons:
            row["Difference (document − plan)"] = None if row["Document"] is None else row["Document"] - row["Plan"]
        st.dataframe(pd.DataFrame(comparisons), hide_index=True, width="stretch", column_config={
            "Plan": st.column_config.NumberColumn(format="S$ %,.0f"),
            "Document": st.column_config.NumberColumn(format="S$ %,.0f"),
            "Difference (document − plan)": st.column_config.NumberColumn(format="S$ %,.0f"),
        })
    with st.expander("Warnings and assumptions"):
        for warning in result["warnings"]:
            st.write(f"- {warning}")


def seller_inputs() -> None:
    st.subheader("Sell your current flat")
    st.caption("Cash proceeds and CPF refund are shown separately. Use current loan and CPF statements for all owners.")
    buyer_plan = st.session_state.get("buyer_plan")
    with st.form("seller_planner"):
        left, right = st.columns(2)
        with left:
            expected_sale = st.number_input("Expected sale price (S$)", min_value=0.0, step=10_000.0)
            conservative_sale = st.number_input("Conservative sale price (optional, S$)", min_value=0.0, step=10_000.0)
            optimistic_sale = st.number_input("Optimistic sale price (optional, S$)", min_value=0.0, step=10_000.0)
            outstanding_loan = st.number_input("Outstanding housing loan (S$)", min_value=0.0, step=10_000.0)
            cpf_method = st.radio("CPF refund input", ["Official total", "Components"], horizontal=True)
            cpf_refund = st.number_input("CPF refund required for all owners (S$)", min_value=0.0, step=10_000.0, disabled=cpf_method != "Official total")
            cpf_principal = cpf_interest = cpf_other = 0.0
            if cpf_method == "Components":
                cpf_principal = st.number_input("CPF principal used (S$)", min_value=0.0, step=10_000.0)
                cpf_interest = st.number_input("CPF accrued interest (S$)", min_value=0.0, step=10_000.0)
                cpf_other = st.number_input("Other CPF refund required (S$)", min_value=0.0, step=1_000.0)
        with right:
            agent_fee = st.number_input("Agent fee (S$)", min_value=0.0, step=1_000.0)
            legal_fee = st.number_input("Legal fee (S$)", min_value=0.0, step=500.0)
            resale_levy = st.number_input("Resale levy (S$)", min_value=0.0, step=1_000.0)
            upgrading = st.number_input("Outstanding upgrading costs (S$)", min_value=0.0, step=1_000.0)
            other_costs = st.number_input("Other sale deductions (S$)", min_value=0.0, step=500.0)

        use_buyer_plan = st.checkbox(
            "Use the buyer plan above for next-home cash and CPF needs",
            value=buyer_plan is not None,
            disabled=buyer_plan is None,
        )
        next_cash = next_cpf = 0.0
        if not use_buyer_plan:
            first, second = st.columns(2)
            with first:
                next_cash = st.number_input("Next-home initial cash needed (optional, S$)", min_value=0.0, step=10_000.0)
            with second:
                next_cpf = st.number_input("Next-home CPF used for price (optional, S$)", min_value=0.0, step=10_000.0)
        submitted = st.form_submit_button("Calculate seller plan", type="primary")

    if not submitted:
        if result := st.session_state.get("seller_plan"):
            seller_result(result)
        return
    config = {
        "expected_sale_price_sgd": expected_sale,
        "outstanding_loan_sgd": outstanding_loan,
        "agent_fee_sgd": agent_fee,
        "legal_fee_sgd": legal_fee,
        "resale_levy_sgd": resale_levy,
        "upgrading_cost_sgd": upgrading,
        "other_selling_costs_sgd": other_costs,
    }
    if cpf_method == "Official total":
        config["cpf_refund_required_sgd"] = cpf_refund
    else:
        config.update({"cpf_principal_used_sgd": cpf_principal, "cpf_accrued_interest_sgd": cpf_interest, "cpf_other_refund_sgd": cpf_other})
    for key, value in (("conservative_sale_price_sgd", conservative_sale), ("optimistic_sale_price_sgd", optimistic_sale)):
        add_if_positive(config, key, value)
    if use_buyer_plan and buyer_plan:
        asking = buyer_plan["scenarios"]["asking"]
        config["next_home_cash_needed_sgd"] = asking["total_initial_cash_needed_sgd"]
        config["next_home_cpf_needed_sgd"] = asking["cpf_oa_used_for_price_sgd"]
    elif next_cash > 0 and next_cpf > 0:
        config["next_home_cash_needed_sgd"] = next_cash
        config["next_home_cpf_needed_sgd"] = next_cpf
    try:
        result = SELLER.build(config)
    except ValueError as error:
        st.error(str(error))
        return
    st.session_state["seller_plan"] = result
    seller_result(result)


def seller_result(result: dict) -> None:
    expected = result["scenarios"]["expected"]
    st.success("Seller plan calculated.")
    columns = st.columns(4)
    columns[0].metric("Estimated cash proceeds", money(expected["estimated_cash_proceeds_sgd"]))
    columns[1].metric("CPF refund from sale", money(expected["cpf_refund_from_sale_sgd"]))
    columns[2].metric("Loan top-up risk", money(expected["loan_cash_top_up_needed_sgd"]))
    columns[3].metric("Selling-cost top-up risk", money(expected["selling_costs_cash_top_up_needed_sgd"]))
    st.download_button(
        "Download seller plan JSON", json.dumps(result, indent=2),
        file_name="seller_plan.json", mime="application/json",
    )
    st.subheader("Expected sale breakdown")
    st.dataframe([{
        "Sale price": expected["sale_price_sgd"],
        "Loan paid from sale": expected["loan_paid_from_sale_sgd"],
        "CPF refund from sale": expected["cpf_refund_from_sale_sgd"],
        "Selling costs": expected["total_selling_costs"],
        "Cash proceeds": expected["estimated_cash_proceeds_sgd"],
    }], hide_index=True, width="stretch", column_config={
        "Sale price": st.column_config.NumberColumn(format="S$ %,.0f"),
        "Loan paid from sale": st.column_config.NumberColumn(format="S$ %,.0f"),
        "CPF refund from sale": st.column_config.NumberColumn(format="S$ %,.0f"),
        "Selling costs": st.column_config.NumberColumn(format="S$ %,.0f"),
        "Cash proceeds": st.column_config.NumberColumn(format="S$ %,.0f"),
    })
    st.subheader("Sale-price scenarios")
    scenarios = pd.DataFrame(result["scenarios"]).T.reset_index().rename(columns={"index": "Scenario"})
    displayed = ["Scenario", "sale_price_sgd", "estimated_cash_proceeds_sgd", "cpf_refund_from_sale_sgd", "loan_cash_top_up_needed_sgd", "selling_costs_cash_top_up_needed_sgd"]
    st.dataframe(
        scenarios[displayed],
        hide_index=True, width="stretch",
        column_config={column: st.column_config.NumberColumn(format="S$ %,.0f") for column in displayed if column.endswith("_sgd")},
    )
    if "next_home_comparison" in expected:
        comparison = expected["next_home_comparison"]
        st.subheader("Expected sale compared with next-home plan")
        st.dataframe([{
            "Cash sale surplus / gap": comparison["sale_cash_surplus_or_gap_sgd"],
            "CPF refund surplus / gap": comparison["sale_cpf_refund_surplus_or_gap_sgd"],
        }], hide_index=True, width="stretch", column_config={
            "Cash sale surplus / gap": st.column_config.NumberColumn(format="S$ %,.0f"),
            "CPF refund surplus / gap": st.column_config.NumberColumn(format="S$ %,.0f"),
        })
    with st.expander("Warnings and assumptions"):
        for warning in result["warnings"]:
            st.write(f"- {warning}")


def render() -> None:
    st.header("Buy and sell planning")
    st.write("Plan a purchase, a sale, or both together. The page uses the existing buyer and seller calculation rules; it does not save your inputs.")
    buyer_tab, seller_tab = st.tabs(["Buy a resale flat", "Sell a current flat"])
    with buyer_tab:
        buyer_inputs()
    with seller_tab:
        seller_inputs()

