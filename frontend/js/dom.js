// Small DOM helpers. Text is always set with `textContent`/text nodes, never parsed as HTML.

/**
 * Creates an element. `props` are set as properties (`className`, `type`, `disabled`, ...),
 * except `attrs` (set with setAttribute), `dataset` and `on` (event listeners).
 * Children may be nodes, strings (inserted as text) or null/false (skipped).
 */
export function h(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null) continue;
    if (key === "attrs") {
      for (const [name, attr] of Object.entries(value)) {
        if (attr !== undefined && attr !== null && attr !== false) node.setAttribute(name, attr);
      }
    } else if (key === "dataset") {
      Object.assign(node.dataset, value);
    } else if (key === "on") {
      for (const [event, handler] of Object.entries(value)) node.addEventListener(event, handler);
    } else {
      node[key] = value;
    }
  }
  append(node, children);
  return node;
}

function append(node, children) {
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

/** An icon that screen readers skip; the text next to it carries the meaning. */
export function icon(symbol) {
  return h("span", { className: "icon", attrs: { "aria-hidden": "true" } }, symbol);
}

export const RECEIPT_STATUS_WORDS = {
  uploaded: "Waiting for extraction",
  extracting: "Extracting",
  extracted: "Ready for review",
  failed: "Extraction failed",
  confirmed: "Confirmed",
};

/** `Expense.review_status` in words (a rule result, never a probability; decision 0008). */
export const REVIEW_STATUS_WORDS = {
  accepted: "Accepted",
  needs_review: "Needs review",
  rejected: "Nothing usable extracted",
};

/**
 * A `YYYY-MM-DD` date as `08.10.2026` (de-DE). The date has no time zone, so it is
 * formatted in UTC and never shifts by a day. Anything else is returned unchanged
 * ("" for null or undefined).
 */
export function formatDate(iso) {
  if (iso === null || iso === undefined) return "";
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!match) return iso;
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString("de-DE", { dateStyle: "medium", timeZone: "UTC" });
}

export function formatTimestamp(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("de-DE", { dateStyle: "medium", timeStyle: "short" });
}
