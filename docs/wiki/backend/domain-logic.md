# Domain logic

This is the pure, deterministic code in `backend/app/domain/`. It makes up the "substantial application logic" (criterion 2). No module here imports `db`, `ai` or `api`.

## `validation.py` (F4)
- Parses numbers: German `1,99`, `1.234,56` and `-0,50`. Currencies `€`/`EUR` become `EUR`.
- Arithmetic checks, with a tolerance of 0.02 per check:
  - the line items sum to the subtotal (or to the total if there is no subtotal)
  - subtotal plus tax equals the total
- Date checks: the date parses, isn't in the future, and is no more than 2 years old.
- Each failed check returns a `Flag {field, code, message}`.

## `confidence.py` (F4)
Sets the review status from the flags ([0008](../decisions/0008-rule-based-review-status-not-probability.md)):
- `rejected`: required fields missing that the rules can't fill (no total and no line items)

The **plausibility rule** that decides whether the output is a receipt at all runs before this, and a non-receipt never gets a review status: the receipt becomes `failed` with `not_a_receipt` ([0015](../decisions/0015-non-receipt-is-a-failure-with-retry-or-manual-entry.md)). Its exact thresholds are set in F04 and tested against the spike outputs.
- `needs_review`: any flag, an `unreadable_fields` entry, a missing merchant or date, or any `uncategorized` item
- `accepted`: otherwise

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
- **Rules** (ordered, each in linear time; timing tests on 200k-character runs guard this, including labels joined by `/` `.` `=` `-` and IBAN-shaped groups):
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
