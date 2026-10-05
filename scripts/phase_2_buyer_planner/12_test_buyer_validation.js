"use strict";

const assert = require("node:assert/strict");
const {test} = require("node:test");
const {compareDocumentFigures} = require("../../web/12_buyer_validation.js");

const plan = {scenarios: {
  asking: {purchase_price_sgd: 600000, hdb_value_sgd: 580000, loan_used_sgd: 435000,
    cpf_oa_used_for_price_sgd: 100000, monthly_loan_payment_sgd: 2000,
    buyers_stamp_duty_sgd: 12600, additional_buyers_stamp_duty_sgd: 0,
    total_initial_cash_needed_sgd: 77600},
  offer: {purchase_price_sgd: 590000, hdb_value_sgd: 580000, loan_used_sgd: 435000,
    cpf_oa_used_for_price_sgd: 100000, monthly_loan_payment_sgd: 2000,
    buyers_stamp_duty_sgd: 12300, additional_buyers_stamp_duty_sgd: 0,
    total_initial_cash_needed_sgd: 67300}
}};

test("compares documents with the chosen scenario and keeps zero values", () => {
  const result = compareDocumentFigures(plan, "offer", {
    buyers_stamp_duty_sgd: 12350, additional_buyers_stamp_duty_sgd: 0
  });
  assert.equal(result.purchase_price_sgd, 590000);
  assert.equal(result.rows[0].difference_sgd, 50);
  assert.equal(result.rows[1].difference_sgd, 0);
  assert.equal(result.rows.length, 2);
});

test("rejects missing scenario, missing documents, and invalid amounts", () => {
  assert.throws(() => compareDocumentFigures(plan, "missing", {hdb_value_sgd: 580000}));
  assert.throws(() => compareDocumentFigures(plan, "asking", {}));
  assert.throws(() => compareDocumentFigures(plan, "asking", {loan_used_sgd: -1}));
});

