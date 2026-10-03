# Error format

Every non-2xx response has this body:

```json
{ "error": "not_found", "detail": "Receipt 42 does not exist.", "fields": null }
```

- `error` is a stable machine-readable code, and `detail` is a message a person can read.
- `fields` is set only for `validation_error`: a list of `{field, message}`. `field` is the error location joined with `.`, without the `body` prefix, e.g. `line_items.0.amount`. Query and path errors keep their prefix, e.g. `query.month`. For every other code it is `null`.
- Stack traces, exception text and internal paths are never included. An HTTP status or error code that has no row below is logged and answered as `500 internal_error`.
- One mapping in `backend/app/api/errors.py` turns error codes into HTTP statuses. Routing errors from Starlette (unknown path, wrong method, broken multipart body) use the same format.

## HTTP error codes
| HTTP | `error` | When |
|---|---|---|
| 400 | `bad_request` | the multipart body can't be parsed at all, e.g. no boundary (raised by Starlette before validation); a body cut off mid-file arrives as a missing `file`, i.e. `422 validation_error` |
| 404 | `not_found` | an unknown id or path; an id that isn't an integer (e.g. `/receipts/abc`) is an unknown path |
| 405 | `method_not_allowed` | the path exists, but not with this method. The `Allow` header lists the methods of the first route that matches the path only (a Starlette limit) |
| 409 | `invalid_state` | the action isn't allowed in the current status, e.g. extracting a receipt that is `extracting`, or deleting a seed category |
| 413 | `file_too_large` | the upload is bigger than `MAX_UPLOAD_MB` |
| 422 | `unsupported_file` | the upload isn't a jpeg, png or webp (checked by its first bytes) |
| 422 | `uncategorized_items` | confirm was called while some items are `uncategorized` |
| 422 | `incomplete_expense` | confirm was called while merchant, date or total is missing |
| 422 | `validation_error` | a field, query or path parameter failed validation, the JSON is malformed (`field: "body"`), or an unknown field was sent; `fields` says which |
| 500 | `storage_error` | the database or a file couldn't be read or written |
| 500 | `internal_error` | an unexpected error; the details are only in the server log |
| 501 | `not_implemented` | the endpoint is in the contract but not built yet; `detail` names the feature that builds it |

No endpoint returns 503 or 504. Model problems happen during async extraction ([0007](../decisions/0007-async-extraction-with-polling.md)) and show up only as a receipt error.

## Receipt error codes
A receipt with `status: failed` carries one of these in `Receipt.error`, plus a fixed message in `Receipt.error_detail`. They are stored values, not HTTP responses.

| `error` | Cause | User actions |
|---|---|---|
| `llm_unavailable` | the model server is unreachable | Retry, Enter manually |
| `llm_timeout` | the model didn't answer in time, after one retry | Retry, Enter manually |
| `llm_error` | the model server answered with an error (e.g. unknown model) | Retry, Enter manually |
| `malformed_output` | the output wasn't valid JSON for the schema after one repair attempt, or was cut off at `LLM_MAX_TOKENS` | Retry, Enter manually |
| `not_a_receipt` | the model reported `is_receipt=false`, or the plausibility rule judged the output not to be a receipt ([0015](../decisions/0015-non-receipt-is-a-failure-with-retry-or-manual-entry.md)) | Retry, Enter manually |
| `unreadable_image` | the model server couldn't decode the image | Enter manually |
| `interrupted` | the app restarted while the receipt was `uploaded` or `extracting` | Retry, Enter manually |

- **Retry** is `POST /receipts/{id}/extract`. At temperature 0 the same model gives the same answer for the same image, so for `not_a_receipt` and `malformed_output` a retry helps only after a model or prompt change.
- **Enter manually** is `POST /expenses` with `receipt_id` ([api-endpoints](api-endpoints.md#expenses)).
