// Review page: the receipt photo beside the expense form (docs/wiki/frontend/pages.md).
// Editing semantics and the client-side AI badge follow decision 0018; the lean field set,
// Save & confirm and Save draft follow decision 0019.
import { ApiError, api, clearError, formatMoney, pollReceipt, showError } from "./api.js";
import { CATEGORIES, UNCATEGORIZED, categoryLabel } from "./categories.js";
import { RECEIPT_STATUS_WORDS, REVIEW_STATUS_WORDS, SOURCE_WORDS, h, icon } from "./dom.js";

const CATEGORY_SOURCE_HINTS = { user: "from your earlier choice", seed: "default" };

const NOT_A_RECEIPT_TEXT = "The image wasn't recognised as a receipt.";
const SAME_RESULT_TEXT =
  "A retry with the same model usually gives the same result; it helps only after a model or prompt change.";
const CORRECTED_NOTE = "AI-extracted, corrected by you — check the remaining values";
const UNKNOWN_TEXT = "unknown, please fill in";
const SLOW_PROCESSING_TEXT =
  "Still processing. On a CPU-only model server this can take several minutes; the page keeps checking.";

const SCALARS = [
  { name: "merchant", label: "Merchant", kind: "text", required: true },
  { name: "date", label: "Date", kind: "date", required: true },
  { name: "currency", label: "Currency", kind: "currency", required: true },
  { name: "total", label: "Total", kind: "money", required: true },
];
// Flags on any other field (subtotal, tax, payment_method, ...) have no input and go to
// "Other findings", so none is dropped. Fields not shown are never sent: the server keeps them.
const SCALAR_NAMES = new Set(SCALARS.map((def) => def.name));
// The keys PATCH `checked_fields` accepts (decision 0031): the contract's `CheckedField`.
// Only an `unreadable` flag on one of these gets a "Looks right" button. `subtotal` and
// `tax` (prompt v1 only) have no input, so their flags and buttons are in "Other findings".
const CHECKABLE_FIELDS = new Set([
  "merchant",
  "date",
  "currency",
  "line_items",
  "subtotal",
  "tax",
  "total",
  "payment_method",
]);
const STALE_TEXT = "Edited — rechecked when you save";
// Confirm needs these; the API refuses `null` for them in a PATCH.
const CONFIRM_REQUIRED = ["merchant", "date", "total"];

// `unit` and `unit_price` are not shown and not sent (decision 0019).
const ITEM_FIELDS = [
  { name: "description", label: "Item", kind: "text", required: true },
  { name: "qty", label: "Quantity", kind: "qty", required: false },
  { name: "amount", label: "Amount", kind: "money", required: true },
];

// ---------------------------------------------------------------- state

const params = new URLSearchParams(window.location.search);
const rawId = params.get("id") || "";
const receiptId = /^\d+$/.test(rawId) ? Number(rawId) : null;
// `review.html?expense=<id>`: reopens an expense without a photo, e.g. after a reload of a
// manual entry whose confirm was refused. `?id=` wins over `?expense=`, which wins over `?manual=1`.
const rawExpenseId = params.get("expense");
const expenseParam = receiptId === null && rawExpenseId !== null;
const expenseId =
  expenseParam && /^[1-9]\d*$/.test(rawExpenseId) && Number.isSafeInteger(Number(rawExpenseId))
    ? Number(rawExpenseId)
    : null;
// `review.html?manual=1`: an expense typed in without a photo (no receipt, no image column,
// no receipt actions). A receipt id wins, so `?id=` keeps the receipt flow.
const standalone = receiptId === null && (expenseParam || params.get("manual") === "1");

const layout = document.querySelector(".review-layout");
const panel = document.getElementById("panel");
const figure = document.getElementById("receipt-figure");
const live = document.getElementById("live");

const state = {
  receipt: null,
  expense: null, // the expense as last returned by the server
  mode: "loading", // loading | processing | review | failed | manual
  unlocked: false, // a confirmed expense opened with "Edit again"
  busy: false,
  draft: null, // the form's values as typed (strings)
  edited: new Set(), // badge keys of fields the user edited in this page session
  checked: new Set(), // `unreadable` flag fields marked "Looks right", sent as checked_fields (0031)
  aiTracked: false, // this session saw the expense as unconfirmed `ai`, so per-field badges are known
  leaving: false, // navigating away after a successful save or delete: no unsaved-changes warning
  nextKey: 1,
  fieldErrors: [], // [{path, message}] from local validation or ApiError.fields
};

// Nodes that input handlers update without a full re-render.
let refs = {};

// The running receipt poll's AbortController, or null. Polling has no time limit (F12),
// so it must be stopped whenever the page leaves the processing screen.
let poll = null;

function stopPolling() {
  if (poll) poll.abort();
  poll = null;
}

function announce(text) {
  live.textContent = "";
  window.setTimeout(() => {
    live.textContent = text;
  }, 50);
}

// ---------------------------------------------------------------- values

function toInput(value, kind) {
  if (value === null || value === undefined) return "";
  if (kind === "money") return Number(value).toFixed(2);
  return String(value);
}

/** Parses an input's text. Returns {ok, value} with `null` for an empty input, or {ok: false, message}. */
function parseValue(kind, text) {
  const trimmed = (text || "").trim();
  if (trimmed === "") return { ok: true, value: null };
  if (kind === "text") return { ok: true, value: trimmed };
  if (kind === "date") {
    return /^\d{4}-\d{2}-\d{2}$/.test(trimmed)
      ? { ok: true, value: trimmed }
      : { ok: false, message: "Enter a date as YYYY-MM-DD." };
  }
  if (kind === "currency") {
    const code = trimmed.toUpperCase();
    return /^[A-Z]{3}$/.test(code)
      ? { ok: true, value: code }
      : { ok: false, message: "Use a three-letter code such as EUR." };
  }
  const normalized = trimmed.replace(/\s/g, "").replace(",", ".");
  if (kind === "money") {
    if (!/^-?\d+(\.\d{1,2})?$/.test(normalized)) {
      return { ok: false, message: "Enter an amount such as 12.99 (at most 2 decimals)." };
    }
    const value = Number(normalized);
    if (Math.abs(value) >= 1e10) return { ok: false, message: "This amount is too large." };
    return { ok: true, value };
  }
  if (!/^-?\d+(\.\d+)?$/.test(normalized)) {
    return { ok: false, message: "Enter a number such as 1 or 0.5." };
  }
  return { ok: true, value: Number(normalized) };
}

function newItem() {
  return {
    key: state.nextKey++,
    id: null,
    description: "",
    qty: "",
    amount: "",
    category: UNCATEGORIZED,
  };
}

