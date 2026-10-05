"""Plan the cost of buying an HDB resale flat from user supplied assumptions.

Input: a JSON object. Run --help for the required fields. No network or database
access is needed; model prices can be copied in as comparison values.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from pathlib import Path


IRAS_BSD = "https://www.iras.gov.sg/taxes/stamp-duty/for-property/buying-or-acquiring-property/buyer%27s-stamp-duty-%28bsd%29"
IRAS_ABSD = "https://www.iras.gov.sg/taxes/stamp-duty/for-property/buying-or-acquiring-property/additional-buyer%27s-stamp-duty-%28absd%29"
HDB_FINANCE = "https://www.hdb.gov.sg/sitecore/content/hdbinfoweb/home/buying-a-flat/resale-flats/process-for-buying-a-resale-flat/resale-flat-planning/mode-of-financing"
CPF_DOWNPAYMENT = "https://www.cpf.gov.sg/service/article/can-i-use-my-cpf-savings-for-the-down-payment-of-my-property"
CPF_USAGE = "https://www.cpf.gov.sg/member/tools-and-services/calculators/cpf-housing-usage"
HDB_VALUE = "https://www.hdb.gov.sg/buying-a-flat/resale-flats/process-for-buying-a-resale-flat/option-to-purchase/request-for-value"
HDB_INTEREST = "https://www.hdb.gov.sg/sitecore/content/hdbinfoweb/home/managing-my-home/finances/loan-matters/interest-rate"
MONEYSENSE_FINANCE = "https://www.moneysense.gov.sg/buying-a-property-how-much-can-you-afford/"

CENT = Decimal("0.01")
BSD_BANDS = ((Decimal(180000), Decimal(".01")), (Decimal(180000), Decimal(".02")),
             (Decimal(640000), Decimal(".03")), (Decimal(500000), Decimal(".04")),
             (Decimal(1500000), Decimal(".05")), (None, Decimal(".06")))


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def nonnegative(config: dict, key: str, *, required: bool = False) -> Decimal:
    if required and key not in config:
        raise ValueError(f"Missing required input: {key}")
    try:
        amount = money(config.get(key, 0))
    except (ValueError, TypeError, ArithmeticError) as error:
        raise ValueError(f"{key} must be a non-negative number") from error
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"{key} must be a non-negative finite number")
    return amount


def percentage(config: dict, key: str, *, required: bool = False) -> Decimal:
    value = nonnegative(config, key, required=required)
    if value > 100:
        raise ValueError(f"{key} must be between 0 and 100")
    return value / 100


def stamp_duty(base: Decimal) -> Decimal:
    remaining = base
    duty = Decimal(0)
    for size, rate in BSD_BANDS:
        taxable = remaining if size is None else min(size, remaining)
        duty += taxable * rate
        remaining -= taxable
        if remaining <= 0:
            break
    return max(Decimal(1), duty.to_integral_value(rounding=ROUND_FLOOR)) if base > 0 else Decimal(0)


def monthly_payment(principal: Decimal, annual_rate: Decimal, years: int) -> Decimal:
    if principal == 0:
        return Decimal(0)
    periods = years * 12
    if periods <= 0:
        raise ValueError("loan_years must be positive")
    rate = float(annual_rate) / 12
    payment = float(principal) / periods if rate == 0 else float(principal) * rate / (1 - (1 + rate) ** (-periods))
    return money(payment)


def as_number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def validate(config: dict) -> dict:
    if not isinstance(config, dict):
        raise ValueError("Input file must contain one JSON object")
    loan_type = config.get("loan_type")
    if loan_type not in {"hdb", "bank", "none"}:
        raise ValueError("loan_type must be hdb, bank, or none")
    value_status = config.get("value_status")
    if value_status not in {"official", "assumed"}:
        raise ValueError("value_status must be official or assumed")
    price = nonnegative(config, "asking_price_sgd", required=True)
    value = nonnegative(config, "hdb_value_sgd", required=True)
    if not price or not value:
        raise ValueError("asking_price_sgd and hdb_value_sgd must be positive")
    percentage(config, "absd_rate_pct", required=True)
    percentage(config, "annual_interest_rate_pct", required=loan_type != "none")
    if loan_type != "none":
        if "loan_years" not in config or isinstance(config["loan_years"], bool):
            raise ValueError("loan_years is required for a loan")
        try:
            years = int(config["loan_years"])
        except (ValueError, TypeError) as error:
            raise ValueError("loan_years must be a whole number") from error
        if years != config["loan_years"] or not 1 <= years <= (25 if loan_type == "hdb" else 30):
            raise ValueError("loan_years must be a whole number within the HDB-flat planning limit (25 for HDB loan, 30 for bank loan)")
    for key in ("cash_available_sgd", "cpf_oa_available_sgd", "monthly_household_income_sgd",
                "monthly_housing_budget_sgd", "loan_amount_sgd", "cpf_eligible_limit_sgd",
                "proposed_offer_sgd", "fair_value_lower_sgd", "fair_value_upper_sgd",
                "renovation_sgd", "legal_sgd", "valuation_fee_sgd", "insurance_sgd",
                "moving_sgd", "other_upfront_sgd", "other_monthly_sgd", "option_deposit_sgd",
                "monthly_other_debt_sgd"):
        if key in config:
            nonnegative(config, key)
    maximum_ltv = Decimal(".55") if loan_type == "bank" and int(config["loan_years"]) > 25 else Decimal(".75")
    if "ltv_limit_pct" in config and percentage(config, "ltv_limit_pct") > maximum_ltv:
        raise ValueError(f"ltv_limit_pct exceeds the {maximum_ltv * 100}% planning ceiling for this loan term")
    chosen_ltv = percentage(config, "ltv_limit_pct") if "ltv_limit_pct" in config else maximum_ltv
    if "bank_minimum_cash_pct" in config:
        if loan_type != "bank":
            raise ValueError("bank_minimum_cash_pct only applies to a bank loan")
        minimum = Decimal(".10") if chosen_ltv <= Decimal(".55") else Decimal(".05")
        if percentage(config, "bank_minimum_cash_pct") < minimum:
            raise ValueError(f"bank_minimum_cash_pct must be at least {minimum * 100}% for this loan term")
    if loan_type == "none" and nonnegative(config, "loan_amount_sgd"):
        raise ValueError("loan_amount_sgd must be zero with loan_type none")
    if loan_type == "none" and "ltv_limit_pct" in config:
        raise ValueError("ltv_limit_pct does not apply without a loan")
    if nonnegative(config, "option_deposit_sgd") > 5000:
        raise ValueError("HDB resale option deposit cannot exceed S$5,000")
    lower = nonnegative(config, "fair_value_lower_sgd") if "fair_value_lower_sgd" in config else None
    upper = nonnegative(config, "fair_value_upper_sgd") if "fair_value_upper_sgd" in config else None
    if (lower is None) != (upper is None) or (lower is not None and lower > upper):
        raise ValueError("Supply an ordered fair_value_lower_sgd and fair_value_upper_sgd pair")
    if "comparison_prices_sgd" in config:
        if not isinstance(config["comparison_prices_sgd"], list):
            raise ValueError("comparison_prices_sgd must be a list")
        for raw in config["comparison_prices_sgd"]:
            if not money(raw).is_finite() or money(raw) <= 0:
                raise ValueError("Comparison prices must be positive finite numbers")
    return config


def scenario(config: dict, price: Decimal, value: Decimal) -> dict:
    loan_type = config["loan_type"]
    base = min(price, value)
    cov = max(Decimal(0), price - value)
    default_ltv = Decimal(".55") if loan_type == "bank" and int(config["loan_years"]) > 25 else Decimal(".75")
    ltv = percentage(config, "ltv_limit_pct") if "ltv_limit_pct" in config else default_ltv
    if loan_type == "none":
        loan = Decimal(0)
        max_loan = Decimal(0)
    else:
        max_loan = money(base * ltv)
        requested = nonnegative(config, "loan_amount_sgd") if "loan_amount_sgd" in config else max_loan
        if requested > max_loan:
            raise ValueError(f"loan_amount_sgd exceeds the {as_number(ltv * 100)}% planning LTV ceiling for S${price}")
        loan = requested
    default_bank_cash = Decimal(".10") if loan_type == "bank" and ltv <= Decimal(".55") else Decimal(".05")
    bank_cash_rate = percentage(config, "bank_minimum_cash_pct") if "bank_minimum_cash_pct" in config else default_bank_cash
    bank_cash_min = money(base * bank_cash_rate) if loan_type == "bank" else Decimal(0)
    cpf_available = nonnegative(config, "cpf_oa_available_sgd")
    cpf_cap = min(cpf_available, nonnegative(config, "cpf_eligible_limit_sgd")) if "cpf_eligible_limit_sgd" in config else cpf_available
    cpf_purchase = min(cpf_cap, max(Decimal(0), base - loan - bank_cash_min))
    cash_purchase = price - loan - cpf_purchase
    duty_base = max(price, value)
    bsd = stamp_duty(duty_base)
    absd = max(Decimal(0), (duty_base * percentage(config, "absd_rate_pct")).to_integral_value(rounding=ROUND_FLOOR))
    costs = {key: nonnegative(config, key) for key in
             ("renovation_sgd", "legal_sgd", "valuation_fee_sgd", "insurance_sgd", "moving_sgd", "other_upfront_sgd")}
    cash_fees = bsd + absd + sum(costs.values(), Decimal(0))
    cash_total = cash_purchase + cash_fees
    monthly = monthly_payment(loan, percentage(config, "annual_interest_rate_pct"), int(config["loan_years"])) if loan else Decimal(0)
    monthly_all = monthly + nonnegative(config, "other_monthly_sgd")
    cash_available = nonnegative(config, "cash_available_sgd")
    budget = nonnegative(config, "monthly_housing_budget_sgd") if "monthly_housing_budget_sgd" in config else None
    income = nonnegative(config, "monthly_household_income_sgd")
    deposit = nonnegative(config, "option_deposit_sgd")
    if deposit > cash_purchase:
        raise ValueError("option_deposit_sgd exceeds the cash portion of the purchase price")
    return {
        "purchase_price_sgd": as_number(price), "hdb_value_sgd": as_number(value),
        "cash_over_value_sgd": as_number(cov), "financing_base_sgd": as_number(base),
        "planning_max_loan_sgd": as_number(max_loan), "loan_used_sgd": as_number(loan),
        "cpf_oa_used_for_price_sgd": as_number(cpf_purchase),
        "cash_for_price_sgd": as_number(cash_purchase),
        "bank_minimum_cash_component_sgd": as_number(bank_cash_min),
        "option_deposit_cash_within_price_sgd": as_number(deposit),
        "cash_due_after_option_deposit_sgd": as_number(cash_total - deposit),
        "buyers_stamp_duty_sgd": as_number(bsd), "additional_buyers_stamp_duty_sgd": as_number(absd),
        "other_upfront_costs_sgd": {k: as_number(v) for k, v in costs.items()},
        "total_initial_cash_needed_sgd": as_number(cash_total),
        "total_initial_cash_and_cpf_sgd": as_number(cash_total + cpf_purchase),
        "cash_shortfall_sgd": as_number(max(Decimal(0), cash_total - cash_available)),
        "monthly_loan_payment_sgd": as_number(monthly),
        "monthly_housing_outflow_sgd": as_number(monthly_all),
        "monthly_payment_to_gross_income_pct": as_number(money(monthly_all / income * 100)) if income else None,
        "msr_30pct_screen_pass": monthly <= income * Decimal(".30") if income and loan else None,
        "bank_tdsr_55pct_screen_pass": (monthly + nonnegative(config, "monthly_other_debt_sgd") <= income * Decimal(".55"))
            if income and loan_type == "bank" and "monthly_other_debt_sgd" in config else None,
        "within_user_monthly_budget": monthly_all <= budget if budget is not None else None,
        "within_available_cash": cash_total <= cash_available,
        "five_year_cash_and_cpf_outflow_sgd": as_number(cash_total + cpf_purchase + monthly_all * 60),
    }


def affordability_ceiling(config: dict) -> dict | None:
    if "monthly_housing_budget_sgd" not in config:
        return None
    # Screening assumes value equals price: actual Cash Over Valuation is unknown before HDB's Request for Value.
    simple = dict(config)
    simple.pop("loan_amount_sgd", None)
    simple["option_deposit_sgd"] = 0
    cash = nonnegative(config, "cash_available_sgd")
    budget = nonnegative(config, "monthly_housing_budget_sgd")
    def fits(price: int) -> bool:
        result = scenario(simple, Decimal(price), Decimal(price))
        return result["total_initial_cash_needed_sgd"] <= float(cash) and result["monthly_housing_outflow_sgd"] <= float(budget)
    low, high = 0, 10_000_000
    while low < high:
        middle = (low + high + 1) // 2
        if fits(middle):
            low = middle
        else:
            high = middle - 1
    return {"indicative_max_price_sgd": low,
            "assumption": "Screening only: official value equals price, no cash over valuation, and loan up to the planning LTV cap. Actual approved loan, CPF limit, eligibility and costs may reduce this ceiling."}


def sensitivity(config: dict, asking: Decimal, value: Decimal) -> list[dict]:
    """Show one-factor changes, holding the other buyer inputs constant."""
    cases = []
    changes = []
    if config["loan_type"] != "none":
        rate = nonnegative(config, "annual_interest_rate_pct")
        changes.append(("interest_rate_minus_1pp", "annual_interest_rate_pct", max(Decimal(0), rate - 1)))
        if rate < 100:
            changes.append(("interest_rate_plus_1pp", "annual_interest_rate_pct", min(Decimal(100), rate + 1)))
        years = int(config["loan_years"])
        if years > 5 and not (config["loan_type"] == "bank" and years > 25 and years - 5 <= 25):
            changes.append(("loan_term_minus_5y", "loan_years", years - 5))
        if years + 5 <= (25 if config["loan_type"] == "hdb" else 30) and not (config["loan_type"] == "bank" and years <= 25):
            changes.append(("loan_term_plus_5y", "loan_years", years + 5))
    renovation = nonnegative(config, "renovation_sgd")
    changes.append(("renovation_plus_10000", "renovation_sgd", renovation + 10000))
    if renovation >= 10000:
        changes.append(("renovation_minus_10000", "renovation_sgd", renovation - 10000))
    for name, key, value_changed in changes:
        alternate = dict(config)
        alternate[key] = as_number(value_changed) if isinstance(value_changed, Decimal) else value_changed
        result = scenario(alternate, asking, value)
        cases.append({"case": name, "changed_input": key, "changed_value": alternate[key],
                      "initial_cash_sgd": result["total_initial_cash_needed_sgd"],
                      "monthly_housing_outflow_sgd": result["monthly_housing_outflow_sgd"],
                      "cash_shortfall_sgd": result["cash_shortfall_sgd"]})
    return cases


def build(config: dict) -> dict:
    validate(config)
    asking = nonnegative(config, "asking_price_sgd")
    value = nonnegative(config, "hdb_value_sgd")
    default_ltv_pct = 55 if config["loan_type"] == "bank" and int(config["loan_years"]) > 25 else 75 if config["loan_type"] != "none" else 0
    ltv_pct = config.get("ltv_limit_pct", default_ltv_pct)
    default_bank_cash_pct = 10 if config["loan_type"] == "bank" and money(ltv_pct) <= 55 else 5 if config["loan_type"] == "bank" else 0
    requested = [("asking", asking)]
    if "proposed_offer_sgd" in config:
        requested.append(("offer", nonnegative(config, "proposed_offer_sgd")))
    for index, raw in enumerate(config.get("comparison_prices_sgd", []), 1):
        requested.append((f"comparison_{index}", money(raw)))
    scenarios = {}
    for name, price in requested:
        if price <= 0:
            raise ValueError("Every scenario price must be positive")
        scenarios[name] = scenario(config, price, value)
    lower = nonnegative(config, "fair_value_lower_sgd") if "fair_value_lower_sgd" in config else None
    upper = nonnegative(config, "fair_value_upper_sgd") if "fair_value_upper_sgd" in config else None
    warnings = ["Planning estimate only; HFE letter or bank approval determines eligibility and loan amount.",
                "ABSD rate is user supplied; verify buyer profile, existing property count and any remission with IRAS.",
                "Option deposit is part of the purchase cash, not an additional cost. GST/timing may alter actual fees."]
    if config["value_status"] == "assumed":
        warnings.append("HDB value is an assumption, not the model's fair-price estimate or HDB's official value; loan, CPF, COV and stamp duty can change after Request for Value.")
    if "cpf_eligible_limit_sgd" not in config and nonnegative(config, "cpf_oa_available_sgd"):
        warnings.append("CPF eligibility limit was not supplied; OA balance alone does not establish usable CPF. Check CPF's housing usage calculator.")
    if config["loan_type"] != "none" and "loan_amount_sgd" not in config:
        warnings.append("Loan amount uses the planning LTV cap; it is not an approved loan offer. Lower LTV may apply.")
    if config["loan_type"] == "hdb":
        warnings.append("Verify HDB loan tenure, second-loan conditions and the interest rate in your HFE letter. Interest can change.")
    if config["loan_type"] == "bank":
        warnings.append("Bank terms, tenure, LTV and minimum cash can vary with existing loans and borrower profile; confirm the loan offer.")
    return {
        "status": "planning_estimate", "calculated_on": date.today().isoformat(),
        "buyer_inputs": {"loan_type": config["loan_type"], "value_status": config["value_status"],
                         "annual_interest_rate_pct": config.get("annual_interest_rate_pct"),
                         "loan_years": config.get("loan_years"), "absd_rate_pct": config["absd_rate_pct"],
                         "ltv_limit_pct": ltv_pct,
                         "bank_minimum_cash_pct": config.get("bank_minimum_cash_pct", default_bank_cash_pct)},
        "fair_value_range_sgd": [as_number(lower), as_number(upper)] if lower is not None else None,
        "asking_position_vs_fair_range": ("below" if asking < lower else "above" if asking > upper else "within") if lower is not None else None,
        "scenarios": scenarios, "sensitivity_at_asking": sensitivity(config, asking, value),
        "affordability_screen": affordability_ceiling(config),
        "warnings": warnings,
        "formula_sources": {
            "bsd": {"effective_from": "2023-02-15", "url": IRAS_BSD},
            "absd_user_rate": {"effective_from": "2023-04-27", "url": IRAS_ABSD},
            "ltv_and_hdb_resale_funding": {"checked_on": "2026-09-29", "url": HDB_FINANCE},
            "cpf_and_bank_cash": {"checked_on": "2026-09-29", "url": CPF_DOWNPAYMENT},
            "cpf_usage_limit": {"checked_on": "2026-09-29", "url": CPF_USAGE},
            "request_for_value": {"checked_on": "2026-09-29", "url": HDB_VALUE},
            "hdb_interest_rate": {"checked_on": "2026-09-29", "url": HDB_INTEREST},
            "bank_ltv_cash_term_and_debt_screens": {"checked_on": "2026-09-29", "url": MONEYSENSE_FINANCE},
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="JSON inputs; see data/reference/11_buyer_inputs.example.json")
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    args = parser.parse_args()
    try:
        result = build(json.loads(args.input.read_text(encoding="utf-8")))
        rendered = json.dumps(result, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        print(rendered, end="")
        return 0
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"Buyer planner failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
