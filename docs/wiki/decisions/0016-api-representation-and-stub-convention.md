# 0016 — API representation and stub convention

**Status:** Accepted (2026-10-03)

## Context
F2 fixes the contract before most endpoints exist ([0009](0009-contract-first-parallel-development.md)). Two things were open: how values look on the wire, and how endpoints that aren't built yet get into `docs/openapi.json`, which only lists routes that exist.

## Decision
- **Representation:**
  - Money is a JSON number with at most 2 decimals, backed by `Decimal` on the server.
  - Dates are `YYYY-MM-DD`; timestamps are ISO 8601 in UTC; ids are integers.
  - Response keys are always present, with `null` for a missing value.
  - Request DTOs forbid unknown fields.
  - The app uses `separate_input_output_schemas=False`, so a DTO used for both requests and responses (`Budget`, `Goal`) appears once in the spec instead of as an `-Input`/`-Output` pair.
- **Errors:** one body `{error, detail, fields?}` for every non-2xx response, with a single code-to-status mapping in `app/api/errors.py` ([error-format](../contracts/error-format.md)). Model errors reach clients only through `Receipt.error`, never as 503 or 504 ([0007](0007-async-extraction-with-polling.md)).
- **Stubs:** every documented endpoint exists from F2 as a route with its final path, parameters, `response_model` and status code. Its body raises `NotImplementedYet`, which answers `501 not_implemented` and names the feature that builds it. Stubs import only `app.api.schemas`, `app.api.errors` and `app.errors`.
- A test compares the endpoint tables in [api-endpoints](../contracts/api-endpoints.md) with the generated spec in both directions.

## Consequences
- The frontend can build against the full spec now; F03–F10 replace stub bodies without changing the spec.
- A number loses nothing for amounts with 2 decimals, works directly with `Intl.NumberFormat`, and keeps external clients simple. Arithmetic stays in `Decimal` on the server.
- 501 is not listed in each route's responses, because it is temporary; it is documented once in the error format.
- F02 adds `python-multipart`, which FastAPI needs to register the upload route.
- With one schema per shared DTO, a field with a default (e.g. `Goal.monthly_income`, `ErrorBody.fields`) is shown as optional in the spec, although the server always sends it.
