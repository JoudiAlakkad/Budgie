# 0013 — Deterministic item categorisation by lookup

**Status:** Accepted (2026-09-28)

## Context
A single supermarket receipt mixes groceries, snacks, alcohol and household goods. To find budget leaks, spending has to be categorised per line item, not per receipt. The user reviews every receipt before confirming it.

## Decision
1. **Normalise the item name**, deterministically:
   - lowercase, and fold umlauts and ß
   - split off quantity and unit
   - drop qualifiers such as `bio` and own-brand prefixes
   - expand common receipt abbreviations

   For example, `"BIO BANANE 1 KG"` becomes `banane`, with qty 1 and unit kg.
2. **Look up the name** in the `item_categories` table with an exact match. The table is seeded from `backend/app/domain/data/item_categories_seed.yaml`.
3. **Not found:** the item is marked `uncategorized` and highlighted in the review screen, and the user picks a category. The choice is saved to the table with source `user`, so the same item is categorised automatically next time. The user can also correct a category that came from the table.
4. **Confirming is blocked:** a receipt with uncategorised items can't be confirmed. The API returns 422.
5. **No AI:** the model never assigns categories, and its output schema has no category field.

## Consequences
- Categorisation is fully explainable and testable. Each item stores its `category_source` (`seed`, `user` or `none`).
- It depends on the extraction getting the text right. A misread name, such as `BANAME`, won't match, and the user is asked.
- The evaluation reports lookup coverage (how many items get a category without asking the user), first with only the seed table and again after the user's choices.
- Details: [domain-logic](../backend/domain-logic.md).
