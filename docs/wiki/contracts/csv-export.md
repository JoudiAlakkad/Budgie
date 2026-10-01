# CSV export

`GET /api/expenses/export.csv?from=YYYY-MM-DD&to=YYYY-MM-DD` returns the documented file-exchange integration point ([architecture](../architecture.md#integration-points)).

- **What's included:** confirmed expenses only, one row per **line item**. Manual expenses without items get one row with an empty `item_*`.
- **Format:** UTF-8, comma-separated, with a header row. Decimals use `.` and dates are ISO 8601.

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
