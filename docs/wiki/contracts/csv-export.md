# CSV export

`GET /api/expenses/export.csv?from=YYYY-MM-DD&to=YYYY-MM-DD` returns the documented file-exchange integration point ([architecture](../architecture.md#integration-points)). Columns final since F2; format details fixed in F10 ([0024](../decisions/0024-csv-format-and-formula-guard.md)).

- **What's included:** confirmed expenses only, one row per **line item**. Every expense has at least one item, manual ones included. Deposit and discount items are included. Amounts are as stored, never converted.
- **Range:** `from ≤ date ≤ to`, both optional and inclusive, on the purchase date. `from` after `to`, or no match, gives the header row only. A bad date answers `422 validation_error` on `query.from` / `query.to`. The other `GET /expenses` filters don't apply to the export.
- **Order:** `date` ascending, then `expense_id`, then the item's position on the receipt.
- **Format:** RFC 4180: UTF-8 without BOM, comma-separated, CRLF line ends, a header row. A field is quoted only if it contains a comma, a quote or a line break, and a quote inside is doubled. Dates are ISO 8601. Money columns have exactly 2 decimals with `.`, rounded half up (`-0.25`; zero is `0.00`, never `-0.00`); `item_qty` is a plain decimal without exponent or trailing zeros (`1`, `1.234`). An empty field means `null` (only `item_qty` and `item_unit` can be empty).
- **Formula guard:** a value in `merchant`, `item_description`, `item_normalized_name` or `item_unit` that starts with `=`, `+`, `-`, `@`, a tab or a carriage return gets a leading `'`, so spreadsheets don't run it as a formula. Numeric columns are never prefixed.
- **Response:** `200`, `Content-Type: text/csv; charset=utf-8`, `Content-Disposition: attachment; filename="budgie-expenses_<from>_<to>.csv"` (`all` for a missing bound), `Cache-Control: no-store`. The file is built in memory before the response starts, so a failure is a JSON error, never a truncated file. Rows are never logged.
- **Opening in a German spreadsheet:** the file is for machines. Open it through the import dialog (LibreOffice: separator comma; Excel: Data → From Text/CSV), not by double-click.

| Column | Type | Example |
|---|---|---|
| `expense_id` | int | `42` |
| `date` | date | `2026-10-03` |
| `merchant` | string | `REWE` |
| `currency` | string | `EUR` |
| `expense_total` | decimal | `23.47` |
| `item_description` | string | `BIO BANANE 1 KG` |
| `item_normalized_name` | string | `banane` |
| `item_qty` | decimal? | `1` |
| `item_unit` | string? | `kg` |
| `item_amount` | decimal | `1.99` |
| `category` | string | `groceries.fresh` |
| `source` | `ai` \| `ai_corrected` \| `manual` | `ai_corrected` |

`source` marks AI-generated data, which criterion 19 requires to be identifiable.
