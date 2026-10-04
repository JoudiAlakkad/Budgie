# Contracts

The seam between [frontend](../frontend/) and [backend](../backend/), and the only way for external clients to reach the service ([architecture](../architecture.md#integration-points)).

**Status:** final since F2, together with `backend/app/api/schemas.py` and `docs/openapi.json`. Only the main session changes them ([0009](../decisions/0009-contract-first-parallel-development.md)); conventions and the stub rule are in [0016](../decisions/0016-api-representation-and-stub-convention.md).

- [API endpoints](api-endpoints.md): routes, request and response shapes
- [Error format](error-format.md): shared error body and status codes
- [Receipt lifecycle](receipt-lifecycle.md): status values and transitions
- [CSV export](csv-export.md): the file-exchange integration point
