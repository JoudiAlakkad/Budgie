# 0020 — Saving user category choices and the duplicate rule

**Status:** Accepted (2026-10-07)

## Context
F07 builds the lookup from [0013](0013-deterministic-item-categorisation-by-lookup.md) and the duplicate check from [domain-logic](../backend/domain-logic.md#duplicatespy-f7). Both left open how a choice made on the review page reaches the table, how the seed meets an existing database, how the Pfand rule from [0019](0019-lean-extraction-line-totals-date-as-printed.md) survives an exact-match lookup, and which expenses a new one is compared to. [0018](0018-review-form-editing-semantics.md) stored a choice on the item only and deferred the table to F07.

## Decision
- **Choices are saved implicitly** (the student's decision, 2026-10-07): when `POST /expenses` or `PATCH /expenses/{id}` sends an item `category`, the server upserts `item_categories[normalized_name] = category` with source `user`, in the same transaction as the expense. It overrides a seed entry. An empty `normalized_name` is not saved. The review page needs no extra request; `PUT /item-categories/{name}` stays for the Settings page (F8).
- **Choices made in the same request apply to the rest of it:** the table is loaded, the request's sent categories are laid over it, then the other items are categorised.
- **The categorizer takes the table as a parameter** (`categorize(description, table)`), so it never opens its own session inside a caller's transaction. The pipeline loads the table in a short read transaction after the model call.
- **Seed sync instead of insert-if-empty:** on startup, every name in `item_categories_seed.yaml` that is missing is inserted, and rows still `seed` take the YAML's value; `user` rows are never touched. A new seed entry thus reaches an existing database. A sync failure is logged and never stops startup.
- **`DELETE /item-categories/{name}` on a `user` row** removes it and restores the seed value if the YAML has that name, so "the seed applies again" ([api-endpoints](../contracts/api-endpoints.md#item-categories-the-lookup-table)).
- **Deposit canonicalisation:** the normaliser maps any name containing `pfand` to `pfand` and any containing `leergut` to `leergut`. Seed entries put both under `deposit`, so 0019's substring rule survives under an exact lookup and stays user-overridable.
- **Duplicates are checked against all other expenses** (the student's decision), confirmed and drafts alike, excluding the expense itself: same normalised merchant, same date, totals within 0.01. Only the expense being created or edited gets `possible_duplicate`, on `field: null`, and it is recomputed on every create and edit. Nothing is deleted.
- **PyYAML** becomes a runtime dependency for the seed file (`safe_load` only).

## Consequences
- No contract change: `Flag.code` is a plain string, and the `/item-categories` DTOs already exist.
- Any category sent by an API client trains the table; the review page only sends one for a new row or a changed category ([pages](../frontend/pages.md)), so an unchanged seed category keeps its source.
- A saved choice doesn't recategorise other open drafts; only items categorised afterwards benefit.
- Deleting the earlier expense leaves a stale `possible_duplicate` on the newer one until it is edited.
- A `null`-field flag shows under "Other findings" on the review page.
