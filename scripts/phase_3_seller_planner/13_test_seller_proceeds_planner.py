"""Focused checks of seller proceeds and shortage handling."""
import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("seller", Path(__file__).with_name("13_seller_proceeds_planner.py"))
seller = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(seller)


class SellerPlannerTest(unittest.TestCase):
    def test_normal_proceeds_and_next_home_comparison(self):
        result = seller.build({
            "expected_sale_price_sgd": 600000, "conservative_sale_price_sgd": 570000,
            "optimistic_sale_price_sgd": 630000, "outstanding_loan_sgd": 200000,
            "cpf_refund_required_sgd": 150000, "agent_fee_sgd": 12000,
            "legal_fee_sgd": 2000, "next_home_cash_needed_sgd": 250000,
            "next_home_cpf_needed_sgd": 120000
        })
        expected = result["scenarios"]["expected"]
        self.assertEqual(expected["estimated_cash_proceeds_sgd"], 236000)
        self.assertEqual(expected["cpf_refund_from_sale_sgd"], 150000)
        self.assertEqual(expected["next_home_comparison"]["sale_cash_surplus_or_gap_sgd"], -14000)
        self.assertEqual(expected["next_home_comparison"]["sale_cpf_refund_surplus_or_gap_sgd"], 30000)
        self.assertEqual(result["scenarios"]["conservative"]["estimated_cash_proceeds_sgd"], 206000)

    def test_cpf_shortfall_at_market_value_is_separate_from_loan_shortfall(self):
        result = seller.build({"expected_sale_price_sgd": 300000,
                               "outstanding_loan_sgd": 100000,
                               "cpf_principal_used_sgd": 210000,
                               "cpf_accrued_interest_sgd": 40000})
        row = result["scenarios"]["expected"]
        self.assertEqual(row["cpf_refund_from_sale_sgd"], 200000)
        self.assertEqual(row["cpf_refund_shortfall_sgd"], 50000)
        self.assertEqual(row["loan_cash_top_up_needed_sgd"], 0)
        self.assertEqual(row["estimated_cash_proceeds_sgd"], 0)

    def test_loan_and_fee_shortfalls_are_flagged(self):
        result = seller.build({"expected_sale_price_sgd": 200000,
                               "outstanding_loan_sgd": 210000,
                               "cpf_refund_required_sgd": 50000,
                               "legal_fee_sgd": 1000})
        row = result["scenarios"]["expected"]
        self.assertEqual(row["loan_cash_top_up_needed_sgd"], 10000)
        self.assertEqual(row["selling_costs_cash_top_up_needed_sgd"], 1000)
        self.assertEqual(row["cpf_refund_from_sale_sgd"], 0)
        self.assertEqual(row["estimated_cash_proceeds_sgd"], 0)

    def test_every_scenario_reconciles_to_sale_price(self):
        result = seller.build({"expected_sale_price_sgd": 600000,
                               "outstanding_loan_sgd": 180000,
                               "cpf_refund_required_sgd": 250000,
                               "agent_fee_sgd": 11000})
        for row in result["scenarios"].values():
            self.assertAlmostEqual(row["sale_price_sgd"],
                row["loan_paid_from_sale_sgd"] + row["cpf_refund_from_sale_sgd"] +
                row["selling_costs_paid_from_sale_sgd"] + row["estimated_cash_proceeds_sgd"])

    def test_rejects_conflicting_or_incomplete_inputs(self):
        base = {"expected_sale_price_sgd": 600000, "outstanding_loan_sgd": 200000,
                "cpf_refund_required_sgd": 150000}
        with self.assertRaises(ValueError):
            seller.build(base | {"cpf_principal_used_sgd": 100000})
        with self.assertRaises(ValueError):
            seller.build(base | {"conservative_sale_price_sgd": 610000})
        with self.assertRaises(ValueError):
            seller.build(base | {"next_home_cash_needed_sgd": 200000})
        with self.assertRaises(ValueError):
            seller.build(base | {"legal_fee_sgd": -1})


if __name__ == "__main__":
    unittest.main()
