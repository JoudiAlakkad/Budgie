// Settings: monthly budgets per spending category (GET/PUT /budgets) and the savings goal
// (GET/PUT /goal). No item-category table (F08 decision). A goal can't be deleted: the
// contract has no DELETE /goal.
import { ApiError, api, clearError, showError } from "./api.js";
import { SPENDING_CATEGORIES, categoryLabel } from "./categories.js";
import { h, icon } from "./dom.js";

const budgetsForm = document.getElementById("budgets-form");
const budgetRows = document.getElementById("budget-rows");
const budgetsSave = document.getElementById("budgets-save");
const budgetsStatus = document.getElementById("budgets-status");

const goalForm = document.getElementById("goal-form");
const goalEmpty = document.getElementById("goal-empty");
const goalSave = document.getElementById("goal-save");
const goalStatus = document.getElementById("goal-status");
const goalInputs = {
  target_amount: document.getElementById("goal-target-amount"),
  target_date: document.getElementById("goal-target-date"),
  monthly_income: document.getElementById("goal-monthly-income"),
};

const AMOUNT_MESSAGE = "Enter an amount of at least 0.01 with at most two decimals.";

/** One number input per spending category, keyed by category. */
const budgetInputs = new Map();

// ---------------------------------------------------------------- field errors

function describedBy(input, add, remove) {
  const ids = (input.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);
  const next = ids.filter((id) => id !== remove);
  if (add) next.push(add);
  if (next.length) input.setAttribute("aria-describedby", next.join(" "));
  else input.removeAttribute("aria-describedby");
}

function clearFieldErrors(form) {
  for (const node of form.querySelectorAll(".field-error, .form-errors")) node.remove();
  for (const input of form.querySelectorAll("[aria-invalid]")) {
    input.removeAttribute("aria-invalid");
    describedBy(input, null, `${input.id}-error`);
  }
}

function setFieldError(input, message) {
  const id = `${input.id}-error`;
  let node = document.getElementById(id);
  if (node) {
    node.append(` ${message}`);
  } else {
    node = h("span", { className: "field-error", id }, icon("✖"), " ", message);
    input.closest(".field").append(node);
    describedBy(input, id, id);
  }
  input.setAttribute("aria-invalid", "true");
}

/** Errors that belong to no input (e.g. `body`) are listed above the form's buttons. */
function showFormErrors(form, messages) {
  if (!messages.length) return;
  const list = h(
    "ul",
    { className: "form-errors" },
    messages.map((message) => h("li", { className: "field-error" }, icon("✖"), " ", message)),
  );
  form.querySelector(".actions").before(list);
}

function focusFirstInvalid(form) {
  const invalid = form.querySelector("[aria-invalid='true']");
  if (invalid) invalid.focus();
}

/** "" for an empty input, null for an unusable value, otherwise the number. */
function amountValue(input) {
  if (input.validity.badInput) return null;
  if (input.value.trim() === "") return "";
  if (!input.validity.valid) return null;
  const value = Number(input.value);
  return Number.isFinite(value) ? value : null;
}

function amountText(value) {
  return value === null || value === undefined ? "" : Number(value).toFixed(2);
}

// ---------------------------------------------------------------- budgets

function budgetRow(category) {
  const id = `budget-${category.replace(/\W/g, "-")}`;
  const input = h("input", {
    id,
    type: "number",
    inputMode: "decimal",
    min: "0.01",
    step: "0.01",
    attrs: { "aria-describedby": "budgets-hint" },
  });
  budgetInputs.set(category, input);
  return h(
    "div",
    { className: "field budget-row" },
    h("label", { htmlFor: id }, categoryLabel(category)),
    h("span", { className: "budget-input" }, input, h("span", { className: "unit", attrs: { "aria-hidden": "true" } }, "€")),
  );
}

function fillBudgets(budgets) {
  for (const input of budgetInputs.values()) input.value = "";
  for (const budget of budgets) {
    const input = budgetInputs.get(budget.category);
    if (input) input.value = amountText(budget.monthly_limit);
  }
}

async function loadBudgets() {
  budgetRows.replaceChildren(...SPENDING_CATEGORIES.map(budgetRow));
  try {
    fillBudgets(await api.get("/budgets"));
    budgetsSave.disabled = false;
  } catch (err) {
    showError(err);
    budgetsStatus.textContent = "Budgets could not be loaded.";
    for (const input of budgetInputs.values()) input.disabled = true;
  } finally {
    budgetRows.removeAttribute("aria-busy");
  }
}

