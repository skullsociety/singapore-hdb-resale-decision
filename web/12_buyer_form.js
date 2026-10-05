"use strict";

const form = document.querySelector("#buyer-form");
const loanChoice = form.elements.loan_type;
const resultSection = document.querySelector("#result");
const errorBox = document.querySelector("#form-error");
const submitButton = document.querySelector("#calculate-button");
let lastResult = null;
let lastValidation = null;
const validationSection = document.querySelector("#validation");
const validationForm = document.querySelector("#validation-form");

const integerMoney = new Intl.NumberFormat("en-SG", {style: "currency", currency: "SGD", maximumFractionDigits: 0});
const decimalMoney = new Intl.NumberFormat("en-SG", {style: "currency", currency: "SGD", minimumFractionDigits: 2});
const cash = value => value == null ? "—" : integerMoney.format(value);
const monthly = value => value == null ? "—" : decimalMoney.format(value);
const numberFields = [
  "asking_price_sgd", "hdb_value_sgd", "proposed_offer_sgd", "fair_value_lower_sgd",
  "fair_value_upper_sgd", "absd_rate_pct", "annual_interest_rate_pct", "loan_years",
  "loan_amount_sgd", "ltv_limit_pct", "bank_minimum_cash_pct", "monthly_other_debt_sgd",
  "cash_available_sgd", "cpf_oa_available_sgd", "cpf_eligible_limit_sgd",
  "monthly_household_income_sgd", "monthly_housing_budget_sgd", "renovation_sgd",
  "legal_sgd", "valuation_fee_sgd", "insurance_sgd", "moving_sgd",
  "other_upfront_sgd", "other_monthly_sgd", "option_deposit_sgd"
];

function updateLoanFields() {
  const withLoan = loanChoice.value === "hdb" || loanChoice.value === "bank";
  document.querySelector("#loan-fields").classList.toggle("hidden", !withLoan);
  for (const name of ["annual_interest_rate_pct", "loan_years"]) {
    form.elements[name].required = withLoan;
    form.elements[name].disabled = !withLoan;
  }
  for (const name of ["loan_amount_sgd", "ltv_limit_pct"]) {
    form.elements[name].disabled = !withLoan;
  }
  form.elements.bank_minimum_cash_pct.disabled = loanChoice.value !== "bank";
  form.elements.monthly_other_debt_sgd.disabled = loanChoice.value !== "bank";
  if (loanChoice.value === "hdb") form.elements.loan_years.max = 25;
  else form.elements.loan_years.max = 30;
}
loanChoice.addEventListener("change", updateLoanFields);
updateLoanFields();

document.querySelector("#assume-value").addEventListener("click", () => {
  const price = form.elements.asking_price_sgd.value;
  if (!price || Number(price) <= 0) {
    form.elements.asking_price_sgd.focus();
    showError("Enter the asking price first, then use it as a value assumption.");
    return;
  }
  form.elements.hdb_value_sgd.value = price;
  form.elements.value_status.value = "assumed";
  hideError();
});

document.querySelector("#estimate-file").addEventListener("change", async event => {
  const file = event.target.files?.[0];
  if (!file) return;
  const status = document.querySelector("#import-status");
  try {
    if (file.size > 2_000_000) throw new Error("The report is too large to import.");
    const data = JSON.parse(await file.text());
    const lower = Number(data.lower_estimate_sgd);
    const upper = Number(data.upper_estimate_sgd);
    if (data.status !== "estimate" || !Number.isFinite(lower) || !Number.isFinite(upper) || lower <= 0 || upper < lower) {
      throw new Error("Choose a Step 10 report with a valid price range.");
    }
    form.elements.fair_value_lower_sgd.value = lower;
    form.elements.fair_value_upper_sgd.value = upper;
    form.elements.fair_value_lower_sgd.closest("details").open = true;
    status.textContent = `Imported the Step 10 range: ${cash(lower)} to ${cash(upper)}. It is separate from HDB value.`;
  } catch (error) {
    status.textContent = error.message || "Could not read the estimate report.";
  } finally {
    event.target.value = "";
  }
});

function showError(message) {
  errorBox.textContent = message;
  errorBox.classList.remove("hidden");
  errorBox.scrollIntoView({behavior: "smooth", block: "center"});
}
function hideError() {
  errorBox.textContent = "";
  errorBox.classList.add("hidden");
}

