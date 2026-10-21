// The only module that calls `fetch` (docs/wiki/frontend/api-client.md).
// Paths passed to `api.*` are contract paths without the `/api` prefix.

const API_PREFIX = "/api";
const POLL_INTERVAL_MS = 2000;
const POLL_SLOW_AFTER_MS = 5 * 60 * 1000;
const POLL_SLOW_INTERVAL_MS = 10000;
const POLL_MAX_FAILURES = 3; // transient poll failures in a row before giving up
const FINAL_STATUSES = new Set(["extracted", "failed", "confirmed"]);

/** A non-2xx response, built from the error body (contracts/error-format.md). */
export class ApiError extends Error {
  constructor(status, error, detail, fields) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.error = error;
    this.detail = detail;
    this.fields = fields || null;
  }
}

async function errorFrom(response) {
  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  if (body && typeof body.error === "string" && typeof body.detail === "string") {
    return new ApiError(response.status, body.error, body.detail, body.fields);
  }
  return new ApiError(
    response.status,
    "internal_error",
    `The server answered with an unexpected error (HTTP ${response.status}).`,
    null,
  );
}

async function request(method, path, body) {
  const init = { method, headers: { Accept: "application/json" } };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(API_PREFIX + path, init);
  } catch {
    throw new ApiError(0, "network_error", "Could not reach the server. Is Budgie running?", null);
  }
  if (!response.ok) {
    throw await errorFrom(response);
  }
  if (response.status === 204) {
    return null;
  }
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body) => request("POST", path, body),
  patch: (path, body) => request("PATCH", path, body),
  put: (path, body) => request("PUT", path, body),
  delete: (path) => request("DELETE", path),
  /** Multipart `POST /receipts` with the photo as `file`; resolves to the `Receipt`. */
  upload(file) {
    const form = new FormData();
    form.append("file", file);
    return request("POST", "/receipts", form);
  },
  download,
};

const DEFAULT_DOWNLOAD_NAME = "budgie-expenses.csv";

/**
 * The file name from a `Content-Disposition` header (`filename*=UTF-8''…` or
 * `filename="…"`), without any path part; null when there is none.
 */
