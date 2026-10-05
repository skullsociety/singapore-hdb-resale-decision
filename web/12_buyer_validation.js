"use strict";

const comparisonFields = {
  hdb_value_sgd: "HDB value",
  loan_used_sgd: "Housing loan amount",
  cpf_oa_used_for_price_sgd: "CPF used for price",
  monthly_loan_payment_sgd: "Monthly loan repayment",
  buyers_stamp_duty_sgd: "Buyer's Stamp Duty",
  additional_buyers_stamp_duty_sgd: "Additional Buyer's Stamp Duty",
  total_initial_cash_needed_sgd: "Total initial cash"
};

function compareDocumentFigures(plan, scenarioName, documents) {
  const scenario = plan?.scenarios?.[scenarioName];
  if (!scenario) throw new Error("Choose a calculated price scenario first.");
  const rows = [];
  for (const [key, label] of Object.entries(comparisonFields)) {
    if (!Object.hasOwn(documents, key)) continue;
    const documented = documents[key];
    const estimated = scenario[key];
    if (!Number.isFinite(documented) || documented < 0 || !Number.isFinite(estimated)) {
      throw new Error("Document figures must be valid non-negative amounts.");
    }
    rows.push({key, label, plan_sgd: estimated, document_sgd: documented,
      difference_sgd: Math.round((documented - estimated) * 100) / 100});
  }
  if (!rows.length) throw new Error("Enter at least one figure from your documents.");
  return {scenario: scenarioName, purchase_price_sgd: scenario.purchase_price_sgd, rows};
}

const BuyerValidation = {comparisonFields, compareDocumentFigures};
if (typeof window !== "undefined") window.BuyerValidation = BuyerValidation;
if (typeof module !== "undefined") module.exports = BuyerValidation;
