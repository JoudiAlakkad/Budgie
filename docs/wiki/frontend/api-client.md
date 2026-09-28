# API client

The file is `frontend/js/api.js`, and it's the only module that calls `fetch`. It's planned for F6.

- `api.get/post/patch/put/delete(path, body?)`:
  - prefixes `/api`
  - sends and parses JSON
  - on a non-2xx response, throws an `ApiError {status, error, detail}` built from the [error format](../contracts/error-format.md)
- `api.upload(file)` sends the multipart `POST /receipts`.
- `pollReceipt(id, onUpdate)` polls every 2 s until the status is `extracted`, `failed` or `confirmed`, and stops after 5 minutes with a "still processing" message ([receipt-lifecycle](../contracts/receipt-lifecycle.md)).
- `showError(err)` shows `err.detail` in a banner. The code in `err.error` decides the action, for example `llm_unavailable` offers Retry.
- Money is formatted with `Intl.NumberFormat('de-DE', {style: 'currency', currency})`.
- There is no other state. Pages fetch fresh data on load, and nothing is cached in `localStorage` except UI preferences.
