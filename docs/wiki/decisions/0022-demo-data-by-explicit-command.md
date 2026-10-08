# 0022 — Demo data by an explicit command through the services

**Status:** Accepted (2026-10-08)

## Context
Scenarios 2 and 3 must work "on the seeded demo data" ([roadmap](../plan/roadmap.md)). The roadmap put the demo seed in F9; the student moved it to F8, and F9 extends it with leak patterns. Data handling must be tested app code, not a one-off agent task.

## Decision
- **An explicit command**, `python -m app.demo_seed [--today YYYY-MM-DD]` (`make seed-demo`; in Docker `docker exec <ctr> python -m app.demo_seed`). There is no config flag: a startup side effect could load demo data into a real database.
- **It only loads into an empty database:** with any expense, receipt, budget or goal it refuses (exit 1) and changes nothing. `item_categories` doesn't count, since startup always seeds it.
- **Through the services:** each expense is created and confirmed with `ExpenseService`, and budgets and the goal are set with `BudgetService`. The stored data is thus normalised, categorised and assessed by the same rules as real data.
- **Relative to today:** the two previous months are full, and the current month holds the days up to today. The plan is deterministic for a given `today`; every description is in the item seed and the items sum to the total, so no expense is flagged.
- The logic is `services/demo_seed.py`, with tests; `app/demo_seed.py` is a thin CLI.

## Consequences
- The seed isn't atomic: it runs one transaction per expense. If it fails halfway, delete the database and run it again.
- The scenario-2 states (`over`, `on_pace_to_overrun`) depend on the day of the month; the tests pin `today`.
