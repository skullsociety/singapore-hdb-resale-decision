"""Focused checks for the buyer calculator's regulated and cash-flow rules."""

import importlib.util
import unittest
from decimal import Decimal
from pathlib import Path


spec = importlib.util.spec_from_file_location("buyer_planner", Path(__file__).with_name("11_buyer_cost_planner.py"))
planner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(planner)


def inputs(**changes):
    values = {
        "asking_price_sgd": 600000, "hdb_value_sgd": 580000,
        "value_status": "assumed", "loan_type": "bank", "loan_years": 25,
        "annual_interest_rate_pct": 3, "absd_rate_pct": 0,
        "cpf_oa_available_sgd": 80000, "cpf_eligible_limit_sgd": 80000,
        "cash_available_sgd": 200000, "monthly_housing_budget_sgd": 3000,
        "option_deposit_sgd": 5000,
    }
    values.update(changes)
    return values


class BuyerPlannerTest(unittest.TestCase):
    def test_bsd_marginal_bands_and_rounding(self):
        self.assertEqual(planner.stamp_duty(Decimal("180000")), Decimal(1800))
        self.assertEqual(planner.stamp_duty(Decimal("360000")), Decimal(5400))
        self.assertEqual(planner.stamp_duty(Decimal("1000000")), Decimal(24600))
        self.assertEqual(planner.stamp_duty(Decimal("1000000.99")), Decimal(24600))

    def test_bank_cash_includes_cov_and_does_not_duplicate_deposit(self):
        result = planner.build(inputs())["scenarios"]["asking"]
        self.assertEqual(result["cash_over_value_sgd"], 20000)
        self.assertEqual(result["loan_used_sgd"], 435000)
        self.assertEqual(result["bank_minimum_cash_component_sgd"], 29000)
        self.assertEqual(result["cpf_oa_used_for_price_sgd"], 80000)
        self.assertEqual(result["cash_for_price_sgd"], 85000)
        self.assertEqual(result["buyers_stamp_duty_sgd"], 12600)
        self.assertEqual(result["total_initial_cash_needed_sgd"], 97600)
        self.assertEqual(result["cash_due_after_option_deposit_sgd"], 92600)

    def test_offer_and_absd_use_higher_value(self):
        result = planner.build(inputs(proposed_offer_sgd=550000, absd_rate_pct=5))
        offer = result["scenarios"]["offer"]
        self.assertEqual(offer["cash_over_value_sgd"], 0)
        self.assertEqual(offer["additional_buyers_stamp_duty_sgd"], 29000)
        self.assertEqual(offer["buyers_stamp_duty_sgd"], 12000)

    def test_invalid_loan_and_deposit_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "exceeds"):
            planner.build(inputs(loan_amount_sgd=500000))
        with self.assertRaisesRegex(ValueError, "option deposit"):
            planner.build(inputs(option_deposit_sgd=6000))
        with self.assertRaisesRegex(ValueError, "25 for HDB"):
            planner.build(inputs(loan_type="hdb", loan_years=30))

    def test_sensitivity_moves_in_expected_direction(self):
        result = planner.build(inputs(renovation_sgd=30000))
        asking = result["scenarios"]["asking"]
        cases = {row["case"]: row for row in result["sensitivity_at_asking"]}
        self.assertGreater(cases["interest_rate_plus_1pp"]["monthly_housing_outflow_sgd"],
                           asking["monthly_housing_outflow_sgd"])
        self.assertEqual(cases["renovation_plus_10000"]["initial_cash_sgd"],
                         asking["total_initial_cash_needed_sgd"] + 10000)

    def test_long_bank_loan_uses_lower_ltv_and_more_cash(self):
        values = inputs(loan_years=30)
        result = planner.build(values)["scenarios"]["asking"]
        self.assertEqual(result["planning_max_loan_sgd"], 319000)
        self.assertEqual(result["bank_minimum_cash_component_sgd"], 58000)
        with self.assertRaisesRegex(ValueError, "55"):
            planner.build(inputs(loan_years=30, ltv_limit_pct=75))


if __name__ == "__main__":
    unittest.main()