function draftFrom(expense) {
  const draft = {};
  for (const def of SCALARS) draft[def.name] = toInput(expense[def.name], def.kind);
  draft.items = expense.line_items.map((item) => {
    const row = { key: state.nextKey++, id: item.id, category: item.category };
    for (const def of ITEM_FIELDS) row[def.name] = toInput(item[def.name], def.kind);
    return row;
  });
  return draft;
}

function emptyDraft() {
  const draft = {};
  for (const def of SCALARS) draft[def.name] = "";
  draft.currency = "EUR";
  draft.items = [newItem()];
  return draft;
}

function serverItem(id) {
  if (!state.expense || id === null) return null;
  return state.expense.line_items.find((item) => item.id === id) || null;
}

function sameValue(parsed, serverValue) {
  if (!parsed.ok) return false;
  const a = parsed.value;
  let b = serverValue === undefined ? null : serverValue;
  // Inputs are trimmed, so compare stored text trimmed too: an untrimmed or empty AI value
  // must not make the form dirty on load.
  if (typeof b === "string") b = b.trim() || null;
  if (a === null || b === null) return a === b;
  if (typeof a === "number") return a === Number(b);
  return a === String(b);
}

function scalarChanged(def) {
  return !sameValue(parseValue(def.kind, state.draft[def.name]), state.expense[def.name]);
}

function rowContentChanged(row, item) {
  return ITEM_FIELDS.some((def) => !sameValue(parseValue(def.kind, row[def.name]), item[def.name]));
}

/**
 * The item edits in one pass: `any` = an item was added, removed or had its name, quantity
 * or amount changed (not its category); `changedIds` = ids of stored items whose content
 * changed or that were removed (decision 0031: their `line_items[i]` flags are stale).
 */
function itemChanges() {
  const server = state.expense.line_items;
  const rows = state.draft.items;
  const rowsById = new Map(rows.filter((row) => row.id !== null).map((row) => [row.id, row]));
  const changedIds = new Set();
  for (const item of server) {
    const row = rowsById.get(item.id);
    if (!row || rowContentChanged(row, item)) changedIds.add(item.id);
  }
  // A new row, or (not possible on this page today) a reordering, changes the list too.
  const any =
    changedIds.size > 0 || rows.length !== server.length || rows.some((row, i) => row.id !== server[i].id);
  return { any, changedIds };
}

function itemContentChanged() {
  return itemChanges().any;
}

/** Only a category changed; call when the content is unchanged (rows and ids line up). */
function categoryChanged() {
  return state.draft.items.some((row, i) => row.category !== state.expense.line_items[i].category);
}

function itemsChanged() {
  return itemContentChanged() || categoryChanged();
}

/** `edited` is editedState(); updateActions() passes the one it computed for the flags. */
function isDirty(edited = editedState()) {
  if (!state.expense) return false;
  // A "Looks right" mark is an unsaved change too: it goes out with the next save (0031).
  return edited.scalars.size > 0 || edited.items || categoryChanged() || state.checked.size > 0;
}

/** Required fields that had a value and were emptied: the API refuses `null` for them. */
function emptiedRequired() {
  if (!state.expense) return [];
  return SCALARS.filter(
    (def) => def.required && state.expense[def.name] !== null && state.draft[def.name].trim() === "",
  );
}

/** Local checks before a request. `creating` = POST /expenses, where every required field is needed. */
function validate(creating) {
  const errors = [];
  for (const def of SCALARS) {
    const parsed = parseValue(def.kind, state.draft[def.name]);
    if (!parsed.ok) {
      errors.push({ path: def.name, message: parsed.message });
    } else if (def.required && parsed.value === null) {
      if (creating || state.expense[def.name] !== null) {
        errors.push({ path: def.name, message: `${def.label} is required.` });
      }
    }
  }
  if (creating || itemsChanged()) {
    if (state.draft.items.length === 0) {
      errors.push({ path: "line_items", message: "Add at least one line item." });
    }
    state.draft.items.forEach((row, i) => {
      for (const def of ITEM_FIELDS) {
        const parsed = parseValue(def.kind, row[def.name]);
        const path = `line_items.${i}.${def.name}`;
        if (!parsed.ok) errors.push({ path, message: parsed.message });
        else if (def.required && parsed.value === null) {
          errors.push({ path, message: `${def.label} is required.` });
        }
      }
    });
  }
  return errors;
}

function itemPayload(row) {
  const payload = {};
  if (row.id !== null) payload.id = row.id;
  for (const def of ITEM_FIELDS) payload[def.name] = parseValue(def.kind, row[def.name]).value;
  const item = serverItem(row.id);
  const descriptionChanged =
    item !== null && !sameValue(parseValue("text", row.description), item.description);
  // The form doesn't show unit or unit price (decision 0019). Send the stored values back,
  // or the server would erase unit_price; a changed description lets it derive the unit anew.
  if (item !== null) {
    if (item.unit_price !== null) payload.unit_price = item.unit_price;
    if (!descriptionChanged && item.unit !== null) payload.unit = item.unit;
  }
  // Send a category only when the user chose one, so a stored category keeps its source.
  // A user's earlier choice is sent again when the description changes, or the server
  // would recategorise the item (decision 0018).
  if (row.category !== UNCATEGORIZED) {
    if (!item || item.category !== row.category || (descriptionChanged && item.category_source === "user")) {
      payload.category = row.category;
    }
  }
  return payload;
}

function buildPatch() {
  const body = {};
  for (const def of SCALARS) {
    if (scalarChanged(def)) body[def.name] = parseValue(def.kind, state.draft[def.name]).value;
  }
  if (itemsChanged()) body.line_items = state.draft.items.map(itemPayload);
  // Values the user marked "Looks right"; clears their `unreadable` flags (decision 0031).
  if (state.checked.size > 0) body.checked_fields = [...state.checked];
  return body;
}

function buildCreate() {
  // A failed receipt's manual entry attaches to its photo; a standalone one has no receipt.
  const body = receiptId === null ? {} : { receipt_id: receiptId };
  for (const def of SCALARS) {
    const value = parseValue(def.kind, state.draft[def.name]).value;
    if (value !== null) body[def.name] = value;
  }
  body.line_items = state.draft.items.map(itemPayload);
  return body;
}

// ---------------------------------------------------------------- AI badge (decision 0018)

/** "fields": badge each unedited AI value; "note": one expense-level note; "none". */
function badgeMode() {
  const expense = state.expense;
  if (!expense || expense.confirmed || state.mode !== "review") return "none";
  if (expense.source === "ai") return "fields";
  if (expense.source === "ai_corrected") return state.aiTracked ? "fields" : "note";
  return "none";
}

function scalarBadgeKey(name) {
  return `scalar:${name}`;
}

