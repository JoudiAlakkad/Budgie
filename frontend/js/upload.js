// Upload page: drag and drop or pick a photo, POST /receipts, then open the review page.
import { api, clearError, formatMoney, showError } from "./api.js";
import { RECEIPT_STATUS_WORDS, formatTimestamp, h } from "./dom.js";

const dropZone = document.getElementById("drop-zone");
const fileInput = document.getElementById("file-input");
const statusLine = document.getElementById("upload-status");
const recentList = document.getElementById("recent-list");
const recentEmpty = document.getElementById("recent-empty");

let uploading = false;

async function uploadFile(file) {
  if (!file || uploading) return;
  uploading = true;
  clearError();
  fileInput.disabled = true;
  statusLine.textContent = `Uploading ${file.name}…`;
  try {
    const receipt = await api.upload(file);
    statusLine.textContent = "Uploaded. Opening the review page…";
    window.location.href = `review.html?id=${encodeURIComponent(receipt.id)}`;
  } catch (err) {
    // 413 file_too_large and 422 unsupported_file carry a readable detail.
    statusLine.textContent = "";
    showError(err);
    uploading = false;
    fileInput.disabled = false;
    fileInput.value = "";
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

function formatDate(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
  if (!match) return iso;
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  return date.toLocaleDateString("de-DE", { dateStyle: "medium", timeZone: "UTC" });
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

/**
 * Receipts and manual expenses in one list, newest first. A receipt sorts by `uploaded_at`,
 * a manual expense by its `date` (the Expense has no creation time), so a manual expense
 * sorts after the receipts uploaded on the same day (UTC); one without a date goes last.
 */
function recentEntries(receipts, manualExpenses) {
  const entries = [
    ...receipts.map((receipt) => ({ key: receipt.uploaded_at || "", id: receipt.id, row: receiptRow(receipt) })),
    ...manualExpenses.map((expense) => ({ key: expense.date || "", id: expense.id, row: manualRow(expense) })),
  ];
  entries.sort((a, b) => {
    if (a.key !== b.key) {
      if (!a.key) return 1;
      if (!b.key) return -1;
      return a.key < b.key ? 1 : -1;
    }
    return b.id - a.id;
  });
  return entries.map((entry) => entry.row);
}

async function loadRecent() {
  // Both lists load in parallel; if one fails, the other is still shown.
  const [receipts, expenses] = await Promise.allSettled([api.get("/receipts"), api.get("/expenses")]);
  const failed = [receipts, expenses].find((result) => result.status === "rejected");
  if (failed) showError(failed.reason);
  const receiptList = receipts.status === "fulfilled" ? receipts.value : [];
  // A receipt's expense is already listed through its receipt.
  const manualExpenses =
    expenses.status === "fulfilled" ? expenses.value.filter((expense) => expense.receipt_id === null) : [];
  const rows = recentEntries(receiptList, manualExpenses);
  recentList.replaceChildren(...rows);
  recentEmpty.hidden = rows.length > 0 || Boolean(failed);
}

loadRecent();
