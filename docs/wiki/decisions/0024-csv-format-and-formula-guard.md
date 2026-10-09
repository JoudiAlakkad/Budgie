# 0024 — CSV format and formula guard

**Status:** Accepted (2026-10-09)

## Context
F10 builds `GET /expenses/export.csv`, the file-exchange integration point. Its columns have been fixed since F2 ([csv-export](../contracts/csv-export.md)), but the format details were open: separator and decimal mark for German users, encoding, row order, the filename, and what to do with cell values a spreadsheet would run as a formula (CSV injection, OWASP). The target users open files in German LibreOffice or Excel, where `;` and `,` are the defaults.

## Decision
All choices are the student's, taking the F10 plan's recommendations.
- **RFC 4180 with comma and dot**, as the contract already said, not German `;` and `,`. The file is an integration point for other programs, and a locale-dependent format would break them.
- **No BOM.** It breaks naive parsers (the first header becomes `\ufeffexpense_id`). Users open the file through the import dialog instead; the README (F12) explains it.
- **Formula guard on string columns only:** `merchant`, `item_description`, `item_normalized_name` and `item_unit` get a leading `'` when they start with `=`, `+`, `-`, `@`, a tab or a carriage return. A value already starting with `'` (e.g. the Dutch shop name `'t Hoekje`) is prefixed too, so the guard is lossless: stripping exactly one leading `'` always gives back the stored value (found by the reviewer: without it, a stored `'t Hoekje` and a guarded `=t Hoekje` could not be told apart). The guard is chosen by column, never by value, so negative amounts stay bare.
- **Row order:** `date` ascending, then `expense_id`, then item position, the natural order for a ledger.
- **Only `from`/`to` filter the export.** The contract stays as it is, and the Expenses page says the other filters don't apply.
- **Filename:** `budgie-expenses_<from|all>_<to|all>.csv`, built from ISO dates only, so there is no header injection.
- **No redaction at export time.** AI text is redacted before it is stored ([0017](0017-personal-data-is-redacted-by-code.md)), and the export is the user's own confirmed data. It never includes raw model output, image paths, flags, receipt ids or model metadata.
- **In memory, no streaming.** The data is one user's. The file is built before the response starts, so a database error is a JSON `500`, never a truncated `200`. Rows are never logged, and `Cache-Control: no-store` keeps the file out of caches.
- **Pure domain module:** formatting and the guard live in `domain/csv_export.py`, the query in a new `services/export.py` (`ExportService`), not on `ExpenseService`, whose method `list` breaks `-> list[...]` annotations on Python 3.12 ([workflow](../plan/workflow.md#ci-checks)).

## Consequences
- **The guard changes data.** A description like `-20% Rabatt` is exported as `'-20% Rabatt`. A machine client has to strip exactly one leading `'` from those four columns; the contract says so.
- Double-clicking the file in a German spreadsheet shows one column and may garble umlauts. That is accepted and documented, not fixed.
- A filtered list on the Expenses page and its export can differ (category, status, source). The page states it next to the button.
