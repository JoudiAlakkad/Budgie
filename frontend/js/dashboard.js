// Dashboard: spend vs. budget per category and goal progress for one month
// (GET /insights/summary; rules in decision 0021). Leak cards come in F09.
import { api, clearError, formatMoney, showError } from "./api.js";
import { categoryLabel } from "./categories.js";
import { h, icon } from "./dom.js";

// The insights endpoint sums every currency as EUR (decision 0021).
const CURRENCY = "EUR";
const MONTH_PATTERN = /^\d{4}-(0[1-9]|1[0-2])$/;
const FIRST_MONTH = "0001-01";
const LAST_MONTH = "9999-12";

const container = document.getElementById("dashboard");
const statusLine = document.getElementById("dashboard-status");
const monthInput = document.getElementById("month-input");
const prevButton = document.getElementById("month-prev");
const nextButton = document.getElementById("month-next");

const state = {
  currentMonth: null, // the server's current month (Europe/Berlin), see currentSummary()
  currentPromise: null, // the pending or settled summary asked for without a month
  currentFetchedOn: null, // local date of that request
  month: null, // the month on screen
  requestId: 0, // only the newest request may render
};

// Text first, colour second: every state has words and an icon.
const STATE_TEXT = {
  over: { symbol: "✖", text: "Over budget" },
  on_pace_to_overrun: { symbol: "⚠", text: "On pace to overrun" },
  under: { symbol: "✓", text: "Within budget" },
  none: { symbol: "–", text: "No budget" },
};

// ---------------------------------------------------------------- months

/** A "YYYY-MM" within FIRST_MONTH…LAST_MONTH (year 0000 isn't a valid date). */
function isMonth(value) {
  return (
    typeof value === "string" &&
    MONTH_PATTERN.test(value) &&
    value >= FIRST_MONTH &&
    value <= LAST_MONTH
  );
}

/**
 * "2026-10" moved by `delta` months, e.g. shiftMonth("2026-01", -1) === "2025-12";
 * null when the result would leave FIRST_MONTH…LAST_MONTH.
 */
function shiftMonth(month, delta) {
  const [year, mon] = month.split("-").map(Number);
  const index = year * 12 + (mon - 1) + delta;
  const newYear = Math.floor(index / 12);
  const newMonth = (index % 12) + 1;
  const shifted = `${String(newYear).padStart(4, "0")}-${String(newMonth).padStart(2, "0")}`;
  return isMonth(shifted) ? shifted : null;
}

function updateNavButtons() {
  prevButton.disabled = !state.month || shiftMonth(state.month, -1) === null;
  nextButton.disabled = !state.month || shiftMonth(state.month, 1) === null;
}

