// Review page: the receipt photo beside the expense form (docs/wiki/frontend/pages.md).
// Editing semantics and the client-side AI badge follow decision 0018; the lean field set,
// Save & confirm and Save draft follow decision 0019.
import { ApiError, api, clearError, formatMoney, pollReceipt, showError } from "./api.js";
import { CATEGORIES, UNCATEGORIZED, categoryLabel } from "./categories.js";
import { RECEIPT_STATUS_WORDS, h, icon } from "./dom.js";

const REVIEW_WORDS = {
  accepted: "Accepted",
  needs_review: "Needs review",
  rejected: "Nothing usable extracted",
};

const SOURCE_WORDS = {
  ai: "Extracted by AI",
  ai_corrected: "Extracted by AI, corrected by you",
  manual: "Entered by you",
};

const CATEGORY_SOURCE_HINTS = { user: "from your earlier choice", seed: "default" };

const NOT_A_RECEIPT_TEXT = "The image wasn't recognised as a receipt.";
const SAME_RESULT_TEXT =
  "A retry with the same model usually gives the same result; it helps only after a model or prompt change.";
const CORRECTED_NOTE = "AI-extracted, corrected by you — check the remaining values";
const UNKNOWN_TEXT = "unknown, please fill in";

const SCALARS = [
  { name: "merchant", label: "Merchant", kind: "text", required: true },
  { name: "date", label: "Date", kind: "date", required: true },
  { name: "currency", label: "Currency", kind: "currency", required: true },
  { name: "total", label: "Total", kind: "money", required: true },
];
// Flags on any other field (subtotal, tax, payment_method, ...) have no input and go to
// "Other findings", so none is dropped. Fields not shown are never sent: the server keeps them.
const SCALAR_NAMES = new Set(SCALARS.map((def) => def.name));
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
  aiTracked: false, // this session saw the expense as unconfirmed `ai`, so per-field badges are known
  leaving: false, // navigating away after a successful save or delete: no unsaved-changes warning
  nextKey: 1,
  fieldErrors: [], // [{path, message}] from local validation or ApiError.fields
};

// Nodes that input handlers update without a full re-render.
let refs = {};

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

function itemsChanged() {
  const server = state.expense.line_items;
  const rows = state.draft.items;
  if (rows.length !== server.length) return true;
  return rows.some((row, i) => {
    const item = server[i];
    if (row.id !== item.id || row.category !== item.category) return true;
    return ITEM_FIELDS.some((def) => !sameValue(parseValue(def.kind, row[def.name]), item[def.name]));
  });
}

function isDirty() {
  if (!state.expense) return false;
  return SCALARS.some(scalarChanged) || itemsChanged();
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
  // Send a category only when the user chose one, so a stored category keeps its source.
  // A user's earlier choice is sent again when the description changes, or the server
  // would recategorise the item (decision 0018).
  if (row.category !== UNCATEGORIZED) {
    const item = serverItem(row.id);
    const descriptionChanged =
      item !== null && !sameValue(parseValue("text", row.description), item.description);
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
  return body;
}

function buildCreate() {
  const body = { receipt_id: receiptId };
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
  return h("div", { className: "panel-header" }, h("h1", {}, title), deleteButton());
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
    goHome();
  } catch (err) {
    showError(err);
  } finally {
    state.busy = false;
  }
}

// ---------------------------------------------------------------- processing

