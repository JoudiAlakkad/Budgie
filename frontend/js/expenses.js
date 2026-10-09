// Expenses page: list, filter, open, delete and export expenses
// (docs/wiki/frontend/pages.md, "Expenses page rules"; decision 0024 for the CSV).
import { ApiError, api, clearError, formatMoney, showError } from "./api.js";
import { CATEGORIES, UNCATEGORIZED, categoryLabel } from "./categories.js";
import { REVIEW_STATUS_WORDS, SOURCE_WORDS, formatDate, h, icon } from "./dom.js";

const DATE_PATTERN = /^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$/;
const UNKNOWN = "unknown";

// Filter names are the query parameters of `GET /expenses`, in the address too.
const FILTER_NAMES = ["from", "to", "category", "confirmed", "review_status", "has_receipt"];
const EXPORT_FILTERS = ["from", "to"];
const BOOLEAN_VALUES = new Set(["true", "false"]);
const CATEGORY_VALUES = new Set([...CATEGORIES, UNCATEGORIZED]);

// Words first, icon second; colour is never the only signal.
const CONFIRMED_TEXT = {
  true: { symbol: "✓", text: "Confirmed" },
  false: { symbol: "✎", text: "Draft" },
};
const REVIEW_SYMBOLS = { accepted: "✓", needs_review: "⚠", rejected: "✖" };

const form = document.getElementById("filters");
const inputs = {
  from: document.getElementById("filter-from"),
  to: document.getElementById("filter-to"),
  category: document.getElementById("filter-category"),
  confirmed: document.getElementById("filter-confirmed"),
  review_status: document.getElementById("filter-review"),
  has_receipt: document.getElementById("filter-type"),
};
const clearButton = document.getElementById("filters-clear");
const rangeHint = document.getElementById("range-hint");
const listContainer = document.getElementById("list");
const listHeading = document.getElementById("list-heading");
const listStatus = document.getElementById("list-status");
const exportButton = document.getElementById("export-button");
const exportRange = document.getElementById("export-range");
const exportStatus = document.getElementById("export-status");
const dialog = document.getElementById("delete-dialog");
const dialogWhat = document.getElementById("delete-what");
const dialogPhoto = document.getElementById("delete-photo");

const state = {
  errorCount: 0, // bumped by every error shown, so an older list request doesn't clear it
  requestId: 0, // only the newest list request may draw
  pendingDelete: null, // the expense the dialog asks about
  downloading: false,
  // `errorCount` right after the download's own error, so the next download clears only
  // that one and never a list or delete error shown since.
  downloadErrorAt: null,
  // "Deleted: …" waiting for the next load that draws the list; it goes before that count.
  announcement: "",
};

// ---------------------------------------------------------------- filters

function fillSelects() {
  inputs.category.replaceChildren(
    h("option", { value: "" }, "All categories"),
    ...CATEGORIES.map((category) => h("option", { value: category }, categoryLabel(category))),
    h("option", { value: UNCATEGORIZED }, categoryLabel(UNCATEGORIZED)),
  );
  inputs.review_status.replaceChildren(
    h("option", { value: "" }, "All"),
    ...Object.entries(REVIEW_STATUS_WORDS).map(([value, words]) => h("option", { value }, words)),
  );
}

/** A filter value the API accepts, or "" (an unknown value from the address is dropped). */
function validValue(name, value) {
  if (typeof value !== "string" || value === "") return "";
  switch (name) {
    case "from":
    case "to":
      return DATE_PATTERN.test(value) ? value : "";
    case "category":
      return CATEGORY_VALUES.has(value) ? value : "";
    case "review_status":
      return Object.hasOwn(REVIEW_STATUS_WORDS, value) ? value : "";
    case "confirmed":
    case "has_receipt":
      return BOOLEAN_VALUES.has(value) ? value : "";
    default:
      return "";
  }
}

function currentFilters() {
  const filters = {};
  for (const name of FILTER_NAMES) {
    const value = validValue(name, inputs[name].value);
    if (value) filters[name] = value;
  }
  return filters;
}

function filtersFromAddress() {
  const params = new URLSearchParams(window.location.search);
  for (const name of FILTER_NAMES) {
    inputs[name].value = validValue(name, params.get(name));
  }
}

