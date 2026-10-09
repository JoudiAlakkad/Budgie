// The only module that calls `fetch` (docs/wiki/frontend/api-client.md).
// Paths passed to `api.*` are contract paths without the `/api` prefix.

const API_PREFIX = "/api";
const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 5 * 60 * 1000;
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

/** Polling gave up after 5 minutes; the receipt may still finish later. */
export class StillProcessingError extends Error {
  constructor() {
    const detail =
      "The receipt is still being processed. Reload the page in a moment to check again.";
    super(detail);
    this.name = "StillProcessingError";
    this.detail = detail;
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
 * Calls `onUpdate(receipt)` after every poll and resolves to the final receipt.
 * Rejects with `StillProcessingError` after 5 minutes, or with the `ApiError` of a failed poll.
 */
export function pollReceipt(id, onUpdate) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const tick = async () => {
      let receipt;
      try {
        receipt = await api.get(`/receipts/${id}`);
      } catch (err) {
        reject(err);
        return;
      }
      if (onUpdate) onUpdate(receipt);
      if (FINAL_STATUSES.has(receipt.status)) {
        resolve(receipt);
      } else if (Date.now() - started >= POLL_TIMEOUT_MS) {
        reject(new StillProcessingError());
      } else {
        setTimeout(tick, POLL_INTERVAL_MS);
      }
    };
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
  if (!(err instanceof ApiError) && !(err instanceof StillProcessingError)) {
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
