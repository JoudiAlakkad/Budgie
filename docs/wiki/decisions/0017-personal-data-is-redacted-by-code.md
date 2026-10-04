# 0017 — Personal data is redacted by code, not by hand or by an agent

**Status:** Accepted (2026-10-04)

## Context
Receipts carry personal data that Budgie doesn't need: store addresses, masked card digits, terminal and transaction ids, cashier names, URLs. The model copies some of it into its output, e.g. a card-payment line read as an item, or free text in the spike's base schema ([AI spike](../backend/ai-spike.md)).

F03 first built its test fixtures by having a general-purpose agent anonymise the spike outputs by hand. That was rejected: in production there is no agent to anonymise receipts, and a hand edit can't be repeated or checked. If the app needs anonymisation, it has to be code that the app and the tooling share.

## Decision
- `backend/app/domain/redaction.py` is a pure, deterministic module ([domain-logic](../backend/domain-logic.md#redactionpy-f3)) with these functions:
  - `find_personal_data(text)` lists matches of a fixed rule table: IBAN, Luhn-valid card number, masked card digits, e-mail, URL, phone, VAT/tax id, street and house number, postcode and city, labelled ids, cashier name.
  - `redact_text(text)` replaces each match with a typed placeholder (`[card]`, `[address]`, …). It is idempotent.
- **Production:** the pipeline (F05) redacts `raw_model_output`, `merchant` and line-item descriptions **before storing** them. Raw model output, prompts and image bytes are never logged.
- **Fixtures:** `scripts/make_fixtures.py` generates `tests/fixtures/recorded_responses/` from the local spike outputs using the same module. It is deterministic and refuses to write if anything is left. A pytest check scans every committed fixture.
- **Evaluation (F11):** committed evaluation material goes through the same module.

## Consequences
- The same code protects the database, the logs, the fixtures and the evaluation. A rule that is missing is added once and tested once.
- A regex table is best effort. An unusual format can slip through, and an odd item name can be redacted by mistake. The negatives in the tests (prices, dates, EAN codes, item names) guard against the second.
- The uploaded image is still stored unredacted. It stays local and is deleted with its receipt ([persistence](../backend/persistence.md)).
- The stored raw output is no longer exactly what the model said. The placeholders show where text was removed, which is enough for debugging and evaluation.