// Only merchant, date, currency and total carry a badge; items have none (decision 0019).
function showsBadge(key, serverValue) {
  if (badgeMode() !== "fields") return false;
  if (serverValue === null || serverValue === undefined) return false;
  return !state.edited.has(key);
}

function aiBadge(key) {
  const badge = h("span", { className: "badge-ai" }, "AI-generated");
  refs.badges.set(key, badge);
  return badge;
}

function markEdited(key) {
  if (key === null) return;
  state.edited.add(key);
  const badge = refs.badges && refs.badges.get(key);
  if (badge) {
    badge.remove();
    refs.badges.delete(key);
  }
}

// ---------------------------------------------------------------- applying server data

function applyExpense(expense) {
  state.expense = expense;
  state.draft = draftFrom(expense);
  state.fieldErrors = [];
  // The server's flags are new: earlier "Looks right" marks were sent or no longer apply.
  state.checked.clear();
  if (expense.source === "ai" && !expense.confirmed) state.aiTracked = true;
  if (expense.confirmed) {
    // Confirming reviews every value; the badges don't come back after "Edit again".
    state.aiTracked = false;
  }
  if (state.receipt) {
    state.receipt.expense = expense;
    state.receipt.status = expense.confirmed ? "confirmed" : "extracted";
    state.receipt.error = null;
    state.receipt.error_detail = null;
  }
}

function route(receipt) {
  state.receipt = receipt;
  clearError();
  if (receipt.status === "uploaded" || receipt.status === "extracting") {
    startProcessing();
  } else if (receipt.status === "failed") {
    state.mode = "failed";
    render();
  } else if (receipt.expense) {
    state.unlocked = false;
    applyExpense(receipt.expense);
    state.mode = "review";
    render();
  } else {
    state.mode = "failed";
    render();
  }
}

// ---------------------------------------------------------------- shared pieces

function deleteButton() {
  return h(
    "button",
    { type: "button", className: "danger", on: { click: onDelete } },
    "Delete receipt",
  );
}

function panelHeader(title) {
  return h(
    "div",
    { className: "panel-header" },
    h("h1", {}, title),
    receiptId === null ? null : deleteButton(),
  );
}

function flagNode(flag, id) {
  return h(
    "span",
    { className: "flag-message", id },
    icon("⚠"),
    " Check: ",
    flag.message,
  );
}

/**
 * A flag as shown on the form (decision 0031): the message, dimmed with a note while its
 * value is edited, and for an `unreadable` flag a "Looks right" button that swaps the
 * message for "Marked as checked" and Undo. `id` goes on the message; the checked note
 * gets `<id>-checked`. Returns the entry; `entry.root` is the node to insert, and
 * `describedIds()` lists only the shown node's id for an input's aria-describedby.
 * Its state is set by syncFlags(), which updateActions() runs after every change.
 */
function flagEntry(flag, id, readOnly) {
  const message = flagNode(flag, id);
  const staleNote = h("span", { className: "stale-note" }, ` (${STALE_TEXT})`);
  staleNote.hidden = true;
  message.append(staleNote);
  const entry = { flag, message, staleNote, checkable: false, root: message };
  entry.describedIds = () => (entry.message.hidden || !id ? [] : [id]);
  refs.flagEntries.push(entry);
  if (readOnly || flag.code !== "unreadable" || !CHECKABLE_FIELDS.has(flag.field)) return entry;

  entry.checkable = true;
  const checkedId = id ? `${id}-checked` : null;
  entry.lookRight = h(
    "button",
    {
      type: "button",
      className: "flag-action",
      on: {
        click: () => {
          // A save in flight would apply its response and drop a mark made now. The
          // button is disabled then (updateActions); this guard is the fallback.
          if (state.busy) return;
          state.checked.add(flag.field);
          updateActions();
          entry.undo.focus();
          announce("Marked as checked. It is sent with the next save.");
        },
      },
    },
    "Looks right",
  );
  entry.actions = h("div", { className: "flag-actions" }, entry.lookRight);
  entry.checkedText = h("span", { id: checkedId }, icon("✔"), " Marked as checked");
  entry.undo = h(
    "button",
    {
      type: "button",
      className: "link-button",
      on: {
        click: () => {
          if (state.busy) return;
          state.checked.delete(flag.field);
          updateActions();
          entry.lookRight.focus();
          announce("Check undone.");
        },
      },
    },
    "Undo",
  );
  entry.checkedLine = h("div", { className: "flag-message flag-checked" }, entry.checkedText, " ", entry.undo);
  entry.checkedLine.hidden = true;
  entry.root = h("div", { className: "flag-entry" }, message, entry.actions, entry.checkedLine);
  entry.describedIds = () => {
    if (!id) return [];
    return entry.message.hidden ? [checkedId] : [id];
  };
  return entry;
}

/**
 * Edited values whose flags are stale until the next save (decision 0031). Computed once
 * per updateActions() and shared by syncFlags() and isDirty(): `scalars` = names of the
 * changed scalar fields, `items` = any item content change, `itemIds` = stored items whose
 * content changed or that were removed.
 */
function editedState() {
  if (!state.expense || !state.draft) return { scalars: new Set(), items: false, itemIds: new Set() };
  const items = itemChanges();
  return {
    scalars: new Set(SCALARS.filter(scalarChanged).map((def) => def.name)),
    items: items.any,
    itemIds: items.changedIds,
  };
}

function flagIsStale(flag, edited) {
  const field = flag.field;
  if (field && edited.scalars.has(field)) return true;
  if (flag.code === "possible_duplicate" && (edited.scalars.has("date") || edited.scalars.has("total"))) {
    return true;
  }
  if (!edited.items) return false;
  // The whole list and the sum: stale on any item add, remove or edit.
  if (flag.code === "sum_mismatch" || field === "line_items") return true;
  // `uncategorized_item` keeps its own show/hide rule in syncCategoryRow.
  const itemMatch = typeof field === "string" ? /^line_items\[(\d+)\]$/.exec(field) : null;
  if (!itemMatch || flag.code === "uncategorized_item") return false;
  // `line_items[i]` is the stored item at index i: stale only when that item changed or
  // was removed. An index with no stored item can't be matched, so any edit counts.
  const item = state.expense.line_items[Number(itemMatch[1])];
  return item ? edited.itemIds.has(item.id) : true;
}

/**
 * Sets every shown flag's stale and checked state, then the inputs' aria-describedby.
 * "Looks right" and Undo are disabled while a save is in flight, like the save buttons.
 */