/** `?a=1&b=2` for the given names, or "" when none is set. */
function queryFor(filters, names) {
  const params = new URLSearchParams();
  for (const name of names) {
    if (filters[name]) params.set(name, filters[name]);
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

function writeAddress(filters) {
  const query = queryFor(filters, FILTER_NAMES);
  const url = `${window.location.pathname}${query}${window.location.hash}`;
  window.history.replaceState(null, "", url);
}

function rangeText(filters) {
  if (filters.from && filters.to) return `${formatDate(filters.from)} to ${formatDate(filters.to)}`;
  if (filters.from) return `from ${formatDate(filters.from)} on`;
  if (filters.to) return `up to ${formatDate(filters.to)}`;
  return "all dates";
}

function updateHints(filters) {
  const reversed = filters.from && filters.to && filters.from > filters.to;
  rangeHint.hidden = !reversed;
  rangeHint.textContent = reversed ? "From is after To, so no expense can match." : "";
  exportRange.textContent = `Range: ${rangeText(filters)}.`;
}

// ---------------------------------------------------------------- list

function dateCell(expense) {
  if (!expense.date) return h("span", { className: "unknown-value" }, UNKNOWN);
  return h("time", { dateTime: expense.date }, formatDate(expense.date));
}

function categoryLabels(expense) {
  const seen = [];
  for (const item of expense.line_items || []) {
    if (!seen.includes(item.category)) seen.push(item.category);
  }
  if (seen.length === 0) return h("span", { className: "hint" }, "no items");
  return h(
    "ul",
    { className: "category-tags" },
    ...seen.map((category) =>
      h(
        "li",
        { className: category === UNCATEGORIZED ? "category-tag uncategorized" : "category-tag" },
        category === UNCATEGORIZED ? icon("⚠ ") : null,
        categoryLabel(category),
      ),
    ),
  );
}

function statusCell(expense) {
  const confirmed = CONFIRMED_TEXT[String(Boolean(expense.confirmed))];
  const reviewWords = REVIEW_STATUS_WORDS[expense.review_status] || expense.review_status;
  return h(
    "div",
    { className: "expense-status" },
    h(
      "span",
      { className: `status-word ${expense.confirmed ? "is-confirmed" : "is-draft"}` },
      icon(`${confirmed.symbol} `),
      confirmed.text,
    ),
    h(
      "span",
      { className: `review-word review-${expense.review_status}` },
      icon(`${REVIEW_SYMBOLS[expense.review_status] || "•"} `),
      "Review: ",
      reviewWords,
    ),
  );
}

function sourceCell(expense) {
  // AI values are always labelled as such (criterion 19), as plain text (SOURCE_WORDS).
  return h("span", { className: "source-label" }, SOURCE_WORDS[expense.source] || expense.source);
}

/** "REWE on 03.10.2026, 23,47 €", for screen-reader names and the delete dialog. */
function describe(expense) {
  const merchant = expense.merchant || "unknown merchant";
  const date = expense.date ? formatDate(expense.date) : "unknown date";
  const total =
    expense.total === null || expense.total === undefined
      ? "unknown total"
      : formatMoney(expense.total, expense.currency);
  return `${merchant} on ${date}, ${total}`;
}

function openHref(expense) {
  if (expense.receipt_id !== null && expense.receipt_id !== undefined) {
    return `review.html?id=${encodeURIComponent(expense.receipt_id)}`;
  }
  return `review.html?expense=${encodeURIComponent(expense.id)}`;
}

function expenseRow(expense) {
  const label = describe(expense);
  const total =
    expense.total === null || expense.total === undefined
      ? h("span", { className: "unknown-value" }, UNKNOWN)
      : formatMoney(expense.total, expense.currency);
  return h(
    "tr",
    {},
    h("td", { dataset: { label: "Date" } }, dateCell(expense)),
    h(
      "td",
      { className: "merchant", dataset: { label: "Merchant" } },
      expense.merchant || h("span", { className: "unknown-value" }, UNKNOWN),
    ),
    h("td", { className: "num", dataset: { label: "Total" } }, total),
    h("td", { dataset: { label: "Categories" } }, categoryLabels(expense)),
    h("td", { dataset: { label: "Status" } }, statusCell(expense)),
    h("td", { dataset: { label: "Source" } }, sourceCell(expense)),
    h(
      "td",
      { className: "row-actions" },
      h(
        "a",
        { className: "button", href: openHref(expense) },
        "Open",
        h("span", { className: "visually-hidden" }, `: ${label}`),
      ),
      h(
        "button",
        { type: "button", className: "danger", on: { click: () => askDelete(expense) } },
        "Delete",
        h("span", { className: "visually-hidden" }, `: ${label}`),
      ),
    ),
  );
}

function expenseTable(expenses) {
  const headers = ["Date", "Merchant", "Total", "Categories", "Status", "Source", "Actions"];
  return h(
    "table",
    { className: "expense-table" },
    h("caption", { className: "visually-hidden" }, "Expenses, newest date first"),
    h(
      "thead",
      {},
      h(
        "tr",
        {},
        ...headers.map((text) =>
          h("th", { scope: "col", className: text === "Total" ? "num" : null }, text),
        ),
      ),
    ),
    h("tbody", {}, ...expenses.map(expenseRow)),
  );
}

function emptyState(filtered) {
  return h(
    "p",
    { className: "empty-state" },
    filtered ? "No expenses match these filters. " : "No expenses yet. ",
    h("a", { href: "index.html" }, "Upload a receipt"),
  );
}

function countText(count) {
  return count === 1 ? "1 expense" : `${count} expenses`;
}

/**
 * Writes the live region, after any pending delete announcement, which it then clears.
 * Only the newest list request calls this, so the announcement never goes before a
 * stale count and is never lost to a superseded request.
 */
function announce(text) {
  listStatus.textContent = [state.announcement, text].filter(Boolean).join(" ");
  state.announcement = "";
}

/** `showError`, counted, so a list request that started earlier leaves it up. */
function reportError(err) {
  state.errorCount += 1;
  showError(err);
}

async function loadList() {
  const filters = currentFilters();
  writeAddress(filters);
  updateHints(filters);
  const requestId = ++state.requestId;
  const errorsBefore = state.errorCount;
  listContainer.setAttribute("aria-busy", "true");
  let expenses;
  try {
    expenses = await api.get(`/expenses${queryFor(filters, FILTER_NAMES)}`);
  } catch (err) {
    if (requestId !== state.requestId) return;
    listContainer.setAttribute("aria-busy", "false");
    listContainer.replaceChildren(h("p", { className: "hint" }, "The expenses could not be loaded."));
    // A delete that went through is still announced, so it isn't lost to a failed reload.
    announce("");
    reportError(err);
    return;
  }
  if (requestId !== state.requestId) return;
  if (state.errorCount === errorsBefore) clearError();
  listContainer.setAttribute("aria-busy", "false");
  const filtered = Object.keys(filters).length > 0;
  if (expenses.length === 0) {
    listContainer.replaceChildren(emptyState(filtered));
    announce("");
    return;
  }
  // The server's order is kept (newest date first, no date last).
  listContainer.replaceChildren(h("div", { className: "table-wrap" }, expenseTable(expenses)));
  announce(
    filtered ? `${countText(expenses.length)} match these filters.` : `${countText(expenses.length)}.`,
  );
}

// ---------------------------------------------------------------- delete

function askDelete(expense) {
  state.pendingDelete = expense;
  dialogWhat.textContent = describe(expense);
  dialogPhoto.hidden = expense.receipt_id === null || expense.receipt_id === undefined;
  dialog.returnValue = "";
  dialog.showModal();
}

async function deleteExpense(expense) {
  let verb = "Deleted";
  try {
    await api.delete(`/expenses/${encodeURIComponent(expense.id)}`);
  } catch (err) {
    if (!(err instanceof ApiError && err.status === 404)) {
      reportError(err);
      return;
    }
    // 404: someone (another tab, a retry) deleted it already; the reload shows that.
    verb = "Already deleted";
  }
  // Kept in `state` until the newest list request draws, which puts it before its count.
  state.announcement = `${verb}: ${describe(expense)}.`;
  await loadList();
  listHeading.focus();
}

dialog.addEventListener("close", () => {
  const expense = state.pendingDelete;
  state.pendingDelete = null;
  if (expense && dialog.returnValue === "delete") deleteExpense(expense);
});

// ---------------------------------------------------------------- export

async function downloadCsv() {
  if (state.downloading) return;
  state.downloading = true;
  // aria-disabled, not `disabled`: a disabled button drops keyboard focus to <body>.
  // The `state.downloading` guard above ignores clicks meanwhile.
  exportButton.setAttribute("aria-disabled", "true");
  exportStatus.textContent = "Preparing the CSV…";
  // Clear only the download's own earlier error; a list or delete error shown since
  // (which bumped `errorCount`) is still true and stays up.
  if (state.downloadErrorAt !== null && state.downloadErrorAt === state.errorCount) clearError();
  state.downloadErrorAt = null;
  const query = queryFor(currentFilters(), EXPORT_FILTERS);
  try {
    const filename = await api.download(`/expenses/export.csv${query}`);
    exportStatus.textContent = `Downloaded ${filename}.`;
  } catch (err) {
    exportStatus.textContent = "";
    reportError(err);
    state.downloadErrorAt = state.errorCount;
  } finally {
    state.downloading = false;
    exportButton.removeAttribute("aria-disabled");
  }
}

// ---------------------------------------------------------------- wiring

fillSelects();
filtersFromAddress();
form.addEventListener("change", () => loadList());
form.addEventListener("submit", (e) => e.preventDefault());
clearButton.addEventListener("click", () => {
  for (const name of FILTER_NAMES) inputs[name].value = "";
  loadList();
});
exportButton.addEventListener("click", downloadCsv);
loadList();