/** Maps `fields[].field` like "3.monthly_limit" back through the sent list to its input. */
function showBudgetErrors(err, sent) {
  const unmatched = [];
  for (const { field, message } of err.fields || []) {
    const index = /^(\d+)(\.|$)/.exec(String(field));
    const budget = index ? sent[Number(index[1])] : null;
    const input = budget ? budgetInputs.get(budget.category) : null;
    if (input) setFieldError(input, message);
    else unmatched.push(`${field}: ${message}`);
  }
  showFormErrors(budgetsForm, unmatched);
}

async function saveBudgets(event) {
  event.preventDefault();
  clearError();
  clearFieldErrors(budgetsForm);
  budgetsStatus.textContent = "";

  const sent = [];
  for (const [category, input] of budgetInputs) {
    const value = amountValue(input);
    if (value === null) setFieldError(input, AMOUNT_MESSAGE);
    else if (value !== "") sent.push({ category, monthly_limit: value });
  }
  if (budgetsForm.querySelector("[aria-invalid='true']")) {
    budgetsStatus.textContent = "Not saved: check the marked fields.";
    focusFirstInvalid(budgetsForm);
    return;
  }

  budgetsSave.disabled = true;
  budgetsStatus.textContent = "Saving…";
  try {
    fillBudgets(await api.put("/budgets", sent));
    budgetsStatus.textContent = "Budgets saved.";
  } catch (err) {
    budgetsStatus.textContent = "Not saved.";
    showError(err);
    if (err instanceof ApiError && err.fields) {
      showBudgetErrors(err, sent);
      focusFirstInvalid(budgetsForm);
    }
  } finally {
    budgetsSave.disabled = false;
  }
}

// ---------------------------------------------------------------- goal

function setGoalDisabled(disabled) {
  for (const input of Object.values(goalInputs)) input.disabled = disabled;
  goalSave.disabled = disabled;
}

function fillGoal(goal) {
  goalInputs.target_amount.value = amountText(goal.target_amount);
  goalInputs.target_date.value = goal.target_date || "";
  goalInputs.monthly_income.value = amountText(goal.monthly_income);
}

async function loadGoal() {
  try {
    fillGoal(await api.get("/goal"));
    goalEmpty.hidden = true;
    setGoalDisabled(false);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      goalEmpty.hidden = false;
      setGoalDisabled(false);
    } else {
      showError(err);
      goalStatus.textContent = "The goal could not be loaded.";
    }
  }
}

function showGoalErrors(err) {
  const unmatched = [];
  for (const { field, message } of err.fields || []) {
    const input = goalInputs[String(field).split(".")[0]];
    if (input) setFieldError(input, message);
    else unmatched.push(`${field}: ${message}`);
  }
  showFormErrors(goalForm, unmatched);
}

async function saveGoal(event) {
  event.preventDefault();
  clearError();
  clearFieldErrors(goalForm);
  goalStatus.textContent = "";

  const target = amountValue(goalInputs.target_amount);
  if (target === null || target === "") setFieldError(goalInputs.target_amount, AMOUNT_MESSAGE);
  const date = goalInputs.target_date.value;
  if (!date || !goalInputs.target_date.validity.valid) {
    setFieldError(goalInputs.target_date, "Enter a target date.");
  }
  const income = amountValue(goalInputs.monthly_income);
  if (income === null) {
    setFieldError(goalInputs.monthly_income, "Enter an amount of 0 or more with at most two decimals, or leave it empty.");
  }
  if (goalForm.querySelector("[aria-invalid='true']")) {
    goalStatus.textContent = "Not saved: check the marked fields.";
    focusFirstInvalid(goalForm);
    return;
  }

  const body = { target_amount: target, target_date: date, monthly_income: income === "" ? null : income };
  goalSave.disabled = true;
  goalStatus.textContent = "Saving…";
  try {
    fillGoal(await api.put("/goal", body));
    goalEmpty.hidden = true;
    goalStatus.textContent = "Goal saved.";
  } catch (err) {
    goalStatus.textContent = "Not saved.";
    showError(err);
    if (err instanceof ApiError && err.fields) {
      showGoalErrors(err);
      focusFirstInvalid(goalForm);
    }
  } finally {
    goalSave.disabled = false;
  }
}

budgetsForm.addEventListener("submit", saveBudgets);
goalForm.addEventListener("submit", saveGoal);
loadBudgets();
loadGoal();