function syncFlags(edited) {
  if (!refs.flagEntries) return;
  for (const entry of refs.flagEntries) {
    if (entry.checkable) {
      entry.lookRight.disabled = state.busy;
      entry.undo.disabled = state.busy;
    }
    const stale = flagIsStale(entry.flag, edited);
    entry.message.classList.toggle("stale", stale);
    entry.staleNote.hidden = !stale;
    if (entry.checkable) {
      const checked = state.checked.has(entry.flag.field);
      entry.message.hidden = checked;
      entry.actions.hidden = checked;
      entry.checkedLine.hidden = !checked;
    }
  }
  for (const sync of refs.describers) sync();
}

/**
 * Keeps `input`'s aria-describedby to the shown flag and error nodes: a hidden node it
 * named directly would still be read (docs/wiki/plan/workflow.md).
 */
function describeWith(input, entries, errorIds) {
  return () => {
    const ids = entries.flatMap((entry) => entry.describedIds()).concat(errorIds).join(" ");
    // Runs on every keystroke: touch the attribute only when the list changes.
    if (ids === (input.getAttribute("aria-describedby") || "")) return;
    if (ids) input.setAttribute("aria-describedby", ids);
    else input.removeAttribute("aria-describedby");
  };
}

function errorNode(message, id) {
  return h("span", { className: "field-error", id }, icon("✖"), " ", message);
}

async function onDelete() {
  if (state.busy) return;
  const ok = window.confirm(
    "Delete this receipt, its photo and its expense? This can't be undone.",
  );
  if (!ok) return;
  state.busy = true;
  try {
    await api.delete(`/receipts/${receiptId}`);
    state.mode = "deleted";
    stopPolling();
    goHome();
  } catch (err) {
    showError(err);
  } finally {
    state.busy = false;
  }
}

// ---------------------------------------------------------------- processing

async function startProcessing() {
  // One poll at a time: a Retry starts a new one.
  stopPolling();
  const controller = new AbortController();
  poll = controller;
  state.mode = "processing";
  const statusText = h(
    "span",
    {},
    RECEIPT_STATUS_WORDS[state.receipt.status] || state.receipt.status,
    "…",
  );
  // The spinner box is a live region (role="status"): the status words and, after
  // 5 minutes, the slow note are announced when they change or appear.
  const statusLines = h("div", {}, statusText);
  let slowNote = null;
  panel.replaceChildren(
    panelHeader("Reading your receipt"),
    h(
      "div",
      { className: "spinner-box", attrs: { role: "status" } },
      h("div", { className: "spinner", attrs: { "aria-hidden": "true" } }),
      statusLines,
    ),
    h(
      "p",
      { className: "hint" },
      "The AI reads one receipt at a time; this can take a minute. The page updates by itself.",
    ),
  );
  try {
    const receipt = await pollReceipt(
      receiptId,
      (update) => {
        statusText.textContent = `${RECEIPT_STATUS_WORDS[update.status] || update.status}…`;
      },
      () => {
        if (state.mode !== "processing" || slowNote) return;
        slowNote = h("p", { className: "hint" }, SLOW_PROCESSING_TEXT);
        statusLines.append(slowNote);
      },
      { signal: controller.signal },
    );
    if (controller.signal.aborted || state.mode !== "processing") return;
    poll = null;
    route(receipt);
  } catch (err) {
    // A cancelled poll rejects with the abort reason: the page has moved on, so ignore it.
    if (controller.signal.aborted || state.mode !== "processing") return;
    poll = null;
    showError(err);
    // Polling stopped, so "the page keeps checking" is no longer true.
    if (slowNote) {
      slowNote.remove();
      slowNote = null;
    }
    statusText.textContent = "Not finished yet.";
    panel.append(
      h(
        "button",
        { type: "button", on: { click: () => window.location.reload() } },
        "Check again",
      ),
    );
  }
}

// ---------------------------------------------------------------- failed

function renderFailed() {
  const receipt = state.receipt;
  const code = receipt.error;
  const children = [panelHeader("Extraction failed")];
  if (code === "not_a_receipt") {
    children.push(h("p", { className: "note warn" }, icon("⚠"), " ", NOT_A_RECEIPT_TEXT));
  }
  children.push(
    h(
      "p",
      { className: "note error" },
      icon("✖"),
      " ",
      receipt.error_detail || "The extraction didn't produce an expense.",
    ),
  );
  if (code === "not_a_receipt" || code === "malformed_output") {
    children.push(h("p", { className: "hint" }, SAME_RESULT_TEXT));
  }
  if (code === "unreadable_image") {
    children.push(
      h(
        "p",
        { className: "hint" },
        "The photo couldn't be read, so a retry won't help. Enter the expense by hand, or delete it and upload a clearer photo.",
      ),
    );
  }
  const actions = h("div", { className: "actions" });
  if (code !== "unreadable_image") {
    actions.append(
      h("button", { type: "button", className: "primary", on: { click: onRetry } }, "Retry"),
    );
  }
  actions.append(
    h("button", { type: "button", on: { click: onEnterManually } }, "Enter manually"),
  );
  children.push(actions);
  panel.replaceChildren(...children);
}

async function onRetry() {
  if (state.busy) return;
  state.busy = true;
  clearError();
  try {
    const receipt = await api.post(`/receipts/${receiptId}/extract`);
    state.receipt = receipt;
    state.busy = false;
    startProcessing();
  } catch (err) {
    state.busy = false;
    showError(err);
  }
}

function onEnterManually() {
  stopPolling();
  clearError();
  state.mode = "manual";
  state.expense = null;
  state.draft = emptyDraft();
  state.fieldErrors = [];
  state.checked.clear();
  render();
  const first = panel.querySelector("input");
  if (first) first.focus();
}

// ---------------------------------------------------------------- the expense form

function errorsFor(path) {
  return state.fieldErrors.filter((err) => err.path === path);
}