function filenameFrom(header) {
  if (!header) return null;
  let name = null;
  const extended = /filename\*\s*=\s*([^']*)'[^']*'([^;]+)/i.exec(header);
  if (extended) {
    try {
      name = decodeURIComponent(extended[2].trim());
    } catch {
      name = null;
    }
  }
  if (!name) {
    const plain = /filename\s*=\s*(?:"((?:[^"\\]|\\.)*)"|([^;]+))/i.exec(header);
    if (plain) name = (plain[1] !== undefined ? plain[1].replace(/\\(.)/g, "$1") : plain[2]).trim();
  }
  if (!name) return null;
  // Never let a header pick a directory.
  name = name.split(/[\\/]/).pop().trim();
  return name || null;
}

/**
 * `GET` a file and save it through a temporary `<a download>` (F10, frontend/api-client.md).
 * Throws an `ApiError` on a non-2xx response, like the other calls. Resolves to the file name.
 */
async function download(path) {
  let response;
  try {
    response = await fetch(API_PREFIX + path, { method: "GET" });
  } catch {
    throw new ApiError(0, "network_error", "Could not reach the server. Is Budgie running?", null);
  }
  if (!response.ok) {
    throw await errorFrom(response);
  }
  let blob;
  try {
    blob = await response.blob();
  } catch {
    throw new ApiError(0, "network_error", "The download was interrupted. Please try again.", null);
  }
  const filename = filenameFrom(response.headers.get("Content-Disposition")) || DEFAULT_DOWNLOAD_NAME;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.hidden = true;
  document.body.append(link);
  try {
    link.click();
  } finally {
    link.remove();
    // Revoking in the same task can cancel the download in some browsers.
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return filename;
}

/**
 * Polls `GET /receipts/{id}` every 2 s until the status is extracted, failed or confirmed.
 * Calls `onUpdate(receipt)` after every successful poll and resolves to the final receipt.
 * After 5 minutes it calls `onSlow()` once and keeps polling every 10 s; it never
 * gives up on time.
 *
 * A failed poll with an `ApiError` of status 0 (network) or 5xx is retried at the
 * current interval; after 3 such failures in a row it rejects with the last error, and
 * any successful poll resets the count. A 4xx `ApiError` (e.g. 404 for a deleted
 * receipt) or any other error rejects at once (docs/wiki/frontend/api-client.md).
 *
 * Cancellation: pass `options.signal` (an `AbortSignal`). Once it aborts, no further tick
 * is scheduled, the result of a request already in flight is ignored, `onUpdate` and
 * `onSlow` aren't called again, and the promise rejects with `signal.reason` (a
 * `DOMException` named "AbortError" for a plain `controller.abort()`). Callers ignore
 * that rejection by checking `signal.aborted` first. An already aborted signal rejects
 * at once, without a request.
 *
 * @param {number} id
 * @param {(receipt: object) => void} [onUpdate]
 * @param {() => void} [onSlow]
 * @param {{signal?: AbortSignal}} [options]
 * @returns {Promise<object>} the receipt in its final status
 */
export function pollReceipt(id, onUpdate, onSlow, { signal } = {}) {
  const started = Date.now();
  let slow = false;
  let failures = 0;
  let timer = null;
  return new Promise((resolve, reject) => {
    const cancelled = () => Boolean(signal && signal.aborted);
    const onAbort = () => {
      if (timer !== null) clearTimeout(timer);
      timer = null;
      reject(signal.reason);
    };
    const settle = (fn, value) => {
      if (signal) signal.removeEventListener("abort", onAbort);
      fn(value);
    };
    const scheduleNext = () => {
      if (!slow && Date.now() - started >= POLL_SLOW_AFTER_MS) {
        slow = true;
        if (onSlow) onSlow();
      }
      timer = setTimeout(tick, slow ? POLL_SLOW_INTERVAL_MS : POLL_INTERVAL_MS);
    };
    const tick = async () => {
      timer = null;
      let receipt;
      try {
        receipt = await api.get(`/receipts/${id}`);
      } catch (err) {
        if (cancelled()) return;
        const transient = err instanceof ApiError && (err.status === 0 || err.status >= 500);
        failures += 1;
        if (!transient || failures >= POLL_MAX_FAILURES) {
          settle(reject, err);
          return;
        }
        scheduleNext();
        return;
      }
      if (cancelled()) return;
      failures = 0;
      if (onUpdate) onUpdate(receipt);
      if (FINAL_STATUSES.has(receipt.status)) {
        settle(resolve, receipt);
        return;
      }
      scheduleNext();
    };
    if (signal) {
      if (signal.aborted) {
        reject(signal.reason);
        return;
      }
      signal.addEventListener("abort", onAbort, { once: true });
    }
    tick();
  });
}

function errorBanner() {
  let banner = document.getElementById("error-banner");
  if (!banner) {
    banner = document.createElement("div");
    banner.id = "error-banner";
    banner.className = "error-banner";
    banner.setAttribute("role", "alert");
    const main = document.querySelector("main") || document.body;
    main.prepend(banner);
  }
  return banner;
}

/** Shows `err.detail` (or a generic message) in the page's error banner. */
export function showError(err) {
  const banner = errorBanner();
  banner.replaceChildren();
  const text = document.createElement("p");
  const icon = document.createElement("span");
  icon.className = "icon";
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = "⚠ ";
  text.append(icon, "Error: ");
  const message =
    (err && typeof err.detail === "string" && err.detail) || "Something went wrong.";
  text.append(message);
  const close = document.createElement("button");
  close.type = "button";
  close.className = "link-button";
  close.textContent = "Dismiss";
  close.addEventListener("click", clearError);
  banner.append(text, close);
  banner.hidden = false;
  if (!(err instanceof ApiError)) {
    console.error(err);
  }
}

export function clearError() {
  const banner = document.getElementById("error-banner");
  if (banner) {
    banner.replaceChildren();
    banner.hidden = true;
  }
}

/** Money as `1,99 €` (de-DE); falls back to the plain number for an odd currency code. */
export function formatMoney(amount, currency) {
  if (amount === null || amount === undefined) return "";
  try {
    return new Intl.NumberFormat("de-DE", { style: "currency", currency }).format(amount);
  } catch {
    return `${amount} ${currency}`;
  }
}
