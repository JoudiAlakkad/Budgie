# CSV export

`GET /api/expenses/export.csv?from=YYYY-MM-DD&to=YYYY-MM-DD` returns the documented file-exchange integration point ([architecture](../architecture.md#integration-points)). Columns fixed in F2, `item_qty` and `item_unit` removed in F10 (the student's decision); format details fixed in F10 ([0024](../decisions/0024-csv-format-and-formula-guard.md)).

- **What's included:** confirmed expenses only, one row per **line item**. Every expense has at least one item, manual ones included. Deposit and discount items are included. Amounts are as stored, never converted.
- **Range:** `from ≤ date ≤ to`, both optional and inclusive, on the purchase date. `from` after `to`, or no match, gives the header row only. A bad date answers `422 validation_error` on `query.from` / `query.to`. The other `GET /expenses` filters don't apply to the export.
- **Order:** `date` ascending, then `expense_id`, then the item's position on the receipt.
- **Format:** RFC 4180: UTF-8 without BOM, comma-separated, CRLF line ends, a header row. A field is quoted only if it contains a comma, a quote, a line break or a semicolon (`;`, so a spreadsheet that splits on `;` keeps it as one cell), and a quote inside is doubled. Dates are ISO 8601. Money columns have exactly 2 decimals with `.`, rounded half up (`-0.25`; zero is `0.00`, never `-0.00`). An empty field would mean `null`, but a confirmed expense has no empty column.
- **Formula guard:** a value in `merchant`, `item_description` or `item_normalized_name` gets a leading `'`, so spreadsheets don't run it as a formula, when it starts with a tab or a carriage return, or when its first character after any leading whitespace is `=`, `+`, `-`, `@` or one of their full-width forms `＝`, `＋`, `－`, `＠` (e.g. ` =1+1` → `' =1+1`). Whitespace means anything Python's `str.lstrip()` removes, Unicode spaces such as NBSP included. The guard runs before quoting, so a guarded value with `;` is written `"' =1;2"`. A value that already starts with `'` gets one too, so a client recovers the stored value by stripping exactly one leading `'` from these three columns. Numeric columns are never prefixed.
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
| `item_amount` | decimal | `1.99` |
| `category` | string | `groceries.fresh` |
| `source` | `ai` \| `ai_corrected` \| `manual` | `ai_corrected` |

`source` marks AI-generated data, which criterion 19 requires to be identifiable.