function scalarField(def, readOnly, flags) {
  const id = `f-${def.name}`;
  const expense = state.expense;
  const serverValue = expense ? expense[def.name] : null;
  const errorIds = [];
  const extra = [];

  const entries = flags.map((flag, i) => flagEntry(flag, `${id}-flag-${i}`, readOnly));
  extra.push(...entries.map((entry) => entry.root));
  const errors = errorsFor(def.name);
  errors.forEach((err, i) => {
    const errId = `${id}-err-${i}`;
    errorIds.push(errId);
    extra.push(errorNode(err.message, errId));
  });

  const input = h("input", {
    id,
    name: def.name,
    type: def.kind === "date" ? "date" : "text",
    value: state.draft[def.name],
    disabled: readOnly,
    required: def.required,
    attrs: {
      inputmode: def.kind === "money" ? "decimal" : null,
      maxlength: def.kind === "currency" ? "3" : null,
      placeholder: def.required ? null : "–",
      autocomplete: "off",
      "aria-invalid": errors.length ? "true" : null,
    },
  });

  // Only required fields say "unknown, please fill in"; an empty optional one shows a quiet "–".
  let unknown = null;
  if (def.required && expense && serverValue === null) {
    unknown = h("span", { className: "unknown" }, readOnly ? "unknown" : UNKNOWN_TEXT);
    unknown.hidden = state.draft[def.name].trim() !== "";
  }

  const badgeKey = scalarBadgeKey(def.name);
  const label = h(
    "label",
    { htmlFor: id },
    def.label,
    def.required ? h("span", { className: "hint" }, " (required)") : null,
    showsBadge(badgeKey, serverValue) ? aiBadge(badgeKey) : null,
  );

  input.addEventListener("input", () => {
    state.draft[def.name] = input.value;
    markEdited(badgeKey);
    if (unknown) unknown.hidden = input.value.trim() !== "";
    updateActions();
  });

  const field = h("div", { className: "field" }, label, input, unknown, ...extra);
  const describe = describeWith(input, entries, errorIds);
  refs.describers.push(() => {
    describe();
    // The highlight stays while a flag is left that the user hasn't marked "Looks right".
    field.classList.toggle("flagged", entries.some((entry) => !entry.message.hidden));
  });
  return field;
}

function categoryOptions(selected) {
  const options = [];
  if (selected === UNCATEGORIZED) {
    options.push(h("option", { value: UNCATEGORIZED, selected: true }, "Choose a category…"));
  }
  for (const category of CATEGORIES) {
    options.push(h("option", { value: category, selected: category === selected }, categoryLabel(category)));
  }
  return options;
}

function itemRow(row, index, readOnly, flagsByItemId) {
  const n = index + 1;
  const item = serverItem(row.id);
  const flags = (row.id !== null && flagsByItemId.get(row.id)) || [];
  const tr = h("tr", { dataset: { key: String(row.key) } });
  const rowError = errorsFor(`line_items.${index}`).concat(errorsFor(`line_items.${index}.id`));

  for (const def of ITEM_FIELDS) {
    const id = `item-${row.key}-${def.name}`;
    const errors = errorsFor(`line_items.${index}.${def.name}`);
    const describedBy = [];
    const extra = [];
    errors.forEach((err, i) => {
      describedBy.push(`${id}-err-${i}`);
      extra.push(errorNode(err.message, `${id}-err-${i}`));
    });
    if (def.name === "description") {
      flags
        .filter((flag) => flag.code !== "uncategorized_item")
        .forEach((flag, i) => {
          // `line_items[i]` isn't a checked_fields key, so these never hide: a static list is right.
          describedBy.push(`${id}-flag-${i}`);
          extra.push(flagEntry(flag, `${id}-flag-${i}`, readOnly).root);
        });
      rowError.forEach((err, i) => {
        describedBy.push(`${id}-rowerr-${i}`);
        extra.push(errorNode(err.message, `${id}-rowerr-${i}`));
      });
    }
    const serverValue = item ? item[def.name] : null;
    const input = h("input", {
      id,
      type: "text",
      value: row[def.name],
      disabled: readOnly,
      required: def.required,
      attrs: {
        inputmode: def.kind === "money" || def.kind === "qty" ? "decimal" : null,
        placeholder: def.required ? null : "–",
        autocomplete: "off",
        "aria-describedby": describedBy.join(" ") || null,
        "aria-invalid": errors.length ? "true" : null,
      },
    });
    let unknown = null;
    if (def.required && item && serverValue === null) {
      unknown = h("span", { className: "unknown" }, readOnly ? "unknown" : UNKNOWN_TEXT);
      unknown.hidden = row[def.name].trim() !== "";
    }
    input.addEventListener("input", () => {
      row[def.name] = input.value;
      if (unknown) unknown.hidden = input.value.trim() !== "";
      updateActions();
    });
    tr.append(
      h(
        "td",
        { className: def.kind === "text" ? "" : "num" },
        h("label", { htmlFor: id, className: "visually-hidden" }, `Item ${n}: ${def.label}`),
        input,
        unknown,
        ...extra,
      ),
    );
  }

  // Category
  const selectId = `item-${row.key}-category`;
  const categoryFlags = flags.filter((flag) => flag.code === "uncategorized_item");
  const catExtra = [];
  const flagNodes = categoryFlags.map((flag, i) => flagNode(flag, `${selectId}-flag-${i}`));
  catExtra.push(...flagNodes);
  const missingNote = h(
    "span",
    { className: "flag-message", id: `${selectId}-missing` },
    icon("⚠"),
    " Choose a category before confirming.",
  );
  const sourceHint = h("span", { className: "hint" });
  const updateSourceHint = () => {
    const hint =
      item && item.category === row.category ? CATEGORY_SOURCE_HINTS[item.category_source] : null;
    sourceHint.textContent = hint || "";
    sourceHint.hidden = !hint;
  };
  updateSourceHint();
  const categoryPath = `line_items.${index}.category`;
  const categoryErrors = errorsFor(categoryPath);
  const errorNodes = categoryErrors.map((err, i) => errorNode(err.message, `${selectId}-err-${i}`));
  catExtra.push(...errorNodes);
  // While the item is uncategorised exactly one "needs a category" message shows:
  // the server's uncategorized_item flag if there is one, else the missing note.
  // Once a category is picked the flag is stale and hides; it shows again if the
  // user goes back to "Choose a category…". Server category errors are stale after
  // any change: they hide and are dropped from state.fieldErrors, so a local
  // re-render (Add item, Remove, …) doesn't bring them back; only a new server
  // response or a failed save can.
  const otherFlagCount = flags.length - categoryFlags.length;
  let categoryErrorsShown = errorNodes.length > 0;
  const select = h(
    "select",
    { id: selectId, disabled: readOnly, required: true },
    ...categoryOptions(row.category),
  );
  select.value = row.category;
  // The one place that sets the row's category state. A read-only row gets no
  // needs-category highlight or missing note: its select is disabled, so there is
  // nothing to act on (a confirmed expense can't hold an uncategorised item anyway).
  const syncCategoryRow = () => {
    const uncategorised = row.category === UNCATEGORIZED;
    tr.classList.toggle("needs-category", uncategorised && !readOnly);
    tr.classList.toggle("flagged", otherFlagCount > 0 || (uncategorised && flagNodes.length > 0));
    for (const node of flagNodes) node.hidden = !uncategorised;
    missingNote.hidden = !uncategorised || flagNodes.length > 0 || readOnly;
    for (const node of errorNodes) node.hidden = !categoryErrorsShown;
    // aria-describedby reads a referenced node even when it is hidden, so only
    // shown flags, notes and errors are listed (found by /code-review).
    const ids = flagNodes
      .concat([missingNote], errorNodes)
      .filter((node) => !node.hidden)
      .map((node) => node.id)
      .join(" ");
    if (ids) select.setAttribute("aria-describedby", ids);
    else select.removeAttribute("aria-describedby");
    if (categoryErrorsShown) select.setAttribute("aria-invalid", "true");
    else select.removeAttribute("aria-invalid");
  };
  syncCategoryRow();
  select.addEventListener("change", () => {
    row.category = select.value;
    categoryErrorsShown = false;
    // Paths are index-based; a change doesn't move rows, so `index` is still this row's.
    state.fieldErrors = state.fieldErrors.filter((err) => err.path !== categoryPath);
    syncCategoryRow();
    updateSourceHint();
    updateActions();
  });
  tr.append(
    h(
      "td",
      {},
      h("label", { htmlFor: selectId, className: "visually-hidden" }, `Item ${n}: Category`),
      select,
      sourceHint,
      missingNote,
      ...catExtra,
    ),
  );

  // Remove
  const removeCell = h("td");
  if (!readOnly) {
    removeCell.append(
      h(
        "button",
        {
          type: "button",
          className: "link-button",
          disabled: state.draft.items.length <= 1,
          title: state.draft.items.length <= 1 ? "An expense needs at least one item." : "",
          attrs: { "aria-label": `Remove item ${n}` },
          on: {
            click: () => {
              state.draft.items.splice(index, 1);
              state.fieldErrors = [];
              render();
              const next = refs.addButton;
              if (next) next.focus();
            },
          },
        },
        "Remove",
      ),
    );
  }
  tr.append(removeCell);

  return tr;
}

