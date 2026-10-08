# 0021 — Dashboard and goal semantics

**Status:** Accepted (2026-10-08)

## Context
F08 builds `GET /insights/summary`, whose shape has been fixed since F2 ([api-endpoints](../contracts/api-endpoints.md#settings-and-insights)). The sketch in [domain-logic](../backend/domain-logic.md#budgetpy-f8) left open what counts as spend, how past and future months are projected, which categories are listed, and how a goal with only `target_amount`, `target_date` and an optional `monthly_income` turns into progress.

## Decision
- **Spend** counts confirmed expenses whose date falls in the month: the sum of line-item amounts per category. `deposit`, `discount` and `uncategorized` are left out. The repository returns raw sums, and `domain/budget.py` drops them, so the rule is tested in one place.
- **Projection:** for the current month (Europe/Berlin) it is `spent / today.day × days_in_month`. For a past or future month, `projected = spent`.
- **State:** `over` if `spent > budget`, `on_pace_to_overrun` if `projected > budget`, otherwise `under`. A category with no budget, or spend exactly equal to the budget, is `under`.
- **Rows:** every category with spend ≠ 0 or a budget, ordered by spend descending, then by name. `total_spent` is the sum of the rows. `projected_total` is the sum of the rounded row projections, so the rows add up on screen. `total_budget` is the sum of all budgets, or `null` if there are none.
- **The goal is per month and stateless** (the student's decision): nothing about past months is stored.
  - `months_left = max(1, months from the summary month to the target month, inclusive)`
  - `required_per_month = target_amount / months_left`. A target date that has passed clamps to 1 month, and the UI says so.
  - `saved_this_month = monthly_income − projected_total` (the student's decision). For a past month this is the real saving; for the current month it is the saving expected at month end, so `on_track` isn't optimistic early in the month.
  - `on_track = saved_this_month ≥ required_per_month`. Without income, both are `null`; so they are for a month after the current one, which hasn't started (found by `/code-review`).
- **Confirm needs at least one line item** (the student's decision, after `/code-review`): spend is the item sum, so an item-less expense would count as 0 €. A confirmed `sum_mismatch` expense counts its items; the user saw the flag.
- Summary amounts are clamped to ±9,999,999,999.99, the response `Money` limit.
- Money is `Decimal`, rounded half up to cents.

## Consequences
- No contract change.
- `required_per_month` rises over time, because past savings aren't remembered.
- For the current month, `saved_this_month` and `on_track` inherit the projection's noise: on day 1 one large shop can make them say "Behind".
- Discounts are never subtracted, so spend is slightly overstated.
- All currencies are summed as if they were EUR.
- A very distant target date makes `required_per_month` round to 0.00, so the goal always counts as on track.
- A future month's goal card shows only what is needed, since its projected spend is 0.
- A goal can't be removed, since the contract has no `DELETE /goal`.
