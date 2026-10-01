# Contracts

The seam between [frontend](../frontend/) and [backend](../backend/), and the only way for external clients to reach the service ([architecture](../architecture.md#integration-points)).

**Status:** draft from the plan. F2 finalises these pages together with `backend/app/api/schemas.py` and `docs/openapi.json`. After F2, only the main session changes them ([0009](../decisions/0009-contract-first-parallel-development.md)).

- [API endpoints](api-endpoints.md): routes, request and response shapes
- [Error format](error-format.md): shared error body and status codes
- [Receipt lifecycle](receipt-lifecycle.md): status values and transitions
- [CSV export](csv-export.md): the file-exchange integration point