function itemsTable(readOnly, flagsByItemId) {
  const headers = ITEM_FIELDS.map((def) => def.label).concat(["Category", ""]);
  const cols = ["c-desc", "c-qty", "c-amount", "c-cat", "c-remove"];
  return h(
    "div",
    { className: "items-wrap" },
    h(
      "table",
      { className: "items" },
      h("caption", { className: "visually-hidden" }, "Line items"),
      h("colgroup", {}, ...cols.map((c) => h("col", { className: c }))),
      h(
        "thead",
        {},
        h(
          "tr",
          {},
          ...headers.map((text, i) =>
            h("th", { scope: "col" }, text || h("span", { className: "visually-hidden" }, i === headers.length - 1 ? "Actions" : "")),
          ),
        ),
      ),
      h(
        "tbody",
        {},
        ...state.draft.items.map((row, i) => itemRow(row, i, readOnly, flagsByItemId)),
      ),
    ),
  );
}

function itemsSumText() {
  let sum = 0;
  for (const row of state.draft.items) {
    const parsed = parseValue("money", row.amount);
    if (!parsed.ok) return "Items add up to: some amounts aren't valid yet.";
    sum += parsed.value || 0;
  }
  const currency = parseValue("currency", state.draft.currency);
  const code = currency.ok && currency.value ? currency.value : "EUR";
  return `Items add up to ${formatMoney(Math.round(sum * 100) / 100, code)}`;
}

/** Splits flags into scalar fields, items (by id), the item list, and the rest. */
function sortFlags() {
  const byField = new Map();
  const byItemId = new Map();
  const listFlags = [];
  const general = [];
  const expense = state.expense;
  for (const flag of expense ? expense.flags : []) {
    const field = flag.field;
    const itemMatch = typeof field === "string" ? /^line_items\[(\d+)\]$/.exec(field) : null;
    if (field && SCALAR_NAMES.has(field)) {
      if (!byField.has(field)) byField.set(field, []);
      byField.get(field).push(flag);
    } else if (itemMatch && expense.line_items[Number(itemMatch[1])]) {
      const id = expense.line_items[Number(itemMatch[1])].id;
      if (!byItemId.has(id)) byItemId.set(id, []);
      byItemId.get(id).push(flag);
    } else if (field === "line_items") {
      listFlags.push(flag);
    } else {
      general.push(flag);
    }
  }
  return { byField, byItemId, listFlags, general };
}

/** Field errors whose path matches no input (e.g. `body`), shown above the form. */
function unmatchedErrors() {
  return state.fieldErrors.filter((err) => {
    if (SCALAR_NAMES.has(err.path)) return false;
    const m = /^line_items\.(\d+)(\.(\w+))?$/.exec(err.path);
    if (!m) return err.path !== "line_items";
    const index = Number(m[1]);
    if (index >= state.draft.items.length) return true;
    const field = m[3];
    return field !== undefined && field !== "id" && field !== "category" &&
      !ITEM_FIELDS.some((def) => def.name === field);
  });
}

function statusLine() {
  const expense = state.expense;
  if (!expense) return null;
  const parts = [
    h(
      "span",
      {},
      "Review status: ",
      h(
        "span",
        { className: "review-status" },
        REVIEW_STATUS_WORDS[expense.review_status] || expense.review_status,
      ),
    ),
    h("span", {}, "Source: ", SOURCE_WORDS[expense.source] || expense.source),
    state.receipt
      ? h("span", {}, "Receipt: ", RECEIPT_STATUS_WORDS[state.receipt.status] || state.receipt.status)
      : null,
  ];
  return h("div", { className: "status-line" }, ...parts);
}

