// Upload page: drag and drop or pick a photo, POST /receipts, then open the review page.
import { ApiError, api, clearError, formatMoney, showError } from "./api.js";
import { RECEIPT_STATUS_WORDS, formatDate, formatTimestamp, h } from "./dom.js";
import { MAX_UPLOAD_BYTES, MAX_UPLOAD_MB, preparePhoto } from "./resize.js";

const dropZone = document.getElementById("drop-zone");
const fileInput = document.getElementById("file-input");
const statusLine = document.getElementById("upload-status");
const recentList = document.getElementById("recent-list");
const recentEmpty = document.getElementById("recent-empty");

let uploading = false;

const TOO_LARGE_MESSAGE = `This photo is larger than ${MAX_UPLOAD_MB} MB, the upload limit.`;

/** The upload is over; the page accepts another file. */
function resetUpload() {
  uploading = false;
  fileInput.disabled = false;
  fileInput.value = "";
  dropZone.removeAttribute("aria-busy");
}

/** Shows an error in the banner; a 413 says the limit, which the server's detail doesn't. */
function showUploadError(err) {
  if (err instanceof ApiError && err.error === "file_too_large") {
    showError(new ApiError(err.status, err.error, TOO_LARGE_MESSAGE, null));
  } else {
    showError(err);
  }
}

async function uploadFile(file) {
  if (!file || uploading) return;
  uploading = true;
  clearError();
  fileInput.disabled = true;
  dropZone.setAttribute("aria-busy", "true");
  // Downsizing a phone photo takes a moment (decision 0027); it never rejects.
  statusLine.textContent = "Preparing photo…";
  const upload = await preparePhoto(file);
  if (upload.size > MAX_UPLOAD_BYTES) {
    // Refused here, before the transfer; the same code the API would answer with.
    statusLine.textContent = "";
    showError(new ApiError(413, "file_too_large", TOO_LARGE_MESSAGE, null));
    resetUpload();
    return;
  }
  statusLine.textContent = `Uploading ${file.name}…`;
  try {
    const receipt = await api.upload(upload);
    statusLine.textContent = "Uploaded. Opening the review page…";
    window.location.href = `review.html?id=${encodeURIComponent(receipt.id)}`;
  } catch (err) {
    // 422 unsupported_file carries a readable detail; 413 gets one that names the limit.
    statusLine.textContent = "";
    showUploadError(err);
    resetUpload();
  }
}

fileInput.addEventListener("change", () => {
  uploadFile(fileInput.files && fileInput.files[0]);
});

for (const event of ["dragenter", "dragover"]) {
  dropZone.addEventListener(event, (e) => {
    e.preventDefault();
    dropZone.classList.add("drag-over");
  });
}
for (const event of ["dragleave", "dragend"]) {
  dropZone.addEventListener(event, () => dropZone.classList.remove("drag-over"));
}
dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropZone.classList.remove("drag-over");
  const files = e.dataTransfer && e.dataTransfer.files;
  if (files && files.length > 1) {
    statusLine.textContent = "Only the first file is uploaded.";
  }
  uploadFile(files && files[0]);
});
// A file dropped next to the zone would otherwise open in the browser tab.
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => e.preventDefault());

function receiptSummary(receipt) {
  const expense = receipt.expense;
  if (!expense) return `Receipt ${receipt.id}`;
  const parts = [expense.merchant || `Receipt ${receipt.id}`];
  if (expense.total !== null) parts.push(formatMoney(expense.total, expense.currency));
  return parts.join(" · ");
}

function receiptRow(receipt) {
  return h(
    "li",
    {},
    h(
      "a",
      { href: `review.html?id=${encodeURIComponent(receipt.id)}` },
      h("span", {}, receiptSummary(receipt)),
      h(
        "span",
        {},
        h("span", { className: "status-word" }, RECEIPT_STATUS_WORDS[receipt.status] || receipt.status),
        " · uploaded ",
        h("time", { dateTime: receipt.uploaded_at }, formatTimestamp(receipt.uploaded_at)),
      ),
    ),
  );
}

/** An expense entered by hand without a photo (`receipt_id` is null). */
function manualRow(expense) {
  const parts = [expense.merchant || "unknown merchant"];
  if (expense.total !== null) parts.push(formatMoney(expense.total, expense.currency));
  return h(
    "li",
    {},
    h(
      "a",
      { href: `review.html?expense=${encodeURIComponent(expense.id)}` },
      h("span", {}, parts.join(" · ")),
      h(
        "span",
        {},
        h(
          "span",
          { className: "status-word" },
          `Manual entry · ${expense.confirmed ? "confirmed" : "draft"}`,
        ),
        " · ",
        expense.date
          ? h("time", { dateTime: expense.date }, formatDate(expense.date))
          : "no date",
      ),
    ),
  );
}

/** A UTC timestamp as milliseconds; an unreadable one sorts last. */
function timeOf(iso) {
  const ms = Date.parse(iso || "");
  return Number.isNaN(ms) ? -Infinity : ms;
}

/**
 * Receipts and manual expenses in one list, newest first by when they were added: a
 * receipt by `uploaded_at`, a manual expense by `created_at` (not its purchase date), so
 * an expense entered just now is at the top. Ties by id, highest first.
 */
function recentEntries(receipts, manualExpenses) {
  const entries = [
    ...receipts.map((receipt) => ({ time: timeOf(receipt.uploaded_at), id: receipt.id, row: receiptRow(receipt) })),
    ...manualExpenses.map((expense) => ({ time: timeOf(expense.created_at), id: expense.id, row: manualRow(expense) })),
  ];
  entries.sort((a, b) => {
    if (a.time !== b.time) return a.time < b.time ? 1 : -1;
    return b.id - a.id;
  });
  return entries.map((entry) => entry.row);
}

async function loadRecent() {
  // Both lists load in parallel; if one fails, the other is still shown.
  const [receipts, expenses] = await Promise.allSettled([
    api.get("/receipts"),
    api.get("/expenses?has_receipt=false"),
  ]);
  const failed = [receipts, expenses].find((result) => result.status === "rejected");
  if (failed) showError(failed.reason);
  const receiptList = receipts.status === "fulfilled" ? receipts.value : [];
  // The server already filters (`has_receipt=false`); this guard keeps a receipt's expense
  // from being listed twice should the filter ever be ignored.
  const manualExpenses =
    expenses.status === "fulfilled" ? expenses.value.filter((expense) => expense.receipt_id === null) : [];
  const rows = recentEntries(receiptList, manualExpenses);
  recentList.replaceChildren(...rows);
  recentEmpty.hidden = rows.length > 0 || Boolean(failed);
}

loadRecent();