function collectInputs() {
  const data = {loan_type: loanChoice.value, value_status: form.elements.value_status.value};
  for (const name of numberFields) {
    const field = form.elements[name];
    if (field.disabled || field.value.trim() === "") continue;
    const parsed = Number(field.value);
    if (!Number.isFinite(parsed)) throw new Error(`Please enter a valid number for ${name.replaceAll("_", " ")}.`);
    data[name] = parsed;
  }
  const alternatives = form.elements.comparison_prices_sgd.value.trim();
  if (alternatives) {
    data.comparison_prices_sgd = alternatives.split(",").map(part => {
      const parsed = Number(part.trim());
      if (!part.trim() || !Number.isFinite(parsed) || parsed <= 0) throw new Error("Separate comparison prices with commas, for example 550000, 625000.");
      return parsed;
    });
  }
  return data;
}

function addMetric(parent, label, value, note, emphasis = false) {
  const card = document.createElement("div");
  card.className = emphasis ? "metric emphasis" : "metric";
  for (const [tag, valueText] of [["span", label], ["strong", value], ["small", note]]) {
    const element = document.createElement(tag);
    element.textContent = valueText;
    card.append(element);
  }
  parent.append(card);
}

function addRow(parent, cells) {
  const row = document.createElement("tr");
  for (const value of cells) {
    const cell = document.createElement("td");
    cell.textContent = value;
    row.append(cell);
  }
  parent.append(row);
}

function addBreakdown(parent, label, value) {
  const name = document.createElement("dt");
  const amount = document.createElement("dd");
  name.textContent = label;
  amount.textContent = value;
  parent.append(name, amount);
}

function renderResult(data) {
  lastResult = data;
  lastValidation = null;
  document.querySelector("#validation-result").classList.add("hidden");
  const scenarioChoice = document.querySelector("#validation-scenario");
  scenarioChoice.replaceChildren();
  for (const [name, scenario] of Object.entries(data.scenarios)) {
    const option = document.createElement("option");
    option.value = name;
    const title = name === "asking" ? "Asking price" : name === "offer" ? "Your offer" : `Alternative ${name.split("_")[1]}`;
    option.textContent = `${title} · ${cash(scenario.purchase_price_sgd)}`;
    scenarioChoice.append(option);
  }
  validationSection.classList.remove("hidden");
  const asking = data.scenarios.asking;
  const cards = document.querySelector("#result-cards");
  cards.replaceChildren();
  addMetric(cards, "Initial cash needed", cash(asking.total_initial_cash_needed_sgd), "Includes cash for price and upfront costs", true);
  addMetric(cards, "CPF used for price", cash(asking.cpf_oa_used_for_price_sgd), "Based on the limit you entered");
  addMetric(cards, "Monthly housing outflow", monthly(asking.monthly_housing_outflow_sgd), "New home-loan payment plus housing costs; other debts excluded");
  addMetric(cards, "Cash shortfall", cash(asking.cash_shortfall_sgd), asking.within_available_cash ? "Covered by entered cash" : "More cash than entered is needed");

  const valueMessage = data.buyer_inputs.value_status === "official" ? "using the entered official HDB value" : "using an assumed HDB value";
  const rangeMessage = data.asking_position_vs_fair_range ? ` · Asking price is ${data.asking_position_vs_fair_range} the Step 10 fair range` : "";
  const ceiling = data.affordability_screen ? ` · Indicative ceiling ${cash(data.affordability_screen.indicative_max_price_sgd)}` : "";
  document.querySelector("#result-subtitle").textContent = `Planning estimate ${valueMessage}${rangeMessage}${ceiling}.`;

  const breakdown = document.querySelector("#breakdown");
  breakdown.replaceChildren();
  for (const [label, value] of [
    ["Purchase price", asking.purchase_price_sgd], ["HDB value entered", asking.hdb_value_sgd],
    ["Cash over value", asking.cash_over_value_sgd], ["Loan used", asking.loan_used_sgd],
    ["CPF used for price", asking.cpf_oa_used_for_price_sgd], ["Cash for price", asking.cash_for_price_sgd],
    ["Buyer's Stamp Duty", asking.buyers_stamp_duty_sgd],
    ["Additional Buyer's Stamp Duty", asking.additional_buyers_stamp_duty_sgd],
    ["Option deposit (within cash for price)", asking.option_deposit_cash_within_price_sgd],
    ["Cash still due after option deposit", asking.cash_due_after_option_deposit_sgd],
    ["Five-year gross cash and CPF outflow", asking.five_year_cash_and_cpf_outflow_sgd]
  ]) addBreakdown(breakdown, label, cash(value));
  for (const [key, value] of Object.entries(asking.other_upfront_costs_sgd)) {
    if (value) addBreakdown(breakdown, key.replaceAll("_", " ").replace(" sgd", ""), cash(value));
  }

  const comparisons = document.querySelector("#comparison-body");
  comparisons.replaceChildren();
  for (const [name, scenario] of Object.entries(data.scenarios)) {
    const label = name === "asking" ? "Asking" : name === "offer" ? "Your offer" : `Alternative ${name.split("_")[1]}`;
    addRow(comparisons, [`${label} · ${cash(scenario.purchase_price_sgd)}`, cash(scenario.total_initial_cash_needed_sgd), monthly(scenario.monthly_housing_outflow_sgd), cash(scenario.cash_shortfall_sgd)]);
  }
  const sensitivity = document.querySelector("#sensitivity-body");
  sensitivity.replaceChildren();
  for (const item of data.sensitivity_at_asking) {
    addRow(sensitivity, [item.case.replaceAll("_", " "), cash(item.initial_cash_sgd), monthly(item.monthly_housing_outflow_sgd)]);
  }
  const warnings = document.querySelector("#warnings");
  warnings.replaceChildren();
  for (const warning of data.warnings) {
    const item = document.createElement("li");
    item.textContent = warning;
    warnings.append(item);
  }
  resultSection.classList.remove("hidden");
  resultSection.scrollIntoView({behavior: "smooth", block: "start"});
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  hideError();
  if (!form.reportValidity()) return;
  submitButton.disabled = true;
  submitButton.textContent = "Calculating…";
  try {
    const response = await fetch("/calculate", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(collectInputs()), cache: "no-store"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "The calculation could not be completed.");
    renderResult(data);
  } catch (error) {
    resultSection.classList.add("hidden");
    showError(error.message || "Please check the entries and try again.");
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "Calculate my plan →";
  }
});

