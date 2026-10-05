"""Estimate HDB resale proceeds from seller-supplied figures.

All amounts are SGD. This is a planning calculation, not an HDB completion statement.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

CENT = Decimal("0.01")
CPF_SOURCE = "https://www.cpf.gov.sg/member/home-ownership/using-your-cpf-to-buy-a-home/cpf-refund-when-selling-or-transferring-property"
HDB_SOURCE = "https://www.hdb.gov.sg/managing-my-home/selling-a-flat/process-for-selling-a-flat/intent-to-sell?anchor=sales-proceeds"
HDB_NEXT_HOME = "https://www.hdb.gov.sg/sitecore/content/hdbinfoweb/home/buying-a-flat/resale-flats/process-for-buying-a-resale-flat/resale-flat-planning/mode-of-financing"
FEE_KEYS = ("agent_fee_sgd", "legal_fee_sgd", "resale_levy_sgd",
            "upgrading_cost_sgd", "other_selling_costs_sgd")


def amount(config: dict, key: str, *, required: bool = False) -> Decimal:
    if required and key not in config:
        raise ValueError(f"Missing required input: {key}")
    try:
        value = Decimal(str(config.get(key, 0))).quantize(CENT, rounding=ROUND_HALF_UP)
    except (ArithmeticError, TypeError, ValueError) as error:
        raise ValueError(f"{key} must be a non-negative number") from error
    if not value.is_finite() or value < 0:
        raise ValueError(f"{key} must be a non-negative finite number")
    return value


def number(value: Decimal) -> float:
    return float(value)


def validated(config: dict) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal, dict[str, Decimal]]:
    if not isinstance(config, dict):
        raise ValueError("Input must be one JSON object")
    expected = amount(config, "expected_sale_price_sgd", required=True)
    if expected <= 0:
        raise ValueError("expected_sale_price_sgd must be positive")
    conservative = amount(config, "conservative_sale_price_sgd") if "conservative_sale_price_sgd" in config else (expected * Decimal(".95")).quantize(CENT, rounding=ROUND_HALF_UP)
    optimistic = amount(config, "optimistic_sale_price_sgd") if "optimistic_sale_price_sgd" in config else (expected * Decimal("1.05")).quantize(CENT, rounding=ROUND_HALF_UP)
    if not 0 < conservative <= expected <= optimistic:
        raise ValueError("Sale prices must be positive and ordered: conservative <= expected <= optimistic")
    loan = amount(config, "outstanding_loan_sgd", required=True)
    if "cpf_refund_required_sgd" in config:
        cpf_due = amount(config, "cpf_refund_required_sgd")
        if any(key in config for key in ("cpf_principal_used_sgd", "cpf_accrued_interest_sgd", "cpf_other_refund_sgd")):
            raise ValueError("Enter the CPF refund total OR its components, not both")
    else:
        principal = amount(config, "cpf_principal_used_sgd", required=True)
        interest = amount(config, "cpf_accrued_interest_sgd", required=True)
        cpf_due = principal + interest + amount(config, "cpf_other_refund_sgd")
    fees = {key: amount(config, key) for key in FEE_KEYS}
    buyer_keys = ("next_home_cash_needed_sgd", "next_home_cpf_needed_sgd")
    if sum(key in config for key in buyer_keys) == 1:
        raise ValueError("Enter both next-home cash and CPF needs, or leave both blank")
    for key in buyer_keys:
        if key in config:
            amount(config, key)
    return conservative, expected, optimistic, loan, cpf_due, fees


def calculate_scenario(config: dict, sale_price: Decimal, loan: Decimal,
                       cpf_due: Decimal, fees: dict[str, Decimal]) -> dict:
    loan_paid = min(sale_price, loan)
    loan_gap = loan - loan_paid
    after_loan = sale_price - loan_paid
    cpf_refunded = min(after_loan, cpf_due)
    cpf_gap = cpf_due - cpf_refunded
    after_cpf = after_loan - cpf_refunded
    total_fees = sum(fees.values(), Decimal(0))
    fees_paid_from_sale = min(after_cpf, total_fees)
    fees_gap = total_fees - fees_paid_from_sale
    cash = after_cpf - fees_paid_from_sale
    result = {
        "sale_price_sgd": number(sale_price),
        "outstanding_loan_sgd": number(loan),
        "loan_paid_from_sale_sgd": number(loan_paid),
        "loan_cash_top_up_needed_sgd": number(loan_gap),
        "cpf_refund_required_sgd": number(cpf_due),
        "cpf_refund_from_sale_sgd": number(cpf_refunded),
        "cpf_refund_shortfall_sgd": number(cpf_gap),
        "selling_costs_sgd": {key: number(value) for key, value in fees.items()},
        "total_selling_costs_sgd": number(total_fees),
        "selling_costs_paid_from_sale_sgd": number(fees_paid_from_sale),
        "selling_costs_cash_top_up_needed_sgd": number(fees_gap),
        "estimated_cash_proceeds_sgd": number(cash),
    }
    if "next_home_cash_needed_sgd" in config or "next_home_cpf_needed_sgd" in config:
        buyer_cash = amount(config, "next_home_cash_needed_sgd")
        buyer_cpf = amount(config, "next_home_cpf_needed_sgd")
        result["next_home_comparison"] = {
            "buyer_cash_need_sgd": number(buyer_cash),
            "buyer_cpf_need_sgd": number(buyer_cpf),
            "sale_cash_surplus_or_gap_sgd": number(cash - buyer_cash),
            "sale_cpf_refund_surplus_or_gap_sgd": number(cpf_refunded - buyer_cpf),
        }
    return result


def build(config: dict) -> dict:
    conservative, expected, optimistic, loan, cpf_due, fees = validated(config)
    prices = {"conservative": conservative, "expected": expected, "optimistic": optimistic}
    scenarios = {name: calculate_scenario(config, price, loan, cpf_due, fees)
                 for name, price in prices.items()}
    warnings = [
        "Planning estimate only. Confirm the loan redemption amount, CPF refund and sale deductions in HDB, CPF and lender statements.",
        "Cash proceeds and CPF refund are separate. CPF returned to an account is not automatically cash available for the next home.",
        "The calculation shows all cash proceeds across the sale; it does not schedule when an option deposit or completion balance is received.",
    ]
    if "conservative_sale_price_sgd" not in config or "optimistic_sale_price_sgd" not in config:
        warnings.append("A missing low or high sale price uses an illustrative 5% change from the expected price.")
    if "cpf_refund_required_sgd" not in config:
        warnings.append("CPF refund uses the entered principal, accrued interest and other refund components; check CPF's current required total.")
    if any(row["cpf_refund_shortfall_sgd"] > 0 for row in scenarios.values()):
        warnings.append("A CPF refund shortfall may not require a cash top-up when sold at market value. Below-market sales and other cases need CPF/HDB confirmation.")
    if any(row["loan_cash_top_up_needed_sgd"] > 0 for row in scenarios.values()):
        warnings.append("At least one scenario does not cover the housing loan; HDB says the loan balance must be paid in cash.")
    if any(row["selling_costs_cash_top_up_needed_sgd"] > 0 for row in scenarios.values()):
        warnings.append("At least one scenario has selling costs that cannot be covered from proceeds; budget cash for those costs.")
    if "next_home_cash_needed_sgd" in config or "next_home_cpf_needed_sgd" in config:
        warnings.append("Next-home comparison excludes other savings, CPF usage limits, refund timing and second HDB loan conditions.")
    return {
        "status": "planning_estimate",
        "calculated_on": date.today().isoformat(),
        "cpf_refund_source": "official_total" if "cpf_refund_required_sgd" in config else "entered_components",
        "scenarios": scenarios,
        "warnings": warnings,
        "formula_sources": {
            "cpf_refund": {"checked_on": "2026-09-29", "url": CPF_SOURCE},
            "hdb_sale_proceeds": {"checked_on": "2026-09-29", "url": HDB_SOURCE},
            "next_home_financing": {"checked_on": "2026-09-29", "url": HDB_NEXT_HOME},
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Seller assumptions JSON")
    parser.add_argument("--output", type=Path, help="Optional result JSON")
    args = parser.parse_args()
    try:
        result = build(json.loads(args.input.read_text(encoding="utf-8")))
        rendered = json.dumps(result, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        print(rendered, end="")
        return 0
    except (ValueError, OSError, TypeError, json.JSONDecodeError) as error:
        print(f"Seller planner failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

