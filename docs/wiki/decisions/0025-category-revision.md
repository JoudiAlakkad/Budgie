# 0025 — Category revision: groceries, drinks, pet

**Status:** Accepted (2026-10-11)

## Context
The list from [0013](0013-deterministic-item-categorisation-by-lookup.md) split groceries into `groceries.fresh` and `groceries.staples` and kept `alcohol` apart from `drinks`. In use, the student found the split harder to budget against than it was worth: a user sets one food budget, not two, and the fresh/staples line is unclear for many items (frozen vegetables, bread, eggs). Pet food and litter had no home and landed in `other` or `household`.

## Decision
The student's list (2026-10-11):
- **`groceries.fresh` + `groceries.staples` → `groceries`.**
- **`alcohol` is merged into `drinks`.**
- **New spending category `pet`** (pet food, litter, pet care).
- The order, which `CATEGORIES` keeps: `groceries`, `snacks_sweets`, `drinks`, `tobacco`, `household`, `personal_care`, `health`, `eating_out`, `transport`, `clothing`, `electronics`, `pet`, `other`, plus the special categories `deposit` and `discount`.
- **Labels:** `groceries` "Groceries", `drinks` "Drinks", `pet` "Pet supplies"; the others are unchanged.
- **No migration: local data is reset.** The data is one student's test and demo data. After the update, the SQLite file under `data/` is deleted and `make seed-demo` runs again. There is no startup mapping from old to new keys.
- Categorisation stays lookup-only: the model never assigns a category (0013 point 5 unchanged).

## Consequences
- A database from before F13 holds keys the API's Literals no longer accept, so its responses fail validation (`500`). The reset is required, and the F13 PR says so.
- The seed's fresh and staples lists become one `groceries` list, and the alcohol list moves into `drinks`. A new `pet` section is seeded.
- Leak detection loses the separate alcohol view: a wine purchase now counts toward the `drinks` spike. The demo seed's spike pattern moves to `drinks` ([0022](0022-demo-data-by-explicit-command.md), [0023](0023-leak-rules-and-thresholds.md)).
- Pfand and Leergut stay `deposit` ([0019](0019-lean-extraction-line-totals-date-as-printed.md)).
- The list is in [domain-logic](../backend/domain-logic.md#categories); the Literals, `domain/categories.py`, `frontend/js/categories.js` and the leak labels follow it, checked by the existing equality tests.
