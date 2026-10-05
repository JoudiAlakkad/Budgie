# Domain logic

This is the pure, deterministic code in `backend/app/domain/`. It makes up the "substantial application logic" (criterion 2). No module here imports `db`, `ai` or `api`.

## Input types (F4)
The domain imports neither `ai.schema` nor `api.schemas`, so its rules take frozen dataclasses. F05 builds them from the model output, and F06 builds them from an edited expense.
- In `app/domain/facts.py`. `FlagCode` there is a Literal of the F4 codes, and a test checks it against the Flag codes table below.
- `ItemFacts(description, amount: Decimal, category: str | None)`: `None`, blank, or `"uncategorized"` in any case means not categorised yet.
- `ReceiptFacts(merchant, date: str | None, currency, subtotal, tax, total: Decimal | None, items, unreadable_fields)`.
- `Flag(field, code, message)`: the same shape as the `Flag` DTO.
- Money must be finite: NaN and Infinity raise `ValueError`. Build Decimals from `str(float)`. The sum check rounds to cents anyway, so a float-born `Decimal(2.98)` = 2.9799… can't cause a false flag.
- A blank or whitespace-only string counts as missing.

## `validation.py` (F4)
- `parse_number`: German `1,99`, `1.234,56` and `-0,50`, and plain `1.99`. Nothing calls it yet (the schema gives amounts as JSON numbers); it is kept for text input. ASCII digits, an optional leading `-`, no inner spaces. In order:
  1. a comma is the decimal point, and any `.` before it separates groups of exactly 3 digits (`1.234,56`; `1.23,45` is rejected)
  2. without a comma, dots that split 1–3 leading digits from 3-digit groups are thousands (`1.234` → 1234, `1.234.567`)
  3. otherwise a single `.` is the decimal point (`1.99`, `1.2345`)
  4. digits only (`1234`)
  - The first group of a grouped number has no leading zero, so `0.123,45`, `000.000` and `0.123` are rejected; `0,50` and `0.5` parse.
  - Everything else raises `ValueError` (`1.234.5`, `1234.567`, `.5`, `5.`, `+1`, `1 234,56`).