function clearDocumentComparison() {
  lastValidation = null;
  document.querySelector("#validation-result").classList.add("hidden");
}
validationForm.addEventListener("input", clearDocumentComparison);
document.querySelector("#validation-scenario").addEventListener("change", clearDocumentComparison);

validationForm.addEventListener("submit", event => {
  event.preventDefault();
  const error = document.querySelector("#validation-error");
  error.classList.add("hidden");
  const figures = {};
  for (const key of Object.keys(window.BuyerValidation.comparisonFields)) {
    const field = validationForm.elements[key];
    if (field.value.trim() === "") continue;
    const amount = Number(field.value);
    if (!Number.isFinite(amount) || amount < 0 || !field.checkValidity()) {
      error.textContent = "Enter valid non-negative document amounts.";
      error.classList.remove("hidden");
      return;
    }
    figures[key] = amount;
  }
  try {
    const compared = window.BuyerValidation.compareDocumentFigures(lastResult, document.querySelector("#validation-scenario").value, figures);
    const body = document.querySelector("#validation-body");
    body.replaceChildren();
    for (const row of compared.rows) {
      const tr = document.createElement("tr");
      for (const value of [row.label, monthly(row.plan_sgd), monthly(row.document_sgd), `${row.difference_sgd > 0 ? "+" : ""}${monthly(row.difference_sgd)}`]) {
        const td = document.createElement("td");
        td.textContent = value;
        tr.append(td);
      }
      tr.lastElementChild.className = row.difference_sgd > 0 ? "delta-positive" : row.difference_sgd < 0 ? "delta-negative" : "";
      body.append(tr);
    }
    document.querySelector("#validation-summary").textContent = `Comparing ${compared.rows.length} document figure${compared.rows.length === 1 ? "" : "s"} with the ${cash(compared.purchase_price_sgd)} plan.`;
    document.querySelector("#validation-result").classList.remove("hidden");
    lastValidation = compared;
  } catch (problem) {
    error.textContent = problem.message || "Could not compare those figures.";
    error.classList.remove("hidden");
    document.querySelector("#validation-result").classList.add("hidden");
    lastValidation = null;
  }
});

document.querySelector("#download-button").addEventListener("click", () => {
  if (!lastResult) return;
  const blob = new Blob([JSON.stringify(lastValidation ? {...lastResult, document_comparison: lastValidation} : lastResult, null, 2) + "\n"], {type: "application/json"});
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "12_buyer_plan.json";
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