function monthName(month) {
  const [year, mon] = month.split("-").map(Number);
  return new Date(Date.UTC(year, mon - 1, 1)).toLocaleDateString("en-GB", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
}

function formatDate(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
  if (!match) return iso || "";
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  return date.toLocaleDateString("de-DE", { dateStyle: "medium", timeZone: "UTC" });
}

function money(amount) {
  return formatMoney(amount, CURRENCY);
}

/** "past", "current" or "future", relative to the server's current month. */
function monthKind(month) {
  if (!state.currentMonth || month === state.currentMonth) return "current";
  return month < state.currentMonth ? "past" : "future";
}

// ---------------------------------------------------------------- rendering

function totalsSection(summary, kind) {
  const budgetValue =
    summary.total_budget === null
      ? h("dd", {}, "No budgets set. ", h("a", { href: "settings.html" }, "Set budgets in Settings"))
      : h("dd", {}, money(summary.total_budget));
  return h(
    "section",
    { className: "totals", attrs: { "aria-labelledby": "totals-heading" } },
    h("h2", { id: "totals-heading" }, "Totals"),
    h(
      "dl",
      { className: "totals-list" },
      h("div", {}, h("dt", {}, "Spent"), h("dd", {}, money(summary.total_spent))),
      h("div", {}, h("dt", {}, "Budget"), budgetValue),
      kind === "current" &&
        h(
          "div",
          {},
          h("dt", {}, "Projected by month end"),
          h("dd", {}, money(summary.projected_total)),
        ),
    ),
  );
}

function stateKey(row) {
  if (row.budget === null) return "none";
  return STATE_TEXT[row.state] ? row.state : "under";
}

/** Fraction of the bar, clamped to 0…1. */
function fraction(value, budget) {
  if (!budget || budget <= 0) return 0;
  return Math.min(Math.max(value / budget, 0), 1);
}

function spendBar(row, showProjection) {
  const bar = h("div", { className: "bar", attrs: { "aria-hidden": "true" } });
  const fill = h("div", { className: "bar-fill" });
  fill.style.width = `${fraction(row.spent, row.budget) * 100}%`;
  bar.append(fill);
  if (showProjection) {
    const marker = h("div", { className: "bar-projection" });
    marker.style.left = `${fraction(row.projected, row.budget) * 100}%`;
    bar.append(marker);
  }
  return bar;
}

function categoryRow(row, kind) {
  const key = stateKey(row);
  const word = STATE_TEXT[key];
  const amount =
    row.budget === null
      ? `${money(row.spent)} · no budget`
      : `${money(row.spent)} of ${money(row.budget)}`;
  const current = kind === "current";
  return h(
    "li",
    { className: `spend-row state-${key}` },
    h(
      "div",
      { className: "spend-head" },
      h("span", { className: "spend-name" }, categoryLabel(row.category)),
      h("span", { className: "spend-amount" }, amount),
    ),
    row.budget !== null && spendBar(row, current),
    h(
      "p",
      { className: "spend-meta" },
      h("span", { className: "state-text" }, icon(word.symbol), " ", word.text),
      current && h("span", { className: "hint" }, `Projected by month end: ${money(row.projected)}`),
    ),
  );
}

function categoriesSection(summary, kind) {
  const section = h(
    "section",
    { attrs: { "aria-labelledby": "categories-heading" } },
    h("h2", { id: "categories-heading" }, "Spending by category"),
  );
  if (summary.categories.length === 0) {
    section.append(
      h(
        "p",
        { className: "note empty-state" },
        `No confirmed expenses in ${monthName(summary.month)}. `,
        h("a", { href: "index.html" }, "Upload a receipt"),
      ),
    );
    return section;
  }
  if (kind === "current") {
    section.append(
      h(
        "p",
        { className: "hint" },
        "The bar shows the spend against the budget; the dark mark is the projection for the month end.",
      ),
    );
  }
  section.append(
    h("ul", { className: "spend-list" }, summary.categories.map((row) => categoryRow(row, kind))),
  );
  return section;
}

function goalSection(summary, kind) {
  const section = h(
    "section",
    { className: "goal-card", attrs: { "aria-labelledby": "goal-heading" } },
    h("h2", { id: "goal-heading" }, "Savings goal"),
  );
  const goal = summary.goal;
  if (!goal) {
    section.append(
      h("p", {}, "No savings goal yet. ", h("a", { href: "settings.html" }, "Set a savings goal")),
    );
    return section;
  }

  section.append(
    h("p", { className: "goal-target" }, `Target: ${money(goal.target_amount)} by ${formatDate(goal.target_date)}`),
  );
  // Relative to the viewed month, not the client's today: months_left clamps to 1 for both.
  if (goal.target_date < `${summary.month}-01`) {
    section.append(
      h(
        "p",
        { className: "note warn" },
        icon("⚠"),
        " Target date has passed. The whole target amount counts as needed this month.",
      ),
    );
  } else if (goal.target_date.startsWith(`${summary.month}-`)) {
    section.append(
      h(
        "p",
        { className: "note warn" },
        icon("⚠"),
        " Target date is this month: the full amount is needed now.",
      ),
    );
  }

  const needed = h(
    "div",
    {},
    h("dt", {}, "Needed this month"),
    h("dd", {}, money(goal.required_per_month)),
  );
  if (kind === "future") {
    // Nothing is spent yet, so income − 0 would read as "On track"; show the need only.
    section.append(
      h("dl", { className: "goal-list" }, needed),
      h("p", { className: "hint" }, "This month hasn't started yet."),
    );
    return section;
  }

  const savedLabel = kind === "past" ? "Saved this month" : "Expected savings this month";
  const list = h(
    "dl",
    { className: "goal-list" },
    needed,
    goal.saved_this_month !== null &&
      h("div", {}, h("dt", {}, savedLabel), h("dd", {}, money(goal.saved_this_month))),
  );
  section.append(list);

  if (goal.on_track === true) {
    section.append(h("p", { className: "goal-state on-track" }, icon("✓"), " On track"));
  } else if (goal.on_track === false) {
    section.append(h("p", { className: "goal-state behind" }, icon("✖"), " Behind"));
  } else {
    section.append(
      h(
        "p",
        { className: "hint" },
        h("a", { href: "settings.html" }, "Add your monthly income in Settings"),
        " to track progress.",
      ),
    );
  }
  return section;
}

function render(summary) {
  const kind = monthKind(summary.month);
  container.replaceChildren(
    h("h2", { className: "month-heading" }, monthName(summary.month)),
    totalsSection(summary, kind),
    categoriesSection(summary, kind),
    goalSection(summary, kind),
  );
}

// ---------------------------------------------------------------- loading

function setMonthInUrl(month) {
  const url = new URL(window.location.href);
  url.searchParams.set("month", month);
  window.history.replaceState(null, "", url);
}

async function fetchSummary(month) {
  return month
    ? api.get(`/insights/summary?month=${encodeURIComponent(month)}`)
    : api.get("/insights/summary");
}

function display(summary) {
  state.month = summary.month;
  monthInput.value = summary.month;
  updateNavButtons();
  render(summary);
  statusLine.textContent = `Showing ${monthName(summary.month)}.`;
}

/** The browser's local calendar date, used only to notice that a day has passed. */
function localDateKey() {
  const now = new Date();
  return `${now.getFullYear()}-${now.getMonth() + 1}-${now.getDate()}`;
}

/**
 * The summary for the server's current month (Europe/Berlin), asked for without a month.
 * The promise is shared, so a navigation during the first load doesn't ask twice. It is
 * asked again once the local date has changed (a tab left open across a month boundary)
 * or after a failure; on success it sets `state.currentMonth`.
 */
function currentSummary() {
  const today = localDateKey();
  if (!state.currentPromise || state.currentFetchedOn !== today) {
    const promise = fetchSummary(null).then((summary) => {
      state.currentMonth = summary.month;
      return summary;
    });
    promise.catch(() => {
      if (state.currentPromise === promise) state.currentPromise = null;
    });
    state.currentPromise = promise;
    state.currentFetchedOn = today;
  }
  return state.currentPromise;
}

/**
 * Shows `month` ("YYYY-MM"), or the server's current month for `null`. The current
 * month (see `currentSummary`) decides whether projections are shown; with a month, both
 * requests run in parallel. Only the newest request renders.
 */
async function show(month) {
  const requestId = ++state.requestId;
  clearError();
  container.setAttribute("aria-busy", "true");
  const [current, requested] = await Promise.allSettled([
    currentSummary(),
    month ? fetchSummary(month) : Promise.resolve(null),
  ]);
  if (requestId !== state.requestId) return;
  try {
    if (current.status === "rejected") throw current.reason;
    if (!month) {
      display(current.value);
      return;
    }
    if (requested.status === "rejected") {
      showError(requested.reason);
      // A bad `?month=` on the first load: fall back to the current month.
      if (!state.month) display(current.value);
      else monthInput.value = state.month;
      return;
    }
    display(requested.value);
    setMonthInUrl(requested.value.month);
  } catch (err) {
    showError(err);
    if (state.month) {
      // Put the picker back on the month that is still on screen.
      monthInput.value = state.month;
    } else {
      container.replaceChildren(h("p", {}, "The dashboard could not be loaded."));
    }
  } finally {
    container.removeAttribute("aria-busy");
  }
}

function init() {
  const requested = new URLSearchParams(window.location.search).get("month");
  show(isMonth(requested) ? requested : null);
}

function step(delta) {
  const month = state.month && shiftMonth(state.month, delta);
  if (month) show(month);
}

prevButton.addEventListener("click", () => step(-1));
nextButton.addEventListener("click", () => step(1));
monthInput.addEventListener("change", () => {
  // A cleared, half-typed or out-of-range picker value is ignored; wait for a valid month.
  if (isMonth(monthInput.value) && monthInput.value !== state.month) show(monthInput.value);
});

monthInput.min = FIRST_MONTH;
monthInput.max = LAST_MONTH;
updateNavButtons();
init();