- `normalize_currency`: `€`, `EUR` and `eur` become `EUR`; another 3-letter code is uppercased; anything else is unknown. The spike returned `€`.
- `parse_date`: `YYYY-MM-DD` and `DD.MM.YYYY` (the spike returned `22.09.2026`).
- **Arithmetic check**, tolerance 0.02: the line items sum to the subtotal and to the total, each checked on its own when present, so a receipt can get two `sum_mismatch` flags (subtotal first). Because German VAT is included, the items should match the total too. A US-style receipt (net subtotal + tax = total) is flagged on the total; the user decided this is acceptable, since Budgie targets German receipts and a false flag only costs a review. Both sides are rounded to cents (half up) in a 400-digit decimal context (`SUM_PRECISION`; a float's integer part has at most 309 digits) before the comparison, so a float-born or huge amount (`1e30`) is flagged, never a crash. The message shows the rounded values. Upper limits on amounts belong to the API's `Money` (`max_digits=12`), which F05 and F06 must handle.
- **No tax check.** German receipts print VAT as included: in the spike's TEDi run, subtotal 3.10, tax 0.49 and total 3.10 were all correct, and `subtotal + tax = total` would have flagged it. Tax is stored but not checked.
- **Date checks:** the date parses, isn't in the future, and is no more than 2 years old (by calendar date). `today` is a parameter, so the tests are deterministic. The cutoff is `years_before(today, 2)`, which falls back from 29 Feb to 28 Feb; a date exactly on the cutoff passes.

## `confidence.py` (F4)
Sets the review status from the flags ([0008](../decisions/0008-rule-based-review-status-not-probability.md)). How this answers criterion 13 is explained in [uncertainty](uncertainty.md).
- `rejected`: no total and no line items, so nothing usable was extracted
- `needs_review`: any flag
- `accepted`: otherwise

`assess(facts, today)` returns the status and every flag. F05 calls it after extraction; F06 calls it again after every create or edit, so a fixed field clears its flag. F06 drops a key from `unreadable_fields` once the user edits that field.

### Plausibility rule
`is_plausible_receipt(facts)` is `False` when there is **no merchant, no total and at most one item**. The pipeline checks it before the review status; a non-receipt never gets one, and the receipt becomes `failed` with `not_a_receipt` ([0015](../decisions/0015-non-receipt-is-a-failure-with-retry-or-manual-entry.md)).
- Spike evidence: it catches the base-schema pinboard runs (one item, no merchant, no total).
- **Known limit:** the strict-schema pinboard run invented a merchant, a date, a subtotal and a total, so it passes this rule. Its items (22.98) match the invented subtotal (23.00) but not the total (25.00), so it is flagged `sum_mismatch` on `total`, plus `date_too_old` (2023-10-26) and `unreadable` on `total` and `tax`, and lands in `needs_review`.
- **Recorded fixtures:** `missing_fields` has 2 items, so it is plausible. `valid_receipt` (the spike's ALDI run) is flagged `sum_mismatch` on `subtotal` and `total`, because the model listed the payment lines ZU ZAHLEN, BAR and ZURÜCK as items: the rule catches a real extraction error.

### Flag codes
| Code | Field | Set when |
|---|---|---|
| `sum_mismatch` | `subtotal` and/or `total` | the items' sum is off that value by more than 0.02 |
| `date_unparseable` | `date` | a date is present but matches neither format |
| `date_in_future` | `date` | the date is after `today` |
| `date_too_old` | `date` | the date is more than 2 years before `today` |
| `currency_unknown` | `currency` | a currency is present but not recognised |
| `missing_merchant` | `merchant` | no merchant |
| `missing_date` | `date` | no date |
| `missing_total` | `total` | no total |
| `unreadable` | the key | the model listed the key in `unreadable_fields`; once per key, and not for a key that already has a `missing_*` flag |
| `uncategorized_item` | `line_items[i]` | the item has no category yet |

`assess` returns them in this order: `sum_mismatch` (subtotal, then total), the date flag, `currency_unknown`, the `missing_*` flags, `unreadable` in the model's order, `uncategorized_item` by index. F7 adds `possible_duplicate`.

## `categorize.py` (F7)
Implements [0013](../decisions/0013-deterministic-item-categorisation-by-lookup.md).
1. **`normalize(description)`** returns `(normalized_name, qty, unit)`:
   - lowercase, and fold `ä→ae ö→oe ü→ue ß→ss`
   - pull out the quantity and unit (`1 kg`, `500g`, `2x`, `2 St`, `1,5l`)
   - drop prices and stray symbols
   - drop qualifiers (`bio`, `organic`, `frisch`, and own-brand prefixes such as `ja!`, `k-classic`, `gut&guenstig`)
   - expand abbreviations from a small dictionary (`tk`→`tiefkuehl`, `h-milch`→`milch`)
   - collapse whitespace
2. **`lookup(normalized_name, table)`** returns `(category, source)`, or `("uncategorized", "none")`. It's an exact match only.
3. A user's choice is saved as `item_categories[normalized_name] = category` with source `user`, and it overrides the seed entry.

### Categories
`groceries.fresh`, `groceries.staples`, `snacks_sweets`, `drinks`, `alcohol`, `tobacco`, `household`, `personal_care`, `health`, `eating_out`, `transport`, `clothing`, `electronics`, `other`, plus the special categories `deposit` and `discount`, which aren't counted as spending.

The API mirrors this list as Literals in `app/api/schemas.py` (`SpendingCategory`, `Category`), because `api` doesn't import `domain`. A test parses this section and checks that the Literals match it.

## `duplicates.py` (F7)
- A receipt is a likely duplicate if the normalised merchant, the date and the total (±0.01) match an existing expense.
- It raises a `possible_duplicate` flag. Nothing is deleted automatically.

## `budget.py` (F8)
- Monthly spend per category counts confirmed expenses only, and excludes `deposit` and `discount`.
- It compares the spend to the budget and gives a linear projection for the whole month: `spend / day_of_month × days_in_month`.
- Savings-goal progress: `(income − spend)` per month, against the target.

## `leaks.py` (F9)
Each detector returns `Leak {type, category?, merchant?, amount, explanation}`.
- `recurring`: the same merchant, or the same normalised item, ≥ N times in a month
- `over_budget` / `on_pace_to_overrun`: from `budget.py`
- `spike`: a category's monthly spend is more than k × the median of the previous 3 months
- `small_frequent`: at least M purchases under X € in a category, adding up to at least Y % of that category's spend

The thresholds are constants in the module, listed here once they're fixed.

## `redaction.py` (F3)
The safety net for personal data in the three places where the model writes free text: `merchant`, item descriptions and the stored raw output ([0017](../decisions/0017-personal-data-is-redacted-by-code.md)). The first line of defence is the schema: fixed keys, enums for `payment_method` and `unreadable_fields`, and no field for card or address data.
- **Bounded input:** `redact_text`, `clean_merchant` and `find_personal_data` look at no more than `MAX_TEXT_CHARS` = 65,536 characters, far above model output capped by `LLM_MAX_TOKENS` (about 8–10 KB). The F05 pipeline must know that a stored raw output ending in ` [truncated]` is no longer valid JSON. A longer text:
  - is cut at the limit, or earlier if the cut would split a JSON escape, a card number or an IBAN (that number's trailing run of up to 43 characters is dropped whole, so it can't leak half-redacted)
  - gets the suffix ` [truncated]`
  - nothing after the cut is returned or searched
- **Idempotence with the limit:** placeholders count as one character towards it, because redaction can make a text longer (`Bon.1,` → `Bon.[id],`). A text that already ends in the suffix keeps it, so a second pass never cuts again.
- **Slow-regex guard:** a fixed-size test. Every hostile run is built at exactly 65,536 characters, and `redact_text`, `clean_merchant` and every rule's `finditer` must finish each one, run once, under `SLOW_S` = 2 s.
  - The runs: each character class, digit groups, IBAN-shaped and masked groups, labels joined by `/` `.` `=` `-` with and without `1 kg`, a full receipt, and a long merchant line.
  - The linear rules take at most about 70 ms (IBAN-shaped groups), about 30× below the limit.
  - An exponential rule passes the limit after a few dozen characters: a child-process test shows the old `card_masked` pattern doing so on 44 `*`.
- **Rules** (ordered, each linear):
- `_fit_iban` stops walking groups once the length passes the country's IBAN length (or 34).
- Rules:
  - `iban` → `[iban]`: an IBAN that passes the ISO 13616 **mod-97 checksum** (`iban_checksum_valid`, computed digit by digit).
    - Shape: 2 uppercase letters and 2 check digits, then one compact run or groups of 4 after exactly one space, 15–34 characters in all. For the common countries the length must also match `IBAN_LENGTHS` (DE 22, AT 20, CH 21, NL 18, FR 27, GB 22, …).
    - The regex is a bounded lookahead, and `_fit_iban` judges it in Python. It drops trailing groups that aren't part of the IBAN (`AT61 … 3201 BANK` → `[iban] BANK`). A rejected candidate doesn't hide an IBAN that starts inside it.
    - Not matched: lowercase IBANs, and separators other than one ASCII space.
  - `iban_masked` → `[iban]`: the country code and check digits, then at least 4 mask characters (`*`, `X`, `x`, `#`) and a shown digit at the end (`DE89 **** **** **** **30 00`, `DE89****3000`). It has no checksum to check.
  - `card`: a 13–19 digit run, Luhn-valid and not a valid EAN-13 → `[card]`
  - `card_masked`: `****1234`, `XXXX XXXX 1234`, … → `[card]`
  - `labelled_id`: terminal, trace, receipt, till, transaction, TSE, customer or card number and similar labels. The label stays and the value becomes `[id]`.
    - The regex is only label, separator and value, with no lookahead; `_fit_id` decides in Python whether the value is an id.
    - The separator may be spaces, tabs or no-break spaces around `:`, `#` or `Nr.`, or a hyphen followed by a digit (`TID-123` → `TID-[id]`).
    - Never treated as a value: dates, prices (either decimal separator), thousands (`3.500`), times, and quantities with a unit, with or without a space (`500g`, `2kg`, `3x`, `5 kg`).
- Earlier rules win when matches overlap. A `labelled_id` value that runs into an earlier finding is cut back to the part before it (`Bon 12 4111…` → `Bon [id] [card]`), so `redact_text` is idempotent.
- `find_personal_data(text)` returns `Finding {kind, start, end}`. `redact_text(text)` replaces each match and is idempotent. The rules see JSON escapes decoded, and a match never splits an escape or crosses a `"`, so redacted JSON stays valid.
- `clean_merchant(text)` keeps the shop name:
  1. take the first of up to three non-blank lines that still has text after steps 2–3 (`Tel. 0231 123456\nREWE` → `REWE`)
  2. cut it before a 5-digit postcode, a run of 6 or more digits, `http`, `www.`, `@`, or a `Tel`/`Telefon`/`Fon`/`Fax` label
  3. strip trailing punctuation
  4. return `None` if nothing is left, otherwise the `redact_text` result
- **F05 applies it:** `clean_merchant` to `merchant`, and `redact_text` to the descriptions and the raw output.
- **Must survive:** prices, dates, times, quantities, weights, EAN codes, item names (including `APFELRING 2`, `Holzweg 2`, and uppercase names that look like the start of an IBAN, such as `PC24 BLAUBEEREN`, `XL12 HANDTUCH` or `GR12 TOMATEN 500G`), IBAN-shaped strings with a wrong checksum, and chain names.
- **Why only these rules:** the address, URL, phone, tax-id and cashier rules were written for the spike's base schema, where `unreadable_fields` was free text. Once the schema closed that field, they mostly hit item names (`Hering 2` → `[address]`), and one of them hung on a line of `*` (catastrophic backtracking). They were removed.
- **Known limits:**
  - In a merchant like `Markt | info@markt.example`, the part before the `@` survives the cut, and `Shop @ Home` becomes `Shop`.
  - A label glued into a rejected value isn't seen: in `Kasse-Bon 1234`, the `1234` survives.
  - Header text the model copies into `merchant` is cleaned in the `merchant` column but stays in the stored raw output, unless F05 replaces it there too.

### Fixture generator (`scripts/make_fixtures.py`)
- `python scripts/make_fixtures.py --spike data/spike --out tests/fixtures/recorded_responses [--check]`.
- **Only strict spike runs** are read, by file name (`<stamp>_gemma3-4b_strict_<image>_r1.json`): `valid_receipt` (+ `_fenced`) and `non_receipt_claimed_receipt`. They were recorded before `payment_method` existed, so the generator inserts `"payment_method": null` and says so in `source`.
- **Synthetic:** every other case, including `missing_fields` and `cut_off_length`, which copy the shape of the spike's base-schema failures (`source` says so).
- Spike-based cases and `cut_off_length` use `max_tokens` 1024; the other synthetic cases use 2048.
- **Merchant:** the `merchant` of a spike answer goes through `clean_merchant`, the same way F05 stores it. `source` notes this only if it changed something.
- **Synthetic cases** build without a spike folder (`render_synthetic`), and a test compares them byte for byte with the committed files.
- **Writing:** every file is written into a temp folder, then moved in with `os.replace` (atomic per file), and stale `.json` files are removed.
- **Exit codes:** 2, writing nothing, on a missing or ambiguous spike file, on personal data left after redaction, on a hit of the independent scan, or on a write error. 1 when `--check` finds a difference.
- **Second check:** `scripts/fixture_scan.py` is an independent, broader scan that shares no code with `redaction.py`. It flags 5+ digit runs, `@`, masked digits, streets with or without a house number, `Am`/`An der`/`Im <Name> <n>`, URLs and bare domains, phone-like digit groups, and `Tel`/`Telefon`/`Fon`/`Fax`/`USt` with a value. The generator runs it and refuses to write on a hit, and the tests run it on every committed fixture. It also flags item names like `APFELRING 2`, so a future fixture with one needs an allowlist entry. It catches compact masked IBANs (`DE89****3000`) but not the spaced form; `iban_masked` redacts both before the scan runs.
- `make lint` covers `scripts/`, which became shared code in F3.
