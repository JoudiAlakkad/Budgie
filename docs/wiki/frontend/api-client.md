# API client

The file is `frontend/js/api.js`, and it's the only module that calls `fetch`. Built in F06, with `js/dom.js` (text-only DOM helpers, the receipt status words, since F10 the shared `REVIEW_STATUS_WORDS` and `SOURCE_WORDS`, `formatTimestamp` and, since F08, `formatDate` for a `YYYY-MM-DD` date as de-DE, using UTC) and `js/categories.js` (`CATEGORIES` in the contract's order, and since F08 `SPENDING_CATEGORIES` without `deposit` and `discount`; `tests/unit/test_frontend_static.py` checks it against `api/schemas.py`).

- `api.get/post/patch/put/delete(path, body?)`:
  - prefixes `/api`
  - sends and parses JSON
  - on a non-2xx response, throws an `ApiError {status, error, detail, fields}` built from the [error format](../contracts/error-format.md); `fields` marks the form inputs that failed validation
- `api.upload(file)` sends the multipart `POST /receipts`.
- `api.download(path)` (F10) does a `GET`, throws an `ApiError` on non-2xx like the others, and saves the body as a file: the name comes from `Content-Disposition` (fallback `budgie-expenses.csv`), through a temporary `<a download>` and `URL.createObjectURL`; the URL is revoked after 1 s, because revoking in the same task can cancel the download. It reads `filename*=` and `filename=`, drops any path part, and resolves to the filename. `test_frontend_static.py` checks its path like any `api.get`.
- `pollReceipt(id, onUpdate, onSlow)` polls every 2 s until the status is `extracted`, `failed` or `confirmed` ([receipt-lifecycle](../contracts/receipt-lifecycle.md)).
  - After 5 minutes it calls `onSlow()` once, then keeps polling every 10 s. It never gives up on time.
  - **Failed polls (third `/code-review`):** a poll that fails with a network error (`ApiError` status 0) or a 5xx is retried at the current interval, because a laptop sleep, a Wi-Fi drop or an app restart shouldn't end a long extraction. After **3 failures in a row** it rejects with the last error. Any successful poll resets the count. A 4xx, e.g. `404` for a deleted receipt, and any non-`ApiError` reject at once. Implemented with `POLL_MAX_FAILURES = 3`; a retry waits the current interval, and the 5-minute `onSlow` check also runs on the retry path. After polling stops with an error, the review page removes the slow note and forgets it, so a restarted poll could show it again. `StillProcessingError` was removed with the time limit, so `showError` logs only errors that aren't `ApiError`s.
  - The review page then shows a note in the spinner box: "Still processing. On a CPU-only model server this can take several minutes; the page keeps checking."
  - **Why (F12, `/code-review`):** the old hard stop at 5 minutes was shorter than the backend's own worst case: `LLM_TIMEOUT_S` × (1 + `LLM_MAX_RETRIES`), plus a repair turn. That is 360 s with the defaults and far more with the CPU override (600 s). A UI limit can't know the server's settings, and an extraction always ends in a final status (on restart, `interrupted`), so polling until the end is safe.
- `showError(err)` shows `err.detail` in a banner.
- A failed receipt is not an `ApiError`: `GET /receipts/{id}` returns `200` with `status: failed`. The page shows `error_detail` and the actions for its `error` code ([receipt error codes](../contracts/error-format.md#receipt-error-codes)).
- Money arrives as a JSON number and is formatted with `Intl.NumberFormat('de-DE', {style: 'currency', currency})`.
- There is no other state. Pages fetch fresh data on load, and nothing is cached in `localStorage` except UI preferences.
