# Error format

Every non-2xx response has this body:

```json
{ "error": "llm_unavailable", "detail": "The local model server is not reachable. Your receipt was saved; retry extraction later." }
```

- `error` is a stable machine-readable code, and `detail` is a message a person can read.
- Stack traces and internal paths are never included.

| HTTP | `error` | When |
|---|---|---|
| 400 | `bad_request` | the request has the wrong shape |
| 404 | `not_found` | an unknown id |
| 409 | `invalid_state` | the action isn't allowed in the current status, e.g. extracting a receipt that is already `extracting` |
| 413 | `file_too_large` | the upload is bigger than `MAX_UPLOAD_MB` |
| 422 | `unsupported_file` / `unreadable_image` / `not_a_receipt` | the input can't be processed |
| 422 | `uncategorized_items` | confirm was called while some items have no category |
| 422 | `validation_error` | a field failed validation (FastAPI's default, mapped to this format) |
| 500 | `storage_error` | the database or a file couldn't be read or written |
| 503 | `llm_unavailable` | the model server is unreachable |
| 504 | `llm_timeout` | the model didn't answer in time, after one retry |

Because of async extraction ([0007](../decisions/0007-async-extraction-with-polling.md)), the LLM errors usually show up as the receipt's `status: failed` with `error: llm_unavailable | llm_timeout | malformed_output`. `POST /receipts/{id}/extract` returns 503 or 504 only when a synchronous check fails.