async function startProcessing() {
  state.mode = "processing";
  const statusText = h(
    "span",
    {},
    RECEIPT_STATUS_WORDS[state.receipt.status] || state.receipt.status,
    "…",
  );
  panel.replaceChildren(
    panelHeader("Reading your receipt"),
    h(
      "div",
      { className: "spinner-box", attrs: { role: "status" } },
      h("div", { className: "spinner", attrs: { "aria-hidden": "true" } }),
      statusText,
    ),
    h(
      "p",
      { className: "hint" },
      "The AI reads one receipt at a time; this can take a minute. The page updates by itself.",
    ),
  );
  try {
    const receipt = await pollReceipt(receiptId, (update) => {
      statusText.textContent = `${RECEIPT_STATUS_WORDS[update.status] || update.status}…`;
    });
    if (state.mode !== "processing") return;
    route(receipt);
  } catch (err) {
    if (state.mode !== "processing") return;
    showError(err);
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
  clearError();
  state.mode = "manual";
  state.expense = null;
  state.draft = emptyDraft();
  state.fieldErrors = [];
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
  const describedBy = [];
  const extra = [];

  flags.forEach((flag, i) => {
    const flagId = `${id}-flag-${i}`;
    describedBy.push(flagId);
    extra.push(flagNode(flag, flagId));
  });
  const errors = errorsFor(def.name);
  errors.forEach((err, i) => {
    const errId = `${id}-err-${i}`;
    describedBy.push(errId);
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
      "aria-describedby": describedBy.join(" ") || null,
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

  return h(
    "div",
    { className: flags.length ? "field flagged" : "field" },
    label,
    input,
    unknown,
    ...extra,
  );
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
          describedBy.push(`${id}-flag-${i}`);
          extra.push(flagNode(flag, `${id}-flag-${i}`));
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
  const describedBy = [];
  categoryFlags.forEach((flag, i) => {
    describedBy.push(`${selectId}-flag-${i}`);
    catExtra.push(flagNode(flag, `${selectId}-flag-${i}`));
  });
  const missingNote = h(
    "span",
    { className: "flag-message", id: `${selectId}-missing` },
    icon("⚠"),
    " Choose a category before confirming.",
  );
  missingNote.hidden = row.category !== UNCATEGORIZED || categoryFlags.length > 0 || readOnly;
  describedBy.push(missingNote.id);
  const sourceHint = h("span", { className: "hint" });
  const updateSourceHint = () => {
    const hint =
      item && item.category === row.category ? CATEGORY_SOURCE_HINTS[item.category_source] : null;
    sourceHint.textContent = hint || "";
    sourceHint.hidden = !hint;
  };
  updateSourceHint();
  const categoryErrors = errorsFor(`line_items.${index}.category`);
  categoryErrors.forEach((err, i) => {
    describedBy.push(`${selectId}-err-${i}`);
    catExtra.push(errorNode(err.message, `${selectId}-err-${i}`));
  });
  const select = h(
    "select",
    {
      id: selectId,
      disabled: readOnly,
      required: true,
      attrs: {
        "aria-describedby": describedBy.join(" ") || null,
        "aria-invalid": categoryErrors.length ? "true" : null,
      },
    },
    ...categoryOptions(row.category),
  );
  select.value = row.category;
  select.addEventListener("change", () => {
    row.category = select.value;
    tr.classList.toggle("needs-category", row.category === UNCATEGORIZED);
    missingNote.hidden = row.category !== UNCATEGORIZED || categoryFlags.length > 0;
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

  if (flags.length) tr.classList.add("flagged");
  if (row.category === UNCATEGORIZED && !readOnly) tr.classList.add("needs-category");
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
      h("span", { className: "review-status" }, REVIEW_WORDS[expense.review_status] || expense.review_status),
    ),
    h("span", {}, "Source: ", SOURCE_WORDS[expense.source] || expense.source),
    h("span", {}, "Receipt: ", RECEIPT_STATUS_WORDS[state.receipt.status] || state.receipt.status),
  ];
  return h("div", { className: "status-line" }, ...parts);
}

function renderForm() {
  const manual = state.mode === "manual";
  const expense = state.expense;
  const readOnly = !manual && expense.confirmed && !state.unlocked;
  refs = { badges: new Map() };

  const title = manual ? "Enter the expense manually" : readOnly ? "Confirmed expense" : "Review the expense";
  const children = [panelHeader(title)];

  if (manual) {
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
        ...general.map((flag) => h("li", {}, flagNode(flag))),
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
  for (const flag of listFlags) form.append(h("p", {}, flagNode(flag)));
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
              render();
              const first = document.getElementById("f-merchant");
              if (first) first.focus();
            },
          },
        },
        "Edit again",
      ),
    );
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
  if (manual) {
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
  } else {
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
  if (!refs.saveConfirm) return;
  const manual = state.mode === "manual";
  const reasons = [];
  const missing = CONFIRM_REQUIRED.filter((name) => state.draft[name].trim() === "");

  // Save draft: POST needs merchant, date and total; a PATCH can't empty them once set.
  let draftBlocked = false;
  if (manual) {
    if (missing.length) reasons.push(`To save, fill in ${labelsOf(missing).join(", ")}.`);
  } else {
    refs.dirty.textContent = isDirty() ? "Unsaved changes" : "";
    const emptied = emptiedRequired();
    if (emptied.length) {
      draftBlocked = true;
      reasons.push(
        `${emptied.map((def) => def.label).join(", ")} can't be emptied once set: enter a value to save.`,
      );
    }
  }
  refs.saveDraft.disabled = state.busy || draftBlocked;

  // Save & confirm: confirming needs merchant, date, total and a category on every item.
  const confirmReasons = [];
  if (missing.length) confirmReasons.push(`Fill in ${labelsOf(missing).join(", ")}.`);
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
        state.draft.total.trim() !== "" ||
        state.draft.items.some((row) => row.description.trim() !== "")));
  if (unsaved) {
    e.preventDefault();
    e.returnValue = "";
  }
});

async function init() {
  if (receiptId === null) {
    figure.hidden = true;
    panel.replaceChildren(
      h("h1", {}, "No receipt selected"),
      h("p", {}, h("a", { href: "index.html" }, "Upload a receipt"), " or pick one from the list."),
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
