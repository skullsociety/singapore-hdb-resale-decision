"use strict";

const form = document.querySelector("#seller-form");
const resultSection = document.querySelector("#seller-result");
const errorBox = document.querySelector("#form-error");
const submitButton = document.querySelector("#calculate-button");
let lastResult = null;

const currency = new Intl.NumberFormat("en-SG", {style: "currency", currency: "SGD", minimumFractionDigits: 2});
const dollars = value => currency.format(value);
const numberFields = [
  "expected_sale_price_sgd", "conservative_sale_price_sgd", "optimistic_sale_price_sgd",
  "outstanding_loan_sgd", "cpf_refund_required_sgd", "cpf_principal_used_sgd",
  "cpf_accrued_interest_sgd", "cpf_other_refund_sgd", "agent_fee_sgd",
  "legal_fee_sgd", "resale_levy_sgd", "upgrading_cost_sgd", "other_selling_costs_sgd",
  "next_home_cash_needed_sgd", "next_home_cpf_needed_sgd"
];

function collectInputs() {
  const data = {};
  for (const name of numberFields) {
    const value = form.elements[name].value.trim();
    if (value === "") continue;
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed < 0) throw new Error("Enter valid non-negative amounts.");
    data[name] = parsed;
  }
  return data;
}

function metric(parent, label, value, note, emphasis = false) {
  const box = document.createElement("div");
  box.className = emphasis ? "metric emphasis" : "metric";
  for (const [tag, content] of [["span", label], ["strong", dollars(value)], ["small", note]]) {
    const element = document.createElement(tag);
    element.textContent = content;
    box.append(element);
  }
  parent.append(box);
}

function line(parent, label, value) {
  const title = document.createElement("dt");
  const amount = document.createElement("dd");
  title.textContent = label;
  amount.textContent = dollars(value);
  parent.append(title, amount);
}

function row(parent, cells) {
  const tr = document.createElement("tr");
  for (const value of cells) {
    const td = document.createElement("td");
    td.textContent = value;
    tr.append(td);
  }
  parent.append(tr);
}

function render(data) {
  lastResult = data;
  const middle = data.scenarios.expected;
  const cards = document.querySelector("#result-cards");
  cards.replaceChildren();
  metric(cards, "Estimated cash proceeds", middle.estimated_cash_proceeds_sgd, "Cash after loan, CPF refund and entered costs", true);
  metric(cards, "Returned to CPF", middle.cpf_refund_from_sale_sgd, "Not automatically cash you can spend");
  metric(cards, "Selling costs", middle.total_selling_costs_sgd, "The amounts you entered");
  metric(cards, "Cash top-up risk", middle.loan_cash_top_up_needed_sgd + middle.selling_costs_cash_top_up_needed_sgd, "Loan and cost shortages; CPF shortfall shown below");
  document.querySelector("#result-subtitle").textContent = "At your expected sale price of " + dollars(middle.sale_price_sgd) + ".";
  const breakdown = document.querySelector("#breakdown");
  breakdown.replaceChildren();
  for (const [label, value] of [
    ["Sale price", middle.sale_price_sgd],
    ["Housing loan paid from sale", middle.loan_paid_from_sale_sgd],
    ["Loan cash top-up needed", middle.loan_cash_top_up_needed_sgd],
    ["CPF refund required", middle.cpf_refund_required_sgd],
    ["CPF returned from sale", middle.cpf_refund_from_sale_sgd],
    ["CPF refund shortfall", middle.cpf_refund_shortfall_sgd],
    ["Selling costs paid from sale", middle.selling_costs_paid_from_sale_sgd],
    ["Selling costs cash top-up", middle.selling_costs_cash_top_up_needed_sgd],
    ["Estimated cash proceeds", middle.estimated_cash_proceeds_sgd]
  ]) line(breakdown, label, value);
  for (const [key, value] of Object.entries(middle.selling_costs_sgd)) {
    if (value > 0) line(breakdown, key.replaceAll("_", " ").replace(" sgd", ""), value);
  }
  const scenarioBody = document.querySelector("#scenario-body");
  scenarioBody.replaceChildren();
  for (const [name, scenario] of Object.entries(data.scenarios)) {
    row(scenarioBody, [
      name[0].toUpperCase() + name.slice(1) + " · " + dollars(scenario.sale_price_sgd),
      dollars(scenario.estimated_cash_proceeds_sgd),
      dollars(scenario.cpf_refund_from_sale_sgd),
      dollars(scenario.loan_cash_top_up_needed_sgd + scenario.selling_costs_cash_top_up_needed_sgd)
    ]);
  }
  const nextHome = document.querySelector("#next-home-result");
  const nextBody = document.querySelector("#next-home-body");
  nextBody.replaceChildren();
  if (middle.next_home_comparison) {
    for (const [name, scenario] of Object.entries(data.scenarios)) {
      const comparison = scenario.next_home_comparison;
      row(nextBody, [
        name[0].toUpperCase() + name.slice(1),
        dollars(comparison.sale_cash_surplus_or_gap_sgd),
        dollars(comparison.sale_cpf_refund_surplus_or_gap_sgd)
      ]);
    }
    nextHome.classList.remove("hidden");
  } else {
    nextHome.classList.add("hidden");
  }
  const warnings = document.querySelector("#warnings");
  warnings.replaceChildren();
  for (const message of data.warnings) {
    const item = document.createElement("li");
    item.textContent = message;
    warnings.append(item);
  }
  resultSection.classList.remove("hidden");
  resultSection.scrollIntoView({behavior: "smooth", block: "start"});
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  errorBox.classList.add("hidden");
  if (!form.reportValidity()) return;
  submitButton.disabled = true;
  submitButton.textContent = "Calculating…";
  try {
    const response = await fetch("/seller/calculate", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(collectInputs()), cache: "no-store"
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not calculate sale proceeds.");
    render(data);
  } catch (error) {
    resultSection.classList.add("hidden");
    errorBox.textContent = error.message || "Please check your entries.";
    errorBox.classList.remove("hidden");
    errorBox.scrollIntoView({behavior: "smooth", block: "center"});
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "Calculate sale proceeds →";
  }
});

document.querySelector("#buyer-plan-file").addEventListener("change", async event => {
  const file = event.target.files?.[0];
  if (!file) return;
  const status = document.querySelector("#import-status");
  try {
    if (file.size > 2_000_000) throw new Error("The buyer report is too large.");
    const data = JSON.parse(await file.text());
    const asking = data.scenarios?.asking;
    const cashNeed = asking?.total_initial_cash_needed_sgd;
    const cpfNeed = asking?.cpf_oa_used_for_price_sgd;
    if (data.status !== "planning_estimate" || !Number.isFinite(cashNeed) ||
        !Number.isFinite(cpfNeed) || cashNeed < 0 || cpfNeed < 0) {
      throw new Error("Choose a valid Step 12 buyer plan JSON.");
    }
    form.elements.next_home_cash_needed_sgd.value = cashNeed;
    form.elements.next_home_cpf_needed_sgd.value = cpfNeed;
    status.textContent = "Loaded the buyer plan's asking-price cash and CPF needs. Review both figures before calculating.";
  } catch (error) {
    status.textContent = error.message || "Could not read that buyer plan.";
  } finally {
    event.target.value = "";
  }
});

document.querySelector("#download-button").addEventListener("click", () => {
  if (!lastResult) return;
  const blob = new Blob([JSON.stringify(lastResult, null, 2) + "\n"], {type: "application/json"});
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "14_seller_plan.json";
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

