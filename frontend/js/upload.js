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

async function loadRecent() {
  try {
    const receipts = await api.get("/receipts");
    recentList.replaceChildren(
      ...receipts.map((receipt) =>
        h(
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
        ),
      ),
    );
    recentEmpty.hidden = receipts.length > 0;
  } catch (err) {
    showError(err);
  }
}

loadRecent();