function renderForm() {
  const manual = state.mode === "manual";
  const expense = state.expense;
  const readOnly = !manual && expense.confirmed && !state.unlocked;
  refs = { badges: new Map(), flagEntries: [], describers: [] };

  const title = standalone
    ? manual
      ? "Add an expense manually"
      : readOnly
        ? "Confirmed expense"
        : "Expense without a photo"
    : manual
      ? "Enter the expense manually"
      : readOnly
        ? "Confirmed expense"
        : "Review the expense";
  const children = [panelHeader(title)];

  if (manual && standalone) {
    children.push(
      h(
        "p",
        { className: "note" },
        "An expense without a photo. Merchant, date, total and at least one item are needed to save.",
      ),
    );
  } else if (manual) {
    if (state.receipt.error === "not_a_receipt") {
      children.push(h("p", { className: "note warn" }, icon("⚠"), " ", NOT_A_RECEIPT_TEXT));
    }
    children.push(
      h(
        "p",
        { className: "note" },
        "Type the values from the photo. Nothing from the failed extraction is filled in.",
      ),
    );
  } else {
    children.push(statusLine());
    if (standalone && !expense.confirmed) {
      children.push(
        h("p", { className: "note" }, "Saved as a draft, not confirmed yet. Further saves update it."),
      );
    }
    if (expense.confirmed) {
      children.push(
        h(
          "p",
          { className: "note ok" },
          icon("✔"),
          readOnly
            ? " Confirmed. It counts towards budgets and insights."
            : " Confirmed. Save draft with a change un-confirms it; Save & confirm confirms it again.",
        ),
      );
    }
    const mode = badgeMode();
    if (mode === "note") {
      children.push(h("p", { className: "note ai" }, icon("ℹ"), " ", CORRECTED_NOTE));
    } else if (mode === "fields") {
      children.push(
        h(
          "p",
          { className: "note ai" },
          icon("ℹ"),
          " Values marked ",
          h("span", { className: "badge-ai" }, "AI-generated"),
          " were read from the photo by the AI. Compare them with the image; editing a value removes its mark.",
        ),
      );
    }
  }

  const { byField, byItemId, listFlags, general } = sortFlags();
  // A flag on an item removed from the form has no row left to sit on.
  const shownIds = new Set(state.draft.items.map((row) => row.id));
  for (const [id, flags] of byItemId) {
    if (!shownIds.has(id)) general.push(...flags);
  }
  if (general.length) {
    children.push(
      h("p", { className: "hint", id: "other-findings" }, "Other findings (not a field on this form):"),
      h(
        "ul",
        { className: "flag-list", attrs: { "aria-labelledby": "other-findings" } },
        ...general.map((flag) => h("li", {}, flagEntry(flag, undefined, readOnly).root)),
      ),
    );
  }
  const stray = unmatchedErrors();
  if (stray.length) {
    children.push(
      h(
        "ul",
        { className: "flag-list", attrs: { "aria-label": "Errors" } },
        ...stray.map((err) => h("li", {}, errorNode(`${err.path}: ${err.message}`))),
      ),
    );
  }

  const form = h("form", { noValidate: true, attrs: { "aria-label": "Expense" } });
  // Both actions leave the page, so Enter in a field doesn't trigger one; the buttons do.
  form.addEventListener("submit", (e) => e.preventDefault());

  form.append(
    h(
      "fieldset",
      { className: "fields", disabled: false },
      h("legend", { className: "visually-hidden" }, "Expense details"),
      ...SCALARS.map((def) => scalarField(def, readOnly, byField.get(def.name) || [])),
    ),
  );

  const itemsHeading = h("h2", { id: "items-heading" }, "Line items");
  form.append(itemsHeading);
  for (const flag of listFlags) form.append(h("div", {}, flagEntry(flag, undefined, readOnly).root));
  for (const err of errorsFor("line_items")) form.append(h("p", {}, errorNode(err.message)));
  if (state.draft.items.length === 0) {
    form.append(
      h("p", { className: "unknown" }, "No line items were extracted: add at least one item to save."),
    );
  }
  form.append(itemsTable(readOnly, byItemId));

  refs.sum = h("span", { className: "hint", attrs: { "aria-live": "polite" } }, itemsSumText());
  const footer = h("div", { className: "items-footer" });
  if (!readOnly) {
    refs.addButton = h(
      "button",
      {
        type: "button",
        on: {
          click: () => {
            const row = newItem();
            state.draft.items.push(row);
            render();
            const input = document.getElementById(`item-${row.key}-description`);
            if (input) input.focus();
          },
        },
      },
      "Add item",
    );
    footer.append(refs.addButton);
  }
  footer.append(refs.sum);
  form.append(footer);

  form.append(actionsBar(manual, readOnly));
  children.push(form);
  panel.replaceChildren(...children.filter(Boolean));
  updateActions();
}

function actionsBar(manual, readOnly) {
  const bar = h("div", { className: "actions" });
  refs.reason = h("p", { className: "reason hint", id: "action-reason" });
  if (readOnly) {
    bar.append(
      h(
        "button",
        {
          type: "button",
          className: "primary",
          on: {
            click: () => {
              state.unlocked = true;
              state.checked.clear();
              render();
              const first = document.getElementById("f-merchant");
              if (first) first.focus();
            },
          },
        },
        "Edit again",
      ),
    );
    if (standalone) bar.append(h("a", { className: "button", href: "index.html" }, "Back to home"));
    return bar;
  }
  refs.saveConfirm = h(
    "button",
    {
      type: "button",
      className: "primary",
      attrs: { "aria-describedby": "action-reason" },
      on: { click: onSaveAndConfirm },
    },
    "Save & confirm",
  );
  refs.saveDraft = h(
    "button",
    {
      type: "button",
      attrs: { "aria-describedby": "action-reason" },
      on: { click: onSaveDraft },
    },
    "Save draft",
  );
  bar.append(refs.saveConfirm, refs.saveDraft);
  if (standalone) {
    // A plain link home: the beforeunload check warns about unsaved changes.
    bar.append(h("a", { className: "button", href: "index.html" }, manual ? "Cancel" : "Back to home"));
  }
  if (manual && !standalone) {
    bar.append(
      h(
        "button",
        {
          type: "button",
          on: {
            click: () => {
              state.mode = "failed";
              state.draft = null;
              clearError();
              render();
            },
          },
        },
        "Cancel",
      ),
    );
  } else if (!manual) {
    refs.dirty = h("span", { className: "dirty-marker" });
    bar.append(refs.dirty);
  }
  bar.append(refs.reason);
  return bar;
}

function labelsOf(names) {
  return SCALARS.filter((def) => names.includes(def.name)).map((def) => def.label.toLowerCase());
}

/** Updates the two buttons, their reasons and the item sum without rebuilding the form. */
function updateActions() {
  if (refs.sum) refs.sum.textContent = itemsSumText();
  // Every edit and every "Looks right"/Undo comes through here, so the flags follow too.
  // The edited state is computed once here and shared by the flags and the dirty marker.
  const edited = editedState();
  syncFlags(edited);
  if (!refs.saveConfirm) return;
  const manual = state.mode === "manual";
  const reasons = [];
  const missing = CONFIRM_REQUIRED.filter((name) => state.draft[name].trim() === "");

  // Save draft: POST needs merchant, date and total; a PATCH can't empty them once set.
  let draftBlocked = false;
  if (manual) {
    if (missing.length) reasons.push(`To save, fill in ${labelsOf(missing).join(", ")}.`);
  } else {
    refs.dirty.textContent = isDirty(edited) ? "Unsaved changes" : "";
    const emptied = emptiedRequired();
    if (emptied.length) {
      draftBlocked = true;
      reasons.push(
        `${emptied.map((def) => def.label).join(", ")} can't be emptied once set: enter a value to save.`,
      );
    }
  }
  refs.saveDraft.disabled = state.busy || draftBlocked;

  // Save & confirm: confirming needs merchant, date, total, at least one item and a
  // category on every item (the server answers 422 incomplete_expense otherwise).
  const confirmReasons = [];
  if (missing.length) confirmReasons.push(`Fill in ${labelsOf(missing).join(", ")}.`);
  if (state.draft.items.length === 0) confirmReasons.push("Add at least one line item.");
  const uncategorized = state.draft.items.filter((row) => row.category === UNCATEGORIZED).length;
  if (uncategorized) {
    confirmReasons.push(
      `Choose a category for every item (${uncategorized} ${uncategorized === 1 ? "item" : "items"} left).`,
    );
  }
  refs.saveConfirm.disabled = state.busy || draftBlocked || confirmReasons.length > 0;
  if (confirmReasons.length) {
    reasons.push(`Save & confirm is unavailable: ${confirmReasons.join(" ")}`);
  }
  refs.reason.textContent = reasons.join(" ");
}

