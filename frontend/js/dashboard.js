// Dashboard: spend vs. budget per category, goal progress (GET /insights/summary,
// decision 0021) and the potential-leak cards (GET /insights/leaks, decision 0023)
// for one month. Every user-visible text says "potential leak"; identifiers keep `leak`.
import { ApiError, api, clearError, formatMoney, showError } from "./api.js";
import { categoryLabel } from "./categories.js";
import { formatDate, h, icon } from "./dom.js";

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

// The insights endpoints, both answering for the current month without `?month=`.
// The paths stay literal so tests/unit/test_frontend_static.py can check them.
const ENDPOINTS = {
  summary: (query) => api.get(`/insights/summary${query}`),
  leaks: (query) => api.get(`/insights/leaks${query}`),
};

/** The request for the current month (no `?month=`) of one endpoint, see currentRequest(). */
function emptyCurrent() {
  return {
    promise: null, // the pending or settled request asked for without a month
    fetchedOn: null, // Berlin date of that request
    pending: false, // whether that request is still in flight
  };
}

const state = {
  currentMonth: null, // the server's current month (Europe/Berlin), from the summary
  current: { summary: emptyCurrent(), leaks: emptyCurrent() },
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

// Potential-leak types F09 returns (decision 0023), as words with an icon. Any other
// type (small_frequent, recurring, later ones) is shown as its raw value with LEAK_OTHER_SYMBOL.
const LEAK_TYPE_TEXT = {
  over_budget: { symbol: "✖", text: "Over budget" },
  on_pace_to_overrun: { symbol: "⚠", text: "Running out early" },
  spike: { symbol: "↑", text: "Spike" },
};
const LEAK_OTHER_SYMBOL = "•";
const LEAK_NO_CATEGORY = "Potential leak";

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

function money(amount) {
  return formatMoney(amount, CURRENCY);
}

const BERLIN_DATE = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Europe/Berlin",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

/** Today in Europe/Berlin as "YYYY-MM-DD": the server's calendar (decision 0021). */
function berlinToday() {
  const parts = Object.fromEntries(
    BERLIN_DATE.formatToParts(new Date()).map((part) => [part.type, part.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}`;
}

/**
 * "past", "current" or "future", relative to the server's current month; before the
 * server has answered once, relative to Berlin's month computed here.
 */
function monthKind(month) {
  const current = state.currentMonth || berlinToday().slice(0, 7);
  if (month === current) return "current";
  return month < current ? "past" : "future";
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

function leakCard(leak) {
  const known = Object.hasOwn(LEAK_TYPE_TEXT, leak.type) ? LEAK_TYPE_TEXT[leak.type] : null;
  const symbol = known ? known.symbol : LEAK_OTHER_SYMBOL;
  const typeText = known ? known.text : String(leak.type);
  const label = leak.category ? categoryLabel(leak.category) : LEAK_NO_CATEGORY;
  return h(
    "li",
    { className: "leak-card", dataset: { type: known ? leak.type : "other" } },
    h(
      "h3",
      { className: "leak-head" },
      h("span", { className: "leak-category" }, label),
      h("span", { className: "visually-hidden" }, ": "),
      h("span", { className: "leak-type" }, icon(symbol), " ", typeText),
    ),
    h("p", { className: "leak-explanation" }, leak.explanation),
  );
}

/** Fills the section once its `GET /insights/leaks` result is in (see settle()). */
function fillLeaks(section, month, result) {
  section.removeAttribute("aria-busy");
  const heading = section.firstChild;
  if (!result.ok) {
    const err = result.reason;
    if (!(err instanceof ApiError)) console.error(err);
    const detail = err && typeof err.detail === "string" && err.detail ? ` ${err.detail}` : "";
    section.replaceChildren(
      heading,
      h(
        "p",
        { className: "note error empty-state" },
        icon("⚠"),
        ` Potential leaks could not be loaded.${detail}`,
      ),
    );
    return;
  }
  const leaks = Array.isArray(result.value) ? result.value : [];
  if (leaks.length === 0) {
    section.replaceChildren(
      heading,
      h("p", { className: "note empty-state" }, `No potential leaks found in ${monthName(month)}.`),
    );
    return;
  }
  section.replaceChildren(heading, h("ul", { className: "leak-list" }, leaks.map(leakCard)));
}

/**
 * The "Potential leaks" section: a loading hint now, filled when `leaksResult` settles,
 * as long as the section is still on screen (a newer month replaces it).
 */
function leaksSection(month, leaksResult) {
  const section = h(
    "section",
    { className: "leaks", attrs: { "aria-labelledby": "leaks-heading", "aria-busy": "true" } },
    h("h2", { id: "leaks-heading" }, "Potential leaks"),
    h("p", { className: "hint empty-state" }, "Loading potential leaks…"),
  );
  leaksResult.then((result) => {
    if (section.isConnected) fillLeaks(section, month, result);
  });
  return section;
}

function render(summary, leaksResult) {
  const kind = monthKind(summary.month);
  container.replaceChildren(
    h("h2", { className: "month-heading" }, monthName(summary.month)),
    totalsSection(summary, kind),
    leaksSection(summary.month, leaksResult),
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

/** `GET /insights/summary` or `/insights/leaks`; `month` null asks for the current month. */
function fetchInsight(endpoint, month) {
  return ENDPOINTS[endpoint](month ? `?month=${encodeURIComponent(month)}` : "");
}

/**
 * A promise that never rejects: `{ok: true, value}` or `{ok: false, reason}`. Attaching
 * it right away keeps a failed request that nothing awaits from being an unhandled rejection.
 */
function settle(promise) {
  return promise.then(
    (value) => ({ ok: true, value }),
    (reason) => ({ ok: false, reason }),
  );
}

function display(summary, leaksResult) {
  state.month = summary.month;
  monthInput.value = summary.month;
  updateNavButtons();
  render(summary, leaksResult);
  statusLine.textContent = `Showing ${monthName(summary.month)}.`;
}

/**
 * `endpoint` ("summary" or "leaks") for the server's current month (Europe/Berlin), asked
 * for without a month; a summary sets `state.currentMonth` on success. A pending request
 * is always shared, so a navigation during the first load doesn't ask twice. A settled one
 * is reused, except:
 * - `refresh` asks again (the current month is about to be shown, so its data must be fresh);
 * - the Berlin date has changed since (a tab left open across a month boundary);
 * - the last request failed.
 */
function currentRequest(endpoint, { refresh = false } = {}) {
  const cache = state.current[endpoint];
  const today = berlinToday();
  const stale = !cache.promise || cache.fetchedOn !== today;
  if (stale || (refresh && !cache.pending)) {
    let promise = fetchInsight(endpoint, null);
    if (endpoint === "summary") {
      promise = promise.then((summary) => {
        state.currentMonth = summary.month;
        return summary;
      });
    }
    cache.pending = true;
    promise
      .catch(() => {
        if (cache.promise === promise) cache.promise = null;
      })
      .finally(() => {
        if (cache.promise === promise || cache.promise === null) {
          cache.pending = false;
        }
      });
    cache.promise = promise;
    cache.fetchedOn = today;
  }
  return cache.promise;
}

function showLoadFailure() {
  if (state.month) {
    // Put the picker back on the month that is still on screen.
    monthInput.value = state.month;
  } else {
    container.replaceChildren(h("p", {}, "The dashboard could not be loaded."));
  }
}

/**
 * Shows `month` ("YYYY-MM"), or the server's current month for `null`.
 * - The current month (see `currentRequest`) decides whether projections are shown.
 * - The known current month is shown from the requests without a month, never a second
 *   `?month=` request; any other month is asked for in parallel with the current summary.
 * - The potential leaks of the shown month load in parallel with its summary; the page
 *   draws once the summary is in, and the leaks section fills itself when they arrive.
 *   A failed leaks request only shows an error line in that section.
 * - If only the current-month request fails, the requested month is still shown,
 *   classified by the last known current month or Berlin's month (`monthKind`).
 * - Only the newest request renders.
 */
async function show(month) {
  const requestId = ++state.requestId;
  clearError();
  container.setAttribute("aria-busy", "true");
  const wantsCurrent = !month || month === state.currentMonth;
  const currentPromise = currentRequest("summary", { refresh: wantsCurrent });
  const requestedPromise = wantsCurrent ? currentPromise : fetchInsight("summary", month);
  let leaksResult = settle(
    wantsCurrent ? currentRequest("leaks", { refresh: true }) : fetchInsight("leaks", month),
  );
  const [current, requested] = await Promise.allSettled([currentPromise, requestedPromise]);
  if (requestId !== state.requestId) return;
  try {
    if (requested.status === "rejected") {
      showError(requested.reason);
      // A bad `?month=` on the first load: fall back to the current month.
      if (!state.month && !wantsCurrent && current.status === "fulfilled") {
        display(current.value, settle(currentRequest("leaks", { refresh: true })));
      } else {
        showLoadFailure();
      }
      return;
    }
    let summary = requested.value;
    if (month && summary.month !== month) {
      // The current month moved on (new Berlin day) while `month` was the old one.
      leaksResult = settle(fetchInsight("leaks", month));
      summary = await fetchInsight("summary", month);
      if (requestId !== state.requestId) return;
    }
    display(summary, leaksResult);
    if (month) setMonthInUrl(summary.month);
  } catch (err) {
    if (requestId !== state.requestId) return;
    showError(err);
    showLoadFailure();
  } finally {
    if (requestId === state.requestId) container.removeAttribute("aria-busy");
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
