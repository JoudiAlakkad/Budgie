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

The API mirrors this list as Literals in `app/api/schemas.py` (`SpendingCategory`, `Category`), because `api` doesn't import `domain`. A test checks that both lists match.

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