function focusFirstError() {
  const invalid = panel.querySelector("[aria-invalid='true']") || panel.querySelector(".field-error");
  if (invalid) invalid.focus();
}

/** Leaves for the upload page without the unsaved-changes warning. */
function goHome() {
  state.leaving = true;
  window.location.href = "index.html";
}

/**
 * Validates and, if anything changed, sends the POST (manual entry, with `receipt_id`) or the
 * PATCH. Resolves to true once the server has the form's values; otherwise the page shows why.
 */
async function saveChanges() {
  const manual = state.mode === "manual";
  const errors = validate(manual);
  if (errors.length) {
    state.fieldErrors = errors;
    render();
    focusFirstError();
    announce("Some values need fixing before saving.");
    return false;
  }
  if (!manual && !isDirty()) return true;
  try {
    let expense;
    if (manual) {
      expense = await api.post("/expenses", buildCreate());
      state.mode = "review";
      if (standalone) {
        // The page stays open if the confirm is refused: put the new id in the URL (no
        // navigation), so a reload reopens this draft instead of an empty form that would
        // create a second expense.
        window.history.replaceState(null, "", `review.html?expense=${expense.id}`);
      }
    } else {
      expense = await api.patch(`/expenses/${state.expense.id}`, buildPatch());
    }
    state.unlocked = false;
    applyExpense(expense);
    return true;
  } catch (err) {
    state.busy = false;
    state.fieldErrors =
      err instanceof ApiError && err.fields
        ? err.fields.map((field) => ({ path: field.field, message: field.message }))
        : [];
    render();
    showError(err);
    return false;
  }
}

async function runSave(confirmAfter) {
  if (state.busy) return;
  state.busy = true;
  updateActions();
  clearError();
  const saved = await saveChanges();
  if (!saved) {
    state.busy = false;
    updateActions();
    return;
  }
  if (!confirmAfter) {
    goHome();
    return;
  }
  try {
    await api.post(`/expenses/${state.expense.id}/confirm`);
    goHome();
  } catch (err) {
    // 422 uncategorized_items or incomplete_expense: stay on the saved expense with its
    // recomputed flags, and show the server's reason.
    state.busy = false;
    render();
    showError(err);
    announce("Saved, but not confirmed.");
  }
}

function onSaveAndConfirm() {
  runSave(true);
}

function onSaveDraft() {
  runSave(false);
}

// ---------------------------------------------------------------- page

function render() {
  // Every screen but processing is drawn here, so leaving processing stops the poll.
  stopPolling();
  if (state.mode === "failed") renderFailed();
  else if (state.mode === "review" || state.mode === "manual") renderForm();
}

function renderImage() {
  const src = `/api/receipts/${receiptId}/image`;
  const img = h("img", { src, alt: `Photo of receipt ${receiptId}` });
  const caption = h(
    "figcaption",
    { className: "hint" },
    h("a", { href: src, target: "_blank", rel: "noopener" }, "Open the photo in full size"),
  );
  img.addEventListener("error", () => {
    caption.textContent = "The photo couldn't be loaded.";
  });
  figure.replaceChildren(img, caption);
}

window.addEventListener("beforeunload", (e) => {
  if (state.leaving) return;
  const unsaved =
    (state.mode === "review" && state.expense && isDirty()) ||
    (state.mode === "manual" &&
      state.draft &&
      (state.draft.merchant.trim() !== "" ||
        state.draft.date.trim() !== "" ||
        state.draft.total.trim() !== "" ||
        state.draft.items.some((row) => row.description.trim() !== "")));
  if (unsaved) {
    e.preventDefault();
    e.returnValue = "";
  }
});

function initStandalone() {
  document.title = "Add an expense · Budgie";
  figure.hidden = true;
  layout.classList.add("single");
  state.mode = "manual";
  state.expense = null;
  state.draft = emptyDraft();
  state.fieldErrors = [];
  render();
  const first = panel.querySelector("input");
  if (first) first.focus();
}

function expenseUnavailable(heading) {
  panel.replaceChildren(
    h("h1", {}, heading),
    h(
      "p",
      {},
      h("a", { href: "index.html" }, "Back to home"),
      " or ",
      h("a", { href: "review.html?manual=1" }, "add an expense manually"),
      ".",
    ),
  );
}

/** `review.html?expense=<id>`: the standalone review of an expense without a photo. */
async function initExpense() {
  document.title = "Expense · Budgie";
  figure.hidden = true;
  layout.classList.add("single");
  if (expenseId === null) {
    expenseUnavailable("Expense not found");
    return;
  }
  let expense;
  try {
    expense = await api.get(`/expenses/${expenseId}`);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      expenseUnavailable("Expense not found");
    } else {
      showError(err);
      expenseUnavailable("Expense not available");
    }
    return;
  }
  if (expense.receipt_id !== null && expense.receipt_id !== undefined) {
    // An expense with a photo keeps the receipt flow.
    state.leaving = true;
    window.location.replace(`review.html?id=${expense.receipt_id}`);
    return;
  }
  state.unlocked = false;
  applyExpense(expense);
  state.mode = "review";
  render();
}

async function init() {
  if (expenseParam) {
    initExpense();
    return;
  }
  if (standalone) {
    initStandalone();
    return;
  }
  if (receiptId === null) {
    figure.hidden = true;
    layout.classList.add("single");
    panel.replaceChildren(
      h("h1", {}, "No receipt selected"),
      h(
        "p",
        {},
        h("a", { href: "index.html" }, "Upload a receipt"),
        ", pick one from the list, or ",
        h("a", { href: "review.html?manual=1" }, "add an expense manually"),
        ".",
      ),
    );
    return;
  }
  renderImage();
  try {
    const receipt = await api.get(`/receipts/${receiptId}`);
    route(receipt);
  } catch (err) {
    showError(err);
    panel.replaceChildren(
      h("h1", {}, "Receipt not available"),
      h("p", {}, h("a", { href: "index.html" }, "Back to the upload page")),
    );
  }
}

init();
