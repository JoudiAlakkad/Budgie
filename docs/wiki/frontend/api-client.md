# API client

The file is `frontend/js/api.js`, and it's the only module that calls `fetch`. Built in F06, with `js/dom.js` (text-only DOM helpers, the receipt status words, `formatTimestamp` and, since F08, `formatDate` for a `YYYY-MM-DD` date as de-DE, using UTC) and `js/categories.js` (`CATEGORIES` in the contract's order, and since F08 `SPENDING_CATEGORIES` without `deposit` and `discount`; `tests/unit/test_frontend_static.py` checks it against `api/schemas.py`).

- `api.get/post/patch/put/delete(path, body?)`:
  - prefixes `/api`
  - sends and parses JSON
  - on a non-2xx response, throws an `ApiError {status, error, detail, fields}` built from the [error format](../contracts/error-format.md); `fields` marks the form inputs that failed validation
- `api.upload(file)` sends the multipart `POST /receipts`.
- `pollReceipt(id, onUpdate)` polls every 2 s until the status is `extracted`, `failed` or `confirmed`, and stops after 5 minutes with a "still processing" message ([receipt-lifecycle](../contracts/receipt-lifecycle.md)).
- `showError(err)` shows `err.detail` in a banner.
- A failed receipt is not an `ApiError`: `GET /receipts/{id}` returns `200` with `status: failed`. The page shows `error_detail` and the actions for its `error` code ([receipt error codes](../contracts/error-format.md#receipt-error-codes)).
- Money arrives as a JSON number and is formatted with `Intl.NumberFormat('de-DE', {style: 'currency', currency})`.
- There is no other state. Pages fetch fresh data on load, and nothing is cached in `localStorage` except UI preferences.
