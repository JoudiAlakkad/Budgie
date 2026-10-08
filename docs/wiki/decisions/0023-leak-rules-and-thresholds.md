# 0023 — Leak rules and thresholds

**Status:** Accepted (2026-10-08)

## Context
F09 builds `GET /insights/leaks`, whose shape has been fixed since F2: `Leak {type: recurring|over_budget|on_pace_to_overrun|spike|small_frequent, category?, merchant?, amount, explanation}` ([api-endpoints](../contracts/api-endpoints.md#settings-and-insights)). The sketch in [domain-logic](../backend/domain-logic.md#leakspy-f9) left the rules and thresholds open.

The student's motivating case: 50 € set for eating out, and 89 % of it already spent before the middle of the month. If the user keeps eating out at that rate, the budget is gone long before the month ends.

F08's dashboard row already says `on_pace_to_overrun` whenever the linear projection passes the budget ([0021](0021-dashboard-and-goal-semantics.md)). That rule fires on day 1 after a single purchase, which 0021 lists as noise. A leak card is meant to be a stronger signal than a row state.

## Decision
- **F09 detects three types:** `over_budget`, `on_pace_to_overrun` (the "early burn" below) and `spike` (the student's decision). `small_frequent` and `recurring` stay in the contract's enum but are never returned. They are [future work](../plan/roadmap.md#future-work).
- **Spend** is the same as in 0021: confirmed expenses dated in the month, item amounts summed per category, with `deposit`, `discount` and `uncategorized` left out. Only spending categories get leaks.
- **Early burn (`on_pace_to_overrun`), a literal threshold** (the student's decision, chosen over reusing the 0021 projection):
  - only for the current Europe/Berlin month, and only for a category with a budget
  - `0 < spent ≤ budget` (above the budget it is `over_budget` instead, never both)
  - `used = spent / budget ≥ BURN_USED_MIN` = **0.80**
  - `elapsed = today.day / days_in_month ≤ BURN_ELAPSED_MAX` = **0.50**, so up to day 15 in a 30- or 31-day month and up to day 14 in February
  - `amount = projected − budget`, with `projected` from 0021. It is always positive here, since used ≥ 0.8 by mid-month projects to at least 1.6 × the budget.
  - **Run-out day** = `ceil(budget × today.day / spent)`, the day the budget is used up at the current daily rate. It is never before today and always falls inside the month.
  - **Visits** = confirmed expenses in the month with at least one item in the category.
  - Explanation: `Eating out: you've used 89 % of your 50,00 € budget (44,50 €) by 14.10.2026, with 17 days left. At this pace it runs out around 16.10.2026 and the month ends at about 98,54 € (+48,54 €). 6 visit(s) so far, about 7,42 € each.` The visit text is always "visit(s)", for any count (the student's decision). When the run-out day is today, the explanation says the budget is used up today.
- **`over_budget`**, in any month: `spent > budget`, `amount = spent − budget`. `Eating out: 62,40 € spent of a 50,00 € budget, 12,40 € over.`
- **`spike`**, in any month:
  - The history is the 3 months before the viewed month. A history month counts only if it has any spend at all after the 0021 filter (a month with only deposit returns doesn't count), so months before the user started with Budgie don't count as 0.
  - At least 2 counted months are needed (`SPIKE_MIN_MONTHS`). The median is taken over the counted months, with the category's spend in each (0 if it has none that month).
  - It fires when `median > 0` and `spent > SPIKE_FACTOR × median`, where `SPIKE_FACTOR` = **1.5**. It compares the real spend, not the projection, so in the current month it fires only once the spend has actually happened.
  - `amount = spent − median`. `Snacks and sweets: 38,00 € this month, +60 % vs. your median of 23,75 € over the last 3 months.` The month count in the text is the number of counted history months (2 or 3), not always 3 (the student's decision).
- **Wording:** "at this pace" and "about", never "you will". There are no probabilities ([0008](0008-rule-based-review-status-not-probability.md)). Every number in the text is computed and every sentence is a fixed template; no model writes leak text.
- **"Potential leak" in the UI** (the student's decision): a rule can only flag a pattern, and whether it really is a leak is for the user to judge. Every user-visible text says "potential leak" (section "Potential leaks", empty state "No potential leaks found in <month>."). Code and contract names keep `leak` (`Leak`, `GET /insights/leaks`, `domain/leaks.py`), so the contract doesn't change. The explanations don't use the word.
- **Formats** in `explanation` match the UI: money as `de-DE` currency with a plain space before `€` (`44,50 €`, not the NBSP `Intl` writes), dates as `de-DE` medium (`14.10.2026`), percentages as whole numbers rounded half up, except that the used share never shows 100 % while some of the budget is left (99,6 % shows as 99 %; found by `/code-review`). Every amount in the text is clamped like `amount`, and the order is decided on the real, unclamped amounts. Category names use the labels of `frontend/js/categories.js`; the domain keeps its own copy, and a test checks the two are equal.
- **Order:** `over_budget`, then `on_pace_to_overrun`, then `spike`; within a type by `amount` descending, then by category key (as in the summary). A category can be both `over_budget` and `spike`.
- `category` is set on every leak and `merchant` is always `null`. Amounts are rounded half up to cents and clamped to the `Money` limit, as in 0021.
- The thresholds are constants in `domain/leaks.py`: `BURN_USED_MIN`, `BURN_ELAPSED_MAX`, `SPIKE_FACTOR`, `SPIKE_HISTORY_MONTHS` = 3 and `SPIKE_MIN_MONTHS` = 2.

## Consequences
- No contract change, so the backend and frontend can be built in parallel ([0009](0009-contract-first-parallel-development.md)).
- **The row and the card can disagree.** In the second half of the month a category can be "On pace to overrun" on the dashboard without a burn card, even at 95 % used. A small spend early in the month gets a row state but no card. The card is the stronger, rarer signal.
- A single large purchase of 80 % or more of a budget in the first half is a burn leak, even on day 1. That is intended: the budget is mostly gone either way.
- A past month never gets a burn leak, only `over_budget` and `spike`.
- `spike` needs 2 months of history, so a new user sees no spike cards in their first two months.
- The category labels exist twice (frontend and domain), held together by a test.
- The demo can show a burn card only in the first half of the month.
- **Demo side effects (accepted by the student):** health is at 98 % of its budget on day 1, so it also gets a burn card in the first half; eating out turns `over_budget` in the second half and later also `spike`.
